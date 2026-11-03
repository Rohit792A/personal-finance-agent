# tests/test_budgets.py
"""GET /budgets (api.get_budgets): monthly caps for one month, scaled caps for All Time (DEC-073).

Budgets are monthly caps. All Time adds up the spending of every month in the ledger, so it compares
that with each monthly cap times the number of months that have expenses. It used to compare all of
it with a single month's caps, and the 18-month demo ledger showed every category ~1,800% over budget.

The unit tests answer get_budgets' queries from a small in-memory ledger. The last test runs the real
SQL, and only when TEST_DATABASE_URL points at a throwaway local PostgreSQL, e.g.

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55433/postgres venv/bin/python -m pytest tests/test_budgets.py
"""
import os
from datetime import date
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import sqlalchemy

import api
import config
from database import DatabaseManager

MONTHLY_BUDGETS = {"Food": 16000.0, "Travel": 4000.0, "Utilities": 4500.0}

# (day, category, type, amount). August to October have expenses; November only a salary credit.
LEDGER = [
    (date(2026, 8, 1), "Rent", "EXPENSE", 30000.0),  # no budget: not part of the comparison
    (date(2026, 8, 1), "Paycheck", "INCOME", 140000.0),  # income is never spending
    (date(2026, 8, 3), "Food", "EXPENSE", 15000.0),
    (date(2026, 8, 9), "Travel", "EXPENSE", 1000.0),
    (date(2026, 9, 4), "Food", "EXPENSE", 18000.0),
    (date(2026, 9, 20), "Travel", "EXPENSE", 9000.0),
    (date(2026, 10, 2), "Food", "EXPENSE", 14000.0),
    (date(2026, 11, 1), "Paycheck", "INCOME", 140000.0),  # a month without expenses adds no budget
]


def by_category(result):
    return {row["category"]: row for row in result["categories"]}


def check_all_time(result):
    """Three months with expenses: every cap is its monthly budget x 3."""
    assert result["months_count"] == 3
    assert result["monthly_total_budget"] == 24500.0
    assert result["total_budget"] == 73500.0
    assert result["total_spent"] == 57000.0  # Food + Travel; Rent has no budget
    assert result["overall_utilization_pct"] == 77.6
    assert [row["category"] for row in result["categories"]] == ["Food", "Travel", "Utilities"]  # by spend
    assert by_category(result) == {
        # Before DEC-073: 47,000 against 16,000 = 293.8%, "exceeded".
        "Food": {"category": "Food", "budget": 48000.0, "monthly_budget": 16000.0, "spent": 47000.0,
                 "remaining": 1000.0, "utilization_pct": 97.9, "status": "warning"},
        "Travel": {"category": "Travel", "budget": 12000.0, "monthly_budget": 4000.0, "spent": 10000.0,
                   "remaining": 2000.0, "utilization_pct": 83.3, "status": "warning"},
        "Utilities": {"category": "Utilities", "budget": 13500.0, "monthly_budget": 4500.0, "spent": 0.0,
                      "remaining": 13500.0, "utilization_pct": 0.0, "status": "ok"},
    }


def check_september(result):
    """One month: the caps are the monthly budgets."""
    assert result["month"] == "2026-09"
    assert result["months_count"] == 1
    assert result["total_budget"] == result["monthly_total_budget"] == 24500.0
    assert result["total_spent"] == 27000.0
    assert result["overall_utilization_pct"] == 110.2
    rows = by_category(result)
    assert {name: row["budget"] for name, row in rows.items()} == MONTHLY_BUDGETS
    assert all(row["monthly_budget"] == row["budget"] for row in rows.values())
    assert rows["Food"]["utilization_pct"] == 112.5 and rows["Food"]["status"] == "exceeded"
    assert rows["Food"]["remaining"] == 0.0
    assert rows["Travel"]["utilization_pct"] == 225.0 and rows["Travel"]["status"] == "exceeded"
    assert rows["Utilities"]["spent"] == 0.0 and rows["Utilities"]["status"] == "ok"


# ---------------------------------------------------------------------------------------------
# Unit tests: get_budgets' queries answered from LEDGER
# ---------------------------------------------------------------------------------------------

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class _FakeConnection:
    """Answers the spend, month-count and budget queries the way PostgreSQL would for `ledger`."""

    def __init__(self, ledger, budgets):
        self.ledger, self.budgets = ledger, budgets
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.queries.append(sql)
        month = (params or {}).get("month")
        expenses = [row for row in self.ledger
                    if row[2] == "EXPENSE" and (month is None or row[0].strftime("%Y-%m") == month)]
        if "FROM budgets" in sql:
            return _Result(sorted(self.budgets.items()))
        if "COUNT(DISTINCT" in sql:
            return _Result([(len({row[0].strftime("%Y-%m") for row in expenses}),)])
        if "SUM(t.amount)" in sql:
            spent = {}
            for _, category, _, amount in expenses:
                spent[category] = spent.get(category, 0.0) + amount
            return _Result(list(spent.items()))
        raise AssertionError(f"unexpected query: {sql}")


@pytest.fixture
def fake_db(monkeypatch):
    """Points api.get_budgets at an in-memory ledger; returns the connections it opened."""
    connections = []

    def install(ledger=LEDGER, budgets=MONTHLY_BUDGETS):
        def connect():
            connections.append(_FakeConnection(ledger, budgets))
            return connections[-1]

        class FakeDatabase:
            pool = SimpleNamespace(connect=connect)

            def close(self):
                pass

        monkeypatch.setattr(api, "DatabaseManager", FakeDatabase)
        return connections

    return install


def test_all_time_scales_each_monthly_cap_by_the_months_with_expenses(fake_db):
    fake_db()
    result = api.get_budgets()
    assert result["month"] == "ALL"
    check_all_time(result)


@pytest.mark.parametrize("month", ["ALL", "all"])
def test_month_all_is_all_time(fake_db, month):
    fake_db()
    result = api.get_budgets(month=month)
    assert result["month"] == month
    check_all_time(result)


def test_one_month_uses_the_monthly_caps(fake_db):
    connections = fake_db()
    check_september(api.get_budgets(month="2026-09"))
    # The month count only matters for All Time, so it isn't queried for one month.
    assert not any("COUNT(DISTINCT" in sql for sql in connections[-1].queries)


def test_an_empty_ledger_shows_the_monthly_caps(fake_db):
    fake_db(ledger=[])
    result = api.get_budgets()
    assert result["months_count"] == 1
    assert result["total_budget"] == result["monthly_total_budget"] == 24500.0
    assert result["total_spent"] == 0.0 and result["overall_utilization_pct"] == 0
    assert all(row["budget"] == row["monthly_budget"] and row["status"] == "ok" for row in result["categories"])


def test_no_budgets_means_no_comparison(fake_db):
    fake_db(budgets={})
    result = api.get_budgets()
    assert result["categories"] == []
    assert result["total_budget"] == 0.0 and result["overall_utilization_pct"] == 0


@pytest.mark.parametrize("spent, shown, status", [
    (9995.0, 99.9, "warning"),     # 99.95% would round up to 100.0 and read as over budget
    (9999.99, 99.9, "warning"),
    (10000.0, 100.0, "exceeded"),  # reaching the cap is 100%
    (10004.0, 100.0, "exceeded"),
    (7996.0, 80.0, "warning"),     # the 80% warning follows the shown value
])
def test_spending_under_the_cap_never_shows_as_100_percent(fake_db, spent, shown, status):
    fake_db(ledger=[(date(2026, 9, 4), "Food", "EXPENSE", spent)], budgets={"Food": 10000.0})
    result = api.get_budgets(month="2026-09")
    (food,) = result["categories"]
    assert (food["utilization_pct"], food["status"]) == (shown, status)
    assert result["overall_utilization_pct"] == shown


# ---------------------------------------------------------------------------------------------
# The real SQL against PostgreSQL (TEST_DATABASE_URL only)
# ---------------------------------------------------------------------------------------------

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")


def _reset_manager_state():
    if DatabaseManager._shared_pool is not None:
        DatabaseManager._shared_pool.dispose()
    DatabaseManager._shared_pool = None
    DatabaseManager._shared_connector = None
    DatabaseManager._schema_initialized = False
    DatabaseManager._security_hardened = False


@pytest.mark.skipif(not TEST_URL, reason="set TEST_DATABASE_URL to a throwaway local Postgres")
def test_against_postgres():
    assert urlparse(TEST_URL).hostname in ("127.0.0.1", "localhost", "::1"), \
        "TEST_DATABASE_URL must be a local throwaway database"
    saved_url = config.DATABASE_URL
    _reset_manager_state()
    config.DATABASE_URL = TEST_URL
    try:
        manager = DatabaseManager()  # creates the schema
        with manager.pool.begin() as conn:
            conn.execute(sqlalchemy.text(
                "TRUNCATE assets, transactions, ingested_files, categories, budgets RESTART IDENTITY CASCADE"))
            for name in sorted({row[1] for row in LEDGER} | set(MONTHLY_BUDGETS)):
                conn.execute(sqlalchemy.text("INSERT INTO categories (name) VALUES (:n)"), {"n": name})
            for name, amount in MONTHLY_BUDGETS.items():
                conn.execute(sqlalchemy.text("INSERT INTO budgets (category, monthly_budget) VALUES (:c, :a)"),
                             {"c": name, "a": amount})
            insert = sqlalchemy.text("""
                INSERT INTO transactions (transaction_date, amount, description, category_id, transaction_type, row_signature)
                VALUES (:day, :amount, :description, (SELECT id FROM categories WHERE name = :category), :type, :sig)
            """)
            for i, (day, category, tx_type, amount) in enumerate(LEDGER):
                conn.execute(insert, {"day": day, "amount": amount, "description": f"{category} {i}",
                                      "category": category, "type": tx_type, "sig": f"budgets-test-{i}"})
            # An expense without a category isn't in the spend total, so its month adds no budget either.
            conn.execute(insert, {"day": date(2026, 12, 5), "amount": 999.0, "description": "Uncategorised",
                                  "category": "(none)", "type": "EXPENSE", "sig": "budgets-test-uncategorised"})

        check_all_time(api.get_budgets())
        check_all_time(api.get_budgets(month="ALL"))
        check_september(api.get_budgets(month="2026-09"))
    finally:
        _reset_manager_state()
        config.DATABASE_URL = saved_url
