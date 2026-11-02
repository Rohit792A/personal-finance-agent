# app/telemetry/__init__.py
"""OpenTelemetry Distributed Tracing & Trajectory Analysis (FDE Pillar 3: SCALE).
Provides observability, span tracing, and failure trajectory analysis for prompt hill-climbing.
"""
from .tracer import AgentTracer, TraceSpan, get_recent_traces, find_weakest_step, GLOBAL_TRACE_HISTORY

__all__ = ["AgentTracer", "TraceSpan", "get_recent_traces", "find_weakest_step", "GLOBAL_TRACE_HISTORY"]

