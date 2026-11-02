# app/telemetry/__init__.py
"""OpenTelemetry Distributed Tracing & Trajectory Analysis (FDE Pillar 3: SCALE).
Provides observability, span tracing, and failure trajectory analysis for prompt hill-climbing.
DEC-063: optional Cloud Trace export (`ENABLE_CLOUD_TRACE=1`), request-level root spans, and
Vertex AI token usage read from each Gemini reply (`usage.py`).
"""
from .tracer import (
    AgentTracer,
    TraceSpan,
    get_recent_traces,
    find_weakest_step,
    GLOBAL_TRACE_HISTORY,
    otel_span,
    model_call_span,
    traced_request,
    cloud_trace_status,
)
from .usage import record_usage, usage_snapshot

__all__ = [
    "AgentTracer",
    "TraceSpan",
    "get_recent_traces",
    "find_weakest_step",
    "GLOBAL_TRACE_HISTORY",
    "otel_span",
    "model_call_span",
    "traced_request",
    "cloud_trace_status",
    "record_usage",
    "usage_snapshot",
]
