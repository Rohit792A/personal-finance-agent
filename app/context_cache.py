# app/context_cache.py
"""Vertex AI Context Caching (Prefix Caching) Manager.

FDE Pillar 4: OPTIMIZE.
Caches static fiduciary persona instructions, Indian tax/financial guidelines, and MCP tool
definitions across turns with a configurable TTL (default: 2 hours / 7200s) to slash input
token cost (up to 75% on cached prefixes) and reduce Time-to-First-Token (TTFT).
"""
import datetime
import hashlib
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PrefixContextCacheManager:
    """Manages static prompt prefix caching with Vertex AI CachedContent and local hit tracking."""

    _registry: Dict[str, Dict[str, Any]] = {}
    _stats: Dict[str, int] = {
        "cache_hits": 0,
        "cache_misses": 0,
        "estimated_tokens_saved": 0,
    }

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
        """Looks up or registers a static prompt prefix in the TTL cache (and Vertex AI Context Cache when enabled)."""
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
                "vertex_cached_content": entry.get("vertex_cached_content"),
                "approx_tokens": approx_tokens,
                "hit_count": entry["hits"],
            }

        # Cache miss -> register prefix
        PrefixContextCacheManager._stats["cache_misses"] += 1
        vertex_cached_obj = None

        if os.getenv("ENABLE_VERTEX_CONTEXT_CACHE", "").strip() in ("1", "true", "yes"):
            try:
                from vertexai.preview import caching
                from vertexai.generative_models import Part

                vertex_cached_obj = caching.CachedContent.create(
                    model_name=model_name,
                    system_instruction=system_instruction or static_prefix,
                    contents=[Part.from_text(static_prefix)],
                    ttl=datetime.timedelta(seconds=self.ttl_seconds),
                )
            except Exception as e:
                logger.debug(f"Vertex AI remote CachedContent registration skipped (using local prefix cache): {e}")

        PrefixContextCacheManager._registry[key] = {
            "cache_key": key,
            "model_name": model_name,
            "created_at": now,
            "expires_at": now + self.ttl_seconds,
            "approx_tokens": approx_tokens,
            "hits": 0,
            "vertex_cached_content": vertex_cached_obj,
        }
        logger.info(f"[Prefix Cache MISS -> REGISTERED] key={key[:8]} model={model_name} ttl={self.ttl_seconds}s")
        return {
            "cache_hit": False,
            "cache_key": key,
            "model_name": model_name,
            "vertex_cached_content": vertex_cached_obj,
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


    def get_stats(self) -> Dict[str, Any]:
        """Returns prefix cache hit/miss metrics and active cached entries."""
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
        return {
            **PrefixContextCacheManager._stats,
            "active_entries": len(active),
            "entries": active,
        }
