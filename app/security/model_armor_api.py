# app/security/model_armor_api.py
"""Google Cloud Model Armor API: the real `sanitizeUserPrompt` / `sanitizeModelResponse` calls (DEC-063).

FDE Pillar 2: GOVERN.
Off unless `MODEL_ARMOR_TEMPLATE` holds a full template name,
`projects/{project}/locations/{location}/templates/{template_id}`. Then `ModelArmor`
(model_armor.py) asks this API about chat prompts, document text and the advisor's replies,
AFTER its local regex screen (which stays the first layer and the fallback).

- Client: the official `google-cloud-modelarmor` package (`modelarmor_v1.ModelArmorClient`) on the
  template's regional endpoint, `modelarmor.{location}.rep.googleapis.com`. It is imported lazily,
  so the app starts (regex only) if the package is missing.
- One attempt per request (no client retries) with a short timeout (`MODEL_ARMOR_TIMEOUT_SECONDS`,
  default 5). Any error returns `ApiVerdict(ok=False)`, the caller logs it and keeps the regex
  result: Model Armor being down never fails a request.
- The verdict comes from the per-filter match states. Which filters make text unsafe depends on
  the use: prompts and replies are blocked by any matched filter except `sdp` (sensitive data is
  tokenised by the separate SDP layer in dlp.py, and blocking on it would refuse ordinary
  questions about one's own accounts); document text (bank narrations) is flagged only by
  `pi_and_jailbreak` and `malicious_uris`, the indirect-injection threats the regex document
  screen looks for, so a responsible-AI false positive never wipes a ledger narration.
- `screen_documents()` screens many narrations in a few requests (up to 50 rows / 8,000 characters
  each) and splits a flagged batch in halves until it finds the rows that match.
"""
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from telemetry.tracer import otel_span

logger = logging.getLogger(__name__)

_TEMPLATE_RE = re.compile(r"^projects/([^/]+)/locations/([^/]+)/templates/([^/]+)$")
DEFAULT_TIMEOUT_SECONDS = 5.0

# SanitizationResult.filter_results values are FilterResult messages with one of these set.
_ONEOF_TO_FILTER = {
    "rai_filter_result": "rai",
    "sdp_filter_result": "sdp",
    "pi_and_jailbreak_filter_result": "pi_and_jailbreak",
    "malicious_uri_filter_result": "malicious_uris",
    "csam_filter_filter_result": "csam",
    "virus_scan_filter_result": "virus_scan",
}
PROMPT_BLOCKING: FrozenSet[str] = frozenset({"rai", "pi_and_jailbreak", "malicious_uris", "csam", "virus_scan"})
RESPONSE_BLOCKING: FrozenSet[str] = PROMPT_BLOCKING
DOCUMENT_BLOCKING: FrozenSet[str] = frozenset({"pi_and_jailbreak", "malicious_uris"})

# Document batches: rows and characters per request, and a cap on requests per batch.
MAX_BATCH_ROWS = 50
MAX_BATCH_CHARS = 8000
MAX_CALLS_PER_BATCH = 200

_CLIENTS: Dict[str, object] = {}
_CLIENT_LOCK = threading.Lock()
_WARNED_TEMPLATES: Set[str] = set()


@dataclass
class ApiVerdict:
    """One Model Armor answer. `ok` is False when the API could not be used (see `error`)."""

    ok: bool
    blocked: bool = False
    filters_matched: List[str] = field(default_factory=list)
    details: List[str] = field(default_factory=list)
    invocation_result: str = ""
    error: str = ""
    latency_ms: float = 0.0


def template_name() -> str:
    return (os.getenv("MODEL_ARMOR_TEMPLATE") or "").strip()


def parse_template(name: str) -> Optional[Tuple[str, str, str]]:
    """(project, location, template_id) of a full template name, else None."""
    match = _TEMPLATE_RE.match(name or "")
    return match.groups() if match else None


def is_enabled() -> bool:
    """True when MODEL_ARMOR_TEMPLATE holds a well-formed template name."""
    name = template_name()
    if not name:
        return False
    if parse_template(name) is None:
        if name not in _WARNED_TEMPLATES:
            _WARNED_TEMPLATES.add(name)
            logger.warning(
                "MODEL_ARMOR_TEMPLATE is not a full template name "
                "(projects/{project}/locations/{location}/templates/{id}); Model Armor API is off."
            )
        return False
    return True


def regional_endpoint(location: str) -> str:
    return f"modelarmor.{location}.rep.googleapis.com"


def _timeout() -> float:
    try:
        return max(0.5, float(os.getenv("MODEL_ARMOR_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _client(location: str):
    """One ModelArmorClient per regional endpoint, created on first use."""
    with _CLIENT_LOCK:
        client = _CLIENTS.get(location)
        if client is None:
            from google.api_core.client_options import ClientOptions
            from google.cloud import modelarmor_v1

            client = modelarmor_v1.ModelArmorClient(client_options=ClientOptions(api_endpoint=regional_endpoint(location)))
            _CLIENTS[location] = client
        return client


def verdict_from_result(result, blocking: FrozenSet[str]) -> ApiVerdict:
    """Reads a `modelarmor_v1.SanitizationResult` into an ApiVerdict."""
    from google.cloud import modelarmor_v1

    match_found = modelarmor_v1.FilterMatchState.MATCH_FOUND
    invocation = modelarmor_v1.InvocationResult(result.invocation_result).name
    if invocation == "FAILURE":
        return ApiVerdict(ok=False, invocation_result=invocation, error="InvocationFailure")

    matched: List[str] = []
    details: List[str] = []
    for key, filter_result in result.filter_results.items():
        which = modelarmor_v1.FilterResult.pb(filter_result).WhichOneof("filter_result")
        name = _ONEOF_TO_FILTER.get(which, str(key))
        sub = getattr(filter_result, which) if which else None
        if sub is None:
            continue
        if name == "sdp":
            hit = match_found in (sub.inspect_result.match_state, sub.deidentify_result.match_state)
        else:
            hit = sub.match_state == match_found
        if not hit:
            continue
        matched.append(name)
        if name == "rai":
            for rai_key, rai_result in sub.rai_filter_type_results.items():
                if rai_result.match_state == match_found:
                    details.append(f"rai:{rai_key}")
        elif name == "pi_and_jailbreak":
            details.append(f"pi_and_jailbreak:{modelarmor_v1.DetectionConfidenceLevel(sub.confidence_level).name.lower()}")

    if not matched and result.filter_match_state == match_found:
        # The overall state says "match" but no filter detail was readable: treat it as unsafe.
        matched, details = ["unspecified"], ["unspecified"]
        blocked = True
    else:
        blocked = any(name in blocking for name in matched)
    return ApiVerdict(ok=True, blocked=blocked, filters_matched=sorted(set(matched)), details=details,
                      invocation_result=invocation)


def _call(kind: str, text: str, blocking: FrozenSet[str], user_prompt: str = "", label: str = "") -> ApiVerdict:
    parsed = parse_template(template_name())
    if parsed is None:
        return ApiVerdict(ok=False, error="NotConfigured")
    _project, location, _template_id = parsed
    started = time.monotonic()
    span_name = f"model_armor.{kind}" + (f".{label}" if label else "")
    with otel_span(span_name, {"model_armor.location": location, "model_armor.text_chars": len(text or "")}) as span:
        try:
            from google.cloud import modelarmor_v1

            client = _client(location)
            if kind == "sanitize_user_prompt":
                request = modelarmor_v1.SanitizeUserPromptRequest(
                    name=template_name(), user_prompt_data=modelarmor_v1.DataItem(text=text or ""))
                response = client.sanitize_user_prompt(request=request, retry=None, timeout=_timeout())
            else:
                request = modelarmor_v1.SanitizeModelResponseRequest(
                    name=template_name(), model_response_data=modelarmor_v1.DataItem(text=text or ""),
                    user_prompt=user_prompt or "")
                response = client.sanitize_model_response(request=request, retry=None, timeout=_timeout())
            verdict = verdict_from_result(response.sanitization_result, blocking)
        except Exception as e:  # network, permission, quota, missing package, bad template...
            verdict = ApiVerdict(ok=False, error=type(e).__name__)
        verdict.latency_ms = round((time.monotonic() - started) * 1000, 2)
        if not verdict.ok:
            logger.warning("Model Armor %s unavailable (%s); keeping the regex screen's result.", kind, verdict.error)
        if span is not None:
            span.set_attribute("decision_layer", "model_armor_api" if verdict.ok else "regex_fallback")
            span.set_attribute("model_armor.verdict", ("block" if verdict.blocked else "allow") if verdict.ok else "error")
            if verdict.filters_matched:
                span.set_attribute("model_armor.filters_matched", ",".join(verdict.filters_matched))
            if verdict.details:
                span.set_attribute("model_armor.details", ",".join(verdict.details))
            if verdict.invocation_result:
                span.set_attribute("model_armor.invocation_result", verdict.invocation_result)
            if verdict.error:
                span.set_attribute("model_armor.error", verdict.error)
    return verdict


def sanitize_user_prompt(text: str, blocking: FrozenSet[str] = PROMPT_BLOCKING, label: str = "") -> ApiVerdict:
    """Model Armor's verdict on text going INTO a model (a chat prompt or document text)."""
    return _call("sanitize_user_prompt", text, blocking, label=label)


def sanitize_model_response(text: str, user_prompt: str = "", blocking: FrozenSet[str] = RESPONSE_BLOCKING) -> ApiVerdict:
    """Model Armor's verdict on a model's reply (`user_prompt` gives it the question for context)."""
    return _call("sanitize_model_response", text, blocking, user_prompt=user_prompt)


@dataclass
class DocumentScreen:
    """Result of `screen_documents`: `flagged` maps a row index to the filters that matched it;
    `screened` holds every row the API decided. Rows in neither keep the regex result (`error`
    says why the API stopped: an API error or the per-batch request cap)."""

    flagged: Dict[int, List[str]] = field(default_factory=dict)
    screened: Set[int] = field(default_factory=set)
    error: str = ""
    calls: int = 0


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _batches(indices: List[int], texts: Sequence[str]) -> List[List[int]]:
    batches: List[List[int]] = []
    current: List[int] = []
    size = 0
    for i in indices:
        length = len(_one_line(texts[i])) + 1
        if current and (len(current) >= MAX_BATCH_ROWS or size + length > MAX_BATCH_CHARS):
            batches.append(current)
            current, size = [], 0
        current.append(i)
        size += length
    if current:
        batches.append(current)
    return batches


def _screen_batch(indices: List[int], texts: Sequence[str], blocking: FrozenSet[str], out: DocumentScreen) -> bool:
    """Screens rows together; a flagged batch is split until single rows. False stops the run."""
    if out.calls >= MAX_CALLS_PER_BATCH:
        out.error = out.error or "RequestCapReached"
        return False
    out.calls += 1
    verdict = sanitize_user_prompt("\n".join(_one_line(texts[i]) for i in indices), blocking=blocking, label="document")
    if not verdict.ok:
        out.error = verdict.error
        return False
    if not verdict.blocked:
        out.screened.update(indices)
        return True
    if len(indices) == 1:
        out.screened.add(indices[0])
        out.flagged[indices[0]] = [name for name in verdict.filters_matched if name in blocking] or verdict.filters_matched
        return True
    middle = len(indices) // 2
    return (_screen_batch(indices[:middle], texts, blocking, out)
            and _screen_batch(indices[middle:], texts, blocking, out))


def screen_documents(texts: Sequence[str], blocking: FrozenSet[str] = DOCUMENT_BLOCKING) -> DocumentScreen:
    """Screens many short texts (bank narrations) in batched requests; empty texts are skipped."""
    out = DocumentScreen()
    indices = [i for i, text in enumerate(texts) if str(text or "").strip()]
    with otel_span("model_armor.screen_documents", {"model_armor.rows": len(indices)}) as span:
        for batch in _batches(indices, texts):
            if not _screen_batch(batch, texts, blocking, out):
                break
        if span is not None:
            span.set_attribute("model_armor.requests", out.calls)
            span.set_attribute("model_armor.rows_flagged", len(out.flagged))
            span.set_attribute("model_armor.rows_unscreened", len(indices) - len(out.screened))
            if out.error:
                span.set_attribute("model_armor.error", out.error)
    if out.error:
        logger.warning("Model Armor screened %d of %d rows (%s); the rest keep the regex screen's result.",
                       len(out.screened), len(indices), out.error)
    return out
