# app/gmail_sync.py
"""
Gmail Bank Alert & Monthly Statement Synchronization Engine
-----------------------------------------------------------
Connects read-only to Gmail via IMAP over SSL (imap.gmail.com:993) using a
revocable Google App Password. The App Password and bank-statement PDF passwords
are stored in Google Secret Manager (via `secret_store`), never in Supabase.

Features:
1. Bank Alert Ingestion (on demand, or Cloud Scheduler's hourly tick once the interval set on Bank Sync has passed):
   Reads transaction alert emails from Indian banks (HDFC, SBI, ICICI, Axis,
   Kotak, IDFC, PNB, BOB, Yes Bank, etc.), extracts date, amount, debit/credit
   type, merchant/VPA, and 12-digit UPI/IMPS/NEFT Reference (UTR/RRN).
2. Monthly Statement Attachment Ingestion:
   Detects PDF / Excel statement attachments in bank emails and routes them
   through the AI Statement Extractor.
3. Cross-Source Reconciliation:
   Works with DatabaseManager's 3-layer reconciliation engine, so a daily email
   alert and the matching end-of-month statement row are merged into one
   transaction instead of being counted twice.
"""

import os
import re
import shutil
import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
from datetime import datetime, date, timedelta
from html import unescape
import logging
from typing import Dict, Any, List, Optional, Tuple

import sqlalchemy

# app/ is the import root everywhere (Cloud Shell, tests and the Docker image), so modules are
# imported by their plain names. The old `app.`-prefixed imports crashed the container (DEC-025).
from database import DatabaseManager
from models import ReceiptData
import secret_store
import statement_store

logger = logging.getLogger(__name__)

# Credentials live in Google Secret Manager, never in Supabase (DEC-022 / DEC-023).
# Only non-secret settings (address, sender allowlist, schedule, status) stay in the
# gmail_sync_settings table.
GMAIL_APP_PASSWORD_SECRET = os.getenv("GMAIL_APP_PASSWORD_SECRET", "pfa-gmail-app-password")
# One or more statement PDF passwords, separated by commas, semicolons or new lines.
STATEMENT_PASSWORDS_SECRET = os.getenv("STATEMENT_PASSWORDS_SECRET", "pfa-statement-passwords")
MASKED_SECRET = "•••• •••• •••• ••••"


def _load_app_password(strict: bool = False) -> str:
    """Gmail App Password from Secret Manager ("" if not set). With strict, errors propagate."""
    try:
        value = secret_store.get_secret(
            GMAIL_APP_PASSWORD_SECRET, env_var="GMAIL_APP_PASSWORD", required=False
        )
    except Exception as exc:
        if strict:
            raise
        logger.warning(f"Could not read the Gmail App Password from Secret Manager: {exc}")
        return ""
    return (value or "").replace(" ", "").strip()


def _load_statement_passwords(strict: bool = False) -> str:
    """Statement PDF password list from Secret Manager ("" if not set)."""
    try:
        value = secret_store.get_secret(
            STATEMENT_PASSWORDS_SECRET, env_var="GMAIL_PDF_PASSWORD", required=False
        )
    except Exception as exc:
        if strict:
            raise
        logger.warning(f"Could not read statement passwords from Secret Manager: {exc}")
        return ""
    return (value or "").strip()

# High-precision Rule & Regex Classifier for Indian Banking Narrations
INDIAN_MERCHANT_CATEGORIES = [
    # Food & Groceries
    (r"(swiggy|zomato|zepto|blinkit|instamart|bigbasket|dunzo|mcdonald|domino|starbucks|chai point|cafe|restaurant|bakery|haldiram|pizza)", "Food", "EXPENSE"),
    # Transportation & Travel
    (r"(uber|ola|rapido|irCTC|indian railways|metro|fastag|shell|indian oil|hpcl|bpcl|petrol|fuel|makemytrip|cleartrip|indigo|air india)", "Transportation", "EXPENSE"),
    # Utilities & Bills
    (r"(bescom|tata power|adani elec|msedcl|airtel|jio|vodafone|vi |act fibernet|indane|bharat gas|hp gas|electricity|water board)", "Utilities", "EXPENSE"),
    # Health & Medical
    (r"(apollo|pharmeasy|1mg|tata 1mg|medplus|practo|max health|fortis|clinic|medical|hospital|pharmacy|diagnostics)", "Health/Medical", "EXPENSE"),
    # Personal & Shopping
    (r"(amazon|flipkart|myntra|ajio|nykaa|zara|h&m|decathlon|tata cliq|bookmyshow|pvr|inox|cult\.fit|urban company)", "Personal", "EXPENSE"),
    # Mutual Funds & SIP
    (r"(sip |mutual fund|nippon|hdfc mf|icici pru|sbi mf|parag parikh|mirae|axis mf|quant mf|zerodha coin|groww mf)", "SIP", "EXPENSE"),
    # Stocks & Trading
    (r"(zerodha|groww|upstox|angel one|angelone|icici direct|hdfc sky|dhan|kotak sec|sharekhan)", "Stocks", "EXPENSE"),
    # Investments (Gold, FD, PPF)
    (r"(fixed deposit|term deposit|gold|sgb|ppf|nps|national pension)", "Investment", "EXPENSE"),
    # Rent & Housing
    (r"(rent|nobroker|cred rent|magicbricks|landlord|house rent)", "Rent", "EXPENSE"),
    # Debt / EMI / Credit Cards
    (r"(credit card|cred |emi |loan |bajaj finserv|hdfc card|sbi card|icici card|axis card|citi card)", "Debt", "EXPENSE"),
    # Income (Salary / Payroll / Remittance)
    (r"(salary|payroll|inward rem|stipend|client pay|bonus)", "Paycheck", "INCOME"),
    # Interest
    (r"(int\.pd|interest cr|interest paid|sb int)", "Interest", "INCOME"),
    # Self-Transfer
    (r"(self transfer|own account|trf to own|trf to self|self trf)", "Transfer", "TRANSFER"),
]


def classify_indian_narration(narration: str, raw_type: str = "DEBIT") -> tuple[str, str, bool]:
    """
    Classifies standard Indian bank transaction narration into category,
    transaction_type ('EXPENSE', 'INCOME', 'TRANSFER'), and is_self_transfer flag.
    """
    text = narration.lower()

    if any(k in text for k in ["self transfer", "own acc", "to self", "trf to own"]):
        return "Transfer", "TRANSFER", True

    for pattern, category, trans_type in INDIAN_MERCHANT_CATEGORIES:
        if re.search(pattern, text):
            if raw_type.upper() == "CREDIT" and trans_type != "TRANSFER":
                if category not in ("Paycheck", "Bonus", "Interest"):
                    return category, "INCOME", False
            return category, trans_type, (category == "Transfer")

    if raw_type.upper() == "CREDIT":
        return "Other", "INCOME", False
    return "Other", "EXPENSE", False

# Known Indian bank sender keywords & domains
INDIAN_BANK_SENDERS = {
    "hdfcbank": "HDFC Bank",
    "sbi": "State Bank of India (SBI)",
    "onlinesbi": "State Bank of India (SBI)",
    "icicibank": "ICICI Bank",
    "axisbank": "Axis Bank",
    "kotak": "Kotak Mahindra Bank",
    "idfcfirstbank": "IDFC First Bank",
    "idfc": "IDFC First Bank",
    "yesbank": "Yes Bank",
    "pnbindia": "Punjab National Bank",
    "bankofbaroda": "Bank of Baroda",
    "indusind": "IndusInd Bank",
    "federalbank": "Federal Bank",
    "canarabank": "Canara Bank",
    "unionbankofindia": "Union Bank",
    "rblbank": "RBL Bank",
    "sc.com": "Standard Chartered",
    "hsbc": "HSBC Bank",
}

# Regex patterns for parsing Indian bank transaction alert emails
AMOUNT_REGEX = re.compile(
    r"(?:Rs\.?|INR|₹)\s*([0-9,]+(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)

DEBIT_KEYWORDS = re.compile(
    r"\b(debited|spent|withdrawn|paid|sent|deducted|purchase|charged|used\s+for)\b",
    re.IGNORECASE,
)

CREDIT_KEYWORDS = re.compile(
    r"\b(credited|received|deposited|refund|reversal|added)\b",
    re.IGNORECASE,
)

OTP_ONLY_KEYWORDS = re.compile(
    r"\b(OTP\s+is|One\s+Time\s+Password|verification\s+code|login\s+OTP|do\s+not\s+share\s+this\s+OTP)\b",
    re.IGNORECASE,
)

UTR_REGEX = re.compile(
    r"(?:UPI\s*Ref(?:\s*No\.?)?|Ref(?:\s*No\.?)?|Reference\s*No\.?|UTR|RRN|IMPS\s*Ref(?:\s*No\.?)?)[:\s#-]*(\d{9,12})\b"
    r"|\bUPI[/\-](\d{12})\b",
    re.IGNORECASE,
)

MERCHANT_PATTERNS = [
    # "to VPA swiggy@paytm" or "towards VPA zomato@hdfcbank"
    re.compile(r"(?:to|from|towards)\s+(?:VPA\s+)?([a-zA-Z0-9._-]+@[a-zA-Z0-9]+)", re.IGNORECASE),
    # "at AMAZON INDIA on" or "to SWIGGY BANGALORE on"
    re.compile(r"\b(?:at|to|towards|favouring|from)\s+([A-Z0-9][A-Za-z0-9\s&._/-]{2,40}?)(?:\s+on\s+|\s+via\s+|\s+Ref|\s*\(|\.|,|$)"),
]


def _clean_html_to_text(raw_html: str) -> str:
    """Strips HTML/CSS/script tags and returns normalized plain text."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _decode_mime_header(header_val: Optional[str]) -> str:
    """Decodes MIME encoded email Subject / From headers."""
    if not header_val:
        return ""
    parts = decode_header(header_val)
    decoded = []
    for content, charset in parts:
        if isinstance(content, bytes):
            decoded.append(content.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(content))
    return "".join(decoded).strip()


def detect_bank_name(sender: str, subject: str, body: str) -> str:
    """Identifies the Indian bank from email sender, subject, or body."""
    combined = f"{sender} {subject}".lower()
    for key, name in INDIAN_BANK_SENDERS.items():
        if key in combined:
            return name
    body_lower = body[:500].lower()
    for key, name in INDIAN_BANK_SENDERS.items():
        if key in body_lower:
            return name
    return "Bank"


def parse_indian_bank_alert_email(
    sender: str,
    subject: str,
    body_text: str,
    email_date: date,
) -> Optional[Dict[str, Any]]:
    """
    Parses an Indian bank transaction alert email into a structured transaction dictionary.
    Returns None if the email is an OTP, promotional message, or non-transaction email.
    """
    full_text = f"{subject} . {body_text}"

    # 1. Ignore pure OTP / verification emails that aren't completed transactions
    if OTP_ONLY_KEYWORDS.search(full_text) and not (
        "debited from" in full_text.lower() or "credited to" in full_text.lower()
    ):
        return None

    # 2. Must contain a debit or credit indicator
    has_debit = bool(DEBIT_KEYWORDS.search(full_text))
    has_credit = bool(CREDIT_KEYWORDS.search(full_text))
    if not has_debit and not has_credit:
        return None

    # Determine transaction direction (check which keyword appears near the amount or first)
    tx_type = "DEBIT"
    if has_credit and not has_debit:
        tx_type = "CREDIT"
    elif has_credit and has_debit:
        # e.g. "Rs 500 debited from your a/c and credited to VPA..." -> DEBIT for the account holder!
        lower_text = full_text.lower()
        if "debited from your" in lower_text or "debited from a/c" in lower_text or "spent on your" in lower_text:
            tx_type = "DEBIT"
        elif "credited to your" in lower_text or "credited to a/c" in lower_text or "received in your" in lower_text:
            tx_type = "CREDIT"
        else:
            m_deb = DEBIT_KEYWORDS.search(full_text)
            m_cred = CREDIT_KEYWORDS.search(full_text)
            tx_type = "DEBIT" if m_deb.start() <= m_cred.start() else "CREDIT"

    # 3. Extract transaction amount
    amt_match = AMOUNT_REGEX.search(full_text)
    if not amt_match:
        return None

    try:
        amount = float(amt_match.group(1).replace(",", ""))
    except ValueError:
        return None

    if amount <= 0:
        return None

    # 4. Extract UPI / IMPS / NEFT 9-12 digit Reference Number (UTR/RRN)
    utr = None
    utr_match = UTR_REGEX.search(full_text)
    if utr_match:
        utr = utr_match.group(1) or utr_match.group(2)
    else:
        # Fallback: check for 12-digit number near 'UPI' or 'Ref'
        fallback_utr = re.search(r"\b(\d{12})\b", full_text)
        if fallback_utr:
            utr = fallback_utr.group(1)

    # 5. Extract Merchant / VPA counterparty
    merchant = None
    for pat in MERCHANT_PATTERNS:
        m = pat.search(body_text) or pat.search(subject)
        if m:
            candidate = m.group(1).strip(" .,-:")
            if len(candidate) >= 3 and candidate.lower() not in {"your account", "your a/c", "account", "a/c"}:
                merchant = candidate
                break

    if not merchant:
        merchant = subject[:60].strip() or "Bank Transaction"

    bank_name = detect_bank_name(sender, subject, body_text)
    short_bank = bank_name.split("(")[0].replace("Bank", "").strip() or "Bank"

    # Format narration with [Bank Alert] prefix and (Ref: <UTR>) so monthly statement reconciliation matches 100%
    ref_suffix = f" (Ref: {utr})" if utr else ""
    narration = f"[{short_bank} Alert] {merchant}{ref_suffix}"

    return {
        "date": str(email_date),
        "amount": amount,
        "type": tx_type,
        "bank_name": bank_name,
        "utr": utr,
        "merchant": merchant,
        "narration": narration,
    }


DEFAULT_ALLOWED_BANK_EMAILS = [
    # HDFC Bank (Alerts & Monthly Statements)
    "alerts@hdfcbank.bank.in",
    "estatement@hdfcbank.bank.in",
    "information@mailers.hdfcbank.bank.in",
    "alerts@hdfcbank.net",
    "estatement@hdfcbank.net",
    # Kotak Mahindra Bank (Alerts & Monthly Statements)
    "bankalerts@kotak.bank.in",
    "bankstatements@kotak.bank.in",
    "bankalerts@kotak.com",
    # State Bank of India - SBI (Alerts & Monthly Statements)
    "cbsalerts.sbi@alerts.sbi.bank.in",
    "cbssbi.cas@alerts.sbi.bank.in",
    "yonobysbi@alerts.sbi.bank.in",
    "onlinesbi@sbi.co.in",
]

STATEMENT_EXPORT_ROOT = statement_store.LOCAL_ROOT  # local development only; see statement_store

_MONTH_NAME_MAP = {
    "january": "01", "jan": "01",
    "february": "02", "feb": "02",
    "march": "03", "mar": "03",
    "april": "04", "apr": "04",
    "may": "05",
    "june": "06", "jun": "06",
    "july": "07", "jul": "07",
    "august": "08", "aug": "08",
    "september": "09", "sep": "09", "sept": "09",
    "october": "10", "oct": "10",
    "november": "11", "nov": "11",
    "december": "12", "dec": "12",
}


def infer_statement_month_key(subject: str, filename: str, email_dt: date) -> str:
    """Infers the YYYY-MM statement month from the email subject, filename, or email date."""
    combined = f"{subject} {filename}".lower()

    # 1. Check explicit "to DD-MM-YYYY" range end date (e.g. "from 01-06-2026 to 31-08-2026")
    range_match = re.search(r"to\s+\d{1,2}[-/](\d{2})[-/](20\d{2})", combined)
    if range_match:
        return f"{range_match.group(2)}-{range_match.group(1)}"

    # 2. Check Month Name + Year (e.g. "September 2026", "AUG2026", "Aug-2026")
    month_pat = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)[\s\-_]*(20\d{2})\b",
        combined,
    )
    if month_pat:
        m_num = _MONTH_NAME_MAP.get(month_pat.group(1)[:3], "01")
        return f"{month_pat.group(2)}-{m_num}"

    # 3. If sent in the first 7 days of the month as a monthly statement, attribute to previous month
    if email_dt.day <= 7 and "statement" in combined:
        prev_month_last_day = email_dt.replace(day=1) - timedelta(days=1)
        return prev_month_last_day.strftime("%Y-%m")

    return email_dt.strftime("%Y-%m")


def _parse_allowed_senders_list(raw_str: Optional[str]) -> List[str]:
    """Parses comma-separated allowlist of bank email addresses or returns default official bank emails."""
    if not raw_str or not raw_str.strip():
        return list(DEFAULT_ALLOWED_BANK_EMAILS)
    items = [
        s.strip().lower()
        for s in re.split(r"[,;\n]+", raw_str)
        if s.strip() and "@" in s.strip()
    ]
    return items if items else list(DEFAULT_ALLOWED_BANK_EMAILS)


def _is_sender_strictly_allowed(from_header: str, allowed_emails: List[str]) -> bool:
    """Strictly verifies that the extracted sender email address matches the bank sender allowlist."""
    _, addr = email.utils.parseaddr(from_header or "")
    addr = addr.strip().lower()
    if not addr or "@" not in addr:
        return False
    allowed_set = {a.strip().lower() for a in allowed_emails}
    return addr in allowed_set


def _redact_pii_from_bank_email(text: str) -> str:
    """Scrubs personal identifiable information (names, PAN, full account numbers, phone numbers) in memory."""
    if not text:
        return ""
    scrubbed = re.sub(r"\bDear\s+[A-Za-z\s.]{2,35}[,:]", "Dear Customer,", text, flags=re.IGNORECASE)
    scrubbed = re.sub(r"\b[A-Z]{5}\d{4}[A-Z]\b", "[REDACTED_PAN]", scrubbed)
    scrubbed = re.sub(r"(?<!\d)(?:\+91[\-\s]?)?[6-9]\d{9}(?!\d)", "[REDACTED_PHONE]", scrubbed)
    return scrubbed


class GmailBankSyncService:
    """Manages Gmail IMAP connection, daily alert syncing, and monthly statement PDF export & reconciliation."""

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993

    def __init__(self):
        self.db = DatabaseManager()

    def get_settings(self) -> Dict[str, Any]:
        """Retrieves current Gmail sync configuration.

        Non-secret settings come from the gmail_sync_settings table (or env fallback). Password
        presence is checked in Secret Manager; no part of any password is ever returned.
        """
        default_senders_str = ", ".join(DEFAULT_ALLOWED_BANK_EMAILS)
        has_app_pw = bool(_load_app_password())
        has_pdf_pw = bool(_load_statement_passwords())
        try:
            with self.db.pool.connect() as conn:
                row = conn.execute(sqlalchemy.text("""
                    SELECT email_address, auto_sync_enabled, sync_interval_hours,
                           last_synced_at, last_sync_status, allowed_senders
                    FROM gmail_sync_settings
                    WHERE id = 1
                    LIMIT 1
                """)).fetchone()

                count_row = conn.execute(sqlalchemy.text(
                    "SELECT COUNT(*), COALESCE(SUM(transactions_extracted), 0) FROM gmail_processed_emails"
                )).fetchone()
                emails_processed = int(count_row[0]) if count_row else 0
                total_tx_synced = int(count_row[1]) if count_row else 0

                stmt_count_row = conn.execute(sqlalchemy.text(
                    "SELECT COUNT(*) FROM exported_statement_pdfs"
                )).fetchone()
                exported_pdfs_count = int(stmt_count_row[0]) if stmt_count_row else 0

                if row and row[0]:
                    allowed_list = _parse_allowed_senders_list(row[5])
                    return {
                        "configured": bool(row[0] and has_app_pw),
                        "email_address": row[0],
                        "app_password_masked": MASKED_SECRET if has_app_pw else "",
                        "has_pdf_password": has_pdf_pw,
                        "allowed_senders": ", ".join(allowed_list),
                        "auto_sync_enabled": bool(row[1]),
                        "sync_interval_hours": int(row[2] or 24),
                        "last_synced_at": row[3].isoformat() if row[3] else None,
                        "last_sync_status": row[4] or "Ready to sync",
                        "emails_processed": emails_processed,
                        "total_transactions_synced": total_tx_synced,
                        "exported_pdfs_count": exported_pdfs_count,
                    }
        except Exception as e:
            logger.warning(f"Failed to read gmail_sync_settings: {e}")

        env_email = os.getenv("GMAIL_EMAIL", "").strip()
        return {
            "configured": bool(env_email and has_app_pw),
            "email_address": env_email,
            "app_password_masked": MASKED_SECRET if has_app_pw else "",
            "has_pdf_password": has_pdf_pw,
            "allowed_senders": default_senders_str,
            "auto_sync_enabled": True,
            "sync_interval_hours": 24,
            "last_synced_at": None,
            "last_sync_status": "Not configured yet" if not env_email else "Ready to sync",
            "emails_processed": 0,
            "total_transactions_synced": 0,
            "exported_pdfs_count": 0,
        }

    def _get_raw_credentials(self, strict: bool = False) -> Tuple[str, str, str, List[str]]:
        """Returns (email_address, app_password, pdf_passwords, allowed_senders_list).

        Address and allowlist come from the database (or env fallback); both passwords come
        from Secret Manager.
        """
        app_pw = _load_app_password(strict=strict)
        pdf_pw = _load_statement_passwords(strict=strict)
        try:
            with self.db.pool.connect() as conn:
                row = conn.execute(sqlalchemy.text(
                    "SELECT email_address, allowed_senders FROM gmail_sync_settings WHERE id = 1 LIMIT 1"
                )).fetchone()
                if row and row[0]:
                    return (row[0].strip(), app_pw, pdf_pw, _parse_allowed_senders_list(row[1]))
        except Exception:
            pass
        return (
            os.getenv("GMAIL_EMAIL", "").strip(),
            app_pw,
            pdf_pw,
            list(DEFAULT_ALLOWED_BANK_EMAILS),
        )

    def save_settings(
        self,
        email_address: str,
        app_password: Optional[str] = None,
        pdf_password: Optional[str] = None,
        allowed_senders: Optional[str] = None,
        auto_sync_enabled: bool = True,
        sync_interval_hours: int = 24,
    ) -> Dict[str, Any]:
        """Saves Gmail settings and the strict bank sender allowlist, verifying IMAP login.

        New passwords are verified, then stored as new Secret Manager versions. The database
        only keeps non-secret settings (its legacy password columns are written as empty).
        """
        email_clean = email_address.strip()
        existing_email, existing_pw, existing_pdf_pw, existing_allowed = self._get_raw_credentials()

        pw_clean = app_password.replace(" ", "").strip() if app_password else existing_pw
        pdf_pw_clean = pdf_password.strip() if pdf_password is not None and pdf_password != "" else existing_pdf_pw
        allowed_list = (
            _parse_allowed_senders_list(allowed_senders)
            if allowed_senders is not None
            else existing_allowed
        )
        allowed_str = ", ".join(allowed_list)

        if not email_clean or not pw_clean:
            raise ValueError("Both Gmail Address and 16-character Google App Password are required.")

        try:
            mail = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT, timeout=12)
            mail.login(email_clean, pw_clean)
            mail.logout()
        except Exception as e:
            raise ValueError(
                f"Gmail IMAP authentication failed: {str(e)}. "
                "Please ensure you are using a 16-character Google App Password "
                "(generate one at myaccount.google.com/apppasswords)."
            )

        # Store changed credentials in Secret Manager (verified above), never in the database.
        if pw_clean != existing_pw:
            secret_store.set_secret(GMAIL_APP_PASSWORD_SECRET, pw_clean)
        if pdf_pw_clean and pdf_pw_clean != existing_pdf_pw:
            secret_store.set_secret(STATEMENT_PASSWORDS_SECRET, pdf_pw_clean)

        with self.db.pool.begin() as conn:
            conn.execute(sqlalchemy.text("""
                INSERT INTO gmail_sync_settings (
                    id, email_address, app_password, pdf_password, allowed_senders,
                    auto_sync_enabled, sync_interval_hours, last_sync_status, updated_at
                )
                VALUES (1, :email, '', '', :allowed, :auto_sync, :interval, 'Connected & Verified (Strict Bank Filter)', CURRENT_TIMESTAMP)
                ON CONFLICT (id) DO UPDATE SET
                    email_address = EXCLUDED.email_address,
                    app_password = '',
                    pdf_password = '',
                    allowed_senders = EXCLUDED.allowed_senders,
                    auto_sync_enabled = EXCLUDED.auto_sync_enabled,
                    sync_interval_hours = EXCLUDED.sync_interval_hours,
                    last_sync_status = 'Connected & Verified (Strict Bank Filter)',
                    updated_at = CURRENT_TIMESTAMP;
            """), {
                "email": email_clean,
                "allowed": allowed_str,
                "auto_sync": auto_sync_enabled,
                "interval": sync_interval_hours,
            })

        return self.get_settings()

    def sync_now(
        self,
        days_back: int = 30,
        include_statements: bool = True,
    ) -> Dict[str, Any]:
        """
        Connects to Gmail IMAP and executes a STRICT server-side filter (`X-GM-RAW`)
        matching ONLY the allowlisted official HDFC, Kotak, and SBI sender email addresses.
        - Extracts daily bank alert emails
        - Exports monthly bank statement PDFs organized by month (`data/monthly_statements/<YYYY-MM>/`)
        - Unlocks & imports PDFs and reconciles against daily alerts with zero duplicates.
        """
        email_addr, app_pw, pdf_pw, allowed_emails = self._get_raw_credentials(strict=True)
        if not email_addr or not app_pw:
            raise ValueError("Gmail Sync is not configured yet. Please add your Gmail address and App Password first.")

        try:
            mail = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT, timeout=25)
            mail.login(email_addr, app_pw)
            mail.select("INBOX", readonly=True)

            # Strict server-side Gmail search: ONLY emails from the exact allowlisted bank email addresses
            from_clause = " OR ".join(allowed_emails)
            gm_query = f"newer_than:{max(1, int(days_back))}d from:({from_clause})"
            status, messages = mail.search(None, "X-GM-RAW", f'"{gm_query}"')

            if status != "OK" or not messages or not messages[0]:
                mail.logout()
                return self._record_sync_result(
                    0, 0, 0,
                    f"Checked strict bank senders ({len(allowed_emails)} addresses): 0 new bank emails in last {days_back}d."
                )

            msg_nums = messages[0].split()[-150:]

            with self.db.pool.connect() as conn:
                processed_ids = {
                    row[0]
                    for row in conn.execute(sqlalchemy.text("SELECT message_id FROM gmail_processed_emails")).fetchall()
                }
                exported_msg_ids = {
                    row[0]
                    for row in conn.execute(sqlalchemy.text("SELECT message_id FROM exported_statement_pdfs")).fetchall()
                }

            alert_receipts: List[ReceiptData] = []
            newly_processed_emails: List[Tuple[str, str, str, str, int]] = []
            pdfs_exported_count = 0
            statement_tx_count = 0

            for num in reversed(msg_nums):
                try:
                    hdr_res, hdr_data = mail.fetch(
                        num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM DATE)])"
                    )
                    if hdr_res != "OK" or not hdr_data or not hdr_data[0]:
                        continue

                    hdr_msg = email.message_from_bytes(hdr_data[0][1])
                    msg_id = (hdr_msg.get("Message-ID") or f"uid-{num.decode()}").strip()

                    sender = _decode_mime_header(hdr_msg.get("From"))
                    if not _is_sender_strictly_allowed(sender, allowed_emails):
                        continue

                    raw_subject = _decode_mime_header(hdr_msg.get("Subject"))
                    subject = _redact_pii_from_bank_email(raw_subject)

                    # Skip if already processed AND (not a statement email or already exported)
                    is_likely_statement = "statement" in raw_subject.lower() or "cas@" in sender.lower() or "statements@" in sender.lower()
                    if msg_id in processed_ids and (not is_likely_statement or msg_id in exported_msg_ids):
                        continue

                    res, msg_data = mail.fetch(num, "(RFC822)")
                    if res != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)

                    try:
                        email_dt = parsedate_to_datetime(msg.get("Date")).date()
                    except Exception:
                        email_dt = date.today()

                    body_text = ""
                    attachments_found = 0
                    _, sender_email_only = email.utils.parseaddr(sender)

                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disp = str(part.get("Content-Disposition") or "")
                        filename = part.get_filename()
                        if filename:
                            filename = _decode_mime_header(filename)

                        if include_statements and filename and filename.lower().endswith((".pdf", ".xlsx", ".xls")):
                            payload_bytes = part.get_payload(decode=True)
                            if payload_bytes:
                                exported, imported_tx = self._export_and_process_statement_pdf(
                                    msg_id=msg_id,
                                    sender_email=sender_email_only,
                                    subject=subject,
                                    email_dt=email_dt,
                                    filename=filename,
                                    file_bytes=payload_bytes,
                                    pdf_password=pdf_pw,
                                )
                                pdfs_exported_count += exported
                                statement_tx_count += imported_tx
                                attachments_found += 1
                            continue

                        if "attachment" not in content_disp.lower():
                            if content_type == "text/plain":
                                payload = part.get_payload(decode=True)
                                if payload:
                                    body_text += " " + payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                            elif content_type == "text/html" and not body_text:
                                payload = part.get_payload(decode=True)
                                if payload:
                                    html_str = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                                    body_text += " " + _clean_html_to_text(html_str)

                    if attachments_found > 0:
                        newly_processed_emails.append((msg_id, "[Monthly Bank Statement PDF]", sender_email_only, "STATEMENT", statement_tx_count))
                        processed_ids.add(msg_id)
                        exported_msg_ids.add(msg_id)
                        continue

                    if msg_id in processed_ids:
                        continue

                    body_text = _redact_pii_from_bank_email(body_text)
                    parsed_alert = parse_indian_bank_alert_email(sender_email_only, subject, body_text, email_dt)
                    if parsed_alert:
                        category, trans_type, is_self = classify_indian_narration(
                            parsed_alert["narration"], parsed_alert["type"]
                        )
                        receipt = ReceiptData(
                            date=datetime.strptime(parsed_alert["date"], "%Y-%m-%d").date(),
                            amount=parsed_alert["amount"],
                            description=parsed_alert["narration"],
                            category=category,
                            type=trans_type,
                            confidence=95.0,
                            is_self_transfer=is_self,
                            requires_human_review=(category == "Other"),
                        )
                        alert_receipts.append(receipt)
                        newly_processed_emails.append((msg_id, "[Bank Transaction Alert]", sender_email_only, "ALERT", 1))
                        processed_ids.add(msg_id)
                except Exception as msg_err:
                    logger.warning(f"Skipping email message due to parse error: {msg_err}")

            mail.logout()

            saved_alert_ids = []
            if alert_receipts:
                saved_alert_ids = self.db.save_transactions_batch(
                    alert_receipts, gcs_uri="gmail://strict-bank-alerts"
                )

            if newly_processed_emails:
                with self.db.pool.begin() as conn:
                    for m_id, subj, sndr, e_type, cnt in newly_processed_emails:
                        conn.execute(sqlalchemy.text("""
                            INSERT INTO gmail_processed_emails (
                                message_id, subject, sender, email_type, transactions_extracted
                            )
                            VALUES (:id, :subj, :sndr, :etype, :cnt)
                            ON CONFLICT (message_id) DO NOTHING;
                        """), {
                            "id": m_id,
                            "subj": subj,
                            "sndr": sndr,
                            "etype": e_type,
                            "cnt": cnt,
                        })

            status_msg = (
                f"Synced {len(saved_alert_ids)} alert(s) & exported {pdfs_exported_count} monthly statement PDF(s) "
                f"({statement_tx_count} statement tx synced)."
            )
            return self._record_sync_result(
                len(saved_alert_ids),
                statement_tx_count,
                len(newly_processed_emails),
                status_msg,
                pdfs_exported=pdfs_exported_count,
            )

        except Exception as e:
            logger.error(f"Gmail bank sync failed: {e}", exc_info=True)
            self._record_sync_result(0, 0, 0, f"Sync error: {str(e)}")
            raise

    def _export_and_process_statement_pdf(
        self,
        msg_id: str,
        sender_email: str,
        subject: str,
        email_dt: date,
        filename: str,
        file_bytes: bytes,
        pdf_password: str = "",
    ) -> Tuple[int, int]:
        """
        Exports a monthly bank statement PDF into `data/monthly_statements/<YYYY-MM>/`,
        attempts automatic decryption if password-protected, and imports transactions
        with 3-layer deduplication against daily alerts.
        Returns (pdfs_exported, transactions_imported).
        """
        bank_name = detect_bank_name(sender_email, subject, filename)
        if "hdfc" in sender_email.lower() or "hdfc" in subject.lower():
            bank_name = "HDFC Bank"
            bank_prefix = "HDFC_Bank"
        elif "kotak" in sender_email.lower() or "kotak" in subject.lower():
            bank_name = "Kotak Mahindra Bank"
            bank_prefix = "Kotak_Bank"
        elif "sbi" in sender_email.lower() or "sbi" in subject.lower():
            bank_name = "State Bank of India (SBI)"
            bank_prefix = "SBI"
        else:
            bank_prefix = re.sub(r"[^a-zA-Z0-9]", "_", bank_name)

        month_key = infer_statement_month_key(subject, filename, email_dt)

        safe_orig = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
        export_filename = f"{bank_prefix}_{month_key}_{safe_orig}"
        # Work on a private temp copy, then keep the result via statement_store (a private
        # Cloud Storage bucket on Cloud Run, data/monthly_statements locally). DEC-025.
        staging = statement_store.staging_dir()
        export_path = os.path.join(staging, export_filename)

        try:
            with open(export_path, "wb") as f:
                f.write(file_bytes)

            is_encrypted = False
            sync_status = "EXPORTED"
            imported_count = 0

            if export_path.lower().endswith(".pdf"):
                is_encrypted, unlocked = self._check_and_unlock_pdf(export_path, pdf_password)
                if is_encrypted and not unlocked:
                    sync_status = "PASSWORD_REQUIRED"

            if sync_status != "PASSWORD_REQUIRED":
                try:
                    from main import process_file

                    res = process_file(export_path)
                    sync_status = "IMPORTED_AND_RECONCILED"
                    # Extract transaction count from messages if available
                    if isinstance(res, dict) and res.get("messages"):
                        for m in res["messages"]:
                            m_cnt = re.search(r"Found\s+(\d+)\s+transactions", str(m))
                            if m_cnt:
                                imported_count = int(m_cnt.group(1))
                                break
                    if imported_count == 0:
                        imported_count = 1
                except Exception as e:
                    logger.warning(f"Exported PDF {export_filename} could not be parsed yet: {e}")
                    sync_status = "EXPORTED_ONLY"

            location = statement_store.save(export_path, month_key, export_filename)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        with self.db.pool.begin() as conn:
            conn.execute(sqlalchemy.text("""
                INSERT INTO exported_statement_pdfs (
                    message_id, month_key, bank_name, file_name, file_path,
                    email_subject, email_date, is_encrypted, sync_status, transactions_imported
                )
                VALUES (:msg_id, :mkey, :bname, :fname, :fpath, :subj, :edate, :enc, :status, :tx_cnt)
                ON CONFLICT (file_path) DO UPDATE SET
                    is_encrypted = EXCLUDED.is_encrypted,
                    sync_status = EXCLUDED.sync_status,
                    transactions_imported = GREATEST(exported_statement_pdfs.transactions_imported, EXCLUDED.transactions_imported);
            """), {
                "msg_id": msg_id,
                "mkey": month_key,
                "bname": bank_name,
                "fname": export_filename,
                "fpath": location,
                "subj": subject[:200],
                "edate": email_dt,
                "enc": (sync_status == "PASSWORD_REQUIRED"),
                "status": sync_status,
                "tx_cnt": imported_count,
            })
            # Imported rows reference their source file; point them at the stored copy.
            conn.execute(sqlalchemy.text(
                "UPDATE assets SET gcs_uri = :final WHERE gcs_uri = :staged"
            ), {"final": location, "staged": export_path})

        return 1, imported_count

    def _check_and_unlock_pdf(self, pdf_path: str, pdf_passwords_str: str = "") -> Tuple[bool, bool]:
        """
        Checks if `pdf_path` is encrypted. If encrypted, tries any comma-separated
        passwords in `pdf_passwords_str`. If unlocked, overwrites `pdf_path` with the
        decrypted PDF. Returns (was_encrypted, is_now_unlocked).
        """
        try:
            from pypdf import PdfReader, PdfWriter
            reader = PdfReader(pdf_path)
            if not reader.is_encrypted:
                return False, True

            candidates = [
                p.strip()
                for p in re.split(r"[,;\n]+", pdf_passwords_str or "")
                if p.strip()
            ]
            for pw in candidates:
                try:
                    res = reader.decrypt(pw)
                    if res > 0:
                        writer = PdfWriter()
                        for page in reader.pages:
                            writer.add_page(page)
                        temp_unlocked = pdf_path + ".unlocked"
                        with open(temp_unlocked, "wb") as out_f:
                            writer.write(out_f)
                        os.replace(temp_unlocked, pdf_path)
                        logger.info(f"Successfully decrypted bank statement PDF: {os.path.basename(pdf_path)}")
                        return True, True
                except Exception:
                    continue

            return True, False
        except Exception as e:
            logger.warning(f"PDF encryption check failed for {pdf_path}: {e}")
            return False, True

    def get_exported_statements(self) -> Dict[str, Any]:
        """Returns all exported monthly statement PDFs grouped by month_key (YYYY-MM)."""
        try:
            with self.db.pool.connect() as conn:
                rows = conn.execute(sqlalchemy.text("""
                    SELECT id, month_key, bank_name, file_name, file_path,
                           email_subject, email_date, is_encrypted, sync_status,
                           transactions_imported, exported_at
                    FROM exported_statement_pdfs
                    ORDER BY month_key DESC, email_date DESC, id DESC
                """)).fetchall()

                statements = []
                by_month: Dict[str, List[Dict[str, Any]]] = {}

                for r in rows:
                    item = {
                        "id": int(r[0]),
                        "month_key": r[1],
                        "bank_name": r[2],
                        "file_name": r[3],
                        "file_path": r[4],
                        "email_subject": r[5],
                        "email_date": str(r[6]) if r[6] else None,
                        "is_encrypted": bool(r[7]),
                        "sync_status": r[8],
                        "transactions_imported": int(r[9] or 0),
                        "exported_at": r[10].isoformat() if r[10] else None,
                    }
                    statements.append(item)
                    by_month.setdefault(r[1], []).append(item)

                months_grouped = [
                    {"month_key": m, "count": len(items), "statements": items}
                    for m, items in sorted(by_month.items(), key=lambda x: x[0], reverse=True)
                ]
                return {
                    "export_directory": statement_store.describe_root(),
                    "total_pdfs": len(statements),
                    "months": months_grouped,
                    "statements": statements,
                }
        except Exception as e:
            logger.error(f"Failed to list exported statements: {e}")
            return {"export_directory": statement_store.describe_root(), "total_pdfs": 0, "months": [], "statements": []}

    def unlock_and_sync_statement(self, stmt_id: int, password: str = "") -> Dict[str, Any]:
        """Unlocks a password-protected exported bank statement PDF and imports & reconciles its transactions.

        Also retries a statement whose import failed or never ran (DEC-060): once unlocked, the stored
        copy needs no password. If the import fails, the statement is marked IMPORT_FAILED (not
        encrypted any more) so the Bank Sync screen offers a retry instead of "Imported (0 txns)".
        """
        with self.db.pool.connect() as conn:
            row = conn.execute(sqlalchemy.text("""
                SELECT id, file_name, file_path, bank_name, month_key
                FROM exported_statement_pdfs
                WHERE id = :id
                LIMIT 1
            """), {"id": stmt_id}).fetchone()

        if not row:
            raise ValueError(f"Statement PDF #{stmt_id} not found.")

        location = row[2]
        if not statement_store.exists(location):
            raise ValueError(f"The exported statement file is missing from storage ({row[1]}).")

        from main import process_file

        # Work on a local copy; the unlocked PDF replaces the stored one (as before), so later
        # downloads and re-imports don't need the password again.
        with statement_store.local_copy(location) as local_path:
            was_enc, unlocked = self._check_and_unlock_pdf(local_path, password)
            if was_enc and not unlocked:
                if not (password or "").strip():
                    raise ValueError("This statement is password-protected. Enter its PDF password to import it.")
                raise ValueError("Incorrect PDF password for this bank statement. Please check your bank's PDF password format.")
            if was_enc:
                statement_store.replace(location, local_path)

            try:
                res = process_file(local_path)
            except Exception:
                self._mark_statement_status(stmt_id, "IMPORT_FAILED")
                raise

        summary = res.get("summary") if isinstance(res, dict) else None
        messages = list(res.get("messages") or []) if isinstance(res, dict) else []
        imported_count = int(summary.get("rows", 0)) if summary else 0
        if not imported_count:
            for m in messages:
                m_cnt = re.search(r"Found\s+(\d+)\s+transactions", str(m))
                if m_cnt:
                    imported_count = int(m_cnt.group(1))
                    break

        with self.db.pool.begin() as conn:
            # GREATEST: re-running an already imported file reads nothing and keeps its count.
            conn.execute(sqlalchemy.text("""
                UPDATE exported_statement_pdfs
                SET is_encrypted = FALSE,
                    sync_status = 'IMPORTED_AND_RECONCILED',
                    transactions_imported = GREATEST(COALESCE(transactions_imported, 0), :cnt)
                WHERE id = :id
            """), {"cnt": imported_count, "id": stmt_id})
            if local_path != location:
                conn.execute(sqlalchemy.text(
                    "UPDATE assets SET gcs_uri = :final WHERE gcs_uri = :staged"
                ), {"final": location, "staged": local_path})

        if summary:
            message = (f"Imported {row[1]}: read {summary['rows']} transactions, {summary['added']} added to your "
                       f"ledger, {summary['already']} were already in it.")
        else:  # e.g. this exact file was imported before
            message = messages[0] if messages else f"Unlocked {row[1]}; nothing new to import."

        return {
            "status": "success",
            "id": stmt_id,
            "bank_name": row[3],
            "month_key": row[4],
            "file_name": row[1],
            "transactions_imported": imported_count,
            "added": int(summary.get("added", 0)) if summary else 0,
            "already_in_ledger": int(summary.get("already", 0)) if summary else 0,
            "message": message,
            "notifications": messages,
        }

    def _mark_statement_status(self, stmt_id: int, status: str) -> None:
        """Records that a statement's stored copy is unlocked but not imported (`status`)."""
        try:
            with self.db.pool.begin() as conn:
                conn.execute(sqlalchemy.text("""
                    UPDATE exported_statement_pdfs
                    SET is_encrypted = FALSE, sync_status = :status
                    WHERE id = :id
                """), {"status": status, "id": stmt_id})
        except Exception as e:
            logger.warning(f"Could not mark statement #{stmt_id} as {status}: {e}")

    def _record_sync_result(
        self,
        alerts_synced: int,
        statements_synced: int,
        emails_scanned: int,
        status_msg: str,
        pdfs_exported: int = 0,
    ) -> Dict[str, Any]:
        """Updates last_synced_at and last_sync_status in gmail_sync_settings."""
        try:
            with self.db.pool.begin() as conn:
                conn.execute(sqlalchemy.text("""
                    UPDATE gmail_sync_settings
                    SET last_synced_at = CURRENT_TIMESTAMP,
                        last_sync_status = :status
                    WHERE id = 1;
                """), {"status": status_msg})
        except Exception:
            pass

        return {
            "status": "success",
            "alerts_synced": alerts_synced,
            "statements_synced": statements_synced,
            "pdfs_exported": pdfs_exported,
            "emails_processed": emails_scanned,
            "message": status_msg,
            "synced_at": datetime.utcnow().isoformat(),
        }
