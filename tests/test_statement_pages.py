# tests/test_statement_pages.py
"""Long bank statements are read a few pages per Gemini call (DEC-060).

A 7-month Kotak statement (361 rows) came back from one call cut off mid-JSON, and a 12-month one
(971 rows) came back with only its first 122 rows. These tests drive `StatementExtractor` with a
fake model that reads the attached pages' text like a perfect reader (and can be told to cut off,
come up short or fail), on small PDFs built here. The Bank Sync retry path is tested with a fake
database. No Google credentials or network are needed.
"""
import contextlib
import io
import json
import re
import threading
from types import SimpleNamespace

import pytest
from google.api_core import exceptions as google_exceptions
from pypdf import PdfReader
from vertexai.generative_models import GenerationResponse

import statement_agent
from exceptions import ExtractionError

ROW_LINE = re.compile(r"^(\d+) (\d{2}) Apr 2026 (\S+) UPI-\d+ ([\d,]+\.\d{2}) [\d,]+\.\d{2}$")


# ---------------------------------------------------------------------------------------------
# Synthetic statements
# ---------------------------------------------------------------------------------------------

def make_pdf(pages) -> bytes:
    """A small PDF whose pages carry the given text lines (Helvetica), readable by pypdf."""
    def esc(text):
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    page_ids = [5 + 2 * i for i in range(len(pages))]
    bodies = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{p} 0 R" for p in page_ids), len(pages))).encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, lines in enumerate(pages):
        ops = ["BT", "/F1 9 Tf", "12 TL", "30 810 Td"] + [f"({esc(line)}) Tj T*" for line in lines] + ["ET"]
        stream = "\n".join(ops).encode()
        bodies[4 + 2 * i] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        bodies[5 + 2 * i] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                             f"/Resources << /Font << /F1 3 0 R >> >> /Contents {4 + 2 * i} 0 R >>").encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for obj_id in sorted(bodies):
        offsets[obj_id] = len(out)
        out += b"%d 0 obj\n" % obj_id + bodies[obj_id] + b"\nendobj\n"
    xref = len(out)
    size = max(bodies) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for obj_id in range(1, size):
        out += b"%010d 00000 n \n" % offsets[obj_id]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return bytes(out)


def statement_pages(page_count: int, rows_per_page: int):
    """Kotak-style pages: a header, then numbered rows with a 12-digit reference each."""
    pages, serial = [], 0
    for page in range(page_count):
        lines = ["Savings Account Transactions", "# Date Description Chq/Ref. No. Withdrawal (Dr.) Deposit (Cr.) Balance"]
        if page == 0:
            lines.append("- - Opening Balance - - - 9,999.00")
        for _ in range(rows_per_page):
            serial += 1
            lines.append(f"{serial} {min(serial, 28):02d} Apr 2026 UPI/SHOP{serial}/{100000000000 + serial}/Tea "
                         f"UPI-{serial} {serial}.00 9,000.00")
        pages.append(lines)
    return pages


def write_statement(tmp_path, page_count, rows_per_page, name="Kotak_Bank_2026-04_statement.pdf"):
    path = tmp_path / name
    path.write_bytes(make_pdf(statement_pages(page_count, rows_per_page)))
    return path


# ---------------------------------------------------------------------------------------------
# A fake Gemini that reads the attached pages
# ---------------------------------------------------------------------------------------------

class FakePart:
    """Stands in for vertexai's Part: keeps the bytes so the fake model can read them."""

    def __init__(self, data, mime_type):
        self.data, self.mime_type = data, mime_type

    @staticmethod
    def from_data(data, mime_type):
        return FakePart(data, mime_type)

    @staticmethod
    def from_uri(uri, mime_type):
        return FakePart(uri.encode(), mime_type)


def cut_off_reply():
    """A real SDK reply that stopped at the output limit (half a JSON document)."""
    return GenerationResponse.from_dict({"candidates": [{
        "content": {"role": "model", "parts": [{"text": '{"transactions": [{"date": "2026-04-0'}]},
        "finish_reason": "MAX_TOKENS",
    }]})


class PageReadingModel:
    """Returns one row per row line on the attached pages, in order.

    `behaviour(first_page, page_count, rows)` may change the answer: return a list of rows, the
    string "cut" (a reply cut off at the output limit), "broken" (invalid JSON) or an exception
    instance to raise.
    """

    def __init__(self, behaviour=None):
        self.behaviour = behaviour or (lambda first, count, rows: rows)
        self.calls = []  # (first page number, page count, prompt)
        self.lock = threading.Lock()

    def generate_content(self, contents, generation_config=None):
        document, prompt = contents
        assert generation_config == {"response_mime_type": "application/json",
                                     "max_output_tokens": statement_agent.MAX_OUTPUT_TOKENS}
        reader = PdfReader(io.BytesIO(document.data))
        rows = []
        first_serial = None
        for page in reader.pages:
            for line in (page.extract_text() or "").splitlines():
                match = ROW_LINE.match(line.strip())
                if match:
                    serial = int(match.group(1))
                    first_serial = serial if first_serial is None else first_serial
                    rows.append({"date": f"2026-04-{match.group(2)}", "amount": float(match.group(4).replace(",", "")),
                                 "description": match.group(3), "category": "Food", "type": "EXPENSE",
                                 "confidence": 95, "is_self_transfer": False})
        span = re.search(r"pages? (\d+)(?:-(\d+))? of a \d+-page", prompt)
        first_page = int(span.group(1)) if span else 1
        with self.lock:
            self.calls.append((first_page, len(reader.pages), prompt))
        answer = self.behaviour(first_page, len(reader.pages), rows)
        if isinstance(answer, Exception):
            raise answer
        if answer == "cut":
            return cut_off_reply()
        if answer == "broken":
            return SimpleNamespace(text='{"transactions": [ {"date": ')
        return SimpleNamespace(text=json.dumps({"transactions": answer}))


@pytest.fixture
def reader_with(monkeypatch):
    """Builds a StatementExtractor around a PageReadingModel (2 pages per call, 3 at once)."""
    monkeypatch.setattr(statement_agent.vertexai, "init", lambda **kwargs: None)
    monkeypatch.setattr(statement_agent, "Part", FakePart)
    monkeypatch.setattr(statement_agent, "PAGES_PER_CALL", 2)
    monkeypatch.setattr(statement_agent, "PARALLEL_CALLS", 3)
    monkeypatch.setattr(statement_agent.time, "sleep", lambda seconds: None)

    def build(behaviour=None):
        model = PageReadingModel(behaviour)
        monkeypatch.setattr(statement_agent, "GenerativeModel", lambda name: model)
        return statement_agent.StatementExtractor(), model

    return build


def descriptions(data):
    return [t.description for t in data.transactions]


# ---------------------------------------------------------------------------------------------
# Reading a statement span by span
# ---------------------------------------------------------------------------------------------

def test_long_statement_is_read_a_few_pages_per_call_in_page_order(reader_with, tmp_path):
    agent, model = reader_with()
    data = agent.extract(str(write_statement(tmp_path, page_count=5, rows_per_page=3)))

    assert descriptions(data) == [f"UPI/SHOP{n}/{100000000000 + n}/Tea" for n in range(1, 16)]
    assert sorted((first, count) for first, count, _ in model.calls) == [(1, 2), (3, 2), (5, 1)]
    prompts = {first: prompt for first, _, prompt in model.calls}
    assert "pages 1-2 of a 5-page bank statement" in prompts[1]
    assert "page 5 of a 5-page bank statement" in prompts[5]
    assert all("Do not skip rows, stop early or summarise" in p for p in prompts.values())
    assert agent.pages_read == 5 and agent.coverage_notes == []


def test_a_short_statement_is_still_read_in_one_call_with_the_original_prompt(reader_with, tmp_path):
    agent, model = reader_with()
    data = agent.extract(str(write_statement(tmp_path, page_count=2, rows_per_page=4)))

    assert len(data.transactions) == 8
    assert len(model.calls) == 1 and model.calls[0][1] == 2
    assert "bank statement; the other pages" not in model.calls[0][2]
    assert "Extract EVERY transaction you can find in the document." in model.calls[0][2]


def test_a_cut_off_reply_is_read_again_in_halves(reader_with, tmp_path):
    agent, model = reader_with(lambda first, count, rows: "cut" if count > 1 else rows)
    data = agent.extract(str(write_statement(tmp_path, page_count=5, rows_per_page=3)))

    assert descriptions(data) == [f"UPI/SHOP{n}/{100000000000 + n}/Tea" for n in range(1, 16)]
    # pages 1-2 and 3-4 were cut off and read page by page; page 5 fitted the first time
    assert sorted((first, count) for first, count, _ in model.calls) == [
        (1, 1), (1, 2), (2, 1), (3, 1), (3, 2), (4, 1), (5, 1)]
    assert agent.coverage_notes == []


def test_a_reply_that_is_not_json_is_read_again_in_halves(reader_with, tmp_path):
    agent, _model = reader_with(lambda first, count, rows: "broken" if count > 1 else rows)
    data = agent.extract(str(write_statement(tmp_path, page_count=4, rows_per_page=3)))
    assert len(data.transactions) == 12


def test_a_page_that_never_fits_stops_the_whole_import(reader_with, tmp_path):
    agent, model = reader_with(lambda first, count, rows: "cut" if first == 3 else rows)
    with pytest.raises(ExtractionError, match="page 3 of this statement"):
        agent.extract(str(write_statement(tmp_path, page_count=4, rows_per_page=3)))
    # pages 3-4 were halved, page 3 tried twice
    assert sum(1 for first, count, _ in model.calls if (first, count) == (3, 1)) == 2


def test_a_short_reply_is_read_again_page_by_page(reader_with, tmp_path):
    agent, model = reader_with(lambda first, count, rows: rows[:1] if count > 1 else rows)
    data = agent.extract(str(write_statement(tmp_path, page_count=4, rows_per_page=6)))

    assert descriptions(data) == [f"UPI/SHOP{n}/{100000000000 + n}/Tea" for n in range(1, 25)]
    assert agent.coverage_notes == []
    assert sorted((first, count) for first, count, _ in model.calls) == [
        (1, 1), (1, 2), (2, 1), (3, 1), (3, 2), (4, 1)]


def test_a_gap_that_remains_is_reported(reader_with, tmp_path):
    agent, _model = reader_with(lambda first, count, rows: rows[:1])
    data = agent.extract(str(write_statement(tmp_path, page_count=2, rows_per_page=6)))

    assert len(data.transactions) == 2  # the page-by-page reading found one row per page
    assert agent.coverage_notes == [
        "Pages 1-2 of the statement: 2 transactions read, but the page text shows about 12. "
        "Please check those dates in the ledger."
    ]


def test_a_reply_within_the_allowed_gap_is_accepted(reader_with, tmp_path):
    agent, model = reader_with(lambda first, count, rows: rows[:-1])
    data = agent.extract(str(write_statement(tmp_path, page_count=2, rows_per_page=6)))
    assert len(data.transactions) == 11 and len(model.calls) == 1 and agent.coverage_notes == []


def test_busy_vertex_ai_is_retried(reader_with, tmp_path):
    attempts = []

    def busy_once(first, count, rows):
        attempts.append(first)
        return google_exceptions.ResourceExhausted("quota") if len(attempts) == 1 else rows

    agent, model = reader_with(busy_once)
    data = agent.extract(str(write_statement(tmp_path, page_count=2, rows_per_page=3)))
    assert len(data.transactions) == 6 and len(model.calls) == 2


def test_vertex_ai_busy_on_every_attempt_fails_cleanly(reader_with, tmp_path):
    agent, model = reader_with(lambda first, count, rows: google_exceptions.ServiceUnavailable("down"))
    with pytest.raises(ExtractionError, match="busy"):
        agent.extract(str(write_statement(tmp_path, page_count=2, rows_per_page=3)))
    assert len(model.calls) == statement_agent.CALL_ATTEMPTS


def test_a_file_that_cannot_be_split_is_read_whole(reader_with, tmp_path):
    agent, model = reader_with(lambda first, count, rows: rows)
    model.generate_content = lambda contents, generation_config=None: (
        model.calls.append(contents[1]) or SimpleNamespace(text=json.dumps({"transactions": []})))
    pdf = tmp_path / "Kotak statement April 2026.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really a pdf")

    assert agent.extract(str(pdf)).transactions == []
    assert len(model.calls) == 1 and "bank statement; the other pages are read separately" not in model.calls[0]
    assert agent.pages_read == 0


def test_dates_are_still_checked_after_reading_in_parts(reader_with, tmp_path):
    def wrong_year(first, count, rows):
        return [dict(row, date=row["date"].replace("2026", "2023")) for row in rows]

    agent, _model = reader_with(wrong_year)
    data = agent.extract(str(write_statement(tmp_path, page_count=3, rows_per_page=2,
                                             name="Kotak statement April 2026.pdf")))
    assert {t.transaction_date.year for t in data.transactions} == {2026}
    assert agent.date_notes


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "12 01 Apr 2026 UPI/HungerBox/123456789012/Hungerbox",   # Kotak (serial number, then date)
    "01/04/26 UPI-SWIGGY-SWIGGY@ICICI-123456789012",        # HDFC
    "01-04-2026 01-04-2026 BY TRANSFER-UPI/CR/123456789012",  # SBI
    "1 Apr 2026 1 Apr 2026 TO TRANSFER-UPI/DR/123456789012",
    "01-Apr-26 NEFT CR-HDFC0000001-EMPLOYER",
    "05.04.2026 ATM WDL 1234",
])
def test_row_lines_of_common_statement_layouts_are_counted(line):
    assert statement_agent.count_row_lines(line) == 1


@pytest.mark.parametrize("line", [
    "# Date Description Chq/Ref. No. Withdrawal (Dr.) Deposit (Cr.) Balance",
    "- - Opening Balance - - - 9,999.99",
    "Order",
    "UPI-123456789012 99.99 99,999.99",
    "Statement period 01 Apr 2026 to 31 Oct 2026",
    "01/04/26",
])
def test_other_lines_are_not_counted(line):
    assert statement_agent.count_row_lines(line) == 0


def test_page_spans():
    spans = statement_agent.split_into_spans(5, 2)
    assert [(s.first, s.last) for s in spans] == [(1, 2), (3, 4), (5, 5)]
    assert [s.label for s in spans] == ["pages 1-2", "pages 3-4", "page 5"]
    first, second = statement_agent.PageSpan(3, 6, 9).halves()
    assert (first.first, first.last, second.first, second.last) == (3, 4, 5, 6)
    assert statement_agent.PageSpan(1, 3, 3).whole and not spans[0].whole
    assert statement_agent.allowed_gap(10) == 2 and statement_agent.allowed_gap(100) == 5


def test_page_row_counts_reads_each_page_and_rejects_non_pdfs(tmp_path):
    pdf = make_pdf(statement_pages(3, 4))
    assert statement_agent.page_row_counts(pdf) == [4, 4, 4]
    assert statement_agent.page_row_counts(b"%PDF-1.4 not really a pdf") is None
    part = statement_agent.pages_as_pdf(pdf, statement_agent.PageSpan(2, 3, 3))
    assert len(PdfReader(io.BytesIO(part)).pages) == 2


# ---------------------------------------------------------------------------------------------
# Password-protected PDFs: Gemini answers them with 400 INVALID_ARGUMENT (the 06:18 uploads)
# ---------------------------------------------------------------------------------------------

def encrypt(pdf_bytes: bytes, user_password: str) -> bytes:
    """`user_password` "" gives a PDF that opens without a password (owner restrictions only)."""
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes)))
    writer.encrypt(user_password=user_password, owner_password="owner-only", algorithm="AES-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_locked_and_owner_only_pdfs_are_told_apart():
    pdf = make_pdf(statement_pages(3, 4))
    locked, owner_only = encrypt(pdf, "1234"), encrypt(pdf, "")
    assert statement_agent.pdf_needs_password(locked)
    assert not statement_agent.pdf_needs_password(owner_only) and not statement_agent.pdf_needs_password(pdf)
    assert not statement_agent.pdf_needs_password(b"%PDF-1.4 not really a pdf")
    # An owner-only PDF opens without a password, so it is still counted and split page by page.
    assert statement_agent.page_row_counts(owner_only) == [4, 4, 4]
    part = statement_agent.pages_as_pdf(owner_only, statement_agent.PageSpan(1, 2, 3))
    assert statement_agent.page_row_counts(part) == [4, 4]
    assert statement_agent.page_row_counts(locked) is None


def test_decrypt_pdf_uses_the_first_password_that_opens_it():
    pdf = make_pdf(statement_pages(2, 3))
    locked = encrypt(pdf, "1234")
    unlocked = statement_agent.decrypt_pdf(locked, ["", "wrong", "1234", "later"])
    assert unlocked and not statement_agent.pdf_needs_password(unlocked)
    assert statement_agent.page_row_counts(unlocked) == [3, 3]
    assert statement_agent.decrypt_pdf(locked, ["wrong"]) is None
    assert statement_agent.decrypt_pdf(pdf, []) == pdf  # not locked: unchanged


def test_a_locked_pdf_never_reaches_gemini(reader_with, tmp_path):
    agent, model = reader_with()
    path = tmp_path / "Acct Statement_1234_01112026_11.47.08.pdf"
    path.write_bytes(encrypt(make_pdf(statement_pages(2, 3)), "1234"))
    with pytest.raises(statement_agent.PasswordRequiredError, match="password-protected"):
        agent.extract(str(path))
    assert model.calls == []


def test_upload_unlock_uses_the_given_password_then_the_saved_ones(monkeypatch, tmp_path):
    import main

    pdf = make_pdf(statement_pages(2, 3))
    path = tmp_path / "Acct Statement_1234.pdf"
    saved = []
    monkeypatch.setattr(main, "_saved_statement_passwords", lambda: list(saved))

    path.write_bytes(pdf)  # not locked: left alone
    assert main._unlock_for_reading(str(path)) is False and path.read_bytes() == pdf

    path.write_bytes(encrypt(pdf, "1234"))
    with pytest.raises(main.PasswordRequiredError, match="password-protected"):
        main._unlock_for_reading(str(path))
    with pytest.raises(main.PasswordRequiredError, match="doesn't open this PDF"):
        main._unlock_for_reading(str(path), "wrong")
    assert main._unlock_for_reading(str(path), "1234") is True
    assert statement_agent.page_row_counts(path.read_bytes()) == [3, 3]

    path.write_bytes(encrypt(pdf, "5678"))
    saved.append("5678")  # a statement password saved for Bank Sync
    assert main._unlock_for_reading(str(path)) is True
    assert not statement_agent.pdf_needs_password(path.read_bytes())


def test_upload_route_passes_the_password_and_explains_a_locked_pdf(monkeypatch):
    from fastapi.testclient import TestClient

    import api
    import auth
    import config

    monkeypatch.setattr(auth, "authenticate_user",
                        lambda token: auth.AuthUser(uid="owner", email="owner@example.com", provider="google.com"))
    monkeypatch.setattr(config, "AI_DAILY_LIMITS", {})

    class NoDatabase:  # the ledger count before/after is best effort
        pool = SimpleNamespace(connect=lambda: (_ for _ in ()).throw(RuntimeError("no database in tests")))

        def close(self):
            pass

    seen = {}

    def locked(path, pdf_password=""):
        seen["password"] = pdf_password
        raise statement_agent.PasswordRequiredError(statement_agent.LOCKED_PDF_MESSAGE)

    monkeypatch.setattr(api, "DatabaseManager", NoDatabase)
    monkeypatch.setattr(api, "process_file", locked)
    resp = TestClient(api.app).post(
        "/upload", files={"file": ("Acct Statement_1234.pdf", b"%PDF-1.4 locked", "application/pdf")},
        data={"password": "1234"}, headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 400 and "password-protected" in resp.json()["detail"]
    assert seen["password"] == "1234"


# ---------------------------------------------------------------------------------------------
# Bank Sync: Unlock & Sync / Retry import (gmail_sync.unlock_and_sync_statement)
# ---------------------------------------------------------------------------------------------

class RecordingConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.db.executed.append((sql, dict(params or {})))
        if sql.startswith("SELECT id, file_name, file_path, bank_name, month_key"):
            return SimpleNamespace(fetchone=lambda: (11, "Kotak_Bank_2026-10_statement.pdf",
                                                     "gs://bucket/2026-10/Kotak_Bank_2026-10_statement.pdf",
                                                     "Kotak Mahindra Bank", "2026-10"))
        return SimpleNamespace(fetchone=lambda: None)


class RecordingDatabase:
    def __init__(self):
        self.executed = []
        self.pool = SimpleNamespace(connect=lambda: RecordingConnection(self), begin=lambda: RecordingConnection(self))

    def statements(self, prefix):
        return [(sql, params) for sql, params in self.executed if sql.startswith(prefix)]


@pytest.fixture
def bank_sync(monkeypatch, tmp_path):
    import gmail_sync
    import main

    local = tmp_path / "Kotak_Bank_2026-10_statement.pdf"
    local.write_bytes(b"%PDF-1.4 unlocked copy")

    @contextlib.contextmanager
    def local_copy(location):
        yield str(local)

    replaced = []
    monkeypatch.setattr(gmail_sync.statement_store, "exists", lambda location: True)
    monkeypatch.setattr(gmail_sync.statement_store, "local_copy", local_copy)
    monkeypatch.setattr(gmail_sync.statement_store, "replace", lambda location, path: replaced.append(location))

    service = gmail_sync.GmailBankSyncService.__new__(gmail_sync.GmailBankSyncService)
    service.db = RecordingDatabase()

    def configure(process_result=None, encrypted=False, password_ok=True):
        monkeypatch.setattr(service, "_check_and_unlock_pdf",
                            lambda path, password: (encrypted, (not encrypted) or (password_ok and bool(password))))

        def fake_process_file(path):
            if isinstance(process_result, Exception):
                raise process_result
            return process_result

        monkeypatch.setattr(main, "process_file", fake_process_file)
        return service

    configure.replaced = replaced
    return configure


def test_a_failed_import_marks_the_statement_for_retry(bank_sync):
    service = bank_sync(ExtractionError("The AI reader couldn't finish page 3 of this statement"), encrypted=True)

    with pytest.raises(ExtractionError):
        service.unlock_and_sync_statement(11, "secret")

    marks = service.db.statements("UPDATE exported_statement_pdfs SET is_encrypted = FALSE, sync_status = :status")
    assert [params for _sql, params in marks] == [{"status": "IMPORT_FAILED", "id": 11}]
    assert bank_sync.replaced  # the unlocked copy replaced the stored one, so a retry needs no password


def test_retry_without_password_imports_and_reports_the_counts(bank_sync):
    service = bank_sync({"status": "success",
                         "messages": ["Found 361 transactions in the PDF (13 pages).",
                                      "174 of 361 transactions were already in your ledger, so they weren't added again."],
                         "summary": {"rows": 361, "added": 187, "already": 174, "conflicts": 0, "repeated_in_file": 0}})

    result = service.unlock_and_sync_statement(11, "")

    assert result["transactions_imported"] == 361 and result["added"] == 187 and result["already_in_ledger"] == 174
    assert result["message"] == ("Imported Kotak_Bank_2026-10_statement.pdf: read 361 transactions, "
                                 "187 added to your ledger, 174 were already in it.")
    assert len(result["notifications"]) == 2
    (sql, params), = service.db.statements("UPDATE exported_statement_pdfs SET is_encrypted = FALSE, sync_status = 'IMPORTED_AND_RECONCILED'")
    assert "GREATEST" in sql and params == {"cnt": 361, "id": 11}
    assert not bank_sync.replaced  # nothing to unlock


def test_an_already_imported_file_keeps_its_count_and_says_so(bank_sync):
    note = "'Kotak_Bank_2026-10_statement.pdf' was already imported on 01 Nov 2026 (361 transactions), so nothing was added again."
    service = bank_sync({"status": "success", "messages": [note]})

    result = service.unlock_and_sync_statement(11, "")

    assert result["message"] == note and result["transactions_imported"] == 0
    (_sql, params), = service.db.statements("UPDATE exported_statement_pdfs SET is_encrypted = FALSE, sync_status = 'IMPORTED_AND_RECONCILED'")
    assert params["cnt"] == 0  # GREATEST keeps the stored count


def test_a_locked_statement_still_needs_its_password(bank_sync):
    service = bank_sync({"status": "success", "messages": []}, encrypted=True)
    with pytest.raises(ValueError, match="password-protected"):
        service.unlock_and_sync_statement(11, "")
    with pytest.raises(ValueError, match="Incorrect PDF password"):
        bank_sync({"status": "success", "messages": []}, encrypted=True, password_ok=False).unlock_and_sync_statement(11, "wrong")
    assert not service.db.statements("UPDATE exported_statement_pdfs")
