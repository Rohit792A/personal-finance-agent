/**
 * GET /transactions in the recorded demo: every list the Transactions and Upload screens ask for is
 * computed from the one recorded ledger (all transactions), with the backend's filter, sort and
 * limit rules (get_transactions in app/api.py):
 *   month        YYYY-MM of the date ('ALL' or absent: every month)
 *   type         TRANSFER: transfers and self-transfers; EXPENSE / INCOME: that type, transfers excluded
 *   category     category name, case-insensitive
 *   search       substring of the description or category, case-insensitive
 *   requires_review  true / false
 *   order_by, order_dir  date (default), amount, description, category, confidence, id, type, review;
 *                ties by id, newest first
 *   limit        default 1000
 * Text sorts use the browser's collation, which can order a few rows differently from PostgreSQL's.
 */
import { loadFixture, loadManifest } from './manifest';

export interface LedgerRow {
  id: number;
  date: string;
  amount: number;
  description: string;
  category: string;
  type: string;
  confidence: number;
  requires_human_review: boolean;
  is_self_transfer: boolean;
}

type SortKey = 'date' | 'amount' | 'description' | 'category' | 'confidence' | 'id' | 'type' | 'review';

const SORT_KEYS: Record<string, SortKey> = {
  date: 'date',
  amount: 'amount',
  description: 'description',
  category: 'category',
  confidence: 'confidence',
  id: 'id',
  type: 'type',
  review: 'review',
};

const DEFAULT_LIMIT = 1000;
const collator = (): Intl.Collator => new Intl.Collator('en', { sensitivity: 'base', numeric: true });

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function toRow(raw: Record<string, unknown>): LedgerRow {
  return {
    id: typeof raw.id === 'number' ? raw.id : 0,
    date: typeof raw.date === 'string' ? raw.date : '',
    amount: typeof raw.amount === 'number' ? raw.amount : 0,
    description: typeof raw.description === 'string' ? raw.description : '',
    category: typeof raw.category === 'string' ? raw.category : 'Uncategorized',
    type: typeof raw.type === 'string' ? raw.type : 'EXPENSE',
    confidence: typeof raw.confidence === 'number' ? raw.confidence : 100,
    requires_human_review: raw.requires_human_review === true,
    is_self_transfer: raw.is_self_transfer === true,
  };
}

let ledgerPromise: Promise<LedgerRow[]> | null = null;

/** Every recorded transaction (loaded once). */
export function loadLedger(): Promise<LedgerRow[]> {
  ledgerPromise ??= loadManifest()
    .then((manifest) => loadFixture(manifest.ledgerFile))
    .then((data) => {
      const rows = isRecord(data) && Array.isArray(data.transactions) ? data.transactions : [];
      return rows.filter(isRecord).map(toRow);
    })
    .catch((error: unknown) => {
      ledgerPromise = null;
      throw error;
    });
  return ledgerPromise;
}

/** FastAPI's bool parsing for query parameters; null when absent or not a boolean. */
function queryBool(value: string | null): boolean | null {
  if (value === null) return null;
  const lowered = value.trim().toLowerCase();
  if (['true', '1', 'yes', 'on'].includes(lowered)) return true;
  if (['false', '0', 'no', 'off'].includes(lowered)) return false;
  return null;
}

function compare(a: LedgerRow, b: LedgerRow, key: SortKey, words: Intl.Collator): number {
  switch (key) {
    case 'date':
      return a.date < b.date ? -1 : a.date > b.date ? 1 : 0;
    case 'amount':
      return a.amount - b.amount;
    case 'confidence':
      return a.confidence - b.confidence;
    case 'id':
      return a.id - b.id;
    case 'review':
      return Number(a.requires_human_review) - Number(b.requires_human_review);
    case 'description':
      return words.compare(a.description, b.description);
    case 'category':
      return words.compare(a.category, b.category);
    case 'type':
      return words.compare(a.type, b.type);
  }
}

/** The response GET /transactions gives for these query parameters, from the recorded ledger. */
export async function answerTransactions(query: URLSearchParams): Promise<{ transactions: LedgerRow[]; total: number }> {
  const rows = await loadLedger();
  const month = (query.get('month') ?? '').trim();
  const type = (query.get('type') ?? '').trim().toUpperCase();
  const category = (query.get('category') ?? '').trim().toLowerCase();
  const search = (query.get('search') ?? '').trim().toLowerCase();
  const review = queryBool(query.get('requires_review'));
  const parsedLimit = Number.parseInt(query.get('limit') ?? '', 10);
  const limit = Number.isFinite(parsedLimit) && parsedLimit > 0 ? parsedLimit : DEFAULT_LIMIT;
  const sortKey = SORT_KEYS[(query.get('order_by') ?? 'date').toLowerCase()] ?? 'date';
  const direction = (query.get('order_dir') ?? 'desc').toLowerCase() === 'asc' ? 1 : -1;

  const selected = rows.filter((row) => {
    if (month && month.toUpperCase() !== 'ALL' && row.date.slice(0, 7) !== month) return false;
    if (type && type !== 'ALL' && row.type.toUpperCase() !== type) return false;
    if (category && category !== 'all' && row.category.toLowerCase() !== category) return false;
    if (search && !row.description.toLowerCase().includes(search) && !row.category.toLowerCase().includes(search)) {
      return false;
    }
    if (review !== null && row.requires_human_review !== review) return false;
    return true;
  });

  const words = collator();
  selected.sort((a, b) => direction * compare(a, b, sortKey, words) || b.id - a.id);
  const page = selected.slice(0, limit);
  return { transactions: page, total: page.length };
}
