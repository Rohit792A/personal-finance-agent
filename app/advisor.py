# app/advisor.py
"""Strategic AI Personal CFO Advisor Agent (`advisor-agent`).

Refactored to FDE Production Standard (Four Pillars: Build, Govern, Scale, Optimize):
- Pillar 1 (Build): ledger context from in-process MCP tool calls (`MCPClient.get_context_snapshot`: four
  tools), put into the prompt; no tools are passed to Gemini, so there is no function calling. Proactive
  EMI/cash-shortfall alerts come from the MCP calculator tool.
- Pillar 2 (Govern): agent identity `advisor-agent` (HMAC-signed workload tokens, a local stand-in for
  SPIFFE, security/auth.py), prompt screening (regex, plus the Google Cloud Model Armor API on prompts
  and replies when MODEL_ARMOR_TEMPLATE is set) and PII tokenization (regex, plus Google Sensitive
  Data Protection when SDP_ENABLED=1) before the model sees the message (DEC-063).
- Pillar 3 (Scale): OpenTelemetry distributed span instrumentation (`AgentTracer`) across every stage;
  one root span per chat / insights request, exported to Cloud Trace when ENABLE_CLOUD_TRACE=1.
- Pillar 4 (Optimize): Dynamic Model Tiering (Lite / Standard / Pro), Prefix Context Caching (`PrefixContextCacheManager`),
  and Sliding-Window Context Compaction to eliminate quadratic token growth.
  Caching (DEC-063): with ENABLE_VERTEX_CONTEXT_CACHE=1 the static persona is stored as an explicit
  Vertex AI cache per model and passed to Gemini as `cached_content` (no system instruction is sent
  then); otherwise, or if the cache can't be created, requests are sent exactly as before. Every
  reply's `usage_metadata` (including `cached_content_token_count`, which also counts implicit
  caching) is recorded on the model call's span and in the process totals.
- Gemini 3 (DEC-042): each tier's model comes from config (`MODEL_LITE`, `MODEL_NAME`, `MODEL_PRO`); by
  default Standard and Pro both use gemini-3.5-flash.
  Requests set no temperature and no thinking budget: Gemini 3 is tuned for its default
  temperature, and it chooses its own thinking depth.
"""
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import vertexai
from vertexai.generative_models import Content, GenerativeModel, Part

# app/ is the import root everywhere (Cloud Shell, tests and the Docker image); see DEC-025.
from config import LOCATION, MODEL_LITE, MODEL_NAME, MODEL_PRO, PROJECT_ID
from context_cache import PrefixContextCacheManager
from gemini import reply_text
from mcp import MCPClient
from security import FinancialDLPPipeline, ModelArmor, SecurityViolationError
from telemetry import AgentTracer
from telemetry.usage import record_usage

logger = logging.getLogger(__name__)

# Shown instead of a reply that Model Armor flags (DEC-063; only with MODEL_ARMOR_TEMPLATE set).
WITHHELD_REPLY_MESSAGE = (
    "⚠️ **Security Policy Alert**: The advisor's reply was withheld because the Model Armor response "
    "policy flagged it ({reason}). Please rephrase your question."
)

STATIC_FIDUCIARY_PREFIX = """System Persona:
You are a world-class, elite Personal CFO and Wealth Advisor (`advisor-agent`).
Your objective is to provide actionable, rigorous, and personalized financial advice to help the user grow wealth, eliminate wasteful expenses, optimize cash flow timing, and manage investments.
All amounts are in Indian Rupees (INR, ₹).

Core Fiduciary Tenets:
1. Act as a dedicated fiduciary: every number must come from the verified Ledger MCP Server or deterministic calculators, never from memory.
2. Warn proactively if upcoming rent, EMIs, or recurring bills risk exceeding available liquid cash flow.
3. Prioritize clearing high-interest credit card/personal debt and maintaining an emergency buffer before aggressive equity allocation.
4. Format in clean GitHub-style Markdown with bold key figures and clear action items."""


class ModelTier(str, Enum):
    """FDE Dynamic Model Tiering specifications.

    The values are tier names, not model names: the Standard and Pro tiers may use the same
    model, and equal Enum values would make one tier an alias of the other.
    """
    LITE = "lite"
    STANDARD = "standard"
    PRO = "pro"

    @property
    def model(self) -> str:
        """The Gemini model this tier uses (config.py, overridable per environment)."""
        return {"lite": MODEL_LITE, "standard": MODEL_NAME, "pro": MODEL_PRO}[self.value]


class FinancialAdvisor:
    """Enterprise Personal CFO and Wealth Planning Specialist Agent (`advisor-agent`)."""

    AGENT_ID = "advisor-agent"

    def __init__(self, default_model: Optional[str] = None):
        self.default_model = default_model or MODEL_NAME
        self.model = None
        self.mcp_client = MCPClient(caller_agent_id=self.AGENT_ID)
        self.model_armor = ModelArmor(strict_mode=True)
        self.dlp = FinancialDLPPipeline()
        self.context_cache = PrefixContextCacheManager(ttl_seconds=7200)
        self.last_trace_summary: Optional[Dict[str, Any]] = None

        try:
            vertexai.init(project=PROJECT_ID, location=LOCATION)
            self.model = GenerativeModel(self.default_model)
        except Exception as e:
            logger.warning(f"[FinancialAdvisor] Vertex AI init skipped or unavailable: {e}")

    def _select_model_tier(self, message: str, history_len: int) -> ModelTier:
        """Pillar 4 (Optimize): Dynamic Model Tiering router.

        Returns the tier; `ModelTier.model` names its Gemini model.
        """
        clean_msg = message.lower()

        # Strategic / Complex scenario queries -> Tier 3: Pro
        pro_triggers = [
            "simulate", "retirement", "retire", "fire", "portfolio", "tax loss", "tax-loss",
            "compound interest", "10 year", "5 year", "asset allocation", "amortization"
        ]
        if any(trigger in clean_msg for trigger in pro_triggers):
            logger.info(f"[Model Router] Routing to Tier 3 (Pro, {ModelTier.PRO.model}) for complex strategy.")
            return ModelTier.PRO

        # Simple lookup / categorization / single fact queries -> Tier 1: Lite
        lite_triggers = ["what is my balance", "total spend on", "list categories", "how many transactions"]
        if any(trigger in clean_msg for trigger in lite_triggers) and history_len <= 2:
            logger.info(f"[Model Router] Routing to Tier 1 (Lite, {ModelTier.LITE.model}) for low-latency fact retrieval.")
            return ModelTier.LITE

        # General multi-turn fiduciary conversation -> Tier 2: Standard
        return ModelTier.STANDARD

    def _compact_context_history(
        self, raw_history: Optional[List[Dict[str, Any]]], max_turns: int = 4
    ) -> Tuple[List[Content], Optional[str]]:
        """Pillar 4 (Optimize): Sliding-Window Context Compaction to eliminate O(N^2) quadratic growth.

        Retains the last `max_turns` verbatim turns. If earlier turns exist, condenses them into
        a structured state summary anchor.
        """
        if not raw_history:
            return [], None

        clean_history = [
            msg for msg in raw_history
            if msg.get("content") and str(msg.get("content")).strip()
        ]

        if len(clean_history) <= max_turns:
            vertex_history = []
            for msg in clean_history:
                role = "model" if msg.get("role") in ("assistant", "model") else "user"
                vertex_history.append(Content(role=role, parts=[Part.from_text(msg["content"].strip())]))
            return vertex_history, None

        # Compact older turns into a rolling memory summary
        older_turns = clean_history[:-max_turns]
        recent_turns = clean_history[-max_turns:]

        summary_bullet_points = []
        for turn in older_turns:
            role_label = "User asked" if turn.get("role") == "user" else "Advisor recommended"
            snippet = turn.get("content", "")[:120].replace("\n", " ")
            summary_bullet_points.append(f"- {role_label}: \"{snippet}...\"")

        historical_summary_anchor = "### Earlier Conversation Context Summary (Compacted):\n" + "\n".join(summary_bullet_points)

        vertex_history = []
        for msg in recent_turns:
            role = "model" if msg.get("role") in ("assistant", "model") else "user"
            vertex_history.append(Content(role=role, parts=[Part.from_text(msg["content"].strip())]))

        logger.info(f"[Context Compaction] Compressed {len(older_turns)} older turns to prevent quadratic token growth.")
        return vertex_history, historical_summary_anchor

    def _get_financial_summary_text(self) -> str:
        """Fetches aggregated financial context via decoupled MCP Client."""
        try:
            return self.mcp_client.get_context_snapshot()
        except Exception as e:
            logger.warning(f"MCP context retrieval error: {e}")
            return "Financial summary currently unavailable."

    def check_proactive_alerts(
        self,
        current_balance: Optional[float] = None,
        upcoming_commitments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Invokes the MCP deterministic calculator to detect upcoming EMI/rent balance shortfalls."""
        args: Dict[str, Any] = {}
        if current_balance is not None:
            args["current_balance"] = current_balance
        if upcoming_commitments is not None:
            args["upcoming_commitments"] = upcoming_commitments
        res = self.mcp_client.call_tool("calculate_emi_and_shortfall", args)
        if res.get("is_error"):
            return {"will_fall_short": False, "alerts": [], "error": res.get("error")}
        return res.get("data", {"will_fall_short": False, "alerts": []})

    def generate_insights(self) -> str:
        """Generates executive CFO wealth optimization insights with OTel tracing and prefix caching."""
        logger.info("[FinancialAdvisor] Generating executive CFO insights via MCP plane...")
        tracer = AgentTracer(workflow_name="advisor_generate_insights")
        # One trace per request (DEC-063): every span below joins this root span.
        with tracer.request_span("advisor.generate_insights", {"agent": self.AGENT_ID}):
            return self._generate_insights(tracer)

    def _generate_insights(self, tracer: AgentTracer) -> str:
        """The insights pipeline (see `generate_insights`)."""
        try:
            # The MCP tool calls made inside this block become children of this span (DEC-063).
            with tracer.start_span("mcp_context_snapshot", {"agent": self.AGENT_ID}):
                data_string = self._get_financial_summary_text()

            if "No financial records" in data_string:
                self.last_trace_summary = tracer.get_summary(persist=True)
                return "No data available yet! Please import some transactions first."

            cache_span = tracer.start_span("prefix_context_cache", {"model": self.default_model})
            cache_meta = self.context_cache.get_or_register_prefix(self.default_model, STATIC_FIDUCIARY_PREFIX)
            # An explicit Vertex AI cache of the persona (ENABLE_VERTEX_CONTEXT_CACHE=1), or None.
            explicit_cache = (
                self.context_cache.get_explicit_cache(self.default_model, STATIC_FIDUCIARY_PREFIX) if self.model else None
            )
            tracer.end_span(cache_span, status="OK", extra_attributes={
                "cache_hit": cache_meta["cache_hit"],
                **self.context_cache.explicit_cache_attributes(self.default_model, explicit_cache),
            })

            # Everything after the static persona; sent alone when the persona is in an explicit cache.
            insights_request = f"""Real-Time Verified Financial State (from Ledger MCP Server):
<financial_summary>
{data_string}
</financial_summary>

Deliver 3 concrete, high-impact, non-obvious recommendations.
Format in clean Markdown with bold numbers and clear action steps."""
            prompt = f"{STATIC_FIDUCIARY_PREFIX}\n\n{insights_request}"

            if not self.model:
                self.last_trace_summary = tracer.get_summary(persist=True)
                return "Vertex AI is currently initializing or offline in this environment."

            llm_span = tracer.start_span("vertex_ai_generate_insights", {"model": self.default_model})
            response, used_cache = self._generate_insights_reply(prompt, insights_request, explicit_cache)
            text = reply_text(response)
            tracer.end_span(llm_span, status="OK", extra_attributes=record_usage(
                response, self.default_model, "advisor.insights", explicit_cache=used_cache))
            self.last_trace_summary = tracer.get_summary(persist=True)
            return text
        except Exception as e:
            logger.error(f"[FinancialAdvisor] Insights generation failed: {e}", exc_info=True)
            self.last_trace_summary = tracer.get_summary(persist=True)
            return "Trouble analyzing finances. Please review system logs."

    def _generate_insights_reply(self, prompt: str, insights_request: str, explicit_cache) -> Tuple[Any, bool]:
        """(reply, used_explicit_cache). With an explicit cache the persona is not sent again: the
        API rejects a request that repeats what the cache holds (DEC-063)."""
        if explicit_cache is not None:
            try:
                cached_model = GenerativeModel.from_cached_content(cached_content=explicit_cache)
                return cached_model.generate_content(insights_request), True
            except Exception as e:
                self.context_cache.disable_explicit_cache(
                    self.default_model, f"generation with the cache failed ({type(e).__name__})")
        return self.model.generate_content(prompt), False

    def chat(self, message: str, history: Optional[List[Dict[str, Any]]] = None) -> str:
        """FDE Production Multi-Turn Conversational Interface.
        Pipeline:
        1. Model Armor: Screen input for adversarial prompt injection / delimiter tampering.
        2. Cloud DLP: Redact sensitive financial/PII entities before LLM transmission.
        3. Dynamic Tiering: Route query to the Lite, Standard, or Pro tier's model (config.py).
        4. Prefix Context Caching & Context Compaction: Cache static prefix and compact older history.
        5. MCP Context: four in-process MCP tool calls; their results go into the prompt (no function calling).
        6. Cloud Model Execution: Vertex AI start_chat.
        7. Cloud DLP Restore: De-tokenize and rehydrate responses for client presentation.

        DEC-063: steps 1 and 2 are local regex layers, followed by the Google Cloud Model Armor and
        Sensitive Data Protection APIs when MODEL_ARMOR_TEMPLATE / SDP_ENABLED are set; with Model
        Armor on, the reply is screened as well (step 6b) before it is de-tokenized. Step 4 passes
        an explicit Vertex AI cache to Gemini when ENABLE_VERTEX_CONTEXT_CACHE=1. The turn is one
        trace (root span `advisor.chat`).
        """
        tracer = AgentTracer(workflow_name="advisor_chat")
        with tracer.request_span("advisor.chat", {"agent": self.AGENT_ID}):
            return self._chat_turn(tracer, message, history)

    def _armor_attributes(self, response: bool = False) -> Dict[str, Any]:
        """Span attributes saying which Model Armor layer decided (DEC-063)."""
        decision = getattr(self.model_armor, "last_response_decision" if response else "last_decision", None)
        return ModelArmor.decision_attributes(decision)

    def _dlp_attributes(self) -> Dict[str, Any]:
        """Span attributes saying which redaction layers ran (DEC-063)."""
        attributes = getattr(self.dlp, "trace_attributes", None)
        return attributes() if callable(attributes) else {}

    def _chat_turn(self, tracer: AgentTracer, message: str, history: Optional[List[Dict[str, Any]]]) -> str:
        """The chat pipeline (see `chat`)."""
        # Step 1: Model Armor Screening (Govern)
        armor_span = tracer.start_span("model_armor_screen_prompt", {"agent": self.AGENT_ID})
        try:
            with tracer.activate(armor_span):  # a Model Armor API call's span nests under this one
                is_safe, sanitized_msg, threat_reason = self.model_armor.screen_prompt(message)
        except SecurityViolationError as sve:
            tracer.end_span(armor_span, status="SECURITY_BLOCKED",
                            extra_attributes={"threat": sve.threat_type, **self._armor_attributes()})
            self.last_trace_summary = tracer.get_summary(persist=True)
            raise
        if not is_safe:
            tracer.end_span(armor_span, status="SECURITY_BLOCKED",
                            extra_attributes={"reason": threat_reason, **self._armor_attributes()})
            self.last_trace_summary = tracer.get_summary(persist=True)
            logger.warning(f"[ModelArmor] Blocked prompt injection turn: {threat_reason}")
            return (
                "⚠️ **Security Policy Alert**: Your query could not be processed because it contains "
                f"restricted directives or system delimiters ({threat_reason})."
            )
        tracer.end_span(armor_span, status="OK", extra_attributes=self._armor_attributes())

        # Step 2: Cloud DLP Redaction (Govern)
        dlp_span = tracer.start_span("cloud_dlp_redact", {"agent": self.AGENT_ID})
        with tracer.activate(dlp_span):  # a Sensitive Data Protection call's span nests under this one
            redacted_msg, token_map = self.dlp.redact(sanitized_msg)
        tracer.end_span(dlp_span, status="OK",
                        extra_attributes={"entities_redacted": len(token_map), **self._dlp_attributes()})

        # Step 3: Dynamic Model Tiering (Optimize)
        history_len = len(history or [])
        tier = self._select_model_tier(redacted_msg, history_len)

        # Step 4: Prefix Caching & Sliding Window Compaction (Optimize)
        cache_span = tracer.start_span("prefix_context_cache", {"tier": tier.value, "model": tier.model})
        cache_meta = self.context_cache.get_or_register_prefix(tier.model, STATIC_FIDUCIARY_PREFIX)
        # An explicit Vertex AI cache of the persona (ENABLE_VERTEX_CONTEXT_CACHE=1), or None.
        explicit_cache = self.context_cache.get_explicit_cache(tier.model, STATIC_FIDUCIARY_PREFIX) if self.model else None
        tracer.end_span(cache_span, status="OK", extra_attributes={
            "cache_hit": cache_meta["cache_hit"],
            **self.context_cache.explicit_cache_attributes(tier.model, explicit_cache),
        })

        vertex_history, history_anchor = self._compact_context_history(history, max_turns=4)

        # Step 5: Fetch verified financial metrics from decoupled MCP tool plane (Build)
        # The MCP tool calls made inside this block become children of this span (DEC-063).
        with tracer.start_span("mcp_context_snapshot", {"agent": self.AGENT_ID}):
            data_string = self._get_financial_summary_text()

        anchor_clause = f"\n{history_anchor}\n" if history_anchor else ""

        # Everything after the static persona changes per turn. Without an explicit cache it ends
        # the system instruction (as before); with one, it goes into the user turn (DEC-063).
        turn_context = f"""Real-Time Verified Financial State (via Ledger MCP Server):
<financial_summary>
{data_string}
</financial_summary>
{anchor_clause}
Guidelines:
1. Act as a dedicated fiduciary.
2. Base all figures strictly on verified financial metrics above.
3. Answer follow-up questions directly, maintaining full context.
4. Format in clean GitHub-style Markdown with bold key figures and clear action items."""
        system_instruction = f"{STATIC_FIDUCIARY_PREFIX}\n\n{turn_context}"

        if self.model:
            llm_span = tracer.start_span("vertex_ai_chat_turn", {"tier": tier.value, "model": tier.model})
            try:
                # No temperature or thinking budget for Gemini 3 (see the module docstring).
                generation_config: Dict[str, Any] = {"top_p": 0.95}

                raw_response, used_cache = self._send_chat_turn(
                    tier, system_instruction, turn_context, vertex_history, redacted_msg,
                    generation_config, explicit_cache,
                )
                raw_text = reply_text(raw_response)
                tracer.end_span(llm_span, status="OK", extra_attributes=record_usage(
                    raw_response, tier.model, "advisor.chat", explicit_cache=used_cache))

                # Step 6b: Model Armor screens the reply (only with MODEL_ARMOR_TEMPLATE set, DEC-063)
                raw_text = self._screen_reply(tracer, raw_text, redacted_msg)

                # Step 7: Cloud DLP Restore / De-tokenization (Govern)
                restored_reply = self.dlp.restore(raw_text, token_map)
                self.last_trace_summary = tracer.get_summary(persist=True)
                return restored_reply

            except Exception as e:
                tracer.end_span(llm_span, status="ERROR", extra_attributes={"error": str(e)})
                self.last_trace_summary = tracer.get_summary(persist=True)
                logger.error(f"[FinancialAdvisor] Vertex AI chat call failed: {e}", exc_info=True)
                return self._fallback_chat_reply(message, history, data_string)
        else:
            self.last_trace_summary = tracer.get_summary(persist=True)
            return self._fallback_chat_reply(message, history, data_string)

    def _send_chat_turn(
        self,
        tier: ModelTier,
        system_instruction: str,
        turn_context: str,
        vertex_history: List[Content],
        redacted_msg: str,
        generation_config: Dict[str, Any],
        explicit_cache,
    ) -> Tuple[Any, bool]:
        """Sends the chat turn; returns (reply, used_explicit_cache).

        With an explicit cache (DEC-063) the model is built from it and gets no system
        instruction (the API rejects one next to `cached_content`); the per-turn context goes in
        the user message. If that fails, explicit caching is switched off for the model and the
        turn is sent the usual way.
        """
        if explicit_cache is not None:
            try:
                cached_model = GenerativeModel.from_cached_content(cached_content=explicit_cache)
                cached_session = cached_model.start_chat(history=list(vertex_history))
                reply = cached_session.send_message(
                    f"{turn_context}\n\nUser message:\n{redacted_msg}", generation_config=generation_config)
                return reply, True
            except Exception as e:
                self.context_cache.disable_explicit_cache(
                    tier.model, f"generation with the cache failed ({type(e).__name__})")
        advisor_model = GenerativeModel(
            tier.model,
            system_instruction=[Part.from_text(system_instruction)],
        )
        chat_session = advisor_model.start_chat(history=vertex_history)
        return chat_session.send_message(redacted_msg, generation_config=generation_config), False

    def _screen_reply(self, tracer: AgentTracer, raw_text: str, redacted_msg: str) -> str:
        """Step 6b (DEC-063): the Model Armor API screens the reply. Only with MODEL_ARMOR_TEMPLATE
        set; otherwise no span is added and the reply passes, as before. A flagged reply is
        replaced by WITHHELD_REPLY_MESSAGE; an API failure lets it through (logged)."""
        if not ModelArmor.api_enabled():
            return raw_text
        span = tracer.start_span("model_armor_screen_response", {"agent": self.AGENT_ID})
        with tracer.activate(span):
            is_safe, reason = self.model_armor.screen_model_response(raw_text, user_prompt=redacted_msg)
        if is_safe:
            tracer.end_span(span, status="OK", extra_attributes=self._armor_attributes(response=True))
            return raw_text
        tracer.end_span(span, status="SECURITY_BLOCKED",
                        extra_attributes={"reason": reason, **self._armor_attributes(response=True)})
        return WITHHELD_REPLY_MESSAGE.format(reason=reason)

    def _fallback_chat_reply(
        self, message: str, history: Optional[List[Dict[str, Any]]], data_string: str
    ) -> str:
        """Graceful response when Vertex AI is unauthenticated or temporarily offline."""
        clean_msg = (message or "").strip().lower()
        if "spend" in clean_msg or "expense" in clean_msg:
            topic_hint = "Based on your aggregated records, review your top expense categories in the Analytics tab."
        elif "invest" in clean_msg or "sip" in clean_msg or "stock" in clean_msg:
            topic_hint = "Consistent investments in SIPs and index funds are proven drivers of compound wealth growth."
        elif "budget" in clean_msg or "save" in clean_msg:
            topic_hint = "Consider the 50/30/20 rule: 50% essentials, 30% discretionary, and 20% savings/SIP."
        else:
            topic_hint = "I am ready to analyze your spending leaks, budget limits, and cash flow."

        return (
            f"**AI CFO Advisor (Fiduciary Guidance):**\n\n"
            f"Regarding your query *\"{message}\"*:\n\n"
            f"{topic_hint}\n\n"
            f"> [!NOTE]\n"
            f"> Direct Vertex AI real-time reasoning is operating in resilient fallback mode with MCP metrics active."
        )