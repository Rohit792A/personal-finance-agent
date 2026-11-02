#!/usr/bin/env python3
"""Reset the demo database to the sample data (Phase 1, see DEC-025 in decision.md).

Run from the repository root. The demo environment's settings pick the demo Supabase project and
read its password (`pfa-db-password-demo`) from Secret Manager:

    # First time only: mark the new, empty demo database as the demo, then load the sample data.
    APP_ENV=demo venv/bin/python scripts/demo_reset.py --mark-demo

    # Afterwards (Cloud Scheduler normally does this nightly through /internal/demo-reset):
    APP_ENV=demo venv/bin/python scripts/demo_reset.py

    # Against a throwaway local Postgres:
    APP_ENV=demo DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres \\
        venv/bin/python scripts/demo_reset.py --mark-demo --yes

    # Preview the sample data without connecting to any database:
    venv/bin/python scripts/demo_reset.py --dry-run --today 2026-10-18

Safety: the reset only runs when APP_ENV=demo, the target is not the personal Supabase project,
and the database itself carries the demo marker (see app/demo_seed.py). `--mark-demo` writes that
marker, only into a database without user data, after you type the target's project ref (or host
for DATABASE_URL) to confirm.

Exit codes: 0 done, 1 not confirmed or failed, 2 refused by a safety check.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))  # app/ is the import root (DEC-025)


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Restore the demo database to the generated sample data.",
    )
    parser.add_argument("--mark-demo", action="store_true",
                        help="first run only: mark the (empty) target database as the demo database")
    parser.add_argument("--yes", action="store_true",
                        help="skip the typed confirmation for --mark-demo")
    parser.add_argument("--today", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD",
                        help="generate data ending on this date (default: today in India)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the sample data summary and monthly totals; touch no database")
    return parser.parse_args(argv)


def _describe_target(config):
    """(description, text to type to confirm) for the database this backend would connect to."""
    if config.DATABASE_URL:
        from sqlalchemy.engine import make_url

        url = make_url(config.DATABASE_URL)
        host = url.host or "localhost"
        return (f"DATABASE_URL: database '{url.database}' on {host}:{url.port or 5432} "
                f"as user '{url.username}'"), host
    if config.INSTANCE_CONNECTION_NAME:
        return f"Cloud SQL instance {config.INSTANCE_CONNECTION_NAME}", config.INSTANCE_CONNECTION_NAME
    return (f"Supabase project {config.SUPABASE_PROJECT_REF} "
            f"({config.SUPABASE_POOLER_HOST}:{config.SUPABASE_POOLER_PORT}, database '{config.DB_NAME}', "
            f"user '{config.DB_USER}.{config.SUPABASE_PROJECT_REF}')"), config.SUPABASE_PROJECT_REF


def _print_preview(demo_seed, today):
    rows = demo_seed.generate_demo_transactions(today)
    print(json.dumps(demo_seed.summarize(rows, today), indent=2))
    print(f"\n{'Month':8} {'Rows':>5} {'Income':>12} {'Expense':>12} {'Net':>12} {'Saved':>7} {'Review':>6}")
    for month in demo_seed.monthly_summary(rows):
        print(f"{month['month']:8} {month['count']:5} {month['income']:12,.0f} {month['expense']:12,.0f} "
              f"{month['net']:12,.0f} {month['savings_rate_pct']:6.1f}% {month['review']:6}")


def main(argv=None) -> int:
    args = _parse_args(argv)
    import config
    import demo_seed

    today = args.today or demo_seed.india_today()
    if args.dry_run:
        _print_preview(demo_seed, today)
        return 0

    # Configuration checks first, before any connection is made.
    try:
        demo_seed.check_demo_target(None, "reset the demo database")
    except demo_seed.DemoResetRefused as refusal:
        print(f"Refused: {refusal}", file=sys.stderr)
        return 2

    description, confirm_text = _describe_target(config)
    print(f"Environment: {config.APP_ENV}")
    print(f"Target:      {description}")
    if args.mark_demo and not args.yes:
        print("--mark-demo marks this database as the DEMO database. From then on its data can be "
              "wiped and replaced every night.")
        try:
            typed = input(f"Type '{confirm_text}' to continue: ").strip()
        except EOFError:
            typed = ""
        if typed != confirm_text:
            print("Not confirmed; nothing was changed.", file=sys.stderr)
            return 1

    from database import DatabaseManager

    try:
        engine = DatabaseManager().pool  # also creates any missing tables
        if args.mark_demo:
            demo_seed.mark_database_as_demo(engine)
            print("Demo marker present: app_meta(environment = demo).")
        summary = demo_seed.reset_demo_data(engine, today=today)
    except demo_seed.DemoResetRefused as refusal:
        print(f"Refused: {refusal}", file=sys.stderr)
        return 2
    except Exception as error:  # report briefly; the reset rolled back, old data is intact
        print(f"Demo reset failed ({type(error).__name__}): {error}", file=sys.stderr)
        return 1
    finally:
        DatabaseManager.dispose_shared_pool()

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
