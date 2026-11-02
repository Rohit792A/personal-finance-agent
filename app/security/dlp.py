# app/security/dlp.py
"""Financial Data Loss Prevention (DLP) Pipeline.
FDE Pillar 2: GOVERN.
Redacts and tokenizes PII, PCI-DSS data, and Indian financial identifiers
before sending payloads to cloud LLMs, with reversible de-tokenization for client responses.
"""
import re
import logging
from typing import Dict, Tuple, List, Optional

logger = logging.getLogger(__name__)


class FinancialDLPPipeline:
    """Automated Cloud DLP pipeline for sensitive personal and banking data."""

    PATTERNS = {
        "PAN": (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b"), "[PAN_{idx}]"),
        "IFSC": (re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), "[IFSC_{idx}]"),
        "CARD": (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), "[CARD_{idx}]"),
        "AADHAAR": (re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"), "[AADHAAR_{idx}]"),
        "UPI_VPA": (re.compile(r"\b[a-zA-Z0-9._-]{2,30}@[a-zA-Z]{2,15}\b"), "[UPI_{idx}]"),
        "PHONE": (re.compile(r"\b(?:\+91[\-\s]?)?[6-9]\d{9}\b"), "[PHONE_{idx}]"),
        "EMAIL": (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL_{idx}]"),
        "ACCOUNT_NO": (re.compile(r"\b(?:A/C|Acct|Account)?\s*(?:No\.?)?\s*[:\-]?\s*(\d{9,18})\b", re.IGNORECASE), "[ACCOUNT_{idx}]"),
    }

    def __init__(self):
        pass

    def redact(self, text: str) -> Tuple[str, Dict[str, str]]:
        """Redacts sensitive PII/PCI entities from text and returns a reversible token map.
        
        Returns:
            Tuple of (redacted_text: str, token_map: Dict[str, str])
            where token_map maps '[TOKEN_ID]' -> original sensitive value
        """
        if not text:
            return "", {}

        redacted_text = text
        token_map: Dict[str, str] = {}
        counts: Dict[str, int] = {}

        for entity_type, (regex, token_template) in self.PATTERNS.items():
            matches = list(regex.finditer(redacted_text))
            for match in reversed(matches):
                # If group 1 exists (e.g. account number capture), use it; else full match
                orig_val = match.group(1) if match.lastindex and match.lastindex >= 1 else match.group(0)
                
                # Filter out obvious false positives (e.g. standard dates or small numbers)
                if entity_type == "ACCOUNT_NO" and (len(orig_val) < 9 or orig_val.startswith("202") and len(orig_val) == 8):
                    continue
                if entity_type == "UPI_VPA" and "@" not in orig_val:
                    continue

                counts[entity_type] = counts.get(entity_type, 0) + 1
                token_placeholder = token_template.format(idx=counts[entity_type])
                token_map[token_placeholder] = orig_val

                # Replace in string
                start, end = (match.start(1), match.end(1)) if match.lastindex and match.lastindex >= 1 else (match.start(), match.end())
                redacted_text = redacted_text[:start] + token_placeholder + redacted_text[end:]

        if token_map:
            logger.info(f"[Cloud DLP] Redacted {len(token_map)} sensitive financial entities: {list(counts.keys())}")

        return redacted_text, token_map

    def restore(self, text: str, token_map: Dict[str, str]) -> str:
        """Restores redacted tokens back to their original values for client presentation."""
        if not text or not token_map:
            return text

        restored_text = text
        for token_placeholder, orig_val in token_map.items():
            restored_text = restored_text.replace(token_placeholder, orig_val)

        return restored_text

    @staticmethod
    def redact_text(text: str, preserve_reference_numbers: bool = True):
        """Redacts PII from a single description string while preserving 12-digit UPI/IMPS references."""
        from types import SimpleNamespace

        dlp = FinancialDLPPipeline()
        doc_patterns = {
            k: v for k, v in dlp.PATTERNS.items()
            if not (preserve_reference_numbers and k == "ACCOUNT_NO")
        }
        out = str(text or "")
        masked = 0
        for _entity_type, (regex, token_template) in doc_patterns.items():
            matches = list(regex.finditer(out))
            for idx, match in enumerate(reversed(matches), start=1):
                start, end = (
                    (match.start(1), match.end(1))
                    if match.lastindex and match.lastindex >= 1
                    else (match.start(), match.end())
                )
                out = out[:start] + token_template.format(idx=idx) + out[end:]
                masked += 1
        return SimpleNamespace(sanitized_text=out, redacted_count=masked)

    def redact_transaction_descriptions(
        self_or_transactions,
        transactions: Optional[list] = None,
        preserve_reference_numbers: bool = True,
    ):
        """Redacts PAN, Aadhaar, Card numbers, IFSC, Phone, and Email from extracted document rows.

        Preserves 12-digit UPI/IMPS reference numbers (RRN) when `preserve_reference_numbers=True`
        so cross-source reconciliation (`database.extract_reference_numbers`) remains exact.
        Returns `(redacted_txs, redacted_rows_count)`.
        """
        if isinstance(self_or_transactions, FinancialDLPPipeline):
            dlp = self_or_transactions
            tx_list = transactions or []
        else:
            dlp = FinancialDLPPipeline()
            tx_list = self_or_transactions or []

        redacted_rows = 0
        doc_patterns = {
            k: v for k, v in dlp.PATTERNS.items()
            if not (preserve_reference_numbers and k == "ACCOUNT_NO")
        }
        out_txs = []
        for tx in tx_list:
            desc = getattr(tx, "description", None) if not isinstance(tx, dict) else tx.get("description")
            if not desc:
                out_txs.append(tx)
                continue
            text = str(desc)
            row_masked = 0
            for _entity_type, (regex, token_template) in doc_patterns.items():
                matches = list(regex.finditer(text))
                for idx, match in enumerate(reversed(matches), start=1):
                    start, end = (
                        (match.start(1), match.end(1))
                        if match.lastindex and match.lastindex >= 1
                        else (match.start(), match.end())
                    )
                    text = text[:start] + token_template.format(idx=idx) + text[end:]
                    row_masked += 1
            if row_masked > 0:
                redacted_rows += 1
                if isinstance(tx, dict):
                    tx = dict(tx)
                    tx["description"] = text
                else:
                    tx.description = text
            out_txs.append(tx)
        return out_txs, redacted_rows

