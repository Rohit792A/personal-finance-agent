# demo_seed.py
"""Demo Environment Sample Data & Guarded Nightly Reset (Phase 1, DEC-025).

The demo backend (`APP_ENV=demo`) runs the same code as the personal backend, against its own
Supabase project. Every night Cloud Scheduler calls `/internal/demo-reset`, which runs
`reset_demo_data()` to restore the sample data and clear anything a visitor added, edited or
uploaded.

FDE Pillar 2 (GOVERN): this is the only code that bulk-deletes data, so it refuses
(`DemoResetRefused`) unless three independent checks agree that the target is the demo:
1. `config.APP_ENV == "demo"`;
2. the configured Supabase project is not the personal one, unless `config.DATABASE_URL` points
   at a local test database (and neither that URL nor the engine URL may name the personal project);
3. the connected database itself holds the marker row `app_meta(environment = demo)`.
`mark_database_as_demo()` writes that marker once, and only into a database whose user-data tables
are empty, so the real database can never be marked by mistake.

FDE Pillar 3 (SCALE): `reset_demo_data()` runs in ONE transaction (TRUNCATE, then reload the
categories, the demo budgets and ~1,500 transactions). Rows go in as multi-row INSERTs of 200, i.e. a
handful of round trips to the remote database instead of one per row. If anything fails, the old
data stays exactly as it was.

The sample data (`generate_demo_transactions`) covers about 18 months of a fictional software
engineer in Bengaluru: salary with an April hike and an annual bonus, rent, UPI food and grocery
orders, street-vendor payments, bills, subscriptions, cabs, SIPs, a recurring deposit, a loan EMI,
insurance, card bill payments and transfers between own accounts, refunds, an annual trip and a
festive spike around Diwali. A few rows need review, exactly one pair is a possible duplicate (the
same purchase as a Gmail alert and as a statement line) and a few rows carry user corrections.
People, accounts and the employer are fictional; merchants are real brands so the narrations read
like real Indian bank statements.

Randomness is keyed by calendar date, not by `today`, so consecutive nightly resets show the same
history (only the newest day is new). The review items, the possible duplicate, the user
corrections and two current-month purchases are placed relative to `today`, so the newest data
always has something to show on every screen.
"""
from __future__ import annotations

import hashlib
import logging
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import sqlalchemy

import config
from database import compute_row_signature

logger = logging.getLogger(__name__)

DEFAULT_SEED = 20261018
MONTHS_OF_HISTORY = 18
DEMO_ENVIRONMENT = "demo"
ENVIRONMENT_KEY = "environment"
INSERT_CHUNK_ROWS = 200
AI_USAGE_RETENTION_DAYS = 30
# India has no daylight saving time, so a fixed offset avoids depending on the tz database.
INDIA_TZ = timezone(timedelta(hours=5, minutes=30), "IST")

# Wiped by every reset. CASCADE also clears any table that references these (e.g. assets).
RESET_TABLES = (
    "transactions", "assets", "ingested_files", "gmail_processed_emails",
    "exported_statement_pdfs", "gmail_sync_settings", "budgets", "categories",
)
# Tables that hold a user's own data. The demo marker is only written into a database where all
# of them are empty, which is what makes it impossible to mark the personal database.
USER_DATA_TABLES = (
    "transactions", "assets", "ingested_files", "gmail_processed_emails",
    "exported_statement_pdfs", "gmail_sync_settings",
)
APP_META_DDL = """
    CREATE TABLE IF NOT EXISTS app_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )
"""

# Same ids as the personal database: the API falls back to id 5 ("Other") in several places.
CATEGORIES: Dict[int, str] = {
    1: "Food", 2: "Health/Medical", 3: "Home", 4: "Transportation", 5: "Other", 6: "Personal",
    7: "SIP", 8: "Utilities", 9: "Travel", 10: "Debt", 11: "Investment", 12: "Stocks",
    13: "Rent", 14: "Savings", 15: "Paycheck", 16: "Bonus", 17: "Interest", 18: "Transfer",
}
CATEGORY_IDS: Dict[str, int] = {name: cid for cid, name in CATEGORIES.items()}
INCOME_CATEGORIES = frozenset({"Paycheck", "Bonus", "Interest"})

# Monthly budgets for the discretionary categories. Fixed commitments and investments (rent, EMI,
# SIPs, deposits) have none. Chosen by a grid search over the generated data so that almost every
# month shows a mix: typically Transportation over budget, Food, Utilities and Home close to it
# (80-99%), and Personal, Health, Travel and Other on track.
DEMO_BUDGETS: Dict[str, int] = {
    "Food": 16000,
    "Transportation": 4000,
    "Utilities": 4500,
    "Personal": 9000,
    "Health/Medical": 3000,
    "Home": 6500,
    "Travel": 4000,
    "Other": 5000,
}

# Diwali (Lakshmi Puja) dates; the festive-season spike is built around them.
DIWALI_DATES = {
    2023: date(2023, 11, 12), 2024: date(2024, 11, 1), 2025: date(2025, 10, 21),
    2026: date(2026, 11, 8), 2027: date(2027, 10, 29), 2028: date(2028, 10, 17),
}

_MON = ("", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_CENT = Decimal("0.01")


class DemoResetRefused(RuntimeError):
    """A demo-only write (marking or resetting) was attempted on a database that isn't the demo."""


# ---------------------------------------------------------------------------
# Persona (all fictional) and merchants
# ---------------------------------------------------------------------------

_CARD = "4521XXXXXXXX1234"            # the persona's credit card, masked as on a statement
_CARD_LAST4 = _CARD[-4:]
_POS_PREFIX = f"POS {_CARD} "
_CARD_REFUND_PREFIX = f"REFUND {_CARD} "
_EMPLOYER = "ACME TECHNOLOGIES PVT LTD"
_EMPLOYER_IFSC = "HDFC0000001"
_OWN_SBI_IFSC = "SBIN0001234"
_LANDLORD = ("MANJUNATH R", "HDFC", "manjunath.r1968@okhdfcbank")
_LANDLORD_IFSC = "CNRB0002612"
_COOK = ("LAKSHMAMMA K", "UTIB", "lakshmamma.k1979@okaxis")
_LOAN_ACCOUNT = "P400PL07385516"
_MF_MANDATE = "ICCL0000ZRD4471209"
_RD_ACCOUNT = "XXXXXXXX4417"
_FD_ACCOUNT = "XXXXXXXX7731"

# Amounts at the most recent level; earlier months step back from these (see _Ledger levels).
_LATEST_NET_SALARY = 140_000          # take-home pay after the latest April hike
_SALARY_HIKE = 0.07
_LATEST_RENT = 28_350                 # the lease renews every March, +5%
_LATEST_COOK_PAY = 5_000              # +500 every April
_LATEST_SIP = 7_500                   # stepped up by 2,500 every May, after the hike
_SECOND_SIP = 5_000
_EMI = 6_850
_RD_INSTALLMENT = 2_000

# Electricity bill multiplier by month: Bengaluru's summer peak is March to May.
_ELECTRICITY_SEASON = {
    1: 1.0, 2: 1.15, 3: 1.6, 4: 2.0, 5: 1.85, 6: 1.25,
    7: 1.0, 8: 0.95, 9: 1.0, 10: 1.05, 11: 0.95, 12: 0.95,
}


def _qr_payee(name: str, kind: str) -> Tuple[str, str, str]:
    """A small business's UPI QR handle, derived from its name so it is the same every day."""
    digits = str(int(hashlib.sha256(name.encode("utf-8")).hexdigest(), 16))
    if kind == "paytm":
        return name, "PYTM", f"paytmqr28100505{digits[:6]}@paytm"
    if kind == "phonepe":
        return name, "YESB", f"q{digits[:9]}@ybl"
    return name, "FDRL", f"bharatpe.9{digits[:9]}@fbpe"


# (payee name, payee bank code, VPA)
_UPI = {
    "swiggy": ("SWIGGY", "YESB", "swiggy.stores@ybl"),
    "zomato": ("ZOMATO", "ICIC", "zomato.order@icici"),
    "zepto": ("ZEPTO MARKETPLACE", "HDFC", "zeptomarketplace@hdfcbank"),
    "blinkit": ("BLINKIT", "ICIC", "blinkit.payu@icici"),
    "instamart": ("SWIGGY INSTAMART", "YESB", "instamart.swiggy@ybl"),
    "bigbasket": ("BIGBASKET", "HDFC", "bigbasket.payu@hdfcbank"),
    "uber": ("UBER INDIA", "UTIB", "uber.rides@axisbank"),
    "ola": ("OLA CABS", "ICIC", "olacabs.rides@icici"),
    "rapido": ("RAPIDO", "YESB", "rapido.bike@ybl"),
    "bmrcl": ("BMRCL NAMMA METRO", "HDFC", "bmrcl.recharge@hdfcbank"),
    "bescom": ("BESCOM", "UTIB", "bescom.bbps@axisbank"),
    "indane": ("INDANE GAS", "ICIC", "indane.lpg@icici"),
    "bookmyshow": ("BOOKMYSHOW", "ICIC", "bookmyshow.pay@icici"),
    "practo": ("PRACTO TECHNOLOGIES", "ICIC", "practo.consult@icici"),
    "tata1mg": ("TATA 1MG", "HDFC", "tata1mg.payu@hdfcbank"),
    "urbanco": ("URBAN COMPANY", "YESB", "urbancompany@ybl"),
    "redbus": ("REDBUS", "ICIC", "redbus.pay@icici"),
    "irctc": ("IRCTC", "SBIN", "irctc.eticket@sbi"),
    "cred": ("CRED CLUB", "UTIB", "cred.club@axisb"),
    "zerodha": ("ZERODHA BROKING", "HDFC", "zerodha.funds@hdfcbank"),
    "zerodha_coin": ("ZERODHA COIN", "HDFC", "zerodha.coin@hdfcbank"),
}
# (payee, lowest, highest) for small daily payments; amounts move in steps of 5 rupees.
_STREET_VENDORS = (
    (_qr_payee("SRI LAKSHMI TEA STALL", "paytm"), 15, 60),
    (_qr_payee("ANNAPOORNA DARSHINI", "phonepe"), 40, 180),
    (_qr_payee("RAJU FRUIT STALL", "paytm"), 40, 160),
    (_qr_payee("NAMMA FILTER COFFEE", "bharatpe"), 25, 90),
    (_qr_payee("SLV VEGETABLES", "phonepe"), 60, 280),
    (_qr_payee("MAHALAKSHMI CONDIMENTS", "phonepe"), 30, 220),
    (_qr_payee("BASAVA TENDER COCONUT", "paytm"), 50, 80),
    (_qr_payee("IYENGAR BAKERY", "bharatpe"), 30, 200),
)
_RESTAURANTS = (
    "MANGO LEAF BISTRO KORAMANGALA", "BARLEY AND BRIX INDIRANAGAR", "COASTAL CURRY HOUSE HSR",
    "GREEN CHILLI ANDHRA MESS", "HAPPY BELLY CAFE", "ROOFTOP 27 BREWERY", "UDUPI GRAND VEG",
)
_GROCERS_CARD = ("DMART AVENUE", "SPAR HYPERMARKET")
_FUEL_PUMPS = ("HP PETROL PUMP HSR LAYOUT", "INDIAN OIL KORAMANGALA", "SHELL SARJAPUR ROAD")
_ATMS = ("HDFC ATM KORAMANGALA", "HDFC ATM HSR LAYOUT", "HDFC ATM BTM LAYOUT")
_SHOPS = {
    "amazon": "AMAZON PAY INDIA PRIVA",
    "flipkart": "FLIPKART INTERNET PVT",
    "myntra": "MYNTRA DESIGNS",
    "decathlon": "DECATHLON SPORTS INDIA",
    "hm": "H AND M HENNES MAURITZ",
}
_FESTIVE_APPAREL = ("MYNTRA DESIGNS", "LIFESTYLE INTERNATIONAL", "FABINDIA OVERSEAS")
_SALON = _qr_payee("STYLE STUDIO UNISEX SALON", "phonepe")
_CLINIC = _qr_payee("SRI SAI CLINIC", "bharatpe")
_HARDWARE = _qr_payee("SRI VENKATESHWARA HARDWARE", "paytm")
_SWEET_SHOP = _qr_payee("GANESH SWEETS AND SAVOURIES", "phonepe")
_FLOWER_STALL = _qr_payee("KR MARKET FLOWER STALL", "paytm")
_FRIENDS = (
    ("KARTHIK M", "ICIC", "karthik.m94@okicici"),
    ("PRIYA SHARMA", "HDFC", "priyasharma.07@okhdfcbank"),
    ("ANIKET JOSHI", "SBIN", "aniketj.blr@oksbi"),
    ("DIVYA RAO", "UTIB", "divyarao.92@okaxis"),
    ("MOHIT VERMA", "YESB", "mohitverma11@ybl"),
)
# (remark, category, lowest, highest) for payments to friends.
_FRIEND_PAYMENTS = (
    ("Dinner split", "Food", 300, 1200),
    ("Cab share", "Transportation", 150, 500),
    ("Movie tickets", "Personal", 250, 700),
    ("Trek booking share", "Travel", 1500, 3500),
    ("Gift contribution", "Other", 500, 1500),
)
_TRIPS = (
    {
        "restaurants": ("SUNSET SHACK CALANGUTE", "CAFE PORTO PANJIM", "SEA BREEZE GRILL ANJUNA"),
        "rental": _qr_payee("BAGA SCOOTER RENTALS", "phonepe"),
        "activity": _qr_payee("GRAND ISLAND BOAT TOURS", "paytm"),
        "souvenir": _qr_payee("MAPUSA HANDICRAFTS", "bharatpe"),
    },
    {
        "restaurants": ("FORT KOCHI SEAFOOD CAFE", "KAYAL KITCHEN ALLEPPEY", "MUNNAR TEA GARDEN CAFE"),
        "rental": _qr_payee("ALLEPPEY HOUSEBOAT STAYS", "phonepe"),
        "activity": _qr_payee("KATHAKALI ARTS CENTRE", "paytm"),
        "souvenir": _qr_payee("MATTANCHERRY SPICE MART", "bharatpe"),
    },
)
_GAS_EPOCH = date(2023, 1, 9)  # first cylinder refill of the (absolute) refill sequence


# ---------------------------------------------------------------------------
# Small date and money helpers
# ---------------------------------------------------------------------------

def india_today() -> date:
    """Today's date in India, which is what the demo's 'today' means."""
    return datetime.now(INDIA_TZ).date()


def diwali_date(year: int) -> date:
    """Diwali for `year`; years outside the table fall back to a typical date."""
    return DIWALI_DATES.get(year, date(year, 11, 1))


def _inr(amount) -> Decimal:
    if isinstance(amount, float):
        amount = f"{amount:.2f}"
    return Decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)


def _indian_amount_text(amount: Decimal) -> str:
    """2499 -> '2,499.00'; 123456 -> '1,23,456.00' (Indian digit grouping, as in bank alerts)."""
    whole, frac = f"{amount:.2f}".split(".")
    if len(whole) > 3:
        head, groups = whole[:-3], [whole[-3:]]
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join([head] + groups if head else groups)
    return f"{whole}.{frac}"


def _add_months(first: date, months: int) -> date:
    index = first.year * 12 + (first.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _month_end(first: date) -> date:
    return _add_months(first, 1) - timedelta(days=1)


def _months(start: date, end: date) -> Iterator[date]:
    current = start.replace(day=1)
    while current <= end:
        yield current
        current = _add_months(current, 1)


def _working_day_on_or_after(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _pick(rng: random.Random, values: Sequence, weights: Sequence[float]):
    return rng.choices(values, weights=weights, k=1)[0]


def _trip_start(year: int) -> date:
    """The annual February trip starts on the first Friday on or after 10 February."""
    day = date(year, 2, 10)
    while day.weekday() != 4:
        day += timedelta(days=1)
    return day


# ---------------------------------------------------------------------------
# Sample data generator
# ---------------------------------------------------------------------------

@dataclass
class _Tx:
    seq: int
    date: date
    amount: Decimal
    description: str
    category: str
    type: str
    confidence: float
    priority: int            # 0: designed amount (review/duplicate), 1: fixed amount, 2: may be nudged
    review: bool = False
    tag: str = ""
    dup_twin: bool = False   # the statement line of the deliberate possible-duplicate pair
    ref: str = ""
    locked: bool = False


class _Ledger:
    """Builds the sample ledger. Use `generate_demo_transactions()` instead of this class."""

    def __init__(self, today: date, seed: int):
        self.today = today
        self.seed = seed
        self.start = _add_months(today.replace(day=1), -(MONTHS_OF_HISTORY - 1))
        # One extra month is generated, then dropped, so the first card bill in range can be
        # computed from the previous month's card spending.
        self.gen_start = _add_months(self.start, -1)
        self.txs: List[_Tx] = []
        self._seq = 0
        self._refunds: List[Tuple[_Tx, date, str]] = []
        self._away: set = set()

    # -- randomness -------------------------------------------------------
    # Every stream gets its own generator keyed by a calendar date (or year), so the same day
    # always produces the same transactions whatever `today` is. Draws never depend on whether a
    # row falls inside the window: `_add` drops out-of-window rows only after drawing.
    def _rng(self, *key) -> random.Random:
        return random.Random("|".join(["pfa-demo", str(self.seed), *map(str, key)]))

    @staticmethod
    def _utr(rng: random.Random, day: date) -> str:
        """A 12-digit UPI/IMPS reference; like real RRNs it starts with the year's last digit."""
        return f"{day.year % 10}{rng.randrange(10 ** 11):011d}"

    # -- levels that change once a year ----------------------------------
    def _changes_after(self, day: date, month: int, until: Optional[date] = None) -> int:
        """How many yearly changes (on the 1st of `month`) fall after `day`, up to `until`."""
        until = until or self.today
        return sum(1 for y in range(day.year, until.year + 1) if day < date(y, month, 1) <= until)

    def _salary_level(self, payroll_month: date) -> int:
        # Salary for month M is credited on the first working day of M+1, and the April payroll
        # is the first one with the hike. Levels count back from the latest credited payroll.
        latest_payroll = _add_months(self.today.replace(day=1), -1)
        if self.today < _working_day_on_or_after(self.today.replace(day=1)):
            latest_payroll = _add_months(latest_payroll, -1)
        steps = self._changes_after(payroll_month, 4, until=latest_payroll)
        return int(round(_LATEST_NET_SALARY / (1 + _SALARY_HIKE) ** steps))

    def _rent_level(self, month: date) -> int:
        steps = self._changes_after(month, 3)
        return _LATEST_RENT if steps == 0 else int(round(_LATEST_RENT / 1.05 ** steps, -2))

    def _cook_pay(self, month: date) -> int:
        return max(3_500, _LATEST_COOK_PAY - 500 * self._changes_after(month, 4))

    def _sip_level(self, month: date) -> int:
        return max(2_500, _LATEST_SIP - 2_500 * self._changes_after(month, 5))

    # -- row builders -------------------------------------------------------
    def _add(self, day: date, amount, description: str, category: str, tx_type: str,
             rng: random.Random, *, conf=(90.0, 99.0), priority: int = 2, review: bool = False,
             tag: str = "", dup_twin: bool = False, ref: str = "") -> Optional[_Tx]:
        confidence = round(rng.uniform(*conf), 1) if isinstance(conf, tuple) else float(conf)
        if not (self.gen_start <= day <= self.today):
            return None
        if (tx_type == "TRANSFER") != (category == "Transfer"):
            raise ValueError(f"Transfers must use the Transfer category: {description}")
        self._seq += 1
        tx = _Tx(self._seq, day, _inr(amount), description, category, tx_type, confidence,
                 priority, review, tag, dup_twin, ref)
        self.txs.append(tx)
        return tx

    def _upi(self, rng, day, payee, amount, category, *, remark="Payment", **kw) -> Optional[_Tx]:
        name, bank, vpa = payee
        ref = self._utr(rng, day)
        return self._add(day, amount, f"UPI/DR/{ref}/{name}/{bank}/{vpa}/{remark}", category,
                         "EXPENSE", rng, ref=ref, **kw)

    def _pos(self, rng, day, merchant, amount, category, **kw) -> Optional[_Tx]:
        return self._add(day, amount, f"{_POS_PREFIX}{merchant}", category, "EXPENSE", rng, **kw)

    def _refund_later(self, tx: Optional[_Tx], when: date, kind: str) -> None:
        """Refunds are written after amounts are final, so a refund always matches its purchase."""
        if tx is not None:
            self._refunds.append((tx, when, kind))

    # -- streams -----------------------------------------------------------
    def _plan_away_days(self) -> None:
        """Days spent out of Bengaluru (home for Diwali, the February trip): no local spending."""
        for year in range(self.gen_start.year, self.today.year + 1):
            diwali = diwali_date(year)
            self._away.update(diwali + timedelta(days=n) for n in range(-1, 4))
            trip = _trip_start(year)
            self._away.update(trip + timedelta(days=n) for n in range(4))

    def _daily(self, day: date) -> None:
        rng = self._rng("day", day.isoformat())
        weekday = day.weekday()
        weekend = weekday >= 5
        local = day not in self._away

        if local:
            # Tea stalls, darshinis, fruit and vegetable vendors: small, frequent UPI payments.
            count = _pick(rng, (0, 1, 2, 3), (50, 38, 10, 2) if weekend else (40, 42, 15, 3))
            for _ in range(count):
                payee, low, high = rng.choice(_STREET_VENDORS)
                self._upi(rng, day, payee, rng.randrange(low, high + 1, 5), "Food",
                          remark=rng.choice(("Payment", "UPI")))

            # Food delivery, more often on Friday to Sunday.
            if rng.random() < (0.22, 0.24, 0.24, 0.26, 0.40, 0.44, 0.38)[weekday]:
                payee = _UPI["swiggy"] if rng.random() < 0.55 else _UPI["zomato"]
                order = self._upi(rng, day, payee, int(rng.triangular(150, 750, 300)), "Food",
                                  tag="delivery")
                if rng.random() < 0.03:  # cancelled order, refunded the same or the next day
                    self._refund_later(order, day + timedelta(days=rng.randint(0, 1)), "upi")

            # Quick commerce top-ups.
            if rng.random() < (0.15 if weekend else 0.10):
                payee = _UPI[_pick(rng, ("zepto", "blinkit", "instamart"), (45, 35, 20))]
                self._upi(rng, day, payee, int(rng.triangular(120, 1100, 350)), "Food")

            # Weekly grocery runs, mostly at weekends.
            if rng.random() < (0.28 if weekend else 0.03):
                if rng.random() < 0.35:
                    self._upi(rng, day, _UPI["bigbasket"], int(rng.triangular(450, 2400, 1000)), "Food")
                else:
                    self._pos(rng, day, rng.choice(_GROCERS_CARD),
                              _inr(rng.triangular(450, 2400, 1000)), "Food")

            # Eating out with friends: Friday and Saturday nights.
            if rng.random() < {4: 0.19, 5: 0.22, 6: 0.09}.get(weekday, 0.025):
                place = rng.choice(_RESTAURANTS)
                if rng.random() < 0.7:
                    self._pos(rng, day, place, _inr(rng.triangular(550, 2600, 1000)), "Food")
                else:
                    self._upi(rng, day, _qr_payee(place, "phonepe"),
                              int(rng.triangular(450, 1500, 700)), "Food")

            # Cabs and bike taxis: office days are Tuesday to Thursday (hybrid work).
            if weekday in (1, 2, 3):
                rides = _pick(rng, (0, 1, 2), (50, 38, 12))
            elif weekend:
                rides = _pick(rng, (0, 1, 2), (72, 24, 4))
            else:
                rides = _pick(rng, (0, 1), (88, 12))
            for _ in range(rides):
                provider = _pick(rng, ("rapido", "uber", "ola"), (30, 45, 25))
                fare = rng.randint(55, 190) if provider == "rapido" else int(rng.triangular(100, 520, 220))
                self._upi(rng, day, _UPI[provider], fare, "Transportation")

            # Scooter fuel.
            if rng.random() < 0.06:
                self._pos(rng, day, rng.choice(_FUEL_PUMPS), rng.choice((300, 400, 500, 500, 600)),
                          "Transportation", priority=1)

            # Movies.
            if rng.random() < (0.05 if weekday >= 4 else 0.012):
                self._upi(rng, day, _UPI["bookmyshow"], rng.randint(380, 1100), "Personal",
                          remark="Movie tickets")

        # Online shopping happens wherever the persona is.
        if rng.random() < 0.045:
            shop = _pick(rng, ("amazon", "flipkart", "myntra", "decathlon", "hm"), (40, 18, 25, 9, 8))
            category = "Personal"
            if shop in ("amazon", "flipkart") and rng.random() < 0.45:
                category = "Home"
            purchase = self._pos(rng, day, _SHOPS[shop], rng.randint(4, 30) * 100 - 1, category,
                                 tag="shopping")
            if shop != "decathlon" and rng.random() < 0.15:  # returned within a week or so
                self._refund_later(purchase, day + timedelta(days=rng.randint(4, 10)), "card")

        # Pharmacy.
        if rng.random() < 0.03:
            if rng.random() < 0.6:
                self._pos(rng, day, "APOLLO PHARMACY HSR LAYOUT", _inr(rng.triangular(120, 1600, 380)),
                          "Health/Medical")
            else:
                self._upi(rng, day, _UPI["tata1mg"], int(rng.triangular(150, 1500, 450)),
                          "Health/Medical", remark="Order payment")

    def _monthly(self, first: date) -> None:
        rng = self._rng("month", first.isoformat())
        last = _month_end(first)

        def day(n: int) -> date:
            return first.replace(day=min(n, last.day))

        def some_days(count: int, lo: int = 1, hi: int = 31) -> List[date]:
            return [day(n) for n in sorted(rng.sample(range(lo, min(hi, last.day) + 1), count))]

        month_name = _MON[first.month]

        # Salary for the previous month, credited on the first working day.
        payroll = _add_months(first, -1)
        salary = self._salary_level(payroll) + rng.choice((0, 0, 0, 0, 350, -420, 780, 1250))
        self._add(_working_day_on_or_after(first), salary,
                  f"NEFT CR-{_EMPLOYER_IFSC}-{_EMPLOYER}-SALARY {_MON[payroll.month]} {payroll.year}",
                  "Paycheck", "INCOME", rng, conf=(98.0, 99.8), priority=1)

        # Rent to the landlord between the 1st and the 5th, by UPI or NEFT.
        rent_day = day(rng.randint(1, 5))
        rent = self._rent_level(first)
        if rng.random() < 0.75:
            self._upi(rng, rent_day, _LANDLORD, rent, "Rent", remark=f"Rent {month_name.title()}",
                      conf=(95.0, 99.5), priority=1)
        else:
            utr = f"N{rent_day:%y}{rent_day.timetuple().tm_yday:03d}{rng.randrange(10 ** 8):08d}"
            self._add(rent_day, rent,
                      f"NEFT DR-{_LANDLORD_IFSC}-{_LANDLORD[0]}-NETBANK, MUM-{utr}-RENT {month_name} {first.year}",
                      "Rent", "EXPENSE", rng, conf=(96.0, 99.5), priority=1)

        # The cook's pay: a UPI payment to a person, so only the remark explains it.
        self._upi(rng, day(rng.randint(1, 4)), _COOK, self._cook_pay(first), "Home",
                  remark=f"Salary {month_name.title()}", conf=(88.0, 95.0), priority=1, tag="cook")

        # Personal-loan EMI and SIPs by NACH mandate.
        mandate_day = _working_day_on_or_after(day(5))
        self._add(mandate_day, _EMI, f"ACH D- BAJAJ FINANCE LTD-{_LOAN_ACCOUNT}", "Debt", "EXPENSE",
                  rng, conf=(97.0, 99.6), priority=1)
        self._add(mandate_day, self._sip_level(first),
                  f"ACH D- ZERODHA BROKING-SIP {mandate_day:%Y%m%d}{rng.randint(10, 99)}", "SIP",
                  "EXPENSE", rng, conf=(96.0, 99.5), priority=1)
        self._add(_working_day_on_or_after(day(15)), _SECOND_SIP,
                  f"ACH D- INDIAN CLEARING CORP-{_MF_MANDATE}", "SIP", "EXPENSE", rng,
                  conf=(95.0, 99.5), priority=1)
        self._add(day(10), _RD_INSTALLMENT, f"RD INSTALLMENT DEBIT RD A/C {_RD_ACCOUNT}", "Savings",
                  "EXPENSE", rng, conf=(95.0, 99.5), priority=1)

        # Savings moved to the persona's own SBI account: a transfer, not spending.
        transfer_day = day(rng.randint(9, 13))
        amount = rng.choice((10_000, 10_000, 15_000, 5_000))
        self._add(transfer_day, amount, f"IMPS/P2A/{self._utr(rng, transfer_day)}/SELF/{_OWN_SBI_IFSC}",
                  "Transfer", "TRANSFER", rng, conf=(96.0, 99.5), priority=1)

        # Bills: electricity peaks in summer; broadband and mobile are on card autopay.
        bill = int(rng.randint(1050, 1450) * _ELECTRICITY_SEASON[first.month])
        self._upi(rng, day(rng.randint(14, 19)), _UPI["bescom"], bill, "Utilities",
                  remark="Electricity bill", conf=(96.0, 99.5))
        self._pos(rng, day(7), "ACT FIBERNET BANGALORE", "1179.00", "Utilities", conf=(96.0, 99.5), priority=1)
        self._pos(rng, day(9), "AIRTEL POSTPAID BLR", "706.82", "Utilities", conf=(96.0, 99.5), priority=1)

        # Subscriptions and the gym: identical narrations every month, so /recurring finds them.
        self._pos(rng, day(2), "CULTFIT CUREFIT HEALTHCARE", "1899.00", "Personal", conf=(94.0, 99.0), priority=1)
        self._pos(rng, day(3), "NETFLIX.COM", "649.00", "Personal", conf=(95.0, 99.5), priority=1)
        self._pos(rng, day(14), "SPOTIFY INDIA", "139.00", "Personal", conf=(95.0, 99.5), priority=1)
        self._pos(rng, day(22), "GOOGLE YOUTUBE PREMIUM", "149.00", "Personal", conf=(95.0, 99.5), priority=1)

        # Haircut, metro card top-ups and cash.
        self._upi(rng, day(rng.randint(1, 28)), _SALON, rng.randrange(300, 451, 50), "Personal")
        for metro_day in some_days(_pick(rng, (1, 2), (55, 45))):
            self._upi(rng, metro_day, _UPI["bmrcl"], 500, "Transportation", remark="Metro card recharge",
                      priority=1)
        for atm_day in some_days(_pick(rng, (1, 2), (80, 20))):
            amount = rng.choice((2000, 2000, 2000, 3000, 3000, 5000))
            self._add(atm_day, amount, f"ATM WDL/{rng.choice(_ATMS)}/{rng.randrange(10 ** 6):06d}",
                      "Other", "EXPENSE", rng, conf=(95.0, 99.5), priority=1)

        # A doctor's consultation in some months.
        if rng.random() < 0.3:
            if rng.random() < 0.5:
                self._upi(rng, day(rng.randint(1, 28)), _UPI["practo"], rng.randrange(500, 801, 50),
                          "Health/Medical", remark="Consultation")
            else:
                self._upi(rng, day(rng.randint(1, 28)), _CLINIC, rng.randrange(400, 701, 50),
                          "Health/Medical", remark="Consultation")

        # Home services and small repairs.
        for _ in range(_pick(rng, (0, 1, 2), (30, 50, 20))):
            if rng.random() < 0.55:
                self._upi(rng, day(rng.randint(1, 28)), _UPI["urbanco"], rng.randrange(499, 1600, 100),
                          "Home", remark="Home service", tag="household")
            else:
                self._upi(rng, day(rng.randint(1, 28)), _HARDWARE, rng.randint(150, 900), "Home",
                          tag="household")

        # Occasional stock purchases.
        if rng.random() < 0.12:
            self._upi(rng, day(rng.randint(1, 28)), _UPI["zerodha"], rng.randrange(5000, 15001, 1000),
                      "Stocks", remark="Funds added", priority=1)

        # A weekend getaway in some months (bus tickets and a homestay).
        fridays = [d for d in (day(n) for n in range(1, last.day + 1)) if d.weekday() == 4]
        if rng.random() < 0.18:
            friday = rng.choice(fridays)
            self._upi(rng, friday, _UPI["redbus"], rng.randint(1100, 2400), "Travel", remark="Bus tickets")
            self._pos(rng, friday, "MAKEMYTRIP INDIA PVT LTD", rng.randint(32, 78) * 100, "Travel")

        # Payments to friends, with a remark that says what they were for.
        for _ in range(_pick(rng, (0, 1, 2), (35, 45, 20))):
            remark, category, low, high = rng.choice(_FRIEND_PAYMENTS)
            self._upi(rng, day(rng.randint(1, 28)), rng.choice(_FRIENDS), rng.randrange(low, high + 1, 10),
                      category, remark=remark, conf=(88.0, 96.0), tag="p2p")

        # Quarterly savings-account interest.
        if first.month in (3, 6, 9, 12):
            self._add(last, rng.randint(1050, 1950), "CREDIT INTEREST CAPITALISED", "Interest", "INCOME",
                      rng, conf=(97.0, 99.8), priority=1)

    def _gas_refills(self) -> None:
        """LPG cylinder refills every 44-53 days, as one sequence from a fixed starting date."""
        refill, k = _GAS_EPOCH, 0
        while refill <= self.today:
            rng = self._rng("gas", k)
            self._upi(rng, refill, _UPI["indane"], rng.randint(855, 918), "Utilities",
                      remark="LPG refill", conf=(94.0, 99.0))
            refill += timedelta(days=44 + rng.randint(0, 9))
            k += 1

    def _annual(self, year: int) -> None:
        rng = self._rng("year", year)
        # Annual bonus for the financial year that ended in March, paid in June.
        bonus_day = _working_day_on_or_after(date(year, 6, rng.randint(18, 24)))
        bonus = int(round(self._salary_level(date(year - 1, 4, 1)) * rng.uniform(0.78, 0.9), -2))
        self._add(bonus_day, bonus,
                  f"NEFT CR-{_EMPLOYER_IFSC}-{_EMPLOYER}-ANNUAL BONUS FY {year - 1}-{year % 100:02d}",
                  "Bonus", "INCOME", rng, conf=(97.0, 99.5), priority=1)
        # Part of the bonus goes into a fixed deposit (own money, so a transfer) and into stocks.
        fd_day = bonus_day + timedelta(days=rng.randint(1, 4))
        self._add(fd_day, 50_000, f"TRF TO FD A/C {_FD_ACCOUNT}", "Transfer", "TRANSFER", rng,
                  conf=(96.0, 99.5), priority=1)
        self._upi(rng, bonus_day + timedelta(days=rng.randint(5, 9)), _UPI["zerodha"], 40_000, "Stocks",
                  remark="Funds added", priority=1)
        # Tax-saving ELSS top-up in March.
        self._upi(rng, date(year, 3, rng.randint(8, 24)), _UPI["zerodha_coin"], 10_000, "Investment",
                  remark="ELSS lumpsum", priority=1)
        # Yearly renewals: Amazon Prime (July), health insurance (August), a health check-up,
        # and scooter insurance (December).
        self._pos(rng, date(year, 7, rng.randint(10, 16)), "AMAZON PRIME MEMBERSHIP", "1499.00",
                  "Personal", priority=1)
        self._pos(rng, date(year, 8, rng.randint(8, 20)), "HDFC ERGO GENERAL INSURANCE",
                  rng.choice(("15890.00", "16240.00", "17105.00")), "Health/Medical", priority=1)
        self._pos(rng, date(year, rng.choice((7, 9, 11)), rng.randint(5, 25)), "HEALTHIANS DIAGNOSTICS",
                  "2199.00", "Health/Medical", priority=1)
        self._pos(rng, date(year, 12, rng.randint(3, 20)), "ACKO GENERAL INSURANCE", "1862.00",
                  "Transportation", priority=1)
        self._annual_trip(year)

    def _annual_trip(self, year: int) -> None:
        """A four-day February trip: flights booked in December, the hotel in January."""
        rng = self._rng("trip", year)
        destination = rng.choice(_TRIPS)
        self._pos(rng, date(year - 1, 12, rng.randint(10, 18)), "INDIGO 6E INTERGLOBE AVIATION",
                  rng.randint(12_500, 17_800), "Travel", priority=1)
        self._pos(rng, date(year, 1, rng.randint(8, 15)), "MAKEMYTRIP INDIA PVT LTD",
                  rng.randint(98, 165) * 100, "Travel", priority=1)
        start = _trip_start(year)
        self._upi(rng, start, _UPI["uber"], rng.randint(950, 1350), "Transportation")
        for offset in range(4):
            for _ in range(rng.randint(1, 2)):
                self._pos(rng, start + timedelta(days=offset), rng.choice(destination["restaurants"]),
                          _inr(rng.triangular(600, 2600, 1200)), "Food")
        self._upi(rng, start + timedelta(days=1), destination["rental"], rng.randrange(1500, 2201, 100),
                  "Travel", remark="Rental")
        self._upi(rng, start + timedelta(days=2), destination["activity"], rng.randrange(1200, 2601, 100),
                  "Travel", remark="Booking")
        self._upi(rng, start + timedelta(days=3), destination["souvenir"], rng.randint(700, 2100), "Personal")
        self._upi(rng, start + timedelta(days=3), _UPI["uber"], rng.randint(900, 1300), "Transportation")

    def _festive_season(self, diwali: date) -> None:
        """The run-up to Diwali: festival sales, clothes, gifts, sweets and the trip home.

        Purchases are kept inside one "festive month" (the month of the week before Diwali), so
        that month clearly stands out; travel is booked weeks earlier, as people do.
        """
        rng = self._rng("diwali", diwali.isoformat())
        festive_first = (diwali - timedelta(days=7)).replace(day=1)
        festive_last = min(_month_end(festive_first), diwali)

        def before(days: int) -> date:
            return min(max(diwali - timedelta(days=days), festive_first), festive_last)

        self._upi(rng, diwali - timedelta(days=55), _UPI["irctc"], rng.randint(1850, 2650), "Travel",
                  remark="Ticket booking", priority=1)  # the train back, booked when bookings open
        self._pos(rng, diwali - timedelta(days=26), "INDIGO 6E INTERGLOBE AVIATION",
                  rng.randint(9_800, 13_600), "Travel", priority=1)  # the flight home
        self._pos(rng, before(rng.randint(10, 16)), rng.choice((_SHOPS["amazon"], _SHOPS["flipkart"])),
                  rng.choice((21_999, 24_990, 27_999, 32_990, 36_990)), rng.choice(("Personal", "Home")),
                  priority=1)  # festival sale: a phone or a TV
        self._pos(rng, before(rng.randint(3, 9)), "TITAN WORLD FORUM MALL", rng.choice((4995, 6495, 8995)),
                  "Other", priority=1)  # a gift for the parents
        for _ in range(2):
            self._pos(rng, before(rng.randint(5, 12)), rng.choice(_FESTIVE_APPAREL),
                      rng.randint(20, 55) * 100 - 1, "Personal")
        self._pos(rng, before(rng.randint(4, 9)), "DMART AVENUE", _inr(rng.uniform(1200, 3000)), "Home")
        self._pos(rng, before(rng.randint(2, 6)), "DMART AVENUE", _inr(rng.uniform(2500, 4500)), "Other")  # dry-fruit gift boxes
        self._pos(rng, before(rng.randint(3, 8)), "GIFT STUDIO FORUM MALL", _inr(rng.uniform(3000, 6000)),
                  "Other")
        self._pos(rng, before(rng.randint(4, 10)), rng.choice(_RESTAURANTS), _inr(rng.uniform(2500, 4500)),
                  "Food")
        self._pos(rng, before(2), "SRI KRISHNA JEWELLERS JAYANAGAR", rng.choice((6800, 8400, 9950)),
                  "Personal", priority=1)  # a silver coin on Dhanteras
        self._upi(rng, before(rng.randint(2, 4)), _SWEET_SHOP, rng.randint(2000, 3800), "Food",
                  remark="Diwali sweets")
        self._upi(rng, before(rng.randint(2, 5)), _COOK, rng.choice((2000, 2500)), "Home",
                  remark="Diwali bonus", conf=(88.0, 95.0), priority=1, tag="cook")
        self._upi(rng, before(3), _FLOWER_STALL, rng.randint(450, 950), "Other", remark="Pooja flowers")
        self._upi(rng, before(2), _UPI["uber"], rng.randint(950, 1350), "Transportation")  # to the airport

    def _relative_items(self) -> None:
        """Items placed relative to today, so the newest data always has them."""
        rng = self._rng("today", self.today.isoformat())

        def ago(days: int) -> date:
            return max(self.start, self.today - timedelta(days=days))

        def needs_review(**kw):
            return dict(priority=0, review=True, **kw)

        # Rows that need review: ambiguous payees, low confidence, "Other" or a guessed category.
        self._upi(rng, ago(2), _qr_payee("SRI VINAYAKA ENTERPRISES", "phonepe"), 1840, "Other",
                  **needs_review(conf=61.0))
        self._upi(rng, ago(6), ("NAVEEN KUMAR", "SBIN", "naveenkumar.blr@oksbi"), 2500, "Other",
                  remark="Sent via UPI", **needs_review(conf=57.0))
        self._pos(rng, ago(11), "PAYU*TECHSHOP ONLINE", "3299.00", "Personal", **needs_review(conf=72.0))
        self._add(ago(19), 1250,
                  f"NEFT CR-UTIB0000123-RAZORPAY SOFTWARE PVT LTD-PAYOUT {self._utr(rng, ago(19))}",
                  "Other", "INCOME", rng, **needs_review(conf=64.0))
        self._upi(rng, ago(41), _qr_payee("BHARATPE MERCHANT", "bharatpe"), 760, "Food",
                  **needs_review(conf=69.0))
        self._add(ago(63), 999, "ACH D- TPSL MANDATE COLLECTION-0004277612", "Other", "EXPENSE", rng,
                  **needs_review(conf=55.0))
        self._add(ago(128), 4000, f"IMPS/P2A/{self._utr(rng, ago(128))}/RAHUL S/ICIC0002345", "Other",
                  "EXPENSE", rng, **needs_review(conf=63.0))

        # Exactly one possible duplicate: one card purchase seen both as a Gmail alert and as a
        # statement line (same date and amount), both waiting for the user to decide.
        dup_day, dup_amount = ago(24), Decimal("2499.00")
        self._add(dup_day, dup_amount,
                  f"[Gmail HDFC Alert] Rs.{_indian_amount_text(dup_amount)} spent on card XX{_CARD_LAST4} at AMAZON PAY",
                  "Other", "EXPENSE", rng, **needs_review(conf=66.0))
        self._pos(rng, dup_day, _SHOPS["amazon"], dup_amount, "Personal", **needs_review(conf=74.0, dup_twin=True))

        # Two current-month purchases, so the Budgets screen (which opens on the latest month)
        # shows a category over budget even early in the month.
        first = self.today.replace(day=1)
        self._pos(rng, first + timedelta(days=max(0, round(self.today.day * 0.4) - 1)),
                  "CROMA RETAIL KORAMANGALA", "6999.00", "Personal", conf=(94.0, 98.5), priority=1)
        self._pos(rng, first + timedelta(days=max(0, round(self.today.day * 0.7) - 1)),
                  rng.choice(_RESTAURANTS), _inr(rng.uniform(2900, 3600)), "Food", conf=(93.0, 98.5))

    # -- post-processing ----------------------------------------------------
    @staticmethod
    def _make_unique(rows: List[_Tx], used: set) -> None:
        """Keeps (date, amount) unique so the only possible duplicate is the deliberate pair.

        Designed and fixed amounts win; a colliding variable amount moves up by 1 rupee (10 for
        round amounts).
        """
        for tx in sorted(rows, key=lambda t: (t.priority, t.seq)):
            if tx.dup_twin:
                continue
            while (tx.date, tx.amount) in used:
                tx.amount += Decimal(10) if tx.amount % 10 == 0 else Decimal(1)
            used.add((tx.date, tx.amount))

    def _materialize_refunds(self) -> List[_Tx]:
        added = []
        for original, when, kind in self._refunds:
            rng = self._rng("refund", original.date.isoformat(), original.description)
            if kind == "card":
                merchant = original.description[len(_POS_PREFIX):]
                description = f"{_CARD_REFUND_PREFIX}{merchant}"
            else:
                name, bank, vpa = original.description.split("/")[3:6]
                description = f"UPI/CR/{self._utr(rng, when)}/{name}/{bank}/{vpa}/REFUND {original.ref}"
            tx = self._add(when, original.amount, description, original.category, "INCOME", rng,
                           conf=(92.0, 98.5))
            if tx is not None:
                added.append(tx)
        return added

    def _card_bills(self) -> List[_Tx]:
        """Pays each month's card spending (net of card refunds) early the next month."""
        spend: Dict[Tuple[int, int], Decimal] = defaultdict(Decimal)
        for tx in self.txs:
            key = (tx.date.year, tx.date.month)
            if tx.type == "EXPENSE" and tx.description.startswith(_POS_PREFIX):
                spend[key] += tx.amount
            elif tx.type == "INCOME" and tx.description.startswith(_CARD_REFUND_PREFIX):
                spend[key] -= tx.amount
        bills = []
        for first in _months(self.start, self.today):
            previous = _add_months(first, -1)
            due = spend.get((previous.year, previous.month), Decimal(0))
            rng = self._rng("card-bill", first.isoformat())
            pay_day = first.replace(day=rng.randint(5, 8))
            method = _pick(rng, ("cred", "autopay", "netbanking"), (50, 35, 15))
            if method == "cred":
                name, bank, vpa = _UPI["cred"]
                description = f"UPI/DR/{self._utr(rng, pay_day)}/{name}/{bank}/{vpa}/CC bill payment"
            elif method == "autopay":
                description = f"CC 000{_CARD[:4]}XXXXXX{_CARD_LAST4} AUTOPAY SI-TAD"
            else:
                description = f"BIL/ONL/{self._utr(rng, pay_day)}/HDFC BANK CREDIT CARD/XX{_CARD_LAST4}"
            if due > 0:
                tx = self._add(pay_day, due, description, "Transfer", "TRANSFER", rng, conf=(96.0, 99.5))
                if tx is not None:
                    bills.append(tx)
        return bills

    def _ensure_activity_today(self, used: set) -> None:
        if any(tx.date == self.today for tx in self.txs):
            return
        rng = self._rng("today", self.today.isoformat(), "activity")
        payee, low, high = _STREET_VENDORS[0]
        tx = self._upi(rng, self.today, payee, rng.randrange(low, high + 1, 5), "Food", remark="UPI")
        self._make_unique([tx], used)

    def _mark_user_corrections(self) -> None:
        """A few recent rows look as if the user edited or confirmed them (locked, 100%)."""
        rng = self._rng("today", self.today.isoformat(), "corrections")
        low, high = self.today - timedelta(days=120), self.today - timedelta(days=3)
        window = [t for t in self.txs if low <= t.date <= high and t.type == "EXPENSE" and not t.review]
        picks = [t for t in window if t.tag == "cook"][-2:]
        others = [t for t in window if t.tag in ("p2p", "household", "shopping")]
        picks += rng.sample(others, min(3, len(others)))
        for tx in picks:
            tx.locked, tx.confidence = True, 100.0

    def build(self) -> List[dict]:
        self._plan_away_days()
        day = self.gen_start
        while day <= self.today:
            self._daily(day)
            day += timedelta(days=1)
        for first in _months(self.gen_start, self.today):
            self._monthly(first)
        self._gas_refills()
        for year in range(self.gen_start.year, self.today.year + 1):
            self._annual(year)
            self._festive_season(diwali_date(year))
        self._relative_items()

        used: set = set()
        self._make_unique(self.txs, used)
        self._make_unique(self._materialize_refunds(), used)
        self._make_unique(self._card_bills(), used)
        self.txs = [tx for tx in self.txs if tx.date >= self.start]
        self._ensure_activity_today(used)
        self._mark_user_corrections()

        rows = [
            {
                "transaction_date": tx.date,
                "amount": tx.amount,
                "description": tx.description,
                "category_id": CATEGORY_IDS[tx.category],
                "transaction_type": tx.type,
                "ai_confidence_score": tx.confidence,
                "is_self_transfer": tx.type == "TRANSFER",
                "row_signature": compute_row_signature(tx.date, tx.amount, tx.description, tx.type),
                "requires_human_review": tx.review,
                "user_locked": tx.locked,
            }
            for tx in sorted(self.txs, key=lambda t: (t.date, t.seq))
        ]
        if len({r["row_signature"] for r in rows}) != len(rows):  # guaranteed by _make_unique
            raise RuntimeError("Demo data generator produced duplicate row signatures.")
        return rows


def generate_demo_transactions(today: date, seed: int = DEFAULT_SEED) -> List[dict]:
    """About 18 months of sample transactions ending at `today` (never later). Pure and deterministic.

    Each dict has exactly the `transactions` columns the reset inserts: transaction_date (date),
    amount (Decimal, positive, 2 decimals), description, category_id (see CATEGORIES),
    transaction_type ('EXPENSE' | 'INCOME' | 'TRANSFER'), ai_confidence_score (0-100),
    is_self_transfer, row_signature (database.compute_row_signature, unique),
    requires_human_review and user_locked. Rows are sorted by date.
    """
    if isinstance(today, datetime):
        today = today.date()
    return _Ledger(today, seed).build()


def possible_duplicate_groups(rows: Sequence[dict]) -> List[List[dict]]:
    """Groups of rows that share date, amount and type, i.e. what a duplicate check would flag."""
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["transaction_date"], row["amount"], row["transaction_type"])].append(row)
    return [group for group in groups.values() if len(group) > 1]


def monthly_summary(rows: Sequence[dict]) -> List[dict]:
    """Per-month totals computed the way the API does (transfers count as neither side)."""
    months: Dict[str, dict] = {}
    for row in rows:
        key = row["transaction_date"].strftime("%Y-%m")
        month = months.setdefault(key, {"month": key, "count": 0, "income": Decimal(0),
                                        "expense": Decimal(0), "transfers": Decimal(0), "review": 0})
        month["count"] += 1
        month["review"] += int(row["requires_human_review"])
        bucket = {"INCOME": "income", "EXPENSE": "expense", "TRANSFER": "transfers"}[row["transaction_type"]]
        month[bucket] += row["amount"]
    for month in months.values():
        month["net"] = month["income"] - month["expense"]
        month["savings_rate_pct"] = (
            float(round(month["net"] / month["income"] * 100, 1)) if month["income"] else 0.0
        )
    return [months[key] for key in sorted(months)]


def summarize(rows: Sequence[dict], today: date) -> dict:
    """Counts that describe a generated data set (also returned by `reset_demo_data`)."""
    types = Counter(row["transaction_type"] for row in rows)
    months = sorted({row["transaction_date"].strftime("%Y-%m") for row in rows})
    dates = [row["transaction_date"] for row in rows]
    return {
        "today": today.isoformat(),
        "date_range": {"start": min(dates).isoformat(), "end": max(dates).isoformat()},
        "months_covered": len(months),
        "first_month": months[0],
        "last_month": months[-1],
        "transactions": len(rows),
        "by_type": {kind: types.get(kind, 0) for kind in ("EXPENSE", "INCOME", "TRANSFER")},
        "review_count": sum(1 for row in rows if row["requires_human_review"]),
        "possible_duplicate_pairs": len(possible_duplicate_groups(rows)),
        "user_locked": sum(1 for row in rows if row["user_locked"]),
    }


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------

def _url_names_personal_project(url) -> bool:
    ref = config.PERSONAL_SUPABASE_PROJECT_REF.lower()
    try:
        rendered = url.render_as_string(hide_password=True)
    except AttributeError:
        rendered = str(url)
    parts = (getattr(url, "username", None), getattr(url, "host", None),
             getattr(url, "database", None), rendered)
    return any(ref in str(part).lower() for part in parts if part)


def check_demo_target(engine=None, action: str = "modify the demo data") -> None:
    """Raises DemoResetRefused unless this backend is configured for the demo database.

    Checks configuration only (no database access). The database-level marker check happens
    inside `mark_database_as_demo` / `reset_demo_data`.
    """
    personal_ref = config.PERSONAL_SUPABASE_PROJECT_REF
    if config.APP_ENV != DEMO_ENVIRONMENT:
        raise DemoResetRefused(
            f"Refusing to {action}: APP_ENV is '{config.APP_ENV}'. This only runs in the demo "
            "environment (APP_ENV=demo)."
        )
    if not config.DATABASE_URL and config.SUPABASE_PROJECT_REF == personal_ref:
        raise DemoResetRefused(
            f"Refusing to {action}: the configured Supabase project is the personal one ({personal_ref})."
        )
    if personal_ref.lower() in (config.DATABASE_URL or "").lower():
        raise DemoResetRefused(f"Refusing to {action}: DATABASE_URL points at the personal Supabase project.")
    if engine is not None and _url_names_personal_project(engine.url):
        raise DemoResetRefused(f"Refusing to {action}: the database connection is for the personal Supabase project.")


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(sqlalchemy.text("SELECT to_regclass(:name) IS NOT NULL"),
                             {"name": f"public.{table}"}).scalar())


def _environment_marker(conn) -> Optional[str]:
    if not _table_exists(conn, "app_meta"):
        return None
    return conn.execute(sqlalchemy.text("SELECT value FROM app_meta WHERE key = :key"),
                        {"key": ENVIRONMENT_KEY}).scalar()


# ---------------------------------------------------------------------------
# Database writes
# ---------------------------------------------------------------------------

_CATEGORIES_TABLE = sqlalchemy.table(
    "categories", sqlalchemy.column("id", sqlalchemy.Integer), sqlalchemy.column("name", sqlalchemy.String),
)
_BUDGETS_TABLE = sqlalchemy.table(
    "budgets", sqlalchemy.column("category", sqlalchemy.String),
    sqlalchemy.column("monthly_budget", sqlalchemy.Numeric(12, 2)),
)
_TRANSACTIONS_TABLE = sqlalchemy.table(
    "transactions",
    sqlalchemy.column("transaction_date", sqlalchemy.Date),
    sqlalchemy.column("amount", sqlalchemy.Numeric(12, 2)),
    sqlalchemy.column("description", sqlalchemy.Text),
    sqlalchemy.column("category_id", sqlalchemy.Integer),
    sqlalchemy.column("transaction_type", sqlalchemy.String),
    sqlalchemy.column("ai_confidence_score", sqlalchemy.Float),
    sqlalchemy.column("is_self_transfer", sqlalchemy.Boolean),
    sqlalchemy.column("row_signature", sqlalchemy.String),
    sqlalchemy.column("requires_human_review", sqlalchemy.Boolean),
    sqlalchemy.column("user_locked", sqlalchemy.Boolean),
)


def mark_database_as_demo(engine: sqlalchemy.engine.Engine) -> None:
    """One-time bootstrap: writes the marker row app_meta(environment = demo).

    Idempotent when the marker is already there. Refuses when the configuration isn't the demo's,
    when the database is marked as something else, or when it already holds user data without the
    marker, so the personal database can never be marked. Runs in one transaction: a refusal
    leaves no trace (not even the app_meta table).
    """
    check_demo_target(engine, "mark this database as the demo database")
    with engine.begin() as conn:
        created = not _table_exists(conn, "app_meta")
        conn.execute(sqlalchemy.text(APP_META_DDL))
        if created:
            # Same lockdown as every other table (DatabaseManager re-applies it at startup).
            conn.execute(sqlalchemy.text("ALTER TABLE app_meta ENABLE ROW LEVEL SECURITY"))
        marker = _environment_marker(conn)
        if marker == DEMO_ENVIRONMENT:
            logger.info("Database already carries the demo marker.")
            return
        if marker is not None:
            raise DemoResetRefused(
                f"Refusing to mark this database as the demo database: it is marked as '{marker}'."
            )
        populated = []
        for table in USER_DATA_TABLES:
            if _table_exists(conn, table):
                count = conn.execute(sqlalchemy.text(f"SELECT count(*) FROM {table}")).scalar()
                if count:
                    populated.append(f"{table}: {count} rows")
        if populated:
            raise DemoResetRefused(
                "Refusing to mark this database as the demo database: it already holds data and has "
                f"no demo marker ({', '.join(populated)}). Only an empty database can become the demo."
            )
        conn.execute(
            sqlalchemy.text("INSERT INTO app_meta (key, value, updated_at) VALUES (:key, :value, CURRENT_TIMESTAMP)"),
            {"key": ENVIRONMENT_KEY, "value": DEMO_ENVIRONMENT},
        )
    logger.warning("Marked the connected database as the demo database (app_meta environment = demo).")


def reset_demo_data(engine: sqlalchemy.engine.Engine, today: Optional[date] = None) -> dict:
    """Restores the demo sample data in ONE transaction and returns summary counts.

    Wipes the ledger tables (see RESET_TABLES) and reloads the 18 categories with their fixed ids,
    the demo budgets and the generated transactions. AI usage counters are kept, except rows older
    than 30 days. Raises DemoResetRefused if the configuration or the database marker says this
    isn't the demo database; any other failure rolls everything back, leaving the old data intact.
    """
    check_demo_target(engine, "reset the demo data")
    today = today or india_today()
    started = time.monotonic()
    rows = generate_demo_transactions(today)

    with engine.begin() as conn:
        # Don't queue behind a long query for ever (TRUNCATE needs exclusive locks), and never
        # hold those locks for long.
        conn.execute(sqlalchemy.text("SET LOCAL lock_timeout = '15s'"))
        conn.execute(sqlalchemy.text("SET LOCAL statement_timeout = '120s'"))
        marker = _environment_marker(conn)
        if marker != DEMO_ENVIRONMENT:
            raise DemoResetRefused(
                "Refusing to reset the demo data: the connected database has no demo marker "
                f"(app_meta environment = {marker!r}). Run scripts/demo_reset.py --mark-demo once "
                "against the empty demo database."
            )
        missing = [table for table in RESET_TABLES if not _table_exists(conn, table)]
        if missing:
            raise RuntimeError(
                f"Demo reset needs tables that don't exist yet: {', '.join(missing)}. "
                "Start the backend once (DatabaseManager creates the schema), then retry."
            )

        conn.execute(sqlalchemy.text(f"TRUNCATE {', '.join(RESET_TABLES)} RESTART IDENTITY CASCADE"))
        ai_usage_deleted = 0
        if _table_exists(conn, "ai_usage"):
            ai_usage_deleted = conn.execute(
                sqlalchemy.text("DELETE FROM ai_usage WHERE day < :cutoff"),
                {"cutoff": today - timedelta(days=AI_USAGE_RETENTION_DAYS)},
            ).rowcount

        conn.execute(sqlalchemy.insert(_CATEGORIES_TABLE).values(
            [{"id": cid, "name": name} for cid, name in CATEGORIES.items()]
        ))
        conn.execute(
            sqlalchemy.text("SELECT setval(pg_get_serial_sequence('categories', 'id'), :last_id)"),
            {"last_id": max(CATEGORIES)},
        )
        conn.execute(sqlalchemy.insert(_BUDGETS_TABLE).values(
            [{"category": name, "monthly_budget": Decimal(amount)} for name, amount in DEMO_BUDGETS.items()]
        ))
        # DatabaseManager seeds its default budgets once per database unless this flag exists;
        # set it so they can never be added on top of the demo budgets.
        conn.execute(sqlalchemy.text(
            "INSERT INTO app_meta (key, value) VALUES ('budgets_seeded', 'true') ON CONFLICT (key) DO NOTHING"
        ))
        for offset in range(0, len(rows), INSERT_CHUNK_ROWS):
            conn.execute(sqlalchemy.insert(_TRANSACTIONS_TABLE).values(rows[offset:offset + INSERT_CHUNK_ROWS]))
        loaded = conn.execute(sqlalchemy.text("SELECT count(*) FROM transactions")).scalar()
        if loaded != len(rows):
            raise RuntimeError(f"Demo reset loaded {loaded} transactions instead of {len(rows)}.")

    summary = summarize(rows, today)
    summary.update({
        "categories": len(CATEGORIES),
        "budgets": len(DEMO_BUDGETS),
        "ai_usage_rows_deleted": int(ai_usage_deleted or 0),
        "seconds": round(time.monotonic() - started, 2),
    })
    logger.info(
        "Demo data reset: %(transactions)s transactions over %(months_covered)s months "
        "(%(review_count)s to review) in %(seconds)ss.", summary,
    )
    return summary
