# tests/test_reconcile.py
"""Import-time duplicate rules (app/database.py, DEC-034): when an incoming row is one that is
already in the ledger, and how the labels of the two are merged.

The narrations are modelled on the personal data: HDFC Gmail alerts, Kotak and SBI statement
lines (12-digit UPI references), monthly statements without references, and your own phone
number inside every UPI transfer.
"""
from datetime import date

import pytest

import database
from database import (
    MATCH_ALERT_AND_STATEMENT,
    MATCH_RELABELLED,
    MATCH_SAME_REFERENCE,
    MATCH_SAME_WORDING,
    account_hint,
    account_hints,
    description_similarity,
    extract_reference_numbers,
    merged_labels,
    reconcile_rule,
)

DAY = date(2026, 9, 5)
NEXT_DAY = date(2026, 9, 6)
ALERT_OUT = "[HDFC Alert] ❗ You have done a UPI txn. Check details! (Ref: 624860229214)"
KOTAK_IN = "UPI/ROHIT KUSHWAHA/HDFC/624860229214/Salary Aug UPI-624887031856"


def rule(incoming, existing, incoming_type="EXPENSE", existing_type="EXPENSE", incoming_day=DAY, existing_day=DAY):
    return reconcile_rule(incoming_day, incoming, incoming_type, existing_day, existing, existing_type)


def test_reference_numbers_are_12_digits():
    assert extract_reference_numbers("UPI/9000000001@kotak/625113803877/self") == {"625113803877"}
    assert extract_reference_numbers("(Ref: UPI-624887031856)") == {"624887031856"}
    assert extract_reference_numbers("FD PREMAT PROCEEDS: 6000000002 6000000002TO") == set()  # account number
    assert extract_reference_numbers("card 4111111111111111") == set()  # 16 digits
    assert extract_reference_numbers(None) == set()


def test_same_reference_is_one_payment_whatever_the_labels():
    # The HDFC alert (money out) and the Kotak line (money in) of one transfer, a day apart.
    assert rule(KOTAK_IN, ALERT_OUT, "TRANSFER", "EXPENSE", NEXT_DAY, DAY) == MATCH_SAME_REFERENCE
    # A re-import that called the transfer an income.
    reimport = "UPI/ROHIT KUSHWAHA/HDFC/624860229214/Salary Aug (Ref: UPI-624887031856)"
    assert rule(reimport, KOTAK_IN, "INCOME", "TRANSFER") == MATCH_SAME_REFERENCE


def test_different_references_are_different_payments():
    assert rule("UPI/SWIGGY/627000000001/Order", "UPI/SWIGGY/627000000002/Order") is None


def test_a_shared_phone_number_is_not_a_reference():
    # Two transfers to your own account, a day apart, both carrying your phone number. The old
    # 9-12 digit rule matched them on the phone number and merged two real transfers.
    first = "UPI-ROHIT KUSHWAHA-9000000001@KOTAKBANK-KKBK0000001-625113803877-SELF"
    second = "UPI-ROHIT KUSHWAHA-9000000001@KOTAKBANK-KKBK0000001-661709947151-BACK"
    assert rule(second, first, "TRANSFER", "TRANSFER", NEXT_DAY, DAY) is None
    assert rule(second, first, "TRANSFER", "TRANSFER") is None  # same day: the references still differ


def test_without_a_shared_reference_only_the_same_day_counts():
    monthly = "UPI SURESH KUMAR Rent july"            # monthly statement: no reference numbers
    quarterly = "UPI/SURESH KUMAR/655102547137/Rent july"
    assert rule(quarterly, monthly) == MATCH_SAME_WORDING
    assert rule(quarterly, monthly, incoming_day=NEXT_DAY) is None


def test_alert_and_statement_line_of_the_same_type():
    assert rule("UPI/ZOMATO LTD/627000000555/food", "[HDFC Alert] ZOMATO") == MATCH_ALERT_AND_STATEMENT


def test_a_transfer_labelled_differently_matches_only_with_similar_wording():
    assert rule("UPI/Rohit Kushwaha/655909496650/To back", "UPI Rohit Kushwaha To back",
                "EXPENSE", "TRANSFER") == MATCH_RELABELLED
    assert rule("FD PREMAT PROCEEDS: 6000000002 (Ref: 6000000002TO)", "FD PREMAT PROCEEDS: 6000000002 6000000002TO",
                "INCOME", "TRANSFER") == MATCH_RELABELLED
    assert rule("UPI/MUTUAL FUNDS/SIP", "UPI Rohit Kushwaha self", "EXPENSE", "TRANSFER") is None


def test_an_expense_and_an_income_need_a_shared_reference():
    # Paid ₹500 and got ₹500 back from the same friend on the same day: two transactions.
    assert rule("UPI Amit Sharma dinner", "UPI Amit Sharma dinner", "INCOME", "EXPENSE") is None


def test_same_type_without_anything_in_common():
    assert rule("POS CAFE COFFEE DAY", "UPI HUNGERBOX ORDER") is None


def test_descriptions_that_are_only_a_bank_name():
    # "kotak" is a bank stop word, so these share no merchant word, but the letters are the same
    # (found by replaying the removed duplicates of the monthly Kotak statement).
    assert rule("KOTAK811/620116025326", "KOTAK811", "TRANSFER", "TRANSFER") == MATCH_SAME_WORDING
    assert rule("KOTAK811/620116025326", "UPI/Rohit Kushwaha/620113317074/Kotak card wall",
                "TRANSFER", "TRANSFER") is None  # different references
    assert rule("KOTAK811", "UPI Rohit Kushwaha Kotak card wall", "TRANSFER", "TRANSFER") is None
    # Two rows without any words are not evidence of anything.
    assert rule("", "", "EXPENSE", "EXPENSE") is None
    assert rule("12345", "", "TRANSFER", "EXPENSE") is None


def test_the_type_changes_only_when_two_banks_saw_the_same_payment():
    # The HDFC alert (money out) and the SBI line (money in), same reference: a transfer.
    assert merged_labels("EXPENSE", "Other", "INCOME", "other", between_accounts=True) == ("TRANSFER", "Transfer", True, False)
    assert merged_labels("TRANSFER", "Transfer", "EXPENSE", "other", between_accounts=True) == ("TRANSFER", "Transfer", True, False)
    # Anything else that disagrees on the type keeps the row as it is and asks (found on the
    # demo: a re-exported statement read a coffee purchase as an income).
    assert merged_labels("EXPENSE", "Food", "INCOME", "other") == ("EXPENSE", "Food", False, True)
    assert merged_labels("EXPENSE", "Food", "TRANSFER", "transfer") == ("EXPENSE", "Food", False, True)
    assert merged_labels("TRANSFER", "Transfer", "INCOME", "other") == ("TRANSFER", "Transfer", True, True)


def test_same_type_merges_never_downgrade_the_category():
    assert merged_labels("EXPENSE", "Food", "EXPENSE", "other") == ("EXPENSE", "Food", False, False)
    assert merged_labels("EXPENSE", "Other", "EXPENSE", "food") == ("EXPENSE", "food", False, False)
    assert merged_labels("EXPENSE", "Food", "EXPENSE", "travel") == ("EXPENSE", "travel", False, False)  # as before
    assert merged_labels("TRANSFER", "Transfer", "TRANSFER", "transfer") == ("TRANSFER", "transfer", True, False)


def test_which_bank_a_record_came_from():
    assert account_hint("gmail://strict-bank-alerts", ALERT_OUT) == "hdfc"
    assert account_hint("/tmp/u/a1/Kotak_Bank_2026-09_12XXXXX345.pdf", KOTAK_IN) == "kotak"
    assert account_hint("gs://bucket/2026-10/SBI_2026-10_9067820971730092026.pdf", "UPI/CR/...") == "sbi"
    assert account_hint("/tmp/u/b2/Kotak statement 1 Jun 26-31 Aug 26.pdf", "x") == "kotak"
    assert account_hint("/tmp/u/c3/July 25 to Aug 25 2026.pdf", "x") is None       # bank not named
    assert account_hint("/tmp/u/d4/HDFC to Kotak transfers.pdf", "x") is None      # two banks: unclear
    assert account_hint("/tmp/u/e5/Sbicard statement.pdf", "x") is None            # not a word on its own
    assert account_hint("/tmp/u/f6/IMG20260830060404.jpg", "DMART C21 MALL") is None
    assert account_hint("gmail://strict-bank-alerts", "[HDFC] Chips") is None
    assert account_hint("", "Rent") is None
    # A Kotak row that absorbed the HDFC alert in the cleanup: the alert's file says nothing,
    # the Kotak statement still names the bank.
    assert account_hints(("gmail://strict-bank-alerts", "/tmp/u/a1/Kotak_Bank_2026-09.pdf"), KOTAK_IN) == {"kotak"}
    assert account_hints((), ALERT_OUT) == {"hdfc"}
    assert account_hints(("/tmp/u/c3/July 25 to Aug 25 2026.pdf",), "x") == set()


def test_description_similarity():
    assert description_similarity("Auto 12", "AUTO") == 1.0
    assert description_similarity("Chai Poha", "Hunger Box") == 0.0
    assert description_similarity("HUNGERBOX", "UPI/HUNGERBOX/123456789012/lunch order office") == 1.0
    assert database.is_generic_category(" Other ") and not database.is_generic_category("Food")


def test_upload_messages():
    main = pytest.importorskip("main")
    summary = {"rows": 45, "already": 41, "repeated_in_file": 0, "conflicts": 2,
               "sources": [("Kotak July 2026.pdf", 38), ("Gmail alerts", 3)]}
    assert main._duplicate_notes(summary) == [
        "41 of 45 transactions were already in your ledger (from 'Kotak July 2026.pdf' and your Gmail alerts), "
        "so they weren't added again.",
        "2 of those transactions came with a different type (expense / income / transfer) this time; "
        "the ledger kept its label and marked them for review.",
    ]
    one = {"rows": 1, "already": 1, "repeated_in_file": 0, "sources": [("Kotak statement.pdf", 1)]}
    assert main._duplicate_notes(one, receipt=True) == [
        "This receipt matches a transaction already in your ledger (from 'Kotak statement.pdf'), so it wasn't added again."
    ]
    assert main._duplicate_notes({"rows": 3, "already": 0, "repeated_in_file": 0, "sources": []}) == []
    from datetime import datetime
    assert main._already_imported_message("/tmp/x/Kotak_Bank_2026-09.pdf", datetime(2026, 10, 15, 19, 34), 49) == (
        "'Kotak_Bank_2026-09.pdf' was already imported on 15 Oct 2026 (49 transactions), so nothing was added again."
    )
