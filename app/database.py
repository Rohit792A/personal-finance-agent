# database.py
"""Enterprise PostgreSQL Persistence & 3-Layer Cross-Source Reconciliation Engine.

FDE Pillar 3 (SCALE):
- Manages pooled SQLAlchemy (`pg8000`) connections supporting both Supabase PostgreSQL URIs
  and Google Cloud SQL Connector (`get_cloud_sql_credentials`).
- Prevents duplicate rows with three mechanisms:
  1. Deterministic SHA-256 `row_signature` (`date|amount|description|type`) and `file_hash`
     (`ingested_files`: a statement file is imported once, claimed inside the import transaction).
  2. Cross-Source Reconciliation (`find_reconcilable_transaction`, rules in `reconcile_rule`):
     an incoming row that is already in the ledger (a Gmail alert, an overlapping statement, a
     re-import that labelled it differently, the other side of a transfer between your own
     accounts) updates that row instead of adding a duplicate. Evidence: a shared 12-digit
     UPI/IMPS reference number (any type, +/- 2 days), or on the same date a matching amount
     plus alert-vs-statement, a shared merchant word, or similar wording when one side is a
     transfer.
  3. `merged_labels`: a re-import never relabels a row on its own say-so. The type changes only
     when the same reference appears in two different banks' records (a transfer between your
     own accounts); other disagreements keep the row and mark it for review, and "Other" never
     replaces a specific category. DEC-034.
"""
import logging
import mimetypes
import sqlalchemy
import hashlib
import subprocess
from sqlalchemy.exc import SQLAlchemyError
from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials
import google.auth

import config
import owner_profile
from config import DB_USER, DB_NAME
# The password is resolved at connection time via config.get_db_password() (Secret Manager),
# never imported as a module-level constant.
from models import ReceiptData
from exceptions import DatabaseError

logger = logging.getLogger(__name__)

# Starting monthly budgets per category. Until Phase 1 these lived only in server memory
# (api.py CUSTOM_BUDGETS) and were lost on restart; now they seed the `budgets` table once per
# database, and edits from the Budgets screen are stored there.
DEFAULT_BUDGETS = {
    "Food": 2500.0,
    "Rent": 15000.0,
    "Utilities": 5000.0,
    "Personal": 2000.0,
    "Transportation": 4000.0,
    "Health/Medical": 5000.0,
    "Travel": 10000.0,
    "SIP": 20000.0,
    "Investment": 15000.0,
    "Home": 8000.0,
    "Other": 5000.0,
}

# Idempotent lockdown of the `public` schema (see DatabaseManager._apply_security_hardening).
# Shared with scripts/phase0_db_migration.py so both run exactly the same statements.
SECURITY_HARDENING_SQL = """
    DO $$
    DECLARE r record;
    BEGIN
        FOR r IN
            SELECT c.relname
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND NOT c.relrowsecurity
        LOOP
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.relname);
        END LOOP;

        IF (SELECT count(*) FROM pg_roles WHERE rolname IN ('anon', 'authenticated')) = 2 THEN
            REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
            REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
            REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;
        END IF;
    END
    $$;
"""

def get_cloud_sql_credentials():
    """Retrieve credentials for Cloud SQL Connector, supporting Cloud Shell, local dev, and service accounts."""
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            stderr=subprocess.DEVNULL,
            timeout=4
        ).decode().strip()
        if token:
            return Credentials(token=token)
    except Exception:
        pass

    try:
        creds, _ = google.auth.default()
        return creds
    except Exception:
        return None

import re
from collections import Counter, namedtuple
from datetime import date, timedelta

_BANK_STOPWORDS = {
    "upi", "imps", "neft", "rtgs", "ach", "pos", "atm", "ecs", "bil", "inb",
    "bank", "hdfc", "hdfcbank", "sbi", "icici", "icicibank", "axis", "axisbank",
    "kotak", "idfc", "pnb", "bob", "yes", "alert", "gmail", "ref", "utr", "rrn",
    "vpa", "paytm", "okaxis", "okhdfcbank", "okicici", "oksbi", "ybl", "ibl", "axl",
    "debited", "credited", "spent", "purchase", "card", "acct", "account",
    "transfer", "payment", "paid", "from", "for", "the", "and", "india", "pvt",
    "ltd", "limited", "services", "online", "bangalore", "blr", "mumbai", "delhi",
    "transaction", "towards", "info", "chq", "wdl", "dep", "by", "to", "in", "at",
}

_REFERENCE_NUMBER = re.compile(r"(?<!\d)\d{12}(?!\d)")
_GENERIC_CATEGORIES = {"", "?", "other", "others", "uncategorized", "uncategorised"}


class FileAlreadyImported(Exception):
    """This exact file (same SHA-256) was imported before; nothing was added (DEC-034)."""

    def __init__(self, file_name: str, imported_at=None, rows_imported: int = None):
        super().__init__(f"{file_name} was already imported")
        self.file_name = file_name
        self.imported_at = imported_at
        self.rows_imported = rows_imported


def extract_reference_numbers(text: str) -> set[str]:
    """12-digit UPI/IMPS reference numbers (RRN) in a narration or alert.

    Only 12 digits count. Narrations also carry phone numbers (10 digits, e.g. your own number in
    every UPI transfer) and account numbers, which made two different payments look like one
    (DEC-034; the duplicate cleanup uses the same rule, DEC-032).
    """
    return set(_REFERENCE_NUMBER.findall(str(text or "")))


def is_alert_description(text: str) -> bool:
    """Returns True if the transaction description originated from a daily email/SMS alert."""
    if not text:
        return False
    lower = text.lower()
    return "alert]" in lower or lower.startswith("[gmail")


def extract_merchant_tokens(text: str) -> set[str]:
    """Extracts normalized merchant/counterparty tokens for cross-source matching."""
    if not text:
        return set()
    cleaned = re.sub(r"\[[^\]]*\]", " ", text.lower())
    cleaned = re.sub(r"[^a-z\s]", " ", cleaned)
    return {
        tok for tok in cleaned.split()
        if len(tok) >= 3 and tok not in _BANK_STOPWORDS
    }


def compute_row_signature(
    trans_date, amount: float, description: str, trans_type: str
) -> str:
    """Generates a unique deterministic fingerprint for a single transaction row."""
    normalized_str = f"{trans_date}|{float(amount):.2f}|{description.strip().lower()}|{trans_type.strip().upper()}"
    return hashlib.sha256(normalized_str.encode("utf-8")).hexdigest()


def _existing_id_for_signature(db_conn, row_sig: str) -> int:
    """Returns the id of the existing row with this fingerprint.

    Used when an upsert's DO UPDATE is skipped because the existing row is user_locked,
    in which case PostgreSQL's RETURNING yields no row.
    """
    row = db_conn.execute(
        sqlalchemy.text("SELECT id FROM transactions WHERE row_signature = :sig LIMIT 1"),
        {"sig": row_sig},
    ).fetchone()
    return row[0]


def description_similarity(first: str, second: str) -> float:
    """How alike two descriptions are, from 0 to 1.

    1.0 for the same letters (digits and punctuation ignored); otherwise the larger of the word
    overlap (Jaccard) and containment, so a short "HUNGERBOX ORDER" matches the long UPI narration.
    """
    if re.sub(r"[^a-z]", "", (first or "").lower()) == re.sub(r"[^a-z]", "", (second or "").lower()):
        return 1.0  # includes two empty descriptions
    tokens_a, tokens_b = extract_merchant_tokens(first), extract_merchant_tokens(second)
    if not tokens_a or not tokens_b:
        return 0.0
    shared = len(tokens_a & tokens_b)
    return max(shared / len(tokens_a | tokens_b), shared / min(len(tokens_a), len(tokens_b)))


def is_generic_category(name) -> bool:
    """True for "Other" and empty or placeholder categories."""
    return (name or "").strip().lower() in _GENERIC_CATEGORIES


def _norm_type(trans_type) -> str:
    return str(trans_type or "EXPENSE").strip().upper()


# Why an incoming row was recognised as one already in the ledger (strongest first).
MATCH_SAME_REFERENCE = "same reference number"
MATCH_ALERT_AND_STATEMENT = "alert + statement"
MATCH_SAME_WORDING = "same wording"
MATCH_EXCEL_AND_STATEMENT = "excel + statement"
MATCH_RELABELLED = "labelled differently"
_MATCH_STRENGTH = {
    MATCH_SAME_REFERENCE: 5,
    MATCH_ALERT_AND_STATEMENT: 4,
    MATCH_SAME_WORDING: 3,
    MATCH_EXCEL_AND_STATEMENT: 2,
    MATCH_RELABELLED: 1,
}

ReconcileMatch = namedtuple("ReconcileMatch", "id description transaction_type category user_locked rule sources")


def is_bank_statement_description(text: str) -> bool:
    """True when a row description carries a bank statement tag or raw rail narration."""
    raw = (text or "").strip()
    if not raw:
        return False
    if re.search(r"\[[^\]]*statement\]", raw, flags=re.IGNORECASE):
        return True
    if re.match(r"^(UPI|IMPS|NEFT|RTGS|ACH|PCD|POS)[/\-\s]", raw, flags=re.IGNORECASE):
        return True
    if re.search(r"\(Ref:\s*\d+\)", raw, flags=re.IGNORECASE):
        return True
    return False


def combine_excel_and_bank_description(excel_desc: str, bank_desc: str) -> str:
    """Combines the user's Excel description with the bank statement narration & UPI reference number."""
    ex = (excel_desc or "").strip()
    bk = (bank_desc or "").strip()
    if not ex:
        return bk
    if not bk:
        return ex
    if bk.lower().startswith(ex.lower()):
        return bk
    if ex.lower().startswith(bk.lower()):
        return ex
    return f"{ex} {bk}"

# Banks named in Gmail alert tags ("[HDFC Alert] ...") and statement file names
# ("Kotak_Bank_2026-09_....pdf", "SBI_2026-10_....pdf"). Used only to tell two accounts apart.
_BANKS = ("hdfc", "kotak", "sbi", "icici", "axis", "idfc", "pnb", "bob", "indusind", "canara")
_BANK_IN_NAME = re.compile(r"(?<![a-z])(" + "|".join(_BANKS) + r")(?![a-z])")
_ALERT_BANK = re.compile(r"^\s*\[([a-z]+)[^\]]*alert\]")


def account_hint(source: str, description: str):
    """The bank a row's record came from, when it is clear, else None: the bank in a Gmail alert's
    tag, or the only bank named in the statement's file name. Receipts, hand-entered rows and
    unnamed files give None."""
    alert = _ALERT_BANK.match((description or "").lower())
    if alert:
        return alert.group(1) if alert.group(1) in _BANKS else None
    if not source or source.startswith("gmail://"):
        return None
    banks = set(_BANK_IN_NAME.findall(source.rstrip("/").rsplit("/", 1)[-1].lower()))
    return banks.pop() if len(banks) == 1 else None


def account_hints(sources, description) -> set:
    """Every bank a ledger row is known to come from: its alert tag and all its files (a row
    that absorbed copies, e.g. through the duplicate cleanup, carries each copy's file)."""
    banks = {account_hint("", description)} | {account_hint(source, "") for source in (sources or ())}
    banks.discard(None)
    return banks


def _similar_wording(first: str, second: str) -> bool:
    """description_similarity >= 0.5, where two descriptions without any letters don't count."""
    if not re.search(r"[a-z]", (first or "").lower()) or not re.search(r"[a-z]", (second or "").lower()):
        return False
    return description_similarity(first, second) >= 0.5


def reconcile_rule(incoming_date, incoming_desc, incoming_type, existing_date, existing_desc, existing_type):
    """The rule that makes an existing row (same amount, within 2 days) the incoming transaction,
    or None if they are different transactions (DEC-034).

    1. Both carry 12-digit reference numbers: the same payment if they share one, whatever the
       labels say (an alert and its statement line, a re-import that labelled it differently,
       or the two sides of a transfer between your own accounts); two payments if they don't.
    2. Otherwise only the same calendar date counts:
       - same type: a Gmail alert and a statement or receipt row, a shared merchant word, or the
         same letters (e.g. "KOTAK811" and "KOTAK811/620116025326");
       - different type: one side is a transfer and the wording is similar (a re-import or an
         overlapping statement that labelled the same line differently).
       An expense and an income without a shared reference are never merged.
    """
    refs_in, refs_existing = extract_reference_numbers(incoming_desc), extract_reference_numbers(existing_desc)
    if refs_in and refs_existing:
        return MATCH_SAME_REFERENCE if refs_in & refs_existing else None
    if str(existing_date)[:10] != str(incoming_date)[:10]:
        return None
    type_in, type_existing = _norm_type(incoming_type), _norm_type(existing_type)
    if type_in == type_existing:
        if is_alert_description(incoming_desc) != is_alert_description(existing_desc):
            return MATCH_ALERT_AND_STATEMENT
        if (extract_merchant_tokens(incoming_desc) & extract_merchant_tokens(existing_desc)
                or _similar_wording(incoming_desc, existing_desc)):
            return MATCH_SAME_WORDING
        return None
    if "TRANSFER" in (type_in, type_existing) and _similar_wording(incoming_desc, existing_desc):
        return MATCH_RELABELLED
    return None


def merged_labels(existing_type, existing_category, incoming_type, incoming_category, between_accounts=False):
    """(type, category, is_transfer, conflict) for a row that an incoming copy is merged into.

    The type changes only with proof (DEC-034):
    - `between_accounts`: the same 12-digit reference appears in records of two different banks
      (e.g. the HDFC alert and the Kotak statement line), so the money moved between your own
      accounts: the row becomes a transfer.
    - Otherwise, if the copies disagree on expense / income / transfer, nothing proves which is
      right (an AI reading of a re-exported statement can mislabel a purchase): the row keeps its
      labels and `conflict` is True, so it is marked for review.
    - Same type: the incoming category wins, unless it is only "Other" and the row already has a
      specific one.
    """
    type_existing, type_in = _norm_type(existing_type), _norm_type(incoming_type)
    if between_accounts:
        category = existing_category if (type_existing == "TRANSFER" and existing_category) else "Transfer"
        return "TRANSFER", category, True, False
    if type_in != type_existing:
        return type_existing, existing_category, type_existing == "TRANSFER", True
    if is_generic_category(incoming_category) and not is_generic_category(existing_category):
        return type_existing, existing_category, type_existing == "TRANSFER", False
    return type_in, incoming_category, type_in == "TRANSFER", False


def find_reconcilable_transaction(
    db_conn,
    trans_date,
    amount: float,
    description: str,
    trans_type: str,
    exclude_ids: set[int] = None,
):
    """The existing row that is this incoming transaction, or None (rules: `reconcile_rule`).

    Looks at rows of any type with the same amount within 2 days and returns the best match:
    the strongest rule, then the closest date, then the same type, then the oldest row.
    `exclude_ids` holds the rows the current batch has already used, so two lines of one
    statement never merge and an existing row absorbs at most one incoming line.
    """
    exclude_ids = exclude_ids or set()
    candidates = db_conn.execute(sqlalchemy.text("""
        SELECT t.id, t.transaction_date, t.description,
               UPPER(COALESCE(t.transaction_type, 'EXPENSE')) AS ttype,
               COALESCE(c.name, '') AS category, t.user_locked,
               (SELECT array_agg(gcs_uri ORDER BY id) FROM assets WHERE transaction_id = t.id) AS sources
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE ABS(t.amount - :amount) < 0.01
          AND t.transaction_date BETWEEN (CAST(:date AS DATE) - INTERVAL '2 days')
                                     AND (CAST(:date AS DATE) + INTERVAL '2 days')
    """), {"amount": float(amount), "date": str(trans_date)[:10]}).fetchall()

    incoming_day = date.fromisoformat(str(trans_date)[:10])
    incoming_type = _norm_type(trans_type)
    best, best_key = None, None
    same_day_excel_bank = []
    in_is_bank = is_bank_statement_description(description)
    in_is_alert = is_alert_description(description)
    for row in candidates:
        cand_id, cand_date, cand_desc, cand_type = int(row[0]), row[1], row[2] or "", row[3]
        if cand_id in exclude_ids:
            continue
        cand_day = cand_date if isinstance(cand_date, date) else date.fromisoformat(str(cand_date)[:10])
        rule = reconcile_rule(trans_date, description, trans_type, cand_date, cand_desc, cand_type)
        if rule is None:
            cand_is_bank = is_bank_statement_description(cand_desc)
            cand_is_alert = is_alert_description(cand_desc)
            if (
                cand_day == incoming_day
                and (in_is_bank != cand_is_bank)
                and not in_is_alert
                and not cand_is_alert
                and (cand_type == incoming_type or "TRANSFER" in (cand_type, incoming_type))
            ):
                same_day_excel_bank.append(
                    ReconcileMatch(
                        cand_id,
                        cand_desc,
                        cand_type,
                        row[4] or "",
                        bool(row[5]),
                        MATCH_EXCEL_AND_STATEMENT,
                        tuple(source for source in (row[6] or []) if source),
                    )
                )
            continue
        key = (-_MATCH_STRENGTH[rule], abs((cand_day - incoming_day).days), cand_type != incoming_type, cand_id)
        if best_key is None or key < best_key:
            best_key = key
            best = ReconcileMatch(cand_id, cand_desc, cand_type, row[4] or "", bool(row[5]), rule,
                                  tuple(source for source in (row[6] or []) if source))
    if best is None and len(same_day_excel_bank) == 1:
        return same_day_excel_bank[0]
    return best


def _source_label(gcs_uri: str) -> str:
    """Where an existing row came from, for upload messages: a file name, Gmail alerts or by hand."""
    if not gcs_uri:
        return "entered by hand"
    if gcs_uri.startswith("gmail://"):
        return "Gmail alerts"
    return gcs_uri.rstrip("/").rsplit("/", 1)[-1]


class DatabaseManager:
    _shared_pool = None
    _shared_connector = None
    _schema_initialized = False
    _security_hardened = False

    def __init__(self):
        if DatabaseManager._shared_pool is not None:
            self.pool = DatabaseManager._shared_pool
            self.connector = DatabaseManager._shared_connector
            self._ensure_schema()
            return

        self.connector = None
        if config.DATABASE_URL:
            # Explicit full URL override (e.g. a local test database).
            db_url = config.DATABASE_URL
            if db_url.startswith("postgresql://"):
                db_url = "postgresql+pg8000://" + db_url[len("postgresql://"):]
            elif db_url.startswith("postgres://"):
                db_url = "postgresql+pg8000://" + db_url[len("postgres://"):]
            self.pool = sqlalchemy.create_engine(db_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
        elif config.INSTANCE_CONNECTION_NAME:
            # Legacy Google Cloud SQL instance ("project:region:instance").
            creds = get_cloud_sql_credentials()
            self.connector = Connector(credentials=creds) if creds else Connector()
            self.pool = self._init_pool()
        else:
            # Default: Supabase transaction pooler. Non-secret settings come from config;
            # the password comes from Secret Manager (or the DB_PASS env var on Cloud Run).
            from sqlalchemy.engine import URL
            url = URL.create(
                drivername="postgresql+pg8000",
                username=f"{DB_USER}.{config.SUPABASE_PROJECT_REF}",
                password=config.get_db_password(),
                host=config.SUPABASE_POOLER_HOST,
                port=config.SUPABASE_POOLER_PORT,
                database=DB_NAME or "postgres"
            )
            self.pool = sqlalchemy.create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)

        DatabaseManager._shared_pool = self.pool
        DatabaseManager._shared_connector = self.connector
        self._ensure_schema()

    def _ensure_schema(self):
        """Ensures required tables and views exist on the connected database.

        Creates the full schema on an empty database (Phase 1: the demo database is bootstrapped
        this way), and is a no-op for tables that already exist. The base table definitions
        mirror the personal database exactly (constraint names included).
        """
        if DatabaseManager._schema_initialized:
            return
        try:
            with self.pool.begin() as conn:
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS categories (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(100) NOT NULL UNIQUE
                    );
                """))
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id SERIAL PRIMARY KEY,
                        transaction_date DATE NOT NULL,
                        amount NUMERIC(12,2) NOT NULL,
                        description TEXT NOT NULL,
                        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                        transaction_type VARCHAR(20) DEFAULT 'EXPENSE',
                        ai_confidence_score DOUBLE PRECISION DEFAULT 100.0,
                        is_self_transfer BOOLEAN DEFAULT FALSE,
                        row_signature VARCHAR(64) NOT NULL UNIQUE,
                        requires_human_review BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                        user_locked BOOLEAN NOT NULL DEFAULT FALSE
                    );
                """))
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS assets (
                        id SERIAL PRIMARY KEY,
                        transaction_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
                        asset_type VARCHAR(100),
                        gcs_uri TEXT,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                # Phase 1: budgets move from server memory into the database (Cloud Run
                # instances come and go), the demo's daily AI counters, and small key/value
                # facts about the database itself (e.g. the 'environment = demo' marker that
                # the nightly demo reset requires before it wipes anything).
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS budgets (
                        category VARCHAR(100) PRIMARY KEY,
                        monthly_budget NUMERIC(12,2) NOT NULL CHECK (monthly_budget >= 0),
                        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS ai_usage (
                        day DATE NOT NULL,
                        feature VARCHAR(32) NOT NULL,
                        count INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (day, feature)
                    );
                """))
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS app_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                # Seed the starting budgets once per database (the values that used to be
                # hardcoded in api.py). Later edits or deletions are never overwritten.
                first_time = conn.execute(sqlalchemy.text("""
                    INSERT INTO app_meta (key, value) VALUES ('budgets_seeded', 'true')
                    ON CONFLICT (key) DO NOTHING
                    RETURNING key
                """)).fetchone()
                if first_time:
                    for category, amount in DEFAULT_BUDGETS.items():
                        conn.execute(sqlalchemy.text("""
                            INSERT INTO budgets (category, monthly_budget) VALUES (:cat, :amt)
                            ON CONFLICT (category) DO NOTHING
                        """), {"cat": category, "amt": amount})
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS ingested_files (
                        file_hash VARCHAR(64) PRIMARY KEY,
                        file_name TEXT NOT NULL,
                        rows_imported INTEGER NOT NULL,
                        imported_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS gmail_sync_settings (
                        id INTEGER PRIMARY KEY DEFAULT 1,
                        email_address VARCHAR(255) NOT NULL,
                        app_password VARCHAR(255) NOT NULL,
                        pdf_password VARCHAR(255) DEFAULT '',
                        allowed_senders TEXT DEFAULT '',
                        auto_sync_enabled BOOLEAN DEFAULT TRUE,
                        sync_interval_hours INTEGER DEFAULT 24,
                        last_synced_at TIMESTAMPTZ,
                        last_sync_status TEXT,
                        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                conn.execute(sqlalchemy.text("""
                    ALTER TABLE gmail_sync_settings
                    ADD COLUMN IF NOT EXISTS allowed_senders TEXT DEFAULT '';
                """))
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS gmail_processed_emails (
                        message_id VARCHAR(255) PRIMARY KEY,
                        subject TEXT,
                        sender TEXT,
                        email_type VARCHAR(32) DEFAULT 'ALERT',
                        transactions_extracted INTEGER DEFAULT 0,
                        processed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS exported_statement_pdfs (
                        id SERIAL PRIMARY KEY,
                        message_id VARCHAR(255) NOT NULL,
                        month_key VARCHAR(16) NOT NULL,
                        bank_name VARCHAR(64) NOT NULL,
                        file_name TEXT NOT NULL,
                        file_path TEXT UNIQUE NOT NULL,
                        email_subject TEXT,
                        email_date DATE,
                        is_encrypted BOOLEAN DEFAULT FALSE,
                        sync_status VARCHAR(64) DEFAULT 'EXPORTED',
                        transactions_imported INTEGER DEFAULT 0,
                        exported_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                # Rows the user has edited or confirmed. Imports and reconciliation must never
                # overwrite them (Phase 0 fix: "I already fixed this!").
                conn.execute(sqlalchemy.text("""
                    ALTER TABLE transactions
                    ADD COLUMN IF NOT EXISTS user_locked BOOLEAN NOT NULL DEFAULT FALSE;
                """))
                # Clarification Agent memory: stores user-resolved payee/narration -> category rules
                # (e.g., "upi/rahul" -> "Rent") so future imports auto-categorize without asking again.
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS learned_rules (
                        id SERIAL PRIMARY KEY,
                        pattern TEXT NOT NULL UNIQUE,
                        category VARCHAR(100) NOT NULL,
                        transaction_type VARCHAR(20) NOT NULL DEFAULT 'EXPENSE',
                        is_self_transfer BOOLEAN NOT NULL DEFAULT FALSE,
                        source VARCHAR(64) NOT NULL DEFAULT 'clarification_agent',
                        hit_count INTEGER NOT NULL DEFAULT 0,
                        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                # Govern Pillar audit trail: immutable log of agent decisions, tool calls, and human approvals.
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS agent_audit_log (
                        id SERIAL PRIMARY KEY,
                        trace_id VARCHAR(64),
                        agent_id VARCHAR(64) NOT NULL,
                        spiffe_id TEXT,
                        action VARCHAR(120) NOT NULL,
                        status VARCHAR(32) NOT NULL DEFAULT 'OK',
                        details TEXT,
                        requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
                        approved_by VARCHAR(100),
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                # Agent Platform A2A Agent Registry: stores deployed Agent Cards and Google Cloud Agent Registry URNs.
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS agent_registry (
                        agent_id VARCHAR(64) PRIMARY KEY,
                        name VARCHAR(128) NOT NULL,
                        version VARCHAR(32) NOT NULL DEFAULT '3.0.0',
                        endpoint_url TEXT NOT NULL,
                        well_known_url TEXT NOT NULL,
                        spiffe_id TEXT,
                        gcp_agent_urn TEXT,
                        status VARCHAR(32) NOT NULL DEFAULT 'ONLINE',
                        card_json TEXT NOT NULL,
                        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                # Reconciled Monthly Bank Balances (DEC-054): authoritative statement-reconciled
                # Month Start (`opening_balance`) and Month End (`closing_balance`) across
                # Kotak Mahindra Bank (SA + ActivMoney FD) and HDFC Bank (SA + Sweep-In FD).
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS monthly_bank_balances (
                        month_key VARCHAR(7) PRIMARY KEY,
                        kotak_sa_open NUMERIC(12,2) NOT NULL DEFAULT 0,
                        kotak_sa_close NUMERIC(12,2) NOT NULL DEFAULT 0,
                        kotak_am_open NUMERIC(12,2) NOT NULL DEFAULT 0,
                        kotak_am_close NUMERIC(12,2) NOT NULL DEFAULT 0,
                        hdfc_sa_open NUMERIC(12,2) NOT NULL DEFAULT 0,
                        hdfc_sa_close NUMERIC(12,2) NOT NULL DEFAULT 0,
                        hdfc_fd_open NUMERIC(12,2) NOT NULL DEFAULT 0,
                        hdfc_fd_close NUMERIC(12,2) NOT NULL DEFAULT 0,
                        opening_balance NUMERIC(12,2) NOT NULL,
                        closing_balance NUMERIC(12,2) NOT NULL,
                        source_note TEXT,
                        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                if not getattr(config, "DEMO_MODE", False):
                    self._seed_reconciled_monthly_balances(conn)
                # security_invoker: queries through the view use the caller's permissions, so
                # row-level security on the underlying tables also applies to the view.
                conn.execute(sqlalchemy.text("""
                    CREATE OR REPLACE VIEW financial_summary WITH (security_invoker = true) AS
                    SELECT 
                        COALESCE(c.name, 'Uncategorized') AS category,
                        t.transaction_type,
                        COUNT(t.id) AS transaction_count,
                        ROUND(SUM(t.amount)::numeric, 2) AS total_amount,
                        ROUND(AVG(t.amount)::numeric, 2) AS avg_amount
                    FROM transactions t
                    LEFT JOIN categories c ON t.category_id = c.id
                    GROUP BY COALESCE(c.name, 'Uncategorized'), t.transaction_type
                    ORDER BY t.transaction_type, total_amount DESC;
                """))
            DatabaseManager._schema_initialized = True
            logger.info("Database schema verified (core ledger, budgets, ai_usage, app_meta, Gmail sync tables, financial_summary).")
        except Exception as e:
            logger.warning(f"Could not verify schema: {e}")
        self._apply_security_hardening()

    def _apply_security_hardening(self):
        """Locks the public schema down so only the backend can reach the data (idempotent).

        - Turns on row-level security (RLS) for every table in `public`. With no policies,
          Supabase's public `anon` / `authenticated` roles can't read or change any row. The
          backend connects as the table owner (`postgres`), which bypasses RLS.
        - Revokes all table, sequence and function privileges from `anon` / `authenticated`,
          including the defaults that Supabase grants on newly created objects.
        Runs on startup so any database this app connects to (e.g. the Phase 1 demo database)
        is secured automatically. The role statements are skipped on non-Supabase Postgres.
        """
        if DatabaseManager._security_hardened:
            return
        try:
            with self.pool.begin() as conn:
                conn.execute(sqlalchemy.text(SECURITY_HARDENING_SQL))
            DatabaseManager._security_hardened = True
            logger.info("Database security hardening verified (RLS on, public API roles revoked).")
        except Exception as e:
            logger.error(f"Database security hardening FAILED; data may be exposed: {e}")

    def _init_pool(self):
        db_password = config.get_db_password()

        def getconn():
            return self.connector.connect(
                config.INSTANCE_CONNECTION_NAME, "pg8000",
                user=DB_USER, password=db_password, db=DB_NAME
            )

        return sqlalchemy.create_engine("postgresql+pg8000://", creator=getconn)

    def find_ingested_file(self, file_hash: str):
        """(file_name, imported_at, rows_imported) if this exact file was imported before, else None."""
        with self.pool.connect() as conn:
            row = conn.execute(sqlalchemy.text(
                "SELECT file_name, imported_at, rows_imported FROM ingested_files WHERE file_hash = :hash"
            ), {"hash": file_hash}).fetchone()
        return tuple(row) if row else None

    def save_transaction(self, data, gcs_uri: str) -> int:
        """Saves one transaction (a receipt) exactly like a batch of one: if the ledger already
        has it (e.g. the statement line for the same purchase), that row is updated instead."""
        return self.save_transactions_batch([data], gcs_uri)[0]

    def save_transactions_batch(self, data_list: list, gcs_uri: str, file_hash: str = None,
                                file_name: str = None) -> list[int]:
        """Saves a batch of transactions atomically in a single connection transaction.

        Each incoming row is first matched against the ledger (`find_reconcilable_transaction`):
        a row that is already there (a Gmail alert, an overlapping statement, a re-import, the
        other side of a transfer between your accounts) is updated in place instead of being
        duplicated. Its labels change only with proof (`merged_labels`): a disagreement on the type
        keeps the row as it is and marks it for review. Rows the user edited or confirmed
        (user_locked) keep their labels and text. The copy's file is recorded on the matched row
        (assets), so a row knows every record it came from. Rows of one batch never merge.

        With `file_hash`, the file is claimed in `ingested_files` inside the same transaction, so
        the same file imported twice (even by two syncs running at once) raises
        FileAlreadyImported and adds nothing (DEC-034).

        Returns one id per incoming row. `self.last_batch_summary` counts what happened: rows,
        added, already (incoming rows that were already in the ledger), repeated_in_file,
        conflicts (already there, but labelled with another type: marked for review), the match
        rules used, and where those existing rows came from (`sources`).
        """
        summary = {"rows": len(data_list), "added": 0, "already": 0, "repeated_in_file": 0,
                   "conflicts": 0, "rules": {}, "sources": []}
        self.last_batch_summary = summary
        if not data_list:
            return []

        try:
            with self.pool.begin() as db_conn:
                if file_hash:
                    # Concurrent imports of the same file wait on this key; the later one finds
                    # it taken once the first commits, and adds nothing.
                    claimed = db_conn.execute(sqlalchemy.text("""
                        INSERT INTO ingested_files (file_hash, file_name, rows_imported)
                        VALUES (:hash, :name, :count)
                        ON CONFLICT (file_hash) DO NOTHING
                        RETURNING file_hash
                    """), {"hash": file_hash, "name": file_name or gcs_uri, "count": len(data_list)}).fetchone()
                    if not claimed:
                        previous = db_conn.execute(sqlalchemy.text(
                            "SELECT file_name, imported_at, rows_imported FROM ingested_files WHERE file_hash = :hash"
                        ), {"hash": file_hash}).fetchone()
                        raise FileAlreadyImported(*(tuple(previous) if previous else (file_name or gcs_uri,)))

                cat_query = sqlalchemy.text("SELECT id, name FROM categories")
                categories = {
                    row[1].strip().lower(): row[0]
                    for row in db_conn.execute(cat_query).fetchall()
                }
                other_id = categories.get("other", 5)

                mime_type, _ = mimetypes.guess_type(gcs_uri)
                if not mime_type:
                    mime_type = "application/octet-stream"

                # Rows the user edited or confirmed (user_locked) are never overwritten by imports.
                # An identical row keeps its specific category when the incoming copy only says
                # "Other" (:generic). xmax = 0 tells a new row from an existing one.
                trans_query = sqlalchemy.text("""
                    INSERT INTO transactions (
                        transaction_date, amount, description, category_id, transaction_type, ai_confidence_score,
                        is_self_transfer, row_signature, requires_human_review
                    )
                    VALUES (:date, :amount, :desc, :cat_id, :type, :confidence, :is_transfer, :sig, :review)
                    ON CONFLICT (row_signature) DO UPDATE
                    SET category_id = CASE WHEN CAST(:generic AS BOOLEAN) AND transactions.category_id IS NOT NULL
                                           THEN transactions.category_id ELSE EXCLUDED.category_id END,
                        transaction_type = EXCLUDED.transaction_type,
                        ai_confidence_score = CASE WHEN CAST(:generic AS BOOLEAN) AND transactions.category_id IS NOT NULL
                                                   THEN transactions.ai_confidence_score ELSE EXCLUDED.ai_confidence_score END,
                        is_self_transfer = EXCLUDED.is_self_transfer,
                        requires_human_review = CASE WHEN CAST(:generic AS BOOLEAN) AND transactions.category_id IS NOT NULL
                                                     THEN transactions.requires_human_review ELSE EXCLUDED.requires_human_review END
                    WHERE NOT transactions.user_locked
                    RETURNING id, (xmax = 0) AS inserted
                """)

                # Confidence and the review flag change only when the labels do.
                reconcile_query = sqlalchemy.text("""
                    UPDATE transactions
                    SET description = :desc,
                        category_id = :cat_id,
                        transaction_type = :type,
                        is_self_transfer = :is_transfer,
                        ai_confidence_score = COALESCE(CAST(:confidence AS DOUBLE PRECISION), ai_confidence_score),
                        requires_human_review = COALESCE(CAST(:review AS BOOLEAN), requires_human_review)
                    WHERE id = :id AND NOT user_locked
                """)
                reconcile_excel_query = sqlalchemy.text("""
                    UPDATE transactions
                    SET description = :desc,
                        category_id = :cat_id,
                        transaction_type = :type,
                        is_self_transfer = :is_transfer,
                        ai_confidence_score = 100.0,
                        requires_human_review = FALSE,
                        user_locked = TRUE
                    WHERE id = :id
                """)
                is_excel_import = str(gcs_uri or "").lower().endswith((".xlsx", ".xls"))

                asset_query = sqlalchemy.text("""
                    INSERT INTO assets (transaction_id, asset_type, gcs_uri)
                    VALUES (:trans_id, :a_type, :uri)
                """)
                # A recognised copy's file is recorded on the row too (once), so the row keeps
                # every record it came from, e.g. which banks saw a transfer.
                matched_asset_query = sqlalchemy.text("""
                    INSERT INTO assets (transaction_id, asset_type, gcs_uri)
                    SELECT :trans_id, :a_type, :uri
                    WHERE NOT EXISTS (SELECT 1 FROM assets WHERE transaction_id = :trans_id AND gcs_uri = :uri)
                """)

                saved_ids = []
                used_ids = set()       # rows this batch matched or created: never matched again
                batch_new_ids = set()
                already_ids = []
                rules = Counter()

                for data in data_list:
                    raw_desc = (getattr(data, "description", "") or "").strip()
                    if re.match(
                        r"^(prev(?:ious|ous)?\s+balance|opening\s+balance|initial\s+balance|month\s+end\s+balance|closing\s+balance)",
                        raw_desc,
                        re.IGNORECASE,
                    ):
                        try:
                            data.category = "Transfer"
                            data.transaction_type = "TRANSFER"
                            data.is_self_transfer = True
                            data.confidence = 100.0
                            data.requires_human_review = False
                        except Exception:
                            pass

                    matched_rule = self.match_learned_rule(getattr(data, "description", ""))
                    if matched_rule:
                        try:
                            data.category = matched_rule["category"]
                            data.transaction_type = matched_rule["transaction_type"]
                            data.is_self_transfer = bool(matched_rule.get("is_self_transfer", False))
                            data.confidence = max(float(getattr(data, "confidence", 99.0)), 99.0)
                            data.requires_human_review = False
                        except Exception:
                            pass

                    trans_type = _norm_type(getattr(data, "transaction_type", "EXPENSE"))
                    is_transfer = getattr(data, "is_self_transfer", False) or (trans_type == "TRANSFER") or ((getattr(data, "category", "") or "").strip().lower() == "transfer")
                    if is_transfer:
                        trans_type = "TRANSFER"
                        cat_key = "transfer"
                    else:
                        cat_key = (getattr(data, "category", "") or "").strip().lower()
                    if matched_rule and cat_key and cat_key not in categories:
                        new_cat_id = db_conn.execute(
                            sqlalchemy.text(
                                "INSERT INTO categories (name) VALUES (:name) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
                            ),
                            {"name": matched_rule["category"].strip()},
                        ).scalar()
                        if new_cat_id:
                            categories[cat_key] = new_cat_id
                    known_cat = cat_key if cat_key in categories else "other"

                    row_sig = compute_row_signature(
                        data.transaction_date, data.amount, data.description, trans_type
                    )
                    category_id = categories.get(cat_key, other_id)
                    confidence_val = getattr(data, "confidence", 100.0)
                    needs_review = False if (is_transfer or matched_rule) else (getattr(data, 'requires_human_review', False) or (confidence_val < 85.0) or (cat_key not in categories))

                    match = find_reconcilable_transaction(
                        db_conn,
                        data.transaction_date,
                        data.amount,
                        data.description,
                        trans_type,
                        exclude_ids=used_ids,
                    )

                    if match is not None:
                        used_ids.add(match.id)
                        already_ids.append(match.id)
                        rules[match.rule] += 1
                        # The same reference in two different banks' records: money moved between
                        # your own accounts. That is the only case where the type may change.
                        bank_in = account_hint(gcs_uri, data.description)
                        banks_existing = account_hints(match.sources, match.description)
                        between_accounts = (match.rule == MATCH_SAME_REFERENCE and bank_in is not None
                                            and bool(banks_existing - {bank_in}))
                        excel_over_bank = is_excel_import and is_bank_statement_description(match.description)
                        bank_over_excel = (
                            is_bank_statement_description(data.description)
                            and not is_bank_statement_description(match.description)
                            and not is_alert_description(match.description)
                        )
                        if excel_over_bank and not between_accounts:
                            final_type = trans_type
                            final_cat = known_cat if not is_generic_category(known_cat) else (match.category or known_cat)
                            final_transfer = is_transfer or (final_type == "TRANSFER")
                            conflict = False
                        elif bank_over_excel and not between_accounts and not is_generic_category(match.category):
                            final_type = match.transaction_type
                            final_cat = match.category
                            final_transfer = final_type == "TRANSFER"
                            conflict = False
                        else:
                            final_type, final_cat, final_transfer, conflict = merged_labels(
                                match.transaction_type, match.category, trans_type, known_cat, between_accounts
                            )
                        if conflict:
                            summary["conflicts"] += 1
                        unchanged = (final_type == match.transaction_type
                                     and final_cat.strip().lower() == match.category.strip().lower())
                        if conflict:
                            review = True  # the copies disagree on the type: ask instead of guessing
                        elif excel_over_bank:
                            review = False
                        elif unchanged:
                            review = None
                        else:
                            review = False if final_transfer else needs_review
                        if excel_over_bank:
                            best_desc = combine_excel_and_bank_description(data.description, match.description)
                        elif bank_over_excel:
                            best_desc = combine_excel_and_bank_description(match.description, data.description)
                        elif is_alert_description(match.description) and not is_alert_description(data.description):
                            best_desc = data.description
                        else:
                            best_desc = match.description
                        db_conn.execute(reconcile_excel_query if excel_over_bank else reconcile_query, {
                            "desc": best_desc,
                            "cat_id": categories.get(final_cat.strip().lower(), other_id),
                            "type": final_type,
                            "is_transfer": final_transfer,
                            "confidence": None if unchanged else confidence_val,
                            "review": review,
                            "id": match.id,
                        })
                        logger.info(
                            f"Already in the ledger as #{match.id} ({match.rule}"
                            f"{', between your accounts' if between_accounts else ''}"
                            f"{', type differs: marked for review' if conflict else ''}), not added again: "
                            f"'{best_desc}' (₹{data.amount})"
                        )
                        db_conn.execute(matched_asset_query, {"trans_id": match.id, "a_type": mime_type, "uri": gcs_uri})
                        saved_ids.append(match.id)
                        continue

                    returned = db_conn.execute(trans_query, {
                        "date": data.transaction_date,
                        "amount": data.amount,
                        "desc": data.description,
                        "cat_id": category_id,
                        "type": trans_type,
                        "confidence": confidence_val,
                        "is_transfer": is_transfer,
                        "sig": row_sig,
                        "review": needs_review,
                        "generic": is_generic_category(known_cat),
                    }).fetchone()
                    if returned is None:
                        # The identical row is user_locked, so the update was skipped and nothing came back.
                        new_id, inserted = _existing_id_for_signature(db_conn, row_sig), False
                    else:
                        new_id, inserted = int(returned[0]), bool(returned[1])
                    if inserted:
                        summary["added"] += 1
                        batch_new_ids.add(new_id)
                    elif new_id in batch_new_ids:
                        summary["repeated_in_file"] += 1  # same date, amount, text and type as an earlier line
                    else:
                        already_ids.append(new_id)
                        rules["identical row"] += 1
                    saved_ids.append(new_id)
                    used_ids.add(new_id)

                    db_conn.execute(asset_query, {
                        "trans_id": new_id,
                        "a_type": mime_type,
                        "uri": gcs_uri
                    })

                if already_ids:
                    # Where each recognised row came from before this import: its first file other
                    # than this one. A row with no earlier file (entered by hand, or written by an
                    # agent or script) must not be reported as coming from the file being imported,
                    # which its asset row above now names (DEC-060).
                    first_files = dict(db_conn.execute(sqlalchemy.text("""
                        SELECT DISTINCT ON (transaction_id) transaction_id, gcs_uri
                        FROM assets
                        WHERE transaction_id = ANY(:ids) AND gcs_uri IS DISTINCT FROM CAST(:current AS TEXT)
                        ORDER BY transaction_id, id
                    """), {"ids": sorted(set(already_ids)), "current": gcs_uri}).fetchall())
                    summary["sources"] = Counter(
                        _source_label(first_files.get(tx_id) or "") for tx_id in already_ids
                    ).most_common()
                summary["already"] = len(already_ids)
                summary["rules"] = dict(rules)

                logger.info(
                    f"Atomically committed batch of {len(saved_ids)} transactions: {summary['added']} new, "
                    f"{summary['already']} already in the ledger {summary['rules']} "
                    f"({summary['conflicts']} labelled differently, marked for review), "
                    f"{summary['repeated_in_file']} repeated within the file."
                )
                return saved_ids

        except SQLAlchemyError as e:
            logger.error(f"Database batch error occurred: {e}")
            raise DatabaseError("Failed to save transaction batch to Cloud SQL") from e

    _memory_learned_rules: dict[str, dict] = {}
    _memory_audit_logs: list[dict] = []

    def save_learned_rule(
        self,
        pattern: str | None = None,
        category: str = "Other",
        transaction_type: str = "EXPENSE",
        is_self_transfer: bool = False,
        source: str = "clarification_agent",
        payee_pattern: str | None = None,
    ) -> dict:
        """Persists a user-resolved merchant/payee pattern to `learned_rules` for future auto-categorization."""
        from datetime import datetime, timezone

        raw_pattern = pattern or payee_pattern or ""
        norm_pattern = raw_pattern.strip().lower()
        if not norm_pattern or len(norm_pattern) < 2:
            raise ValueError("Pattern must be at least 2 characters.")
        clean_cat = (category or "Other").strip()
        is_transfer = bool(is_self_transfer) or clean_cat.lower() == "transfer" or (transaction_type or "").upper() == "TRANSFER"
        norm_type = "TRANSFER" if is_transfer else (transaction_type or "EXPENSE").strip().upper()
        if is_transfer:
            clean_cat = "Transfer"

        rule_record = {
            "pattern": norm_pattern,
            "payee_pattern": norm_pattern.upper(),
            "category": clean_cat,
            "transaction_type": norm_type,
            "is_self_transfer": is_transfer,
            "source": source,
            "hit_count": DatabaseManager._memory_learned_rules.get(norm_pattern, {}).get("hit_count", 0),
            "retroactively_updated": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        DatabaseManager._memory_learned_rules[norm_pattern] = rule_record

        try:
            with self.pool.begin() as conn:
                conn.execute(sqlalchemy.text("""
                    INSERT INTO learned_rules (pattern, category, transaction_type, is_self_transfer, source, updated_at)
                    VALUES (:pat, :cat, :ttype, :istrans, :src, CURRENT_TIMESTAMP)
                    ON CONFLICT (pattern) DO UPDATE
                    SET category = EXCLUDED.category,
                        transaction_type = EXCLUDED.transaction_type,
                        is_self_transfer = EXCLUDED.is_self_transfer,
                        source = EXCLUDED.source,
                        updated_at = CURRENT_TIMESTAMP
                """), {
                    "pat": norm_pattern,
                    "cat": clean_cat,
                    "ttype": norm_type,
                    "istrans": is_transfer,
                    "src": source,
                })

                # Ensure target category exists in categories table
                cat_row = conn.execute(
                    sqlalchemy.text("SELECT id FROM categories WHERE LOWER(name) = :name LIMIT 1"),
                    {"name": clean_cat.lower()},
                ).fetchone()
                if cat_row:
                    target_cat_id = cat_row[0]
                else:
                    target_cat_id = conn.execute(
                        sqlalchemy.text(
                            "INSERT INTO categories (name) VALUES (:name) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
                        ),
                        {"name": clean_cat},
                    ).scalar()

                # Retroactively apply learned rule to existing unlocked transactions matching pattern
                if target_cat_id is not None:
                    retro_res = conn.execute(sqlalchemy.text("""
                        UPDATE transactions
                        SET category_id = :cat_id,
                            transaction_type = :ttype,
                            is_self_transfer = :istrans,
                            requires_human_review = FALSE,
                            ai_confidence_score = GREATEST(COALESCE(ai_confidence_score, 0), 99.0)
                        WHERE NOT COALESCE(user_locked, FALSE)
                          AND LOWER(description) LIKE :pat_like
                          AND (
                              requires_human_review = TRUE
                              OR category_id IS NULL
                              OR category_id IN (
                                  SELECT id FROM categories WHERE LOWER(name) IN ('other', 'uncategorized')
                              )
                          )
                    """), {
                        "cat_id": target_cat_id,
                        "ttype": norm_type,
                        "istrans": is_transfer,
                        "pat_like": f"%{norm_pattern}%",
                    })
                    rule_record["retroactively_updated"] = int(retro_res.rowcount or 0)
        except Exception as e:
            logger.warning(f"Database save_learned_rule fallback to in-memory store: {e}")

        return rule_record

    def get_learned_rules(self) -> list[dict]:
        """Returns all learned payee/narration categorization rules."""
        rules_by_pattern = dict(DatabaseManager._memory_learned_rules)
        try:
            with self.pool.connect() as conn:
                rows = conn.execute(sqlalchemy.text("""
                    SELECT pattern, category, transaction_type, is_self_transfer, source, hit_count, updated_at
                    FROM learned_rules
                    ORDER BY hit_count DESC, updated_at DESC
                """)).fetchall()
                for r in rows:
                    rules_by_pattern[r[0]] = {
                        "pattern": r[0],
                        "payee_pattern": str(r[0]).upper(),
                        "category": r[1],
                        "transaction_type": r[2],
                        "is_self_transfer": bool(r[3]),
                        "source": r[4],
                        "hit_count": int(r[5] or 0),
                        "updated_at": str(r[6]) if r[6] else None,
                    }
        except Exception as e:
            logger.debug(f"get_learned_rules using in-memory rules: {e}")
        return list(rules_by_pattern.values())

    def match_learned_rule(
        self,
        description: str,
        rules: list[dict] | None = None,
        increment_hit: bool = True,
    ) -> dict | None:
        """Matches a transaction description against learned user rules (longest pattern match first)."""
        desc_lower = (description or "").strip().lower()
        if not desc_lower:
            return None
        active_rules = rules if rules is not None else self.get_learned_rules()
        # Sort by pattern length descending so specific patterns ("upi/rahul") match before shorter tokens ("rahul")
        for rule in sorted(active_rules, key=lambda x: len(x.get("pattern", "")), reverse=True):
            pat = rule.get("pattern", "")
            if pat and pat in desc_lower:
                # Increment hit count
                if pat in DatabaseManager._memory_learned_rules:
                    DatabaseManager._memory_learned_rules[pat]["hit_count"] = (
                        DatabaseManager._memory_learned_rules[pat].get("hit_count", 0) + 1
                    )
                if increment_hit:
                    try:
                        with self.pool.begin() as conn:
                            conn.execute(
                                sqlalchemy.text("UPDATE learned_rules SET hit_count = hit_count + 1 WHERE pattern = :pat"),
                                {"pat": pat},
                            )
                    except Exception:
                        pass
                return rule
        return None

    @classmethod
    def format_learned_rules_prompt(cls, limit: int = 30) -> str:
        """Formats user-clarified categorization rules as few-shot prompt instructions for Gemini extractors."""
        rules: list[dict] = []
        try:
            if cls._shared_pool is not None:
                rules = cls().get_learned_rules()
            else:
                rules = list(cls._memory_learned_rules.values())
        except Exception:
            rules = list(cls._memory_learned_rules.values())

        if not rules:
            return ""

        lines = [
            "\nUSER-VERIFIED CATEGORIZATION MEMORY (LEARNED RULES):",
            "The user has previously clarified the following payee/narration patterns. "
            "Whenever a transaction description matches or closely corresponds to one of these patterns, "
            "you MUST assign the specified category, type, and is_self_transfer, and set confidence to 99:",
        ]
        for r in rules[:limit]:
            pat = r.get("payee_pattern") or str(r.get("pattern", "")).upper()
            cat = r.get("category", "Other")
            ttype = r.get("transaction_type", "EXPENSE")
            istrans = bool(r.get("is_self_transfer", False))
            lines.append(
                f'- Pattern "{pat}" -> category="{cat}", type="{ttype}", is_self_transfer={str(istrans).lower()}'
            )
        return "\n".join(lines) + "\n"

    def delete_learned_rule(self, payee_pattern: str) -> bool:
        """Removes a learned payee categorization rule by normalized pattern."""
        norm = (payee_pattern or "").strip().lower()
        if not norm:
            return False
        removed = DatabaseManager._memory_learned_rules.pop(norm, None) is not None
        try:
            with self.pool.begin() as conn:
                res = conn.execute(
                    sqlalchemy.text("DELETE FROM learned_rules WHERE LOWER(pattern) = :pat"),
                    {"pat": norm},
                )
                if res.rowcount and res.rowcount > 0:
                    removed = True
        except Exception as e:
            logger.debug(f"delete_learned_rule fallback to in-memory store: {e}")
        return removed

    def log_agent_audit(
        self,
        agent_id: str,
        action: str,
        status: str = "OK",
        details: dict | None = None,
        trace_id: str | None = None,
        spiffe_id: str | None = None,
        requires_approval: bool = False,
        approved_by: str | None = None,
    ) -> dict:
        """Records an immutable governance audit entry for an agent decision or tool execution."""
        import json
        from datetime import datetime, timezone

        details_json = json.dumps(details or {}, default=str)
        entry = {
            "trace_id": trace_id,
            "agent_id": agent_id,
            "spiffe_id": spiffe_id or f"spiffe://finance-agent.internal/sa/{agent_id}",
            "action": action,
            "status": status,
            "details": details or {},
            "requires_approval": bool(requires_approval),
            "approved_by": approved_by,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        DatabaseManager._memory_audit_logs.insert(0, entry)
        if len(DatabaseManager._memory_audit_logs) > 200:
            DatabaseManager._memory_audit_logs = DatabaseManager._memory_audit_logs[:200]

        try:
            with self.pool.begin() as conn:
                conn.execute(sqlalchemy.text("""
                    INSERT INTO agent_audit_log (
                        trace_id, agent_id, spiffe_id, action, status, details, requires_approval, approved_by
                    )
                    VALUES (:tid, :aid, :spiffe, :act, :st, :det, :req_app, :app_by)
                """), {
                    "tid": trace_id,
                    "aid": agent_id,
                    "spiffe": entry["spiffe_id"],
                    "act": action,
                    "st": status,
                    "det": details_json,
                    "req_app": bool(requires_approval),
                    "app_by": approved_by,
                })
        except Exception as e:
            logger.debug(f"log_agent_audit fallback to in-memory ring buffer: {e}")

        return entry

    def get_agent_audit_logs(self, limit: int = 50) -> list[dict]:
        """Returns recent governance audit trail entries."""
        import json

        safe_limit = min(max(1, int(limit)), 200)
        try:
            with self.pool.connect() as conn:
                rows = conn.execute(sqlalchemy.text("""
                    SELECT id, trace_id, agent_id, spiffe_id, action, status, details,
                           requires_approval, approved_by, created_at
                    FROM agent_audit_log
                    ORDER BY id DESC
                    LIMIT :lim
                """), {"lim": safe_limit}).fetchall()
                if rows:
                    out = []
                    for r in rows:
                        try:
                            det = json.loads(r[6]) if r[6] else {}
                        except Exception:
                            det = {"raw": r[6]}
                        out.append({
                            "id": r[0],
                            "trace_id": r[1],
                            "agent_id": r[2],
                            "spiffe_id": r[3],
                            "action": r[4],
                            "status": r[5],
                            "details": det,
                            "requires_approval": bool(r[7]),
                            "approved_by": r[8],
                            "created_at": str(r[9]) if r[9] else None,
                        })
                    return out
        except Exception as e:
            logger.debug(f"get_agent_audit_logs using in-memory buffer: {e}")
        return DatabaseManager._memory_audit_logs[:safe_limit]

    _memory_agent_cards: dict[str, dict] = {}

    def save_agent_card(self, card_dict: dict) -> dict:
        """Persists an A2A Agent Card manifest and its Google Cloud Agent Registry URN to `agent_registry`."""
        import json

        agent_id = (card_dict.get("agent_id") or "").strip()
        if not agent_id:
            raise ValueError("Agent Card must include agent_id.")
        DatabaseManager._memory_agent_cards[agent_id] = dict(card_dict)

        auth_info = card_dict.get("authentication") or {}
        spiffe_id = auth_info.get("spiffe_id") or card_dict.get("spiffe_id") or ""
        gcp_urn = card_dict.get("gcp_agent_urn") or ""
        card_json = json.dumps(card_dict, default=str)

        try:
            with self.pool.begin() as conn:
                conn.execute(sqlalchemy.text("""
                    INSERT INTO agent_registry (
                        agent_id, name, version, endpoint_url, well_known_url,
                        spiffe_id, gcp_agent_urn, status, card_json, updated_at
                    )
                    VALUES (
                        :aid, :nm, :ver, :eurl, :wkurl, :spiffe, :gcp_urn, :st, :cjson, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (agent_id) DO UPDATE
                    SET name = EXCLUDED.name,
                        version = EXCLUDED.version,
                        endpoint_url = EXCLUDED.endpoint_url,
                        well_known_url = EXCLUDED.well_known_url,
                        spiffe_id = EXCLUDED.spiffe_id,
                        gcp_agent_urn = EXCLUDED.gcp_agent_urn,
                        status = EXCLUDED.status,
                        card_json = EXCLUDED.card_json,
                        updated_at = CURRENT_TIMESTAMP
                """), {
                    "aid": agent_id,
                    "nm": card_dict.get("name", agent_id),
                    "ver": card_dict.get("version", "3.0.0"),
                    "eurl": card_dict.get("url", f"/a2a/agents/{agent_id}/tasks/send"),
                    "wkurl": card_dict.get("well_known_url", f"/a2a/agents/{agent_id}/.well-known/agent.json"),
                    "spiffe": spiffe_id,
                    "gcp_urn": gcp_urn,
                    "st": card_dict.get("status", "ONLINE"),
                    "cjson": card_json,
                })
        except Exception as e:
            logger.debug(f"save_agent_card fallback to in-memory registry: {e}")

        return card_dict

    def get_registered_agent_cards(self) -> list[dict]:
        """Returns all registered A2A Agent Cards from `agent_registry` (or in-memory store)."""
        import json

        cards = dict(DatabaseManager._memory_agent_cards)
        try:
            with self.pool.connect() as conn:
                rows = conn.execute(sqlalchemy.text("""
                    SELECT agent_id, card_json, gcp_agent_urn, status
                    FROM agent_registry
                    ORDER BY agent_id
                """)).fetchall()
                for r in rows:
                    try:
                        parsed = json.loads(r[1]) if r[1] else {}
                        if r[2]:
                            parsed["gcp_agent_urn"] = r[2]
                        if r[3]:
                            parsed["status"] = r[3]
                        cards[r[0]] = parsed
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"get_registered_agent_cards using in-memory registry: {e}")
        return list(cards.values())

    # Authoritative reconciled balance table (DEC-054): statement-reconciled month start and end
    # balances across Kotak (SA + ActivMoney FD) and HDFC (SA + Sweep-In FD). The figures are the
    # owner's, so they live in the git-ignored owner profile (owner_profile.py, DEC-062), not here.

    def _seed_reconciled_monthly_balances(self, conn):
        """Seeds/upserts the reconciled Kotak + HDFC monthly balances and syncs explicit balance rows.

        The rows come from the owner profile (DEC-062); without one, nothing is seeded.
        """
        for (
            m_key, k_sa_o, k_sa_c, k_am_o, k_am_c,
            h_sa_o, h_sa_c, h_fd_o, h_fd_c, open_bal, close_bal, note
        ) in owner_profile.reconciled_monthly_balances():
            conn.execute(sqlalchemy.text("""
                INSERT INTO monthly_bank_balances (
                    month_key,
                    kotak_sa_open, kotak_sa_close,
                    kotak_am_open, kotak_am_close,
                    hdfc_sa_open, hdfc_sa_close,
                    hdfc_fd_open, hdfc_fd_close,
                    opening_balance, closing_balance,
                    source_note, updated_at
                ) VALUES (
                    :mk, :kso, :ksc, :kao, :kac, :hso, :hsc, :hfo, :hfc, :ob, :cb, :note, CURRENT_TIMESTAMP
                )
                ON CONFLICT (month_key) DO UPDATE SET
                    kotak_sa_open = EXCLUDED.kotak_sa_open,
                    kotak_sa_close = EXCLUDED.kotak_sa_close,
                    kotak_am_open = EXCLUDED.kotak_am_open,
                    kotak_am_close = EXCLUDED.kotak_am_close,
                    hdfc_sa_open = EXCLUDED.hdfc_sa_open,
                    hdfc_sa_close = EXCLUDED.hdfc_sa_close,
                    hdfc_fd_open = EXCLUDED.hdfc_fd_open,
                    hdfc_fd_close = EXCLUDED.hdfc_fd_close,
                    opening_balance = EXCLUDED.opening_balance,
                    closing_balance = EXCLUDED.closing_balance,
                    source_note = EXCLUDED.source_note,
                    updated_at = CURRENT_TIMESTAMP
            """), {
                "mk": m_key,
                "kso": k_sa_o, "ksc": k_sa_c,
                "kao": k_am_o, "kac": k_am_c,
                "hso": h_sa_o, "hsc": h_sa_c,
                "hfo": h_fd_o, "hfc": h_fd_c,
                "ob": open_bal, "cb": close_bal,
                "note": note,
            })
            # Also sync any manual "Previous Balance" / "Month end balance" rows in `transactions`
            conn.execute(sqlalchemy.text(r"""
                UPDATE transactions
                SET amount = :ob,
                    transaction_type = 'TRANSFER',
                    is_self_transfer = TRUE
                WHERE TO_CHAR(transaction_date, 'YYYY-MM') = :mk
                  AND LOWER(TRIM(COALESCE(description, ''))) ~ '^(prev(ious|ous)?\s+balance|opening\s+balance|initial\s+balance)'
            """), {"ob": open_bal, "mk": m_key})
            conn.execute(sqlalchemy.text(r"""
                UPDATE transactions
                SET amount = :cb,
                    transaction_type = 'TRANSFER',
                    is_self_transfer = TRUE
                WHERE TO_CHAR(transaction_date, 'YYYY-MM') = :mk
                  AND LOWER(TRIM(COALESCE(description, ''))) ~ '^(month\s+end\s+balance|closing\s+balance)'
            """), {"cb": close_bal, "mk": m_key})

    def get_reconciled_monthly_balances(self) -> dict[str, dict]:
        """Returns a dictionary mapping `YYYY-MM` -> reconciled balance metadata."""
        if getattr(config, "DEMO_MODE", False):
            return {}
        result: dict[str, dict] = {}
        try:
            with self.pool.connect() as conn:
                rows = conn.execute(sqlalchemy.text("""
                    SELECT month_key,
                           kotak_sa_open, kotak_sa_close,
                           kotak_am_open, kotak_am_close,
                           hdfc_sa_open, hdfc_sa_close,
                           hdfc_fd_open, hdfc_fd_close,
                           opening_balance, closing_balance,
                           source_note
                    FROM monthly_bank_balances
                    ORDER BY month_key ASC
                """)).fetchall()
                for r in rows:
                    k_so, k_sc = float(r[1] or 0), float(r[2] or 0)
                    k_ao, k_ac = float(r[3] or 0), float(r[4] or 0)
                    h_so, h_sc = float(r[5] or 0), float(r[6] or 0)
                    h_fo, h_fc = float(r[7] or 0), float(r[8] or 0)
                    result[str(r[0])] = {
                        "month_key": str(r[0]),
                        "opening_balance": round(float(r[9]), 2),
                        "closing_balance": round(float(r[10]), 2),
                        "kotak_open": round(k_so + k_ao, 2),
                        "kotak_close": round(k_sc + k_ac, 2),
                        "kotak_sa_open": k_so,
                        "kotak_sa_close": k_sc,
                        "kotak_am_open": k_ao,
                        "kotak_am_close": k_ac,
                        "hdfc_open": round(h_so + h_fo, 2),
                        "hdfc_close": round(h_sc + h_fc, 2),
                        "hdfc_sa_open": h_so,
                        "hdfc_sa_close": h_sc,
                        "hdfc_fd_open": h_fo,
                        "hdfc_fd_close": h_fc,
                        "source_note": r[11] or "",
                        "reconciled": True,
                    }
        except Exception as e:
            logger.debug(f"get_reconciled_monthly_balances fallback: {e}")
        return result

    def close(self):
        """No-op for shared pool instances; individual connections are returned via context managers."""
        pass

    @classmethod
    def dispose_shared_pool(cls):
        """Explicitly dispose of the shared connection pool on application shutdown."""
        if cls._shared_pool:
            cls._shared_pool.dispose()
            cls._shared_pool = None
        if cls._shared_connector:
            try:
                cls._shared_connector.close()
            except Exception:
                pass
            cls._shared_connector = None