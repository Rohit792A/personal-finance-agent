# tests/test_dedupe.py
"""Duplicate-cleanup planning (app/dedupe.py, DEC-032).

The fixtures mirror patterns found in the personal database: a monthly statement and a
quarterly statement that both contain July (different wording, only one has reference numbers,
sometimes a different transfer/expense label), one statement file imported twice a few seconds
apart, same-day payments with different references, both legs of a transfer between your own
accounts, and hand-entered rows without a source file.
"""
import io
from datetime import date, datetime
from decimal import Decimal

import pytest

import dedupe
from dedupe import Tx

JULY = "gs://pfa-statements/uploads/a1/Kotak July 2026.pdf"
QUARTER = "gs://pfa-statements/uploads/b2/Kotak statement 1 Jun 26-31 Aug 26.pdf"
JULY_AGAIN = "gs://pfa-statements/uploads/c3/Kotak July 2026.pdf"
SBI = "gs://pfa-statements/uploads/d4/SBI July 2026.pdf"
HDFC = "gs://pfa-statements/uploads/e5/HDFC Aug 2026.pdf"
SEPT = "gs://pfa-statements/uploads/f6/Kotak_Bank_2026-09.pdf"
ALERTS = "strict-bank-alerts"
ADDED = datetime(2026, 9, 7, 16, 22)


def tx(tx_id, description, amount="100.00", day="2026-07-04", *, category="Other", type="EXPENSE",
       source="", locked=False, added=ADDED):
    return Tx(
        id=tx_id, date=date.fromisoformat(day), amount=Decimal(amount), description=description,
        category=category, type=type, user_locked=locked, source=source, added=added,
    )


def overlap_rows(first_id=1, second_id=101, first=JULY, second=QUARTER):
    """Four July transactions as they appear in both statements."""
    return [
        tx(first_id, "UPI SURESH KUMAR Rent july", "7500.00", "2026-07-04", category="Rent", source=first),
        tx(second_id, "UPI/SURESH KUMAR/123456789137/Rent july", "7500.00", "2026-07-04", category="Rent", source=second),
        tx(first_id + 1, "UPI NPCI BHIM HDFC Cashback", "3.00", "2026-07-01", type="INCOME", source=first),
        tx(second_id + 1, "UPI/NPCI BHIM/HDFC/123456789261/BHIMCASHBACK", "3.00", "2026-07-01", type="INCOME", source=second),
        tx(first_id + 2, "VISA REFUND Flipkart Payments", "788.00", "2026-07-10", type="INCOME", source=first),
        tx(second_id + 2, "VISA- REFUND/123726/123726/FLIPKARTPAYMENTS", "788.00", "2026-07-10", type="INCOME", source=second),
        tx(first_id + 3, "UPI Rohit Kushwaha Education int july", "3000.00", "2026-07-04", category="Debt", source=first),
        tx(second_id + 3, "UPI/Rohit Kushwaha/123456789737/Edu int july", "3000.00", "2026-07-04", source=second),
    ]


def relabel_rows():
    """The monthly statement calls a payment to your own card a transfer; the quarterly one an expense."""
    return overlap_rows() + [
        tx(7, "UPI Rohit Kushwaha Kotak card wall", "10000.00", "2026-07-20", type="TRANSFER", category="Transfer",
           source=JULY),
        tx(107, "UPI/Rohit Kushwaha/123456789074/Kotak card wall", "10000.00", "2026-07-20", source=QUARTER),
        tx(701, "Salary", "500.00", "2026-07-21", type="INCOME"),
        tx(702, "Groceries", "500.00", "2026-07-21"),
    ]


def pairs(plan_items):
    return {(item.keep.id, item.remove.id) for item in plan_items}


def test_overlapping_statements_keep_the_detailed_copy():
    plan = dedupe.plan_duplicates(overlap_rows())

    assert len(plan.overlapping_imports) == 1
    first, second, matches, _smaller, start, end = plan.overlapping_imports[0]
    assert {first, second} == {(JULY, ADDED), (QUARTER, ADDED)} and matches == 4
    assert (start, end) == (date(2026, 7, 1), date(2026, 7, 10))

    # The quarterly copy has reference numbers, so it stays; the refund has none in either file,
    # so the older row stays.
    assert pairs(plan.removals) == {(101, 1), (102, 2), (3, 103), (104, 4)}
    assert {r.rule for r in plan.removals} == {dedupe.OVERLAPPING_STATEMENTS}
    assert not plan.reviews


def test_kept_row_takes_the_specific_category_of_the_removed_copy():
    plan = dedupe.plan_duplicates(overlap_rows())
    by_pair = {(r.keep.id, r.remove.id): r for r in plan.removals}

    assert by_pair[(104, 4)].set_category == "Debt"  # kept row only had "Other"
    assert by_pair[(104, 4)].set_type is None        # both are expenses
    assert by_pair[(101, 1)].set_category is None    # both "Rent"


def test_rows_from_the_same_statement_are_never_duplicates():
    rows = overlap_rows() + [
        tx(201, "UPI/MUTUAL FUNDS I/HDFC/123456789624/Paid Via Ele", "2000.00", "2026-08-11", source=QUARTER),
        tx(202, "UPI/MUTUAL FUNDS I/HDFC/123456789918/Paid Via Ele", "2000.00", "2026-08-11", source=QUARTER),
        tx(203, "BMRCL METRO", "20.00", "2026-08-12", source=QUARTER),
        tx(204, "BMRCL METRO", "20.00", "2026-08-12", source=QUARTER),
    ]
    plan = dedupe.plan_duplicates(rows)

    keeps = {(p.keep.id, p.remove.id): p.rule for p in plan.keeps}
    assert keeps[(201, 202)] == dedupe.SAME_FILE
    assert keeps[(203, 204)] == dedupe.SAME_FILE
    assert not {201, 202, 203, 204} & {r.remove.id for r in plan.removals}


def test_one_file_imported_twice_is_two_imports():
    # The same statement path imported twice, 43 seconds apart. Rows of one import are separate
    # payments; rows of the two imports are copies, even when the second import labelled one
    # differently.
    first, second = datetime(2026, 10, 15, 19, 34, 18), datetime(2026, 10, 15, 19, 35, 1)
    rows = [
        tx(1001, "UPI/SWIGGY/627000000001/Order", "250.00", "2026-09-02", source=SEPT, added=first),
        tx(1002, "UPI/SWIGGY/627000000002/Order", "250.00", "2026-09-02", source=SEPT, added=first),
        tx(1003, "BMRCL METRO", "40.00", "2026-09-03", source=SEPT, added=first),
        tx(1004, "UPI/Rohit Kushwaha/627000000004/Self", "5000.00", "2026-09-05", type="TRANSFER",
           category="Transfer", source=SEPT, added=first),
        tx(1005, "UPI/ACME PAYROLL/627000000005/Salary", "90000.00", "2026-09-07", type="INCOME",
           category="Salary", source=SEPT, added=first),
        tx(1101, "UPI/SWIGGY/627000000002/Order", "250.00", "2026-09-02", source=SEPT, added=second),
        tx(1102, "BMRCL METRO", "40.00", "2026-09-03", source=SEPT, added=second),
        tx(1103, "UPI/Rohit Kushwaha/627000000004/Self", "5000.00", "2026-09-05", source=SEPT, added=second),
    ]
    plan = dedupe.plan_duplicates(rows)

    assert [(a, b, matches) for a, b, matches, *_ in plan.overlapping_imports] == [((SEPT, first), (SEPT, second), 3)]
    assert dedupe.import_label((SEPT, first)) == "Kotak_Bank_2026-09.pdf (imported 15 Oct 19:34)"
    rules = {(r.keep.id, r.remove.id): r.rule for r in plan.removals}
    assert rules == {
        (1002, 1101): dedupe.OVERLAPPING_STATEMENTS,  # same reference number
        (1003, 1102): dedupe.OVERLAPPING_STATEMENTS,  # same wording, no reference in either
        (1004, 1103): dedupe.RELABELLED,              # transfer in one import, expense in the other
    }
    relabelled = next(r for r in plan.removals if r.remove.id == 1103)
    assert relabelled.set_type is None and "types differ" in relabelled.note  # kept row already says transfer
    assert [(p.keep.id, p.remove.id, p.rule) for p in plan.keeps] == [(1001, 1002, dedupe.SAME_FILE)]
    assert not plan.reviews


def test_overlapping_imports_pair_one_to_one():
    # The monthly statement shows two ₹20 rides on 18 July, the quarterly one only one.
    rows = overlap_rows() + [
        tx(5, "BMRCL METRO RIDE", "20.00", "2026-07-18", source=JULY),
        tx(6, "BMRCL METRO RIDE", "20.00", "2026-07-18", source=JULY),
        tx(105, "UPI/BMRCL/123456789001/metro ride", "20.00", "2026-07-18", source=QUARTER),
    ]
    plan = dedupe.plan_duplicates(rows)

    metro_removed = [r for r in plan.removals if r.keep.id == 105]
    assert len(metro_removed) == 1 and metro_removed[0].remove.id in {5, 6}
    leftover = ({5, 6} - {metro_removed[0].remove.id}).pop()
    # The unmatched ride isn't deleted; it is listed for a look.
    assert (105, leftover) in pairs(plan.reviews)


def test_three_copies_keep_one():
    rows = overlap_rows(1, 101, JULY, QUARTER) + [
        tx(301, "UPI SURESH KUMAR Rent july", "7500.00", "2026-07-04", category="Rent", source=JULY_AGAIN),
        tx(302, "UPI NPCI BHIM HDFC Cashback", "3.00", "2026-07-01", type="INCOME", source=JULY_AGAIN),
        tx(303, "VISA REFUND Flipkart Payments", "788.00", "2026-07-10", type="INCOME", source=JULY_AGAIN),
        tx(304, "UPI Rohit Kushwaha Education int july", "3000.00", "2026-07-04", category="Debt", source=JULY_AGAIN),
    ]
    plan = dedupe.plan_duplicates(rows)

    rent = sorted(r.remove.id for r in plan.removals if r.keep.id == 101)
    assert rent == [1, 301]
    assert len(plan.removals) == 8  # each of the 4 transactions: 3 copies, 1 kept
    # Only the first removed copy carries its category over; the second has the same one.
    assert [r.set_category for r in plan.removals if r.keep.id == 104] == ["Debt", None]


def test_shared_reference_and_alert_pairs():
    rows = [
        tx(401, "[HDFC Alert] SWIGGY (Ref: 123456789012)", "450.00", "2026-08-02", source=ALERTS),
        tx(402, "UPI/SWIGGY/123456789012/Order", "450.00", "2026-08-02", source=HDFC),
        tx(403, "[HDFC Alert] ZOMATO", "300.00", "2026-08-03", source=ALERTS),
        tx(404, "UPI/ZOMATO LTD/123456789555/food", "300.00", "2026-08-03", source=HDFC),
    ]
    plan = dedupe.plan_duplicates(rows)

    rules = {(r.keep.id, r.remove.id): r.rule for r in plan.removals}
    assert rules == {(402, 401): dedupe.SAME_REFERENCE, (404, 403): dedupe.ALERT_AND_STATEMENT}


def test_both_sides_of_a_transfer_are_left_for_review():
    # ₹66,556 moved from HDFC to Kotak. HDFC's statement has the "out" leg; Kotak's monthly and
    # quarterly statements both have the "in" leg. All three carry the same UPI reference.
    rows = overlap_rows() + [
        tx(44, "UPI Rohit Kushwaha To make FD", "66556.00", "2026-07-28", type="TRANSFER", category="Transfer",
           source=JULY),
        tx(749, "UPI/Rohit Kushwaha/621229580182/To make FD", "66556.00", "2026-07-28", type="TRANSFER",
           source=QUARTER),
        tx(53, "UPI-ROHIT KUSHWAHA-621229580182-To make FD", "66556.00", "2026-07-28", type="TRANSFER",
           category="Transfer", source=HDFC),
    ]
    plan = dedupe.plan_duplicates(rows)

    by_pair = {(r.keep.id, r.remove.id): r for r in plan.removals}
    assert by_pair[(749, 44)].rule == dedupe.OVERLAPPING_STATEMENTS  # the Kotak copy from the other statement
    assert by_pair[(749, 44)].set_category == "Transfer"
    assert 53 not in {r.remove.id for r in plan.removals}
    assert [(p.keep.id, p.remove.id, p.rule) for p in plan.reviews] == [(53, 749, dedupe.TRANSFER_SIDES)]


def test_different_reference_numbers_keep_both():
    rows = [
        tx(501, "UPI/GROWW INVEST T/HDFC/123456789374/Paid Via Ele", "2000.00", "2026-08-20", source=HDFC),
        tx(502, "[HDFC Alert] GROWW (Ref: 123456789929)", "2000.00", "2026-08-20", source=ALERTS),
    ]
    plan = dedupe.plan_duplicates(rows)

    assert not plan.removals and not plan.reviews
    assert [(p.rule) for p in plan.keeps] == [dedupe.DIFFERENT_REFERENCES]


def test_phone_numbers_are_not_reference_numbers():
    assert dedupe.references("UPI/9876543210@ybl/123456789012/rent") == {"123456789012"}
    assert dedupe.references("FD PREMAT PROCEEDS: 1234567651") == set()


def test_similarity():
    assert dedupe.similarity("Auto 12", "AUTO") == 1.0  # same letters, digits ignored
    assert dedupe.similarity("Chai Poha", "Hunger Box") == 0.0
    assert dedupe.similarity("Nippon Multi Cap", "Nippon Large Cap") == pytest.approx(2 / 3)
    # A short description inside a long narration counts as similar (containment), although
    # only one of the narration's five words is shared.
    assert dedupe.similarity("HUNGERBOX", "UPI/HUNGERBOX/123456789012/lunch order office cafeteria") == 1.0


def test_hand_entered_rows_review_or_keep():
    rows = [
        tx(601, "Nippon Multi Cap", "2000.00", "2026-04-10", category="SIP"),
        tx(602, "Nippon Large Cap", "2000.00", "2026-04-10", category="SIP"),
        tx(603, "Chai Poha", "84.00", "2025-05-30", category="Food"),
        tx(604, "Hunger Box", "84.00", "2025-05-30", category="Food"),
    ]
    plan = dedupe.plan_duplicates(rows)

    assert not plan.removals
    assert [(p.keep.id, p.remove.id, p.rule) for p in plan.reviews] == [(601, 602, dedupe.SIMILAR_DESCRIPTION)]
    assert [(p.keep.id, p.remove.id, p.rule) for p in plan.keeps] == [(603, 604, dedupe.DIFFERENT_DESCRIPTION)]


def test_your_edits_win():
    rows = overlap_rows()
    rows[0] = tx(1, "UPI SURESH KUMAR Rent july", "7500.00", "2026-07-04", category="Rent", source=JULY, locked=True)
    plan = dedupe.plan_duplicates(rows)
    assert (1, 101) in pairs(plan.removals)  # the edited row stays even without a reference

    rows[1] = tx(101, "UPI/SURESH KUMAR/123456789137/Rent july", "7500.00", "2026-07-04", category="Rent",
                 source=QUARTER, locked=True)
    plan = dedupe.plan_duplicates(rows)
    assert not {1, 101} & {r.remove.id for r in plan.removals}
    assert [(p.rule) for p in plan.reviews if {p.keep.id, p.remove.id} == {1, 101}] == [dedupe.EDITED_COPIES]


def test_edited_kept_row_keeps_its_category_and_conflicts_are_noted():
    rows = overlap_rows()
    rows[7] = tx(104, "UPI/Rohit Kushwaha/123456789737/Edu int july", "3000.00", "2026-07-04", source=QUARTER,
                 locked=True)
    rows[1] = tx(101, "UPI/SURESH KUMAR/123456789137/Rent july", "7500.00", "2026-07-04", category="Home",
                 source=QUARTER)
    plan = dedupe.plan_duplicates(rows)
    by_pair = {(r.keep.id, r.remove.id): r for r in plan.removals}

    assert by_pair[(104, 4)].set_category is None  # you edited the kept row: its category stays
    assert "categories differ" in by_pair[(101, 1)].note


def test_overlapping_statements_labelled_differently_are_removed_and_relabelled():
    plan = dedupe.plan_duplicates(relabel_rows())
    by_pair = {(r.keep.id, r.remove.id): r for r in plan.removals}

    # Kept: the quarterly row (it has the reference number), which takes the monthly
    # statement's more specific label.
    relabelled = by_pair[(107, 7)]
    assert relabelled.rule == dedupe.RELABELLED
    assert (relabelled.set_category, relabelled.set_type) == ("Transfer", "TRANSFER")
    assert relabelled.note == ""
    # Hand-entered income and expense of the same amount are two different things.
    assert [(p.rule) for p in plan.keeps if p.keep.id in (701, 702)] == [dedupe.DIFFERENT_TYPE]


def test_overlapping_statements_that_disagree_are_left_for_review():
    rows = overlap_rows() + [
        # Same narration, but one statement says the money came in and the other that it went out.
        tx(8, "UPI Amit Sharma dinner split", "650.00", "2026-07-06", source=JULY),
        tx(108, "UPI/Amit Sharma/123456789808/dinner split", "650.00", "2026-07-06", type="INCOME", source=QUARTER),
        # Same amount and type, nothing in common in the wording.
        tx(9, "POS CAFE COFFEE DAY", "84.00", "2026-07-07", category="Food", source=JULY),
        tx(109, "UPI/HUNGERBOX/123456789809/order", "84.00", "2026-07-07", source=QUARTER),
        # A transfer in one, an unrelated-looking expense in the other: suggest keeping the transfer.
        tx(10, "UPI Rohit Kushwaha self", "1500.00", "2026-07-08", type="TRANSFER", source=JULY),
        tx(110, "UPI/MUTUAL FUNDS/123456789810/SIP", "1500.00", "2026-07-08", source=QUARTER),
    ]
    plan = dedupe.plan_duplicates(rows)

    reviews = {(p.keep.id, p.remove.id): p.rule for p in plan.reviews}
    assert reviews == {
        (108, 8): dedupe.DIFFERENT_TYPE,
        (109, 9): dedupe.DIFFERENT_WORDING,
        (10, 110): dedupe.DIFFERENT_TYPE,
    }
    assert not {8, 108, 9, 109, 10, 110} & {r.remove.id for r in plan.removals}


def test_statements_that_barely_overlap_are_not_copies():
    rows = [
        tx(801, "UPI/RAMESH/123456789101/dinner", "500.00", "2026-07-05", source=QUARTER),
        tx(802, "NEFT ELECTRICITY BOARD", "500.00", "2026-07-05", source=SBI),
        tx(803, "UPI/AIRTEL/123456789102/recharge", "299.00", "2026-07-08", source=QUARTER),
        tx(804, "SBI CARD PAYMENT", "1500.00", "2026-07-09", source=SBI),
    ]
    plan = dedupe.plan_duplicates(rows)

    assert plan.overlapping_imports == []
    assert not plan.removals
    assert [(p.rule) for p in plan.keeps] == [dedupe.DIFFERENT_DESCRIPTION]


def test_plan_csv_round_trip_and_promoting_a_review_row():
    rows = overlap_rows() + [
        tx(601, "Nippon Multi Cap", "2000.00", "2026-04-10", category="SIP"),
        tx(602, "Nippon Large Cap ", "2000.00", "2026-04-10", category="SIP"),
    ]
    plan = dedupe.plan_duplicates(rows)
    buffer = io.StringIO()
    dedupe.write_plan_csv(plan, buffer)

    approved = dedupe.read_approved_removals(io.StringIO(buffer.getvalue()))
    assert {(a.keep_id, a.remove_id) for a in approved} == pairs(plan.removals)
    debt = next(a for a in approved if a.remove_id == 4)
    assert debt.set_category == "Debt" and debt.amount == Decimal("3000.00") and debt.date == date(2026, 7, 4)

    promoted = buffer.getvalue().replace("review,similar description", "remove,similar description")
    approved = dedupe.read_approved_removals(io.StringIO(promoted))
    nippon = next(a for a in approved if a.remove_id == 602)
    assert nippon.keep_id == 601 and nippon.remove_description == "Nippon Large Cap "


def test_relabel_survives_the_csv_round_trip():
    plan = dedupe.plan_duplicates(relabel_rows())
    buffer = io.StringIO()
    dedupe.write_plan_csv(plan, buffer)

    approved = {a.remove_id: a for a in dedupe.read_approved_removals(io.StringIO(buffer.getvalue()))}
    assert (approved[7].keep_id, approved[7].set_category, approved[7].set_type) == (107, "Transfer", "TRANSFER")
    assert approved[7].rule == dedupe.RELABELLED


def test_plan_validation_errors():
    header = ",".join(dedupe.CSV_FIELDS)

    def row(keep_id, remove_id, day="2026-07-04", **extra):
        values = {name: "" for name in dedupe.CSV_FIELDS}
        values.update(decision="remove", keep_id=keep_id, remove_id=remove_id, date=day, amount="10",
                      keep_description="a", remove_description="b")
        values.update(extra)
        return ",".join(str(values[name]) for name in dedupe.CSV_FIELDS)

    with pytest.raises(dedupe.PlanError, match="marked for removal 2 times"):
        dedupe.read_approved_removals(io.StringIO("\n".join([header, row(1, 2), row(3, 2)])))
    with pytest.raises(dedupe.PlanError, match="both kept and removed"):
        dedupe.read_approved_removals(io.StringIO("\n".join([header, row(1, 2), row(2, 3)])))
    with pytest.raises(dedupe.PlanError, match="date YYYY-MM-DD"):
        dedupe.read_approved_removals(io.StringIO("\n".join([header, row(1, 2, "04/07/2026")])))
    with pytest.raises(dedupe.PlanError, match="missing columns"):
        dedupe.read_approved_removals(io.StringIO("decision,keep_id\nremove,1\n"))
    with pytest.raises(dedupe.PlanError, match="set_type must be one of"):
        dedupe.read_approved_removals(io.StringIO("\n".join([header, row(1, 2, set_type="LOAN")])))

    # Lower case is fine; rows not marked "remove" are ignored.
    approved = dedupe.read_approved_removals(io.StringIO("\n".join([
        header, row(1, 2, set_type=" transfer "), row(5, 6, decision="review", set_type="LOAN"),
    ])))
    assert [(a.keep_id, a.remove_id, a.set_type) for a in approved] == [(1, 2, "TRANSFER")]


def test_same_text_tolerates_spreadsheet_spacing():
    assert dedupe.same_text("Auto to office ", "Auto  to office")
    assert not dedupe.same_text("Auto to office", "Auto to home")
