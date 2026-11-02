# app/telemetry/usage.py
"""Vertex AI token usage read from Gemini replies (`usage_metadata`), DEC-063.

FDE Pillar 4: OPTIMIZE (measured, not estimated).
Every Gemini call in the app hands its reply to `record_usage()`: the advisor's chat and insight
calls, and the statement, receipt, spreadsheet and voice readers. The counts come from the reply's
`usage_metadata`, so they are what Vertex AI reports, not a local guess:

- `cached_content_token_count` is the part of the prompt Vertex AI served from a cache. It covers
  explicit caches (`context_cache.PrefixContextCacheManager.get_explicit_cache`) and implicit
  caching, which Gemini applies on its own when a request repeats a recent prompt prefix.
- `prompt_token_count`, `candidates_token_count`, `thoughts_token_count`, `total_token_count`.

Totals live in memory, per process (one Cloud Run instance), and reset when it restarts. They are
exposed through `PrefixContextCacheManager.get_stats()` and, per call, as attributes of the model
call's trace span. Replies without `usage_metadata` (test doubles) are not counted.
"""
import copy
import threading
from typing import Any, Dict

# usage_metadata field -> short name used in the totals and in span attributes.
_FIELDS = {
    "prompt_token_count": "prompt_tokens",
    "cached_content_token_count": "cached_content_tokens",
    "candidates_token_count": "candidates_tokens",
    "thoughts_token_count": "thoughts_tokens",
    "total_token_count": "total_tokens",
}

_LOCK = threading.Lock()


def _blank() -> Dict[str, int]:
    totals = {"calls_reported": 0, "calls_with_cached_tokens": 0, "explicit_cache_calls": 0}
    totals.update({short: 0 for short in _FIELDS.values()})
    return totals


_TOTALS: Dict[str, int] = _blank()
_BY_SOURCE: Dict[str, Dict[str, int]] = {}
_BY_MODEL: Dict[str, Dict[str, int]] = {}


def usage_from_response(response) -> Dict[str, int]:
    """The reply's token counts by short name, or {} when it carries no `usage_metadata`."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return {}
    counts = {}
    for field, short in _FIELDS.items():
        try:
            counts[short] = int(getattr(meta, field, 0) or 0)
        except (TypeError, ValueError):
            counts[short] = 0
    return counts


def _add(bucket: Dict[str, int], counts: Dict[str, int], explicit_cache: bool) -> None:
    bucket["calls_reported"] += 1
    if counts.get("cached_content_tokens", 0) > 0:
        bucket["calls_with_cached_tokens"] += 1
    if explicit_cache:
        bucket["explicit_cache_calls"] += 1
    for short in _FIELDS.values():
        bucket[short] += counts.get(short, 0)


def record_usage(response, model: str, source: str, explicit_cache: bool = False) -> Dict[str, Any]:
    """Adds one reply's token counts to the totals and returns them as span attributes.

    `source` names the caller (e.g. "advisor.chat", "statement_reader"); `explicit_cache` is True
    when the request referenced an explicit Vertex AI cache.
    """
    counts = usage_from_response(response)
    if not counts:
        return {}
    with _LOCK:
        _add(_TOTALS, counts, explicit_cache)
        _add(_BY_SOURCE.setdefault(source or "unknown", _blank()), counts, explicit_cache)
        _add(_BY_MODEL.setdefault(model or "unknown", _blank()), counts, explicit_cache)
    attributes: Dict[str, Any] = {f"usage.{short}": value for short, value in counts.items()}
    attributes["usage.explicit_cache"] = bool(explicit_cache)
    return attributes


def _with_share(bucket: Dict[str, int]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(bucket)
    prompt = bucket.get("prompt_tokens", 0)
    out["cached_share_pct"] = round(100.0 * bucket.get("cached_content_tokens", 0) / prompt, 1) if prompt else 0.0
    return out


def usage_snapshot() -> Dict[str, Any]:
    """Totals since the process started, overall, per caller and per model."""
    with _LOCK:
        totals = copy.deepcopy(_TOTALS)
        by_source = copy.deepcopy(_BY_SOURCE)
        by_model = copy.deepcopy(_BY_MODEL)
    return {
        **_with_share(totals),
        "by_source": {name: _with_share(bucket) for name, bucket in by_source.items()},
        "by_model": {name: _with_share(bucket) for name, bucket in by_model.items()},
    }


def reset_usage() -> None:
    """Clears the totals (tests)."""
    with _LOCK:
        _TOTALS.clear()
        _TOTALS.update(_blank())
        _BY_SOURCE.clear()
        _BY_MODEL.clear()
