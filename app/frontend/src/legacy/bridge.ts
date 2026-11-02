/**
 * Imperative glue between the React shell and the legacy vanilla-JS screens.
 *
 * - Mounts each legacy screen once, the first time it is shown, into a persistent container
 *   (the shell only toggles visibility afterwards; screens are never re-initialised).
 * - Keeps the ledger, analytics and budgets on the URL's month, and the ledger on the URL's
 *   search and overview stage. Changes made inside a legacy screen are written back to the URL.
 * - Refreshes every mounted screen after an upload or Gmail sync, and tells the shell (dataVersion)
 *   so the period picker reloads its months.
 */
import { screenById, screenForLegacyPage, type ScreenId } from '../shell/screens';
import {
  applyUrlPatch,
  parseUrlState,
  screenSearch,
  serializeUrlState,
  type LedgerView,
  type UrlPatch,
  type UrlState,
} from '../shell/urlState';
import type * as LegacyModule from './index.js';
import type {
  BankSyncInstance,
  LedgerInstance,
  LedgerStage,
  LedgerStageState,
  MonthSyncedInstance,
  MonthSyncedOptions,
  RecurringInstance,
} from './index.js';

export type Legacy = typeof LegacyModule;

export interface BridgeNavigator {
  location: () => { pathname: string; search: string };
  navigate: (to: { pathname: string; search: string }, options: { replace: boolean }) => void;
}

const STAGE_FOR_VIEW: Record<LedgerView, LedgerStage> = {
  ledger: 'ledger',
  overview: 'home',
  years: 'years',
  months: 'months',
};

const VIEW_FOR_STAGE: Record<LedgerStage, LedgerView> = {
  ledger: 'ledger',
  home: 'overview',
  years: 'years',
  months: 'months',
};

/** Elements the legacy code appends to <body>; they need the legacy theme as well. */
const OVERLAY_IDS = [
  'edit-transaction-modal',
  'add-transaction-modal',
  'edit-budget-modal',
  'voice-clarification-modal',
  'toast-container',
];

type MonthComponent = 'ledger' | 'analytics' | 'budgets';

export const legacyContainerId = (screen: ScreenId): string => `legacy-screen-${screen}`;

export class LegacyBridge {
  private readonly navigator: BridgeNavigator;
  private legacy: Legacy | null = null;
  private ledger: LedgerInstance | null = null;
  private analytics: MonthSyncedInstance | null = null;
  private budgets: MonthSyncedInstance | null = null;
  private recurring: RecurringInstance | null = null;
  private bankSync: BankSyncInstance | null = null;
  private readonly mounted = new Set<ScreenId>();
  /** The month each month-aware screen was last set to (avoids redundant reloads). */
  private readonly componentMonth = new Map<MonthComponent, string>();
  private url: UrlState;
  private pendingPatch: UrlPatch | null = null;
  private pendingReplace = true;
  private syncing = false;
  private active = false;
  private dataVersion = 0;
  private readonly listeners = new Set<() => void>();

  constructor(navigator: BridgeNavigator, initialUrl: UrlState) {
    this.navigator = navigator;
    this.url = initialUrl;
  }

  /** Called once the legacy module has loaded. */
  attach(legacy: Legacy): void {
    this.legacy = legacy;
    for (const id of OVERLAY_IDS) document.getElementById(id)?.classList.add('legacy-scope');
  }

  activate(): void {
    this.active = true;
  }

  /** On sign-out: ignore late callbacks, close legacy dialogs and clear toasts. */
  deactivate(): void {
    this.active = false;
    this.pendingPatch = null;
    const legacy = this.legacy;
    if (legacy) {
      for (const modal of [legacy.editModal, legacy.addTransactionModal, legacy.budgetModal, legacy.voiceClarificationModal]) {
        try {
          modal.close();
        } catch {
          // already closed
        }
      }
    }
    document.getElementById('toast-container')?.replaceChildren();
  }

  /** For useSyncExternalStore: bumps whenever data changed (upload, Gmail sync). */
  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getDataVersion = (): number => this.dataVersion;

  isMounted(screen: ScreenId): boolean {
    return this.mounted.has(screen);
  }

  /** Mounts a legacy screen into its container the first time it is shown. */
  mount(screen: ScreenId, container: HTMLElement): void {
    const legacy = this.legacy;
    if (!legacy || this.mounted.has(screen)) return;
    this.mounted.add(screen);
    const url = this.url;

    switch (screen) {
      case 'transactions':
        this.ledger = legacy.initLedgerComponent(container, {
          initialState: { stage: STAGE_FOR_VIEW[url.view], year: url.year, month: url.month, search: url.q },
          autoSelectLatestMonth: false,
          onMonthChange: (month) => {
            this.onComponentMonth('ledger', month);
          },
          onStageChange: (stage, state) => {
            this.onLedgerStage(stage, state);
          },
          onSearchChange: (query) => {
            if (this.active) this.queue({ q: query }, true);
          },
          onNavigatePage: (pageId) => {
            this.openLegacyPage(pageId);
          },
        });
        this.componentMonth.set('ledger', url.month);
        break;
      case 'analytics':
        this.analytics = legacy.initAnalyticsComponent(container, this.monthOptions('analytics'));
        this.componentMonth.set('analytics', url.month);
        break;
      case 'budgets':
        this.budgets = legacy.initBudgetsComponent(container, this.monthOptions('budgets'));
        this.componentMonth.set('budgets', url.month);
        break;
      case 'forecast':
        this.recurring = legacy.initRecurringComponent(container);
        break;
      case 'upload':
        legacy.initUploadComponent(container, {
          onUploadSuccess: () => {
            this.refreshAll();
          },
        });
        break;
      case 'ai-cfo':
        legacy.initInsightsComponent(container);
        break;
      case 'bank-sync':
        this.bankSync = legacy.initBankSyncComponent(container, {
          onSyncSuccess: () => {
            this.refreshAll('bank-sync');
          },
        });
        break;
    }
  }

  /**
   * Applies the URL state to every mounted screen. Safe to call before anything is mounted.
   * The month is global; the ledger's search and overview stage only follow the URL while the
   * Transactions screen is the one being shown.
   */
  sync(url: UrlState, screen: ScreenId | null): void {
    this.url = url;
    this.syncing = true;
    try {
      this.syncMonth('analytics', this.analytics, url.month);
      this.syncMonth('budgets', this.budgets, url.month);
      if (screen === 'transactions') this.syncLedger(url);
      else this.syncMonth('ledger', this.ledger, url.month);
    } finally {
      this.syncing = false;
    }
  }

  /** Reloads every mounted screen (after uploads and Gmail syncs). */
  refreshAll(source?: ScreenId): void {
    if (!this.active) return;
    void this.ledger?.initData();
    void this.analytics?.initData();
    void this.budgets?.initData();
    void this.recurring?.loadRecurring();
    if (source !== 'bank-sync') void this.bankSync?.refresh();
    this.dataVersion += 1;
    for (const listener of this.listeners) listener();
  }

  private monthOptions(name: 'analytics' | 'budgets'): MonthSyncedOptions {
    return {
      initialMonth: this.url.month,
      autoSelectLatestMonth: false,
      onMonthChange: (month) => {
        this.onComponentMonth(name, month);
      },
    };
  }

  private syncMonth(
    name: MonthComponent,
    instance: Pick<MonthSyncedInstance, 'setMonth'> | null,
    month: string,
  ): void {
    if (!instance || this.componentMonth.get(name) === month) return;
    this.componentMonth.set(name, month);
    instance.setMonth(month);
  }

  private syncLedger(url: UrlState): void {
    const ledger = this.ledger;
    if (!ledger) return;
    const stage = STAGE_FOR_VIEW[url.view];
    const monthChanged = this.componentMonth.get('ledger') !== url.month;

    if (stage === 'ledger') {
      const searchChanged = ledger.searchQuery !== url.q.toLowerCase().trim();
      if (ledger.drillStage !== 'ledger' || monthChanged || searchChanged) {
        this.componentMonth.set('ledger', url.month);
        ledger.navigateToStage('ledger', { month: url.month, search: url.q });
      }
      return;
    }

    const yearChanged = stage === 'months' && url.year !== null && ledger.selectedYear !== url.year;
    if (ledger.drillStage !== stage || yearChanged) {
      ledger.navigateToStage(stage, stage === 'months' && url.year ? { year: url.year } : {});
    }
    if (monthChanged) {
      this.componentMonth.set('ledger', url.month);
      ledger.setMonth(url.month);
    }
  }

  private onComponentMonth(name: MonthComponent, month: string): void {
    if (!this.active) return;
    this.componentMonth.set(name, month);
    this.queue({ month }, false);
  }

  private onLedgerStage(stage: LedgerStage, state: LedgerStageState): void {
    if (!this.active) return;
    if (stage === 'ledger') {
      this.componentMonth.set('ledger', state.month);
      this.queue({ view: 'ledger', year: null, month: state.month, q: state.search }, false);
    } else {
      this.queue({ view: VIEW_FOR_STAGE[stage], year: stage === 'months' ? state.year : null, q: '' }, false);
    }
  }

  private openLegacyPage(pageId: string): void {
    if (!this.active) return;
    const screen = screenForLegacyPage(pageId);
    if (!screen) return;
    const month = parseUrlState(this.navigator.location().search).month;
    this.navigator.navigate({ pathname: screenById(screen).path, search: screenSearch(month) }, { replace: false });
  }

  /**
   * Collects URL changes made during one synchronous legacy action (a month change also reports a
   * stage change) and writes them as a single history entry. Changes caused by syncing from the
   * URL only replace the entry, so Back never lands on a state the user didn't choose.
   */
  private queue(patch: UrlPatch, replace: boolean): void {
    const wantsReplace = replace || this.syncing;
    if (this.pendingPatch === null) {
      this.pendingPatch = { ...patch };
      this.pendingReplace = wantsReplace;
      queueMicrotask(() => {
        this.flush();
      });
    } else {
      this.pendingPatch = { ...this.pendingPatch, ...patch };
      this.pendingReplace = this.pendingReplace && wantsReplace;
    }
  }

  private flush(): void {
    const patch = this.pendingPatch;
    const replace = this.pendingReplace;
    this.pendingPatch = null;
    this.pendingReplace = true;
    if (!patch || !this.active) return;
    const { pathname, search } = this.navigator.location();
    const current = parseUrlState(search);
    const next = serializeUrlState(applyUrlPatch(current, patch));
    if (next === serializeUrlState(current)) return;
    this.navigator.navigate({ pathname, search: next }, { replace });
  }
}
