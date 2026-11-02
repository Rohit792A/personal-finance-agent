# app/mcp/__init__.py
"""Model Context Protocol (MCP) and Multi-Agent Orchestration Organs (FDE Pillar 1: BUILD).
Enables official MCP SDK tool decoupling, standardized JSON-RPC 2.0 schema-driven tool execution,
and Agent2Agent (A2A) 4-specialist delegation.
"""
import os
import site

# Extend __path__ with site-packages/mcp so official `mcp.server.mcpserver.MCPServer`
# and `mcp.shared` submodules remain importable even when `app/` is on `sys.path[0]`.
for _sp in site.getsitepackages():
    _sdk_mcp_dir = os.path.join(_sp, "mcp")
    if os.path.isdir(_sdk_mcp_dir) and _sdk_mcp_dir not in __path__:
        __path__.append(_sdk_mcp_dir)

from .ledger_server import LedgerMCPServer
from .client import MCPClient
from .agent_registry import (
    AgentAuthentication,
    AgentCapabilities,
    AgentCard,
    AgentRegistry,
    AgentSkill,
)
from .a2a_orchestrator import A2AOrchestrator, AgentMessage, AgenticCFOOrchestrator

__all__ = [
    "LedgerMCPServer",
    "MCPClient",
    "AgentRegistry",
    "AgentCard",
    "AgentSkill",
    "AgentCapabilities",
    "AgentAuthentication",
    "A2AOrchestrator",
    "AgentMessage",
    "AgenticCFOOrchestrator",
]
