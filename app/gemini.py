# app/gemini.py
"""Reading Gemini replies (DEC-042).

The Vertex AI SDK's `response.text` raises "Multiple content parts are not supported" as soon as
a reply has more than one part. Gemini 3 replies carry a thought signature on their parts and
may split an answer over several parts or add a part that holds only a signature; thought
summaries (parts marked `thought`) are not part of the answer. `reply_text` joins the answer
parts of the first candidate instead, the way the newer google-genai SDK reads a reply.

Errors never include the reply itself: a statement or receipt reply holds financial data, and
the callers log exception messages.

The model names live in config.py (`MODEL_NAME`, `MODEL_LITE`, `MODEL_PRO`).
"""
import contextvars
import logging
import os
import threading

from google.api_core import exceptions as google_exceptions

logger = logging.getLogger(__name__)


def _env_timeout_seconds(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %.1f s.", name, raw, default)
        return default


# Per-call wall-clock & GAPIC timeout for non-streaming Vertex AI extraction calls (DEC-072).
# Set VERTEX_CALL_TIMEOUT_SECONDS=0 to disable.
DEFAULT_CALL_TIMEOUT_SECONDS = _env_timeout_seconds("VERTEX_CALL_TIMEOUT_SECONDS", 240.0)
_active_call_timeout: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "vertex_call_timeout", default=None
)
_gapic_patch_lock = threading.Lock()
_gapic_patched = False


def _run_bounded(fn, timeout_s: float | None):
    """Runs `fn()` in a daemon thread copying `contextvars` so stalled sockets cannot block forever."""
    if not timeout_s or timeout_s <= 0:
        return fn()
    ctx = contextvars.copy_context()
    box: list = [None, None]

    def _target() -> None:
        try:
            box[0] = ctx.run(fn)
        except BaseException as exc:  # noqa: BLE001
            box[1] = exc

    worker = threading.Thread(target=_target, name="vertex-call-guard", daemon=True)
    worker.start()
    worker.join(timeout=timeout_s)
    if worker.is_alive():
        raise google_exceptions.DeadlineExceeded(
            f"Vertex AI generate_content did not respond within {timeout_s:.1f} s"
        )
    if box[1] is not None:
        raise box[1]
    return box[0]


def _ensure_gapic_timeout_installed() -> None:
    """Installs a default GAPIC per-RPC timeout + socket-hang guard on `PredictionServiceClient.generate_content`."""
    global _gapic_patched
    if _gapic_patched:
        return
    with _gapic_patch_lock:
        if _gapic_patched:
            return
        try:
            from google.api_core.gapic_v1.method import DEFAULT as GAPIC_DEFAULT
            from google.cloud.aiplatform_v1.services.prediction_service import PredictionServiceClient

            if not getattr(PredictionServiceClient.generate_content, "_pfa_timeout_wrapped", False):
                orig_gapic = PredictionServiceClient.generate_content

                def _timed_gapic_generate_content(client_self, *args, **kwargs):
                    configured = _active_call_timeout.get()
                    timeout_s = DEFAULT_CALL_TIMEOUT_SECONDS if configured is None else configured
                    if timeout_s and timeout_s > 0 and kwargs.get("timeout", GAPIC_DEFAULT) is GAPIC_DEFAULT:
                        kwargs["timeout"] = timeout_s
                        return _run_bounded(
                            lambda: orig_gapic(client_self, *args, **kwargs),
                            timeout_s + 5.0,
                        )
                    return orig_gapic(client_self, *args, **kwargs)

                _timed_gapic_generate_content._pfa_timeout_wrapped = True  # type: ignore[attr-defined]
                PredictionServiceClient.generate_content = _timed_gapic_generate_content
            _gapic_patched = True
        except Exception as exc:  # pragma: no cover - defensive if SDK internals change
            logger.debug("Could not install GAPIC timeout wrapper: %s", exc)


def generate_with_timeout(model, contents, *, generation_config=None, timeout_seconds: float | None = None):
    """Calls `model.generate_content(contents, generation_config=...)` with a bounded per-call timeout.

    - Real `GenerativeModel` instances enforce the timeout at the GAPIC `PredictionServiceClient`
      layer (so outer retry wrappers on `GenerativeModel.generate_content` are never cut short).
    - Test doubles (`not isinstance(model, GenerativeModel)`) are bounded directly via `_run_bounded`.
    """
    eff_timeout = DEFAULT_CALL_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    _ensure_gapic_timeout_installed()
    token = _active_call_timeout.set(eff_timeout)
    try:
        from vertexai.generative_models import GenerativeModel

        is_sdk_model = isinstance(model, GenerativeModel)
    except Exception:
        is_sdk_model = False

    try:
        if is_sdk_model:
            return model.generate_content(contents, generation_config=generation_config)
        return _run_bounded(
            lambda: model.generate_content(contents, generation_config=generation_config),
            eff_timeout,
        )
    finally:
        _active_call_timeout.reset(token)


def reply_text(response) -> str:
    """The answer text of a Gemini reply: its non-thought text parts, joined in order."""
    candidates = getattr(response, "candidates", None)
    if candidates is None:  # not an SDK reply (a test double): its text is the answer
        return response.text
    if not candidates:
        raise ValueError("Gemini returned no answer; the request may have been blocked.")
    candidate = candidates[0]
    texts = []
    for part in candidate.content.parts:
        data = part.to_dict()
        if data.get("thought"):
            continue
        if data.get("text"):
            texts.append(data["text"])
    if not texts:
        reason = getattr(getattr(candidate, "finish_reason", None), "name", "unknown")
        raise ValueError(f"Gemini's reply has no answer text (finish reason: {reason}).")
    return "".join(texts)
