# tests/test_demo_seed.py
"""Demo sample data (app/demo_seed.py, DEC-026): generator properties and safety guards.

No database is touched: the generator is pure, and every guard tested here refuses before
connecting. The real bootstrap ran against the demo Supabase project (see DEC-026).
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

import config
import demo_seed
from database import compute_row_signature

TODAY = date(2026, 10, 19)
ROWS = demo_seed.generate_demo_transactions(TODAY)


def test_deterministic_for_the_same_day():
    assert demo_seed.generate_demo_transactions(TODAY) == ROWS


def test_about_18_months_and_nothing_in_the_future():
    dates = [r["transaction_date"] for r in ROWS]
    assert max(dates) <= TODAY
    assert min(dates) <= TODAY - timedelta(days=500)
    assert len({(d.year, d.month) for d in dates}) == 18
    assert any(d >= TODAY - timedelta(days=3) for d in dates), "recent days should have data"


def test_rows_are_valid_ledger_rows():
    signatures = set()
    for r in ROWS:
        assert r["category_id"] in demo_seed.CATEGORIES
        assert r["transaction_type"] in ("EXPENSE", "INCOME", "TRANSFER")
        assert r["amount"] > 0 and r["amount"] == r["amount"].quantize(Decimal("0.01"))
        assert 0 <= r["ai_confidence_score"] <= 100
        assert r["is_self_transfer"] == (r["transaction_type"] == "TRANSFER")
        assert r["row_signature"] == compute_row_signature(
            r["transaction_date"], r["amount"], r["description"], r["transaction_type"]
        )
        signatures.add(r["row_signature"])
    assert len(signatures) == len(ROWS), "row signatures must be unique"


def test_every_screen_has_something_to_show():
    assert 6 <= sum(r["requires_human_review"] for r in ROWS) <= 10
    assert len(demo_seed.possible_duplicate_groups(ROWS)) == 1
    assert any(r["user_locked"] for r in ROWS)
    months = demo_seed.monthly_summary(ROWS)
    expenses = sorted(m["expense"] for m in months)
    diwali_month = demo_seed.diwali_date(2025).strftime("%Y-%m")
    assert next(m for m in months if m["month"] == diwali_month)["expense"] > expenses[len(expenses) // 2]


@pytest.mark.parametrize("env,ref,url", [
    ("personal", "amvigyhwiidzmpccvrfa", ""),                     # wrong environment
    ("demo", config.PERSONAL_SUPABASE_PROJECT_REF, ""),           # demo pointed at personal project
    ("demo", "amvigyhwiidzmpccvrfa", f"postgresql://postgres.{config.PERSONAL_SUPABASE_PROJECT_REF}:x@h/postgres"),
])
def test_guard_refuses_anything_but_the_demo(monkeypatch, env, ref, url):
    monkeypatch.setattr(config, "APP_ENV", env)
    monkeypatch.setattr(config, "SUPABASE_PROJECT_REF", ref)
    monkeypatch.setattr(config, "DATABASE_URL", url)
    with pytest.raises(demo_seed.DemoResetRefused):
        demo_seed.check_demo_target(None)
    with pytest.raises(demo_seed.DemoResetRefused):
        demo_seed.reset_demo_data(engine=None, today=TODAY)  # refuses before touching a database
