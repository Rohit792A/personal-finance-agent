/**
 * Monthly Financial Summary & Analytics Component
 * Deep financial insights, income vs expense breakdowns, savings rate,
 * category progress bars, and month-over-month trend analytics.
 */

import { getAnalyticsSummary, getTransactionMonths } from '../api/index.ts';
import { formatCurrency, formatMonthYear, getCategoryBadgeClass } from '../utils/formatters.js';
import { escapeHtml } from '../utils/dom.js';
import { toast } from './toast.js';

export class AnalyticsComponent {
  /**
   * @param {HTMLElement} container
   * @param {Object} [options]
   * @param {(month: string) => void} [options.onMonthChange]
   * @param {string} [options.initialMonth] 'YYYY-MM' or 'ALL' (the app shell passes its URL month).
   * @param {boolean} [options.autoSelectLatestMonth] false keeps 'ALL' instead of jumping to the latest month.
   */
  constructor(container, options = {}) {
    this.container = container;
    this.options = options;
    this.selectedMonth = options.initialMonth || 'ALL';
    this.months = [];
    this.data = null;
    this.isLoading = false;
    this.reloadQueued = false;

    this.renderScaffold();
    this.bindEvents();
    this.initData();
  }

  async initData() {
    await this.loadMonths();
    await this.loadAnalytics();
  }

  renderScaffold() {
    this.container.innerHTML = `
      <div class="space-y-6">
        <!-- Page Title & Controls -->
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl">
          <div>
            <div class="flex items-center gap-3">
              <div class="p-2.5 rounded-xl bg-violet-500/10 text-violet-400 border border-violet-500/20">
                <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
              </div>
              <div>
                <h2 class="text-xl font-bold text-white tracking-tight">Financial Summary &amp; Analytics</h2>
                <p class="text-xs text-slate-400 mt-0.5">Multi-month cash flow analysis, savings health score, and category distribution</p>
              </div>
            </div>
          </div>

          <!-- Month Selector -->
          <div class="flex items-center gap-3">
            <div class="flex items-center gap-2">
              <label for="analytics-month-select" class="text-xs font-semibold text-slate-400">Selected Period:</label>
              <select
                id="analytics-month-select"
                class="px-3.5 py-2 text-xs font-semibold bg-slate-950 border border-slate-700/80 rounded-xl text-violet-300 focus:outline-none focus:ring-2 focus:ring-violet-500 cursor-pointer"
              >
                <option value="ALL">All Time Overview</option>
              </select>
            </div>

            <button
              type="button"
              id="analytics-refresh-btn"
              class="p-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-colors cursor-pointer"
              title="Refresh Analytics"
            >
              <svg id="analytics-refresh-icon" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          </div>
        </div>

        <!-- Dynamic Content Body -->
        <div id="analytics-content-body">
          <!-- Populated by renderAnalytics() -->
        </div>
      </div>
    `;
  }

  bindEvents() {
    const monthSelect = this.container.querySelector('#analytics-month-select');
    const refreshBtn = this.container.querySelector('#analytics-refresh-btn');

    monthSelect?.addEventListener('change', (e) => {
      this.selectedMonth = e.target.value;
      this.loadAnalytics();
      if (typeof this.options.onMonthChange === 'function') {
        this.options.onMonthChange(this.selectedMonth);
      }
    });

    refreshBtn?.addEventListener('click', () => {
      this.initData();
    });
  }

  async loadMonths() {
    try {
      const res = await getTransactionMonths();
      this.months = res.months || [];
      const select = this.container.querySelector('#analytics-month-select');
      if (!select) return;

      let html = `<option value="ALL">All Time Overview (${res.all?.count || 0} transactions)</option>`;
      this.months.forEach((m) => {
        html += `<option value="${escapeHtml(m.month)}">${escapeHtml(m.label)} (${m.count} txns)</option>`;
      });
      select.innerHTML = html;

      // Default to latest available month if 'ALL' or not set (unless the app shell owns the month)
      if (
        this.options.autoSelectLatestMonth !== false &&
        (!this.selectedMonth || this.selectedMonth === 'ALL') &&
        this.months.length > 0
      ) {
        this.selectedMonth = this.months[0].month;
      }

      select.value = this.selectedMonth;
    } catch (err) {
      console.warn('[Analytics] Failed to load months:', err);
    }
  }

  async loadAnalytics() {
    if (this.isLoading) {
      // The month changed while a request was in flight: reload once it finishes.
      this.reloadQueued = true;
      return;
    }
    const body = this.container.querySelector('#analytics-content-body');
    const refreshIcon = this.container.querySelector('#analytics-refresh-icon');

    try {
      this.isLoading = true;
      refreshIcon?.classList.add('animate-spin');
      if (body) {
        body.innerHTML = `
          <div class="flex flex-col items-center justify-center py-16 bg-slate-900 border border-slate-800 rounded-2xl">
            <div class="w-10 h-10 border-2 border-violet-500/30 border-t-violet-400 rounded-full animate-spin mb-3"></div>
            <p class="text-xs text-slate-400 animate-pulse font-medium">Calculating financial metrics &amp; trend vectors...</p>
          </div>
        `;
      }

      const res = await getAnalyticsSummary(this.selectedMonth);
      if (this.reloadQueued) return; // stale; the finally block loads the newer month
      this.data = res;
      this.renderAnalytics();
    } catch (err) {
      if (this.reloadQueued) return;
      toast.show(`Analytics error: ${err.message}`, 'error');
      if (body) {
        body.innerHTML = `
          <div class="p-6 bg-rose-950/40 border border-rose-800 rounded-2xl text-center text-rose-300">
            <p class="font-semibold text-sm">Failed to load financial summary</p>
            <p class="text-xs text-rose-400/80 mt-1">${escapeHtml(err.message)}</p>
          </div>
        `;
      }
    } finally {
      this.isLoading = false;
      refreshIcon?.classList.remove('animate-spin');
      if (this.reloadQueued) {
        this.reloadQueued = false;
        this.loadAnalytics();
      }
    }
  }

  renderAnalytics() {
    const body = this.container.querySelector('#analytics-content-body');
    if (!body || !this.data) return;

    const { kpis, expense_categories = [], income_categories = [], monthly_trends = [] } = this.data;
    const isNetPositive = kpis.net_savings >= 0;

    // Enrich monthly_trends with chronological opening/closing balance if not already provided
    let runningTrendBal = 0;
    const enrichedTrends = monthly_trends.map((tr) => {
      const inc = Number(tr.income || 0);
      const exp = Number(tr.expense || 0);
      const sav = tr.savings !== undefined ? Number(tr.savings) : inc - exp;
      const openBal = tr.opening_balance !== undefined ? Number(tr.opening_balance) : runningTrendBal;
      const closeBal = tr.closing_balance !== undefined ? Number(tr.closing_balance) : openBal + sav;
      runningTrendBal = closeBal;
      return { ...tr, opening_balance: openBal, closing_balance: closeBal };
    });

    const matchedTrend =
      this.selectedMonth && this.selectedMonth !== 'ALL'
        ? enrichedTrends.find((t) => t.month === this.selectedMonth)
        : null;
    const openingBalance =
      kpis.opening_balance !== undefined
        ? Number(kpis.opening_balance)
        : matchedTrend
        ? Number(matchedTrend.opening_balance || 0)
        : 0;
    const closingBalance =
      kpis.closing_balance !== undefined
        ? Number(kpis.closing_balance)
        : matchedTrend && matchedTrend.closing_balance !== undefined
        ? Number(matchedTrend.closing_balance)
        : openingBalance + Number(kpis.net_savings || 0);
    const bankBreakdown = kpis.bank_breakdown || matchedTrend?.bank_breakdown || null;

    body.innerHTML = `
      <div class="space-y-6 animate-fade-in">
        <!-- Executive KPI Strip (Start of Month Balance, Total Inflow, Total Outflow, Net Cash Flow, End of Month Balance, Financial Vitality) -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
          <!-- Start of Month Balance Card -->
          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg relative overflow-hidden">
            <div class="flex items-center justify-between">
              <span class="text-xs font-semibold uppercase tracking-wider text-sky-400">Start of Month Balance</span>
              <span class="p-1.5 rounded-lg bg-sky-500/10 text-sky-400">
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
              </span>
            </div>
            <p class="text-2xl font-bold ${openingBalance >= 0 ? 'text-white' : 'text-rose-400'} mt-2 tabular-nums">
              ${openingBalance >= 0 ? '+' : '-'}${formatCurrency(Math.abs(openingBalance))}
            </p>
            <p class="text-[11px] text-slate-400 mt-1">
              ${
                this.selectedMonth === 'ALL'
                  ? 'All-time starting baseline (Kotak + HDFC)'
                  : bankBreakdown
                  ? `Kotak ${formatCurrency(Number(bankBreakdown.kotak_open || 0))} • HDFC ${formatCurrency(Number(bankBreakdown.hdfc_open || 0))}`
                  : 'Opening balance (carry-forward)'
              }
            </p>
          </div>

          <!-- Income Card -->
          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg relative overflow-hidden">
            <div class="flex items-center justify-between">
              <span class="text-xs font-semibold uppercase tracking-wider text-emerald-400">Total Inflow</span>
              <span class="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-400">
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 11l5-5m0 0l5 5m-5-5v12" />
                </svg>
              </span>
            </div>
            <p class="text-2xl font-bold text-white mt-2">${formatCurrency(kpis.total_income)}</p>
            <p class="text-[11px] text-slate-400 mt-1">Earned across ${kpis.transaction_count} records</p>
          </div>

          <!-- Expense Card -->
          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg relative overflow-hidden">
            <div class="flex items-center justify-between">
              <span class="text-xs font-semibold uppercase tracking-wider text-rose-400">Total Outflow</span>
              <span class="p-1.5 rounded-lg bg-rose-500/10 text-rose-400">
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 13l-5 5m0 0l-5-5m5 5V6" />
                </svg>
              </span>
            </div>
            <p class="text-2xl font-bold text-white mt-2">${formatCurrency(kpis.total_expenses)}</p>
            <p class="text-[11px] text-slate-400 mt-1">Total purchases &amp; payments</p>
          </div>

          <!-- Net Savings Card -->
          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg relative overflow-hidden">
            <div class="flex items-center justify-between">
              <span class="text-xs font-semibold uppercase tracking-wider text-indigo-400">Net Cash Flow</span>
              <span class="p-1.5 rounded-lg ${isNetPositive ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}">
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                </svg>
              </span>
            </div>
            <p class="text-2xl font-bold ${isNetPositive ? 'text-emerald-400' : 'text-rose-400'} mt-2">
              ${isNetPositive ? '+' : ''}${formatCurrency(kpis.net_savings)}
            </p>
            <p class="text-[11px] text-slate-400 mt-1">
              Savings Rate: <span class="font-semibold text-white">${kpis.savings_rate_pct}%</span>
            </p>
          </div>

          <!-- End of Month Balance Card -->
          <div class="bg-slate-900 border border-indigo-500/30 rounded-2xl p-5 shadow-lg relative overflow-hidden">
            <div class="flex items-center justify-between">
              <span class="text-xs font-semibold uppercase tracking-wider text-amber-400">End of Month Balance</span>
              <span class="p-1.5 rounded-lg bg-amber-500/10 text-amber-400">
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </span>
            </div>
            <p class="text-2xl font-bold ${closingBalance >= 0 ? 'text-emerald-400' : 'text-rose-400'} mt-2 tabular-nums">
              ${closingBalance >= 0 ? '+' : '-'}${formatCurrency(Math.abs(closingBalance))}
            </p>
            <p class="text-[11px] text-slate-400 mt-1">
              ${
                this.selectedMonth === 'ALL'
                  ? 'Latest reconciled balance (Kotak + HDFC)'
                  : bankBreakdown
                  ? `Kotak ${formatCurrency(Number(bankBreakdown.kotak_close || 0))} • HDFC ${formatCurrency(Number(bankBreakdown.hdfc_close || 0))}`
                  : 'Closing balance (start + net)'
              }
            </p>
          </div>

          <!-- Financial Health Score Card -->
          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg relative overflow-hidden">
            <div class="flex items-center justify-between">
              <span class="text-xs font-semibold uppercase tracking-wider text-amber-400">Financial Vitality</span>
              <span class="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-amber-500/15 text-amber-300 border border-amber-500/30">
                ${escapeHtml(kpis.health_status)}
              </span>
            </div>
            <div class="flex items-baseline gap-2 mt-2">
              <p class="text-2xl font-bold text-white">${kpis.health_score}</p>
              <span class="text-xs text-slate-500">/ 100</span>
            </div>
            <div class="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden mt-2">
              <div class="h-full bg-gradient-to-r from-amber-500 via-emerald-500 to-indigo-500 rounded-full" style="width: ${Math.min(100, kpis.health_score)}%"></div>
            </div>
          </div>
        </div>

        <!-- Section 2: Category Spending Breakdown & Month-over-Month Trends Grid -->
        <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          <!-- Category Distribution (Left 7 cols) -->
          <div class="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
            <div class="flex items-center justify-between pb-3 border-b border-slate-800">
              <div>
                <h3 class="text-base font-bold text-white">Spending by Category</h3>
                <p class="text-xs text-slate-400">Distribution of expenses for ${escapeHtml(formatMonthYear(this.selectedMonth))}</p>
              </div>
              <span class="text-xs font-semibold text-slate-400">${expense_categories.length} Categories</span>
            </div>

            ${
              expense_categories.length === 0
                ? `<div class="py-12 text-center text-slate-500 text-xs">No expense records found for this period.</div>`
                : `
                <div class="space-y-4 pt-1">
                  ${expense_categories
                    .map((cat) => {
                      return `
                      <div class="space-y-1.5">
                        <div class="flex items-center justify-between text-xs">
                          <div class="flex items-center gap-2">
                            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium border ${getCategoryBadgeClass(cat.category)}">
                              ${escapeHtml(cat.category)}
                            </span>
                            <span class="text-slate-500 text-[11px]">(${cat.count} txns)</span>
                          </div>
                          <div class="text-right">
                            <span class="font-mono font-semibold text-white">${formatCurrency(cat.total)}</span>
                            <span class="text-slate-400 text-[11px] ml-1.5 font-mono">(${cat.percentage}%)</span>
                          </div>
                        </div>
                        <div class="w-full bg-slate-950 rounded-full h-2 overflow-hidden border border-slate-800/80">
                          <div
                            class="h-full bg-gradient-to-r from-indigo-500 to-violet-400 rounded-full transition-all duration-500"
                            style="width: ${Math.min(100, Math.max(2, cat.percentage))}%;"
                          ></div>
                        </div>
                      </div>
                    `;
                    })
                    .join('')}
                </div>
              `
            }
          </div>

          <!-- Month-over-Month Comparison (Right 5 cols) -->
          <div class="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
            <div class="flex items-center justify-between pb-3 border-b border-slate-800">
              <div>
                <h3 class="text-base font-bold text-white">Monthly Trends</h3>
                <p class="text-xs text-slate-400">Historical income, spend &amp; start/end balances</p>
              </div>
              <span class="text-xs font-semibold text-indigo-400">${enrichedTrends.length} Months</span>
            </div>

            ${
              enrichedTrends.length === 0
                ? `<div class="py-12 text-center text-slate-500 text-xs">No trend data available.</div>`
                : `
                <div class="divide-y divide-slate-800/60">
                  ${enrichedTrends
                    .slice()
                    .reverse()
                    .map((tr) => {
                      const isSurplus = tr.savings >= 0;
                      const openB = Number(tr.opening_balance || 0);
                      const closeB = Number(tr.closing_balance || 0);
                      return `
                      <div class="py-3 flex items-center justify-between gap-3 text-xs">
                        <div>
                          <p class="font-semibold text-white text-sm">${escapeHtml(tr.label)}</p>
                          <p class="text-[11px] text-slate-400">
                            In: <span class="text-emerald-400 font-mono">${formatCurrency(tr.income)}</span> &bull; 
                            Out: <span class="text-rose-400 font-mono">${formatCurrency(tr.expense)}</span>
                          </p>
                          <p class="text-[10px] text-slate-500 mt-0.5">
                            Start: <span class="text-sky-400 font-mono">${openB >= 0 ? '+' : '-'}${formatCurrency(Math.abs(openB))}</span> &rarr; 
                            End: <span class="text-amber-400 font-mono">${closeB >= 0 ? '+' : '-'}${formatCurrency(Math.abs(closeB))}</span>
                          </p>
                        </div>
                        <div class="text-right">
                          <span class="inline-flex items-center px-2 py-0.5 rounded-lg text-xs font-bold font-mono ${
                            isSurplus
                              ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-800/60'
                              : 'bg-rose-950/80 text-rose-300 border border-rose-800/60'
                          }">
                            ${isSurplus ? '+' : ''}${formatCurrency(tr.savings)}
                          </span>
                          <p class="text-[10px] text-slate-500 mt-0.5">${isSurplus ? 'Net Surplus' : 'Net Deficit'}</p>
                        </div>
                      </div>
                    `;
                    })
                    .join('')}
                </div>
              `
            }
          </div>
        </div>
      </div>
    `;
  }

  setMonth(month) {
    this.selectedMonth = month;
    const select = this.container.querySelector('#analytics-month-select');
    if (select) select.value = month;
    this.loadAnalytics();
  }
}

export function initAnalyticsComponent(container, options = {}) {
  return new AnalyticsComponent(container, options);
}
