/**
 * Bank Sync Component
 * Gmail Live Bank Alert & Monthly Statement Sync (with 3-Layer Cross-Source Deduplication)
 */

import {
  fetchGmailSyncSettings,
  saveGmailSyncSettings,
  triggerGmailSync,
  fetchExportedStatementPdfs,
  unlockExportedStatementPdf,
  downloadStatementPdf,
} from '../api/index.ts';
import { escapeHtml } from '../utils/dom.js';
import { toast } from './toast.js';

export function initBankSyncComponent(container, { onSyncSuccess } = {}) {
  let exportedStatements = { export_directory: '', total_pdfs: 0, months: [], statements: [] };
  let gmailSettings = {
    configured: false,
    email_address: '',
    app_password_masked: '',
    auto_sync_enabled: true,
    sync_interval_hours: 24,
    last_synced_at: null,
    last_sync_status: 'Not configured yet',
    emails_processed: 0,
    total_transactions_synced: 0,
    exported_pdfs_count: 0,
  };
  let isSavingGmail = false;
  let isSyncingGmail = false;
  let unlockingStmtId = null;

  // Imported only when the import finished. An unlocked statement whose import failed or never
  // ran (IMPORT_FAILED, EXPORTED_ONLY) stays retryable instead of showing "Imported (0 txns)" (DEC-060).
  const isImported = (st) => !st.is_encrypted && st.sync_status === 'IMPORTED_AND_RECONCILED';

  function markStatementImportedOptimistically(stmtId, txCount = 0) {
    const numId = Number(stmtId);
    if (Array.isArray(exportedStatements.statements)) {
      exportedStatements.statements.forEach((s) => {
        if (Number(s.id) === numId) {
          s.is_encrypted = false;
          s.sync_status = 'IMPORTED_AND_RECONCILED';
          s.transactions_imported = txCount || s.transactions_imported || 0;
        }
      });
    }
    if (Array.isArray(exportedStatements.months)) {
      exportedStatements.months.forEach((m) => {
        if (Array.isArray(m.statements)) {
          m.statements.forEach((s) => {
            if (Number(s.id) === numId) {
              s.is_encrypted = false;
              s.sync_status = 'IMPORTED_AND_RECONCILED';
              s.transactions_imported = txCount || s.transactions_imported || 0;
            }
          });
        }
      });
    }
  }

  async function loadData() {
    try {
      const [gmailRes, stmtsRes] = await Promise.all([
        fetchGmailSyncSettings().catch(() => gmailSettings),
        fetchExportedStatementPdfs().catch(() => exportedStatements),
      ]);
      if (gmailRes) {
        gmailSettings = gmailRes;
      }
      if (stmtsRes) {
        exportedStatements = stmtsRes;
      }
      render();
    } catch (err) {
      console.error('Failed to load bank data:', err);
      toast.show('Failed to load bank sync settings', 'error');
    }
  }

  function render() {
    container.innerHTML = `
      <div class="space-y-6">
        <!-- Banner & Header -->
        <div class="bg-gradient-to-r from-slate-900 via-indigo-950/40 to-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl relative overflow-hidden">
          <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div class="space-y-1">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-2xl">🇮🇳</span>
                <h2 class="text-xl font-bold text-white tracking-tight">Live Bank Sync & Statement Reconciliation</h2>
                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  Zero-Duplicate Reconciliation
                </span>
              </div>
              <p class="text-xs sm:text-sm text-slate-400 max-w-3xl">
                Sync real-time bank transaction alerts daily via Gmail (Read-Only IMAP SSL) and automatically reconcile them against your monthly PDF/Excel bank statements so no transaction is ever duplicated.
              </p>
            </div>

            <div class="flex items-center gap-2">
              <button
                id="btn-refresh-accounts"
                class="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold border border-slate-700 flex items-center gap-1.5 transition-colors cursor-pointer"
              >
                <span>🔄</span>
                <span>Refresh Status</span>
              </button>
            </div>
          </div>
        </div>

        <!-- PRIMARY SECTION: Gmail Live Bank Alert & Monthly Statement Sync -->
        <div class="bg-slate-900 border border-indigo-500/30 rounded-2xl p-6 shadow-xl space-y-5">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
            <div class="flex items-center gap-3">
              <div class="p-2.5 rounded-xl bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xl">
                📧
              </div>
              <div>
                <div class="flex items-center gap-2">
                  <h3 class="text-base font-bold text-white">Gmail Live Bank Alert & Monthly Statement Sync</h3>
                  <span class="px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                    gmailSettings.configured
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                  }">
                    ${gmailSettings.configured ? '● Connected' : '○ Setup Required'}
                  </span>
                </div>
                <p class="text-xs text-slate-400 mt-0.5">
                  Reads instant bank alert emails daily & reconciles monthly statements using UTR/Reference numbers and same-day exact-amount matching.
                </p>
              </div>
            </div>

            <div class="flex items-center gap-3 text-xs text-slate-400">
              <div class="px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800">
                <span class="text-slate-500">Emails Processed:</span>
                <span class="font-mono font-semibold text-white ml-1">${gmailSettings.emails_processed || 0}</span>
              </div>
              <div class="px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800">
                <span class="text-slate-500">Last Sync:</span>
                <span class="font-mono text-slate-300 ml-1">${
                  gmailSettings.last_synced_at
                    ? new Date(gmailSettings.last_synced_at).toLocaleString()
                    : 'Never'
                }</span>
              </div>
            </div>
          </div>

          <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <!-- Left 6 cols: Gmail Credentials Configuration -->
            <form id="form-gmail-settings" class="lg:col-span-6 space-y-4">
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                <div class="space-y-1.5">
                  <label for="input-gmail-address" class="block text-xs font-semibold text-slate-300">
                    Your Gmail Address
                  </label>
                  <input
                    type="email"
                    id="input-gmail-address"
                    value="${escapeHtml(gmailSettings.email_address || '')}"
                    placeholder="yourname@gmail.com"
                    class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3.5 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-indigo-500"
                    required
                  />
                </div>

                <div class="space-y-1.5">
                  <div class="flex items-center justify-between">
                    <label for="input-gmail-password" class="block text-xs font-semibold text-slate-300">
                      Google App Password (16 chars)
                    </label>
                    <a
                      href="https://myaccount.google.com/apppasswords"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-[11px] text-indigo-400 hover:text-indigo-300 underline"
                    >
                      Get App Password ↗
                    </a>
                  </div>
                  <input
                    type="password"
                    id="input-gmail-password"
                    placeholder="${escapeHtml(gmailSettings.app_password_masked || 'xxxx xxxx xxxx xxxx')}"
                    class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3.5 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-indigo-500 font-mono"
                    ${gmailSettings.configured ? '' : 'required'}
                  />
                </div>
              </div>

              <div class="space-y-1.5">
                <label for="input-gmail-senders" class="block text-xs font-semibold text-slate-300">
                  Strict Allowed Bank Sender Emails (PII Isolation Filter)
                </label>
                <input
                  type="text"
                  id="input-gmail-senders"
                  value="${escapeHtml(
                    gmailSettings.allowed_senders ||
                    'alerts@hdfcbank.net, estatement@hdfcbank.net, onlinesbi@sbi.co.in, credit_cards@icicibank.com, alerts@axisbank.com, bankalerts@kotak.com'
                  )}"
                  placeholder="alerts@hdfcbank.net, onlinesbi@sbi.co.in"
                  class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3.5 py-2 text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-indigo-500 font-mono"
                />
                <p class="text-[11px] text-emerald-400/90">
                  🛡️ Zero PII Leakage: Gmail's server filters strictly by these exact sender addresses before returning any message—personal emails are never accessed.
                </p>
              </div>

              <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-1">
                <label class="inline-flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    id="check-gmail-autosync"
                    class="rounded border-slate-700 bg-slate-950 text-indigo-600"
                    ${gmailSettings.auto_sync_enabled ? 'checked' : ''}
                  />
                  <span>Automatically check Gmail daily for new bank transactions</span>
                </label>

                <button
                  type="submit"
                  id="btn-save-gmail"
                  ${isSavingGmail ? 'disabled' : ''}
                  class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-600 text-white text-xs font-semibold transition-all cursor-pointer flex items-center justify-center gap-1.5"
                >
                  ${
                    isSavingGmail
                      ? `<span class="animate-spin">⏳</span><span>Verifying IMAP...</span>`
                      : `<span>🔒</span><span>${gmailSettings.configured ? 'Update Gmail Settings' : 'Verify & Connect Gmail'}</span>`
                  }
                </button>
              </div>
            </form>

            <!-- Right 6 cols: Instant Sync Controls & Reconciliation Status -->
            <div class="lg:col-span-6 bg-slate-950/60 border border-slate-800 rounded-xl p-4 flex flex-col justify-between space-y-4">
              <div class="space-y-3">
                <div class="flex items-center justify-between">
                  <span class="text-xs font-semibold text-slate-200">On-Demand Sync & Statement Reconciliation</span>
                  <span class="text-[11px] text-emerald-400 font-mono">${escapeHtml(gmailSettings.last_sync_status || '')}</span>
                </div>

                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label for="select-gmail-days" class="block text-[11px] font-medium text-slate-400 mb-1">
                      Scan Window
                    </label>
                    <select
                      id="select-gmail-days"
                      class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
                    >
                      <option value="2">Last 2 Days (Daily Quick Sync)</option>
                      <option value="7" selected>Last 7 Days (Weekly Sync)</option>
                      <option value="30">Last 30 Days (Full Monthly Sync)</option>
                      <option value="60">Last 60 Days</option>
                    </select>
                  </div>

                  <div class="flex items-end pb-1">
                    <label class="inline-flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                      <input
                        type="checkbox"
                        id="check-include-statements"
                        class="rounded border-slate-700 bg-slate-900 text-indigo-600"
                        checked
                      />
                      <span>Also import PDF/Excel statement attachments</span>
                    </label>
                  </div>
                </div>

                <div class="p-2.5 rounded-lg bg-indigo-950/30 border border-indigo-500/20 text-[11px] text-slate-300 leading-relaxed">
                  <strong class="text-indigo-300">How Monthly Statement Deduplication Works:</strong> When your monthly statement arrives (via Gmail or uploaded manually in the Upload tab), each row is matched against existing daily email alerts by <strong>12-digit UPI/UTR Reference</strong> and <strong>Same-Day + Exact Amount + Merchant</strong>—upgrading the alert in-place without ever creating duplicate entries.
                </div>
              </div>

              <button
                type="button"
                id="btn-trigger-gmail-sync"
                ${isSyncingGmail || !gmailSettings.configured ? 'disabled' : ''}
                class="w-full py-2.5 px-4 rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs font-semibold transition-all shadow-lg shadow-emerald-600/20 cursor-pointer flex items-center justify-center gap-2"
              >
                ${
                  isSyncingGmail
                    ? `<span class="animate-spin">⏳</span><span>Scanning Gmail & Exporting PDFs...</span>`
                    : `<span>⚡</span><span>Sync Bank Emails & Export Monthly PDFs Now</span>`
                }
              </button>
            </div>
          </div>
        </div>

        <!-- MONTHLY EXPORTED BANK STATEMENT PDFS (HDFC / KOTAK / SBI) -->
        <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
            <div class="flex items-center gap-3">
              <div class="p-2.5 rounded-xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 text-xl">
                📄
              </div>
              <div>
                <div class="flex items-center gap-2">
                  <h3 class="text-base font-bold text-white">Monthly Exported Bank Statement PDFs (HDFC &bull; Kotak &bull; SBI)</h3>
                  <span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">
                    ${exportedStatements.total_pdfs || 0} PDF(s) Exported
                  </span>
                </div>
                <p class="text-xs text-slate-400 mt-0.5">
                  Automatically fetched from your bank emails and organized month-wise in <code class="text-indigo-300 font-mono">data/monthly_statements/&lt;YYYY-MM&gt;/</code>
                </p>
              </div>
            </div>
          </div>

          ${
            !exportedStatements.months || exportedStatements.months.length === 0
              ? `
            <div class="p-6 text-center border border-dashed border-slate-800 rounded-xl space-y-1.5">
              <p class="text-sm font-medium text-slate-300">No monthly bank statement PDFs exported yet</p>
              <p class="text-xs text-slate-500">
                Select "Last 30 Days" or "Last 60 Days" above and click "Sync Bank Emails & Export Monthly PDFs Now" to auto-fetch HDFC, Kotak, and SBI PDF statements.
              </p>
            </div>
          `
              : `
            <div class="space-y-5">
              ${exportedStatements.months
                .map(
                  (mGroup) => `
                <div class="space-y-2.5">
                  <div class="flex items-center gap-2">
                    <span class="px-2.5 py-1 rounded-lg bg-indigo-950/80 border border-indigo-500/30 text-xs font-bold font-mono text-indigo-300">
                      📅 ${escapeHtml(mGroup.month_key)}
                    </span>
                    <span class="text-xs text-slate-400">${mGroup.count} statement PDF(s)</span>
                  </div>

                  <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    ${mGroup.statements
                      .map(
                        (st) => {
                          const isThisUnlocking = Number(unlockingStmtId) === Number(st.id);
                          return `
                      <div class="p-4 rounded-xl bg-slate-950/70 border ${
                        isThisUnlocking
                          ? 'border-indigo-500/60 ring-1 ring-indigo-500/30'
                          : st.is_encrypted
                          ? 'border-slate-800 hover:border-slate-700'
                          : isImported(st)
                          ? 'border-emerald-500/30 bg-emerald-950/10'
                          : 'border-amber-500/40 bg-amber-950/10'
                      } flex flex-col justify-between gap-3 transition-all">
                        <div class="space-y-1">
                          <div class="flex items-center justify-between gap-2">
                            <span class="text-xs font-bold text-white flex items-center gap-1.5">
                              <span>${(st.bank_name || '').includes('SBI') ? '🏛️' : (st.bank_name || '').includes('Kotak') ? '💳' : '🏦'}</span>
                              <span>${escapeHtml(st.bank_name || 'Bank')}</span>
                            </span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${
                              isThisUnlocking
                                ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 animate-pulse'
                                : st.is_encrypted || !isImported(st)
                                ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                                : 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                            }">
                              ${
                                isThisUnlocking
                                  ? '⏳ Reading & importing via AI...'
                                  : st.is_encrypted
                                  ? '🔒 Password Protected'
                                  : isImported(st)
                                  ? `✅ Imported (${st.transactions_imported || 0} txns)`
                                  : '⚠️ Not imported'
                              }
                            </span>
                          </div>
                          <p class="text-xs font-mono text-indigo-300 break-all">${escapeHtml(st.file_name)}</p>
                          <p class="text-[11px] text-slate-400 truncate" title="${escapeHtml(st.email_subject || '')}">
                            ${escapeHtml(st.email_subject || 'Monthly Bank Statement')} &bull; Email Date: ${escapeHtml(st.email_date || '')}
                          </p>
                        </div>

                        <div class="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-slate-800/80">
                          <button
                            type="button"
                            data-download-stmt="${escapeHtml(st.id)}"
                            data-file-name="${escapeHtml(st.file_name || '')}"
                            class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold border border-slate-700 inline-flex items-center gap-1.5 cursor-pointer disabled:opacity-60 disabled:cursor-wait"
                          >
                            <span aria-hidden="true">⬇️</span>
                            <span>Download PDF</span>
                          </button>

                          ${
                            st.is_encrypted
                              ? `
                            <div class="flex items-center gap-1.5 flex-1 max-w-xs">
                              <input
                                type="password"
                                data-unlock-input="${escapeHtml(st.id)}"
                                placeholder="PDF Password"
                                ${isThisUnlocking ? 'disabled' : ''}
                                class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1 text-xs text-slate-200 font-mono disabled:opacity-50"
                              />
                              <button
                                type="button"
                                data-unlock-btn="${escapeHtml(st.id)}"
                                ${isThisUnlocking ? 'disabled' : ''}
                                class="btn-unlock-pdf px-2.5 py-1 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-60 text-white text-xs font-semibold whitespace-nowrap cursor-pointer flex items-center gap-1"
                              >
                                ${isThisUnlocking ? '<span class="animate-spin">⏳</span><span>Importing...</span>' : 'Unlock & Sync'}
                              </button>
                            </div>
                          `
                              : isImported(st)
                              ? `<span class="text-[11px] text-emerald-400 font-mono font-semibold">✅ Imported & Reconciled (${st.transactions_imported || 0} txns)</span>`
                              : `
                            <div class="flex items-center gap-2">
                              <span class="text-[11px] text-amber-400">Unlocked, but its transactions aren't in the ledger yet.</span>
                              <button
                                type="button"
                                data-unlock-btn="${escapeHtml(st.id)}"
                                data-needs-password="0"
                                ${isThisUnlocking ? 'disabled' : ''}
                                class="btn-unlock-pdf px-2.5 py-1 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-60 text-white text-xs font-semibold whitespace-nowrap cursor-pointer flex items-center gap-1"
                              >
                                ${isThisUnlocking ? '<span class="animate-spin">⏳</span><span>Importing...</span>' : 'Retry import'}
                              </button>
                            </div>
                          `
                          }
                        </div>
                      </div>
                    `;
                        }
                      )
                      .join('')}
                  </div>
                </div>
              `
                )
                .join('')}
            </div>
          `
          }
        </div>
      </div>
    `;

    attachEventListeners();
  }

  function attachEventListeners() {
    const refreshBtn = container.querySelector('#btn-refresh-accounts');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', loadData);
    }

    // Statement PDF downloads need the sign-in token, so they go through the API client
    // (fetch -> Blob -> file) instead of a plain link.
    container.querySelectorAll('[data-download-stmt]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const stmtId = btn.getAttribute('data-download-stmt');
        const fileName = btn.getAttribute('data-file-name') || `statement-${stmtId}.pdf`;
        btn.disabled = true;
        try {
          await downloadStatementPdf(stmtId, fileName);
        } catch (err) {
          toast.show(err.message || 'Failed to download the statement PDF', 'error');
        } finally {
          btn.disabled = false;
        }
      });
    });

    // Gmail Settings Form
    const gmailForm = container.querySelector('#form-gmail-settings');
    if (gmailForm) {
      gmailForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const emailAddr = container.querySelector('#input-gmail-address').value.trim();
        const appPw = container.querySelector('#input-gmail-password').value.trim();
        const allowedSenders = container.querySelector('#input-gmail-senders')?.value.trim();
        const autoSync = container.querySelector('#check-gmail-autosync').checked;

        isSavingGmail = true;
        render();

        try {
          const updated = await saveGmailSyncSettings({
            email_address: emailAddr,
            app_password: appPw || undefined,
            allowed_senders: allowedSenders,
            auto_sync_enabled: autoSync,
            sync_interval_hours: 24,
          });
          gmailSettings = updated;
          toast.show('Gmail IMAP connection verified and saved!', 'success');
        } catch (err) {
          console.error(err);
          toast.show(err.message || 'Failed to verify Gmail credentials', 'error');
        } finally {
          isSavingGmail = false;
          render();
        }
      });
    }

    // Trigger Gmail Sync Now Button
    const gmailSyncBtn = container.querySelector('#btn-trigger-gmail-sync');
    if (gmailSyncBtn) {
      gmailSyncBtn.addEventListener('click', async () => {
        const daysBack = parseInt(container.querySelector('#select-gmail-days')?.value || '30', 10);
        const includeStatements = container.querySelector('#check-include-statements')?.checked ?? true;

        isSyncingGmail = true;
        render();

        try {
          const res = await triggerGmailSync({
            days_back: daysBack,
            include_statements: includeStatements,
          });
          toast.show(res.message || 'Gmail sync completed!', 'success');
          const [gSet, eStmts] = await Promise.all([
            fetchGmailSyncSettings(),
            fetchExportedStatementPdfs(),
          ]);
          gmailSettings = gSet;
          exportedStatements = eStmts;
          if (typeof onSyncSuccess === 'function') {
            onSyncSuccess(res);
          }
        } catch (err) {
          console.error('Gmail sync failed:', err);
          toast.show(err.message || 'Gmail sync failed', 'error');
        } finally {
          isSyncingGmail = false;
          render();
        }
      });
    }

    // Unlock & Sync exported PDF buttons (and "Retry import" for unlocked statements that weren't imported)
    const unlockBtns = container.querySelectorAll('.btn-unlock-pdf');
    unlockBtns.forEach((btn) => {
      btn.addEventListener('click', async () => {
        const stmtId = btn.getAttribute('data-unlock-btn');
        const needsPassword = btn.getAttribute('data-needs-password') !== '0';
        const inputEl = container.querySelector(`input[data-unlock-input="${CSS.escape(stmtId)}"]`);
        const pw = inputEl ? inputEl.value.trim() : '';
        if (needsPassword && !pw) {
          toast.show('Please enter the PDF password to unlock this statement', 'warning');
          return;
        }
        unlockingStmtId = stmtId;
        render();
        toast.show('Reading the statement with AI. Long statements can take a few minutes; keep this page open.', 'info', 8000);
        try {
          const res = await unlockExportedStatementPdf(stmtId, pw);
          // 1. Immediately update UI state optimistically so card flips to Imported with zero delay
          markStatementImportedOptimistically(stmtId, res.transactions_imported);
          unlockingStmtId = null;
          render();
          toast.show(res.message || 'Statement unlocked and synced!', 'success', 8000);
          // Pages where fewer rows were read than the statement shows (DEC-060).
          (Array.isArray(res.notifications) ? res.notifications : [])
            .filter((note) => String(note).startsWith('⚠️'))
            .forEach((note) => toast.show(String(note), 'warning', 12000));

          // 2. Refresh canonical server list & all ledger/analytics tabs in background
          const [freshStmts, freshGmail] = await Promise.all([
            fetchExportedStatementPdfs().catch(() => exportedStatements),
            fetchGmailSyncSettings().catch(() => gmailSettings),
          ]);
          exportedStatements = freshStmts;
          gmailSettings = freshGmail;
          render();

          if (typeof onSyncSuccess === 'function') {
            onSyncSuccess(res);
          }
        } catch (err) {
          console.error(err);
          // If the request timed out while the backend was still importing, poll the statement
          // list (every 5 s for up to 2 minutes) so the UI still updates once the import lands.
          let recovered = false;
          let failedOnServer = false;
          if (err.status === 408 || err.status === 524 || err.status === 504 || err.status === 0) {
            toast.show('AI PDF extraction in progress on server — waiting for completion...', 'info');
            for (let i = 0; i < 24; i++) {
              await new Promise((r) => setTimeout(r, 5000));
              const latest = await fetchExportedStatementPdfs().catch(() => null);
              if (latest && Array.isArray(latest.statements)) {
                const target = latest.statements.find((s) => Number(s.id) === Number(stmtId));
                if (target && isImported(target)) {
                  exportedStatements = latest;
                  recovered = true;
                  toast.show(
                    `Imported ${target.file_name}: ${target.transactions_imported || 0} transactions reconciled.`,
                    'success'
                  );
                  if (typeof onSyncSuccess === 'function') {
                    onSyncSuccess(target);
                  }
                  break;
                }
                if (target && target.sync_status === 'IMPORT_FAILED') {
                  exportedStatements = latest;
                  failedOnServer = true;
                  break;
                }
              }
            }
          }
          if (!recovered) {
            toast.show(
              failedOnServer
                ? 'The import failed on the server; nothing was added. Use "Retry import" to try again.'
                : err.message || 'Failed to unlock PDF statement',
              'error',
              10000
            );
            const latest = await fetchExportedStatementPdfs().catch(() => null);
            if (latest) exportedStatements = latest;
          }
        } finally {
          unlockingStmtId = null;
          render();
        }
      });
    });
  }

  loadData();

  return {
    refresh: loadData,
  };
}
