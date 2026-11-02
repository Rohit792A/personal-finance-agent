#!/usr/bin/env python3
"""Provision & Synchronize All 5 A2A Agents, Ledger MCP Server, and Bindings in Google Cloud Agent Registry.

Usage:
    ./venv/bin/python scripts/register_gcp_agents.py
"""
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "app"))

from mcp import AgenticCFOOrchestrator  # noqa: E402


def main() -> int:
    orchestrator = AgenticCFOOrchestrator()
    tools = orchestrator.mcp_server.list_tools()

    print("=== Synchronizing 5 A2A Agent Cards + 1 MCP Server + 9 Bindings to Google Cloud Agent Registry ===")
    sync_report = orchestrator.a2a.registry.sync_to_gcp_agent_registry(mcp_tools=tools)
    print(json.dumps(sync_report, indent=2))

    print("\n=== Verifying Live Projected Resources in Google Cloud Agent Registry ===")
    live_state = orchestrator.a2a.registry.fetch_from_gcp_agent_registry()
    print(
        json.dumps(
            {
                "status": live_state.get("status"),
                "project_id": live_state.get("project_id"),
                "location": live_state.get("location"),
                "agents_count": live_state.get("agents_count"),
                "agents": [
                    {
                        "displayName": a.get("displayName"),
                        "agentId": a.get("agentId"),
                        "name": a.get("name"),
                        "version": a.get("version"),
                        "skills": [s.get("id") for s in a.get("skills", [])],
                        "url": (
                            a.get("protocols", [{}])[0]
                            .get("interfaces", [{}])[0]
                            .get("url")
                        ),
                    }
                    for a in live_state.get("agents", [])
                ],
                "mcp_servers": [
                    {
                        "displayName": m.get("displayName"),
                        "mcpServerId": m.get("mcpServerId"),
                        "name": m.get("name"),
                        "tools_count": len(m.get("tools", [])),
                    }
                    for m in live_state.get("mcp_servers", [])
                ],
                "bindings_count": live_state.get("bindings_count"),
            },
            indent=2,
        )
    )
    return 0 if sync_report.get("status") == "SYNCED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
