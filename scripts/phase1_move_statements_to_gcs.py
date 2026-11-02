"""One-time Phase 1 migration: move Gmail-exported statement PDFs into the private bucket (DEC-025).

Cloud Run's disk is wiped whenever an instance stops, so statement PDFs that Gmail sync exported
to data/monthly_statements on the Cloud Shell disk are copied to the private statements bucket
and their database rows (exported_statement_pdfs.file_path, assets.gcs_uri) are pointed at it.

Dry run by default; pass --apply to make changes. Idempotent: rows already in the bucket are
skipped. Local files are kept (they are covered by the Phase 0 backup and stay git-ignored);
delete them by hand once the backend on Cloud Run serves them.

Usage (from the repo root, as the project owner):
    STATEMENT_BUCKET=personal-finance-agent-507010-statements venv/bin/python scripts/phase1_move_statements_to_gcs.py [--apply]
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import sqlalchemy  # noqa: E402

import config  # noqa: E402
import statement_store  # noqa: E402
from database import DatabaseManager  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    args = parser.parse_args()

    if config.IS_DEMO:
        print("Refusing: this migration is for the personal environment only.")
        return 2
    if not statement_store.uses_bucket():
        print("Set STATEMENT_BUCKET to the private statements bucket first.")
        return 2

    db = DatabaseManager()
    with db.pool.connect() as conn:
        rows = conn.execute(sqlalchemy.text(
            "SELECT id, month_key, file_name, file_path FROM exported_statement_pdfs ORDER BY id"
        )).fetchall()

    moved = skipped = missing = 0
    for stmt_id, month_key, file_name, file_path in rows:
        if file_path.startswith(statement_store.GCS_SCHEME):
            skipped += 1
            print(f"#{stmt_id}: already in the bucket")
            continue
        if not os.path.exists(file_path):
            missing += 1
            print(f"#{stmt_id}: local file missing, left unchanged ({os.path.basename(file_path)})")
            continue
        name = os.path.basename(file_path)
        target = f"{statement_store.GCS_SCHEME}{config.STATEMENT_BUCKET}/{month_key}/{name}"
        print(f"#{stmt_id}: {name} -> {target}{'' if args.apply else '  (dry run)'}")
        if not args.apply:
            continue
        location = statement_store.save(file_path, month_key, name)
        assert location == target, (location, target)
        with db.pool.begin() as conn:
            conn.execute(sqlalchemy.text(
                "UPDATE exported_statement_pdfs SET file_path = :new WHERE id = :id AND file_path = :old"
            ), {"new": location, "id": stmt_id, "old": file_path})
            conn.execute(sqlalchemy.text(
                "UPDATE assets SET gcs_uri = :new WHERE gcs_uri = :old"
            ), {"new": location, "old": file_path})
        moved += 1

    print(f"\n{'Moved' if args.apply else 'Would move'}: {moved if args.apply else len(rows) - skipped - missing}, "
          f"already in bucket: {skipped}, missing locally: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
