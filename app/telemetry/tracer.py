# app/telemetry/tracer.py
"""OpenTelemetry Distributed Tracing & Trajectory Bottleneck Analyzer.

FDE Pillar 3: SCALE.
Integrates the official OpenTelemetry SDK (`opentelemetry-api`, `opentelemetry-sdk`)
to capture distributed execution spans across Model Armor, Cloud DLP, A2A delegation,
MCP tool execution, and Vertex AI model turns. Includes `analyze_weakest_step()` to
find the highest-error or highest-latency step in agent trajectories.
"""
import logging
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Initialize OpenTelemetry SDK TracerProvider + InMemorySpanExporter
_OTEL_AVAILABLE = False
_otel_tracer = None
_otel_exporter = None

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    _resource = Resource.create({"service.name": "personal-finance-agentic-cfo", "service.version": "3.0.0"})
    _provider = TracerProvider(resource=_resource)
    _otel_exporter = InMemorySpanExporter()
    _provider.add_span_processor(SimpleSpanProcessor(_otel_exporter))
    otel_trace.set_tracer_provider(_provider)
    _otel_tracer = otel_trace.get_tracer("finance.agent.tracer")
    _OTEL_AVAILABLE = True
except Exception as e:
    logger.debug(f"OpenTelemetry SDK fallback to lightweight span recorder: {e}")


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
    """A span_id string that also acts as a context manager (`with tracer.start_span(...):`)."""

    def __new__(cls, span_id: str, tracer: "AgentTracer"):
        obj = super().__new__(cls, span_id)
        obj._tracer = tracer
        return obj

    def __enter__(self) -> "_SpanHandle":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._tracer.end_span(str(self), status="ERROR", extra_attributes={"error": str(exc_val)})
        else:
            self._tracer.end_span(str(self), status="OK")
        return False


class AgentTracer:
    """Manages OpenTelemetry spans and trajectories for multi-agent workflows."""

    def __init__(self, trace_id: Optional[str] = None, workflow_name: str = "agent_workflow"):
        self.trace_id = trace_id or str(uuid.uuid4())
        self.workflow_name = workflow_name
        self.spans: List[TraceSpan] = []
        self.active_spans: Dict[str, TraceSpan] = {}
        self._otel_active_spans: Dict[str, Any] = {}

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

    return {
        "otel_sdk_active": _OTEL_AVAILABLE,
        "total_traces_recorded": len(GLOBAL_TRACE_HISTORY),
        "global_weakest_step_to_fix_first": global_weakest,
        "step_breakdown": aggregated_steps,
        "recent_traces": traces,
    }


def find_weakest_step(limit: int = 20) -> Optional[Dict[str, Any]]:
    """Returns the global weakest step across recent traces to fix first."""
    return get_recent_traces(limit=limit).get("global_weakest_step_to_fix_first")

