/**
 * The recorded demo's stand-in for the network: src/api/client.ts hands every request here when
 * RECORDED_DEMO is on, and nothing is ever fetched except the fixture files next to the app.
 *
 * - Reads (GET) return the response recorded from the real backend for the same path and query
 *   (manifest routes). Transaction lists are computed from the recorded ledger (ledger.ts).
 * - The AI CFO chat and the Jarvis copilot replay the recorded answer to the same question; any other
 *   question gets a short note listing the questions this demo can replay.
 * - Agent Registry actions (skills, MCP tools, the 4-agent check, the master resolver) replay their
 *   recorded responses.
 * - Writes (edits, deletes, budgets, rules, category answers, uploads) get a success answer shaped
 *   like the backend's, but nothing is stored, and a notice says so.
 * - Gmail routes answer 403 as the demo backend does; server text-to-speech answers 503, so the
 *   clarification dialog uses the browser's own speech, as it does whenever Cloud TTS is unavailable.
 */
import type { ApiRequestOptions } from '../api/client';
import { answerTransactions, loadLedger } from './ledger';
import { loadFixture, loadManifest, normalizeQuestion, type RecordedEntry, type RecordedManifest } from './manifest';
import { showRecordedNotice } from './notice';

export interface RecordedResult {
  response: Response;
  data: unknown;
}

const NOT_SAVED = 'Recorded demo: nothing was saved. This copy of the app replays responses recorded from the real backend.';
const READ_DELAY_MS = 120;
const ACTION_DELAY_MS = 450;
const AI_DELAY_MS = 900;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const text = (value: unknown): string => (typeof value === 'string' ? value : '');

function reply(status: number, data: unknown): RecordedResult {
  return { response: new Response(null, { status, headers: { 'content-type': 'application/json' } }), data };
}

async function replay(entry: RecordedEntry): Promise<RecordedResult> {
  return reply(entry.status, await loadFixture(entry.file));
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cancelled = () => new DOMException('The request was cancelled.', 'AbortError');
    if (signal?.aborted) {
      reject(cancelled());
      return;
    }
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer);
        reject(cancelled());
      },
      { once: true },
    );
  });
}

function notCaptured(method: string, path: string): RecordedResult {
  return reply(404, { detail: `This recorded demo didn't capture ${method} ${path}.` });
}

/** 'GET /budgets?month=2026-05' (query parameters sorted), as scripts/record_demo.py writes it. */
function routeKey(method: string, path: string, query: URLSearchParams, skip?: string): string {
  const pairs = [...query.entries()].filter(([name]) => name !== skip);
  pairs.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  const search = pairs.map(([name, value]) => `${name}=${value}`).join('&');
  return `${method} ${path}${search ? `?${search}` : ''}`;
}

function recordedRoute(manifest: RecordedManifest, method: string, path: string, query: URLSearchParams) {
  // A different `limit` (e.g. a longer audit trail) gets the recorded list.
  return manifest.routes.get(routeKey(method, path, query)) ?? manifest.routes.get(routeKey(method, path, query, 'limit'));
}

function questionList(questions: readonly string[]): string {
  return questions.map((question) => `- ${question}`).join('\n');
}

// --- Reads ---------------------------------------------------------------------------------------

async function answerRead(manifest: RecordedManifest, path: string, query: URLSearchParams): Promise<RecordedResult> {
  if (path === '/transactions') return reply(200, await answerTransactions(query));
  const route = recordedRoute(manifest, 'GET', path, query);
  if (route) return replay(route);
  if (/^\/transactions\/\d+\/clarification-audio$/.test(path)) {
    return reply(503, { detail: 'Cloud TTS unavailable; client should use browser speech synthesis' });
  }
  return notCaptured('GET', path);
}

// --- Replayed AI and agent actions ---------------------------------------------------------------

async function answerChat(manifest: RecordedManifest, message: string): Promise<RecordedResult> {
  const wanted = normalizeQuestion(message);
  const entry = manifest.chat.find((item) => normalizeQuestion(item.question) === wanted);
  if (entry) return replay(entry);
  return reply(200, {
    reply:
      '**This recorded demo can only replay the questions it recorded.** In the live app every question goes to ' +
      `Gemini; here, pick one of these:\n\n${questionList(manifest.chat.map((item) => item.question))}`,
  });
}

async function answerCopilot(manifest: RecordedManifest, message: string): Promise<RecordedResult> {
  const wanted = normalizeQuestion(message);
  const entry = manifest.copilot.find((item) => normalizeQuestion(item.message) === wanted);
  if (entry) {
    const recorded = await loadFixture(entry.file);
    if (isRecord(recorded) && recorded.ledger_mutated === true) {
      showRecordedNotice(NOT_SAVED);
      // The screens would only reload the same recorded data.
      return reply(entry.status, { ...recorded, ledger_mutated: false });
    }
    return reply(entry.status, recorded);
  }
  // The copilot answers through the same advisor as the AI CFO chat, so its recorded answers apply.
  const chat = manifest.chat.find((item) => normalizeQuestion(item.question) === wanted);
  const chatReply = chat ? await loadFixture(chat.file) : null;
  const known = [...manifest.copilot.map((item) => item.message), ...manifest.chat.map((item) => item.question)];
  const content =
    isRecord(chatReply) && typeof chatReply.reply === 'string'
      ? chatReply.reply
      : '**This recorded demo can only replay the commands it recorded.** In the live app Jarvis sends any ' +
        `question or command to the agents and Gemini; here, try one of these:\n\n${questionList(known)}`;
  return reply(200, {
    status: 'COMPLETED',
    reply: content,
    spoken_reply: chat ? 'Here is the recorded answer.' : 'This recorded demo can only replay the commands it recorded.',
    agents_invoked: chat ? ['orchestrator', 'advisor-agent'] : ['orchestrator'],
    actions_taken: [],
    ledger_mutated: false,
    current_screen: 'transactions',
    effective_month: 'ALL',
  });
}

async function answerAgentTask(manifest: RecordedManifest, agentId: string, intent: string): Promise<RecordedResult> {
  const entry = manifest.agentTasks.find((item) => item.agentId === agentId && item.intent === intent);
  if (entry) return replay(entry);
  const why = manifest.notRecorded.find((item) => item.what === `${agentId}:${intent}`)?.why;
  return reply(200, {
    status: 'NOT_RECORDED',
    intent,
    target_agent: agentId,
    result: { message: `This recorded demo has no response for ${intent}.${why ? ` ${why}` : ''}` },
  });
}

async function answerMcpTool(manifest: RecordedManifest, tool: string): Promise<RecordedResult> {
  const entry = manifest.mcpTools.find((item) => item.tool === tool);
  return entry ? replay(entry) : reply(400, { detail: `This recorded demo has no response for the MCP tool ${tool}.` });
}

async function answerMasterResolve(manifest: RecordedManifest, autoApply: boolean): Promise<RecordedResult> {
  if (autoApply) {
    showRecordedNotice(NOT_SAVED);
    if (manifest.masterResolveAppliedFile) {
      return reply(200, await loadFixture(manifest.masterResolveAppliedFile));
    }
    const audit = manifest.copilot.find(
      (item) => normalizeQuestion(item.message) === normalizeQuestion('Audit & resolve categories'),
    );
    if (audit) {
      const recorded = await loadFixture(audit.file);
      if (isRecord(recorded) && isRecord(recorded.master_report)) {
        return reply(200, recorded.master_report);
      }
    }
  }
  const route = manifest.routes.get('POST /agents/master-resolve');
  return route ? replay(route) : notCaptured('POST', '/agents/master-resolve');
}

// --- Writes: acknowledged, never stored ----------------------------------------------------------

/** Keywords for a typed category answer, standing in for the voice agent's resolver. */
const CATEGORY_WORDS: Record<string, string> = {
  food: 'Food', grocery: 'Food', groceries: 'Food', dining: 'Food', restaurant: 'Food', lunch: 'Food', dinner: 'Food',
  rent: 'Rent', sip: 'SIP', mutual: 'SIP', utilities: 'Utilities', utility: 'Utilities', electricity: 'Utilities',
  bill: 'Utilities', transport: 'Transportation', transportation: 'Transportation', cab: 'Transportation',
  taxi: 'Transportation', metro: 'Transportation', fuel: 'Transportation', health: 'Health/Medical',
  medical: 'Health/Medical', medicine: 'Health/Medical', doctor: 'Health/Medical', home: 'Home', personal: 'Personal',
  shopping: 'Personal', travel: 'Travel', trip: 'Travel', flight: 'Travel', hotel: 'Travel', debt: 'Debt', emi: 'Debt',
  loan: 'Debt', investment: 'Investment', stocks: 'Stocks', savings: 'Savings', salary: 'Paycheck',
  paycheck: 'Paycheck', bonus: 'Bonus', interest: 'Interest', transfer: 'Transfer', other: 'Other',
};

function categoryFromAnswer(answer: string): string {
  for (const word of answer.toLowerCase().split(/[^a-z]+/)) {
    const category = CATEGORY_WORDS[word];
    if (category) return category;
  }
  return 'Other';
}

function formField(body: unknown, name: string): FormDataEntryValue | null {
  return body instanceof FormData ? body.get(name) : null;
}

async function answerUpload(manifest: RecordedManifest, body: unknown): Promise<RecordedResult> {
  const file = formField(body, 'file');
  const name = file instanceof File ? file.name : 'your file';
  const route = manifest.routes.get('POST /upload');
  const recorded = route ? await loadFixture(route.file) : null;
  const notes =
    isRecord(recorded) && Array.isArray(recorded.notifications)
      ? recorded.notifications.filter((note): note is string => typeof note === 'string')
      : [];
  const ledger = await loadLedger();
  return reply(200, {
    status: 'success',
    message: `Recorded demo: '${name}' was not uploaded`,
    new_transactions: 0,
    total_transactions: ledger.length,
    notifications: [
      `Recorded demo: '${name}' was not uploaded or read, and nothing was saved.`,
      ...(notes.length > 0 ? ['When the app recorded this demo, a synthetic 2-page bank statement gave:', ...notes] : []),
    ],
  });
}

function answerResolve(id: string, body: unknown): RecordedResult {
  const answer = text(formField(body, 'text_reply')).trim();
  if (!answer) {
    return reply(400, { detail: "This recorded demo can't transcribe audio. Type your answer instead." });
  }
  const category = categoryFromAnswer(answer);
  showRecordedNotice(NOT_SAVED);
  return reply(200, {
    status: 'success',
    transcribed_text: answer,
    resolved_category: category,
    learned_rule: null,
    message: `Recorded demo: transaction #${id} would be set to ${category}; nothing was saved.`,
  });
}

function answerWrite(method: string, path: string, query: URLSearchParams, body: unknown): RecordedResult | null {
  const fields = isRecord(body) ? body : {};
  const transaction = /^\/transactions\/(\d+)$/.exec(path)?.[1];
  let data: unknown = null;
  if (method === 'PUT' && transaction) {
    data = { status: 'success', learned_rule: null, message: `Transaction #${transaction} updated successfully` };
  } else if (method === 'DELETE' && transaction) {
    data = { status: 'success', message: `Transaction #${transaction} deleted successfully` };
  } else if (method === 'POST' && path === '/transactions') {
    data = { status: 'success', message: 'Transaction recorded successfully', id: null };
  } else if (method === 'POST' && path === '/budgets') {
    const amount = typeof fields.monthly_budget === 'number' ? fields.monthly_budget : 0;
    const formatted = amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    data = { status: 'success', message: `Budget for ${text(fields.category)} updated to ₹${formatted}` };
  } else if (method === 'POST' && path === '/agents/master-resolve/apply') {
    const updates = Array.isArray(fields.updates) ? fields.updates.filter(isRecord) : [];
    data = {
      status: 'COMPLETED',
      updated_count: updates.length,
      rules_learned_count: updates.filter((item) => item.teach_rule === true && text(item.payee_pattern)).length,
      updated_ids: updates.map((item) => item.id).filter((id): id is number => typeof id === 'number'),
    };
  } else if (method === 'POST' && path === '/agents/learned-rules') {
    const pattern = (text(fields.payee_pattern) || text(fields.description)).toUpperCase();
    data = { status: 'COMPLETED', result: { payee_pattern: pattern, resolved_category: text(fields.category) } };
  } else if (method === 'DELETE' && path === '/agents/learned-rules') {
    data = { status: 'DELETED', pattern: (query.get('pattern') ?? '').trim().toUpperCase() };
  } else {
    return null;
  }
  showRecordedNotice(NOT_SAVED);
  return reply(200, data);
}

// --- Entry point ---------------------------------------------------------------------------------

/** Answers one API request from the recording. Never touches the network except for fixture files. */
export async function answerFromRecording(endpoint: string, options: ApiRequestOptions): Promise<RecordedResult> {
  const url = new URL(endpoint, 'https://recorded-demo.invalid');
  const path = url.pathname;
  const method = (options.method ?? 'GET').toUpperCase();
  const fields = isRecord(options.body) ? options.body : {};
  const manifest = await loadManifest();

  if (path.startsWith('/api/gmail/')) {
    await wait(READ_DELAY_MS, options.signal);
    return reply(403, { detail: 'Gmail sync is turned off in the demo.' });
  }
  if (method === 'GET') {
    await wait(path === '/insights' ? AI_DELAY_MS : READ_DELAY_MS, options.signal);
    return answerRead(manifest, path, url.searchParams);
  }

  if (method === 'POST' && path === '/insights/chat') {
    await wait(AI_DELAY_MS, options.signal);
    return answerChat(manifest, text(fields.message));
  }
  if (method === 'POST' && path === '/agents/orchestrator/command') {
    await wait(AI_DELAY_MS, options.signal);
    return answerCopilot(manifest, text(fields.message) || text(fields.command));
  }
  await wait(ACTION_DELAY_MS, options.signal);
  const task = /^\/a2a\/agents\/([^/]+)\/tasks\/send$/.exec(path)?.[1];
  if (method === 'POST' && task) {
    return answerAgentTask(manifest, decodeURIComponent(task), text(fields.intent) || text(fields.skill_id));
  }
  if (method === 'POST' && path === '/mcp/execute') return answerMcpTool(manifest, text(fields.tool_name));
  if (method === 'POST' && path === '/agents/master-resolve') return answerMasterResolve(manifest, fields.auto_apply === true);
  if (method === 'POST' && path === '/agents/workflow') {
    const route = manifest.routes.get('POST /agents/workflow');
    return route ? replay(route) : notCaptured(method, path);
  }
  if (method === 'POST' && path === '/upload') return answerUpload(manifest, options.body);
  const resolve = /^\/transactions\/(\d+)\/resolve$/.exec(path)?.[1];
  if (method === 'POST' && resolve) return answerResolve(resolve, options.body);
  return answerWrite(method, path, url.searchParams, options.body) ?? notCaptured(method, path);
}
