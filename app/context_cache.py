# app/context_cache.py
"""Vertex AI Context Caching (Prefix Caching) Manager.

FDE Pillar 4: OPTIMIZE.
Caches static fiduciary persona instructions, Indian tax/financial guidelines, and MCP tool
definitions across turns with a configurable TTL (default: 2 hours / 7200s) to slash input
token cost (up to 75% on cached prefixes) and reduce Time-to-First-Token (TTFT).

What saves tokens and what is bookkeeping (DEC-063):
- `get_explicit_cache()`: with `ENABLE_VERTEX_CONTEXT_CACHE=1`, creates an explicit Vertex AI
  cache (`vertexai.caching.CachedContent`, the same `vertexai` SDK the advisor generates with)
  per model, lazily, holding the static system instruction. The advisor passes it to Gemini via
  `GenerativeModel.from_cached_content`, and then sends no system instruction (the API rejects a
  request that repeats what the cache holds). If creating it fails (for example, the prefix is
  under the model's minimum cacheable size), explicit caching is switched off for that model and
  requests go out exactly as before.
- `get_or_register_prefix()` / `get_or_create_prefix()`: a local, in-memory registry of prompt
  prefixes. Its "hits" and `estimated_tokens_saved` (characters / 4) are local bookkeeping only;
  on their own they save no tokens.
- Measured savings: `get_stats()["vertex_usage"]` totals `usage_metadata` from every Gemini reply
  (telemetry/usage.py). `cached_content_tokens` there is what Vertex AI actually served from a
  cache, explicit or implicit (Gemini caches repeated prompt prefixes on its own).
"""
import datetime
import hashlib
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from telemetry.usage import usage_snapshot

logger = logging.getLogger(__name__)

# Recreate an explicit cache this long before it expires, so a request never refers to an expired one.
_EXPLICIT_REFRESH_MARGIN_S = 120


def explicit_cache_enabled() -> bool:
    """ENABLE_VERTEX_CONTEXT_CACHE is 1 / true / yes."""
    return os.getenv("ENABLE_VERTEX_CONTEXT_CACHE", "").strip().lower() in ("1", "true", "yes")


class PrefixContextCacheManager:
    """Manages static prompt prefix caching with Vertex AI CachedContent and local hit tracking."""

    _registry: Dict[str, Dict[str, Any]] = {}
    _stats: Dict[str, int] = {
        "cache_hits": 0,
        "cache_misses": 0,
        "estimated_tokens_saved": 0,
    }
    # Explicit Vertex AI caches (DEC-063), per model: one per tier, shared by tiers on one model.
    _explicit: Dict[str, Dict[str, Any]] = {}
    _explicit_disabled: Dict[str, str] = {}
    _explicit_lock = threading.Lock()

    def __init__(self, ttl_seconds: int = 7200):
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def compute_prefix_key(model_name: str, static_prefix: str) -> str:
        raw = f"{model_name}||{static_prefix.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def get_or_register_prefix(
        self,
        model_name: str,
        static_prefix: str,
        system_instruction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Looks up or registers a static prompt prefix in the local TTL registry.

        Local bookkeeping only (DEC-063): a "hit" means this process has seen the prefix before,
        not that Vertex AI served it from a cache. `vertex_cached_content` is the explicit cache
        for this model and prefix if `get_explicit_cache()` created one, else None.
        """
        key = self.compute_prefix_key(model_name, static_prefix)
        now = time.time()
        approx_tokens = max(1, len(static_prefix) // 4)

        entry = PrefixContextCacheManager._registry.get(key)
        if entry and entry["expires_at"] > now:
            entry["hits"] += 1
            PrefixContextCacheManager._stats["cache_hits"] += 1
            PrefixContextCacheManager._stats["estimated_tokens_saved"] += approx_tokens
            logger.info(
                f"[Prefix Cache HIT] key={key[:8]} model={model_name} "
                f"(saved ~{approx_tokens} tokens, total hits={entry['hits']})"
            )
            return {
                "cache_hit": True,
                "cache_key": key,
                "model_name": model_name,
                "vertex_cached_content": self._explicit_for(model_name, static_prefix),
                "approx_tokens": approx_tokens,
                "hit_count": entry["hits"],
            }

        # Cache miss -> register prefix
        PrefixContextCacheManager._stats["cache_misses"] += 1
        PrefixContextCacheManager._registry[key] = {
            "cache_key": key,
            "model_name": model_name,
            "created_at": now,
            "expires_at": now + self.ttl_seconds,
            "approx_tokens": approx_tokens,
            "hits": 0,
        }
        logger.info(f"[Prefix Cache MISS -> REGISTERED] key={key[:8]} model={model_name} ttl={self.ttl_seconds}s")
        return {
            "cache_hit": False,
            "cache_key": key,
            "model_name": model_name,
            "vertex_cached_content": self._explicit_for(model_name, static_prefix),
            "approx_tokens": approx_tokens,
            "hit_count": 0,
        }

    def get_or_create_prefix(
        self,
        model_name: str,
        static_prefix: Optional[str] = None,
        system_instruction: Optional[str] = None,
        tool_schemas: Optional[Any] = None,
        ledger_snapshot: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Convenience wrapper combining system_instruction, tool_schemas, and ledger_snapshot into a cached prefix."""
        combined = static_prefix or f"{system_instruction or ''}\n{tool_schemas or ''}\n{ledger_snapshot or ''}"
        return self.get_or_register_prefix(
            model_name=model_name,
            static_prefix=combined,
            system_instruction=system_instruction,
        )

    # ------------------------------------------------------------------------------------------
    # Explicit Vertex AI context caches (DEC-063)
    # ------------------------------------------------------------------------------------------

    @classmethod
    def _explicit_for(cls, model_name: str, system_instruction: str):
        entry = cls._explicit.get(model_name)
        if entry and entry["prefix_key"] == cls.compute_prefix_key(model_name, system_instruction):
            return entry["cached_content"]
        return None

    def get_explicit_cache(self, model_name: str, system_instruction: str):
        """The explicit Vertex AI cache holding `system_instruction` for `model_name`, or None.

        Created on first use and recreated shortly before it expires. None when
        ENABLE_VERTEX_CONTEXT_CACHE is off, or when explicit caching is switched off for the model
        (a failed creation or a failed request with the cache); the caller then sends its request
        the usual way.
        """
        if not explicit_cache_enabled() or not system_instruction:
            return None
        key = self.compute_prefix_key(model_name, system_instruction)
        cls = PrefixContextCacheManager
        with cls._explicit_lock:
            if model_name in cls._explicit_disabled:
                return None
            entry = cls._explicit.get(model_name)
            now = time.time()
            if entry and entry["prefix_key"] == key and entry["expires_at"] - now > _EXPLICIT_REFRESH_MARGIN_S:
                entry["uses"] += 1
                return entry["cached_content"]
            try:
                from vertexai.caching import CachedContent

                cached = CachedContent.create(
                    model_name=model_name,
                    system_instruction=system_instruction,
                    ttl=datetime.timedelta(seconds=self.ttl_seconds),
                    display_name="pfa-advisor-static-prefix",
                )
            except Exception as e:
                reason = f"{type(e).__name__}: {str(e)[:300]}"
                cls._explicit_disabled[model_name] = reason
                logger.warning(
                    "[Context Cache] Explicit Vertex AI cache for %s not created (%s); "
                    "sending requests without it from now on.", model_name, reason)
                return None
            cls._explicit[model_name] = {
                "cached_content": cached,
                "name": getattr(cached, "resource_name", None) or getattr(cached, "name", None),
                "prefix_key": key,
                "created_at": now,
                "expires_at": now + self.ttl_seconds,
                "uses": 1,
            }
            logger.info("[Context Cache] Created explicit Vertex AI cache for %s (ttl %ss).", model_name, self.ttl_seconds)
            return cached

    def disable_explicit_cache(self, model_name: str, reason: str) -> None:
        """Stops using explicit caching for `model_name` (e.g. a request with the cache failed)."""
        cls = PrefixContextCacheManager
        with cls._explicit_lock:
            cls._explicit_disabled[model_name] = reason
            cls._explicit.pop(model_name, None)
        logger.warning("[Context Cache] Explicit caching switched off for %s: %s", model_name, reason)

    def explicit_cache_attributes(self, model_name: str, cached) -> Dict[str, Any]:
        """Trace-span attributes for the explicit cache; {} when ENABLE_VERTEX_CONTEXT_CACHE is off."""
        if not explicit_cache_enabled():
            return {}
        if cached is not None:
            entry = PrefixContextCacheManager._explicit.get(model_name) or {}
            return {"explicit_cache": "active", "explicit_cache.name": entry.get("name") or ""}
        reason = PrefixContextCacheManager._explicit_disabled.get(model_name, "")
        return {"explicit_cache": "unavailable", "explicit_cache.reason": reason[:200]}

    @classmethod
    def delete_explicit_caches(cls) -> int:
        """Deletes the explicit caches this process created (best effort). Returns how many."""
        with cls._explicit_lock:
            entries = list(cls._explicit.values())
            cls._explicit.clear()
        deleted = 0
        for entry in entries:
            try:
                entry["cached_content"].delete()
                deleted += 1
            except Exception as e:
                logger.warning("[Context Cache] Could not delete %s: %s", entry.get("name"), type(e).__name__)
        return deleted

    def get_stats(self) -> Dict[str, Any]:
        """Returns prefix cache hit/miss metrics and active cached entries, plus (DEC-063) the
        measured Vertex AI token usage and the state of the explicit caches."""
        now = time.time()
        active = [
            {
                "cache_key": v["cache_key"],
                "model_name": v["model_name"],
                "approx_tokens": v["approx_tokens"],
                "hits": v["hits"],
                "ttl_remaining_s": max(0, int(v["expires_at"] - now)),
            }
            for v in PrefixContextCacheManager._registry.values()
            if v["expires_at"] > now
        ]
        explicit = [
            {
                "model_name": model,
                "name": entry.get("name"),
                "uses": entry.get("uses", 0),
                "ttl_remaining_s": max(0, int(entry["expires_at"] - now)),
            }
            for model, entry in PrefixContextCacheManager._explicit.items()
        ]
        return {
            **PrefixContextCacheManager._stats,
            "active_entries": len(active),
            "entries": active,
            "vertex_usage": usage_snapshot(),
            "explicit_caching": {
                "enabled": explicit_cache_enabled(),
                "caches": explicit,
                "disabled_models": dict(PrefixContextCacheManager._explicit_disabled),
            },
        }
