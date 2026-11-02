/**
 * Types for the legacy screens (src/components/*.js), which are not type-checked.
 * Only the surface the React shell uses is declared.
 */

export type LedgerStage = 'home' | 'years' | 'months' | 'ledger';

export interface LedgerStageState {
  year: string | null;
  month: string;
  /** Raw search text as typed. */
  search: string;
}

export interface LedgerOptions {
  /** Where the ledger starts (from the URL). Defaults to the 'home' overview. */
  initialState?: { stage?: LedgerStage; year?: string | null; month?: string; search?: string };
  /** false: keep 'ALL' instead of jumping to the latest month when months load. */
  autoSelectLatestMonth?: boolean;
  onMonthChange?: (month: string) => void;
  onNavigatePage?: (pageId: string) => void;
  onStageChange?: (stage: LedgerStage, state: LedgerStageState) => void;
  onSearchChange?: (query: string) => void;
}

export interface LedgerInstance {
  readonly drillStage: LedgerStage;
  readonly selectedYear: string | null;
  readonly selectedMonth: string;
  /** Lower-cased, trimmed search used for filtering. */
  readonly searchQuery: string;
  initData(): Promise<void>;
  setMonth(month: string): void;
  navigateToStage(
    stage: LedgerStage,
    state?: { year?: string; month?: string; search?: string; onlyReview?: boolean },
  ): void;
}

export interface MonthSyncedOptions {
  initialMonth?: string;
  autoSelectLatestMonth?: boolean;
  onMonthChange?: (month: string) => void;
}

export interface MonthSyncedInstance {
  readonly selectedMonth: string;
  initData(): Promise<void>;
  setMonth(month: string): void;
}

export interface RecurringInstance {
  loadRecurring(): Promise<void>;
}

export interface BankSyncInstance {
  refresh(): Promise<void>;
}

export interface LegacyModal {
  close(): void;
}

export function initLedgerComponent(container: HTMLElement, options?: LedgerOptions): LedgerInstance;
export function initAnalyticsComponent(container: HTMLElement, options?: MonthSyncedOptions): MonthSyncedInstance;
export function initBudgetsComponent(container: HTMLElement, options?: MonthSyncedOptions): MonthSyncedInstance;
export function initRecurringComponent(container: HTMLElement): RecurringInstance;
export function initUploadComponent(container: HTMLElement, options?: { onUploadSuccess?: () => void }): void;
export function initInsightsComponent(container: HTMLElement): void;
export function initBankSyncComponent(
  container: HTMLElement,
  options?: { onSyncSuccess?: (result: unknown) => void },
): BankSyncInstance;

export const editModal: LegacyModal;
export const addTransactionModal: LegacyModal;
export const budgetModal: LegacyModal;
export const voiceClarificationModal: LegacyModal;
