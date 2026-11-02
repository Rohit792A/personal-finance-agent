# app/security/model_armor.py
"""Model Armor: Layer-7 Policy Enforcement Point (PEP) for Prompt Injection & Adversarial Defense.
FDE Pillar 2: GOVERN.
Screens incoming user messages and OCR text from receipts/PDFs before dispatching to foundation models.
"""
import re
import logging
from typing import Tuple, Optional, List, Dict

logger = logging.getLogger(__name__)


class SecurityViolationError(Exception):
    """Raised when an adversarial prompt injection or system boundary breach is detected."""
    def __init__(self, message: str, threat_type: str, offending_snippet: str = ""):
        super().__init__(message)
        self.threat_type = threat_type
        self.offending_snippet = offending_snippet


class ModelArmor:
    """Inline Layer-7 Policy Enforcement Point (PEP) for LLM interactions."""

    # High-risk prompt injection and instruction override patterns
    INJECTION_PATTERNS = [
        r"(?i)\bignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)",
        r"(?i)\bdisregard\s+(all\s+)?(previous|prior|system)\s+(instructions|directives)",
        r"(?i)\byou\s+are\s+no\s+longer\s+(a\s+)?(financial|cfo|advisor|ai)",
        r"(?i)\bdo\s+anything\s+now\b",
        r"(?i)\bjailbreak\b",
        r"(?i)\bdeveloper\s+mode\s+(enabled|on)\b",
        r"(?i)\boutput\s+(the\s+)?(system\s+prompt|initial\s+prompt|raw\s+instructions)",
        r"(?i)\brepeat\s+everything\s+(above|said\s+before)",
        r"(?i)\bshow\s+me\s+your\s+(instructions|system\s+message)",
        r"(?i)\bprint\s+environment\s+variables\b",
        r"(?i)\b(api_key|database_url|postgres_password|secret_key)\b",
    ]

    # Delimiter hijacking / prompt framing escape patterns
    DELIMITER_PATTERNS = [
        r"</?financial_summary>",
        r"</?transaction_data>",
        r"</?system>",
        r"\[SYSTEM\]",
        r"\[ADMIN\]",
        r"\[INST\]",
        r"<<SYS>>",
        r"<\|im_start\|>",
        r"<\|im_end\|>",
    ]

    # Indirect data exfiltration indicators in uploaded receipts/docs
    EXFILTRATION_PATTERNS = [
        r"(?i)\b(curl|wget|fetch|axios)\s+https?://",
        r"(?i)\bbase64\s+(encode|decode)\s+transactions\b",
        r"(?i)\bsend\s+(all\s+)?(data|transactions|balances)\s+to\s+https?://",
    ]

    def __init__(self, strict_mode: bool = True):
        self.strict_mode = strict_mode
        self._compiled_injections = [re.compile(p) for p in self.INJECTION_PATTERNS]
        self._compiled_delimiters = [re.compile(p) for p in self.DELIMITER_PATTERNS]
        self._compiled_exfiltration = [re.compile(p) for p in self.EXFILTRATION_PATTERNS]

    def screen_prompt(self, text: str) -> Tuple[bool, str, Optional[str]]:
        """Screens a user chat prompt.
        
        Returns:
            Tuple of (is_safe: bool, sanitized_text: str, risk_reason: Optional[str])
        """
        if not text:
            return True, "", None

        # 1. Delimiter tampering check
        for pattern in self._compiled_delimiters:
            match = pattern.search(text)
            if match:
                reason = f"Delimiter tampering detected: '{match.group(0)}'"
                logger.warning(f"[ModelArmor PEP] Blocked prompt: {reason}")
                if self.strict_mode:
                    raise SecurityViolationError(reason, threat_type="DELIMITER_INJECTION", offending_snippet=match.group(0))
                return False, self._sanitize(text), reason

        # 2. Instruction override check
        for pattern in self._compiled_injections:
            match = pattern.search(text)
            if match:
                reason = f"Prompt injection attempt detected: '{match.group(0)}'"
                logger.warning(f"[ModelArmor PEP] Blocked prompt: {reason}")
                if self.strict_mode:
                    raise SecurityViolationError(reason, threat_type="PROMPT_INJECTION", offending_snippet=match.group(0))
                return False, self._sanitize(text), reason

        # 3. Exfiltration check
        for pattern in self._compiled_exfiltration:
            match = pattern.search(text)
            if match:
                reason = f"Data exfiltration directive detected: '{match.group(0)}'"
                logger.warning(f"[ModelArmor PEP] Blocked prompt: {reason}")
                if self.strict_mode:
                    raise SecurityViolationError(reason, threat_type="DATA_EXFILTRATION", offending_snippet=match.group(0))
                return False, self._sanitize(text), reason

        return True, self._sanitize(text), None

    def screen_document_text(self, ocr_text: str) -> Tuple[bool, str, Optional[str]]:
        """Screens extracted OCR text from PDF bank statements or receipts for indirect prompt injection."""
        if not ocr_text:
            return True, "", None

        # Check for indirect injections embedded in invoice descriptions
        for pattern in self._compiled_injections + self._compiled_delimiters + self._compiled_exfiltration:
            match = pattern.search(ocr_text)
            if match:
                reason = f"Indirect document injection detected: '{match.group(0)}'"
                logger.warning(f"[ModelArmor PEP] Document OCR Alert: {reason}")
                # Neutralize without dropping document completely
                sanitized = pattern.sub("[REDACTED_ADVERSARIAL_DIRECTIVE]", ocr_text)
                return False, sanitized, reason

        return True, ocr_text, None

    @staticmethod
    def inspect_prompt(text: str):
        """Non-raising inspection helper returning an object with `.is_safe`, `.sanitized_text`, `.threat_reason`."""
        from types import SimpleNamespace

        armor = ModelArmor(strict_mode=False)
        is_safe, clean, reason = armor.screen_document_text(str(text or ""))
        return SimpleNamespace(is_safe=is_safe, sanitized_text=clean, threat_reason=reason)

    def screen_transaction_batch(self_or_transactions, transactions: Optional[list] = None) -> dict:
        """Screens and sanitizes descriptions across a batch of extracted transactions in-place.

        Can be called as `ModelArmor().screen_transaction_batch(txs)` or `ModelArmor.screen_transaction_batch(txs)`.
        """
        if isinstance(self_or_transactions, ModelArmor):
            armor = self_or_transactions
            tx_list = transactions or []
        else:
            armor = ModelArmor(strict_mode=False)
            tx_list = self_or_transactions or []

        screened_count = 0
        flagged_count = 0
        for tx in tx_list:
            desc = getattr(tx, "description", None) if not isinstance(tx, dict) else tx.get("description")
            if not desc:
                continue
            screened_count += 1
            is_safe, clean_desc, _reason = armor.screen_document_text(str(desc))
            if not is_safe:
                flagged_count += 1
                if isinstance(tx, dict):
                    tx["description"] = clean_desc
                    tx["requires_human_review"] = True
                else:
                    tx.description = clean_desc
                    tx.requires_human_review = True
        return {"screened": screened_count, "flagged": flagged_count}

    def _sanitize(self, text: str) -> str:
        """Sanitizes text by neutralizing known delimiter tags."""
        sanitized = text
        for pattern in self._compiled_delimiters:
            sanitized = pattern.sub("", sanitized)
        return sanitized.strip()

