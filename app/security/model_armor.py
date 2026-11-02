# app/security/model_armor.py
"""Model Armor: Layer-7 Policy Enforcement Point (PEP) for Prompt Injection & Adversarial Defense.
FDE Pillar 2: GOVERN.
Screens incoming user messages and OCR text from receipts/PDFs before dispatching to foundation models.

Two layers (DEC-063):
1. A local regex screen (the patterns below). It runs first, always, and calls no Google API.
2. Google Cloud Model Armor (`model_armor_api.py`), only when `MODEL_ARMOR_TEMPLATE` is set: chat
   prompts (`screen_prompt`), document text (`inspect_prompt` / `inspect_prompts`) and the
   advisor's replies (`screen_model_response`) are also sent to the real `sanitizeUserPrompt` /
   `sanitizeModelResponse` API. If that call fails, the regex result stands (a warning is logged).
`screen_document_text` and `screen_transaction_batch` stay regex-only primitives.
Which layer decided is kept in `last_decision` / `last_response_decision` (and `decision_layer` on
the `inspect_*` results) for the trace spans.
"""
import re
import logging
from types import SimpleNamespace
from typing import Tuple, Optional, List, Dict

from . import model_armor_api

logger = logging.getLogger(__name__)

API_FLAGGED_DOCUMENT_TEXT = "[REDACTED_ADVERSARIAL_DIRECTIVE]"


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
        # Which layer made the last prompt / reply decision (DEC-063); see decision_attributes().
        self.last_decision: Dict[str, object] = {"layer": "regex", "verdict": "allow"}
        self.last_response_decision: Dict[str, object] = {"layer": "none", "verdict": "allow"}

    @staticmethod
    def api_enabled() -> bool:
        """True when the Google Cloud Model Armor API is configured (MODEL_ARMOR_TEMPLATE)."""
        return model_armor_api.is_enabled()

    @staticmethod
    def decision_attributes(decision: Optional[Dict[str, object]]) -> Dict[str, object]:
        """Trace-span attributes for a decision: `decision_layer` is regex, model_armor_api or
        regex_fallback (the API was configured but failed, so the regex result stood)."""
        decision = decision or {}
        attrs: Dict[str, object] = {"decision_layer": decision.get("layer", "regex")}
        if decision.get("filters"):
            attrs["model_armor.filters_matched"] = ",".join(decision["filters"])
        if decision.get("api_error"):
            attrs["model_armor.api_error"] = decision["api_error"]
        if decision.get("layer") in ("model_armor_api", "regex_fallback"):
            attrs["model_armor.verdict"] = decision.get("verdict", "allow")
        return attrs

    def screen_prompt(self, text: str) -> Tuple[bool, str, Optional[str]]:
        """Screens a user chat prompt.

        The regex patterns run first; a prompt they pass then goes to the Model Armor API when it
        is configured (DEC-063). If the API fails, the regex result stands.

        Returns:
            Tuple of (is_safe: bool, sanitized_text: str, risk_reason: Optional[str])
        """
        self.last_decision = {"layer": "regex", "verdict": "allow"}
        if not text:
            return True, "", None

        # 1. Delimiter tampering check
        for pattern in self._compiled_delimiters:
            match = pattern.search(text)
            if match:
                reason = f"Delimiter tampering detected: '{match.group(0)}'"
                logger.warning(f"[ModelArmor PEP] Blocked prompt: {reason}")
                self.last_decision = {"layer": "regex", "verdict": "block"}
                if self.strict_mode:
                    raise SecurityViolationError(reason, threat_type="DELIMITER_INJECTION", offending_snippet=match.group(0))
                return False, self._sanitize(text), reason

        # 2. Instruction override check
        for pattern in self._compiled_injections:
            match = pattern.search(text)
            if match:
                reason = f"Prompt injection attempt detected: '{match.group(0)}'"
                logger.warning(f"[ModelArmor PEP] Blocked prompt: {reason}")
                self.last_decision = {"layer": "regex", "verdict": "block"}
                if self.strict_mode:
                    raise SecurityViolationError(reason, threat_type="PROMPT_INJECTION", offending_snippet=match.group(0))
                return False, self._sanitize(text), reason

        # 3. Exfiltration check
        for pattern in self._compiled_exfiltration:
            match = pattern.search(text)
            if match:
                reason = f"Data exfiltration directive detected: '{match.group(0)}'"
                logger.warning(f"[ModelArmor PEP] Blocked prompt: {reason}")
                self.last_decision = {"layer": "regex", "verdict": "block"}
                if self.strict_mode:
                    raise SecurityViolationError(reason, threat_type="DATA_EXFILTRATION", offending_snippet=match.group(0))
                return False, self._sanitize(text), reason

        # 4. Google Cloud Model Armor API (only when MODEL_ARMOR_TEMPLATE is set)
        if model_armor_api.is_enabled():
            verdict = model_armor_api.sanitize_user_prompt(text)
            if not verdict.ok:
                self.last_decision = {"layer": "regex_fallback", "verdict": "allow", "api_error": verdict.error}
            elif verdict.blocked:
                filters = verdict.filters_matched
                reason = f"Model Armor flagged the prompt ({', '.join(filters)})"
                logger.warning(f"[ModelArmor PEP] Blocked prompt: {reason}")
                self.last_decision = {"layer": "model_armor_api", "verdict": "block", "filters": filters}
                if self.strict_mode:
                    raise SecurityViolationError(
                        reason, threat_type="MODEL_ARMOR_" + "_".join(f.upper() for f in filters), offending_snippet="")
                return False, self._sanitize(text), reason
            else:
                self.last_decision = {"layer": "model_armor_api", "verdict": "allow", "filters": verdict.filters_matched}

        return True, self._sanitize(text), None

    def screen_model_response(self, text: str, user_prompt: str = "") -> Tuple[bool, Optional[str]]:
        """Screens a model reply with the Model Armor API (DEC-063). Returns (is_safe, reason).

        There is no regex layer for replies: with the API off, or when it fails, every reply
        passes, as it did before. `user_prompt` (already DLP-tokenised by the caller) gives the
        API the question for context.
        """
        self.last_response_decision = {"layer": "none", "verdict": "allow"}
        if not text or not model_armor_api.is_enabled():
            return True, None
        verdict = model_armor_api.sanitize_model_response(text, user_prompt=user_prompt)
        if not verdict.ok:
            self.last_response_decision = {"layer": "regex_fallback", "verdict": "allow", "api_error": verdict.error}
            return True, None
        if verdict.blocked:
            reason = f"Model Armor flagged the reply ({', '.join(verdict.filters_matched)})"
            logger.warning(f"[ModelArmor PEP] Withheld model reply: {reason}")
            self.last_response_decision = {"layer": "model_armor_api", "verdict": "block", "filters": verdict.filters_matched}
            return False, reason
        self.last_response_decision = {"layer": "model_armor_api", "verdict": "allow", "filters": verdict.filters_matched}
        return True, None

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
        """Non-raising inspection helper returning an object with `.is_safe`, `.sanitized_text`, `.threat_reason`
        (and `.decision_layer`). Regex first, then the Model Armor API when configured (DEC-063)."""
        return ModelArmor.inspect_prompts([text])[0]

    @staticmethod
    def inspect_prompts(texts: List[str]) -> List[SimpleNamespace]:
        """`inspect_prompt` for many texts (document rows) at once (DEC-063).

        Every text gets the regex document screen. When the Model Armor API is configured, the texts
        the regex passed are screened in a few batched requests (`model_armor_api.screen_documents`);
        a row the API flags gets `is_safe=False` and `[REDACTED_ADVERSARIAL_DIRECTIVE]`. Rows the API
        could not screen keep the regex result (`decision_layer="regex_fallback"`).
        """
        armor = ModelArmor(strict_mode=False)
        results = []
        for text in texts:
            is_safe, clean, reason = armor.screen_document_text(str(text or ""))
            results.append(SimpleNamespace(is_safe=is_safe, sanitized_text=clean, threat_reason=reason,
                                           decision_layer="regex"))
        if not model_armor_api.is_enabled():
            return results

        candidates = [i for i, res in enumerate(results) if res.is_safe and str(texts[i] or "").strip()]
        screen = model_armor_api.screen_documents([str(texts[i] or "") for i in candidates])
        for position, index in enumerate(candidates):
            res = results[index]
            if position in screen.flagged:
                filters = screen.flagged[position]
                reason = f"Model Armor flagged document text ({', '.join(filters)})"
                logger.warning(f"[ModelArmor PEP] Document Alert: {reason}")
                results[index] = SimpleNamespace(is_safe=False, sanitized_text=API_FLAGGED_DOCUMENT_TEXT,
                                                 threat_reason=reason, decision_layer="model_armor_api",
                                                 filters_matched=filters)
            elif position in screen.screened:
                res.decision_layer = "model_armor_api"
            else:
                res.decision_layer = "regex_fallback"
        return results

    @staticmethod
    def summarize_layers(results) -> Dict[str, int]:
        """How many `inspect_*` results each layer decided, e.g. {"model_armor_api": 40, "regex": 1}."""
        counts: Dict[str, int] = {}
        for res in results:
            layer = getattr(res, "decision_layer", "regex")
            counts[layer] = counts.get(layer, 0) + 1
        return counts

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

