# tests/test_reconcile_db.py
"""Import-time duplicate prevention against a real PostgreSQL (DEC-034).

Runs only when TEST_DATABASE_URL points at a throwaway local database, e.g.

    docker run -d --name pfa-reconcile-test -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:55433:5432 postgres:17
    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55433/postgres venv/bin/python -m pytest tests/test_reconcile_db.py

Every test starts from empty tables. The scenarios replay what put duplicates into the personal
database: a re-import that labelled rows differently, Gmail alerts plus the other bank's
statement line, overlapping monthly and quarterly statements, and one file imported twice.
"""
import os
import threading
from datetime import date
from urllib.parse import urlparse

import pytest
import sqlalchemy

import config
import database
from database import DatabaseManager, FileAlreadyImported
from models import ReceiptData

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="set TEST_DATABASE_URL to a throwaway local Postgres")

CATEGORIES = ["Food", "Transfer", "Other", "Rent", "Debt", "Utilities", "Travel", "Salary"]
ALERTS = "gmail://strict-bank-alerts"
SEPT = "/tmp/uploads/a1/Kotak_Bank_2026-09.pdf"
SEPT_AGAIN = "/tmp/uploads/b2/Kotak_Bank_2026-09.pdf"
SBI = "/tmp/uploads/c3/SBI_2026-09.pdf"
JULY = "/tmp/uploads/d4/Kotak July 2026.pdf"
QUARTER = "/tmp/uploads/e5/Kotak statement 1 Jun 26-31 Aug 26.pdf"


def _reset_manager_state():
    if DatabaseManager._shared_pool is not None:
        DatabaseManager._shared_pool.dispose()
    DatabaseManager._shared_pool = None
    DatabaseManager._shared_connector = None
    DatabaseManager._schema_initialized = False
    DatabaseManager._security_hardened = False


@pytest.fixture(scope="module")
def manager():
    host = urlparse(TEST_URL).hostname
    assert host in ("127.0.0.1", "localhost", "::1"), "TEST_DATABASE_URL must be a local throwaway database"
    saved_url = config.DATABASE_URL
    _reset_manager_state()
    config.DATABASE_URL = TEST_URL
    try:
        yield DatabaseManager()
    finally:
        _reset_manager_state()
        config.DATABASE_URL = saved_url


@pytest.fixture
def db(manager):
    with manager.pool.begin() as conn:
        conn.execute(sqlalchemy.text("TRUNCATE assets, transactions, ingested_files, categories RESTART IDENTITY CASCADE"))
        for name in CATEGORIES:
            conn.execute(sqlalchemy.text("INSERT INTO categories (name) VALUES (:n)"), {"n": name})
    return manager


def row(description, amount, day, type="EXPENSE", category="Other", confidence=95.0):
    return ReceiptData(date=date.fromisoformat(day), amount=amount, description=description,
                       category=category, type=type, confidence=confidence)


def ledger(db):
    with db.pool.connect() as conn:
        return conn.execute(sqlalchemy.text("""
            SELECT t.id, t.transaction_date, t.amount, t.description, UPPER(t.transaction_type) AS ttype,
                   COALESCE(c.name, '') AS category, t.is_self_transfer, t.requires_human_review
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id ORDER BY t.id
        """)).fetchall()


def test_reimport_that_labels_rows_differently_adds_nothing(db):
    first = [
        row("UPI/ROHIT KUSHWAHA/HDFC/624860229214/Salary Aug UPI-624887031856", 50000, "2026-09-05", "TRANSFER", "Transfer"),
        row("UPI/Rohit Kushwaha/662320930163/Paratha back UPI-170025053057", 80, "2026-09-14"),
        row("FD PREMAT PROCEEDS: 6000000002 6000000002TO", 13, "2026-09-22", "TRANSFER", "Transfer"),
    ]
    db.save_transactions_batch(first, SEPT)
    again = [
        row("UPI/ROHIT KUSHWAHA/HDFC/624860229214/Salary Aug (Ref: UPI-624887031856)", 50000, "2026-09-05", "INCOME"),
        row("UPI/Rohit Kushwaha/662320930163/Paratha back (Ref: UPI-170025053057)", 80, "2026-09-14", "TRANSFER", "Transfer"),
        row("FD PREMAT PROCEEDS: 6000000002 (Ref: 6000000002TO)", 13, "2026-09-22", "INCOME"),
        row("Int.Pd:1234567890:01-07-2026 to 30-09-2026", 263, "2026-09-30", "INCOME", "Salary"),  # really new
    ]
    ids = db.save_transactions_batch(again, SEPT_AGAIN)

    rows = ledger(db)
    assert len(rows) == 4 and ids[:3] == [1, 2, 3]
    # Same account, so nothing proves which label is right: each row keeps its own and is
    # marked for review.
    assert [(r.ttype, r.category, r.requires_human_review) for r in rows[:3]] == [
        ("TRANSFER", "Transfer", True), ("EXPENSE", "Other", True), ("TRANSFER", "Transfer", True),
    ]
    summary = db.last_batch_summary
    assert (summary["added"], summary["already"], summary["conflicts"]) == (1, 3, 3)
    assert summary["rules"] == {"same reference number": 2, "labelled differently": 1}
    assert summary["sources"] == [("Kotak_Bank_2026-09.pdf", 3)]


def test_a_row_without_a_file_is_not_reported_as_coming_from_the_import(db):
    # DEC-060: a recognised row with no earlier file (entered by hand, or written by an agent) used
    # to be reported as coming from the very file being imported, since the import adds its asset.
    with db.pool.begin() as conn:
        conn.execute(sqlalchemy.text("""
            INSERT INTO transactions (transaction_date, amount, description, transaction_type, row_signature)
            VALUES ('2026-09-14', 80, 'UPI/Rohit Kushwaha/662320930163/Paratha back', 'EXPENSE', 'entered-by-hand-1')
        """))
    db.save_transactions_batch([row("UPI/Rohit Kushwaha/662320930163/Paratha back UPI-170025053057", 80, "2026-09-14")], SEPT)

    summary = db.last_batch_summary
    assert (summary["added"], summary["already"]) == (0, 1)
    assert summary["sources"] == [("entered by hand", 1)]


def test_alert_and_the_other_banks_statement_become_one_transfer(db):
    db.save_transactions_batch([
        row("[HDFC Alert] ❗ You have done a UPI txn. Check details! (Ref: 624860798457)", 3000, "2026-09-05"),
        row("[HDFC Alert] View: Account update for your HDFC Bank A/c (Ref: 214911308243)", 5, "2026-09-05", "INCOME"),
    ], ALERTS)
    db.save_transactions_batch([
        row("UPI/CR/624860798457/ROHIT KU/HDFC/9000000001/Edu L", 3000, "2026-09-05", "INCOME"),
        row("UPI/DR/214911308243/ROHIT KU/HDFC/9000000001/Self", 5, "2026-09-05"),
    ], SBI)

    rows = ledger(db)
    assert [(r.description[:14], r.ttype, r.category, r.is_self_transfer) for r in rows] == [
        ("UPI/CR/6248607", "TRANSFER", "Transfer", True),  # the statement narration replaces the alert text
        ("UPI/DR/2149113", "TRANSFER", "Transfer", True),
    ]
    assert db.last_batch_summary["sources"] == [("Gmail alerts", 2)]


def test_three_records_of_one_transfer_end_as_one_transfer(db):
    # HDFC alert (money out), then Kotak's statement (money in), then HDFC's own statement line.
    ref = "(Ref: 624860229214)"
    db.save_transactions_batch([row(f"[HDFC Alert] ❗ You have done a UPI txn. Check details! {ref}", 50000, "2026-09-05")], ALERTS)
    db.save_transactions_batch([row("UPI/ROHIT KUSHWAHA/HDFC/624860229214/Salary Aug", 50000, "2026-09-05", "INCOME")], SEPT)
    db.save_transactions_batch([row("UPI-ROHIT KUSHWAHA-KKBK0000123-624860229214-SALARY AUG", 50000, "2026-09-05")],
                               "/tmp/uploads/j1/HDFC_Bank_2026-09.pdf")
    rows = ledger(db)
    assert [(r.ttype, r.category, r.requires_human_review) for r in rows] == [("TRANSFER", "Transfer", False)]
    assert db.last_batch_summary["conflicts"] == 0
    with db.pool.connect() as conn:
        files = [f for (f,) in conn.execute(sqlalchemy.text("SELECT gcs_uri FROM assets ORDER BY id"))]
    assert files == [ALERTS, SEPT, "/tmp/uploads/j1/HDFC_Bank_2026-09.pdf"]


def test_a_shared_phone_number_does_not_merge_two_payments(db):
    db.save_transactions_batch([row("UPI-ROHIT KUSHWAHA-9000000001@KOTAKBANK-625113803877-SELF", 500, "2026-09-08", "TRANSFER")], SEPT)
    db.save_transactions_batch([row("UPI-ROHIT KUSHWAHA-9000000001@KOTAKBANK-661709947151-BACK", 500, "2026-09-09", "TRANSFER")], SBI)
    assert len(ledger(db)) == 2


def test_same_day_expense_and_income_stay_apart(db):
    db.save_transactions_batch([row("UPI Amit Sharma dinner", 650, "2026-07-06")], JULY)
    db.save_transactions_batch([row("UPI Amit Sharma dinner", 650, "2026-07-06", "INCOME")], QUARTER)
    assert [r.ttype for r in ledger(db)] == ["EXPENSE", "INCOME"]


def test_overlapping_monthly_and_quarterly_statements(db):
    db.save_transactions_batch([
        row("UPI SURESH KUMAR Rent july", 7500, "2026-07-04", category="Rent"),
        row("UPI Rohit Kushwaha To back", 25, "2026-07-12", "TRANSFER", "Transfer"),
        row("BMRCL METRO RIDE", 20, "2026-07-18"),
        row("BMRCL METRO RIDE", 20, "2026-07-18"),  # two rides; identical lines are saved once (known limit)
    ], JULY)
    assert db.last_batch_summary["repeated_in_file"] == 1
    db.save_transactions_batch([
        row("UPI/SURESH KUMAR/655102547137/Rent july", 7500, "2026-07-04"),          # "Other" this time
        row("UPI/Rohit Kushwaha/655909496650/To back", 25, "2026-07-12"),            # called an expense
        row("UPI/BMRCL/619900000001/metro ride", 20, "2026-07-18"),
        row("UPI/BMRCL/619900000002/metro ride", 20, "2026-07-18"),                  # the second ride
    ], QUARTER)

    rows = ledger(db)
    assert [(r.description, r.ttype, r.category, r.requires_human_review) for r in rows] == [
        ("UPI SURESH KUMAR Rent july", "EXPENSE", "Rent", False),   # specific category kept
        ("UPI Rohit Kushwaha To back", "TRANSFER", "Transfer", True),  # the other copy said expense
        ("BMRCL METRO RIDE", "EXPENSE", "Other", False),
        ("UPI/BMRCL/619900000002/metro ride", "EXPENSE", "Other", False),
    ]
    summary = db.last_batch_summary
    assert (summary["added"], summary["already"], summary["conflicts"]) == (1, 3, 1)


def test_a_misread_reexport_does_not_turn_a_purchase_into_a_transfer(db):
    # Found on the demo: the re-issued statement's reader called the coffee purchase an income.
    db.save_transactions_batch([row("UPI/BLUE TOKAI COFFEE/629900001111/Coffee beans", 347, "2026-10-14", category="Food")],
                               "/tmp/uploads/g7/Demo_Bank_Statement_Oct_2026.pdf")
    db.save_transactions_batch([row("UPI-BLUE TOKAI-629900001111-COFFEE", 347, "2026-10-14", "INCOME")],
                               "/tmp/uploads/h8/Demo_Bank_Statement_Oct_2026_reissued.pdf")
    db.save_transactions_batch([row("UPI-BLUE TOKAI-629900001111-COFFEE", 347, "2026-10-14", "TRANSFER")],
                               "/tmp/uploads/i9/Kotak_Bank_statement.pdf")  # one bank named, the other not: unproven
    assert [(r.ttype, r.category, r.requires_human_review) for r in ledger(db)] == [("EXPENSE", "Food", True)]
    assert db.last_batch_summary["conflicts"] == 1


def test_rows_of_one_statement_never_merge(db):
    db.save_transactions_batch([
        row("UPI/SWIGGY/627000000001/Order", 250, "2026-09-02"),
        row("UPI/SWIGGY/627000000002/Order", 250, "2026-09-02"),
        row("BMRCL METRO 09:10", 40, "2026-09-03"),
        row("BMRCL METRO 18:40", 40, "2026-09-03"),
    ], SEPT)
    assert len(ledger(db)) == 4 and db.last_batch_summary["added"] == 4


def test_identical_row_keeps_its_specific_category(db):
    db.save_transactions_batch([row("UPI/SWIGGY/627000000001/Order", 250, "2026-09-02", category="Food")], SEPT)
    # Re-imported twice in one file with "Other": the first copy is matched, the second is an
    # exact copy that reaches the row through its signature. Neither may downgrade it.
    copy = row("UPI/SWIGGY/627000000001/Order", 250, "2026-09-02", category="Other", confidence=60)
    db.save_transactions_batch([copy, copy], SEPT_AGAIN)
    rows = ledger(db)
    assert [(r.category, r.requires_human_review) for r in rows] == [("Food", False)]
    assert db.last_batch_summary["rules"] == {"same reference number": 1, "identical row": 1}


def test_rows_you_edited_are_left_alone(db):
    db.save_transactions_batch([row("UPI/Rohit Kushwaha/662320930163/Paratha", 80, "2026-09-14", category="Food")], SEPT)
    with db.pool.begin() as conn:
        conn.execute(sqlalchemy.text("UPDATE transactions SET user_locked = TRUE"))
    db.save_transactions_batch([row("UPI/Rohit Kushwaha/662320930163/Paratha back", 80, "2026-09-14", "TRANSFER")], SEPT_AGAIN)
    assert [(r.ttype, r.category, r.description) for r in ledger(db)] == [
        ("EXPENSE", "Food", "UPI/Rohit Kushwaha/662320930163/Paratha")
    ]


def test_a_receipt_for_a_statement_line_is_not_added_again(db):
    db.save_transactions_batch([row("PCD/8839 DMART C21 MALL IN/DORIND290826/11:50", 774.80, "2026-08-29")], QUARTER)
    db.save_transaction(row("DMART C21 MALL", 774.80, "2026-08-29", category="Food"), "/tmp/uploads/f6/IMG2026.jpg")
    rows = ledger(db)
    assert [(r.description[:9], r.category) for r in rows] == [("PCD/8839 ", "Food")]
    assert db.last_batch_summary["already"] == 1


def test_the_same_file_is_imported_once(db):
    batch = [row("UPI/SWIGGY/627000000001/Order", 250, "2026-09-02")]
    db.save_transactions_batch(batch, SEPT, file_hash="a" * 64, file_name="Kotak_Bank_2026-09.pdf")
    with pytest.raises(FileAlreadyImported) as already:
        db.save_transactions_batch(batch + [row("Something new", 1, "2026-09-03")], SEPT_AGAIN,
                                   file_hash="a" * 64, file_name="Kotak_Bank_2026-09.pdf")
    assert already.value.file_name == "Kotak_Bank_2026-09.pdf" and already.value.rows_imported == 1
    assert len(ledger(db)) == 1  # nothing from the second attempt
    name, imported_at, rows_imported = db.find_ingested_file("a" * 64)
    assert (name, rows_imported) == ("Kotak_Bank_2026-09.pdf", 1) and imported_at is not None
    assert db.find_ingested_file("b" * 64) is None


def test_two_imports_of_one_file_at_the_same_time(db):
    """Two Gmail syncs processing the same statement at once: one saves, the other adds nothing."""
    batch = [row(f"UPI/SHOP/62700000{n:04d}/item", 100 + n, "2026-09-10") for n in range(20)]
    start, outcomes = threading.Barrier(2), []

    def run(path):
        start.wait()
        try:
            db.save_transactions_batch(batch, path, file_hash="c" * 64, file_name="statement.pdf")
            outcomes.append("saved")
        except FileAlreadyImported:
            outcomes.append("skipped")

    threads = [threading.Thread(target=run, args=(p,)) for p in (SEPT, SEPT_AGAIN)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(outcomes) == ["saved", "skipped"]
    assert len(ledger(db)) == 20
