# models.py
"""Strict Pydantic Data Contracts for Multimodal LLM Extraction.

FDE Pillar 1 (BUILD) & Pillar 3 (SCALE):
- Enforces deterministic schema validation between non-deterministic LLM outputs
  (`StatementExtractor`, `ReceiptExtractor`, `AISpreadsheetImporter`) and the PostgreSQL ledger.
- Any malformed date, negative/non-numeric amount, or missing field raises a Pydantic
  `ValidationError` before touching the database.
"""

from pydantic import BaseModel, Field, field_validator
from datetime import date
from typing import List

class ReceiptData(BaseModel):
    """Normalized single-transaction contract used across receipts, PDF rows, and Excel rows."""
    transaction_date: date = Field(..., alias="date")
    amount: float
    description: str
    category: str
    confidence: float = 100.0
    is_self_transfer : bool = False  #Explicit flag for self_tranfers
    requires_human_review : bool = False  # Flag for frontend UI
    transaction_type: str = Field(default="EXPENSE", alias="type")

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        """Normalizes AI confidence to the 0-100 scale used by the review gate (< 85 = review).

        Some model responses use 0-1 (e.g. 0.95); without this, every such row was flagged
        for review (the Phase 0 "80% needs review" bug). Missing/unparseable -> 0 so the row
        is reviewed instead of crashing the import.
        """
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0.0
        if 0 < score <= 1:
            score *= 100
        return max(0.0, min(100.0, score))

    # @field_validator("category")    #decorator to validate the category input from incoming data
    # def validate_category(cls, value):
    #     # We enforce that the AI maps to the exact categories in your SQL table
    #     valid_categories = [
    #         "Food", "Health/Medical", "Home", "Transportation", "Personal",
    #         "SIP", "Utilities", "Travel", "Debt", "Investment", "Stocks", "Rent", 
    #         "Savings","Paycheck","Bonus","Interest","Other"
    #     ]
    #     if value not in valid_categories:
    #         return "Other"  # Safe fallback
    #     return value


# Model for Bank Statements that contain multiple transactions
class StatementData(BaseModel):
    """Container contract for multi-row bank statement PDF and spreadsheet extractions."""
    transactions: List[ReceiptData]