/**
 * API paths and timeouts. The base URL is not configured here: it comes from the signed-in account
 * (see src/config/backends.ts and setApiSession in client.ts).
 */

type Id = number | string;

export const API_ENDPOINTS = {
  ME: '/me',
  UPLOAD: '/upload',
  INSIGHTS: '/insights',
  INSIGHTS_CHAT: '/insights/chat',
  TRANSACTIONS: '/transactions',
  TRANSACTIONS_MONTHS: '/transactions/months',
  TRANSACTION_BY_ID: (id: Id) => `/transactions/${encodeURIComponent(id)}`,
  TRANSACTION_CLARIFICATION: (id: Id) => `/transactions/${encodeURIComponent(id)}/clarification`,
  TRANSACTION_CLARIFICATION_AUDIO: (id: Id) => `/transactions/${encodeURIComponent(id)}/clarification-audio`,
  TRANSACTION_RESOLVE: (id: Id) => `/transactions/${encodeURIComponent(id)}/resolve`,
  ANALYTICS_SUMMARY: '/analytics/summary',
  RECURRING: '/recurring',
  FORECAST: '/forecast',
  BUDGETS: '/budgets',
  GMAIL_SETTINGS: '/api/gmail/settings',
  GMAIL_SYNC: '/api/gmail/sync',
  GMAIL_STATEMENTS: '/api/gmail/statements',
  GMAIL_STATEMENT_DOWNLOAD: (id: Id) => `/api/gmail/statements/${encodeURIComponent(id)}/download`,
  GMAIL_STATEMENT_UNLOCK: (id: Id) => `/api/gmail/statements/${encodeURIComponent(id)}/unlock`,
  A2A_REGISTRY: '/a2a/registry',
  A2A_SYNC_GCP: '/a2a/registry/sync-gcp',
  A2A_AGENT_SEND_TASK: (agentId: Id) => `/a2a/agents/${encodeURIComponent(agentId)}/tasks/send`,
  AGENTS_WORKFLOW: '/agents/workflow',
  AGENTS_MASTER_RESOLVE: '/agents/master-resolve',
  AGENTS_MASTER_APPLY: '/agents/master-resolve/apply',
  AGENTS_ORCHESTRATOR_COMMAND: '/agents/orchestrator/command',
  AGENTS_LEARNED_RULES: '/agents/learned-rules',
  AGENTS_LEARNED_RULE_DELETE: (pattern: string) => `/agents/learned-rules?pattern=${encodeURIComponent(pattern)}`,
  AGENTS_AUDIT_TRAIL: '/agents/audit-trail',
  MCP_TOOLS: '/mcp/tools',
  MCP_EXECUTE: '/mcp/execute',
  TELEMETRY_TRACES: '/telemetry/traces',
} as const;

/** Default request timeout (AI-backed endpoints pass longer ones). */
export const DEFAULT_TIMEOUT_MS = 60_000;
export const AI_TIMEOUT_MS = 90_000;
export const LONG_TIMEOUT_MS = 180_000;
/**
 * Statement imports (upload, Unlock & Sync, Gmail sync): the backend reads a long PDF a few pages
 * per AI call and reconciles every row, which can take several minutes (DEC-060). Cloud Run
 * allows 15 minutes per request.
 */
export const IMPORT_TIMEOUT_MS = 600_000;
