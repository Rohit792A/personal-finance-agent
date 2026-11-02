/**
 * The recorded demo's fixtures: manifest.json and the response files it names, written by
 * scripts/record_demo.py and served from `<base>/recorded/` (recorded-demo/recorded/ in the repo).
 * Files are fetched on first use and kept for the session. Nothing here runs in normal builds.
 */

export interface RecordedEntry {
  file: string;
  status: number;
}

export interface RecordedChat extends RecordedEntry {
  question: string;
}

export interface RecordedCopilot extends RecordedEntry {
  message: string;
}

export interface RecordedAgentTask extends RecordedEntry {
  agentId: string;
  intent: string;
}

export interface RecordedMcpTool extends RecordedEntry {
  tool: string;
}

export interface RecordedManifest {
  /** ISO timestamp of the recording. */
  recordedAt: string;
  /** Day the sample data ends, YYYY-MM-DD (the recording day). */
  recordedOn: string;
  /** Repository commit the recording was made from. */
  commit: string;
  /** The full ledger (GET /transactions with no limit); every transaction list is derived from it. */
  ledgerFile: string;
  /** The master resolver run with auto-apply (recorded through Jarvis's "Audit & resolve categories"). */
  masterResolveAppliedFile: string | null;
  /** 'GET /budgets?month=2026-05' -> response. */
  routes: ReadonlyMap<string, RecordedEntry>;
  chat: readonly RecordedChat[];
  copilot: readonly RecordedCopilot[];
  agentTasks: readonly RecordedAgentTask[];
  mcpTools: readonly RecordedMcpTool[];
  notRecorded: readonly { what: string; why: string }[];
}

const FIXTURES_URL = `${import.meta.env.BASE_URL}recorded/`;
const FILE_NAME = /^[a-z0-9][a-z0-9.-]*\.json$/;

let manifestPromise: Promise<RecordedManifest> | null = null;
let loadedManifest: RecordedManifest | null = null;
let fixtureCache: Map<string, Promise<unknown>> | null = null;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const text = (value: unknown): string => (typeof value === 'string' ? value : '');

function entryOf(raw: Record<string, unknown>): RecordedEntry {
  const file = text(raw.file);
  if (!FILE_NAME.test(file)) throw new Error(`The recorded demo's manifest names an invalid file: ${file || '(none)'}.`);
  return { file, status: typeof raw.status === 'number' ? raw.status : 200 };
}

function listOf<T>(value: unknown, read: (item: Record<string, unknown>) => T): T[] {
  return Array.isArray(value) ? value.filter(isRecord).map(read) : [];
}

function parseManifest(data: unknown): RecordedManifest {
  if (!isRecord(data) || data.format !== 1) throw new Error("The recorded demo's manifest has an unknown format.");
  const source = isRecord(data.source) ? data.source : {};
  const datasets = isRecord(data.datasets) ? data.datasets : {};
  const routes = new Map<string, RecordedEntry>();
  for (const item of listOf(data.routes, (raw) => ({ key: text(raw.key), entry: entryOf(raw) }))) {
    if (item.key) routes.set(item.key, item.entry);
  }
  return {
    recordedAt: text(data.recorded_at),
    recordedOn: text(data.recorded_on),
    commit: text(source.repository_commit),
    ledgerFile: entryOf({ file: datasets.ledger }).file,
    masterResolveAppliedFile: datasets.master_resolve_applied ? entryOf({ file: datasets.master_resolve_applied }).file : null,
    routes,
    chat: listOf(data.chat, (raw) => ({ ...entryOf(raw), question: text(raw.question) })).filter((c) => c.question),
    copilot: listOf(data.copilot, (raw) => ({ ...entryOf(raw), message: text(raw.message) })).filter((c) => c.message),
    agentTasks: listOf(data.agent_tasks, (raw) => ({
      ...entryOf(raw),
      agentId: text(raw.agent_id),
      intent: text(raw.intent),
    })),
    mcpTools: listOf(data.mcp_tools, (raw) => ({ ...entryOf(raw), tool: text(raw.tool) })),
    notRecorded: listOf(data.not_recorded, (raw) => ({ what: text(raw.what), why: text(raw.why) })),
  };
}

async function fetchJson(file: string): Promise<unknown> {
  const response = await fetch(`${FIXTURES_URL}${file}`, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`The recorded demo couldn't load ${file} (HTTP ${String(response.status)}).`);
  return (await response.json()) as unknown;
}

/** Loads manifest.json once; a failed load can be retried. */
export function loadManifest(): Promise<RecordedManifest> {
  manifestPromise ??= fetchJson('manifest.json').then(
    (data) => {
      loadedManifest = parseManifest(data);
      return loadedManifest;
    },
    (error: unknown) => {
      manifestPromise = null;
      throw error;
    },
  );
  return manifestPromise;
}

/** The manifest once loadManifest() has resolved (the recorded app loads it before rendering screens). */
export function recordedManifest(): RecordedManifest | null {
  return loadedManifest;
}

/** A recorded response body, fetched once per file. */
export function loadFixture(file: string): Promise<unknown> {
  fixtureCache ??= new Map();
  let pending = fixtureCache.get(file);
  if (!pending) {
    pending = fetchJson(file);
    pending.catch(() => {
      fixtureCache?.delete(file);
    });
    fixtureCache.set(file, pending);
  }
  return pending;
}

/** Lower case, punctuation and extra spaces removed: a typed question matches its recorded twin. */
export function normalizeQuestion(question: string): string {
  return question
    .toLowerCase()
    .replace(/[^\p{L}\p{N}₹]+/gu, ' ')
    .trim();
}

/** Questions the AI CFO chat can replay (its suggestion chips in the recorded demo). */
export function recordedChatQuestions(): string[] {
  return (loadedManifest?.chat ?? []).map((entry) => entry.question);
}

/** Commands the Jarvis copilot can replay, topped up with chat questions it also answers. */
export function recordedCopilotPrompts(limit = 4): string[] {
  const prompts = (loadedManifest?.copilot ?? []).map((entry) => entry.message);
  for (const question of recordedChatQuestions()) {
    if (prompts.length >= limit) break;
    prompts.push(question);
  }
  return prompts.slice(0, limit);
}
