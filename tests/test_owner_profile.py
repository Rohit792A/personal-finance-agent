# tests/test_owner_profile.py
"""Owner-only data stays out of git (DEC-062).

owner_profile.py reads the owner's account numbers, family and friend payee names and reconciled
balances from a git-ignored file. Without that file (a fresh clone, CI) the master resolver keeps
only its generic rules and nothing is seeded.
"""
import json
import types
from pathlib import Path

import pytest

import owner_profile
from mcp.a2a_orchestrator import AgenticCFOOrchestrator

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "examples" / "owner_profile.example.json"

BALANCE_ROW = {
    "month_key": "2026-09",
    "kotak_sa_open": 1000, "kotak_sa_close": 1100.5,
    "kotak_am_open": 0, "kotak_am_close": 0,
    "hdfc_sa_open": 200, "hdfc_sa_close": 250,
    "hdfc_fd_open": 0, "hdfc_fd_close": 0,
    "opening_balance": 1200, "closing_balance": 1350.5,
    "source_note": "Sample statement",
}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """Points owner_profile at a temporary file (absent until written); yields a writer for it."""
    path = tmp_path / "owner_profile.json"
    monkeypatch.setenv("OWNER_PROFILE_PATH", str(path))
    owner_profile.load.cache_clear()

    def write(data):
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        owner_profile.load.cache_clear()

    yield write
    owner_profile.load.cache_clear()


def test_without_a_profile_every_owner_list_is_empty(profile):
    assert owner_profile.load() == {}
    assert owner_profile.own_account_numbers() == ()
    assert owner_profile.own_name_tokens() == ()
    assert owner_profile.peer_inflow_tokens() == ()
    assert owner_profile.peer_remark_tokens() == ()
    assert owner_profile.reconciled_monthly_balances() == []


def test_tokens_are_trimmed_and_lower_cased(profile):
    profile({"own_name_tokens": ["  Asha Verma ", "", "ASHA V"], "own_account_numbers": [1234567890, "6000000001"]})
    assert owner_profile.own_name_tokens() == ("asha verma", "asha v")
    assert owner_profile.own_account_numbers() == ("1234567890", "6000000001")


def test_the_documented_example_loads(profile):
    profile(EXAMPLE.read_text(encoding="utf-8"))
    assert owner_profile.own_account_numbers() == ("1234567890", "6000000001")
    assert owner_profile.peer_inflow_tokens() == ("ravi mehta", "neha verma")
    assert [row[0] for row in owner_profile.reconciled_monthly_balances()] == ["2026-09"]


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '"text"'])
def test_an_unreadable_profile_is_ignored(profile, content):
    profile(content)
    assert owner_profile.load() == {}
    assert owner_profile.peer_inflow_tokens() == ()


def test_a_field_that_is_not_a_list_is_ignored(profile):
    profile({"peer_inflow_tokens": "asha verma", "peer_remark_tokens": ["Lunch Split"]})
    assert owner_profile.peer_inflow_tokens() == ()
    assert owner_profile.peer_remark_tokens() == ("lunch split",)


def test_balances_follow_the_table_columns_and_skip_bad_rows(profile):
    missing_field = {k: v for k, v in BALANCE_ROW.items() if k != "hdfc_fd_close"}
    not_a_number = {**BALANCE_ROW, "month_key": "2026-10", "closing_balance": "lots"}
    no_month = {**BALANCE_ROW, "month_key": ""}
    no_note = {k: v for k, v in BALANCE_ROW.items() if k != "source_note"} | {"month_key": "2026-11"}
    profile({"reconciled_monthly_balances": [BALANCE_ROW, missing_field, not_a_number, no_month, "junk", no_note]})

    rows = owner_profile.reconciled_monthly_balances()
    assert rows == [
        ("2026-09", 1000.0, 1100.5, 0.0, 0.0, 200.0, 250.0, 0.0, 0.0, 1200.0, 1350.5, "Sample statement"),
        ("2026-11", 1000.0, 1100.5, 0.0, 0.0, 200.0, 250.0, 0.0, 0.0, 1200.0, 1350.5, ""),
    ]
    assert len(owner_profile.BALANCE_FIELDS) == len(rows[0])


def _resolver(balances=None):
    """The master resolver without a database or Google Cloud clients."""
    orch = object.__new__(AgenticCFOOrchestrator)
    orch.db_manager = types.SimpleNamespace(
        match_learned_rule=lambda *args, **kwargs: None,
        get_reconciled_monthly_balances=lambda: balances or {},
    )
    orch.a2a = types.SimpleNamespace(
        voice_agent=types.SimpleNamespace(_classify_category_fallback=lambda description: "Other")
    )
    return orch


def _tier(description, transaction_type="EXPENSE"):
    tx = {
        "id": 1, "description": description, "amount": 500.0, "date": "2026-09-08",
        "category": "Other", "transaction_type": transaction_type,
    }
    return _resolver()._decide_category_with_evidence(tx, [tx], {}, {}, cached_rules=[])["rule_tier"]


def test_own_account_numbers_mark_internal_sweeps(profile):
    narration = "TRF TO 6000000001 INTERNAL"
    assert _tier(narration) != "INTERNAL_BANK_SWEEP"
    profile({"own_account_numbers": ["6000000001"]})
    assert _tier(narration) == "INTERNAL_BANK_SWEEP"
    # The generic keywords need no profile.
    assert _tier("Sweep Transfer From: 1234567890") == "INTERNAL_BANK_SWEEP"


def test_own_name_marks_self_transfers(profile):
    narration = "UPI/ASHA VERMA/123456789012/Lunch"
    assert _tier(narration) != "SELF_TRANSFER_MATCH"
    profile({"own_name_tokens": ["asha verma"]})
    assert _tier(narration) == "SELF_TRANSFER_MATCH"


def test_peer_names_mark_reimbursements(profile):
    narration = "UPI/RAVI MEHTA/123456789012/dinner"
    assert _tier(narration, "INCOME") != "PEER_TRANSFER_INFLOW"
    profile({"peer_inflow_tokens": ["ravi mehta"]})
    assert _tier(narration, "INCOME") == "PEER_TRANSFER_INFLOW"


def test_advisor_balance_is_the_latest_reconciled_closing_balance():
    assert _resolver()._latest_reconciled_balance() == 25000.0
    months = {"2026-10": {"closing_balance": 20.5}, "2026-09": {"closing_balance": 10.0}}
    assert _resolver(months)._latest_reconciled_balance() == 20.5
