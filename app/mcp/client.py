# app/mcp/client.py
"""Model Context Protocol (MCP) Client Adapter.

FDE Pillar 1: BUILD & Pillar 2: GOVERN.
Connects reasoning agents to the decoupled MCP tool plane with SPIFFE caller identity,
JSON-RPC 2.0 invocation, and translation to Vertex AI Gemini `FunctionDeclaration`s.
"""
import logging
import os
import site
import uuid
from typing import Any, Dict, List, Optional

# Allow `mcp.client.*` submodules from the official `mcp` package to resolve when `app/` is on sys.path
__path__ = []
for _sp in site.getsitepackages():
    _sdk_client_dir = os.path.join(_sp, "mcp", "client")
    if os.path.isdir(_sdk_client_dir) and _sdk_client_dir not in __path__:
        __path__.append(_sdk_client_dir)

from .ledger_server import LedgerMCPServer

logger = logging.getLogger(__name__)


class MCPClient:
    """Client adapter for discovering and executing tools on the Ledger MCP Server."""

    def __init__(
        self,
        server: Optional[LedgerMCPServer] = None,
        caller_agent_id: str = "advisor-agent",
    ):
        self.server = server or LedgerMCPServer()
        self.caller_agent_id = caller_agent_id

    def get_tool_declarations(self, caller_agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns the list of MCP tool definitions allowed for this caller agent."""
        effective_agent = caller_agent_id or self.caller_agent_id
        return self.server.list_tools(caller_agent_id=effective_agent)

    def get_vertex_tools(self, caller_agent_id: Optional[str] = None) -> List[Any]:
        """Translates allowed MCP tool schemas into a Vertex AI `Tool` with `FunctionDeclaration`s."""
        try:
            from vertexai.generative_models import FunctionDeclaration, Tool

            declarations = []
            for t in self.get_tool_declarations(caller_agent_id=caller_agent_id):
                declarations.append(
                    FunctionDeclaration(
                        name=t["name"],
                        description=t["description"],
                        parameters=t["parameters"],
                    )
                )
            return [Tool(function_declarations=declarations)] if declarations else []
        except Exception as e:
            logger.debug(f"Vertex AI Tool declaration build skipped: {e}")
            return []

    def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        caller_agent_id: Optional[str] = None,
        human_approved: bool = False,
    ) -> Dict[str, Any]:
        """Invokes a tool on the MCP server with SPIFFE identity authorization."""
        effective_agent = caller_agent_id or self.caller_agent_id
        logger.info(f"[MCP Client] '{effective_agent}' dispatching '{tool_name}' with args {arguments}")
        result = self.server.execute_tool(
            tool_name=tool_name,
            arguments=arguments or {},
            caller_agent_id=effective_agent,
            human_approved=human_approved,
        )
        if result.get("is_error"):
            logger.error(f"[MCP Client] Tool call failed: {result.get('error')}")
        return result

    def call_tool_jsonrpc(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        caller_agent_id: Optional[str] = None,
        human_approved: bool = False,
    ) -> Dict[str, Any]:
        """Invokes a tool using a standard MCP JSON-RPC 2.0 `tools/call` envelope."""
        effective_agent = caller_agent_id or self.caller_agent_id
        rpc_req = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4())[:8],
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
                "caller_agent_id": effective_agent,
                "human_approved": human_approved,
            },
        }
        return self.server.handle_jsonrpc(
            rpc_req,
            caller_agent_id=effective_agent,
            human_approved=human_approved,
        )

    def get_context_snapshot(self, month: Optional[str] = None) -> str:
        """Retrieves a consolidated financial context string via standard MCP tool calls.
        Replaces hardcoded SQL queries inside agent prompts.
        """
        summary_res = self.call_tool("get_financial_summary", {"month": month} if month else {})
        tx_res = self.call_tool("get_recent_transactions", {"limit": 15})
        savings_res = self.call_tool("project_savings_capacity", {"month": month} if month else {})
        shortfall_res = self.call_tool("calculate_emi_and_shortfall", {})

        parts = []
        if not summary_res.get("is_error"):
            sdata = summary_res.get("data", {})
            months_count = sdata.get("active_months_count", 1)
            m_inc = sdata.get("monthly_income", sdata.get("total_income", 0))
            m_exp = sdata.get("monthly_expense", sdata.get("total_expense", 0))
            m_surplus = sdata.get("monthly_surplus", sdata.get("net_surplus", 0))
            typical_sal = sdata.get("typical_monthly_salary", m_inc)
            cum_inc = sdata.get("cumulative_all_months_income", m_inc)
            cum_exp = sdata.get("cumulative_all_months_expense", m_exp)
            latest_info = sdata.get("latest_complete_month") or {}

            if month and month.upper() != "ALL":
                parts.append(
                    f"### Verified Single-Month Financial Metrics for {month} (via Ledger MCP):\n"
                    f"- Month Income ({month}): ₹{m_inc:,.2f}\n"
                    f"- Month Expenses ({month}): ₹{m_exp:,.2f}\n"
                    f"- Month Net Surplus ({month}): ₹{m_surplus:,.2f}"
                )
            else:
                latest_line = (
                    f"- Latest Full Month ({latest_info.get('month')}): Income ₹{latest_info.get('income', 0):,.2f} · "
                    f"Expenses ₹{latest_info.get('expense', 0):,.2f} · Net Surplus ₹{latest_info.get('surplus', 0):,.2f}\n"
                    if latest_info
                    else ""
                )
                parts.append(
                    f"### Verified Monthly vs Multi-Month Financial Metrics (via Ledger MCP — {months_count} Months in Ledger):\n"
                    f"- Typical Monthly Salary (Regular Paycheck): ₹{typical_sal:,.2f}/month\n"
                    f"- Average Monthly Income (1-Month Average): ₹{m_inc:,.2f}/month\n"
                    f"- Average Monthly Expenses (1-Month Average): ₹{m_exp:,.2f}/month\n"
                    f"- Average Net Monthly Surplus: ₹{m_surplus:,.2f}/month\n"
                    f"{latest_line}"
                    f"- Cumulative All-Time Ledger Totals ({months_count} months combined — DO NOT report as 1 month): "
                    f"Cumulative Income ₹{cum_inc:,.2f} · Cumulative Expenses ₹{cum_exp:,.2f}"
                )
            cats = sdata.get("categories", [])
            if cats:
                cat_lines = [
                    f"  * {c.get('category')}: ₹{c.get('monthly_avg_amount', c.get('total_amount', 0)):,.2f}/month "
                    f"(cumulative across {months_count}m: ₹{c.get('total_amount', 0):,.2f}, {c.get('transaction_type')})"
                    for c in cats[:10]
                ]
                parts.append("### Top Category Allocations (Monthly Average vs Cumulative):\n" + "\n".join(cat_lines))

        if not tx_res.get("is_error") and tx_res.get("data"):
            tx_lines = [
                f"  * {t.get('transaction_date')}: ₹{t.get('amount', 0):,.2f} - {t.get('description')} [{t.get('category')}]"
                for t in tx_res.get("data", [])[:10]
            ]
            parts.append("### Recent Transactions (Latest 10 Verified):\n" + "\n".join(tx_lines))

        if not savings_res.get("is_error"):
            pdata = savings_res.get("data", {})
            parts.append(
                f"### Fiduciary Projections (via Planning MCP):\n"
                f"- Savings Rate: {pdata.get('savings_rate_pct', 0)}%\n"
                f"- Suggested SIP Investment: ₹{pdata.get('recommended_monthly_sip', 0):,.2f}\n"
                f"- Emergency Buffer: ₹{pdata.get('emergency_reserve_allocation', 0):,.2f}"
            )

        if not shortfall_res.get("is_error"):
            edata = shortfall_res.get("data", {})
            alerts = edata.get("alerts") or []
            if alerts:
                parts.append("### Proactive Cashflow & EMI Alerts (via Calculator MCP):\n" + "\n".join(f"- {a}" for a in alerts))

        return "\n\n".join(parts) if parts else "No financial records available in ledger."
