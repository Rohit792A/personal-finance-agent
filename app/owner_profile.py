# owner_profile.py
"""Facts about the owner that the personal deployment needs but the public repository must not hold (DEC-062).

- `own_account_numbers`: the owner's own bank and FD account numbers. A narration that mentions one is
  an internal sweep or FD movement (Tier 1 of the master resolver in `mcp/a2a_orchestrator.py`).
- `own_name_tokens`: how the owner's name appears in UPI narrations (Tier 2: self-transfers).
- `peer_inflow_tokens` and `peer_remark_tokens`: names of family and friends, and UPI remarks, whose
  credits are reimbursements or bill-splits rather than income (Tier 7).
- `reconciled_monthly_balances`: the statement-reconciled month start and end balances that
  `DatabaseManager` seeds into `monthly_bank_balances` on startup (DEC-054).

The file is `app/private/owner_profile.json`, or the path in `OWNER_PROFILE_PATH`. `.gitignore`
excludes `app/private/`; `.dockerignore` and `.gcloudignore` don't, so `scripts/deploy_backend.sh`
still ships the file in the backend image it builds from the owner's machine. Without the file (a
fresh clone, CI) every list is empty: the resolver keeps its generic keywords and nothing is seeded.
`docs/examples/owner_profile.example.json` shows the format.
"""
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent / "private" / "owner_profile.json"

# Column order of `monthly_bank_balances`, as unpacked by DatabaseManager._seed_reconciled_monthly_balances.
BALANCE_FIELDS = (
    "month_key",
    "kotak_sa_open", "kotak_sa_close",
    "kotak_am_open", "kotak_am_close",
    "hdfc_sa_open", "hdfc_sa_close",
    "hdfc_fd_open", "hdfc_fd_close",
    "opening_balance", "closing_balance",
    "source_note",
)
_TEXT_FIELDS = ("month_key", "source_note")


def profile_path() -> Path:
    return Path(os.getenv("OWNER_PROFILE_PATH") or DEFAULT_PATH)


@lru_cache(maxsize=1)
def load() -> dict:
    """The parsed profile, or {} when the file is missing or unreadable (read once per process)."""
    path = profile_path()
    if not path.is_file():
        logger.info("No owner profile at %s: owner-specific rules and balance seeding are off.", path)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Owner profile %s could not be read (%s); ignoring it.", path, e)
        return {}
    if not isinstance(data, dict):
        logger.warning("Owner profile %s is not a JSON object; ignoring it.", path)
        return {}
    return data


def _tokens(key: str) -> tuple[str, ...]:
    """Lower-cased, trimmed, non-empty strings from a list in the profile (narrations are matched lower-case)."""
    values = load().get(key) or []
    if not isinstance(values, list):
        logger.warning("Owner profile field %r should be a list; ignoring it.", key)
        return ()
    return tuple(s for s in (str(v).strip().lower() for v in values) if s)


def own_account_numbers() -> tuple[str, ...]:
    return _tokens("own_account_numbers")


def own_name_tokens() -> tuple[str, ...]:
    return _tokens("own_name_tokens")


def peer_inflow_tokens() -> tuple[str, ...]:
    return _tokens("peer_inflow_tokens")


def peer_remark_tokens() -> tuple[str, ...]:
    return _tokens("peer_remark_tokens")


def reconciled_monthly_balances() -> list[tuple]:
    """Rows in BALANCE_FIELDS order; a malformed row is skipped with a warning."""
    rows = []
    for item in load().get("reconciled_monthly_balances") or []:
        try:
            rows.append(tuple(
                str(item.get(f) or "") if f in _TEXT_FIELDS else float(item[f])
                for f in BALANCE_FIELDS
            ))
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            logger.warning("Skipping a malformed reconciled balance row (%s: %s).", type(e).__name__, e)
            continue
        if not rows[-1][0]:
            logger.warning("Skipping a reconciled balance row without month_key.")
            rows.pop()
    return rows
