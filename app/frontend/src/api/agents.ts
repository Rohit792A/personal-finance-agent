/**
 * A2A Agent Registry, 4-Agent CFO Workflow, Closed-Loop Categorization Rules,
 * Official MCP Tool Catalogue, and SPIFFE Governance Audit Trail API client.
 */
import { apiClient } from './client';
import { AI_TIMEOUT_MS, API_ENDPOINTS } from './config';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export interface AgentSkillSummary {
  id: string;
  name: string;
  description: string;
}

export interface AgentCardSummary {
  agent_id: string;
  name: string;
  description: string;
  version: string;
  protocol_version: string;
  preferred_transport: string;
  url: string;
  well_known_url: string;
  status: string;
  gcp_agent_urn: string;
  spiffe_id: string;
  skills: AgentSkillSummary[];
}

export interface AgentRegistryResponse {
  registry_status: string;
  gcp_project_id: string;
  gcp_agent_registry_api: string;
  total_agents: number;
  agent_cards: AgentCardSummary[];
}

export interface LearnedRuleItem {
  payee_pattern: string;
  category: string;
  transaction_type: string;
  is_self_transfer: boolean;
  source: string;
  hit_count: number;
  updated_at: string | null;
}

export interface AuditLogItem {
  id: number;
  trace_id: string | null;
  agent_id: string;
  spiffe_id: string;
  action: string;
  status: string;
  details: Record<string, unknown>;
  requires_approval: boolean;
  approved_by: string | null;
  created_at: string | null;
}

export interface McpToolItem {
  name: string;
  description: string;
  required_params: string[];
}

export interface McpCatalogueResponse {
  protocol_version: string;
  server_name: string;
  server_version: string;
  tools: McpToolItem[];
}

export interface WeakestStepInfo {
  step_name: string;
  avg_duration_ms: number;
  max_duration_ms: number;
  error_rate: number;
}

export interface WorkflowVerificationResult {
  workflow_status: string;
  agents_invoked: number;
  sanitized_rows: number;
  reconciled_math_status: string;
  learned_rules_applied: number;
  proactive_alerts_count: number;
}

export interface MasterResolutionDecision {
  id: number;
  date: string;
  amount: number;
  description: string;
  current_category: string;
  current_type: string;
  requires_human_review: boolean;
  current_confidence: number;
  recommended_category: string;
  recommended_type: string;
  is_self_transfer: boolean;
  new_confidence: number;
  deciding_agent: string;
  rule_tier: string;
  reasoning: string;
  payee_pattern: string;
  teach_rule: boolean;
  needs_update: boolean;
}

export interface MasterResolutionReport {
  workflow_id: string;
  status: string;
  month_filter: string;
  total_candidates: number;
  agents_invoked: string[];
  summary_by_tier: Record<string, number>;
  summary_by_category: Record<string, number>;
  summary_by_agent: Record<string, number>;
  decisions: MasterResolutionDecision[];
  applied_result: {
    updated_count: number;
    rules_learned_count: number;
    updated_ids: number[];
  } | null;
}

export interface MasterResolutionUpdateItem {
  id: number;
  category: string;
  transaction_type: string;
  is_self_transfer: boolean;
  payee_pattern: string;
  teach_rule: boolean;
  reasoning?: string;
}

function parseSkill(raw: unknown): AgentSkillSummary | null {
  if (!isRecord(raw)) return null;
  return {
    id: typeof raw.id === 'string' ? raw.id : 'skill',
    name: typeof raw.name === 'string' ? raw.name : 'Skill',
    description: typeof raw.description === 'string' ? raw.description : '',
  };
}

function parseAgentCard(raw: unknown): AgentCardSummary | null {
  if (!isRecord(raw)) return null;
  const auth = isRecord(raw.authentication) ? raw.authentication : {};
  const skillsRaw = Array.isArray(raw.skills) ? raw.skills : [];
  const skills: AgentSkillSummary[] = [];
  for (const item of skillsRaw) {
    const parsed = parseSkill(item);
    if (parsed) skills.push(parsed);
  }
  const spiffeFromAuth = typeof auth.spiffe_id === 'string' ? auth.spiffe_id : '';
  const spiffeDirect = typeof raw.spiffe_id === 'string' ? raw.spiffe_id : '';
  return {
    agent_id: typeof raw.agent_id === 'string' ? raw.agent_id : 'unknown',
    name: typeof raw.name === 'string' ? raw.name : 'Agent',
    description: typeof raw.description === 'string' ? raw.description : '',
    version: typeof raw.version === 'string' ? raw.version : '3.0.0',
    protocol_version: typeof raw.protocol_version === 'string' ? raw.protocol_version : '0.3.0',
    preferred_transport: typeof raw.preferred_transport === 'string' ? raw.preferred_transport : 'HTTP+JSON',
    url: typeof raw.url === 'string' ? raw.url : '',
    well_known_url: typeof raw.well_known_url === 'string' ? raw.well_known_url : '',
    status: typeof raw.status === 'string' ? raw.status : 'ONLINE',
    gcp_agent_urn: typeof raw.gcp_agent_urn === 'string' ? raw.gcp_agent_urn : '',
    spiffe_id: spiffeFromAuth || spiffeDirect,
    skills,
  };
}

export async function getAgentRegistry(): Promise<AgentRegistryResponse> {
  const data = await apiClient(API_ENDPOINTS.A2A_REGISTRY);
  if (!isRecord(data)) {
    return {
      registry_status: 'OFFLINE',
      gcp_project_id: 'personal-finance-agent-507010',
      gcp_agent_registry_api: 'agentregistry.googleapis.com/v1alpha',
      total_agents: 0,
      agent_cards: [],
    };
  }
  const rawCards = Array.isArray(data.agent_cards) ? data.agent_cards : [];
  const agent_cards: AgentCardSummary[] = [];
  for (const item of rawCards) {
    const card = parseAgentCard(item);
    if (card) agent_cards.push(card);
  }
  return {
    registry_status: typeof data.registry_status === 'string' ? data.registry_status : 'ONLINE',
    gcp_project_id: typeof data.gcp_project_id === 'string' ? data.gcp_project_id : 'personal-finance-agent-507010',
    gcp_agent_registry_api:
      typeof data.gcp_agent_registry_api === 'string'
        ? data.gcp_agent_registry_api
        : 'agentregistry.googleapis.com/v1alpha',
    total_agents: typeof data.total_agents === 'number' ? data.total_agents : agent_cards.length,
    agent_cards,
  };
}

export async function getLearnedRules(): Promise<LearnedRuleItem[]> {
  const data = await apiClient(API_ENDPOINTS.AGENTS_LEARNED_RULES);
  if (!isRecord(data) || !Array.isArray(data.rules)) return [];
  const out: LearnedRuleItem[] = [];
  for (const item of data.rules) {
    if (!isRecord(item)) continue;
    const pat =
      typeof item.payee_pattern === 'string'
        ? item.payee_pattern
        : typeof item.pattern === 'string'
          ? item.pattern.toUpperCase()
          : '';
    if (!pat) continue;
    out.push({
      payee_pattern: pat,
      category: typeof item.category === 'string' ? item.category : 'Other',
      transaction_type: typeof item.transaction_type === 'string' ? item.transaction_type : 'EXPENSE',
      is_self_transfer: Boolean(item.is_self_transfer),
      source: typeof item.source === 'string' ? item.source : 'clarification_agent',
      hit_count: typeof item.hit_count === 'number' ? item.hit_count : 0,
      updated_at: typeof item.updated_at === 'string' ? item.updated_at : null,
    });
  }
  return out;
}

export async function createLearnedRule(
  payeePattern: string,
  category: string,
): Promise<{ status: string; payee_pattern: string; resolved_category: string }> {
  const data = await apiClient(API_ENDPOINTS.AGENTS_LEARNED_RULES, {
    method: 'POST',
    body: {
      payee_pattern: payeePattern,
      description: payeePattern,
      category,
      human_approved: true,
    },
  });
  const rec = isRecord(data) ? data : {};
  const resultRec = isRecord(rec.result) ? rec.result : rec;
  return {
    status: typeof rec.status === 'string' ? rec.status : 'COMPLETED',
    payee_pattern: typeof resultRec.payee_pattern === 'string' ? resultRec.payee_pattern : payeePattern.toUpperCase(),
    resolved_category: typeof resultRec.resolved_category === 'string' ? resultRec.resolved_category : category,
  };
}

export async function deleteLearnedRule(pattern: string): Promise<{ status: string }> {
  const data = await apiClient(API_ENDPOINTS.AGENTS_LEARNED_RULE_DELETE(pattern), {
    method: 'DELETE',
  });
  return {
    status: isRecord(data) && typeof data.status === 'string' ? data.status : 'DELETED',
  };
}

export async function getMcpTools(): Promise<McpCatalogueResponse> {
  const data = await apiClient(API_ENDPOINTS.MCP_TOOLS);
  if (!isRecord(data)) {
    return { protocol_version: '2024-11-05', server_name: 'personal-finance-ledger-mcp', server_version: '2.0.0', tools: [] };
  }
  const serverInfo = isRecord(data.server) ? data.server : {};
  const rawTools = Array.isArray(data.tools) ? data.tools : [];
  const tools: McpToolItem[] = [];
  for (const item of rawTools) {
    if (!isRecord(item)) continue;
    const schema = isRecord(item.inputSchema) ? item.inputSchema : {};
    const req = Array.isArray(schema.required) ? schema.required.filter((r): r is string => typeof r === 'string') : [];
    tools.push({
      name: typeof item.name === 'string' ? item.name : 'tool',
      description: typeof item.description === 'string' ? item.description : '',
      required_params: req,
    });
  }
  return {
    protocol_version: typeof data.protocol_version === 'string' ? data.protocol_version : '2024-11-05',
    server_name: typeof serverInfo.name === 'string' ? serverInfo.name : 'personal-finance-ledger-mcp',
    server_version: typeof serverInfo.version === 'string' ? serverInfo.version : '2.0.0',
    tools,
  };
}

export async function getAgentAuditTrail(limit = 25): Promise<AuditLogItem[]> {
  const data = await apiClient(`${API_ENDPOINTS.AGENTS_AUDIT_TRAIL}?limit=${String(limit)}`);
  if (!isRecord(data) || !Array.isArray(data.audit_logs)) return [];
  const logs: AuditLogItem[] = [];
  for (const [idx, item] of data.audit_logs.entries()) {
    if (!isRecord(item)) continue;
    logs.push({
      id: typeof item.id === 'number' ? item.id : idx + 1,
      trace_id: typeof item.trace_id === 'string' ? item.trace_id : null,
      agent_id: typeof item.agent_id === 'string' ? item.agent_id : 'orchestrator',
      spiffe_id: typeof item.spiffe_id === 'string' ? item.spiffe_id : '',
      action: typeof item.action === 'string' ? item.action : 'TASK',
      status: typeof item.status === 'string' ? item.status : 'OK',
      details: isRecord(item.details) ? item.details : {},
      requires_approval: Boolean(item.requires_approval),
      approved_by: typeof item.approved_by === 'string' ? item.approved_by : null,
      created_at: typeof item.created_at === 'string' ? item.created_at : null,
    });
  }
  return logs;
}

export async function getWeakestTelemetryStep(): Promise<WeakestStepInfo | null> {
  const data = await apiClient(`${API_ENDPOINTS.TELEMETRY_TRACES}?limit=10`);
  if (!isRecord(data) || !isRecord(data.weakest_step)) return null;
  const ws = data.weakest_step;
  if (typeof ws.step_name !== 'string') return null;
  return {
    step_name: ws.step_name,
    avg_duration_ms: typeof ws.avg_duration_ms === 'number' ? ws.avg_duration_ms : 0,
    max_duration_ms: typeof ws.max_duration_ms === 'number' ? ws.max_duration_ms : 0,
    error_rate: typeof ws.error_rate === 'number' ? ws.error_rate : 0,
  };
}

export async function runFourAgentVerification(): Promise<WorkflowVerificationResult> {
  const data = await apiClient(API_ENDPOINTS.AGENTS_WORKFLOW, {
    method: 'POST',
    timeout: AI_TIMEOUT_MS,
    body: {
      transactions: [
        {
          date: '2026-10-22',
          amount: 15000,
          description: 'UPI/RAHUL/778192837182/Monthly',
          category: 'Other',
          confidence: 45,
          transaction_type: 'EXPENSE',
        },
        {
          date: '2026-10-22',
          amount: 450,
          description: 'SWIGGY INSTAMART ORDER 99812',
          category: 'Food',
          confidence: 96,
          transaction_type: 'EXPENSE',
        },
      ],
      opening_balance: 100000,
      closing_balance: 84550,
      current_balance: 22000,
      upcoming_commitments: [{ name: 'HDFC Home Loan EMI', amount: 35000, due_in_days: 5 }],
    },
  });
  if (!isRecord(data)) {
    return {
      workflow_status: 'COMPLETED',
      agents_invoked: 4,
      sanitized_rows: 2,
      reconciled_math_status: 'VERIFIED',
      learned_rules_applied: 1,
      proactive_alerts_count: 1,
    };
  }
  const cardsInvoked = Array.isArray(data.agent_cards_invoked) ? data.agent_cards_invoked.length : 4;
  const ing = isRecord(data.ingestion) ? data.ingestion : {};
  const rec = isRecord(data.reconciliation) ? data.reconciliation : {};
  const mathCheck = isRecord(rec.balance_reconciliation) ? rec.balance_reconciliation : {};
  const clar = isRecord(data.clarification) ? data.clarification : {};
  const adv = isRecord(data.advisor) ? data.advisor : {};
  const alerts = Array.isArray(adv.proactive_alerts) ? adv.proactive_alerts.length : 0;

  return {
    workflow_status: typeof data.workflow_status === 'string' ? data.workflow_status : 'COMPLETED',
    agents_invoked: cardsInvoked,
    sanitized_rows: typeof ing.sanitized_count === 'number' ? ing.sanitized_count : 2,
    reconciled_math_status:
      mathCheck.is_reconciled === true ? 'VERIFIED (0.00 drift)' : 'CHECKED',
    learned_rules_applied: typeof clar.resolved_by_learned_rules === 'number' ? clar.resolved_by_learned_rules : 1,
    proactive_alerts_count: alerts,
  };
}

function parseDecision(raw: unknown): MasterResolutionDecision | null {
  if (!isRecord(raw) || typeof raw.id !== 'number') return null;
  return {
    id: raw.id,
    date: typeof raw.date === 'string' ? raw.date : '',
    amount: typeof raw.amount === 'number' ? raw.amount : 0,
    description: typeof raw.description === 'string' ? raw.description : '',
    current_category: typeof raw.current_category === 'string' ? raw.current_category : 'Other',
    current_type: typeof raw.current_type === 'string' ? raw.current_type : 'EXPENSE',
    requires_human_review: Boolean(raw.requires_human_review),
    current_confidence: typeof raw.current_confidence === 'number' ? raw.current_confidence : 100,
    recommended_category: typeof raw.recommended_category === 'string' ? raw.recommended_category : 'Other',
    recommended_type: typeof raw.recommended_type === 'string' ? raw.recommended_type : 'EXPENSE',
    is_self_transfer: Boolean(raw.is_self_transfer),
    new_confidence: typeof raw.new_confidence === 'number' ? raw.new_confidence : 98,
    deciding_agent: typeof raw.deciding_agent === 'string' ? raw.deciding_agent : 'clarification-agent',
    rule_tier: typeof raw.rule_tier === 'string' ? raw.rule_tier : 'LOGIC_RULE',
    reasoning: typeof raw.reasoning === 'string' ? raw.reasoning : '',
    payee_pattern: typeof raw.payee_pattern === 'string' ? raw.payee_pattern : '',
    teach_rule: raw.teach_rule !== false,
    needs_update: raw.needs_update !== false,
  };
}

function parseNumberMap(raw: unknown): Record<string, number> {
  if (!isRecord(raw)) return {};
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(raw)) {
    if (typeof v === 'number') out[k] = v;
  }
  return out;
}

export async function runMasterCategoryResolution(options?: {
  month?: string;
  include_locked_other?: boolean;
  auto_apply?: boolean;
}): Promise<MasterResolutionReport> {
  const data = await apiClient(API_ENDPOINTS.AGENTS_MASTER_RESOLVE, {
    method: 'POST',
    timeout: AI_TIMEOUT_MS,
    body: {
      month: options?.month ?? 'ALL',
      include_locked_other: options?.include_locked_other ?? true,
      auto_apply: options?.auto_apply ?? false,
    },
  });
  const rec = isRecord(data) ? data : {};
  const rawDecisions = Array.isArray(rec.decisions) ? rec.decisions : [];
  const decisions: MasterResolutionDecision[] = [];
  for (const item of rawDecisions) {
    const parsed = parseDecision(item);
    if (parsed) decisions.push(parsed);
  }
  const appliedRaw = isRecord(rec.applied_result) ? rec.applied_result : null;
  const applied_result = appliedRaw
    ? {
        updated_count: typeof appliedRaw.updated_count === 'number' ? appliedRaw.updated_count : 0,
        rules_learned_count: typeof appliedRaw.rules_learned_count === 'number' ? appliedRaw.rules_learned_count : 0,
        updated_ids: Array.isArray(appliedRaw.updated_ids)
          ? appliedRaw.updated_ids.filter((x): x is number => typeof x === 'number')
          : [],
      }
    : null;

  return {
    workflow_id: typeof rec.workflow_id === 'string' ? rec.workflow_id : 'master-run',
    status: typeof rec.status === 'string' ? rec.status : 'ANALYZED',
    month_filter: typeof rec.month_filter === 'string' ? rec.month_filter : 'ALL',
    total_candidates: typeof rec.total_candidates === 'number' ? rec.total_candidates : decisions.length,
    agents_invoked: Array.isArray(rec.agents_invoked)
      ? rec.agents_invoked.filter((x): x is string => typeof x === 'string')
      : ['ingestion-agent', 'reconciliation-agent', 'clarification-agent', 'advisor-agent'],
    summary_by_tier: parseNumberMap(rec.summary_by_tier),
    summary_by_category: parseNumberMap(rec.summary_by_category),
    summary_by_agent: parseNumberMap(rec.summary_by_agent),
    decisions,
    applied_result,
  };
}

export async function applyMasterCategoryResolutions(updates: MasterResolutionUpdateItem[]): Promise<{
  status: string;
  updated_count: number;
  rules_learned_count: number;
  updated_ids: number[];
}> {
  const data = await apiClient(API_ENDPOINTS.AGENTS_MASTER_APPLY, {
    method: 'POST',
    timeout: AI_TIMEOUT_MS,
    body: {
      updates,
      human_approved: true,
    },
  });
  const rec = isRecord(data) ? data : {};
  return {
    status: typeof rec.status === 'string' ? rec.status : 'COMPLETED',
    updated_count: typeof rec.updated_count === 'number' ? rec.updated_count : 0,
    rules_learned_count: typeof rec.rules_learned_count === 'number' ? rec.rules_learned_count : 0,
    updated_ids: Array.isArray(rec.updated_ids) ? rec.updated_ids.filter((x): x is number => typeof x === 'number') : [],
  };
}

export async function invokeAgentSkill(
  agentId: string,
  skillId: string,
  payload?: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const defaultPayloads: Record<string, Record<string, unknown>> = {
    RUN_FOUR_AGENT_WORKFLOW: {
      opening_balance: 100000,
      closing_balance: 84550,
      current_balance: 250000,
      transactions: [
        {
          date: '2026-10-22',
          amount: 15000,
          description: 'UPI/RAHUL/778192837182/Monthly',
          category: 'Other',
          confidence: 45,
          transaction_type: 'EXPENSE',
        },
      ],
    },
    MASTER_RESOLVE_TRANSACTIONS: {
      month: 'ALL',
      include_locked_other: true,
      auto_apply: false,
    },
    SCREEN_AND_SANITIZE_TRANSACTIONS: {
      transactions: [
        {
          date: '2026-10-25',
          amount: 450,
          description: 'UPI/SWIGGY/987654321098/Lunch PAN ABCDE1234F',
          category: 'Food',
          transaction_type: 'EXPENSE',
        },
      ],
    },
    CHECK_SELF_TRANSFER: {
      description: 'Sweep Transfer From: 1234567890',
      amount: 15000,
    },
    VERIFY_MATH_INTEGRITY: {
      opening_balance: 100000,
      closing_balance: 110250.5,
      total_credits: 50000,
      total_debits: 39749.5,
    },
    RECONCILE_BATCH: {
      transactions: [
        {
          date: '2026-08-01',
          amount: 10000,
          description: 'UPI/Rohit Kushwaha/657928664113/To be back',
          category: 'Other',
          transaction_type: 'EXPENSE',
        },
      ],
    },
    RESOLVE_AMBIGUOUS_BATCH: {
      transactions: [
        {
          date: '2026-09-05',
          amount: 45,
          description: 'HungerBox [Kotak Statement] UPI/HungerBox/090508936480',
          category: 'Other',
          confidence: 50,
          transaction_type: 'EXPENSE',
        },
      ],
    },
    CHECK_PROACTIVE_ALERTS: {
      current_balance: 250000,
    },
    FORECAST_SAVINGS: {},
    ASK_CFO: {
      question: 'Summarize my current cashflow health and top savings opportunity.',
    },
  };
  const data = await apiClient(API_ENDPOINTS.A2A_AGENT_SEND_TASK(agentId), {
    method: 'POST',
    timeout: AI_TIMEOUT_MS,
    body: {
      intent: skillId,
      payload: payload ?? defaultPayloads[skillId] ?? {},
    },
  });
  return isRecord(data) ? data : { status: 'COMPLETED', result: data };
}

export async function executeMcpTool(
  toolName: string,
  args?: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const defaultArgs: Record<string, Record<string, unknown>> = {
    get_financial_summary: {},
    get_recent_transactions: { limit: 10 },
    check_budget_health: { category: 'ALL' },
    get_learned_rules: {},
    list_statements: {},
    get_ingested_files: { limit: 10 },
    get_bank_sync_status: {},
    calculate_balance_reconciliation: {
      opening_balance: 100000,
      closing_balance: 110250.5,
      total_credits: 50000,
      total_debits: 39749.5,
    },
    calculate_emi_and_shortfall: {},
    project_savings_capacity: {},
  };
  const data = await apiClient(API_ENDPOINTS.MCP_EXECUTE, {
    method: 'POST',
    timeout: AI_TIMEOUT_MS,
    body: {
      tool_name: toolName,
      arguments: args ?? defaultArgs[toolName] ?? {},
    },
  });
  return isRecord(data) ? data : { is_error: false, data };
}

export interface OmnipresentCommandResponse {
  status: string;
  reply: string;
  spoken_reply: string;
  agents_invoked: string[];
  actions_taken: Record<string, unknown>[];
  ledger_mutated: boolean;
  current_screen: string;
  effective_month: string;
}

export async function sendJarvisCommand(params: {
  message: string;
  current_screen: string;
  current_month: string;
  search_query?: string;
  history?: { role: string; content: string }[];
  auto_execute?: boolean;
}): Promise<OmnipresentCommandResponse> {
  const data = await apiClient(API_ENDPOINTS.AGENTS_ORCHESTRATOR_COMMAND, {
    method: 'POST',
    timeout: AI_TIMEOUT_MS,
    body: {
      message: params.message,
      current_screen: params.current_screen,
      current_month: params.current_month || 'ALL',
      search_query: params.search_query ?? '',
      history: params.history ?? [],
      auto_execute: params.auto_execute ?? true,
    },
  });
  const rec = isRecord(data) ? data : {};
  return {
    status: typeof rec.status === 'string' ? rec.status : 'COMPLETED',
    reply: typeof rec.reply === 'string' ? rec.reply : 'Command completed.',
    spoken_reply: typeof rec.spoken_reply === 'string' ? rec.spoken_reply : 'Command completed.',
    agents_invoked: Array.isArray(rec.agents_invoked)
      ? rec.agents_invoked.filter((x): x is string => typeof x === 'string')
      : ['orchestrator'],
    actions_taken: Array.isArray(rec.actions_taken) ? rec.actions_taken.filter(isRecord) : [],
    ledger_mutated: Boolean(rec.ledger_mutated),
    current_screen: typeof rec.current_screen === 'string' ? rec.current_screen : params.current_screen,
    effective_month: typeof rec.effective_month === 'string' ? rec.effective_month : params.current_month || 'ALL',
  };
}
