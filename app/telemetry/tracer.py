# app/telemetry/tracer.py
"""OpenTelemetry Distributed Tracing & Trajectory Bottleneck Analyzer.

FDE Pillar 3: SCALE.
Integrates the official OpenTelemetry SDK (`opentelemetry-api`, `opentelemetry-sdk`)
to capture distributed execution spans across Model Armor, Cloud DLP, A2A delegation,
MCP tool execution, and Vertex AI model turns. Includes `analyze_weakest_step()` to
find the highest-error or highest-latency step in agent trajectories.

Where spans go (DEC-063):
- Always: a bounded in-memory exporter, plus the per-workflow summaries in
  `GLOBAL_TRACE_HISTORY` that back `GET /telemetry/traces`.
- With `ENABLE_CLOUD_TRACE=1`: also Google Cloud Trace, through a `BatchSpanProcessor` with the
  official `CloudTraceSpanExporter` (`opentelemetry-exporter-gcp-trace`), into `config.PROJECT_ID`.
  If the exporter can't start (package missing, no credentials), traces stay in memory and a
  warning is logged.

Parent/child links: `AgentTracer.request_span()` opens one root span per request (chat, insights,
an upload) and makes it current, so every span started inside it joins that trace: AgentTracer
spans, MCP tool calls, the Model Armor and Sensitive Data Protection API calls (`otel_span()`)
and the Gemini calls (`model_call_span()`). `with tracer.start_span(...)` and
`tracer.activate(span_id)` make a span the parent of the spans started inside the block. The root
span is not added to the in-memory summaries, so `find_weakest_step()` ranks the same steps as
before.
"""
import contextlib
import functools
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Initialize OpenTelemetry SDK TracerProvider + a bounded in-memory exporter (+ Cloud Trace, opt-in)
_OTEL_AVAILABLE = False
_otel_tracer = None
_otel_exporter = None
_CLOUD_TRACE: Dict[str, Any] = {"enabled": False}


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def _install_cloud_trace_exporter(provider) -> Dict[str, Any]:
    """Adds a BatchSpanProcessor + CloudTraceSpanExporter (ENABLE_CLOUD_TRACE=1). Never raises."""
    status: Dict[str, Any] = {"enabled": True, "active": False, "project": None, "error": None}
    try:
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        import config  # lazy: the project the rest of the app uses for Vertex AI

        status["project"] = config.PROJECT_ID
        provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter(project_id=config.PROJECT_ID)))
        status["active"] = True
        logger.info("Cloud Trace export is on: spans are also sent to project %s.", config.PROJECT_ID)
    except Exception as e:
        status["error"] = type(e).__name__
        logger.warning(
            "ENABLE_CLOUD_TRACE is set but Cloud Trace export could not start (%s); traces stay in memory only.",
            type(e).__name__,
        )
    return status


try:
    from opentelemetry import context as otel_context
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
    from opentelemetry.trace import Status, StatusCode

    class _RecentSpansExporter(SpanExporter):
        """Keeps the most recent finished spans in memory. Bounded, unlike the SDK's
        InMemorySpanExporter, which kept every span for the life of the process."""

        def __init__(self, max_spans: int = 2048):
            self._spans = deque(maxlen=max_spans)
            self._lock = threading.Lock()

        def export(self, spans):
            with self._lock:
                self._spans.extend(spans)
            return SpanExportResult.SUCCESS

        def get_finished_spans(self):
            with self._lock:
                return tuple(self._spans)

        def clear(self):
            with self._lock:
                self._spans.clear()

        def shutdown(self):
            return None

    _resource = Resource.create({"service.name": "personal-finance-agentic-cfo", "service.version": "3.0.0"})
    _provider = TracerProvider(resource=_resource)
    _otel_exporter = _RecentSpansExporter()
    _provider.add_span_processor(SimpleSpanProcessor(_otel_exporter))
    if _flag("ENABLE_CLOUD_TRACE"):
        _CLOUD_TRACE = _install_cloud_trace_exporter(_provider)
    otel_trace.set_tracer_provider(_provider)
    # From this module's own provider, so the exporters above always receive its spans.
    _otel_tracer = _provider.get_tracer("finance.agent.tracer")
    _OTEL_AVAILABLE = True
except Exception as e:
    logger.debug(f"OpenTelemetry SDK fallback to lightweight span recorder: {e}")


def _app_env() -> str:
    return os.getenv("APP_ENV", "personal").strip().lower() or "personal"


def _span_attributes(attributes: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """OTel attribute values must be str, bool, int or float; None is dropped, anything else is a string."""
    clean: Dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        clean[str(key)] = value if isinstance(value, (str, bool, int, float)) else str(value)
    return clean


@contextlib.contextmanager
def otel_span(name: str, attributes: Optional[Dict[str, Any]] = None):
    """An OpenTelemetry span (exported to Cloud Trace when enabled) that is the current span inside
    the block, so spans started there become its children. It is not recorded in an AgentTracer
    summary. Yields the span (or None without the SDK); an exception marks it ERROR and re-raises.
    Only the exception type is recorded, never its message (messages can hold financial data).
    """
    span = None
    if _OTEL_AVAILABLE and _otel_tracer is not None:
        try:
            span = _otel_tracer.start_span(name, attributes=_span_attributes(attributes))
        except Exception:
            span = None
    if span is None:
        yield None
        return
    token = otel_context.attach(otel_trace.set_span_in_context(span))
    try:
        yield span
    except BaseException as exc:
        try:
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
        except Exception:
            pass
        raise
    finally:
        otel_context.detach(token)
        span.end()


def traced_request(name: str, attributes: Optional[Dict[str, Any]] = None):
    """Decorator: runs the function inside a root `otel_span` (one trace per call)."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with otel_span(name, {"pfa.app_env": _app_env(), **(attributes or {})}):
                return func(*args, **kwargs)

        return wrapper

    return decorator


class _ModelCallRecorder:
    """Handed out by `model_call_span`: `record(response)` adds the reply's token usage."""

    def __init__(self, span, model: str, source: str):
        self.span, self.model, self.source = span, model, source

    def record(self, response, explicit_cache: bool = False) -> Dict[str, Any]:
        from .usage import record_usage

        attributes = record_usage(response, self.model, self.source, explicit_cache=explicit_cache)
        if self.span is not None:
            for key, value in attributes.items():
                try:
                    self.span.set_attribute(key, value)
                except Exception:
                    pass
        return attributes


@contextlib.contextmanager
def model_call_span(name: str, model: str, source: str, attributes: Optional[Dict[str, Any]] = None):
    """A span around one Gemini call; call `.record(response)` on what it yields to add the token
    usage from `usage_metadata` (telemetry/usage.py) to the span and to the process totals."""
    span_attributes = {"gen_ai.system": "vertex_ai", "gen_ai.request.model": model, "pfa.source": source}
    span_attributes.update(attributes or {})
    with otel_span(name, span_attributes) as span:
        yield _ModelCallRecorder(span, model, source)


def cloud_trace_status() -> Dict[str, Any]:
    """Whether spans are also exported to Cloud Trace (ENABLE_CLOUD_TRACE)."""
    return dict(_CLOUD_TRACE)


class TraceSpan(BaseModel):
    span_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    trace_id: Optional[str] = None
    name: str
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    status: str = "OK"  # OK, ERROR, SECURITY_BLOCKED


# Ring buffer of recent trace summaries for live inspection and weakest-step analysis
GLOBAL_TRACE_HISTORY: List[Dict[str, Any]] = []
MAX_TRACE_HISTORY = 100


class _SpanHandle(str):
    """A span_id string that also acts as a context manager (`with tracer.start_span(...):`).

    Inside the `with` block the span is the current OpenTelemetry span, so spans started there
    (MCP tool calls, Model Armor / SDP API calls, Gemini calls) become its children (DEC-063).
    """

    def __new__(cls, span_id: str, tracer: "AgentTracer"):
        obj = super().__new__(cls, span_id)
        obj._tracer = tracer
        obj._otel_token = None
        return obj

    def __enter__(self) -> "_SpanHandle":
        self._otel_token = self._tracer._attach_otel(str(self))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                self._tracer.end_span(str(self), status="ERROR", extra_attributes={"error": str(exc_val)})
            else:
                self._tracer.end_span(str(self), status="OK")
        finally:
            token, self._otel_token = self._otel_token, None
            AgentTracer._detach_otel(token)
        return False


class AgentTracer:
    """Manages OpenTelemetry spans and trajectories for multi-agent workflows."""

    def __init__(self, trace_id: Optional[str] = None, workflow_name: str = "agent_workflow"):
        self.trace_id = trace_id or str(uuid.uuid4())
        self.workflow_name = workflow_name
        self.spans: List[TraceSpan] = []
        self.active_spans: Dict[str, TraceSpan] = {}
        self._otel_active_spans: Dict[str, Any] = {}

    @contextlib.contextmanager
    def request_span(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        """The root OpenTelemetry span of one request (DEC-063), current for the whole block, so
        every span started inside joins one trace. It carries this tracer's `trace_id` as
        `pfa.trace_id` (the id in the in-memory summaries and the audit log). It is not added to
        `self.spans`, so the weakest-step analysis is unchanged.
        """
        root_attributes = {"pfa.trace_id": self.trace_id, "pfa.workflow": self.workflow_name, "pfa.app_env": _app_env()}
        root_attributes.update(attributes or {})
        with otel_span(name, root_attributes) as span:
            yield span

    @contextlib.contextmanager
    def activate(self, span_id: str):
        """Makes an active span (from `start_span`) the current OpenTelemetry span inside the block."""
        token = self._attach_otel(str(span_id))
        try:
            yield
        finally:
            AgentTracer._detach_otel(token)

    def add_attributes(self, span_id: str, attributes: Optional[Dict[str, Any]]) -> None:
        """Adds attributes to an active span (its summary entry and its OpenTelemetry span)."""
        span = self.active_spans.get(str(span_id))
        if span is None or not attributes:
            return
        span.attributes.update(attributes)
        otel_span_obj = self._otel_active_spans.get(str(span_id))
        if otel_span_obj is not None:
            try:
                for k, v in attributes.items():
                    otel_span_obj.set_attribute(str(k), str(v))
            except Exception:
                pass

    def _attach_otel(self, span_id: str):
        """Makes the span's OpenTelemetry span current; returns the token for `_detach_otel`."""
        otel_span_obj = self._otel_active_spans.get(span_id)
        if not _OTEL_AVAILABLE or otel_span_obj is None:
            return None
        try:
            return otel_context.attach(otel_trace.set_span_in_context(otel_span_obj))
        except Exception:
            return None

    @staticmethod
    def _detach_otel(token) -> None:
        if token is not None:
            try:
                otel_context.detach(token)
            except Exception:
                pass

    def start_span(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> _SpanHandle:
        """Starts a new named execution span (usable both as a `span_id: str` and a `with` context manager)."""
        attrs = dict(attributes or {})
        attrs["trace_id"] = self.trace_id
        span = TraceSpan(
            trace_id=self.trace_id,
            name=name,
            start_time=time.time(),
            attributes=attrs,
        )
        self.active_spans[span.span_id] = span

        if _OTEL_AVAILABLE and _otel_tracer is not None:
            try:
                otel_span = _otel_tracer.start_span(name)
                for k, v in attrs.items():
                    otel_span.set_attribute(str(k), str(v))
                self._otel_active_spans[span.span_id] = otel_span
            except Exception:
                pass

        return _SpanHandle(span.span_id, self)

    def end_span(
        self, span_id: str, status: str = "OK", extra_attributes: Optional[Dict[str, Any]] = None
    ):
        """Ends an active span, calculating duration and exporting to OpenTelemetry."""
        span = self.active_spans.pop(str(span_id), None)
        otel_span = self._otel_active_spans.pop(str(span_id), None)
        if not span:
            return

        span.end_time = time.time()
        span.duration_ms = round((span.end_time - span.start_time) * 1000, 2)
        span.status = status
        if extra_attributes:
            span.attributes.update(extra_attributes)

        if otel_span is not None:
            try:
                if extra_attributes:
                    for k, v in extra_attributes.items():
                        otel_span.set_attribute(str(k), str(v))
                otel_span.set_attribute("span.status", status)
                if status != "OK":
                    # Shown as an error span in Cloud Trace (DEC-063); the status name says why.
                    otel_span.set_status(Status(StatusCode.ERROR, status))
                otel_span.end()
            except Exception:
                pass

        self.spans.append(span)
        logger.debug(f"[OTel Span] {span.name} completed in {span.duration_ms}ms (Status: {span.status})")

    def record_trajectory_event(
        self, stage: str, input_payload: Any, output_payload: Any, error: Optional[str] = None
    ):
        """Records an agent trajectory event for hill-climbing analysis."""
        span_id = self.start_span(f"trajectory_{stage}")
        status = "ERROR" if error else "OK"
        self.end_span(
            span_id,
            status=status,
            extra_attributes={
                "input_summary": str(input_payload)[:120],
                "output_summary": str(output_payload)[:120],
                "error": error,
            },
        )

    def get_summary(self, persist: bool = True) -> Dict[str, Any]:
        """Returns consolidated trajectory metrics and weakest-step analysis."""
        total_time_ms = sum(s.duration_ms or 0 for s in self.spans)
        weakest = self.find_weakest_step()
        summary = {
            "trace_id": self.trace_id,
            "workflow_name": self.workflow_name,
            "otel_sdk_active": _OTEL_AVAILABLE,
            "total_spans": len(self.spans),
            "total_duration_ms": round(total_time_ms, 2),
            "weakest_step": weakest,
            "spans": [s.model_dump() for s in self.spans],
        }
        if persist and self.spans:
            # Avoid duplicate consecutive entries for same trace_id
            GLOBAL_TRACE_HISTORY[:] = [t for t in GLOBAL_TRACE_HISTORY if t.get("trace_id") != self.trace_id]
            GLOBAL_TRACE_HISTORY.insert(0, summary)
            if len(GLOBAL_TRACE_HISTORY) > MAX_TRACE_HISTORY:
                del GLOBAL_TRACE_HISTORY[MAX_TRACE_HISTORY:]
        return summary

    export_summary = get_summary

    def find_weakest_step(self) -> Optional[Dict[str, Any]]:
        """Identifies the weakest step in this trace (errors first, then highest latency)."""
        if not self.spans:
            return None
        errored = [s for s in self.spans if s.status != "OK"]
        if errored:
            worst = max(errored, key=lambda s: s.duration_ms or 0.0)
            return {
                "step_name": worst.name,
                "reason": f"Non-OK status ({worst.status})",
                "status": worst.status,
                "duration_ms": worst.duration_ms or 0.0,
            }
        slowest = max(self.spans, key=lambda s: s.duration_ms or 0.0)
        return {
            "step_name": slowest.name,
            "reason": "Highest latency bottleneck in trajectory",
            "status": slowest.status,
            "duration_ms": slowest.duration_ms or 0.0,
        }


def get_recent_traces(limit: int = 20) -> Dict[str, Any]:
    """Returns recent distributed traces and aggregate weakest-step bottleneck analysis across runs."""
    safe_limit = min(max(1, int(limit)), MAX_TRACE_HISTORY)
    traces = GLOBAL_TRACE_HISTORY[:safe_limit]

    step_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "errors": 0, "total_duration_ms": 0.0, "max_duration_ms": 0.0}
    )
    for t in traces:
        for s in t.get("spans", []):
            name = s.get("name", "unknown")
            dur = float(s.get("duration_ms") or 0.0)
            st = step_stats[name]
            st["count"] += 1
            if s.get("status") != "OK":
                st["errors"] += 1
            st["total_duration_ms"] = round(st["total_duration_ms"] + dur, 2)
            st["max_duration_ms"] = round(max(st["max_duration_ms"], dur), 2)

    aggregated_steps = []
    for name, st in step_stats.items():
        cnt = max(1, st["count"])
        err_rate = round((st["errors"] / cnt) * 100.0, 1)
        avg_dur = round(st["total_duration_ms"] / cnt, 2)
        aggregated_steps.append({
            "step_name": name,
            "invocations": st["count"],
            "error_count": st["errors"],
            "error_rate_pct": err_rate,
            "avg_duration_ms": avg_dur,
            "max_duration_ms": st["max_duration_ms"],
        })

    # Rank weakest step globally: highest error rate first, then highest avg duration
    aggregated_steps.sort(key=lambda x: (-x["error_rate_pct"], -x["avg_duration_ms"]))
    global_weakest = aggregated_steps[0] if aggregated_steps else None

    result = {
        "otel_sdk_active": _OTEL_AVAILABLE,
        "total_traces_recorded": len(GLOBAL_TRACE_HISTORY),
        "global_weakest_step_to_fix_first": global_weakest,
        "step_breakdown": aggregated_steps,
        "recent_traces": traces,
    }
    if _CLOUD_TRACE.get("enabled"):
        # Only with ENABLE_CLOUD_TRACE set, so the response is unchanged otherwise (DEC-063).
        result["cloud_trace_export"] = cloud_trace_status()
    return result


def find_weakest_step(limit: int = 20) -> Optional[Dict[str, Any]]:
    """Returns the global weakest step across recent traces to fix first."""
    return get_recent_traces(limit=limit).get("global_weakest_step_to_fix_first")

