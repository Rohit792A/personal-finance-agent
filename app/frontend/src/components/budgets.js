/**
 * Category Budgets & Spending Targets Component
 * Visual budget utilization, warning notifications, and interactive monthly limit adjustments.
 */

import { getBudgets, updateBudget, getTransactionMonths } from '../api/index.ts';
import { formatCurrency, formatMonthYear, getCategoryBadgeClass } from '../utils/formatters.js';
import { escapeHtml } from '../utils/dom.js';
import { budgetModal } from './modal.js';
import { toast } from './toast.js';

export class BudgetsComponent {
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
    await this.loadBudgets();
  }

  renderScaffold() {
    this.container.innerHTML = `
      <div class="space-y-6">
        <!-- Top Bar -->
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl">
          <div>
            <div class="flex items-center gap-3">
              <div class="p-2.5 rounded-xl bg-amber-500/10 text-amber-400 border border-amber-500/20">
                <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </div>
              <div>
                <h2 class="text-xl font-bold text-white tracking-tight">Budgets &amp; Spending Goals</h2>
                <p class="text-xs text-slate-400 mt-0.5">Enforce fiscal discipline with active spend limits and real-time alerts</p>
              </div>
            </div>
          </div>

          <!-- Controls -->
          <div class="flex items-center gap-3">
            <div class="flex items-center gap-2">
              <label for="budget-month-select" class="text-xs font-semibold text-slate-400">Budget Cycle:</label>
              <select
                id="budget-month-select"
                class="px-3.5 py-2 text-xs font-semibold bg-slate-950 border border-slate-700/80 rounded-xl text-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-500 cursor-pointer"
              >
                <option value="ALL">All Time Average</option>
              </select>
            </div>

            <button
              type="button"
              id="budget-refresh-btn"
              class="p-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-colors cursor-pointer"
              title="Refresh Budgets"
            >
              <svg id="budget-refresh-icon" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          </div>
        </div>

        <!-- Content Area -->
        <div id="budget-content-body">
          <!-- Rendered via renderBudgets() -->
        </div>
      </div>
    `;
  }

  bindEvents() {
    const monthSelect = this.container.querySelector('#budget-month-select');
    const refreshBtn = this.container.querySelector('#budget-refresh-btn');

    monthSelect?.addEventListener('change', (e) => {
      this.selectedMonth = e.target.value;
      this.loadBudgets();
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
      const select = this.container.querySelector('#budget-month-select');
      if (!select) return;

      let html = `<option value="ALL">All Time Overview</option>`;
      this.months.forEach((m) => {
        html += `<option value="${escapeHtml(m.month)}">${escapeHtml(m.label)}</option>`;
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
      console.warn('[Budgets] Failed to load months:', err);
    }
  }

  async loadBudgets() {
    if (this.isLoading) {
      // The month changed while a request was in flight: reload once it finishes.
      this.reloadQueued = true;
      return;
    }
    const body = this.container.querySelector('#budget-content-body');
    const refreshIcon = this.container.querySelector('#budget-refresh-icon');

    try {
      this.isLoading = true;
      refreshIcon?.classList.add('animate-spin');

      if (body) {
        body.innerHTML = `
          <div class="flex flex-col items-center justify-center py-16 bg-slate-900 border border-slate-800 rounded-2xl">
            <div class="w-10 h-10 border-2 border-amber-500/30 border-t-amber-400 rounded-full animate-spin mb-3"></div>
            <p class="text-xs text-slate-400 animate-pulse font-medium">Auditing category budgets &amp; spend variance...</p>
          </div>
        `;
      }

      const res = await getBudgets(this.selectedMonth);
      if (this.reloadQueued) return; // stale; the finally block loads the newer month
      this.data = res;
      this.renderBudgets();
    } catch (err) {
      if (this.reloadQueued) return;
      toast.show(`Budget error: ${err.message}`, 'error');
    } finally {
      this.isLoading = false;
      refreshIcon?.classList.remove('animate-spin');
      if (this.reloadQueued) {
        this.reloadQueued = false;
        this.loadBudgets();
      }
    }
  }

  renderBudgets() {
    const body = this.container.querySelector('#budget-content-body');
    if (!body || !this.data) return;

    const { categories = [], total_budget, total_spent, overall_utilization_pct } = this.data;
    const isOverOverall = overall_utilization_pct >= 100;
    const isNearOverall = !isOverOverall && overall_utilization_pct >= 80;
    // Budgets are monthly caps. All Time compares the whole ledger's spending with each cap times the
    // months that have expenses (GET /budgets returns months_count); one month uses the caps as they are.
    const monthsCount = Number(this.data.months_count) > 1 ? Number(this.data.months_count) : 1;
    const monthlyTotalBudget = Number(this.data.monthly_total_budget ?? total_budget) || 0;
    const monthlyCap = (cat) => Number(cat.monthly_budget ?? cat.budget) || 0;

    body.innerHTML = `
      <div class="space-y-6 animate-fade-in">
        <!-- Aggregate Budget Summary Card -->
        <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl relative overflow-hidden">
          <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-slate-800">
            <div>
              <span class="text-xs font-semibold uppercase tracking-wider text-amber-400">Overall Budget Utilization</span>
              <div class="flex items-baseline gap-3 mt-1">
                <h3 class="text-3xl font-extrabold text-white">${formatCurrency(total_spent)}</h3>
                <span class="text-sm text-slate-400">spent of ${formatCurrency(total_budget)} budgeted</span>
              </div>
              ${
                monthsCount > 1
                  ? `<p
                      id="budget-period-note"
                      class="text-xs text-slate-500 mt-1"
                      title="All Time adds up the spending of the ${monthsCount} months that have expenses, so each monthly budget is multiplied by ${monthsCount}."
                    >${formatCurrency(monthlyTotalBudget)}/month × ${monthsCount} months</p>`
                  : ''
              }
            </div>

            <div class="flex items-center gap-3">
              <div class="text-right">
                <span class="text-xs font-bold font-mono ${isOverOverall ? 'text-rose-400' : isNearOverall ? 'text-amber-400' : 'text-emerald-400'}">
                  ${overall_utilization_pct}% Utilized
                </span>
                <p class="text-[11px] text-slate-400">${isOverOverall ? 'Over Budget' : isNearOverall ? 'Near Limit' : 'Safe Runway'}</p>
              </div>
            </div>
          </div>

          <!-- Overall Progress Bar -->
          <div class="mt-4">
            <div class="w-full bg-slate-950 rounded-full h-3 overflow-hidden border border-slate-800">
              <div
                class="h-full rounded-full transition-all duration-500 ${
                  isOverOverall
                    ? 'bg-rose-500'
                    : overall_utilization_pct >= 80
                    ? 'bg-amber-500'
                    : 'bg-emerald-500'
                }"
                style="width: ${Math.min(100, overall_utilization_pct)}%;"
              ></div>
            </div>
          </div>
        </div>

        <!-- Category Grid -->
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          ${categories
            .map((cat) => {
              const isOver = cat.status === 'exceeded';
              const isWarn = cat.status === 'warning';
              const barColor = isOver ? 'bg-rose-500' : isWarn ? 'bg-amber-500' : 'bg-emerald-500';
              const badgeColor = isOver
                ? 'bg-rose-500/15 text-rose-300 border-rose-500/30'
                : isWarn
                ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
                : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30';
              const statusText = isOver ? 'Over Budget' : isWarn ? 'Near Limit' : 'Within Budget';

              return `
              <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-md flex flex-col justify-between hover:border-slate-700 transition-colors group">
                <div>
                  <div class="flex items-start justify-between gap-2 mb-3">
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${getCategoryBadgeClass(cat.category)}">
                      ${escapeHtml(cat.category)}
                    </span>
                    <span class="px-2 py-0.5 rounded-md text-[10px] uppercase font-bold tracking-wider border ${badgeColor}">
                      ${statusText}
                    </span>
                  </div>

                  <div class="flex items-baseline justify-between mt-2">
                    <div>
                      <p class="text-lg font-bold font-mono text-white">${formatCurrency(cat.spent)}</p>
                      <p class="text-[11px] text-slate-400">Spent</p>
                    </div>
                    <div class="text-right">
                      <p class="text-sm font-semibold font-mono text-slate-300">${formatCurrency(cat.budget)}</p>
                      <p class="text-[11px] text-slate-500">Target Cap</p>
                      ${
                        monthsCount > 1
                          ? `<p class="budget-cap-scaling text-[10px] font-mono text-slate-500">${formatCurrency(monthlyCap(cat))}/mo × ${monthsCount}</p>`
                          : ''
                      }
                    </div>
                  </div>

                  <!-- Progress Bar -->
                  <div class="mt-3">
                    <div class="flex items-center justify-between text-[11px] text-slate-400 mb-1 font-mono">
                      <span>${cat.utilization_pct}% utilized</span>
                      <span>${isOver ? `+${formatCurrency(cat.spent - cat.budget)} over` : `${formatCurrency(cat.remaining)} left`}</span>
                    </div>
                    <div class="w-full bg-slate-950 rounded-full h-2 overflow-hidden border border-slate-800/80">
                      <div class="h-full ${barColor} rounded-full transition-all duration-300" style="width: ${Math.min(100, cat.utilization_pct)}%;"></div>
                    </div>
                  </div>
                </div>

                <!-- Footer Action -->
                <div class="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-end">
                  <button
                    type="button"
                    data-cat="${escapeHtml(cat.category)}"
                    data-budget="${monthlyCap(cat)}"
                    class="adjust-budget-btn text-xs font-semibold text-indigo-400 hover:text-indigo-300 transition-colors flex items-center gap-1 cursor-pointer"
                  >
                    <span>Adjust Target</span>
                    <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                    </svg>
                  </button>
                </div>
              </div>
            `;
            })
            .join('')}
        </div>
      </div>
    `;

    // Bind adjust budget buttons
    body.querySelectorAll('.adjust-budget-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const cat = btn.getAttribute('data-cat');
        const curr = parseFloat(btn.getAttribute('data-budget')) || 0;
        budgetModal.open(cat, curr, async (category, newBudget) => {
          await updateBudget(category, newBudget);
          toast.show(`Budget for ${category} updated to ${formatCurrency(newBudget)}`, 'success');
          await this.loadBudgets();
        });
      });
    });
  }

  setMonth(month) {
    this.selectedMonth = month;
    const select = this.container.querySelector('#budget-month-select');
    if (select) select.value = month;
    this.loadBudgets();
  }
}

export function initBudgetsComponent(container, options = {}) {
  return new BudgetsComponent(container, options);
}
