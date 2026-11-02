# app/date_guard.py
"""Keeps the year of AI-extracted transaction dates right (DEC-035).

Two uploads landed years off (DEC-033, DEC-035): "Monthly budget March 2026.pdf" was saved as
March 2023 and "Monthly budget August 2025.pdf" as August 2023. The printed sheets show some dates
without a year, the extractors never told the model which year the document is about, and nothing
checked the answer, so Gemini filled in a year from its training data.

Two layers, used by StatementExtractor (PDF), ReceiptExtractor (images) and AISpreadsheetImporter:

- `prompt_context()` tells the model today's date (India time, like the rest of the app), the file
  name and the period that name states, and to take a missing year from the document or the file
  name, never from its own knowledge.
- `fix_years()` checks the answer. When the file name states a period ("March 2026", "2026-09",
  "1 Jun 26-31 Aug 26", "IMG20260830..."), a date more than six months outside it gets the year
  that puts it back inside (same day and month). Without a period in the name, a date after today
  moves back to the most recent past year. It returns short notes for the upload messages.
"""
from __future__ import annotations

import calendar
import logging
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

INDIA_TZ = ZoneInfo("Asia/Kolkata")  # the app's "today" (api.py uses the same zone)
WINDOW_DAYS = 183  # budget sheets carry a few rows from earlier months (last month's salary, balance)
FUTURE_GRACE_DAYS = 2  # time zones and posting delays
MAX_PERIOD_DAYS = 800  # a name that seems to span more than about two years isn't trusted

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
# Whole words only: "marketing" is not March, "decision" is not December.
_MON = r"(?<![a-z])(?P<mon>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")(?![a-z])\.?"
_SEP = r"[\s_.,\-]*"
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"

_MON_DAY_YEAR = re.compile(_MON + _SEP + r"(?:" + _DAY + r"[\s_.,\-]+)?(?P<year>20\d{2})(?!\d)")  # March 2026, Aug 25 2026
_DAY_MON_YEAR = re.compile(r"(?<!\d)" + _DAY + _SEP + _MON + _SEP + r"(?P<year>(?:20)?\d{2})(?!\d)")  # 1 Jun 26
_YEAR_MON = re.compile(r"(?<!\d)(?P<year>20\d{2})" + _SEP + _MON)  # 2026 March
_YEAR_MM = re.compile(r"(?<!\d)(?P<year>20\d{2})[-_.](?P<mm>0[1-9]|1[0-2])(?!\d)")  # 2026-09
_MM_YEAR = re.compile(r"(?<!\d)(?P<mm>0[1-9]|1[0-2])[-_.](?P<year>20\d{2})(?!\d)")  # 09-2026
_YYYYMMDD = re.compile(r"(?<!\d)(?P<year>20\d{2})(?P<mm>0[1-9]|1[0-2])(?P<dd>0[1-9]|[12]\d|3[01])")  # IMG20260830...
_BARE_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_MON_ANY = re.compile(_MON)


def today_in_india() -> date:
    return datetime.now(INDIA_TZ).date()


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def period_from_filename(source: str, today: date | None = None) -> tuple[date, date] | None:
    """The period a file name states, as (first day, last day), or None when it states none.

    Bare years ("Order 2024.pdf") and years after next year are ignored: on their own they are
    too often order or invoice numbers.
    """
    today = today or today_in_india()
    stem = os.path.splitext(os.path.basename(str(source or "").replace("\\", "/")))[0].lower()
    if not stem:
        return None

    def plausible(year: int) -> bool:
        return 2000 <= year <= today.year + 1

    ranges, used = [], []
    for pattern in (_MON_DAY_YEAR, _DAY_MON_YEAR, _YEAR_MON):  # in order of trust
        for m in pattern.finditer(stem):
            if m.span("mon") in used:  # "Dec 2025 - Jan 2026": Jan already has its own year
                continue
            year = int(m.group("year"))
            if year < 100:  # "1 Jun 26": a two-digit year is trusted only within the last ten years
                year += 2000
                if year < today.year - 10:
                    continue
            if plausible(year):
                ranges.append(_month_range(year, _MONTHS[m.group("mon")]))
                used.append(m.span("mon"))
    for pattern in (_YEAR_MM, _MM_YEAR):
        for m in pattern.finditer(stem):
            if plausible(int(m.group("year"))):
                ranges.append(_month_range(int(m.group("year")), int(m.group("mm"))))
    for m in _YYYYMMDD.finditer(stem):
        try:
            day = date(int(m.group("year")), int(m.group("mm")), int(m.group("dd")))
        except ValueError:
            continue
        if plausible(day.year):
            ranges.append((day, day))

    # A month name without its own year ("July 25 to Aug 25 2026") takes the name's only year.
    # "may" on its own is too common a word to count.
    years = {int(y) for y in _BARE_YEAR.findall(stem) if plausible(int(y))}
    if len(years) == 1:
        (year,) = years
        for m in _MON_ANY.finditer(stem):
            if m.span("mon") not in used and m.group("mon") != "may":
                ranges.append(_month_range(year, _MONTHS[m.group("mon")]))

    if not ranges:
        return None
    start, end = min(r[0] for r in ranges), max(r[1] for r in ranges)
    if (end - start).days > MAX_PERIOD_DAYS:
        return None
    return start, end


def _label(period: tuple[date, date]) -> str:
    start, end = period
    if start == end:
        return f"{start.day} {start:%B %Y}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start:%B %Y}"
    return f"{start:%B %Y} to {end:%B %Y}"


def _clean_name(source: str) -> str:
    """The base name, reduced like api._safe_upload_name, so it can't add lines to the prompt."""
    base = os.path.basename(str(source or "").replace("\\", "/")).strip()
    return re.sub(r"[^A-Za-z0-9._ ()-]", "_", base)[:120] or "unknown"


def prompt_context(source: str, today: date | None = None) -> str:
    """Date rules appended to an extraction prompt: today's date, the file name and its period."""
    today = today or today_in_india()
    period = period_from_filename(source, today)
    stated = f" The name indicates the period {_label(period)}." if period else ""
    return (
        "\n\nDATE RULES:\n"
        f"- Today's date is {today.isoformat()}. The file is named \"{_clean_name(source)}\".{stated}\n"
        "- Copy each date from the document. If a date is printed without a year (for example "
        "'14-Mar', '3/14' or '1-May'), take the year from the statement period printed in the "
        "document; if the document shows none, from the file name; if neither gives a year, use "
        "the most recent date with that day and month on or before today.\n"
        "- Never fill in a year from your own knowledge or assumptions about the current date, and "
        "never return a date after today unless the document prints that full date.\n"
    )


def _with_year(day: date, year: int) -> date | None:
    try:
        return day.replace(year=year)
    except ValueError:  # 29 February in a non-leap year
        return None


def _gap(day: date, period: tuple[date, date]) -> int:
    if day < period[0]:
        return (period[0] - day).days
    if day > period[1]:
        return (day - period[1]).days
    return 0


def _checked(day: date, period, today: date) -> tuple[date, str]:
    """Returns (date, status): status is "ok", "period", "future" or "unfixable"."""
    if period:
        low, high = period[0] - timedelta(days=WINDOW_DAYS), period[1] + timedelta(days=WINDOW_DAYS)
        if low <= day <= high:
            return day, "ok"
        candidates = [c for c in (_with_year(day, y) for y in range(low.year, high.year + 1))
                      if c is not None and low <= c <= high]
        if not candidates:
            return day, "unfixable"
        return min(candidates, key=lambda c: (_gap(c, period), c)), "period"
    latest = today + timedelta(days=FUTURE_GRACE_DAYS)
    if day <= latest:
        return day, "ok"
    for year in range(min(day.year - 1, latest.year), latest.year - 8, -1):
        candidate = _with_year(day, year)
        if candidate is not None and candidate <= latest:
            return candidate, "future"
    return day, "unfixable"


def fix_years(rows, source: str, today: date | None = None) -> list[str]:
    """Checks each row's `transaction_date` against the file's period (or today) and fixes the year.

    Changes the rows in place and returns notes for the upload messages (empty when all fit).
    """
    today = today or today_in_india()
    period = period_from_filename(source, today)
    moved, future, unfixable = [], [], []
    for row in rows:
        old = getattr(row, "transaction_date", None)
        if not isinstance(old, date):
            continue
        new, status = _checked(old, period, today)
        if status == "unfixable":
            unfixable.append(old)
        elif status != "ok":
            row.transaction_date = new
            (moved if status == "period" else future).append((old, new))

    notes = []
    if moved:
        old, new = moved[0]
        notes.append(f"Corrected the year on {len(moved)} date{'s' if len(moved) != 1 else ''} to match the "
                     f"file name ({_label(period)}), e.g. {old:%d %b %Y} -> {new:%d %b %Y}.")
    if future:
        notes.append(f"Moved {len(future)} date{'s' if len(future) != 1 else ''} that fell after today "
                     f"back to the most recent past year.")
    if unfixable:
        where = f"the period in the file name ({_label(period)})" if period else "a past date"
        notes.append(f"{len(unfixable)} date{'s' if len(unfixable) != 1 else ''} could not be matched to "
                     f"{where}; please check {'them' if len(unfixable) != 1 else 'it'}.")
    if notes:
        logger.warning("Date check for %s: %s", _clean_name(source), " ".join(notes))
    return notes
