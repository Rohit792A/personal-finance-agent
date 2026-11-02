#!/usr/bin/env python3
"""Phase 0 one-time database migration (idempotent). Dry run by default.

Usage (from the project root):
    ./venv/bin/python scripts/phase0_db_migration.py           # show what would change
    ./venv/bin/python scripts/phase0_db_migration.py --apply   # apply

What it does (see DEC-023 in decision.md):
1. Adds transactions.user_locked, so imports never overwrite rows the user corrected.
2. Clears the review flag on rows flagged ONLY because of the confidence-scale bug: AI
   confidence was stored on a 0-1 scale while the review gate expects 0-100 (< 85 = review).
   A row is cleared when it was on the 0-1 scale, its rescaled confidence is >= 85, and it has
   a real category (not 'Other'). 'Other' rows stay in review for a human to categorize.
3. Rescales all 0-1 confidence values to 0-100.
4. Wipes the plain-text Gmail App Password / statement passwords from gmail_sync_settings,
   but only after confirming they are stored in Secret Manager.
5. Security hardening: row-level security on every public table and no access for Supabase's
   public `anon` / `authenticated` roles (the same SQL the app runs on startup).

Steps 1-4 run in one transaction. Take a backup first (Phase 0 backups live in
~/backups/personal-finance-agent/2026-10-18/).
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import sqlalchemy  # noqa: E402

import secret_store  # noqa: E402
from database import SECURITY_HARDENING_SQL, DatabaseManager  # noqa: E402
from gmail_sync import GMAIL_APP_PASSWORD_SECRET, STATEMENT_PASSWORDS_SECRET  # noqa: E402

ON_01_SCALE = "t.ai_confidence_score > 0 AND t.ai_confidence_score <= 1"

COUNT_QUERIES = {
    "transactions": "SELECT count(*) FROM transactions t",
    "flagged for review": "SELECT count(*) FROM transactions t WHERE t.requires_human_review",
    "rows on the 0-1 confidence scale": f"SELECT count(*) FROM transactions t WHERE {ON_01_SCALE}",
    "flagged rows to clear (bug-only flags)": f"""
        SELECT count(*) FROM transactions t JOIN categories c ON c.id = t.category_id
        WHERE t.requires_human_review AND {ON_01_SCALE}
          AND t.ai_confidence_score * 100 >= 85 AND lower(c.name) <> 'other'""",
    "flagged rows on 0-1 scale that stay flagged": f"""
        SELECT count(*) FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.requires_human_review AND {ON_01_SCALE}
          AND NOT (t.ai_confidence_score * 100 >= 85 AND lower(coalesce(c.name, 'other')) <> 'other')""",
    "public tables without RLS": """
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relrowsecurity""",
    "table grants held by anon/authenticated": """
        SELECT count(*) FROM information_schema.role_table_grants
        WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated')""",
}


class _ReadOnlyManager(DatabaseManager):
    """Same connection logic as the app, but skips the startup schema/hardening steps."""

    def _ensure_schema(self):
        pass


def report(conn, title):
    print(f"\n== {title}")
    for label, sql in COUNT_QUERIES.items():
        print(f"  {label:45} {conn.execute(sqlalchemy.text(sql)).scalar()}")
    row = conn.execute(sqlalchemy.text(
        "SELECT coalesce(app_password, '') <> '', coalesce(pdf_password, '') <> '' "
        "FROM gmail_sync_settings WHERE id = 1"
    )).fetchone()
    print(f"  {'plain-text Gmail password in Supabase':45} {bool(row and row[0])}")
    print(f"  {'plain-text statement password in Supabase':45} {bool(row and row[1])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="apply the changes (default: dry run)")
    args = parser.parse_args()

    engine = _ReadOnlyManager().pool
    with engine.connect() as conn:
        report(conn, "Current state")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to make these changes.")
        return

    with engine.begin() as conn:
        # 1. Protect user corrections.
        conn.execute(sqlalchemy.text(
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS user_locked BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        # 2. Clear bug-only review flags (uses the pre-rescale values to find affected rows).
        cleared = conn.execute(sqlalchemy.text(f"""
            UPDATE transactions t SET requires_human_review = FALSE
            FROM categories c
            WHERE c.id = t.category_id AND t.requires_human_review AND {ON_01_SCALE}
              AND t.ai_confidence_score * 100 >= 85 AND lower(c.name) <> 'other'
              AND NOT t.user_locked
        """)).rowcount
        # 3. Rescale 0-1 confidence to 0-100.
        rescaled = conn.execute(sqlalchemy.text(
            f"UPDATE transactions t SET ai_confidence_score = t.ai_confidence_score * 100 WHERE {ON_01_SCALE}"
        )).rowcount
        # 4. Move any remaining plain-text passwords to Secret Manager, then wipe them.
        row = conn.execute(sqlalchemy.text(
            "SELECT coalesce(app_password, ''), coalesce(pdf_password, '') FROM gmail_sync_settings WHERE id = 1"
        )).fetchone()
        wiped = False
        if row and (row[0] or row[1]):
            if row[0] and not secret_store.secret_exists(GMAIL_APP_PASSWORD_SECRET):
                secret_store.set_secret(GMAIL_APP_PASSWORD_SECRET, row[0].replace(" ", "").strip())
            if row[1] and not secret_store.secret_exists(STATEMENT_PASSWORDS_SECRET):
                secret_store.set_secret(STATEMENT_PASSWORDS_SECRET, row[1].strip())
            if row[0] and not secret_store.secret_exists(GMAIL_APP_PASSWORD_SECRET):
                raise SystemExit("Gmail App Password is not in Secret Manager; refusing to wipe it.")
            conn.execute(sqlalchemy.text(
                "UPDATE gmail_sync_settings SET app_password = '', pdf_password = '' WHERE id = 1"
            ))
            wiped = True
        print(f"\nApplied: cleared {cleared} review flags, rescaled {rescaled} confidence values, "
              f"wiped plain-text passwords: {wiped}")

    # 5. Security hardening (separate transaction, same SQL as app startup).
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text(SECURITY_HARDENING_SQL))
    print("Applied: security hardening (RLS on, anon/authenticated revoked)")

    with engine.connect() as conn:
        report(conn, "After migration")


if __name__ == "__main__":
    main()
