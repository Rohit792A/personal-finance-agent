#!/usr/bin/env python3
"""Find and remove duplicate transactions left by overlapping imports (DEC-032).

Run from the repository root:

    # 1. Dry run (the default; read-only): writes an editable plan (CSV) and a report (Markdown)
    APP_ENV=personal venv/bin/python scripts/dedupe_transactions.py

    # 2. After reviewing the plan and taking a backup: apply the rows marked "remove"
    APP_ENV=personal venv/bin/python scripts/dedupe_transactions.py --apply PLAN.csv

    # Undo an applied plan (restores the rows, their statement files and the kept rows' labels)
    APP_ENV=personal venv/bin/python scripts/dedupe_transactions.py --undo PLAN_ID

    # Any of these against a throwaway local Postgres (e.g. a restored backup):
    DATABASE_URL=postgresql://postgres@127.0.0.1:55432/postgres venv/bin/python scripts/dedupe_transactions.py

The plan has one row per pair: decision (remove / review / keep), the rule that decided it, and
both transactions (see app/dedupe.py for the rules). Only rows whose decision is "remove" are
applied: change a "review" row to "remove" to accept it, or a "remove" row to "keep" to skip it.
set_category / set_type (EXPENSE, INCOME or TRANSFER) relabel the kept row; leave them empty to
keep its label. Leave ids, dates, amounts and descriptions alone: --apply checks them against the
database and changes nothing if any of them no longer match.

Applying runs in one database transaction. Each removed row is copied (in full, as JSON) into
transactions_removed, its statement files (assets) are pointed at the row that stays, the kept
row is relabelled if the plan says so (its old label is saved for undo), and the row is deleted.

Exit codes: 0 done, 1 not confirmed or failed, 2 the plan can't be applied as it is.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))  # app/ is the import root (DEC-025)

DEFAULT_OUT = Path.home() / "backups" / "personal-finance-agent" / "dedupe"

LOAD_SQL = """
    SELECT t.id, t.transaction_date, t.amount, t.description, COALESCE(c.name, '') AS category,
           UPPER(COALESCE(t.transaction_type, 'EXPENSE')) AS ttype, t.user_locked, t.created_at,
           COALESCE(a.gcs_uri, '') AS source
    FROM transactions t
    LEFT JOIN categories c ON c.id = t.category_id
    LEFT JOIN LATERAL (
        SELECT gcs_uri FROM assets WHERE transaction_id = t.id ORDER BY id LIMIT 1
    ) a ON TRUE
    ORDER BY t.id
"""

CREATE_REMOVED_SQL = """
    CREATE TABLE IF NOT EXISTS transactions_removed (
        id INTEGER PRIMARY KEY,
        duplicate_of INTEGER NOT NULL,
        rule TEXT NOT NULL,
        plan TEXT NOT NULL,
        removed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        moved_asset_ids INTEGER[] NOT NULL DEFAULT '{}',
        kept_changed BOOLEAN NOT NULL DEFAULT FALSE,
        kept_before JSONB,
        row_data JSONB NOT NULL
    );
    CREATE INDEX IF NOT EXISTS transactions_removed_plan_idx ON transactions_removed (plan);
"""

INSERT_REMOVED_SQL = """
    INSERT INTO transactions_removed
        (id, duplicate_of, rule, plan, moved_asset_ids, kept_changed, kept_before, row_data)
    SELECT t.id, :keep, :rule, :plan,
           COALESCE((SELECT array_agg(a.id ORDER BY a.id) FROM assets a WHERE a.transaction_id = t.id),
                    '{}'::integer[]),
           :changed,
           CASE WHEN :changed THEN (SELECT jsonb_build_object('category_id', k.category_id,
                                                              'transaction_type', k.transaction_type,
                                                              'is_self_transfer', k.is_self_transfer)
                                    FROM transactions k WHERE k.id = :keep) END,
           to_jsonb(t)
    FROM transactions t
    WHERE t.id = :remove
"""

RESTORE_KEPT_SQL = """
    UPDATE transactions t
    SET category_id = (r.kept_before->>'category_id')::integer,
        transaction_type = r.kept_before->>'transaction_type',
        is_self_transfer = (r.kept_before->>'is_self_transfer')::boolean
    FROM transactions_removed r
    WHERE r.id = :id AND r.kept_changed AND t.id = r.duplicate_of
"""


class PlanMismatch(RuntimeError):
    """The database doesn't match the plan; nothing was changed."""


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Find and remove duplicate transactions.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", metavar="PLAN.csv", type=Path,
                      help="apply the rows marked 'remove' in a reviewed plan")
    mode.add_argument("--undo", metavar="PLAN_ID", help="restore everything an applied plan removed")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"where the dry run writes the plan and report (default: {DEFAULT_OUT})")
    parser.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    return parser.parse_args(argv)


def make_engine(config):
    """An engine for the configured database. Unlike DatabaseManager, it creates no tables."""
    import sqlalchemy
    from sqlalchemy.engine import URL

    if config.DATABASE_URL:
        url = config.DATABASE_URL
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                url = "postgresql+pg8000://" + url[len(prefix):]
        return sqlalchemy.create_engine(url, pool_pre_ping=True)
    url = URL.create(
        drivername="postgresql+pg8000",
        username=f"{config.DB_USER}.{config.SUPABASE_PROJECT_REF}",
        password=config.get_db_password(),
        host=config.SUPABASE_POOLER_HOST,
        port=config.SUPABASE_POOLER_PORT,
        database=config.DB_NAME or "postgres",
    )
    return sqlalchemy.create_engine(url, pool_pre_ping=True)


def describe_target(config):
    """(description, text to type to confirm) for the database the script would change."""
    if config.DATABASE_URL:
        from sqlalchemy.engine import make_url

        url = make_url(config.DATABASE_URL)
        host = url.host or "localhost"
        return f"DATABASE_URL: database '{url.database}' on {host}:{url.port or 5432}", host
    return (f"{config.APP_ENV} database, Supabase project {config.SUPABASE_PROJECT_REF}",
            config.SUPABASE_PROJECT_REF)


def confirm(description: str, confirm_text: str, what: str, assume_yes: bool) -> bool:
    print(f"Target: {description}")
    if assume_yes:
        return True
    print(what)
    try:
        typed = input(f"Type '{confirm_text}' to continue: ").strip()
    except EOFError:
        typed = ""
    return typed == confirm_text


# --- dry run ----------------------------------------------------------------------------------
def load_rows(engine):
    import sqlalchemy
    from dedupe import Tx

    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("SET TRANSACTION READ ONLY"))
        result = conn.execute(sqlalchemy.text(LOAD_SQL)).fetchall()
        conn.rollback()
    return [
        Tx(id=int(r.id), date=r.transaction_date, amount=Decimal(r.amount), description=r.description or "",
           category=r.category or "", type=r.ttype or "EXPENSE", user_locked=bool(r.user_locked),
           source=r.source or "", added=r.created_at)
        for r in result
    ]


def _cell(text) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _tx_cell(tx) -> str:
    from dedupe import source_label

    edited = " · edited by you" if tx.user_locked else ""
    added = f" · added {tx.added:%d %b}" if tx.added else ""
    label = f"{tx.type.title()} · {tx.category or '?'}"
    return (f"#{tx.id} {_cell(tx.description) or '(empty)'} <br>"
            f"_{_cell(label)} · {_cell(source_label(tx))}{added}{edited}_")


def render_report(plan, plan_id: str, target: str, csv_path: Path) -> str:
    from collections import Counter

    import dedupe

    by_rule = Counter(r.rule for r in plan.removals)
    relabels = sum(1 for r in plan.removals if r.set_category or r.set_type)
    lines = [
        f"# Duplicate transactions: dry run `{plan_id}`",
        "",
        f"{target}. Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. **Nothing was changed.**",
        "",
        "| | |",
        "|---|---|",
        f"| Transactions checked | {plan.transactions} |",
        f"| **To remove** | **{len(plan.removals)} rows** ({', '.join(f'{n} {rule}' for rule, n in by_rule.most_common()) or 'none'}) |",
        f"| Kept rows that take the removed copy's label | {relabels} |",
        f"| To review | {len(plan.reviews)} pairs |",
        f"| Kept as separate payments | {len(plan.keeps)} pairs |",
        "",
    ]
    if plan.overlapping_imports:
        lines += ["## Imports that cover the same dates", ""]
        for first, second, matches, smaller, start, end in plan.overlapping_imports:
            lines.append(f"- {_cell(dedupe.import_label(first))} and {_cell(dedupe.import_label(second))}: "
                         f"{matches} of {smaller} rows match between {start} and {end}")
        lines.append("")

    def table(title, items, with_note):
        out = [f"## {title} ({len(items)})", ""]
        if not items:
            return out + ["None.", ""]
        out += ["| Date | Amount | Keep | Remove | Rule |" + (" Note |" if with_note else ""),
                "|---|---:|---|---|---|" + ("---|" if with_note else "")]
        for item in items:
            note = ""
            if with_note:
                parts = []
                if getattr(item, "set_type", None) or getattr(item, "set_category", None):
                    parts.append("kept row becomes " + " · ".join(
                        p for p in ((item.set_type or "").title(), item.set_category or "") if p))
                if getattr(item, "note", ""):
                    parts.append(item.note)
                note = f" {_cell('; '.join(parts))} |"
            out.append(f"| {item.keep.date} | ₹{Decimal(item.keep.amount):,.2f} | {_tx_cell(item.keep)} "
                       f"| {_tx_cell(item.remove)} | {item.rule} |{note}")
        return out + [""]

    lines += table("To remove", plan.removals, True)
    lines += table("To review (left alone unless you change the decision to remove)", plan.reviews, False)
    keep_rules = Counter(p.rule for p in plan.keeps)
    lines += [f"## Kept as separate payments ({len(plan.keeps)})", ""]
    lines += [f"- {n} × {rule}" for rule, n in keep_rules.most_common()] or ["None."]
    lines += ["", "The full list is in the CSV (decision `keep`).", "",
              "## Next steps", "",
              f"1. Open `{csv_path}` and change the `decision` column where you disagree "
              "(`remove`, `review` or `keep`). To relabel the kept row, fill `set_type` "
              "(EXPENSE, INCOME, TRANSFER) and/or `set_category`. Leave the other columns as they are.",
              "2. Apply (after a fresh backup): "
              f"`APP_ENV=personal venv/bin/python scripts/dedupe_transactions.py --apply {csv_path}`",
              f"3. Undo, if ever needed: `APP_ENV=personal venv/bin/python scripts/dedupe_transactions.py --undo {plan_id}`",
              ""]
    return "\n".join(lines)


def dry_run(config, out_dir: Path) -> int:
    import dedupe

    target, _ = describe_target(config)
    engine = make_engine(config)
    try:
        rows = load_rows(engine)
    finally:
        engine.dispose()
    plan = dedupe.plan_duplicates(rows)

    plan_id = f"dedupe-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out_dir, 0o700)
    csv_path, report_path = out_dir / f"{plan_id}.csv", out_dir / f"{plan_id}.md"
    old_umask = os.umask(0o077)
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as handle:
            dedupe.write_plan_csv(plan, handle)
        report_path.write_text(render_report(plan, plan_id, target, csv_path), encoding="utf-8")
    finally:
        os.umask(old_umask)

    print(f"Target: {target} (read-only; nothing was changed)")
    print(f"Transactions checked: {plan.transactions}")
    for first, second, matches, smaller, start, end in plan.overlapping_imports:
        print(f"Overlapping imports: {dedupe.import_label(first)} + {dedupe.import_label(second)}: "
              f"{matches}/{smaller} rows match ({start} to {end})")
    print(f"To remove: {len(plan.removals)} rows   To review: {len(plan.reviews)} pairs   "
          f"Kept as separate: {len(plan.keeps)} pairs")
    print(f"Plan:   {csv_path}\nReport: {report_path}")
    return 0


# --- apply / undo -----------------------------------------------------------------------------
def apply_plan(engine, approved, plan_id: str) -> dict:
    import sqlalchemy as sa
    from database import SECURITY_HARDENING_SQL
    from dedupe import same_text

    ids = sorted({a.keep_id for a in approved} | {a.remove_id for a in approved})
    stats = {"removed": 0, "files_moved": 0, "kept_rows_relabelled": 0, "warnings": []}
    with engine.begin() as conn:
        conn.execute(sa.text(CREATE_REMOVED_SQL))
        conn.execute(sa.text(SECURITY_HARDENING_SQL))  # RLS on, API roles revoked, new table included
        if conn.execute(sa.text("SELECT count(*) FROM transactions_removed WHERE plan = :plan"),
                        {"plan": plan_id}).scalar():
            raise PlanMismatch(f"plan {plan_id} was already applied (undo it first to apply it again)")

        lock = sa.text("SELECT id, transaction_date, amount, description, user_locked, category_id, "
                       "UPPER(COALESCE(transaction_type, 'EXPENSE')) AS ttype "
                       "FROM transactions WHERE id IN :ids FOR UPDATE").bindparams(sa.bindparam("ids", expanding=True))
        current = {int(r.id): r for r in conn.execute(lock, {"ids": ids})}
        problems = []
        for item in approved:
            for tx_id, text in ((item.keep_id, item.keep_description), (item.remove_id, item.remove_description)):
                row = current.get(tx_id)
                if row is None:
                    problems.append(f"#{tx_id} no longer exists")
                elif (row.transaction_date != item.date
                      or Decimal(row.amount).quantize(Decimal("0.01")) != item.amount
                      or not same_text(row.description, text)):
                    problems.append(f"#{tx_id} changed since the plan was made")
        if problems:
            raise PlanMismatch("; ".join(sorted(set(problems))))

        categories = {str(name).strip().lower(): int(cat_id)
                      for cat_id, name in conn.execute(sa.text("SELECT id, name FROM categories"))}
        relabelled = set()
        for item in approved:
            keep = current[item.keep_id]
            new_category = new_type = None
            if (item.set_category or item.set_type) and item.keep_id not in relabelled:
                if keep.user_locked:
                    stats["warnings"].append(f"#{item.keep_id} was edited by you; its label is unchanged")
                else:
                    if item.set_category:
                        new_category = categories.get(item.set_category.strip().lower())
                        if new_category is None:
                            stats["warnings"].append(f"category {item.set_category!r} doesn't exist; "
                                                     f"#{item.keep_id} keeps its category")
                    if item.set_type and item.set_type != keep.ttype:
                        new_type = item.set_type
            changed = (new_category is not None and new_category != keep.category_id) or new_type is not None
            conn.execute(sa.text(INSERT_REMOVED_SQL), {
                "keep": item.keep_id, "remove": item.remove_id, "rule": item.rule, "plan": plan_id,
                "changed": changed,
            })
            moved = conn.execute(sa.text("UPDATE assets SET transaction_id = :keep WHERE transaction_id = :remove"),
                                 {"keep": item.keep_id, "remove": item.remove_id}).rowcount
            if changed:
                conn.execute(sa.text(
                    "UPDATE transactions SET "
                    "category_id = COALESCE(CAST(:category AS INTEGER), category_id), "
                    "transaction_type = COALESCE(CAST(:type AS VARCHAR), transaction_type), "
                    "is_self_transfer = CASE WHEN CAST(:type AS VARCHAR) IS NULL THEN is_self_transfer "
                    "ELSE CAST(:type AS VARCHAR) = 'TRANSFER' END "
                    "WHERE id = :keep AND NOT user_locked"),
                    {"category": new_category, "type": new_type, "keep": item.keep_id})
                relabelled.add(item.keep_id)
                stats["kept_rows_relabelled"] += 1
            deleted = conn.execute(sa.text("DELETE FROM transactions WHERE id = :remove"),
                                   {"remove": item.remove_id}).rowcount
            if deleted != 1:
                raise PlanMismatch(f"#{item.remove_id} could not be removed")
            stats["removed"] += 1
            stats["files_moved"] += moved
    return stats


def undo_plan(engine, plan_id: str) -> dict:
    import sqlalchemy as sa

    stats = {"restored": 0, "files_moved_back": 0, "kept_rows_relabelled_back": 0}
    with engine.begin() as conn:
        if conn.execute(sa.text("SELECT to_regclass('public.transactions_removed')")).scalar() is None:
            raise PlanMismatch("nothing has been removed by this tool yet (no transactions_removed table)")
        rows = conn.execute(sa.text("SELECT id FROM transactions_removed WHERE plan = :plan ORDER BY id FOR UPDATE"),
                            {"plan": plan_id}).fetchall()
        if not rows:
            plans = [r[0] for r in conn.execute(sa.text(
                "SELECT plan FROM transactions_removed GROUP BY plan ORDER BY max(removed_at) DESC"))]
            raise PlanMismatch(f"no rows removed by plan {plan_id!r}; applied plans: {', '.join(plans) or 'none'}")
        for row in rows:
            params = {"id": int(row.id)}
            conn.execute(sa.text(
                "INSERT INTO transactions "
                "SELECT (jsonb_populate_record(NULL::transactions, r.row_data)).* "
                "FROM transactions_removed r WHERE r.id = :id"), params)
            stats["files_moved_back"] += conn.execute(sa.text(
                "UPDATE assets a SET transaction_id = r.id FROM transactions_removed r "
                "WHERE r.id = :id AND a.id = ANY(r.moved_asset_ids)"), params).rowcount
            stats["kept_rows_relabelled_back"] += conn.execute(sa.text(RESTORE_KEPT_SQL), params).rowcount
            conn.execute(sa.text("DELETE FROM transactions_removed WHERE id = :id"), params)
            stats["restored"] += 1
    return stats


def main(argv=None) -> int:
    args = _parse_args(argv)
    import config
    import dedupe

    if not args.apply and not args.undo:
        return dry_run(config, args.out)

    description, confirm_text = describe_target(config)
    if args.apply:
        plan_id = args.apply.stem
        try:
            with open(args.apply, newline="", encoding="utf-8") as handle:
                approved = dedupe.read_approved_removals(handle)
        except (OSError, dedupe.PlanError) as error:
            print(f"Plan not applied: {error}", file=sys.stderr)
            return 2
        if not approved:
            print("The plan has no rows marked 'remove'; nothing to do.")
            return 0
        what = (f"This removes {len(approved)} transactions (plan {plan_id}). They are kept in "
                f"transactions_removed and can be restored with --undo {plan_id}.")
        if not confirm(description, confirm_text, what, args.yes):
            print("Not confirmed; nothing was changed.", file=sys.stderr)
            return 1
        action = lambda engine: apply_plan(engine, approved, plan_id)  # noqa: E731
    else:
        plan_id = args.undo
        if not confirm(description, confirm_text, f"This restores everything plan {plan_id} removed.", args.yes):
            print("Not confirmed; nothing was changed.", file=sys.stderr)
            return 1
        action = lambda engine: undo_plan(engine, plan_id)  # noqa: E731

    engine = make_engine(config)
    try:
        stats = action(engine)
    except PlanMismatch as mismatch:
        print(f"Nothing was changed: {mismatch}", file=sys.stderr)
        return 2
    except Exception as error:  # the transaction rolled back
        print(f"Failed ({type(error).__name__}); nothing was changed: {error}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    for warning in stats.pop("warnings", []):
        print(f"Warning: {warning}")
    print(", ".join(f"{key.replace('_', ' ')}: {value}" for key, value in stats.items()) + f"  (plan {plan_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
