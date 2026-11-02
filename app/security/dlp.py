# app/security/dlp.py
"""Financial Data Loss Prevention (DLP) Pipeline.
FDE Pillar 2: GOVERN.
Redacts and tokenizes PII, PCI-DSS data, and Indian financial identifiers
before sending payloads to cloud LLMs, with reversible de-tokenization for client responses.

Two layers (DEC-063):
1. Local regex patterns (`PATTERNS`), always first. They call no Google API.
2. Google Cloud Sensitive Data Protection (`sdp_api.py`, the Cloud DLP API's `inspect_content`),
   only when `SDP_ENABLED=1`. It inspects the text the regex layer already redacted, and each new
   finding gets the same kind of `[TYPE_n]` token (e.g. `[PAN_2]`, `[PHONE_3]`), so `restore()`
   de-tokenizes replies exactly as before. If the API call fails, the regex result stands.
Document rows (`redact_text`, `redact_texts`, `redact_transaction_descriptions`) keep 12-digit
UPI/IMPS reference numbers when `preserve_reference_numbers=True`: the regex layer skips its
account-number pattern, and the SDP layer requests no bank-account type and drops any finding
that touches a reference number (`reference_number_spans`), because SDP's Aadhaar and phone
detectors can match those numbers and reconciliation needs them.
`last_layer` (regex | regex+sdp_api | regex_fallback) records which layers ran, for the traces.
"""
import re
import logging
from types import SimpleNamespace
from typing import Dict, Tuple, List, Optional

from . import sdp_api

logger = logging.getLogger(__name__)

# The numbers reconciliation reads, which document redaction must leave intact:
# - 12-digit UPI/IMPS reference numbers (RRN); same pattern as database._REFERENCE_NUMBER, used by
#   database.extract_reference_numbers for import-time dedupe (DEC-034);
# - 9-12 digit reference tokens read by A2AOrchestrator._extract_reference_id (RECONCILE_BATCH).
REFERENCE_NUMBER_PATTERN = re.compile(r"(?<!\d)\d{12}(?!\d)")
RECONCILIATION_REFERENCE_PATTERN = re.compile(r"\b(\d{9,12})\b")
# A token written by either layer, e.g. "[PAN_1]" or "[UPI_12]".
_TOKEN_PATTERN = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")
_TOKEN_PARTS = re.compile(r"^\[(?P<label>[A-Z][A-Z0-9_]*)_(?P<idx>\d+)\]$")
# Token label for each Sensitive Data Protection infoType; other types use their own name.
SDP_TOKEN_LABELS = {
    "INDIA_PAN_INDIVIDUAL": "PAN",
    "INDIA_AADHAAR_INDIVIDUAL": "AADHAAR",
    "CREDIT_CARD_NUMBER": "CARD",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    sdp_api.UPI_INFO_TYPE: "UPI",
    sdp_api.BANK_ACCOUNT_INFO_TYPE: "ACCOUNT",
}


def reference_number_spans(text: str) -> List[Tuple[int, int]]:
    """Spans of the reference numbers that document redaction must not touch."""
    spans = [m.span() for m in REFERENCE_NUMBER_PATTERN.finditer(text)]
    spans += [m.span(1) for m in RECONCILIATION_REFERENCE_PATTERN.finditer(text)]
    return spans


def _overlaps(start: int, end: int, spans) -> bool:
    return any(start < s_end and s_start < end for s_start, s_end in spans)


def _token_label(info_type: str) -> str:
    return SDP_TOKEN_LABELS.get(info_type) or re.sub(r"[^A-Z0-9]+", "_", info_type.upper()).strip("_") or "PII"


def tokenize_findings(text: str, findings, token_map: Dict[str, str],
                      protected_spans=()) -> Tuple[str, int]:
    """Replaces Sensitive Data Protection findings with `[TYPE_n]` tokens, adding them to `token_map`.

    Findings that overlap an existing token or a protected span are skipped; of overlapping
    findings the longest wins. Numbering continues after the highest index already used for that
    type, in `token_map` or in the text. Returns (new_text, tokens_added).
    """
    existing = [m.span() for m in _TOKEN_PATTERN.finditer(text)]
    blocked = existing + list(protected_spans)
    candidates = [f for f in findings if 0 <= f.start < f.end <= len(text) and not _overlaps(f.start, f.end, blocked)]
    accepted = []
    for finding in sorted(candidates, key=lambda f: (-(f.end - f.start), f.start)):
        if not _overlaps(finding.start, finding.end, [(a.start, a.end) for a in accepted]):
            accepted.append(finding)
    if not accepted:
        return text, 0

    counters: Dict[str, int] = {}
    for token in list(token_map) + [m.group(0) for m in _TOKEN_PATTERN.finditer(text)]:
        parts = _TOKEN_PARTS.match(token)
        if parts:
            counters[parts["label"]] = max(counters.get(parts["label"], 0), int(parts["idx"]))
    planned = []
    for finding in sorted(accepted, key=lambda f: f.start):
        label = _token_label(finding.info_type)
        counters[label] = counters.get(label, 0) + 1
        planned.append((finding, f"[{label}_{counters[label]}]"))
    for finding, token in reversed(planned):
        token_map[token] = text[finding.start:finding.end]
        text = text[:finding.start] + token + text[finding.end:]
    return text, len(planned)


class FinancialDLPPipeline:
    """PII redaction for prompts: local regex patterns with reversible tokens, plus Google Cloud
    Sensitive Data Protection findings when SDP_ENABLED=1 (DEC-063)."""

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
        # Which layers handled the last redact() call (DEC-063), for the trace spans.
        self.last_layer = "regex"
        self.last_sdp_findings = 0
        self.last_sdp_error = ""

    def trace_attributes(self) -> Dict[str, object]:
        """Trace-span attributes for the last redact() call."""
        attrs: Dict[str, object] = {"decision_layer": self.last_layer}
        if self.last_layer == "regex+sdp_api":
            attrs["sdp.entities_added"] = self.last_sdp_findings
        if self.last_sdp_error:
            attrs["sdp.api_error"] = self.last_sdp_error
        return attrs

    def redact(self, text: str) -> Tuple[str, Dict[str, str]]:
        """Redacts sensitive PII/PCI entities from text and returns a reversible token map.

        Regex patterns first; with SDP_ENABLED=1, Sensitive Data Protection findings in the
        regex-redacted text are tokenized too (DEC-063).

        Returns:
            Tuple of (redacted_text: str, token_map: Dict[str, str])
            where token_map maps '[TOKEN_ID]' -> original sensitive value
        """
        self.last_layer, self.last_sdp_findings, self.last_sdp_error = "regex", 0, ""
        redacted_text, token_map = self._regex_redact(text)
        if not text or not sdp_api.is_enabled():
            return redacted_text, token_map
        try:
            findings = sdp_api.inspect_texts([redacted_text], include_bank_accounts=True)[0]
        except sdp_api.SdpUnavailable as e:
            self.last_layer, self.last_sdp_error = "regex_fallback", str(e)
            logger.warning(f"[Cloud DLP] Sensitive Data Protection unavailable ({e}); keeping the regex redaction.")
            return redacted_text, token_map
        redacted_text, added = tokenize_findings(redacted_text, findings, token_map)
        self.last_layer, self.last_sdp_findings = "regex+sdp_api", added
        if added:
            logger.info(f"[Cloud DLP] Sensitive Data Protection tokenized {added} more entities.")
        return redacted_text, token_map

    def _regex_redact(self, text: str) -> Tuple[str, Dict[str, str]]:
        """The regex layer of `redact()` (unchanged behaviour before DEC-063)."""
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
    def _regex_redact_document_text(text: str, preserve_reference_numbers: bool) -> Tuple[str, int]:
        """The regex layer for one document row: (redacted_text, entities_masked).

        Shared by `redact_texts` and `redact_transaction_descriptions` (identical to the loop both
        ran before DEC-063). Skips the account-number pattern when preserving reference numbers.
        """
        doc_patterns = {
            k: v for k, v in FinancialDLPPipeline.PATTERNS.items()
            if not (preserve_reference_numbers and k == "ACCOUNT_NO")
        }
        out = text
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
        return out, masked

    @staticmethod
    def _sdp_document_layer(texts: List[str], preserve_reference_numbers: bool):
        """Sensitive Data Protection on regex-redacted rows (one batched call).

        Returns (new_texts, added_counts, layer); with the API off or failing, the texts come back
        unchanged with layer "regex" / "regex_fallback". Preserving reference numbers drops every
        finding that touches one and leaves out the bank-account type.
        """
        if not sdp_api.is_enabled():
            return texts, [0] * len(texts), "regex"
        try:
            findings = sdp_api.inspect_texts(texts, include_bank_accounts=not preserve_reference_numbers)
        except sdp_api.SdpUnavailable as e:
            logger.warning(f"[Cloud DLP] Sensitive Data Protection unavailable ({e}); keeping the regex redaction.")
            return texts, [0] * len(texts), "regex_fallback"
        new_texts, added = [], []
        for text, row_findings in zip(texts, findings):
            protected = reference_number_spans(text) if preserve_reference_numbers else []
            new_text, count = tokenize_findings(text, row_findings, {}, protected)
            new_texts.append(new_text)
            added.append(count)
        return new_texts, added, "regex+sdp_api"

    @staticmethod
    def redact_text(text: str, preserve_reference_numbers: bool = True):
        """Redacts PII from a single description string while preserving 12-digit UPI/IMPS references."""
        return FinancialDLPPipeline.redact_texts([text], preserve_reference_numbers=preserve_reference_numbers)[0]

    @staticmethod
    def redact_texts(texts: List[str], preserve_reference_numbers: bool = True) -> List[SimpleNamespace]:
        """`redact_text` for many rows; the SDP layer (when on) inspects them in one batched request.

        Each result has `.sanitized_text`, `.redacted_count` and `.decision_layer`.
        """
        regex_results = [
            FinancialDLPPipeline._regex_redact_document_text(str(text or ""), preserve_reference_numbers)
            for text in texts
        ]
        texts_after, added, layer = FinancialDLPPipeline._sdp_document_layer(
            [out for out, _masked in regex_results], preserve_reference_numbers
        )
        return [
            SimpleNamespace(sanitized_text=out, redacted_count=masked + extra, decision_layer=layer)
            for out, (_regex_out, masked), extra in zip(texts_after, regex_results, added)
        ]

    def redact_transaction_descriptions(
        self_or_transactions,
        transactions: Optional[list] = None,
        preserve_reference_numbers: bool = True,
        report: Optional[dict] = None,
    ):
        """Redacts PAN, Aadhaar, Card numbers, IFSC, Phone, and Email from extracted document rows.

        Preserves 12-digit UPI/IMPS reference numbers (RRN) when `preserve_reference_numbers=True`
        so cross-source reconciliation (`database.extract_reference_numbers`) remains exact.
        Returns `(redacted_txs, redacted_rows_count)`. With SDP_ENABLED=1 the regex output of all
        rows also goes through Sensitive Data Protection in one batched request (DEC-063); pass a
        dict as `report` to receive `decision_layer` and `sdp_entities_added`.
        """
        if isinstance(self_or_transactions, FinancialDLPPipeline):
            tx_list = transactions or []
        else:
            tx_list = self_or_transactions or []

        redacted_rows = 0
        out_txs = []
        row_texts: List[Tuple[int, str, int]] = []  # (position in out_txs, regex output, entities masked)
        for tx in tx_list:
            desc = getattr(tx, "description", None) if not isinstance(tx, dict) else tx.get("description")
            if not desc:
                out_txs.append(tx)
                continue
            text, row_masked = FinancialDLPPipeline._regex_redact_document_text(str(desc), preserve_reference_numbers)
            if row_masked > 0:
                redacted_rows += 1
                if isinstance(tx, dict):
                    tx = dict(tx)
                    tx["description"] = text
                else:
                    tx.description = text
            row_texts.append((len(out_txs), text, row_masked))
            out_txs.append(tx)

        texts_after, added, layer = FinancialDLPPipeline._sdp_document_layer(
            [text for _pos, text, _masked in row_texts], preserve_reference_numbers
        )
        for (pos, _text, row_masked), new_text, extra in zip(row_texts, texts_after, added):
            if not extra:
                continue
            if row_masked == 0:
                redacted_rows += 1
            tx = out_txs[pos]
            if isinstance(tx, dict):
                tx = dict(tx)
                tx["description"] = new_text
                out_txs[pos] = tx
            else:
                tx.description = new_text
        if report is not None:
            report["decision_layer"] = layer
            report["sdp_entities_added"] = sum(added)
        return out_txs, redacted_rows
