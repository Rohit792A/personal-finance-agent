# app/security/sdp_api.py
"""Google Cloud Sensitive Data Protection (Cloud DLP API) inspection (DEC-063).

FDE Pillar 2: GOVERN.
Off unless `SDP_ENABLED=1`. Then `FinancialDLPPipeline` (dlp.py) runs its local regex redaction
first, sends the already-redacted text to `projects.locations.content.inspect` through the
official `google-cloud-dlp` client (`dlp_v2.DlpServiceClient.inspect_content`), and turns each
finding into one of its reversible tokens. If the call fails, the regex result stands.

- Built-in infoTypes: `SDP_INFO_TYPES` (comma-separated), default `INDIA_PAN_INDIVIDUAL,
  INDIA_AADHAAR_INDIVIDUAL, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD_NUMBER`. The list is
  configurable because the names can only be confirmed with `infoTypes.list` once the API is
  enabled; an unknown name makes the API reject the request, so the regex result is used and a
  warning is logged.
- Custom regex infoTypes defined here: `PFA_UPI_ID` (UPI IDs such as name@okhdfcbank) and
  `PFA_BANK_ACCOUNT_NUMBER` (9-18 digits, reported only near words like "A/c" or "account").
- `SDP_MIN_LIKELIHOOD` (default LIKELY), `SDP_LOCATION` (default global), project
  `config.PROJECT_ID`, one attempt per request with `SDP_TIMEOUT_SECONDS` (default 10).
- Findings come back as code-point ranges into the text that was sent; quotes are not requested,
  so the response never repeats the sensitive values.
"""
import logging
import os
import threading
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from telemetry.tracer import otel_span

logger = logging.getLogger(__name__)

DEFAULT_INFO_TYPES = (
    "INDIA_PAN_INDIVIDUAL",
    "INDIA_AADHAAR_INDIVIDUAL",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD_NUMBER",
)
UPI_INFO_TYPE = "PFA_UPI_ID"
BANK_ACCOUNT_INFO_TYPE = "PFA_BANK_ACCOUNT_NUMBER"
# RE2 syntax (what Sensitive Data Protection uses).
UPI_PATTERN = r"\b[A-Za-z0-9][A-Za-z0-9._-]{1,255}@[A-Za-z][A-Za-z0-9]{1,63}\b"
BANK_ACCOUNT_PATTERN = r"\b\d{9,18}\b"
BANK_ACCOUNT_HOTWORDS = r"(?i)\b(a/?c|acct|account|savings|current)\b"

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_CHUNK_CHARS = 50000  # well under the 0.5 MB inspect_content request limit
_LIKELIHOODS = ("VERY_UNLIKELY", "UNLIKELY", "POSSIBLE", "LIKELY", "VERY_LIKELY")

_CLIENT = None
_CLIENT_LOCK = threading.Lock()


class SdpUnavailable(Exception):
    """The Sensitive Data Protection call failed; callers keep the regex result."""


@dataclass(frozen=True)
class Finding:
    """One finding: code-point offsets [start, end) into the text that was inspected."""

    start: int
    end: int
    info_type: str


def is_enabled() -> bool:
    return os.getenv("SDP_ENABLED", "").strip().lower() in ("1", "true", "yes")


def info_types() -> List[str]:
    raw = os.getenv("SDP_INFO_TYPES", "")
    names = [name.strip().upper() for name in raw.split(",") if name.strip()]
    return names or list(DEFAULT_INFO_TYPES)


def min_likelihood() -> str:
    value = os.getenv("SDP_MIN_LIKELIHOOD", "LIKELY").strip().upper()
    return value if value in _LIKELIHOODS else "LIKELY"


def location() -> str:
    return os.getenv("SDP_LOCATION", "global").strip() or "global"


def project() -> str:
    import config  # lazy: same project as Vertex AI and Secret Manager

    return config.PROJECT_ID


def _timeout() -> float:
    try:
        return max(1.0, float(os.getenv("SDP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _client():
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            from google.cloud import dlp_v2

            _CLIENT = dlp_v2.DlpServiceClient()
        return _CLIENT


def build_inspect_config(include_bank_accounts: bool):
    """The InspectConfig: built-in infoTypes, the UPI ID type and (optionally) bank account numbers."""
    from google.cloud import dlp_v2

    custom = [
        dlp_v2.CustomInfoType(
            info_type=dlp_v2.InfoType(name=UPI_INFO_TYPE),
            regex=dlp_v2.CustomInfoType.Regex(pattern=UPI_PATTERN),
            likelihood=dlp_v2.Likelihood.LIKELY,
        )
    ]
    if include_bank_accounts:
        custom.append(
            dlp_v2.CustomInfoType(
                info_type=dlp_v2.InfoType(name=BANK_ACCOUNT_INFO_TYPE),
                regex=dlp_v2.CustomInfoType.Regex(pattern=BANK_ACCOUNT_PATTERN),
                # Bare digit runs stay below the minimum likelihood; a nearby "A/c", "account"...
                # raises them to VERY_LIKELY.
                likelihood=dlp_v2.Likelihood.POSSIBLE,
                detection_rules=[
                    dlp_v2.CustomInfoType.DetectionRule(
                        hotword_rule=dlp_v2.CustomInfoType.DetectionRule.HotwordRule(
                            hotword_regex=dlp_v2.CustomInfoType.Regex(pattern=BANK_ACCOUNT_HOTWORDS),
                            proximity=dlp_v2.CustomInfoType.DetectionRule.Proximity(window_before=30),
                            likelihood_adjustment=dlp_v2.CustomInfoType.DetectionRule.LikelihoodAdjustment(
                                fixed_likelihood=dlp_v2.Likelihood.VERY_LIKELY
                            ),
                        )
                    )
                ],
            )
        )
    return dlp_v2.InspectConfig(
        info_types=[dlp_v2.InfoType(name=name) for name in info_types()],
        custom_info_types=custom,
        min_likelihood=dlp_v2.Likelihood[min_likelihood()],
        include_quote=False,
    )


def _codepoint_range(location_proto, text: str) -> Tuple[int, int]:
    """Code-point offsets of a finding; falls back to converting its UTF-8 byte range."""
    cp = location_proto.codepoint_range
    if cp.end > cp.start:
        return int(cp.start), int(cp.end)
    br = location_proto.byte_range
    if br.end > br.start:
        data = text.encode("utf-8")
        start = len(data[: br.start].decode("utf-8", errors="ignore"))
        end = len(data[: br.end].decode("utf-8", errors="ignore"))
        return start, end
    return 0, 0


def _inspect(text: str, include_bank_accounts: bool) -> Tuple[List[Finding], bool]:
    """One inspect_content request. Returns (findings, findings_truncated). Raises SdpUnavailable."""
    from google.cloud import dlp_v2

    with otel_span("sdp.inspect_content", {"sdp.location": location(), "sdp.text_chars": len(text)}) as span:
        try:
            request = dlp_v2.InspectContentRequest(
                parent=f"projects/{project()}/locations/{location()}",
                inspect_config=build_inspect_config(include_bank_accounts),
                item=dlp_v2.ContentItem(value=text),
            )
            response = _client().inspect_content(request=request, retry=None, timeout=_timeout())
        except Exception as e:
            if span is not None:
                span.set_attribute("sdp.error", type(e).__name__)
                span.set_attribute("decision_layer", "regex_fallback")
            raise SdpUnavailable(type(e).__name__) from e
        findings = []
        for finding in response.result.findings:
            start, end = _codepoint_range(finding.location, text)
            if 0 <= start < end <= len(text):
                findings.append(Finding(start, end, finding.info_type.name))
        truncated = bool(response.result.findings_truncated)
        if span is not None:
            span.set_attribute("decision_layer", "sdp_api")
            span.set_attribute("sdp.findings", len(findings))
            span.set_attribute("sdp.findings_truncated", truncated)
        return findings, truncated


def _chunks(texts: Sequence[str]) -> List[List[int]]:
    chunks: List[List[int]] = []
    current: List[int] = []
    size = 0
    for i, text in enumerate(texts):
        length = len(text) + 1
        if current and size + length > MAX_CHUNK_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(i)
        size += length
    if current:
        chunks.append(current)
    return chunks


def _inspect_rows(indices: List[int], texts: Sequence[str], include_bank_accounts: bool,
                  out: Dict[int, List[Finding]], depth: int = 0) -> None:
    """Inspects rows joined by newlines and maps each finding back to its row (clipped to it).
    A truncated result is re-inspected in halves (a few levels deep)."""
    joined = "\n".join(texts[i] for i in indices)
    findings, truncated = _inspect(joined, include_bank_accounts)
    if truncated and len(indices) > 1 and depth < 6:
        middle = len(indices) // 2
        _inspect_rows(indices[:middle], texts, include_bank_accounts, out, depth + 1)
        _inspect_rows(indices[middle:], texts, include_bank_accounts, out, depth + 1)
        return
    if truncated:
        logger.warning("Sensitive Data Protection truncated its findings for a row; some values may stay unmasked.")
    offset = 0
    bounds = []
    for i in indices:
        bounds.append((i, offset, offset + len(texts[i])))
        offset += len(texts[i]) + 1
    for finding in findings:
        for i, row_start, row_end in bounds:
            start, end = max(finding.start, row_start), min(finding.end, row_end)
            if start < end:
                out.setdefault(i, []).append(Finding(start - row_start, end - row_start, finding.info_type))


def inspect_texts(texts: Sequence[str], include_bank_accounts: bool = True) -> List[List[Finding]]:
    """Findings for each text (offsets into that text). Empty texts are not sent.
    Raises SdpUnavailable if any request fails, so the caller keeps its regex result for all."""
    texts = [str(t or "") for t in texts]
    out: Dict[int, List[Finding]] = {}
    indices = [i for i, text in enumerate(texts) if text.strip()]
    if indices:
        rows = [texts[i] for i in indices]
        for chunk in _chunks(rows):
            _inspect_rows([indices[j] for j in chunk], texts, include_bank_accounts, out)
    return [out.get(i, []) for i in range(len(texts))]
