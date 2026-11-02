# tests/test_statement_bench_fixes.py
"""Regression tests for the 3 bugs + 1 SDK timeout hazard uncovered by StatementBench v1 (DEC-072).

1. `AISpreadsheetImporter` accepts both `{"transactions": [...]}` and bare JSON `[...]` replies.
2. `ReceiptData.category` normalizes `None`, blank strings, and omitted categories to `"Other"`
   (for bank XLSX exports that lack a category column), while rejecting non-string types.
3. `statement_agent.count_row_lines` ignores wrapped Value-Date + Amount + Balance continuation
   lines (e.g. `01/07/25 16007.82 709173.07`) and `Opening/Closing Balance` summary lines so
   multi-page statements are not falsely re-read page by page or flagged with coverage warnings.
4. `gemini.generate_with_timeout` bounds hung `generate_content` calls with `DeadlineExceeded`,
   which `StatementExtractor` and `AISpreadsheetImporter` convert into `ExtractionError`.
"""
import io
import json
import time
from types import SimpleNamespace

import pandas as pd
import pytest
from google.api_core import exceptions as google_exceptions
from pydantic import ValidationError
from pypdf import PdfReader

import ai_importer
import gemini
import statement_agent
from exceptions import ExtractionError
from models import ReceiptData, StatementData
from test_statement_pages import FakePart, make_pdf


def _fake_spreadsheet_db():
    saved = []

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, statement, params=None):
            return SimpleNamespace(fetchone=lambda: None)

    class FakeDB:
        pool = SimpleNamespace(connect=FakeConn, begin=FakeConn)

        def save_transactions_batch(self, data_list, gcs_uri, **kwargs):
            saved.extend(data_list)
            return list(range(len(data_list)))

        def close(self):
            pass

    return FakeDB, saved


def test_receipt_data_normalizes_null_blank_and_missing_categories():
    r_null = ReceiptData(date="2026-04-01", amount=550.0, description="UPI-SHOP-123456789012", category=None)
    r_blank = ReceiptData(date="2026-04-01", amount=550.0, description="UPI-SHOP-123456789012", category="   ")
    r_missing = ReceiptData(date="2026-04-01", amount=550.0, description="UPI-SHOP-123456789012")
    r_kept = ReceiptData(date="2026-04-01", amount=550.0, description="UPI-SHOP-123456789012", category=" Dining ")

    assert r_null.category == "Other"
    assert r_blank.category == "Other"
    assert r_missing.category == "Other"
    assert r_kept.category == "Dining"

    with pytest.raises(ValidationError):
        ReceiptData(date="2026-04-01", amount=550.0, description="UPI-SHOP-123456789012", category=123)


@pytest.mark.parametrize("reply_payload", [
    [
        {"date": "2026-03-05", "amount": 420.50, "description": "UPI/SWIGGY/606412345678", "category": None, "type": "EXPENSE", "confidence": 95, "is_self_transfer": False},
        {"date": "2026-03-31", "amount": 55000.0, "description": "NEFT SALARY CREDIT", "category": "", "type": "INCOME", "confidence": 100, "is_self_transfer": False},
    ],
    {
        "transactions": [
            {"date": "2026-03-05", "amount": 420.50, "description": "UPI/SWIGGY/606412345678", "category": None, "type": "EXPENSE", "confidence": 95, "is_self_transfer": False},
            {"date": "2026-03-31", "amount": 55000.0, "description": "NEFT SALARY CREDIT", "type": "INCOME", "confidence": 100, "is_self_transfer": False},
        ]
    },
])
def test_spreadsheet_importer_accepts_bare_list_and_null_categories(monkeypatch, tmp_path, reply_payload):
    FakeDB, saved = _fake_spreadsheet_db()
    monkeypatch.setattr(ai_importer.vertexai, "init", lambda **kw: None)
    monkeypatch.setattr(
        ai_importer,
        "GenerativeModel",
        lambda name: SimpleNamespace(generate_content=lambda contents, generation_config=None: SimpleNamespace(text=json.dumps(reply_payload))),
    )
    monkeypatch.setattr(ai_importer, "DatabaseManager", FakeDB)

    sheet = tmp_path / "Bank_Statement_2026-03.xlsx"
    pd.DataFrame({"Date": ["2026-03-05", "2026-03-31"], "Withdrawal": [420.50, None], "Deposit": [None, 55000.0]}).to_excel(sheet, index=False)

    res = ai_importer.AISpreadsheetImporter().import_sheet_verbatim(str(sheet))

    assert res["status"] == "success" and res["total"] == 2
    assert res["expenses"] == 1 and res["income"] == 1
    assert [tx.category for tx in saved] == ["Other", "Other"]


@pytest.mark.parametrize("wrapped_or_balance_line", [
    "01/07/25 16007.82 709173.07",
    "01/10/24 69,348.00 3,40,302.11",
    "01-Jan-24 541.00(Dr) 1,27,103.54(Cr)",
    "15/07/25 - 5,100.00 7,09,023.07 Cr",
    "01/11/2025 OPENING BALANCE 1,37,448.55",
    "30/11/2025 Closing Balance 1,43,396.55",
])
def test_count_row_lines_ignores_wrapped_value_dates_and_balance_lines(wrapped_or_balance_line):
    assert statement_agent.count_row_lines(wrapped_or_balance_line) == 0


def test_peacock_valley_wrapped_value_dates_do_not_trigger_page_rereads(monkeypatch, tmp_path):
    p1 = [
        "Date Narration Chq./Ref.No. Value Dt WithdrawalAmt. Deposit Amt. ClosingBalance",
        "01/07/25 NEFT CR-XXNB0145607-PENSION PAYMENT-",
        "KAVYA KAMATH-XXNBN25182781416",
        "XXNBN25182781",
        "416",
        "01/07/25 16007.82 709173.07",
        "02/07/25 UPI-MAMTA-518309931851-GROCERY 518309931851 02/07/25 3398.00 705775.07",
        "03/07/25 NEFT DR-XXMR0001234-HOUSE RENT-",
        "AKASH SAXENA-XXMRN25184999111",
        "03/07/25 24500.00 681275.07",
    ]
    p2 = [
        "Date Narration Chq./Ref.No. Value Dt WithdrawalAmt. Deposit Amt. ClosingBalance",
        "04/07/25 NEFT CR-XXNB0145607-DIVIDEND-",
        "XXNBN25185781999",
        "04/07/25 1250.00 682525.07",
        "05/07/25 POS XXXXXXXXXXXX9940 HOTSTAR 518606698951 05/07/25 1499.00 681026.07",
    ]
    pdf_bytes = make_pdf([p1, p2])
    assert statement_agent.page_row_counts(pdf_bytes) == [3, 2]

    calls = []

    class PvModel:
        def generate_content(self, contents, generation_config=None):
            doc, _prompt = contents
            calls.append(len(PdfReader(io.BytesIO(doc.data)).pages))
            rows = [
                {"date": f"2025-07-0{i}", "amount": 100.0 * i, "description": f"ROW-{i}", "category": "Other",
                 "type": "EXPENSE", "confidence": 95, "is_self_transfer": False}
                for i in range(1, 6)
            ]
            return SimpleNamespace(text=json.dumps({"transactions": rows}))

    monkeypatch.setattr(statement_agent.vertexai, "init", lambda **kw: None)
    monkeypatch.setattr(statement_agent, "Part", FakePart)
    monkeypatch.setattr(statement_agent, "GenerativeModel", lambda name: PvModel())

    pdf_path = tmp_path / "Peacock_2025-07_statement.pdf"
    pdf_path.write_bytes(pdf_bytes)
    extractor = statement_agent.StatementExtractor()
    data = extractor.extract(str(pdf_path))

    assert len(data.transactions) == 5
    assert calls == [2]  # 1 span call only; zero single-page re-reads
    assert extractor.coverage_notes == []


def test_generate_with_timeout_bounds_hung_calls_and_raises_extraction_error(monkeypatch, tmp_path):
    monkeypatch.setattr(gemini, "DEFAULT_CALL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(statement_agent, "CALL_ATTEMPTS", 1)
    monkeypatch.setattr(statement_agent.vertexai, "init", lambda **kw: None)
    monkeypatch.setattr(statement_agent, "Part", FakePart)

    class HungModel:
        def generate_content(self, contents, generation_config=None):
            time.sleep(0.5)
            return SimpleNamespace(text='{"transactions": []}')

    with pytest.raises(google_exceptions.DeadlineExceeded):
        gemini.generate_with_timeout(HungModel(), "hello", timeout_seconds=0.05)

    monkeypatch.setattr(statement_agent, "GenerativeModel", lambda name: HungModel())
    pdf_path = tmp_path / "Hung_2026-04_statement.pdf"
    pdf_path.write_bytes(make_pdf([["01/04/26 UPI-TEST-123456789012 100.00 900.00"]]))
    with pytest.raises(ExtractionError, match="busy"):
        statement_agent.StatementExtractor().extract(str(pdf_path))
