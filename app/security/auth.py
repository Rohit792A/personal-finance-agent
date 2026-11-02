# app/security/auth.py
"""Zero-Trust Agent Identity & Least-Privilege Gateway Enforcement (SPIFFE / WIF).

FDE Pillar 2: GOVERN.
Implements per-agent cryptographic identity (SPIFFE IDs), least-privilege MCP tool allowlists,
and Human-in-the-Loop (HITL) approval gates for mutating operations.

What this is (DEC-063): a local stand-in. The SPIFFE IDs are names, and the workload tokens are
HMAC-SHA256 tokens signed and checked inside this process. No SPIFFE runtime (SPIRE) and no
Google Cloud Workload Identity Federation is involved.
"""
import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Dict, List, Optional, Set
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Key for the in-process A2A/MCP workload tokens. Set AGENT_WIF_SIGNING_KEY to share one key across
# processes; otherwise every process signs with its own random key, so no fixed default key ships in
# the code (DEC-062). Tokens are issued and verified within a single request, which is enough.
_PROCESS_SIGNING_KEY = secrets.token_urlsafe(32)


class AgentPermissionError(PermissionError):
    """Raised when an agent attempts to call an MCP tool outside its least-privilege allowlist."""

    def __init__(self, agent_id: str, tool_name: str, spiffe_id: str = ""):
        self.agent_id = agent_id
        self.tool_name = tool_name
        self.spiffe_id = spiffe_id
        super().__init__(
            f"[AgentGateway PEP] Access denied: agent '{agent_id}' ({spiffe_id}) is not authorized "
            f"to invoke MCP tool '{tool_name}'."
        )


class HumanApprovalRequiredError(PermissionError):
    """Raised when an agent attempts a mutating action without explicit human approval."""

    def __init__(self, agent_id: str, tool_name: str):
        self.agent_id = agent_id
        self.tool_name = tool_name
        super().__init__(
            f"[AgentGateway PEP] Human approval required: agent '{agent_id}' cannot execute "
            f"mutating tool '{tool_name}' without user confirmation (human_approved=True)."
        )


class AgentIdentity(BaseModel):
    """SPIFFE / Workload Identity specification for a specialist agent."""
    agent_id: str
    spiffe_id: str
    display_name: str
    role: str
    allowed_mcp_tools: List[str] = Field(default_factory=list)
    approval_required_tools: List[str] = Field(default_factory=list)


# Canonical registry for the Orchestrator + 4 Specialist Agents (Slide 2 of FDE Pitch)
DEFAULT_AGENT_IDENTITIES: Dict[str, AgentIdentity] = {
    "orchestrator": AgentIdentity(
        agent_id="orchestrator",
        spiffe_id="spiffe://finance-agent.internal/sa/orchestrator",
        display_name="ADK Orchestrator Agent",
        role="Plans multi-step financial jobs and delegates to the 4 specialist agents via A2A.",
        allowed_mcp_tools=[
            "get_financial_summary",
            "get_recent_transactions",
            "check_budget_health",
            "project_savings_capacity",
            "calculate_balance_reconciliation",
            "calculate_emi_and_shortfall",
            "get_learned_rules",
            "save_learned_rule",
            "resolve_and_update_transactions",
            "list_statements",
            "get_ingested_files",
            "get_bank_sync_status",
        ],
        approval_required_tools=["save_learned_rule", "resolve_and_update_transactions"],
    ),
    "ingestion-agent": AgentIdentity(
        agent_id="ingestion-agent",
        spiffe_id="spiffe://finance-agent.internal/sa/ingestion-agent",
        display_name="Ingestion Specialist Agent",
        role="Fetches, unlocks, screens (Model Armor + DLP), and extracts structured rows from PDFs, receipts, spreadsheets, and alerts.",
        allowed_mcp_tools=[
            "list_statements",
            "get_ingested_files",
            "get_bank_sync_status",
        ],
        approval_required_tools=[],
    ),
    "reconciliation-agent": AgentIdentity(
        agent_id="reconciliation-agent",
        spiffe_id="spiffe://finance-agent.internal/sa/reconciliation-agent",
        display_name="Reconciliation Specialist Agent",
        role="Matches rows across alerts and statements, marks self-transfers between own accounts, and verifies mathematical balance integrity.",
        allowed_mcp_tools=[
            "calculate_balance_reconciliation",
            "get_recent_transactions",
            "get_ingested_files",
        ],
        approval_required_tools=[],
    ),
    "clarification-agent": AgentIdentity(
        agent_id="clarification-agent",
        spiffe_id="spiffe://finance-agent.internal/sa/clarification-agent",
        display_name="Clarification & Memory Specialist Agent",
        role="Applies learned payee rules, flags low-confidence transactions for human review, asks via voice/text, and remembers user answers.",
        allowed_mcp_tools=[
            "get_learned_rules",
            "save_learned_rule",
            "resolve_and_update_transactions",
            "get_recent_transactions",
        ],
        approval_required_tools=["save_learned_rule", "resolve_and_update_transactions"],
    ),
    "advisor-agent": AgentIdentity(
        agent_id="advisor-agent",
        spiffe_id="spiffe://finance-agent.internal/sa/advisor-agent",
        display_name="Strategic CFO Advisor Agent",
        role="Answers financial questions over read-only MCP tools, forecasts cash flow, and raises proactive EMI/balance shortfall alerts.",
        allowed_mcp_tools=[
            "get_financial_summary",
            "get_recent_transactions",
            "check_budget_health",
            "project_savings_capacity",
            "calculate_balance_reconciliation",
            "calculate_emi_and_shortfall",
            "get_learned_rules",
            "list_statements",
            "get_bank_sync_status",
        ],
        approval_required_tools=[],
    ),
    # Alias for legacy caller IDs
    "cfo-advisor": AgentIdentity(
        agent_id="cfo-advisor",
        spiffe_id="spiffe://finance-agent.internal/sa/advisor-agent",
        display_name="Strategic CFO Advisor Agent",
        role="Read-only fiduciary advisor alias.",
        allowed_mcp_tools=[
            "get_financial_summary",
            "get_recent_transactions",
            "check_budget_health",
            "project_savings_capacity",
            "calculate_balance_reconciliation",
            "calculate_emi_and_shortfall",
            "get_learned_rules",
            "list_statements",
            "get_bank_sync_status",
        ],
        approval_required_tools=[],
    ),
}


class AgentIdentityManager:
    """Manages SPIFFE identities, short-lived workload tokens, and least-privilege MCP tool enforcement."""

    def __init__(self, signing_key: Optional[str] = None):
        self._identities: Dict[str, AgentIdentity] = dict(DEFAULT_AGENT_IDENTITIES)
        raw_key = signing_key or os.getenv("AGENT_WIF_SIGNING_KEY") or _PROCESS_SIGNING_KEY
        self._signing_key = raw_key.encode("utf-8")

    def get_identity(self, agent_id: str) -> AgentIdentity:
        """Returns the registered AgentIdentity for `agent_id`, or raises KeyError."""
        if agent_id not in self._identities:
            raise KeyError(f"Unknown agent identity '{agent_id}'. Registered: {list(self._identities.keys())}")
        return self._identities[agent_id]

    def list_identities(self) -> List[Dict]:
        """Returns all primary specialist agent identities for governance inspection."""
        primary_ids = ["orchestrator", "ingestion-agent", "reconciliation-agent", "clarification-agent", "advisor-agent"]
        return [self._identities[aid].model_dump() for aid in primary_ids if aid in self._identities]

    def issue_workload_token(self_or_agent_id, agent_id: Optional[str] = None, ttl_seconds: int = 300) -> str:
        """Issues a short-lived HMAC-signed SPIFFE workload token for inter-agent (A2A) or MCP calls."""
        if isinstance(self_or_agent_id, AgentIdentityManager):
            mgr = self_or_agent_id
            aid = str(agent_id or "")
        else:
            mgr = AgentIdentityManager()
            aid = str(self_or_agent_id)
        identity = mgr.get_identity(aid)
        exp = int(time.time()) + ttl_seconds
        payload = f"{identity.agent_id}|{identity.spiffe_id}|{exp}"
        sig = hmac.new(mgr._signing_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{payload}|{sig}"

    def verify_workload_token(self_or_token, token: Optional[str] = None) -> AgentIdentity:
        """Verifies a workload token's HMAC signature and expiration, returning the AgentIdentity."""
        if isinstance(self_or_token, AgentIdentityManager):
            mgr = self_or_token
            tok = str(token or "")
        else:
            mgr = AgentIdentityManager()
            tok = str(self_or_token or "")
        parts = (tok or "").split("|")
        if len(parts) != 4:
            raise PermissionError("Malformed workload token")
        agent_id, spiffe_id, exp_str, sig = parts
        expected_payload = f"{agent_id}|{spiffe_id}|{exp_str}"
        expected_sig = hmac.new(mgr._signing_key, expected_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            raise PermissionError("Invalid workload token signature")
        if int(exp_str) < int(time.time()):
            raise PermissionError("Workload token has expired")
        return mgr.get_identity(agent_id)

    def authorize_tool_call(
        self,
        agent_id: str,
        tool_name: str,
        human_approved: bool = False,
    ) -> AgentIdentity:
        """Enforces least-privilege MCP tool access and HITL approval gates for an agent."""
        identity = self.get_identity(agent_id)
        if tool_name not in identity.allowed_mcp_tools:
            logger.warning(
                f"[AgentGateway PEP] Denied tool '{tool_name}' for '{agent_id}' ({identity.spiffe_id})"
            )
            raise AgentPermissionError(agent_id=agent_id, tool_name=tool_name, spiffe_id=identity.spiffe_id)

        if tool_name in identity.approval_required_tools and not human_approved:
            logger.warning(
                f"[AgentGateway PEP] Blocked unapproved mutating tool '{tool_name}' for '{agent_id}'"
            )
            raise HumanApprovalRequiredError(agent_id=agent_id, tool_name=tool_name)

        return identity

