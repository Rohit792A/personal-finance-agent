# tests/test_tracing.py
"""OpenTelemetry spans and the optional Cloud Trace export (DEC-063). No network or credentials.

The Cloud Trace exporter is replaced by a fake; the parent/child checks read the in-memory
exporter that every span also goes to.
"""
import logging
from types import SimpleNamespace

import pytest
from vertexai.generative_models import GenerationResponse

import config
from telemetry import tracer as tracer_module
from telemetry import usage as usage_module
from telemetry.tracer import AgentTracer, model_call_span, otel_span, traced_request

pytestmark = pytest.mark.skipif(not tracer_module._OTEL_AVAILABLE, reason="OpenTelemetry SDK not installed")


def gemini_reply(text="OK", prompt=1200, cached=0, candidates=40):
    return GenerationResponse.from_dict({
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finish_reason": "STOP"}],
        "usage_metadata": {"prompt_token_count": prompt, "cached_content_token_count": cached,
                           "candidates_token_count": candidates, "total_token_count": prompt + candidates},
    })


@pytest.fixture
def spans():
    """Finished spans recorded during the test."""
    tracer_module._otel_exporter.clear()
    usage_module.reset_usage()
    yield lambda: list(tracer_module._otel_exporter.get_finished_spans())
    tracer_module._otel_exporter.clear()
    usage_module.reset_usage()


def by_name(span_list, name):
    matches = [s for s in span_list if s.name == name]
    assert matches, f"no span named {name}: {[s.name for s in span_list]}"
    return matches[-1]


# ---------------------------------------------------------------------------------------------
# Cloud Trace export
# ---------------------------------------------------------------------------------------------

def test_cloud_trace_is_off_unless_enabled(monkeypatch):
    monkeypatch.setattr(tracer_module, "_CLOUD_TRACE", {"enabled": False})
    assert "cloud_trace_export" not in tracer_module.get_recent_traces()
    monkeypatch.setattr(tracer_module, "_CLOUD_TRACE", {"enabled": True, "active": True, "project": "p", "error": None})
    assert tracer_module.get_recent_traces()["cloud_trace_export"] == {
        "enabled": True, "active": True, "project": "p", "error": None}


def test_enabling_cloud_trace_adds_a_batch_exporter_next_to_the_in_memory_one(monkeypatch):
    from opentelemetry.exporter import cloud_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanExportResult

    exported, projects = [], []

    class FakeCloudTraceExporter:
        def __init__(self, project_id=None, **kwargs):
            projects.append(project_id)

        def export(self, batch):
            exported.extend(batch)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis=30000):
            return True

    monkeypatch.setattr(cloud_trace, "CloudTraceSpanExporter", FakeCloudTraceExporter)
    provider = TracerProvider()
    status = tracer_module._install_cloud_trace_exporter(provider)
    assert status == {"enabled": True, "active": True, "project": config.PROJECT_ID, "error": None}
    assert projects == [config.PROJECT_ID]
    provider.get_tracer("test").start_span("advisor.chat").end()
    provider.force_flush()
    assert [s.name for s in exported] == ["advisor.chat"]
    provider.shutdown()


def test_a_cloud_trace_exporter_that_cannot_start_is_not_fatal(monkeypatch, caplog):
    from google.auth.exceptions import DefaultCredentialsError
    from opentelemetry.exporter import cloud_trace
    from opentelemetry.sdk.trace import TracerProvider

    def no_credentials(*args, **kwargs):
        raise DefaultCredentialsError("no ADC")

    monkeypatch.setattr(cloud_trace, "CloudTraceSpanExporter", no_credentials)
    with caplog.at_level(logging.WARNING):
        status = tracer_module._install_cloud_trace_exporter(TracerProvider())
    assert status["active"] is False and status["error"] == "DefaultCredentialsError"
    assert "traces stay in memory only" in caplog.text


# ---------------------------------------------------------------------------------------------
# Parent/child links
# ---------------------------------------------------------------------------------------------

class ReplyModel:
    def __init__(self, name, **kwargs):
        self.name = name

    def generate_content(self, contents, generation_config=None):
        return gemini_reply("Insights", prompt=900)

    def start_chat(self, history=None):
        class Chat:
            def send_message(self, message, generation_config=None):
                return gemini_reply("Answer", prompt=1500, cached=1024)

        return Chat()


@pytest.fixture
def advisor(monkeypatch):
    import advisor as advisor_module

    for name in ("MODEL_ARMOR_TEMPLATE", "SDP_ENABLED", "ENABLE_VERTEX_CONTEXT_CACHE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(advisor_module.vertexai, "init", lambda **kwargs: None)
    monkeypatch.setattr(advisor_module, "GenerativeModel", ReplyModel)
    adv = advisor_module.FinancialAdvisor()
    monkeypatch.setattr(adv, "_get_financial_summary_text", lambda month=None: "Synthetic summary: income 1000.")
    return adv


def test_a_chat_turn_is_one_trace_with_the_steps_as_children(spans, advisor):
    assert advisor.chat("My PAN is ABCDE1234F. How can I save more?", history=[]) == "Answer"
    recorded = spans()
    root = by_name(recorded, "advisor.chat")
    assert root.parent is None
    assert root.attributes["pfa.trace_id"] == advisor.last_trace_summary["trace_id"]
    assert root.attributes["pfa.workflow"] == "advisor_chat"
    steps = ["model_armor_screen_prompt", "cloud_dlp_redact", "prefix_context_cache", "mcp_context_snapshot",
             "vertex_ai_chat_turn"]
    for name in steps:
        span = by_name(recorded, name)
        assert span.parent.span_id == root.context.span_id, name
        assert span.context.trace_id == root.context.trace_id, name
    assert by_name(recorded, "model_armor_screen_prompt").attributes["decision_layer"] == "regex"
    dlp_span = by_name(recorded, "cloud_dlp_redact")
    assert (dlp_span.attributes["decision_layer"], dlp_span.attributes["entities_redacted"]) == ("regex", "1")
    model_span = by_name(recorded, "vertex_ai_chat_turn")
    assert model_span.attributes["usage.cached_content_tokens"] == "1024"
    assert model_span.attributes["usage.prompt_tokens"] == "1500"
    # The in-memory summary (GET /telemetry/traces) has the same steps and no root span.
    assert [s["name"] for s in advisor.last_trace_summary["spans"]] == steps
    assert advisor.last_trace_summary["spans"][-1]["attributes"]["usage.cached_content_tokens"] == 1024


def test_insights_are_one_trace_too(spans, advisor):
    assert advisor.generate_insights() == "Insights"
    recorded = spans()
    root = by_name(recorded, "advisor.generate_insights")
    for name in ("mcp_context_snapshot", "prefix_context_cache", "vertex_ai_generate_insights"):
        assert by_name(recorded, name).parent.span_id == root.context.span_id
    assert usage_module.usage_snapshot()["by_source"]["advisor.insights"]["prompt_tokens"] == 900


def test_mcp_tool_spans_nest_under_the_active_span(spans):
    from mcp.ledger_server import LedgerMCPServer

    tracer = AgentTracer(workflow_name="unit")
    with tracer.request_span("advisor.chat"):
        with tracer.start_span("mcp_context_snapshot"):
            result = LedgerMCPServer().execute_tool("no_such_tool", {}, caller_agent_id="advisor-agent")
    assert result["is_error"] is True
    recorded = spans()
    root, snapshot, tool = (by_name(recorded, n) for n in ("advisor.chat", "mcp_context_snapshot", "mcp_tool_no_such_tool"))
    assert snapshot.parent.span_id == root.context.span_id
    assert tool.parent.span_id == snapshot.context.span_id
    assert tool.attributes["tool"] == "no_such_tool"
    assert tool.status.status_code.name == "ERROR"


def test_the_model_armor_api_call_nests_under_the_screen_span(spans, advisor, monkeypatch):
    from google.cloud import modelarmor_v1 as ma
    from security import model_armor_api

    def no_match():
        return ma.SanitizationResult(filter_match_state=ma.FilterMatchState.NO_MATCH_FOUND,
                                     invocation_result=ma.InvocationResult.SUCCESS)

    class Client:
        def sanitize_user_prompt(self, request=None, retry=None, timeout=None):
            return ma.SanitizeUserPromptResponse(sanitization_result=no_match())

        def sanitize_model_response(self, request=None, retry=None, timeout=None):
            return ma.SanitizeModelResponseResponse(sanitization_result=no_match())

    monkeypatch.setenv("MODEL_ARMOR_TEMPLATE", "projects/demo/locations/asia-northeast1/templates/guard")
    monkeypatch.setattr(model_armor_api, "_client", lambda location: Client())
    assert advisor.chat("How can I save more?", history=[]) == "Answer"
    recorded = spans()
    screen = by_name(recorded, "model_armor_screen_prompt")
    api_call = by_name(recorded, "model_armor.sanitize_user_prompt")
    assert api_call.parent.span_id == screen.context.span_id
    assert api_call.attributes["decision_layer"] == "model_armor_api"
    assert api_call.attributes["model_armor.location"] == "asia-northeast1"
    reply_check = by_name(recorded, "model_armor.sanitize_model_response")
    assert reply_check.parent.span_id == by_name(recorded, "model_armor_screen_response").context.span_id


def test_model_call_spans_record_token_usage_and_errors(spans):
    with model_call_span("vertex_ai.unit", "gemini-x", "unit_test", {"pages": "pages 1-2"}) as call:
        attributes = call.record(gemini_reply(prompt=2000, cached=1500), explicit_cache=True)
    assert attributes["usage.cached_content_tokens"] == 1500
    with pytest.raises(RuntimeError):
        with model_call_span("vertex_ai.unit", "gemini-x", "unit_test"):
            raise RuntimeError("synthetic failure with data 9000000001")
    first, failed = spans()
    assert first.attributes["usage.prompt_tokens"] == 2000
    assert first.attributes["gen_ai.request.model"] == "gemini-x"
    assert first.attributes["pages"] == "pages 1-2"
    assert failed.status.status_code.name == "ERROR"
    assert failed.attributes["error.type"] == "RuntimeError"
    assert "9000000001" not in str(dict(failed.attributes))  # exception messages are not recorded
    totals = usage_module.usage_snapshot()
    assert totals["by_source"]["unit_test"]["explicit_cache_calls"] == 1
    assert totals["cached_share_pct"] == 75.0


def test_statement_reader_calls_join_the_upload_trace(spans, monkeypatch):
    import statement_agent

    class PageModel:
        def generate_content(self, contents, generation_config=None):
            return GenerationResponse.from_dict({
                "candidates": [{"content": {"role": "model", "parts": [{"text": '{"transactions": []}'}]},
                                "finish_reason": "STOP"}],
                "usage_metadata": {"prompt_token_count": 3000, "candidates_token_count": 10, "total_token_count": 3010},
            })

    monkeypatch.setattr(statement_agent, "pdf_needs_password", lambda data: False)
    monkeypatch.setattr(statement_agent, "page_row_counts", lambda data: [0, 0, 0, 0])
    monkeypatch.setattr(statement_agent, "pages_as_pdf", lambda data, span: b"%PDF-1.4 synthetic")
    extractor = statement_agent.StatementExtractor.__new__(statement_agent.StatementExtractor)
    extractor.date_notes, extractor.coverage_notes, extractor.pages_read, extractor.model = [], [], 0, PageModel()
    tracer = AgentTracer(workflow_name="process_file_ingestion")
    with tracer.request_span("ingestion.process_file"):
        with tracer.start_span("ingestion_agent.extract_pdf"):
            assert extractor._read_statement(b"%PDF", "context") == []
    recorded = spans()
    extract = by_name(recorded, "ingestion_agent.extract_pdf")
    calls = [s for s in recorded if s.name == "vertex_ai.statement_reader"]
    assert len(calls) == 2  # 4 pages, 2 per call, read in parallel threads
    assert all(c.parent.span_id == extract.context.span_id for c in calls)
    assert sorted(c.attributes["pages"] for c in calls) == ["pages 1-2", "pages 3-4"]
    assert usage_module.usage_snapshot()["by_source"]["statement_reader"]["prompt_tokens"] == 6000


def test_traced_request_starts_a_root_span(spans):
    @traced_request("a2a.unit_workflow")
    def workflow():
        with otel_span("child"):
            return "done"

    assert workflow() == "done"
    recorded = spans()
    root, child = by_name(recorded, "a2a.unit_workflow"), by_name(recorded, "child")
    assert root.parent is None and child.parent.span_id == root.context.span_id
    assert root.attributes["pfa.app_env"] in ("personal", "demo")


def test_the_in_memory_exporter_is_bounded():
    exporter = tracer_module._RecentSpansExporter(max_spans=3)
    exporter.export([SimpleNamespace(name=str(i)) for i in range(5)])
    assert [s.name for s in exporter.get_finished_spans()] == ["2", "3", "4"]
