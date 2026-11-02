# app/statement_agent.py
"""Multimodal Bank Statement PDF Extraction Agent (Vertex AI Gemini, `config.MODEL_NAME`).

FDE Pillar 1 (BUILD):
- Passes raw PDF bytes or Google Cloud Storage (`gs://`) URIs directly to Gemini's native
  multimodal document understanding (Gemini 3.5 Flash by default, DEC-042) instead of brittle
  regex/OCR pipelines.
- Enforces `response_mime_type="application/json"` and validates output into `StatementData`.
- Preserves 9-12 digit UPI/IMPS/NEFT Reference (UTR) numbers in descriptions for downstream
  cross-source deduplication against daily Gmail bank alerts.
- Dates (DEC-035): the prompt carries today's date, the file name and the period it states, and
  `date_guard.fix_years` checks the returned years; budget sheets printed without years were
  saved years off before. Notes about corrections are left in `self.date_notes`.
- Long statements (DEC-060): a local PDF is read a few pages per Gemini call (`PAGES_PER_CALL`,
  up to `PARALLEL_CALLS` calls at once) instead of in one reply. A reply has an output limit, and
  Gemini's thinking counts against it: a 7-month Kotak statement (361 rows) came back cut off
  mid-JSON, so nothing was imported, and a 12-month one (971 rows) came back with only its first
  122 rows and was imported as if complete.
  - A reply that is cut off (finish reason MAX_TOKENS) or isn't valid JSON is read again in two
    halves, down to single pages; a page that still fails stops the whole extraction
    (ExtractionError), so a statement is never imported half-way.
  - A call that returns clearly fewer rows than its pages' text shows is read again page by page;
    a gap that remains is reported in `self.coverage_notes` for the upload messages.
- Password-protected PDFs (DEC-060): Gemini rejects a PDF that needs a password to open with
  400 INVALID_ARGUMENT. `pdf_needs_password` / `decrypt_pdf` let callers unlock a file first
  (`main.process_file` does, with the password sent with the upload or a saved statement
  password); a locked file that reaches the reader raises PasswordRequiredError instead of
  calling Gemini. A PDF encrypted with an empty password (owner restrictions only) is read and
  split as usual.
"""
import io
import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import vertexai
from google.api_core import exceptions as google_exceptions
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter
from vertexai.generative_models import GenerativeModel, Part

from config import PROJECT_ID, LOCATION, MODEL_NAME
from date_guard import fix_years, prompt_context
from gemini import reply_text
from models import StatementData
from exceptions import ExtractionError, PasswordRequiredError

logger = logging.getLogger(__name__)

LOCKED_PDF_MESSAGE = (
    "This PDF is password-protected, so it can't be read. Enter its password in the "
    "\"PDF password\" box and upload it again."
)


def _env_int(name: str, default: int) -> int:
    """A positive whole number from the environment, else `default`."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("%s=%r is not a whole number; using %d.", name, raw, default)
        return default


# Pages per Gemini call. Two pages of a Kotak statement (about 70 rows) take ~40 s and ~16k of the
# reply budget (half of it thinking); the 13-page statement in one call needed more than 64k.
PAGES_PER_CALL = _env_int("STATEMENT_PAGES_PER_CALL", 2)
# Calls that run at the same time, so a long statement takes about as long as a few pages.
PARALLEL_CALLS = _env_int("STATEMENT_PARALLEL_CALLS", 6)
# The reply budget of one call (Gemini 3.5 Flash's maximum; thinking included).
MAX_OUTPUT_TOKENS = 65535
# Attempts per call when Vertex AI is busy or briefly unavailable.
CALL_ATTEMPTS = 3

_TRANSIENT_ERRORS = tuple(
    getattr(google_exceptions, name)
    for name in ("ResourceExhausted", "TooManyRequests", "ServiceUnavailable",
                 "InternalServerError", "DeadlineExceeded", "GatewayTimeout")
    if hasattr(google_exceptions, name)
)

# A text line that starts a transaction row: an optional serial number (Kotak's "#" column),
# then a date such as 01-04-2026, 01/04/26, 01-Apr-26 or 01 Apr 2026, then more text. Used only
# to notice a reply that left rows out; it never decides what is imported.
_ROW_START = re.compile(
    r"^\s*(?:\d{1,5}\s+)?"
    r"(?:\d{1,2}[-/.](?:\d{1,2}|[A-Za-z]{3,9})[-/.](?:\d{4}|\d{2})"
    r"|\d{1,2}\s+[A-Za-z]{3,9},?\s+(?:\d{4}|\d{2}))"
    r"\s+\S"
)

STATEMENT_PROMPT = """
            You are an expert financial AI agent. Analyze the attached bank statement PDF.
            Extract EVERY transaction you can find in the document.
            For each transaction, extract:

            1. type: (string) Set to "INCOME" for deposits/credits, "EXPENSE" for withdrawals/purchases, or "TRANSFER" for transfers between accounts.
            2. date: (YYYY-MM-DD)
            3. amount: (number, absolute positive value only)
            4. description: (clean string of the merchant or purpose, preserving any 9-12 digit UPI/IMPS/NEFT Reference or UTR number if present in the row)
            5. category: Extract the category text (e.g. 'Food', 'Utilities'). If the transaction is a transfer between the user's own accounts or accounts in their name, set category strictly to "Transfer".
            6. confidence: (number 0-100). Set this lower (e.g., below 85) if you are unsure of your category guess.
            7. is_self_transfer: (boolean) Set to true ONLY if the transaction is a transfer between the user's own accounts. If true, type should be "TRANSFER" and category should be "Transfer".

            Return the result ONLY as a raw JSON object with a single key "transactions" which contains a list of these objects.
            """


def count_row_lines(text: str) -> int:
    """How many lines of a page's text look like the start of a transaction row."""
    return sum(1 for line in (text or "").splitlines() if _ROW_START.match(line))


def allowed_gap(expected: int) -> int:
    """Rows a reply may come up short of the text-based count before it is read again (the count
    can include a date-led line that isn't a transaction, such as a statement period)."""
    return max(2, math.ceil(expected * 0.05))


@dataclass(frozen=True)
class PageSpan:
    """Pages `first`..`last` (1-based, inclusive) of a `total`-page statement."""
    first: int
    last: int
    total: int

    @property
    def count(self) -> int:
        return self.last - self.first + 1

    @property
    def whole(self) -> bool:
        return self.first == 1 and self.last == self.total

    @property
    def label(self) -> str:
        return f"page {self.first}" if self.count == 1 else f"pages {self.first}-{self.last}"

    def halves(self) -> tuple["PageSpan", "PageSpan"]:
        middle = self.first + self.count // 2 - 1
        return PageSpan(self.first, middle, self.total), PageSpan(middle + 1, self.last, self.total)

    def single_pages(self) -> list["PageSpan"]:
        return [PageSpan(page, page, self.total) for page in range(self.first, self.last + 1)]


def split_into_spans(total_pages: int, pages_per_call: int = None) -> list[PageSpan]:
    """The page spans read by one call each, in page order."""
    size = max(1, pages_per_call or PAGES_PER_CALL)
    return [PageSpan(first, min(first + size - 1, total_pages), total_pages)
            for first in range(1, total_pages + 1, size)]


def page_row_counts(pdf_bytes: bytes):
    """Rows each page's text shows (0 where a page has no text layer), or None when the file
    can't be split here (not a readable PDF, locked with a password, no pages): it is then read
    whole. A PDF encrypted with an empty password (owner restrictions only) is readable."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted and not reader.decrypt(""):
            return None
        if not reader.pages:
            return None
        counts = []
        for page in reader.pages:
            try:
                counts.append(count_row_lines(page.extract_text() or ""))
            except Exception:  # a page whose text can't be read still gets sent to Gemini
                counts.append(0)
        return counts
    except Exception as e:
        logger.info("Statement PDF can't be split into pages (%s); reading it in one call.", type(e).__name__)
        return None


def pdf_needs_password(pdf_bytes: bytes) -> bool:
    """True when the PDF can't be opened without a password. Gemini answers such a file with
    400 INVALID_ARGUMENT ("Request contains an invalid argument"), so it must be unlocked first."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return bool(reader.is_encrypted) and not reader.decrypt("")
    except Exception:
        return False  # not a PDF pypdf can read: let Gemini try it, as before


def decrypt_pdf(pdf_bytes: bytes, passwords) -> bytes | None:
    """The PDF without its password, using the first of `passwords` that opens it, or None if none
    does. The bytes are returned unchanged when the file isn't locked."""
    if not pdf_needs_password(pdf_bytes):
        return pdf_bytes
    for password in passwords:
        if not password:
            continue
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            if not reader.decrypt(password):
                continue
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            buffer = io.BytesIO()
            writer.write(buffer)
            return buffer.getvalue()
        except Exception:
            continue
    return None


def pages_as_pdf(pdf_bytes: bytes, span: PageSpan) -> bytes:
    """The pages of `span` as a PDF of their own (each call opens its own reader: thread-safe)."""
    if span.whole:
        return pdf_bytes
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for index in range(span.first - 1, span.last):
        writer.add_page(reader.pages[index])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def pages_instructions(span: PageSpan) -> str:
    """What the model needs to know when it sees only some pages of the statement."""
    if span.whole:
        return ""
    return (
        f"\nThe attached PDF is {span.label} of a {span.total}-page bank statement; the other pages are read separately.\n"
        "- Extract every transaction row on these pages, in the order they appear. Do not skip rows, stop early or summarise.\n"
        "- Text at the top of the first page that only continues a row from the page before (no date or amount of its own) "
        "is not a separate transaction: leave it out.\n"
    )


def _finish_reason(response):
    """The first candidate's finish reason name (e.g. "STOP", "MAX_TOKENS"), or None."""
    candidates = getattr(response, "candidates", None)
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    return getattr(reason, "name", None) or (str(reason) if reason is not None else None)


class StatementExtractor:
    """Extracts structured transaction rows from multi-page bank statement PDFs."""
    def __init__(self):
        self.date_notes = []
        self.coverage_notes = []
        self.pages_read = 0
        try:
            vertexai.init(project=PROJECT_ID, location=LOCATION)
            self.model = GenerativeModel(MODEL_NAME)
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI: {e}")
            raise ExtractionError("Vertex AI initialization failed") from e

    def extract(self, gcs_uri: str) -> StatementData:
        """Parses a local PDF path or `gs://` URI into a validated `StatementData` object."""
        logger.info(f"Starting PDF statement extraction for URI: {gcs_uri}")
        self.date_notes = []
        self.coverage_notes = []
        self.pages_read = 0

        try:
            from database import DatabaseManager

            # The parts of the prompt that are the same for every call.
            context = prompt_context(gcs_uri) + DatabaseManager.format_learned_rules_prompt()

            # Smart routing for Local vS Cloud file
            if gcs_uri.startswith("gs://"):
                # it is a cloud storage file: read in one call
                document_part = Part.from_uri(uri=gcs_uri, mime_type="application/pdf")
                rows = self._ask(document_part, STATEMENT_PROMPT + context, "the statement")
                if rows is None:
                    raise ExtractionError("The AI reply for this statement was cut off; upload the file instead.")
            else:
                # it is a local file
                with open(gcs_uri, "rb") as f:
                    pdf_bytes = f.read()
                rows = self._read_statement(pdf_bytes, context)

            # Pydantic validates that the AI returned a proper list of ReceiptData objects
            validated_data = StatementData(transactions=rows)

            # A year the model filled in on its own is put back in the file's period (DEC-035).
            self.date_notes = fix_years(validated_data.transactions, gcs_uri)

            logger.info(f"Successfully extracted {len(validated_data.transactions)} transactions from PDF.")
            return validated_data

        except ExtractionError:
            raise

        except json.JSONDecodeError as e:
            logger.error(f"Gemini returned invalid JSON: {e}")
            raise ExtractionError("Invalid JSON from LLM") from e

        except ValidationError as e:
            logger.error(f"Data validation failed: {e}")
            raise ExtractionError("Extracted data did not match required schema") from e

        except Exception as e:
            logger.error(f"Unexpected error during extraction: {e}")
            raise ExtractionError("Extraction process failed") from e

    # ------------------------------------------------------------------------------------------
    # Reading a statement a few pages at a time (DEC-060)
    # ------------------------------------------------------------------------------------------

    def _read_statement(self, pdf_bytes: bytes, context: str) -> list:
        """Every row of a local PDF, read span by span (several spans at once), in page order."""
        if pdf_needs_password(pdf_bytes):
            # Callers unlock files first (process_file, Bank Sync); never send a locked one to Gemini.
            raise PasswordRequiredError(LOCKED_PDF_MESSAGE)
        counts = page_row_counts(pdf_bytes)
        if not counts:
            rows = self._ask(Part.from_data(data=pdf_bytes, mime_type="application/pdf"),
                             STATEMENT_PROMPT + context, "the statement")
            if rows is None:
                raise ExtractionError(
                    "The AI reader couldn't finish this statement (its reply was cut off), and the file "
                    "can't be split into pages. Nothing was imported."
                )
            return rows

        self.pages_read = len(counts)
        spans = split_into_spans(len(counts))
        started = time.monotonic()
        if len(spans) == 1:
            results = [self._read_span(pdf_bytes, spans[0], context, counts)]
        else:
            pool = ThreadPoolExecutor(max_workers=min(PARALLEL_CALLS, len(spans)),
                                      thread_name_prefix="statement-pages")
            try:
                futures = [pool.submit(self._read_span, pdf_bytes, span, context, counts) for span in spans]
                results = [future.result() for future in futures]
            finally:
                # On a failure, calls that haven't started are dropped; running ones finish.
                pool.shutdown(wait=True, cancel_futures=True)

        rows = [row for span_rows, _notes in results for row in span_rows]
        self.coverage_notes = [note for _rows, notes in results for note in notes]
        logger.info("Read %d rows from %d pages in %d calls (%.1f s).",
                    len(rows), len(counts), len(spans), time.monotonic() - started)
        return rows

    def _read_span(self, pdf_bytes: bytes, span: PageSpan, context: str, counts: list) -> tuple[list, list]:
        """(rows, notes) for one span. A reply that comes up short of the rows its pages' text
        shows is read again page by page; a gap that remains becomes a note."""
        expected = sum(counts[span.first - 1:span.last])
        rows = self._rows_for(pdf_bytes, span, context)
        if span.count > 1 and expected and len(rows) < expected - allowed_gap(expected):
            logger.warning("Statement %s: %d rows read, the page text shows about %d; reading each page again.",
                           span.label, len(rows), expected)
            again = []
            for page in span.single_pages():
                again.extend(self._rows_for(pdf_bytes, page, context))
            if len(again) > len(rows):
                rows = again
        notes = []
        if expected and len(rows) < expected - allowed_gap(expected):
            logger.warning("Statement %s: still %d rows against about %d in the page text.", span.label, len(rows), expected)
            notes.append(
                f"{span.label.capitalize()} of the statement: {len(rows)} transactions read, but the page text "
                f"shows about {expected}. Please check those dates in the ledger."
            )
        return rows, notes

    def _rows_for(self, pdf_bytes: bytes, span: PageSpan, context: str) -> list:
        """The rows of a span; a reply that is cut off is read again in halves, then page by page."""
        document_part = Part.from_data(data=pages_as_pdf(pdf_bytes, span), mime_type="application/pdf")
        prompt = STATEMENT_PROMPT + pages_instructions(span) + context
        rows = self._ask(document_part, prompt, span.label)
        if rows is not None:
            return rows
        if span.count > 1:
            first_half, second_half = span.halves()
            logger.warning("Statement %s: the reply was cut off; reading %s and %s separately.",
                           span.label, first_half.label, second_half.label)
            return self._rows_for(pdf_bytes, first_half, context) + self._rows_for(pdf_bytes, second_half, context)
        rows = self._ask(document_part, prompt, span.label)  # one more try for a single page
        if rows is None:
            raise ExtractionError(
                f"The AI reader couldn't finish {span.label} of this statement (its reply was cut off twice). "
                "Nothing was imported; please try again."
            )
        return rows

    def _ask(self, document_part, prompt: str, label: str):
        """One Gemini call. The rows it returned, or None if the reply was cut off or isn't JSON."""
        started = time.monotonic()
        for attempt in range(1, CALL_ATTEMPTS + 1):
            try:
                response = self.model.generate_content(
                    [document_part, prompt],
                    generation_config={"response_mime_type": "application/json",
                                       "max_output_tokens": MAX_OUTPUT_TOKENS},
                )
                break
            except _TRANSIENT_ERRORS as e:
                if attempt == CALL_ATTEMPTS:
                    raise ExtractionError(
                        "The AI service is busy right now, so the statement wasn't imported. "
                        "Please try again in a minute."
                    ) from e
                delay = 2 ** attempt
                logger.warning("Statement %s: %s from Vertex AI (attempt %d of %d); retrying in %d s.",
                               label, type(e).__name__, attempt, CALL_ATTEMPTS, delay)
                time.sleep(delay)

        finish = _finish_reason(response)
        if finish == "MAX_TOKENS":
            logger.warning("Statement %s: reply cut off at the output limit after %.1f s.", label, time.monotonic() - started)
            return None
        try:
            text = reply_text(response)
        except ValueError as e:  # blocked, or no answer text
            raise ExtractionError(f"The AI reader returned no answer for {label} of this statement ({e}).") from e
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("Statement %s: reply is not valid JSON (%s, finish reason %s).", label, e, finish)
            return None

        rows = payload.get("transactions") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ExtractionError("Extracted data did not match required schema")
        logger.info("Statement %s: %d rows (finish reason %s, %.1f s).", label, len(rows), finish, time.monotonic() - started)
        return rows
