# app/mcp/agent_registry.py
"""Google Cloud Agent Platform (`aiplatform.googleapis.com`) & Agent Registry (`agentregistry.googleapis.com`)
A2A Agent Card Discovery, Registration & Card-Based Invocation Engine.

FDE Pillar 1 (BUILD) & Pillar 2 (GOVERN):
Implements the Google A2A Agent Card specification (`/.well-known/agent.json`) and connects
directly to Google Cloud's official **Agent Registry API (`agentregistry.googleapis.com/v1alpha`)**
so that the Orchestrator, all 4 Specialist Agents, and the Ledger MCP Server:
  1. Publish standardized A2A `AgentCard` manifests (`name`, `description`, `url`,
     `cloud_run_url`, `well_known_url`, `version`, `protocolVersion`, `preferredTransport`,
     `capabilities`, `authentication` with SPIFFE ID, and typed `skills`).
  2. Are registered in Google Cloud Agent Registry (`projects/{PROJECT_ID}/locations/global/services/pfa-*`
     -> projected into `projects/{PROJECT_ID}/locations/global/agents/*` and `mcpServers/*`)
     with explicit A2A & MCP `Bindings` (`projects/{PROJECT_ID}/locations/global/bindings/*`),
     plus persisted in PostgreSQL (`agent_registry` table).
  3. Are discovered and invoked **via their `AgentCard`** (`get_agent_card` / `discover_by_skill`
     -> validate `skill` in `card.skills` -> verify SPIFFE workload token against
     `card.authentication` -> dispatch to `card.url` endpoint).
The dispatch is in-process: `card.url` is the key of a registered Python handler, not an HTTP
call, and the workload token is an HMAC token issued and checked in this process (a local
stand-in for SPIFFE; see security/auth.py). Google Cloud Agent Registry holds catalogue entries
for the cards; no agent traffic goes through it.
"""
import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

import config
from database import DatabaseManager
from security.auth import AgentIdentityManager, DEFAULT_AGENT_IDENTITIES
from telemetry.tracer import AgentTracer

logger = logging.getLogger(__name__)

GCP_PROJECT_ID = getattr(config, "PROJECT_ID", "personal-finance-agent-507010")
GCP_PROJECT_NUMBER = os.getenv("GCP_PROJECT_NUMBER", "328452160072")
GCP_AGENT_REGISTRY_LOCATION = os.getenv("GCP_AGENT_REGISTRY_LOCATION", "global")
CLOUD_RUN_BASE_URL = os.getenv(
    "CLOUD_RUN_BASE_URL",
    "https://finance-backend-4yilcu24gq-an.a.run.app",
).rstrip("/")


def _gcp_service_id(agent_id: str) -> str:
    clean = agent_id.strip().lower().replace("_", "-")
    return clean if clean.startswith("pfa-") else f"pfa-{clean}"


def _gcp_service_name(agent_id: str) -> str:
    svc_id = _gcp_service_id(agent_id)
    return f"projects/{GCP_PROJECT_ID}/locations/{GCP_AGENT_REGISTRY_LOCATION}/services/{svc_id}"


def _gcp_agent_urn(agent_id: str) -> str:
    svc_id = _gcp_service_id(agent_id)
    return (
        f"urn:agent:projects-{GCP_PROJECT_NUMBER}:projects:{GCP_PROJECT_NUMBER}:"
        f"locations:{GCP_AGENT_REGISTRY_LOCATION}:agentregistry:services:{svc_id}"
    )


GCP_MCP_SERVICE_ID = "pfa-ledger-mcp-server"
GCP_MCP_SERVICE_NAME = (
    f"projects/{GCP_PROJECT_ID}/locations/{GCP_AGENT_REGISTRY_LOCATION}/services/{GCP_MCP_SERVICE_ID}"
)
GCP_MCP_SERVER_URN = (
    f"urn:mcp:projects-{GCP_PROJECT_NUMBER}:projects:{GCP_PROJECT_NUMBER}:"
    f"locations:{GCP_AGENT_REGISTRY_LOCATION}:agentregistry:services:{GCP_MCP_SERVICE_ID}"
)


class AgentSkill(BaseModel):
    """A2A Agent Card Skill declaration."""

    id: str = Field(..., description="Canonical skill / intent identifier, e.g. 'RECONCILE_BATCH'")
    name: str = Field(..., description="Human-readable skill name")
    description: str = Field(..., description="What this skill does and when the orchestrator should invoke it")
    tags: List[str] = Field(default_factory=list)
    inputModes: List[str] = Field(default_factory=lambda: ["application/json"])
    outputModes: List[str] = Field(default_factory=lambda: ["application/json"])


class AgentCapabilities(BaseModel):
    """A2A Agent Card runtime capabilities."""

    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = True


class AgentAuthentication(BaseModel):
    """A2A Agent Card zero-trust authentication contract."""

    schemes: List[str] = Field(default_factory=lambda: ["SPIFFE-WIF", "Bearer"])
    spiffe_id: str = Field(..., description="Cryptographic SPIFFE workload identity URI")
    allowed_mcp_tools: List[str] = Field(default_factory=list)
    approval_required_tools: List[str] = Field(default_factory=list)


class AgentProvider(BaseModel):
    """A2A Agent Card provider metadata."""

    organization: str = "Personal Finance Agentic AI CFO"
    url: str = CLOUD_RUN_BASE_URL


class AgentCard(BaseModel):
    """Google A2A-compliant Agent Card manifest (`/.well-known/agent.json`) with Google Cloud Agent Registry metadata."""

    agent_id: str = Field(..., description="Unique registry identifier, e.g. 'reconciliation-agent'")
    name: str = Field(..., description="Display name of the agent")
    description: str = Field(..., description="Summary of the agent's domain responsibility")
    version: str = Field(default="3.0.0")
    protocolVersion: str = Field(default="0.3.0")
    preferredTransport: str = Field(default="HTTP+JSON")
    url: str = Field(..., description="Local/relative A2A task invocation endpoint URI (`tasks/send`)")
    cloud_run_url: str = Field(default="", description="Full Cloud Run HTTPS A2A endpoint registered in Google Cloud Agent Registry")
    well_known_url: str = Field(..., description="Discovery URI for this Agent Card (`/.well-known/agent.json`)")
    gcp_service_id: str = Field(default="", description="Google Cloud Agent Registry Service ID (`pfa-*`)")
    gcp_service_name: str = Field(default="", description="Google Cloud Agent Registry Service resource path")
    gcp_agent_urn: str = Field(default="", description="Google Cloud Agent Registry canonical URN (`urn:agent:...`)")
    gcp_registry_resource: str = Field(default="", description="Projected Google Cloud Agent resource name")
    status: str = Field(default="ONLINE", description="ONLINE, DEGRADED, or OFFLINE")
    provider: AgentProvider = Field(default_factory=AgentProvider)
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    authentication: AgentAuthentication
    defaultInputModes: List[str] = Field(default_factory=lambda: ["application/json", "text/plain"])
    defaultOutputModes: List[str] = Field(default_factory=lambda: ["application/json", "text/plain"])
    skills: List[AgentSkill] = Field(default_factory=list)
    supported_intents: List[str] = Field(default_factory=list)
    registered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def model_post_init(self, __context: Any) -> None:
        if not self.gcp_service_id:
            self.gcp_service_id = _gcp_service_id(self.agent_id)
        if not self.gcp_service_name:
            self.gcp_service_name = _gcp_service_name(self.agent_id)
        if not self.gcp_agent_urn:
            self.gcp_agent_urn = _gcp_agent_urn(self.agent_id)
        if not self.cloud_run_url:
            rel = self.url if self.url.startswith("/") else f"/{self.url}"
            self.cloud_run_url = f"{CLOUD_RUN_BASE_URL}{rel}"

    def has_skill(self, skill_or_intent: str) -> bool:
        target = (skill_or_intent or "").strip().upper()
        if any(s.id.upper() == target for s in self.skills):
            return True
        return target in [i.upper() for i in self.supported_intents]

    def get_skill(self, skill_or_intent: str) -> Optional[AgentSkill]:
        target = (skill_or_intent or "").strip().upper()
        for s in self.skills:
            if s.id.upper() == target:
                return s
        return None

    def to_gcp_a2a_card(self) -> Dict[str, Any]:
        """Serializes this `AgentCard` into the exact A2A Agent Card schema validated by
        Google Cloud Agent Registry (`agentregistry.googleapis.com` `A2A_AGENT_CARD`)."""
        return {
            "name": self.name,
            "description": self.description,
            "url": self.cloud_run_url or f"{CLOUD_RUN_BASE_URL}{self.url}",
            "version": self.version,
            "protocolVersion": self.protocolVersion,
            "preferredTransport": self.preferredTransport,
            "capabilities": self.capabilities.model_dump(),
            "defaultInputModes": list(self.defaultInputModes),
            "defaultOutputModes": list(self.defaultOutputModes),
            "skills": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "tags": list(s.tags),
                }
                for s in self.skills
            ],
        }


def _build_default_agent_cards() -> Dict[str, AgentCard]:
    """Constructs the canonical A2A Agent Cards for the Orchestrator + 4 Specialist Agents."""
    orch_id = DEFAULT_AGENT_IDENTITIES["orchestrator"]
    ing_id = DEFAULT_AGENT_IDENTITIES["ingestion-agent"]
    rec_id = DEFAULT_AGENT_IDENTITIES["reconciliation-agent"]
    clar_id = DEFAULT_AGENT_IDENTITIES["clarification-agent"]
    adv_id = DEFAULT_AGENT_IDENTITIES["advisor-agent"]

    cards: List[AgentCard] = [
        AgentCard(
            agent_id="orchestrator",
            name="Agentic AI CFO Orchestrator",
            description="Root ADK Orchestrator that plans multi-step financial jobs, discovers specialist agents in Google Cloud Agent Registry via their Agent Cards, and delegates tasks over A2A.",
            url="/a2a/agents/orchestrator/tasks/send",
            well_known_url="/.well-known/agent.json",
            authentication=AgentAuthentication(
                spiffe_id=orch_id.spiffe_id,
                allowed_mcp_tools=orch_id.allowed_mcp_tools,
                approval_required_tools=orch_id.approval_required_tools,
            ),
            skills=[
                AgentSkill(
                    id="RUN_FOUR_AGENT_WORKFLOW",
                    name="End-to-End 4-Agent CFO Pipeline",
                    description="Coordinates Ingestion, Reconciliation, Clarification, and Advisor specialist agents via their Agent Cards.",
                    tags=["orchestration", "cfo", "workflow"],
                ),
                AgentSkill(
                    id="MASTER_RESOLVE_TRANSACTIONS",
                    name="Master Category Resolution & Data-Driven Decision Engine",
                    description="Orchestrates all 4 specialist agents to analyze unverified or 'Other' ledger transactions using cross-row reimbursement correlation, VPA/merchant history, bank sweep logic, and Gemini reasoning.",
                    tags=["master-agent", "resolution", "categorization", "reconciliation"],
                ),
                AgentSkill(
                    id="APPLY_MASTER_RESOLUTIONS",
                    name="Commit Master Agent Category Decisions & Teach Rules",
                    description="Atomically updates approved transaction categories/types in PostgreSQL and teaches reusable payee patterns via MCP.",
                    tags=["master-agent", "mutation", "hitl-gate", "learned-rules"],
                ),
                AgentSkill(
                    id="OMNIPRESENT_JARVIS_COMMAND",
                    name="Omnipresent Jarvis Voice & Text Master Assistant",
                    description="Context-aware global assistant across all web app screens that answers questions, walks through page metrics, reconciles balances, and updates categories/rules over A2A & MCP.",
                    tags=["jarvis", "voice", "orchestrator", "global-copilot"],
                ),
            ],
            supported_intents=[
                "RUN_FOUR_AGENT_WORKFLOW",
                "MASTER_RESOLVE_TRANSACTIONS",
                "APPLY_MASTER_RESOLUTIONS",
                "OMNIPRESENT_JARVIS_COMMAND",
            ],
        ),
        AgentCard(
            agent_id="ingestion-agent",
            name="Ingestion Specialist Agent",
            description="Fetches, unlocks, screens (Model Armor + Cloud DLP), and reads any financial document (PDF statements, receipts, spreadsheets, bank alerts).",
            url="/a2a/agents/ingestion-agent/tasks/send",
            well_known_url="/a2a/agents/ingestion-agent/.well-known/agent.json",
            authentication=AgentAuthentication(
                spiffe_id=ing_id.spiffe_id,
                allowed_mcp_tools=ing_id.allowed_mcp_tools,
                approval_required_tools=ing_id.approval_required_tools,
            ),
            skills=[
                AgentSkill(
                    id="SCREEN_AND_SANITIZE_TRANSACTIONS",
                    name="Model Armor & Cloud DLP Batch Screening",
                    description="Screens extracted transaction descriptions for indirect prompt injection and masks PII while preserving 12-digit UPI/IMPS references.",
                    tags=["ingestion", "security", "dlp", "model-armor"],
                ),
                AgentSkill(
                    id="INGEST_DOCUMENT",
                    name="Multimodal Document Extraction",
                    description="Extracts structured transactions from PDF statements, receipt images, or Excel spreadsheets.",
                    tags=["ingestion", "ocr", "pdf", "receipt", "excel"],
                ),
            ],
            supported_intents=["SCREEN_AND_SANITIZE_TRANSACTIONS", "INGEST_DOCUMENT"],
        ),
        AgentCard(
            agent_id="reconciliation-agent",
            name="Reconciliation Specialist Agent",
            description="Matches rows across Gmail alerts and bank statements, marks self-transfers between own accounts, deduplicates batches, and verifies balance math.",
            url="/a2a/agents/reconciliation-agent/tasks/send",
            well_known_url="/a2a/agents/reconciliation-agent/.well-known/agent.json",
            authentication=AgentAuthentication(
                spiffe_id=rec_id.spiffe_id,
                allowed_mcp_tools=rec_id.allowed_mcp_tools,
                approval_required_tools=rec_id.approval_required_tools,
            ),
            skills=[
                AgentSkill(
                    id="CHECK_SELF_TRANSFER",
                    name="Self-Transfer & Own-Account Settlement Detector",
                    description="Identifies transfers between the user's own bank accounts or credit-card bill settlements so they are not double-counted as spending.",
                    tags=["reconciliation", "self-transfer", "dedupe"],
                ),
                AgentSkill(
                    id="VERIFY_MATH_INTEGRITY",
                    name="Statement Balance Math Verification",
                    description="Verifies that opening_balance + total_credits - total_debits == closing_balance via the deterministic MCP calculator.",
                    tags=["reconciliation", "math", "audit"],
                ),
                AgentSkill(
                    id="RECONCILE_BATCH",
                    name="Cross-Source Batch Reconciliation",
                    description="Deduplicates rows within a batch, merges rows matching existing Gmail alerts by 12-digit reference ID, marks self-transfers, and checks balance math.",
                    tags=["reconciliation", "alerts", "batch"],
                ),
            ],
            supported_intents=["CHECK_SELF_TRANSFER", "VERIFY_MATH_INTEGRITY", "RECONCILE_BATCH"],
        ),
        AgentCard(
            agent_id="clarification-agent",
            name="Clarification & Memory Specialist Agent",
            description="Applies learned user payee rules, asks via voice/text when confidence < 85%, and persists new learned rules upon human confirmation.",
            url="/a2a/agents/clarification-agent/tasks/send",
            well_known_url="/a2a/agents/clarification-agent/.well-known/agent.json",
            authentication=AgentAuthentication(
                spiffe_id=clar_id.spiffe_id,
                allowed_mcp_tools=clar_id.allowed_mcp_tools,
                approval_required_tools=clar_id.approval_required_tools,
            ),
            skills=[
                AgentSkill(
                    id="RESOLVE_AMBIGUOUS_BATCH",
                    name="Learned-Rule Matching & HITL Clarification Gating",
                    description="Auto-categorizes transactions matching learned user rules (e.g. 'UPI/RAHUL' -> 'Rent') and generates spoken/text clarification prompts for remaining <85% confidence rows.",
                    tags=["clarification", "memory", "hitl", "voice"],
                ),
                AgentSkill(
                    id="LEARN_USER_RULE",
                    name="Persistent Payee Rule Learner",
                    description="Extracts a reusable payee pattern from a transaction narration and saves the user's confirmed category via MCP (requires human_approved=True).",
                    tags=["clarification", "learning", "hitl-gate"],
                ),
            ],
            supported_intents=["RESOLVE_AMBIGUOUS_BATCH", "LEARN_USER_RULE"],
        ),
        AgentCard(
            agent_id="advisor-agent",
            name="Strategic CFO Advisor Agent",
            description="Answers financial questions over read-only MCP tools, forecasts savings capacity, and raises proactive EMI/rent cash-shortfall alerts before due dates.",
            url="/a2a/agents/advisor-agent/tasks/send",
            well_known_url="/a2a/agents/advisor-agent/.well-known/agent.json",
            authentication=AgentAuthentication(
                spiffe_id=adv_id.spiffe_id,
                allowed_mcp_tools=adv_id.allowed_mcp_tools,
                approval_required_tools=adv_id.approval_required_tools,
            ),
            skills=[
                AgentSkill(
                    id="CHECK_PROACTIVE_ALERTS",
                    name="Proactive EMI/Rent Cash-Shortfall Alerting",
                    description="Checks upcoming recurring commitments against liquid balance via MCP calculators to warn before a shortfall or bounce.",
                    tags=["advisor", "shortfall", "emi", "alerts"],
                ),
                AgentSkill(
                    id="FORECAST_SAVINGS",
                    name="Savings & SIP Capacity Forecaster",
                    description="Projects monthly surplus savings capacity and recommended SIP allocation.",
                    tags=["advisor", "forecast", "sip"],
                ),
                AgentSkill(
                    id="ASK_CFO",
                    name="Conversational Fiduciary Q&A",
                    description="Answers natural-language financial planning questions grounded in live MCP ledger snapshots with prefix caching.",
                    tags=["advisor", "chat", "fiduciary"],
                ),
            ],
            supported_intents=["CHECK_PROACTIVE_ALERTS", "FORECAST_SAVINGS", "ASK_CFO"],
        ),
    ]
    return {c.agent_id: c for c in cards}


def _get_gcp_access_token() -> Optional[str]:
    """Obtains a Google Cloud access token via Application Default Credentials or gcloud CLI."""
    try:
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        if creds.token:
            return creds.token
    except Exception:
        pass

    try:
        token = (
            subprocess.check_output(
                ["gcloud", "auth", "print-access-token"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
        if token:
            return token
    except Exception:
        pass
    return None


class AgentRegistry:
    """Central A2A Agent Registry managing Agent Card publication, Google Cloud Agent Registry
    (`agentregistry.googleapis.com`) synchronization, discovery, and card-based invocation."""

    _cards: Dict[str, AgentCard] = _build_default_agent_cards()
    _endpoints: Dict[str, Callable[[str, Dict[str, Any]], Dict[str, Any]]] = {}
    _invocation_counts: Dict[str, int] = {}

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db_manager = db_manager or DatabaseManager()
        for agent_id, card in _build_default_agent_cards().items():
            if agent_id not in AgentRegistry._cards:
                AgentRegistry._cards[agent_id] = card

    def register_agent(
        self,
        agent_id: str,
        handler_func: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        card: Optional[AgentCard] = None,
    ) -> AgentCard:
        """Deploys/registers an agent's A2A `AgentCard` and binds its `card.url` endpoint handler in the Registry."""
        if card is not None:
            AgentRegistry._cards[agent_id] = card
        elif agent_id not in AgentRegistry._cards:
            spiffe = f"spiffe://finance-agent.internal/sa/{agent_id}"
            AgentRegistry._cards[agent_id] = AgentCard(
                agent_id=agent_id,
                name=agent_id.replace("-", " ").title(),
                description=f"Dynamically registered A2A specialist agent '{agent_id}'.",
                url=f"/a2a/agents/{agent_id}/tasks/send",
                well_known_url=f"/a2a/agents/{agent_id}/.well-known/agent.json",
                authentication=AgentAuthentication(spiffe_id=spiffe),
                skills=[],
                supported_intents=[],
            )

        active_card = AgentRegistry._cards[agent_id]
        active_card.status = "ONLINE"
        # Bind handler by agent_id, relative card.url, and Cloud Run card.cloud_run_url
        AgentRegistry._endpoints[agent_id] = handler_func
        AgentRegistry._endpoints[active_card.url] = handler_func
        if active_card.cloud_run_url:
            AgentRegistry._endpoints[active_card.cloud_run_url] = handler_func

        try:
            self.db_manager.save_agent_card(active_card.model_dump())
        except Exception as e:
            logger.debug(f"AgentRegistry DB sync skipped for '{agent_id}': {e}")

        logger.info(
            f"[AgentRegistry] Deployed Agent Card '{active_card.agent_id}' "
            f"(gcp_urn={active_card.gcp_agent_urn}, endpoint={active_card.url}, "
            f"skills={[s.id for s in active_card.skills]})"
        )
        return active_card

    def get_agent_card(self, agent_id: str) -> Optional[AgentCard]:
        """Discovers a deployed agent's `AgentCard` by `agent_id` or `gcp_service_id`."""
        if agent_id in AgentRegistry._cards:
            return AgentRegistry._cards[agent_id]
        if agent_id.startswith("pfa-"):
            stripped = agent_id[len("pfa-"):]
            if stripped in AgentRegistry._cards:
                return AgentRegistry._cards[stripped]
        for card in AgentRegistry._cards.values():
            if card.gcp_agent_urn == agent_id or card.gcp_service_name == agent_id:
                return card
        return None

    def discover_by_skill(self, skill_or_intent: str) -> Optional[AgentCard]:
        """Discovers an online specialist agent whose `AgentCard` advertises `skill_or_intent`."""
        for card in AgentRegistry._cards.values():
            if card.agent_id == "orchestrator":
                continue
            if card.status == "ONLINE" and card.has_skill(skill_or_intent):
                return card
        return None

    def list_agent_cards(self, include_orchestrator: bool = False) -> List[Dict[str, Any]]:
        """Returns all deployed A2A Agent Cards in the registry."""
        out = []
        for agent_id, card in AgentRegistry._cards.items():
            if not include_orchestrator and agent_id == "orchestrator":
                continue
            dump = card.model_dump()
            dump["invocations"] = AgentRegistry._invocation_counts.get(agent_id, 0)
            out.append(dump)
        return out

    def invoke_via_agent_card(
        self,
        target_agent: Optional[str],
        intent: str,
        payload: Dict[str, Any],
        sender_agent: str = "orchestrator",
        workload_token: Optional[str] = None,
        task_id: Optional[str] = None,
        tracer: Optional[AgentTracer] = None,
    ) -> Dict[str, Any]:
        """Discovers the target agent in the Agent Registry via its `AgentCard`, validates its
        declared `AgentSkill` and SPIFFE authentication policy, and invokes its `card.url` endpoint."""
        tid = task_id or str(uuid.uuid4())[:8]

        # 1. Discover Agent Card from the Agent Registry (by agent_id or automatic skill discovery)
        card: Optional[AgentCard] = None
        if target_agent and target_agent not in ("auto", "skill-discovery"):
            card = self.get_agent_card(target_agent)
        else:
            card = self.discover_by_skill(intent)

        if card is None:
            err = f"Target agent '{target_agent}' not found in Agent Registry."
            logger.error(f"[AgentRegistry] {err}")
            return {"task_id": tid, "status": "FAILED", "error": err}

        if card.status != "ONLINE":
            err = f"Agent Card '{card.agent_id}' is currently {card.status}."
            return {"task_id": tid, "status": "FAILED", "error": err}

        # 2. Validate that the requested intent/skill is advertised on the Agent Card (if card defines skills)
        matched_skill = card.get_skill(intent)
        if card.skills and not card.has_skill(intent):
            err = (
                f"Skill/intent '{intent}' is not declared on Agent Card '{card.agent_id}'. "
                f"Declared skills: {[s.id for s in card.skills]}"
            )
            logger.warning(f"[AgentRegistry] {err}")
            return {"task_id": tid, "status": "FAILED", "error": err}

        # 3. Verify SPIFFE Workload Identity token required by `card.authentication`
        token = workload_token
        if not token:
            try:
                token = AgentIdentityManager.issue_workload_token(sender_agent)
            except Exception:
                token = None

        if "SPIFFE-WIF" in card.authentication.schemes and token:
            try:
                AgentIdentityManager.verify_workload_token(token)
            except Exception as auth_err:
                err = f"Invalid workload token for sender '{sender_agent}': {auth_err}"
                logger.error(f"[AgentRegistry] {err}")
                return {"task_id": tid, "status": "FAILED", "error": err}

        # 4. Resolve endpoint handler via `card.url` / `card.cloud_run_url` (falling back to `card.agent_id`)
        endpoint_handler = (
            AgentRegistry._endpoints.get(card.url)
            or AgentRegistry._endpoints.get(card.cloud_run_url)
            or AgentRegistry._endpoints.get(card.agent_id)
        )
        if not endpoint_handler:
            err = f"No live endpoint bound to Agent Card URL '{card.url}' ({card.agent_id})."
            logger.error(f"[AgentRegistry] {err}")
            return {"task_id": tid, "status": "FAILED", "error": err}

        active_tracer = tracer or AgentTracer(workflow_name=f"a2a_card_{card.agent_id}_{intent.lower()}")
        card_summary = {
            "agent_id": card.agent_id,
            "name": card.name,
            "version": card.version,
            "protocolVersion": card.protocolVersion,
            "endpoint_url": card.url,
            "cloud_run_url": card.cloud_run_url,
            "well_known_url": card.well_known_url,
            "gcp_service_name": card.gcp_service_name,
            "gcp_agent_urn": card.gcp_agent_urn,
            "spiffe_id": card.authentication.spiffe_id,
            "skill_id": matched_skill.id if matched_skill else intent,
            "skill_name": matched_skill.name if matched_skill else intent,
        }

        try:
            with active_tracer.start_span(
                f"a2a.{card.agent_id}.{intent}",
                attributes={
                    "task_id": tid,
                    "sender_agent": sender_agent,
                    "target_agent": card.agent_id,
                    "agent_card_url": card.url,
                    "gcp_agent_urn": card.gcp_agent_urn,
                    "well_known_url": card.well_known_url,
                    "skill_id": card_summary["skill_id"],
                },
            ):
                result = endpoint_handler(intent, payload)

            AgentRegistry._invocation_counts[card.agent_id] = (
                AgentRegistry._invocation_counts.get(card.agent_id, 0) + 1
            )

            self.db_manager.log_agent_audit(
                agent_id=card.agent_id,
                spiffe_id=card.authentication.spiffe_id,
                action=f"A2A_CARD:{intent}",
                status="COMPLETED",
                details={
                    "task_id": tid,
                    "sender_agent": sender_agent,
                    "agent_card_url": card.url,
                    "gcp_agent_urn": card.gcp_agent_urn,
                    "well_known_url": card.well_known_url,
                    "skill_id": card_summary["skill_id"],
                },
                trace_id=active_tracer.trace_id,
            )
            return {
                "task_id": tid,
                "sender_agent": sender_agent,
                "target_agent": card.agent_id,
                "intent": intent,
                "status": "COMPLETED",
                "agent_card": card_summary,
                "result": result,
            }
        except Exception as e:
            logger.error(f"[AgentRegistry] Agent Card execution failed at {card.url}: {e}", exc_info=True)
            self.db_manager.log_agent_audit(
                agent_id=card.agent_id,
                spiffe_id=card.authentication.spiffe_id,
                action=f"A2A_CARD:{intent}",
                status="FAILED",
                details={
                    "task_id": tid,
                    "agent_card_url": card.url,
                    "gcp_agent_urn": card.gcp_agent_urn,
                    "error": str(e),
                },
                trace_id=active_tracer.trace_id,
            )
            return {
                "task_id": tid,
                "sender_agent": sender_agent,
                "target_agent": card.agent_id,
                "intent": intent,
                "status": "FAILED",
                "agent_card": card_summary,
                "error": str(e),
            }

    # -------------------------------------------------------------------------
    # Google Cloud Agent Registry API (`agentregistry.googleapis.com/v1alpha`)
    # -------------------------------------------------------------------------
    @staticmethod
    def _gcp_request(
        method: str,
        url: str,
        token: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def sync_to_gcp_agent_registry(
        self,
        mcp_tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Registers or updates all 5 A2A `AgentCard` services, the `LedgerMCPServer` (`TOOL_SPEC`),
        and all A2A + MCP `Bindings` in Google Cloud Agent Registry (`agentregistry.googleapis.com`)."""
        token = _get_gcp_access_token()
        if not token:
            return {
                "status": "SKIPPED",
                "reason": "No Google Cloud ADC / gcloud access token available in this environment.",
                "project_id": GCP_PROJECT_ID,
            }

        base_url = (
            f"https://agentregistry.googleapis.com/v1alpha/projects/{GCP_PROJECT_ID}"
            f"/locations/{GCP_AGENT_REGISTRY_LOCATION}"
        )
        synced_agents: List[Dict[str, Any]] = []
        synced_bindings: List[Dict[str, Any]] = []
        errors: List[str] = []

        # 1. Register / update all 5 A2A Agent Cards as Services (`A2A_AGENT_CARD`)
        for agent_id in [
            "orchestrator",
            "ingestion-agent",
            "reconciliation-agent",
            "clarification-agent",
            "advisor-agent",
        ]:
            card = AgentRegistry._cards.get(agent_id)
            if not card:
                continue
            svc_id = card.gcp_service_id
            payload = {
                "displayName": card.name[:63],
                "description": card.description[:2048],
                "agentSpec": {
                    "type": "A2A_AGENT_CARD",
                    "content": card.to_gcp_a2a_card(),
                },
            }
            create_url = f"{base_url}/services?serviceId={svc_id}"
            try:
                self._gcp_request("POST", create_url, token, payload)
                action = "CREATED"
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    # Already exists -> PATCH to keep Agent Card spec up to date
                    patch_url = (
                        f"{base_url}/services/{svc_id}"
                        f"?updateMask=displayName,description,agentSpec"
                    )
                    try:
                        self._gcp_request("PATCH", patch_url, token, payload)
                        action = "UPDATED"
                    except Exception as pe:
                        action = "EXISTS"
                        logger.debug(f"Patch skipped for {svc_id}: {pe}")
                else:
                    err_text = e.read().decode("utf-8", errors="ignore")
                    errors.append(f"{svc_id}: HTTP {e.code} {err_text}")
                    continue
            except Exception as ex:
                errors.append(f"{svc_id}: {ex}")
                continue

            synced_agents.append({
                "agent_id": agent_id,
                "service_id": svc_id,
                "service_name": card.gcp_service_name,
                "agent_urn": card.gcp_agent_urn,
                "cloud_run_url": card.cloud_run_url,
                "action": action,
            })

        # 2. Register / update the Official MCP Server (`TOOL_SPEC`)
        mcp_sync_info: Optional[Dict[str, Any]] = None
        if mcp_tools:
            clean_tools = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t.get("inputSchema") or t.get("parameters") or {"type": "object"},
                }
                for t in mcp_tools
            ]
            mcp_payload = {
                "displayName": "Personal Finance Ledger MCP Server",
                "description": (
                    "Official MCP Server exposing 11 financial tools across ledger_db, "
                    "document_store, bank_connectors, and calculators."
                ),
                "interfaces": [
                    {
                        "url": f"{CLOUD_RUN_BASE_URL}/mcp/jsonrpc",
                        "protocolBinding": "JSONRPC",
                    }
                ],
                "mcpServerSpec": {
                    "type": "TOOL_SPEC",
                    "content": {"tools": clean_tools},
                },
            }
            try:
                self._gcp_request(
                    "POST",
                    f"{base_url}/services?serviceId={GCP_MCP_SERVICE_ID}",
                    token,
                    mcp_payload,
                )
                mcp_action = "CREATED"
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    mcp_action = "EXISTS"
                else:
                    mcp_action = f"ERROR ({e.code})"
            except Exception as ex:
                mcp_action = f"ERROR ({ex})"

            mcp_sync_info = {
                "service_id": GCP_MCP_SERVICE_ID,
                "service_name": GCP_MCP_SERVICE_NAME,
                "mcp_server_urn": GCP_MCP_SERVER_URN,
                "endpoint_url": f"{CLOUD_RUN_BASE_URL}/mcp/jsonrpc",
                "tools_count": len(clean_tools),
                "action": mcp_action,
            }

        # 3. Register A2A & MCP Bindings in Google Cloud Agent Registry
        orch_urn = _gcp_agent_urn("orchestrator")
        binding_specs = [
            # A2A Bindings: Orchestrator -> 4 Specialist Agents
            ("pfa-bind-orch-ingestion", "Orchestrator -> Ingestion Agent A2A Binding", orch_urn, _gcp_agent_urn("ingestion-agent")),
            ("pfa-bind-orch-reconciliation", "Orchestrator -> Reconciliation Agent A2A Binding", orch_urn, _gcp_agent_urn("reconciliation-agent")),
            ("pfa-bind-orch-clarification", "Orchestrator -> Clarification Agent A2A Binding", orch_urn, _gcp_agent_urn("clarification-agent")),
            ("pfa-bind-orch-advisor", "Orchestrator -> Advisor Agent A2A Binding", orch_urn, _gcp_agent_urn("advisor-agent")),
            # MCP Bindings: Agents -> Ledger MCP Server
            ("pfa-bind-orch-mcp", "Orchestrator -> Ledger MCP Server Binding", orch_urn, GCP_MCP_SERVER_URN),
            ("pfa-bind-ingestion-mcp", "Ingestion Agent -> Ledger MCP Server Binding", _gcp_agent_urn("ingestion-agent"), GCP_MCP_SERVER_URN),
            ("pfa-bind-reconciliation-mcp", "Reconciliation Agent -> Ledger MCP Server Binding", _gcp_agent_urn("reconciliation-agent"), GCP_MCP_SERVER_URN),
            ("pfa-bind-clarification-mcp", "Clarification Agent -> Ledger MCP Server Binding", _gcp_agent_urn("clarification-agent"), GCP_MCP_SERVER_URN),
            ("pfa-bind-advisor-mcp", "Advisor Agent -> Ledger MCP Server Binding", _gcp_agent_urn("advisor-agent"), GCP_MCP_SERVER_URN),
        ]

        auth_provider_name = (
            f"projects/{GCP_PROJECT_ID}/locations/{GCP_AGENT_REGISTRY_LOCATION}"
            "/authProviders/pfa-spiffe-wif-auth"
        )
        for bind_id, display_name, src_urn, tgt_urn in binding_specs:
            bind_payload = {
                "displayName": display_name[:63],
                "description": f"Zero-Trust binding from {src_urn} to {tgt_urn}",
                "source": {"identifier": src_urn},
                "target": {"identifier": tgt_urn},
                "authProviderBinding": {"authProvider": auth_provider_name},
            }
            try:
                self._gcp_request(
                    "POST",
                    f"{base_url}/bindings?bindingId={bind_id}",
                    token,
                    bind_payload,
                )
                b_action = "CREATED"
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    b_action = "EXISTS"
                else:
                    b_action = f"HTTP_{e.code}"
            except Exception as ex:
                b_action = f"ERR:{ex}"

            synced_bindings.append({
                "binding_id": bind_id,
                "source_urn": src_urn,
                "target_urn": tgt_urn,
                "action": b_action,
            })

        return {
            "status": "SYNCED" if not errors else "PARTIAL",
            "project_id": GCP_PROJECT_ID,
            "location": GCP_AGENT_REGISTRY_LOCATION,
            "api": "agentregistry.googleapis.com/v1alpha",
            "agents": synced_agents,
            "mcp_server": mcp_sync_info,
            "bindings": synced_bindings,
            "errors": errors,
        }

    def fetch_from_gcp_agent_registry(self) -> Dict[str, Any]:
        """Queries live registered Agents, MCP Servers, and Bindings from Google Cloud Agent Registry."""
        token = _get_gcp_access_token()
        if not token:
            return {
                "status": "OFFLINE_FALLBACK",
                "project_id": GCP_PROJECT_ID,
                "agents": [],
                "mcp_servers": [],
                "bindings": [],
            }

        base_url = (
            f"https://agentregistry.googleapis.com/v1alpha/projects/{GCP_PROJECT_ID}"
            f"/locations/{GCP_AGENT_REGISTRY_LOCATION}"
        )
        pfa_agents = []
        pfa_mcp = []
        pfa_bindings = []

        try:
            agents_resp = self._gcp_request("GET", f"{base_url}/agents", token)
            for ag in agents_resp.get("agents", []):
                aid = ag.get("agentId", "")
                if "pfa-" in aid:
                    pfa_agents.append(ag)
                    # Backfill projected registryResource onto local AgentCard if matched
                    for card in AgentRegistry._cards.values():
                        if card.gcp_agent_urn == aid:
                            card.gcp_registry_resource = ag.get("name", "")
        except Exception as e:
            logger.debug(f"GCP Agent Registry agents list failed: {e}")

        try:
            mcp_resp = self._gcp_request("GET", f"{base_url}/mcpServers", token)
            for srv in mcp_resp.get("mcpServers", []):
                if "pfa-" in srv.get("mcpServerId", ""):
                    pfa_mcp.append(srv)
        except Exception as e:
            logger.debug(f"GCP Agent Registry mcpServers list failed: {e}")

        try:
            bind_resp = self._gcp_request("GET", f"{base_url}/bindings", token)
            for b in bind_resp.get("bindings", []):
                if "pfa-" in b.get("name", ""):
                    pfa_bindings.append(b)
        except Exception as e:
            logger.debug(f"GCP Agent Registry bindings list failed: {e}")

        return {
            "status": "ONLINE",
            "project_id": GCP_PROJECT_ID,
            "location": GCP_AGENT_REGISTRY_LOCATION,
            "api": "agentregistry.googleapis.com/v1alpha",
            "agents_count": len(pfa_agents),
            "agents": pfa_agents,
            "mcp_servers": pfa_mcp,
            "bindings_count": len(pfa_bindings),
            "bindings": pfa_bindings,
        }
