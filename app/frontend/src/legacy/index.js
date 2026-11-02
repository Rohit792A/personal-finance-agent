/**
 * Entry point to the legacy vanilla-JS screens for the React shell. Loaded with a dynamic import
 * only after sign-in, so the screens (and marked/DOMPurify) never run for signed-out visitors.
 * Types: index.d.ts. Each screen moves to React in Phase 3; delete its export when it does.
 */
export { initLedgerComponent } from '../components/ledger.js';
export { initAnalyticsComponent } from '../components/analytics.js';
export { initBudgetsComponent } from '../components/budgets.js';
export { initRecurringComponent } from '../components/recurring.js';
export { initUploadComponent } from '../components/upload.js';
export { initInsightsComponent } from '../components/insights.js';
export { initBankSyncComponent } from '../components/bankSync.js';
export { editModal, addTransactionModal, budgetModal, voiceClarificationModal } from '../components/modal.js';
