# tests/test_context_cache.py
"""Explicit Vertex AI context caching and measured token usage (DEC-063), Vertex AI faked.

With ENABLE_VERTEX_CONTEXT_CACHE=1 the advisor's static persona goes into one explicit cache per
model (created lazily with `vertexai.caching.CachedContent.create`), and generation passes it as
`cached_content` without repeating the system instruction. A failed creation switches explicit
caching off for that model and the request goes out as before. Token usage comes from each
reply's `usage_metadata`. No network or credentials.
"""
import datetime
import time

import pytest
from google.api_core import exceptions as google_exceptions
from vertexai import caching
from vertexai.generative_models import GenerationResponse

import config
from context_cache import PrefixContextCacheManager
from telemetry import usage as usage_module


def gemini_reply(text="OK", prompt=1200, cached=0, candidates=50):
    return GenerationResponse.from_dict({
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finish_reason": "STOP"}],
        "usage_metadata": {"prompt_token_count": prompt, "cached_content_token_count": cached,
                           "candidates_token_count": candidates, "total_token_count": prompt + candidates},
    })


class FakeCache:
    created = []

    def __init__(self, model_name, system_instruction=None, ttl=None, display_name=None, **kwargs):
        self.model_name = f"projects/p/locations/global/publishers/google/models/{model_name}"
        self.system_instruction, self.ttl, self.display_name = system_instruction, ttl, display_name
        self.resource_name = f"projects/p/locations/global/cachedContents/{len(FakeCache.created) + 1}"
        self.deleted = False

    def delete(self):
        self.deleted = True


class CacheAwareModel:
    """Stands in for GenerativeModel, including `from_cached_content`."""
    created = []
    fail_cached_send = None

    def __init__(self, name, **kwargs):
        self.name, self.kwargs, self.cached, self.sent = name, kwargs, None, []
        CacheAwareModel.created.append(self)

    @classmethod
    def from_cached_content(cls, cached_content, **kwargs):
        model = cls(cached_content.model_name)
        model.cached = cached_content
        return model

    def _reply(self):
        if self.cached is not None and CacheAwareModel.fail_cached_send:
            raise CacheAwareModel.fail_cached_send
        return gemini_reply("Cached answer" if self.cached else "Plain answer", cached=1000 if self.cached else 0)

    def generate_content(self, contents, generation_config=None):
        self.sent.append({"contents": contents})
        return self._reply()

    def start_chat(self, history=None):
        model = self

        class Chat:
            def send_message(self, message, generation_config=None):
                model.sent.append({"message": message, "history": list(history or []),
                                   "generation_config": generation_config})
                return model._reply()

        return Chat()


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    for name in ("MODEL_ARMOR_TEMPLATE", "SDP_ENABLED", "ENABLE_VERTEX_CONTEXT_CACHE"):
        monkeypatch.delenv(name, raising=False)
    PrefixContextCacheManager._explicit.clear()
    PrefixContextCacheManager._explicit_disabled.clear()
    usage_module.reset_usage()
    FakeCache.created, CacheAwareModel.created, CacheAwareModel.fail_cached_send = [], [], None
    yield
    PrefixContextCacheManager._explicit.clear()
    PrefixContextCacheManager._explicit_disabled.clear()
    usage_module.reset_usage()


@pytest.fixture
def create_calls(monkeypatch):
    """CachedContent.create replaced by a fake; returns the list of created caches."""
    def create(**kwargs):
        cache = FakeCache(**kwargs)
        FakeCache.created.append(cache)
        return cache

    monkeypatch.setattr(caching.CachedContent, "create", create)
    return FakeCache.created


@pytest.fixture
def advisor(monkeypatch):
    import advisor as advisor_module

    monkeypatch.setattr(advisor_module.vertexai, "init", lambda **kwargs: None)
    monkeypatch.setattr(advisor_module, "GenerativeModel", CacheAwareModel)
    adv = advisor_module.FinancialAdvisor()
    monkeypatch.setattr(adv, "_get_financial_summary_text", lambda month=None: "Synthetic summary: income 1000.")
    return adv


def _span(advisor, name):
    return next(s for s in advisor.last_trace_summary["spans"] if s["name"] == name)


def test_explicit_caching_is_off_by_default(create_calls, advisor):
    assert PrefixContextCacheManager().get_explicit_cache(config.MODEL_NAME, "persona") is None
    assert advisor.chat("How can I spend less on food?", history=[]) == "Plain answer"
    assert create_calls == []
    assert "explicit_cache" not in _span(advisor, "prefix_context_cache")["attributes"]
    model = CacheAwareModel.created[-1]
    assert model.cached is None and "system_instruction" in model.kwargs
    # Usage is recorded with or without the flag (implicit caching shows up here too).
    assert _span(advisor, "vertex_ai_chat_turn")["attributes"]["usage.prompt_tokens"] == 1200


def test_the_chat_passes_the_explicit_cache_and_no_system_instruction(monkeypatch, create_calls, advisor):
    import advisor as advisor_module

    monkeypatch.setenv("ENABLE_VERTEX_CONTEXT_CACHE", "1")
    assert advisor.chat("How can I spend less on food? My PAN is ABCDE1234F", history=[]) == "Cached answer"
    assert len(create_calls) == 1
    cache = create_calls[0]
    assert cache.system_instruction == advisor_module.STATIC_FIDUCIARY_PREFIX
    assert cache.ttl == datetime.timedelta(seconds=7200)
    assert cache.display_name == "pfa-advisor-static-prefix"
    model = CacheAwareModel.created[-1]
    assert model.cached is cache
    assert model.kwargs == {}  # no system_instruction next to cached_content
    sent = model.sent[-1]
    assert sent["generation_config"] == {"top_p": 0.95}
    assert sent["message"].startswith("Real-Time Verified Financial State (via Ledger MCP Server):\n<financial_summary>\n"
                                      "Synthetic summary: income 1000.\n</financial_summary>")
    assert sent["message"].endswith("\n\nUser message:\nHow can I spend less on food? My PAN is [PAN_1]")
    assert advisor_module.STATIC_FIDUCIARY_PREFIX not in sent["message"]
    cache_span = _span(advisor, "prefix_context_cache")["attributes"]
    assert (cache_span["explicit_cache"], cache_span["explicit_cache.name"]) == ("active", cache.resource_name)
    model_span = _span(advisor, "vertex_ai_chat_turn")["attributes"]
    assert (model_span["usage.cached_content_tokens"], model_span["usage.explicit_cache"]) == (1000, True)

    # Created once per model: the next turn (and the Pro tier, on the same model) reuse it.
    assert advisor.chat("Simulate retirement savings for 10 years", history=[]) == "Cached answer"
    assert len(create_calls) == 1
    stats = PrefixContextCacheManager().get_stats()
    assert stats["vertex_usage"]["cached_content_tokens"] == 2000
    assert stats["vertex_usage"]["explicit_cache_calls"] == 2
    assert stats["vertex_usage"]["by_source"]["advisor.chat"]["calls_reported"] == 2
    assert stats["explicit_caching"]["caches"][0]["uses"] == 2


def test_insights_use_the_explicit_cache_too(monkeypatch, create_calls, advisor):
    import advisor as advisor_module

    monkeypatch.setenv("ENABLE_VERTEX_CONTEXT_CACHE", "1")
    assert advisor.generate_insights() == "Cached answer"
    model = CacheAwareModel.created[-1]
    assert model.cached is create_calls[0]
    contents = model.sent[-1]["contents"]
    assert contents.startswith("Real-Time Verified Financial State (from Ledger MCP Server):")
    assert advisor_module.STATIC_FIDUCIARY_PREFIX not in contents


def test_a_failed_cache_creation_switches_explicit_caching_off_for_that_model(monkeypatch, advisor):
    import advisor as advisor_module

    calls = []

    def too_small(**kwargs):
        calls.append(kwargs)
        raise google_exceptions.InvalidArgument("The cached content is of 210 tokens. The minimum token count to start caching is 1024.")

    monkeypatch.setattr(caching.CachedContent, "create", too_small)
    monkeypatch.setenv("ENABLE_VERTEX_CONTEXT_CACHE", "1")
    assert advisor.chat("How can I spend less on food?", history=[]) == "Plain answer"
    model = CacheAwareModel.created[-1]
    assert model.cached is None
    assert model.kwargs["system_instruction"][0].text.startswith(advisor_module.STATIC_FIDUCIARY_PREFIX + "\n\nReal-Time")
    assert model.sent[-1]["message"] == "How can I spend less on food?"
    attrs = _span(advisor, "prefix_context_cache")["attributes"]
    assert attrs["explicit_cache"] == "unavailable"
    assert "minimum token count" in attrs["explicit_cache.reason"]
    assert advisor.chat("Any savings tips?", history=[]) == "Plain answer"
    assert len(calls) == 1  # not retried for this model
    disabled = PrefixContextCacheManager().get_stats()["explicit_caching"]["disabled_models"]
    assert "minimum token count" in disabled[config.MODEL_NAME]


def test_a_failed_request_with_the_cache_is_resent_without_it(monkeypatch, create_calls, advisor):
    monkeypatch.setenv("ENABLE_VERTEX_CONTEXT_CACHE", "1")
    CacheAwareModel.fail_cached_send = google_exceptions.NotFound("cachedContents/1 not found")
    assert advisor.chat("How can I spend less on food?", history=[]) == "Plain answer"
    assert _span(advisor, "vertex_ai_chat_turn")["attributes"]["usage.explicit_cache"] is False
    disabled = PrefixContextCacheManager().get_stats()["explicit_caching"]["disabled_models"]
    assert disabled[config.MODEL_NAME] == "generation with the cache failed (NotFound)"
    assert PrefixContextCacheManager().get_explicit_cache(config.MODEL_NAME, "persona") is None


def test_an_expiring_cache_is_recreated(monkeypatch, create_calls):
    monkeypatch.setenv("ENABLE_VERTEX_CONTEXT_CACHE", "1")
    manager = PrefixContextCacheManager(ttl_seconds=600)
    first = manager.get_explicit_cache("gemini-x", "persona")
    assert manager.get_explicit_cache("gemini-x", "persona") is first
    PrefixContextCacheManager._explicit["gemini-x"]["expires_at"] = time.time() + 30
    assert manager.get_explicit_cache("gemini-x", "persona") is not first
    assert len(create_calls) == 2
    assert manager.get_or_register_prefix("gemini-x", "persona")["vertex_cached_content"] is create_calls[1]
    assert PrefixContextCacheManager.delete_explicit_caches() == 1
    assert create_calls[1].deleted is True


def test_usage_totals_count_implicit_caching_and_skip_test_doubles():
    from types import SimpleNamespace

    assert usage_module.record_usage(SimpleNamespace(text="no usage"), "gemini-x", "unit") == {}
    attrs = usage_module.record_usage(gemini_reply(prompt=4000, cached=3000), "gemini-x", "unit")
    assert attrs["usage.cached_content_tokens"] == 3000 and attrs["usage.explicit_cache"] is False
    usage_module.record_usage(gemini_reply(prompt=1000, cached=0), "gemini-y", "other")
    totals = usage_module.usage_snapshot()
    assert (totals["calls_reported"], totals["calls_with_cached_tokens"], totals["explicit_cache_calls"]) == (2, 1, 0)
    assert totals["cached_share_pct"] == 60.0
    assert totals["by_model"]["gemini-x"]["cached_content_tokens"] == 3000
    assert totals["by_source"]["other"]["prompt_tokens"] == 1000


def test_the_local_prefix_registry_still_tracks_hits():
    manager = PrefixContextCacheManager(ttl_seconds=300)
    entry1 = manager.get_or_create_prefix(model_name=config.MODEL_LITE, system_instruction="You are the CFO.",
                                          tool_schemas=[{"name": "get_financial_summary"}], ledger_snapshot="Income: 1")
    entry2 = manager.get_or_create_prefix(model_name=config.MODEL_LITE, system_instruction="You are the CFO.",
                                          tool_schemas=[{"name": "get_financial_summary"}], ledger_snapshot="Income: 1")
    assert entry1["cache_hit"] is False and entry2["cache_hit"] is True and entry2["hit_count"] >= 1
    assert entry2["vertex_cached_content"] is None
    stats = manager.get_stats()
    assert {"cache_hits", "cache_misses", "estimated_tokens_saved", "active_entries", "entries",
            "vertex_usage", "explicit_caching"} <= set(stats)
    assert stats["explicit_caching"]["enabled"] is False
