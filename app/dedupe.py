# dedupe.py
"""Duplicate transactions left behind by overlapping imports: the planning logic (DEC-032).

Pure functions, no database access. scripts/dedupe_transactions.py loads the rows, builds a plan
here, writes it as a reviewable CSV, and applies an approved plan (removed rows move to
transactions_removed, so a cleanup can be undone).

An *import* is one statement file processed once: its rows share the file (assets.gcs_uri) and
the exact created_at of that batch. Only rows with the same date and amount are compared. For
each pair the first rule that matches decides:

  keep    same statement file     two lines of one import are two payments
  keep    different reference     their 12-digit UPI/IMPS reference numbers differ
  remove  overlapping statements  two imports covering the same dates (statements of one
                                  account, or one file imported twice); rows pair one-to-one
  remove  ... labelled differently  as above, but one import called it a transfer and the other
                                  an expense/income; the kept row takes the specific label
  review  different type          e.g. an HDFC alert (expense) and a Kotak credit (income)
  review  both sides of a transfer  same reference in two accounts' statements (out and in)
  remove  same reference number   the same payment imported twice
  remove  alert + statement       a Gmail alert and the statement line for the same payment
  review  similar description     looks alike, but no file or reference proves it
  keep    different description   e.g. "Chai Poha" and "Hunger Box", both ₹84

Rows that are the same payment form a cluster with at most one row per import. The most useful
row is kept (one you edited, then one with a reference number, then statement over alert, then
the oldest). If the kept row's category is only "Other", it takes the category (and the
transfer/expense/income label) of a removed copy that has a specific one.
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import IO, Iterable, Optional

from database import (
    description_similarity,
    extract_reference_numbers,
    is_alert_description,
    is_generic_category,
)

# --- rule names (also written to the plan CSV) ------------------------------------------------
SAME_FILE = "same statement file"
DIFFERENT_REFERENCES = "different reference numbers"
OVERLAPPING_STATEMENTS = "overlapping statements"
RELABELLED = "overlapping statements, labelled differently"
DIFFERENT_TYPE = "different type"
TRANSFER_SIDES = "both sides of a transfer"
SAME_REFERENCE = "same reference number"
ALERT_AND_STATEMENT = "alert + statement"
SIMILAR_DESCRIPTION = "similar description"
DIFFERENT_DESCRIPTION = "different description"
DIFFERENT_WORDING = "overlapping statements, different wording"
EDITED_COPIES = "you edited more than one copy"

REMOVE, REVIEW, KEEP = "remove", "review", "keep"
TYPES = ("EXPENSE", "INCOME", "TRANSFER")

_RULE_STRENGTH = {SAME_REFERENCE: 4, OVERLAPPING_STATEMENTS: 3, RELABELLED: 2, ALERT_AND_STATEMENT: 1}

# Two imports count as copies of the same period when, inside their overlapping dates, at least
# this many rows (and this share of the smaller side's rows) match on date + amount. The type is
# left out on purpose: a re-import can label the same line differently.
OVERLAP_MIN_MATCHES = 3
OVERLAP_MIN_SHARE = 0.5

_STATEMENT_FILE = re.compile(r"\.(pdf|xlsx?|csv)$", re.IGNORECASE)
_RECEIPT_FILE = re.compile(r"\.(jpe?g|png|webp|heic|gif)$", re.IGNORECASE)


@dataclass(frozen=True)
class Tx:
    """One transactions row, with its first attached file (assets.gcs_uri) as `source`."""

    id: int
    date: date
    amount: Decimal
    description: str
    category: str
    type: str
    user_locked: bool = False
    source: str = ""
    added: Optional[datetime] = None

    @property
    def amount_key(self) -> str:
        return f"{Decimal(self.amount):.2f}"

    @property
    def import_key(self) -> tuple:
        """(file, batch time): rows saved by one import share both."""
        return (self.source, self.added)


@dataclass(frozen=True)
class Removal:
    keep: Tx
    remove: Tx
    rule: str
    set_category: Optional[str] = None
    set_type: Optional[str] = None
    note: str = ""


@dataclass(frozen=True)
class PairNote:
    """A pair that is not removed automatically: decision is REVIEW or KEEP."""

    decision: str
    rule: str
    keep: Tx
    remove: Tx  # the row that would be removed if you accept a review pair


@dataclass
class Plan:
    removals: list[Removal] = field(default_factory=list)
    reviews: list[PairNote] = field(default_factory=list)
    keeps: list[PairNote] = field(default_factory=list)
    overlapping_imports: list[tuple[tuple, tuple, int, int, date, date]] = field(default_factory=list)
    transactions: int = 0


# --- per-row facts ----------------------------------------------------------------------------
def references(description: str) -> set[str]:
    """12-digit UPI/IMPS reference numbers (RRN). Phone numbers (10) and most account numbers
    aren't 12 digits, so they don't make two different payments look like one. The importer
    uses the same rule (database.extract_reference_numbers, DEC-034)."""
    return extract_reference_numbers(description)


def source_kind(tx: Tx) -> str:
    if is_alert_description(tx.description):
        return "alert"
    if not tx.source:
        return "none"
    if _STATEMENT_FILE.search(tx.source):
        return "statement"
    if _RECEIPT_FILE.search(tx.source):
        return "receipt"
    return "other"  # e.g. Gmail alert rows whose text was replaced by the statement's narration


def similarity(first: str, second: str) -> float:
    """1.0 for the same letters (ignoring digits and punctuation); otherwise the larger of word
    overlap (Jaccard) and containment, so "HUNGERBOX ORDER" matches the long UPI narration.
    Shared with the importer (database.description_similarity)."""
    return description_similarity(first, second)


def _is_generic(category: str) -> bool:
    return is_generic_category(category)


def keeper_rank(tx: Tx) -> tuple:
    """Lower is better: edited by you, has a reference number, statement over alert, oldest."""
    kind_rank = {"statement": 0, "other": 1, "alert": 2, "receipt": 3, "none": 4}[source_kind(tx)]
    return (0 if tx.user_locked else 1, 0 if references(tx.description) else 1, kind_rank, tx.id)


# --- import-level overlap ---------------------------------------------------------------------
def overlapping_statement_imports(rows: Iterable[Tx]) -> dict[frozenset, tuple[int, int, date, date]]:
    """Pairs of statement imports that are copies of the same period: {imports: (matches, rows, from, to)}."""
    by_import: dict[tuple, list[Tx]] = defaultdict(list)
    for tx in rows:
        if source_kind(tx) == "statement":
            by_import[tx.import_key].append(tx)

    found = {}
    for first, second in combinations(sorted(by_import, key=lambda k: (k[0], k[1] or datetime.min)), 2):
        rows_a, rows_b = by_import[first], by_import[second]
        start = max(min(t.date for t in rows_a), min(t.date for t in rows_b))
        end = min(max(t.date for t in rows_a), max(t.date for t in rows_b))
        if start > end:
            continue
        keys_a = Counter((t.date, t.amount_key) for t in rows_a if start <= t.date <= end)
        keys_b = Counter((t.date, t.amount_key) for t in rows_b if start <= t.date <= end)
        matches = sum((keys_a & keys_b).values())
        smaller = min(sum(keys_a.values()), sum(keys_b.values()))
        if smaller and matches >= OVERLAP_MIN_MATCHES and matches / smaller >= OVERLAP_MIN_SHARE:
            found[frozenset((first, second))] = (matches, smaller, start, end)
    return found


# --- pair rules -------------------------------------------------------------------------------
def classify(a: Tx, b: Tx, overlapping: dict) -> tuple[str, str, float]:
    """(decision, rule, similarity) for two rows with the same date and amount."""
    kind_a, kind_b = source_kind(a), source_kind(b)
    refs_a, refs_b = references(a.description), references(b.description)
    shared_reference = bool(refs_a & refs_b)
    score = similarity(a.description, b.description)
    similar = score >= 0.5
    statements = kind_a == kind_b == "statement"

    if statements and a.import_key == b.import_key:
        return KEEP, SAME_FILE, score
    if refs_a and refs_b and not shared_reference:
        return KEEP, DIFFERENT_REFERENCES, score

    if statements and frozenset((a.import_key, b.import_key)) in overlapping:
        if a.type == b.type and (score > 0 or shared_reference):
            rule = OVERLAPPING_STATEMENTS
        elif a.type == b.type:
            return REVIEW, DIFFERENT_WORDING, score
        elif "TRANSFER" in (a.type, b.type) and (similar or shared_reference):
            rule = RELABELLED
        else:
            return REVIEW, DIFFERENT_TYPE, score  # expense vs income, or unrelated wording
    elif a.type != b.type:
        return (REVIEW if shared_reference or similar else KEEP), DIFFERENT_TYPE, score
    elif shared_reference:
        if statements and a.type == "TRANSFER":
            return REVIEW, TRANSFER_SIDES, score  # e.g. HDFC "out" and Kotak "in", same UPI ref
        rule = SAME_REFERENCE
    elif {kind_a, kind_b} == {"alert", "statement"}:
        rule = ALERT_AND_STATEMENT
    elif similar:
        return REVIEW, SIMILAR_DESCRIPTION, score
    else:
        return KEEP, DIFFERENT_DESCRIPTION, score

    if a.user_locked and b.user_locked:
        return REVIEW, EDITED_COPIES, score
    return REMOVE, rule, score


def _ordered(a: Tx, b: Tx) -> tuple[Tx, Tx]:
    """(row to keep, row to remove) by keeper_rank."""
    return (a, b) if keeper_rank(a) <= keeper_rank(b) else (b, a)


def _ordered_for_review(a: Tx, b: Tx) -> tuple[Tx, Tx]:
    """Like _ordered, but when one row is a transfer and the other isn't, suggest keeping the
    transfer, so accepting the pair doesn't leave a transfer counted as income or spending."""
    if a.type != b.type and "TRANSFER" in (a.type, b.type) and not (a.user_locked or b.user_locked):
        return (a, b) if a.type == "TRANSFER" else (b, a)
    return _ordered(a, b)


def _never_same(decision: Optional[tuple[str, str, float]]) -> bool:
    """True for pairs that can't be one payment: two lines of one import, different references,
    or different types with nothing in common."""
    if decision is None:
        return True
    verdict, rule, _score = decision
    return rule in (SAME_FILE, DIFFERENT_REFERENCES) or (verdict == KEEP and rule == DIFFERENT_TYPE)


# --- the plan ---------------------------------------------------------------------------------
def plan_duplicates(rows: Iterable[Tx]) -> Plan:
    rows = list(rows)
    overlapping = overlapping_statement_imports(rows)
    plan = Plan(transactions=len(rows))
    for imports, (matches, smaller, start, end) in sorted(overlapping.items(), key=lambda kv: -kv[1][0]):
        first, second = sorted(imports, key=lambda k: (k[1] or datetime.min, k[0]))
        plan.overlapping_imports.append((first, second, matches, smaller, start, end))

    groups: dict[tuple, list[Tx]] = defaultdict(list)
    for tx in rows:
        groups[(tx.date, tx.amount_key)].append(tx)

    decisions: dict[frozenset, tuple[str, str, float]] = {}
    pairs: list[tuple[Tx, Tx]] = []
    for members in groups.values():
        for a, b in combinations(sorted(members, key=lambda t: t.id), 2):
            decisions[frozenset((a.id, b.id))] = classify(a, b, overlapping)
            pairs.append((a, b))

    def decision_of(a: Tx, b: Tx) -> tuple[str, str, float]:
        return decisions[frozenset((a.id, b.id))]

    # Clusters: reference matches first, then the closest wording, so rows of two imports pair
    # one-to-one. A cluster never holds two rows that can't be the same payment.
    by_id = {tx.id: tx for tx in rows}
    cluster: dict[int, frozenset[int]] = {tx.id: frozenset((tx.id,)) for tx in rows}
    removable = [(a, b) for a, b in pairs if decision_of(a, b)[0] == REMOVE]
    removable.sort(key=lambda ab: (
        decision_of(*ab)[1] != SAME_REFERENCE, -decision_of(*ab)[2],
        -_RULE_STRENGTH[decision_of(*ab)[1]], ab[0].id, ab[1].id,
    ))
    for a, b in removable:
        left, right = cluster[a.id], cluster[b.id]
        if left == right:
            continue
        if any(_never_same(decisions.get(frozenset((x, y)))) for x in left for y in right):
            continue
        merged = left | right
        for tx_id in merged:
            cluster[tx_id] = merged

    removed_ids: set[int] = set()
    listed: set[frozenset] = set()
    for members in sorted(set(cluster.values()), key=min):
        if len(members) < 2:
            continue
        txs = sorted((by_id[i] for i in members), key=keeper_rank)
        if sum(1 for t in txs if t.user_locked) >= 2:
            for a, b in combinations(txs, 2):
                keep, remove = _ordered(a, b)
                plan.reviews.append(PairNote(REVIEW, EDITED_COPIES, keep, remove))
                listed.add(frozenset((a.id, b.id)))
            continue
        keeper, others = txs[0], txs[1:]
        source_of_label = None
        if not keeper.user_locked and _is_generic(keeper.category):
            source_of_label = next((t for t in others if not _is_generic(t.category)), None)
        final_category = source_of_label.category if source_of_label else keeper.category
        final_type = source_of_label.type if source_of_label else keeper.type
        for index, other in enumerate(others):
            notes = []
            if not _is_generic(other.category) and other.category != final_category:
                notes.append(f"categories differ: keeping {final_category!r}, removed copy has {other.category!r}")
            if other.type != final_type:
                notes.append(f"types differ: keeping {final_type}, removed copy is {other.type}")
            plan.removals.append(Removal(
                keeper, other, _rule_between(keeper, other, members, decisions),
                set_category=source_of_label.category if (source_of_label and index == 0) else None,
                set_type=(source_of_label.type if (source_of_label and index == 0
                                                   and source_of_label.type != keeper.type) else None),
                note="; ".join(notes),
            ))
            removed_ids.add(other.id)

    for a, b in pairs:
        key = frozenset((a.id, b.id))
        if key in listed or a.id in removed_ids or b.id in removed_ids:
            continue
        decision, rule, _score = decisions[key]
        if decision == REMOVE:
            if cluster[a.id] == cluster[b.id]:
                continue
            decision = REVIEW  # evidence says duplicate, but it couldn't be paired one-to-one
        if decision == REVIEW:
            keep, remove = _ordered_for_review(a, b)
            plan.reviews.append(PairNote(decision, rule, keep, remove))
        else:
            keep, remove = _ordered(a, b)
            plan.keeps.append(PairNote(decision, rule, keep, remove))

    plan.removals.sort(key=lambda r: (r.keep.date, r.keep.amount_key, r.keep.id, r.remove.id))
    plan.reviews.sort(key=lambda p: (p.keep.date, p.keep.amount_key, p.keep.id, p.remove.id))
    plan.keeps.sort(key=lambda p: (p.keep.date, p.keep.amount_key, p.keep.id, p.remove.id))
    return plan


def _rule_between(keeper: Tx, other: Tx, members: frozenset, decisions: dict) -> str:
    """The rule linking `other` to its cluster (prefer its pair with the keeper)."""
    direct = decisions.get(frozenset((keeper.id, other.id)))
    if direct and direct[0] == REMOVE:
        return direct[1]
    rules = [
        decisions[frozenset((other.id, m))][1]
        for m in members
        if m != other.id and decisions.get(frozenset((other.id, m)), (KEEP,))[0] == REMOVE
    ]
    return max(rules, key=lambda r: _RULE_STRENGTH.get(r, 0)) if rules else OVERLAPPING_STATEMENTS


# --- plan CSV ---------------------------------------------------------------------------------
CSV_FIELDS = [
    "decision", "rule", "note", "date", "amount",
    "keep_id", "remove_id", "set_category", "set_type",
    "keep_type", "remove_type", "keep_category", "remove_category", "keep_edited", "remove_edited",
    "keep_source", "remove_source", "keep_added", "remove_added",
    "keep_description", "remove_description",
]


def source_label(tx: Tx) -> str:
    if source_kind(tx) == "alert":
        return "Gmail alert"
    return tx.source.rsplit("/", 1)[-1] if tx.source else "(no file)"


def import_label(import_key: tuple) -> str:
    source, added = import_key
    name = source.rsplit("/", 1)[-1] if source else "(no file)"
    return f"{name} (imported {added:%d %b %H:%M})" if added else name


def _csv_row(decision: str, rule: str, keep: Tx, remove: Tx, set_category=None, set_type=None, note="") -> dict:
    return {
        "decision": decision, "rule": rule, "note": note,
        "date": keep.date.isoformat(), "amount": keep.amount_key,
        "keep_id": keep.id, "remove_id": remove.id,
        "set_category": set_category or "", "set_type": set_type or "",
        "keep_type": keep.type, "remove_type": remove.type,
        "keep_category": keep.category, "remove_category": remove.category,
        "keep_edited": "yes" if keep.user_locked else "", "remove_edited": "yes" if remove.user_locked else "",
        "keep_source": source_label(keep), "remove_source": source_label(remove),
        "keep_added": f"{keep.added:%Y-%m-%d %H:%M}" if keep.added else "",
        "remove_added": f"{remove.added:%Y-%m-%d %H:%M}" if remove.added else "",
        "keep_description": keep.description, "remove_description": remove.description,
    }


def plan_csv_rows(plan: Plan) -> list[dict]:
    rows = [_csv_row(REMOVE, r.rule, r.keep, r.remove, r.set_category, r.set_type, r.note) for r in plan.removals]
    rows += [_csv_row(p.decision, p.rule, p.keep, p.remove) for p in plan.reviews]
    rows += [_csv_row(p.decision, p.rule, p.keep, p.remove) for p in plan.keeps]
    return rows


def write_plan_csv(plan: Plan, handle: IO[str]) -> None:
    writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(plan_csv_rows(plan))


@dataclass(frozen=True)
class ApprovedRemoval:
    keep_id: int
    remove_id: int
    rule: str
    set_category: Optional[str]
    set_type: Optional[str]
    date: date
    amount: Decimal
    keep_description: str
    remove_description: str


class PlanError(ValueError):
    """The plan file can't be applied as it is."""


def read_approved_removals(handle: IO[str]) -> list[ApprovedRemoval]:
    """Rows whose decision is "remove", validated. Raises PlanError listing every problem."""
    reader = csv.DictReader(handle)
    missing = [name for name in ("decision", "keep_id", "remove_id", "date", "amount",
                                 "keep_description", "remove_description") if name not in (reader.fieldnames or [])]
    if missing:
        raise PlanError(f"missing columns: {', '.join(missing)}")

    approved, problems = [], []
    for line, row in enumerate(reader, start=2):
        if (row.get("decision") or "").strip().lower() != REMOVE:
            continue
        try:
            keep_id, remove_id = int(row["keep_id"]), int(row["remove_id"])
            when = date.fromisoformat(row["date"].strip())
            amount = Decimal(row["amount"].strip()).quantize(Decimal("0.01"))
        except (ValueError, InvalidOperation):
            problems.append(f"line {line}: ids must be numbers, date YYYY-MM-DD, amount a number")
            continue
        if keep_id == remove_id:
            problems.append(f"line {line}: keep_id and remove_id are the same (#{keep_id})")
            continue
        set_type = (row.get("set_type") or "").strip().upper() or None
        if set_type and set_type not in TYPES:
            problems.append(f"line {line}: set_type must be one of {', '.join(TYPES)} or empty")
            continue
        approved.append(ApprovedRemoval(
            keep_id=keep_id, remove_id=remove_id, rule=(row.get("rule") or "").strip() or "manual",
            set_category=(row.get("set_category") or "").strip() or None, set_type=set_type,
            date=when, amount=amount,
            keep_description=row["keep_description"], remove_description=row["remove_description"],
        ))

    removed = Counter(item.remove_id for item in approved)
    for tx_id, count in removed.items():
        if count > 1:
            problems.append(f"#{tx_id} is marked for removal {count} times")
    kept = {item.keep_id for item in approved}
    for tx_id in sorted(kept & set(removed)):
        problems.append(f"#{tx_id} is both kept and removed; keep each duplicate against the row that stays")
    if problems:
        raise PlanError("; ".join(problems))
    return approved


def same_text(first: str, second: str) -> bool:
    """Descriptions compared the way a spreadsheet round trip may change them (outer/inner spaces)."""
    return " ".join((first or "").split()) == " ".join((second or "").split())
