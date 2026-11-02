/**
 * In-browser mock of the demo backend (page.route on the demo Cloud Run URL), for the cases a real
 * backend can't produce on demand: 401/403/429, XSS payloads, Gmail-enabled /me.
 * Sign-in itself is always the real Firebase Auth.
 */
import type { Page, Request, Route } from '@playwright/test';
import { DEMO_BACKEND } from './helpers';

/** Strings that would execute if any screen rendered data as HTML. */
export const XSS = {
  img: '<img src=x onerror="window.__xss=(window.__xss||0)+1">',
  script: '<script>window.__xss=(window.__xss||0)+1</script>',
  svg: '<svg onload="window.__xss=(window.__xss||0)+1"></svg>',
  link: '<a href="javascript:window.__xss=1">click</a>',
};

export interface RecordedRequest {
  method: string;
  path: string;
  authorization: string;
}

type Handler = (route: Route, request: Request) => Promise<void>;

export interface MockOptions {
  me?: Record<string, unknown>;
  /** Replaces the default handler for "METHOD /path" (path without query). */
  handlers?: Record<string, Handler>;
}

export const DEMO_ME = {
  email: 'demo-visitor@example.com',
  uid: 'demo-uid',
  provider: 'password',
  environment: 'demo',
  features: { gmail_sync: false, uploads: true, ai: true },
  ai_limits: { insights: { limit: 5, used: 1, remaining: 4 }, chat: { limit: 20, used: 0, remaining: 20 } },
  data_resets_daily: true,
};

const MONTHS = {
  months: [
    { month: '2026-10', label: 'October 2026', count: 3, income: 85000, expense: 2300, net: 82700, review_count: 1 },
    { month: '2026-09', label: `September 2026 ${XSS.img}`, count: 2, income: 85000, expense: 30500, net: 54500, review_count: 0 },
  ],
  all: { count: 5, income: 170000, expense: 32800, net: 137200, review_count: 1, years_count: 1 },
};

const TRANSACTIONS = [
  { id: 1, date: '2026-10-15', amount: 85000, description: 'Salary credit ACME', category: 'Paycheck', type: 'INCOME', confidence: 99, requires_human_review: false },
  { id: 2, date: '2026-10-12', amount: 450, description: `Swiggy ${XSS.img}`, category: 'Food', type: 'EXPENSE', confidence: 97, requires_human_review: false },
  { id: 3, date: '2026-10-10', amount: 1850, description: `UPI ${XSS.script}`, category: `Other ${XSS.svg}`, type: 'EXPENSE', confidence: 40, requires_human_review: true },
  { id: 4, date: '2026-09-05', amount: 30000, description: 'Rent September', category: 'Rent', type: 'EXPENSE', confidence: 98, requires_human_review: false },
  { id: 5, date: '2026-09-01', amount: 85000, description: 'Salary credit ACME', category: 'Paycheck', type: 'INCOME', confidence: 99, requires_human_review: false },
];

const ANALYTICS = {
  kpis: {
    total_income: 170000,
    total_expenses: 32800,
    net_savings: 137200,
    savings_rate_pct: 80.7,
    health_score: 82,
    health_status: 'Healthy',
    transaction_count: 5,
  },
  expense_categories: [
    { category: 'Rent', total: 30000, percentage: 91.5, count: 1 },
    { category: `Food ${XSS.img}`, total: 2800, percentage: 8.5, count: 2 },
  ],
  income_categories: [{ category: 'Paycheck', total: 170000, percentage: 100, count: 2 }],
  monthly_trends: [
    { label: 'September 2026', income: 85000, expense: 30500, savings: 54500 },
    { label: 'October 2026', income: 85000, expense: 2300, savings: 82700 },
  ],
};

const BUDGETS = {
  categories: [
    { category: 'Food', budget: 8000, spent: 2800, remaining: 5200, utilization_pct: 35, status: 'ON_TRACK' },
    { category: 'Rent', budget: 30000, spent: 30000, remaining: 0, utilization_pct: 100, status: 'OVER_BUDGET' },
  ],
  total_budget: 38000,
  total_spent: 32800,
  overall_utilization_pct: 86.3,
};

const RECURRING = { total_recurring_items: 0, estimated_monthly_burn: 0, items: [] };

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: { 'Access-Control-Allow-Origin': '*' },
    body: JSON.stringify(body),
  });
}

export async function mockBackend(page: Page, options: MockOptions = {}): Promise<RecordedRequest[]> {
  const requests: RecordedRequest[] = [];
  const me = { ...DEMO_ME, ...options.me };

  const defaults: Record<string, Handler> = {
    'GET /me': (route) => json(route, 200, me),
    'GET /transactions/months': (route) => json(route, 200, MONTHS),
    'GET /transactions': (route, request) => {
      const month = new URL(request.url()).searchParams.get('month');
      return json(route, 200, month ? TRANSACTIONS.filter((tx) => tx.date.startsWith(month)) : TRANSACTIONS);
    },
    'GET /analytics/summary': (route) => json(route, 200, ANALYTICS),
    'GET /budgets': (route) => json(route, 200, BUDGETS),
    'GET /recurring': (route) => json(route, 200, RECURRING),
    'GET /forecast': (route) => json(route, 404, { detail: 'Not available in the mock.' }),
    'GET /insights': (route) =>
      json(route, 200, {
        insights: `## Summary\nYou saved **80%**.\n\n${XSS.img}\n${XSS.script}\n${XSS.link}\n<iframe src="https://example.com"></iframe>`,
      }),
    'GET /a2a/registry': (route) =>
      json(route, 200, {
        registry_status: 'ONLINE',
        gcp_project_id: 'personal-finance-agent-507010',
        gcp_agent_registry_api: 'agentregistry.googleapis.com/v1alpha',
        total_agents: 5,
        agent_cards: [],
      }),
    'GET /agents/learned-rules': (route) => json(route, 200, { rules: [] }),
    'GET /agents/audit-trail': (route) => json(route, 200, { audit_logs: [] }),
    'GET /mcp/tools': (route) =>
      json(route, 200, {
        protocol_version: '2024-11-05',
        server: { name: 'personal-finance-ledger-mcp', version: '2.0.0' },
        tools: [],
      }),
    'GET /telemetry/traces': (route) => json(route, 200, { weakest_step: null, traces: [] }),
  };

  await page.route(`${DEMO_BACKEND}/**`, async (route, request) => {
    const url = new URL(request.url());
    if (request.method() === 'OPTIONS') {
      await route.fulfill({
        status: 204,
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Headers': 'authorization, content-type, accept',
          'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
        },
      });
      return;
    }
    requests.push({ method: request.method(), path: url.pathname, authorization: request.headers().authorization ?? '' });
    const key = `${request.method()} ${url.pathname}`;
    const handler = options.handlers?.[key] ?? defaults[key];
    if (handler) await handler(route, request);
    else await json(route, 404, { detail: `No mock for ${key}` });
  });

  return requests;
}
