/**
 * Page state kept in the URL:
 *   ?month=YYYY-MM   global period (absent = all time)
 *   ?q=text          transactions search
 *   ?view=overview|years|months (&year=YYYY)   the ledger's statement overview instead of the table
 */

export type LedgerView = 'ledger' | 'overview' | 'years' | 'months';

export interface UrlState {
  /** 'ALL' or 'YYYY-MM' */
  month: string;
  q: string;
  view: LedgerView;
  /** Only meaningful with view 'months'. */
  year: string | null;
}

export type UrlPatch = Partial<UrlState>;

export const ALL_TIME = 'ALL';

const MONTH_PATTERN = /^\d{4}-(?:0[1-9]|1[0-2])$/;
const YEAR_PATTERN = /^\d{4}$/;
const VIEWS: readonly LedgerView[] = ['ledger', 'overview', 'years', 'months'];

export const isMonthKey = (value: string): boolean => MONTH_PATTERN.test(value);

export function parseUrlState(search: string): UrlState {
  const params = new URLSearchParams(search);
  const month = params.get('month') ?? '';
  const view = params.get('view') ?? 'ledger';
  const year = params.get('year') ?? '';
  const parsedView = (VIEWS as readonly string[]).includes(view) ? (view as LedgerView) : 'ledger';
  return {
    month: isMonthKey(month) ? month : ALL_TIME,
    q: (params.get('q') ?? '').trim(),
    view: parsedView,
    year: parsedView === 'months' && YEAR_PATTERN.test(year) ? year : null,
  };
}

/** Canonical query string ('' or '?...'), parameters in a stable order. */
export function serializeUrlState(state: UrlState): string {
  const params = new URLSearchParams();
  if (state.month !== ALL_TIME && isMonthKey(state.month)) params.set('month', state.month);
  const q = state.q.trim();
  if (q) params.set('q', q);
  if (state.view !== 'ledger') params.set('view', state.view);
  if (state.view === 'months' && state.year && YEAR_PATTERN.test(state.year)) params.set('year', state.year);
  const text = params.toString();
  return text ? `?${text}` : '';
}

export function applyUrlPatch(state: UrlState, patch: UrlPatch): UrlState {
  return { ...state, ...patch };
}

/** Link to a screen that keeps only the global period. */
export function screenSearch(month: string): string {
  return serializeUrlState({ month, q: '', view: 'ledger', year: null });
}

/** "October 2026" for '2026-10'. */
export function formatMonthLabel(month: string): string {
  if (!isMonthKey(month)) return 'All time';
  const [year, mon] = month.split('-').map(Number);
  return new Date(Date.UTC(year ?? 1970, (mon ?? 1) - 1, 1)).toLocaleDateString('en-IN', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  });
}
