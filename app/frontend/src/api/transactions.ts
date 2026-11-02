/**
 * Transactions ledger, analytics, budgets, recurring/forecast and voice-clarification API calls.
 * Return shapes are unchanged from the previous JS module because the legacy screens consume them.
 */
import { apiClient, apiFetchBlob } from './client';
import { API_ENDPOINTS } from './config';

type Id = number | string;

export interface Transaction {
  id: Id;
  date: string;
  amount: number;
  description: string;
  category: string;
  type: string;
  confidence?: number;
  requires_human_review?: boolean;
  is_self_transfer?: boolean;
}

export interface TransactionQuery {
  /** 'YYYY-MM' or 'ALL' */
  month?: string;
  orderBy?: string;
  orderDir?: string;
  type?: string;
  category?: string;
  requiresReview?: boolean | null;
}

export interface MonthSummary {
  month: string;
  label: string;
  count: number;
  income: number;
  expense: number;
  net: number;
  opening_balance?: number;
  closing_balance?: number;
  reconciled?: boolean;
  bank_breakdown?: Record<string, unknown> | null;
  review_count?: number;
}

export interface MonthsResponse {
  months: MonthSummary[];
  years?: unknown[];
  all: Record<string, unknown> | null;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const monthQuery = (month?: string) => (month && month !== 'ALL' ? `?month=${encodeURIComponent(month)}` : '');

export async function getTransactions(params: TransactionQuery = {}): Promise<Transaction[]> {
  const query = new URLSearchParams();
  if (params.month && params.month !== 'ALL') query.set('month', params.month);
  if (params.orderBy) query.set('order_by', params.orderBy);
  if (params.orderDir) query.set('order_dir', params.orderDir);
  if (params.type && params.type !== 'ALL') query.set('type', params.type);
  if (params.category && params.category !== 'ALL') query.set('category', params.category);
  if (params.requiresReview !== undefined && params.requiresReview !== null) {
    query.set('requires_review', params.requiresReview ? 'true' : 'false');
  }
  const qs = query.toString();
  const data = await apiClient(qs ? `${API_ENDPOINTS.TRANSACTIONS}?${qs}` : API_ENDPOINTS.TRANSACTIONS);
  if (Array.isArray(data)) return data as Transaction[];
  if (isRecord(data) && Array.isArray(data.transactions)) return data.transactions as Transaction[];
  return [];
}

export async function updateTransaction(
  id: Id | null | undefined,
  payload: { description: string; category: string; amount: number | string },
): Promise<unknown> {
  if (id === undefined || id === null) throw new Error('Transaction ID is required for update.');
  const amount = typeof payload.amount === 'number' ? payload.amount : parseFloat(payload.amount);
  return apiClient(API_ENDPOINTS.TRANSACTION_BY_ID(id), {
    method: 'PUT',
    body: { description: payload.description, category: payload.category, amount: Number.isNaN(amount) ? 0 : amount },
  });
}

export async function getTransactionMonths(): Promise<MonthsResponse> {
  const data = await apiClient(API_ENDPOINTS.TRANSACTIONS_MONTHS);
  if (isRecord(data)) {
    return {
      ...data,
      months: Array.isArray(data.months) ? (data.months as MonthSummary[]) : [],
      all: isRecord(data.all) ? data.all : null,
    };
  }
  return { months: [], all: null };
}

export async function deleteTransaction(id: Id | null | undefined): Promise<unknown> {
  if (id === undefined || id === null) throw new Error('Transaction ID is required.');
  return apiClient(API_ENDPOINTS.TRANSACTION_BY_ID(id), { method: 'DELETE' });
}

export async function createTransaction(payload: Record<string, unknown>): Promise<unknown> {
  return apiClient(API_ENDPOINTS.TRANSACTIONS, { method: 'POST', body: payload });
}

export async function getAnalyticsSummary(month = 'ALL'): Promise<unknown> {
  return apiClient(`${API_ENDPOINTS.ANALYTICS_SUMMARY}${monthQuery(month)}`);
}

export async function getRecurringTransactions(): Promise<unknown> {
  return apiClient(API_ENDPOINTS.RECURRING);
}

export async function getFutureCostForecast(): Promise<unknown> {
  return apiClient(API_ENDPOINTS.FORECAST);
}

export async function getBudgets(month = 'ALL'): Promise<unknown> {
  return apiClient(`${API_ENDPOINTS.BUDGETS}${monthQuery(month)}`);
}

export async function updateBudget(category: string, monthlyBudget: number | string): Promise<unknown> {
  const amount = typeof monthlyBudget === 'number' ? monthlyBudget : parseFloat(monthlyBudget);
  return apiClient(API_ENDPOINTS.BUDGETS, { method: 'POST', body: { category, monthly_budget: amount } });
}

export async function getTransactionClarification(id: Id): Promise<unknown> {
  return apiClient(API_ENDPOINTS.TRANSACTION_CLARIFICATION(id));
}

/**
 * Server text-to-speech audio for a clarification question, fetched with the sign-in token
 * (an <audio src> URL could not carry it). Resolves with null when the server sent no audio.
 */
export async function fetchClarificationAudio(
  id: Id,
  txMeta: { description?: string; amount?: number; category?: string } = {},
  options: { timeout?: number } = {},
): Promise<Blob | null> {
  const params = new URLSearchParams();
  if (txMeta.description !== undefined) params.set('description', txMeta.description);
  if (txMeta.amount !== undefined) params.set('amount', String(txMeta.amount));
  if (txMeta.category !== undefined) params.set('category', txMeta.category);
  const qs = params.toString();
  const path = API_ENDPOINTS.TRANSACTION_CLARIFICATION_AUDIO(id);
  const { blob, contentType } = await apiFetchBlob(qs ? `${path}?${qs}` : path, {
    timeout: options.timeout ?? 15_000,
    headers: { Accept: 'audio/*' },
  });
  return contentType.includes('audio') ? blob : null;
}

export async function resolveTransactionCategory(
  id: Id,
  { textReply, audioBlob }: { textReply?: string; audioBlob?: Blob } = {},
): Promise<unknown> {
  const formData = new FormData();
  if (audioBlob) {
    const mime = (audioBlob.type || 'audio/webm').toLowerCase();
    const ext =
      mime.includes('mp4') || mime.includes('m4a') || mime.includes('aac')
        ? 'mp4'
        : mime.includes('ogg')
          ? 'ogg'
          : mime.includes('wav')
            ? 'wav'
            : 'webm';
    formData.append('audio_file', audioBlob, `reply.${ext}`);
  }
  const text = textReply?.trim() ?? '';
  if (text) formData.append('text_reply', text);
  if (!audioBlob && !text) {
    throw new Error('Please provide either spoken audio or text input to resolve the category.');
  }
  return apiClient(API_ENDPOINTS.TRANSACTION_RESOLVE(id), { method: 'POST', body: formData });
}
