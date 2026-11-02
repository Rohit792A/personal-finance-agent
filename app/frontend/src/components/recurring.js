/**
 * Future Cost Predictor, Preparedness Planner & Recurring Commitments Component
 * Analyzes Previous Week, Previous Month, and Previous Year (Same Month / Next Month)
 * transactions to predict upcoming expenses and guide proactive financial planning.
 */

import { getRecurringTransactions, getFutureCostForecast } from '../api/index.ts';
import { formatCurrency, formatDate, getCategoryBadgeClass } from '../utils/formatters.js';
import { escapeHtml } from '../utils/dom.js';
import { toast } from './toast.js';

export class RecurringComponent {
  /**
   * @param {HTMLElement} container
   */
  constructor(container) {
    this.container = container;
    this.data = null;
    this.forecast = null;
    this.isLoading = false;

    this.renderScaffold();
    this.bindEvents();
    this.loadRecurring();
  }

  renderScaffold() {
    this.container.innerHTML = `
      <div class="space-y-6">
        <!-- Top Bar -->
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-slate-900 border border-slate-800 border-t-4 border-t-violet-500 rounded-2xl p-6 shadow-xl">
          <div>
            <div class="flex items-center gap-3">
              <div class="p-2.5 rounded-xl bg-violet-500/10 text-violet-400 border border-violet-500/20">
                <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                </svg>
              </div>
              <div>
                <div class="flex items-center gap-2 flex-wrap">
                  <h2 class="text-xl font-bold text-white tracking-tight font-display">Future Cost Predictor &amp; Preparedness Plan</h2>
                  <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-violet-500/15 text-violet-300 border border-violet-500/30">
                    Predictive AI
                  </span>
                </div>
                <p class="text-xs text-slate-400 mt-0.5">
                  Proactive financial planning based on your Previous Week, Previous Month, and Previous Year (Same Month) spending patterns
                </p>
              </div>
            </div>
          </div>

          <button
            type="button"
            id="recurring-refresh-btn"
            class="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-semibold rounded-full bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-600/20 transition-all cursor-pointer shrink-0"
          >
            <svg id="recurring-refresh-icon" class="w-3.5 h-3.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            <span>Recalculate Forecast</span>
          </button>
        </div>

        <!-- Content Body -->
        <div id="recurring-content-body">
          <!-- Rendered via render() -->
        </div>
      </div>
    `;
  }

  bindEvents() {
    const refreshBtn = this.container.querySelector('#recurring-refresh-btn');
    refreshBtn?.addEventListener('click', () => this.loadRecurring());
  }

  async loadRecurring() {
    if (this.isLoading) return;
    const body = this.container.querySelector('#recurring-content-body');
    const refreshIcon = this.container.querySelector('#recurring-refresh-icon');

    try {
      this.isLoading = true;
      refreshIcon?.classList.add('animate-spin');

      if (body) {
        body.innerHTML = `
          <div class="flex flex-col items-center justify-center py-16 bg-slate-900 border border-slate-800 rounded-2xl">
            <div class="w-10 h-10 border-2 border-indigo-500/30 border-t-indigo-400 rounded-full animate-spin mb-3"></div>
            <p class="text-xs text-slate-400 animate-pulse font-medium">Analyzing previous week, previous month, and previous year transactions...</p>
          </div>
        `;
      }

      const [recurringRes, forecastRes] = await Promise.all([
        getRecurringTransactions(),
        getFutureCostForecast().catch((e) => {
          console.warn('[Forecast] Fallback if forecast unavailable:', e);
          return null;
        }),
      ]);
      this.data = recurringRes;
      this.forecast = forecastRes;
      this.render();
    } catch (err) {
      toast.show(`Forecast scan error: ${err.message}`, 'error');
    } finally {
      this.isLoading = false;
      refreshIcon?.classList.remove('animate-spin');
    }
  }

  render() {
    const body = this.container.querySelector('#recurring-content-body');
    if (!body || !this.data) return;

    const { items = [], estimated_monthly_burn = 0, total_recurring_items = 0 } = this.data;
    const fc = this.forecast || {};
    const plan = fc.preparedness_plan || {};
    const horizons = fc.horizons || {};
    const upcoming = fc.upcoming_predictions || [];
    const catForecasts = fc.category_forecasts || [];
    const steps = plan.actionable_steps || [];

    const last7d = horizons.last_7_days || { total_expense: 0, tx_count: 0 };
    const currMonth = horizons.current_month || { label: 'Current Month', total_expense: 0 };
    const prevMonth = horizons.previous_month || { label: 'Previous Month', total_expense: 0 };
    const sameMonthLY = horizons.same_month_last_year || { label: 'Same Month Last Year', total_expense: 0 };
    const nextMonthLY = horizons.next_month_last_year || { label: 'Next Month Last Year', total_expense: 0 };

    body.innerHTML = `
      <div class="space-y-6 animate-fade-in">
        <!-- 1. Top 4 Preparedness KPIs -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg">
            <span class="text-[10px] font-bold uppercase tracking-wider text-emerald-400">Keep Ready (Next 7 Days)</span>
            <p class="text-2xl font-extrabold text-emerald-400 mt-1.5 font-mono tabular-nums">
              +${formatCurrency(plan.next_7_days_buffer || 0)}
            </p>
            <p class="text-[11px] text-slate-400 mt-1">Recommended liquid bank buffer for this week</p>
          </div>

          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg">
            <span class="text-[10px] font-bold uppercase tracking-wider text-amber-400">Remaining ${escapeHtml(currMonth.label)} Forecast</span>
            <p class="text-2xl font-extrabold text-slate-100 mt-1.5 font-mono tabular-nums">
              -${formatCurrency(plan.remaining_month_estimate || 0)}
            </p>
            <p class="text-[11px] text-slate-400 mt-1">Expected additional spend before month-end</p>
          </div>

          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg">
            <span class="text-[10px] font-bold uppercase tracking-wider text-indigo-400">Predicted Next 30-Day Outflow</span>
            <p class="text-2xl font-extrabold text-slate-100 mt-1.5 font-mono tabular-nums">
              -${formatCurrency(plan.next_30_days_forecast || estimated_monthly_burn)}
            </p>
            <p class="text-[11px] text-slate-400 mt-1">Blended MoM + YoY + weekly run-rate model</p>
          </div>

          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg">
            <span class="text-[10px] font-bold uppercase tracking-wider text-violet-400">Future Sinking Fund Target</span>
            <p class="text-2xl font-extrabold text-violet-300 mt-1.5 font-mono tabular-nums">
              +${formatCurrency(plan.weekly_sinking_fund_target || 0)}<span class="text-xs font-normal text-slate-400">/wk</span>
            </p>
            <p class="text-[11px] text-slate-400 mt-1">${formatCurrency(plan.monthly_sinking_fund_target || 0)}/mo for annual &amp; festive spikes</p>
          </div>
        </div>

        <!-- 2. Multi-Horizon Historical Lookback Bar (Prev Week vs Prev Month vs Prev Year) -->
        <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-3 border-b border-slate-800">
            <div>
              <h3 class="text-base font-bold text-white">Historical Baseline Comparison (Week &bull; Month &bull; Previous Year)</h3>
              <p class="text-xs text-slate-400">How your past spending across different time horizons informs your upcoming cost forecast</p>
            </div>
            <span class="text-[11px] font-mono tabular-nums text-slate-400">
              Historical Monthly Avg: <strong class="text-slate-200">-${formatCurrency(horizons.historical_monthly_avg || 0)}</strong>
            </span>
          </div>

          <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            <div class="p-3.5 rounded-xl bg-slate-950/70 border border-slate-800/80">
              <span class="text-[10px] uppercase font-semibold text-slate-400">Previous 7 Days</span>
              <p class="text-base font-bold font-mono tabular-nums text-slate-100 mt-1">-${formatCurrency(last7d.total_expense)}</p>
              <span class="text-[10px] text-slate-500">${last7d.tx_count || 0} transactions</span>
            </div>

            <div class="p-3.5 rounded-xl bg-slate-950/70 border border-slate-800/80">
              <span class="text-[10px] uppercase font-semibold text-indigo-400">This Month (${escapeHtml(currMonth.label)})</span>
              <p class="text-base font-bold font-mono tabular-nums text-slate-100 mt-1">-${formatCurrency(currMonth.total_expense)}</p>
              <span class="text-[10px] text-slate-500">${currMonth.tx_count || 0} txns so far</span>
            </div>

            <div class="p-3.5 rounded-xl bg-slate-950/70 border border-slate-800/80">
              <span class="text-[10px] uppercase font-semibold text-sky-400">Previous Month (${escapeHtml(prevMonth.label)})</span>
              <p class="text-base font-bold font-mono tabular-nums text-slate-100 mt-1">-${formatCurrency(prevMonth.total_expense)}</p>
              <span class="text-[10px] text-slate-500">${prevMonth.tx_count || 0} transactions</span>
            </div>

            <div class="p-3.5 rounded-xl bg-slate-950/70 border border-slate-800/80">
              <span class="text-[10px] uppercase font-semibold text-amber-400">Same Month Last Year (${escapeHtml(sameMonthLY.label)})</span>
              <p class="text-base font-bold font-mono tabular-nums text-slate-100 mt-1">-${formatCurrency(sameMonthLY.total_expense)}</p>
              <span class="text-[10px] text-slate-500">${sameMonthLY.tx_count || 0} transactions</span>
            </div>

            <div class="p-3.5 rounded-xl bg-slate-950/70 border border-slate-800/80">
              <span class="text-[10px] uppercase font-semibold text-violet-400">Next Month Last Year (${escapeHtml(nextMonthLY.label)})</span>
              <p class="text-base font-bold font-mono tabular-nums text-slate-100 mt-1">-${formatCurrency(nextMonthLY.total_expense)}</p>
              <span class="text-[10px] text-slate-500">Upcoming seasonal benchmark</span>
            </div>
          </div>
        </div>

        <!-- 3. Actionable "Plan Ahead" Preparedness Playbook -->
        ${
          steps.length > 0
            ? `
          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
            <div class="pb-3 border-b border-slate-800">
              <h3 class="text-base font-bold text-white">Actionable Financial Preparedness Roadmap</h3>
              <p class="text-xs text-slate-400">Concrete steps to prepare for upcoming weekly, monthly, and annual expenses before they arrive</p>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              ${steps
                .map(
                  (s) => `
                <div class="p-4 rounded-xl bg-slate-950/70 border border-slate-800/90 flex flex-col justify-between gap-2">
                  <div>
                    <span class="inline-block px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 mb-2">
                      ${escapeHtml(s.badge)}
                    </span>
                    <h4 class="text-sm font-bold text-white">${escapeHtml(s.title)}</h4>
                    <p class="text-xs text-slate-300 mt-1 leading-relaxed">${escapeHtml(s.detail)}</p>
                  </div>
                </div>
              `
                )
                .join('')}
            </div>
          </div>
        `
            : ''
        }

        <!-- 4. Predicted Upcoming Bills & Seasonal Expenses -->
        <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
          <div class="flex items-center justify-between pb-3 border-b border-slate-800">
            <div>
              <h3 class="text-base font-bold text-white">Predicted Upcoming Expenses &amp; Bills to Prepare For</h3>
              <p class="text-xs text-slate-400">Detected from unpaid monthly recurring bills, previous month patterns, and last year's seasonal expenses</p>
            </div>
          </div>

          ${
            upcoming.length === 0
              ? `<div class="py-8 text-center text-slate-500 text-xs">No upcoming expenses predicted yet.</div>`
              : `
              <div class="overflow-x-auto rounded-xl border border-slate-800">
                <table class="w-full text-left text-sm text-slate-300 border-collapse">
                  <thead class="bg-slate-950 text-[11px] uppercase tracking-wider font-semibold text-slate-400 border-b border-slate-800">
                    <tr>
                      <th class="py-3.5 px-4">Upcoming Expense / Merchant</th>
                      <th class="py-3.5 px-4">Category</th>
                      <th class="py-3.5 px-4">Forecast Horizon</th>
                      <th class="py-3.5 px-4">Expected Window</th>
                      <th class="py-3.5 px-4">Historical Basis</th>
                      <th class="py-3.5 px-4 text-right">Predicted Cost</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-slate-800/60">
                    ${upcoming
                      .map((u) => {
                        const isUrgent = (u.horizon || '').includes('This Month');
                        const horizonBadge = isUrgent
                          ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                          : (u.horizon || '').includes('Annual')
                          ? 'bg-violet-500/20 text-violet-300 border-violet-500/40'
                          : 'bg-sky-500/15 text-sky-300 border-sky-500/30';
                        return `
                        <tr class="hover:bg-slate-800/30 transition-colors">
                          <td class="py-3.5 px-4 font-semibold text-white max-w-xs truncate" title="${escapeHtml(u.title)}">
                            ${escapeHtml(u.title)}
                          </td>
                          <td class="py-3.5 px-4 whitespace-nowrap">
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${getCategoryBadgeClass(u.category)}">
                              ${escapeHtml(u.category)}
                            </span>
                          </td>
                          <td class="py-3.5 px-4 whitespace-nowrap">
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${horizonBadge}">
                              ${escapeHtml(u.horizon)}
                            </span>
                          </td>
                          <td class="py-3.5 px-4 text-xs text-slate-300 whitespace-nowrap">
                            ${escapeHtml(u.expected_window)}
                          </td>
                          <td class="py-3.5 px-4 text-xs text-slate-400">
                            ${escapeHtml(u.basis)}
                          </td>
                          <td class="py-3.5 px-4 text-right font-mono tabular-nums font-bold text-slate-100 whitespace-nowrap">
                            -${formatCurrency(u.predicted_amount)}
                          </td>
                        </tr>
                      `;
                      })
                      .join('')}
                  </tbody>
                </table>
              </div>
            `
          }
        </div>

        <!-- 5. Category-by-Category Multi-Horizon Forecast Table -->
        ${
          catForecasts.length > 0
            ? `
          <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
            <div class="flex items-center justify-between pb-3 border-b border-slate-800">
              <div>
                <h3 class="text-base font-bold text-white">Category-Wise Future Cost Forecast &amp; Weekly Reserve Plan</h3>
                <p class="text-xs text-slate-400">How much to budget per category based on Previous Week, Previous Month (${escapeHtml(prevMonth.label)}), and Last Year (${escapeHtml(sameMonthLY.label)})</p>
              </div>
            </div>

            <div class="overflow-x-auto rounded-xl border border-slate-800">
              <table class="w-full text-left text-sm text-slate-300 border-collapse">
                <thead class="bg-slate-950 text-[11px] uppercase tracking-wider font-semibold text-slate-400 border-b border-slate-800">
                  <tr>
                    <th class="py-3.5 px-4">Category</th>
                    <th class="py-3.5 px-4 text-right">Prev 7 Days</th>
                    <th class="py-3.5 px-4 text-right">Prev Month</th>
                    <th class="py-3.5 px-4 text-right">Same Month LY</th>
                    <th class="py-3.5 px-4 text-right">Predicted Next 30d</th>
                    <th class="py-3.5 px-4 text-right">Weekly Reserve</th>
                    <th class="py-3.5 px-4 text-center">Signal</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-800/60">
                  ${catForecasts
                    .map((c) => {
                      const sigClass = c.trend_signal.includes('Spike')
                        ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                        : c.trend_signal.includes('Seasonal')
                        ? 'bg-violet-500/20 text-violet-300 border-violet-500/40'
                        : 'bg-slate-800 text-slate-300 border-slate-700';
                      return `
                      <tr class="hover:bg-slate-800/30 transition-colors">
                        <td class="py-3 px-4 whitespace-nowrap">
                          <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${getCategoryBadgeClass(c.category)}">
                            ${escapeHtml(c.category)}
                          </span>
                        </td>
                        <td class="py-3 px-4 text-right font-mono tabular-nums text-xs text-slate-300">
                          -${formatCurrency(c.last_7_days)}
                        </td>
                        <td class="py-3 px-4 text-right font-mono tabular-nums text-xs text-slate-300">
                          -${formatCurrency(c.previous_month)}
                        </td>
                        <td class="py-3 px-4 text-right font-mono tabular-nums text-xs text-slate-400">
                          -${formatCurrency(c.same_month_last_year)}
                        </td>
                        <td class="py-3 px-4 text-right font-mono tabular-nums font-bold text-slate-100">
                          -${formatCurrency(c.predicted_next_30_days)}
                        </td>
                        <td class="py-3 px-4 text-right font-mono tabular-nums text-xs font-semibold text-emerald-400">
                          +${formatCurrency(c.recommended_weekly_reserve)}/wk
                        </td>
                        <td class="py-3 px-4 text-center whitespace-nowrap">
                          <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold border ${sigClass}">
                            ${escapeHtml(c.trend_signal)}
                          </span>
                        </td>
                      </tr>
                    `;
                    })
                    .join('')}
                </tbody>
              </table>
            </div>
          </div>
        `
            : ''
        }

        <!-- 6. Detected Recurring Commitments Ledger -->
        <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
          <div class="flex items-center justify-between pb-3 border-b border-slate-800 flex-wrap gap-2">
            <div>
              <h3 class="text-base font-bold text-white">Detected Recurring Subscriptions &amp; Fixed Commitments (${total_recurring_items})</h3>
              <p class="text-xs text-slate-400">Autonomous pattern matching across transaction history &bull; Committed Run Rate: <strong class="font-mono tabular-nums text-slate-200">-${formatCurrency(estimated_monthly_burn)}/mo</strong></p>
            </div>
          </div>

          ${
            items.length === 0
              ? `<div class="py-12 text-center text-slate-500 text-xs">No recurring subscriptions detected yet.</div>`
              : `
              <div class="overflow-x-auto rounded-xl border border-slate-800">
                <table class="w-full text-left text-sm text-slate-300 border-collapse">
                  <thead class="bg-slate-950 text-[11px] uppercase tracking-wider font-semibold text-slate-400 border-b border-slate-800">
                    <tr>
                      <th class="py-3.5 px-4">Merchant / Description</th>
                      <th class="py-3.5 px-4">Category</th>
                      <th class="py-3.5 px-4 text-right">Avg Charge</th>
                      <th class="py-3.5 px-4 text-center">Frequency</th>
                      <th class="py-3.5 px-4 text-center">First Seen</th>
                      <th class="py-3.5 px-4 text-center">Last Seen</th>
                      <th class="py-3.5 px-4 text-center">Status</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-slate-800/60 font-normal">
                    ${items
                      .map((item) => {
                        return `
                        <tr class="hover:bg-slate-800/30 transition-colors">
                          <td class="py-3.5 px-4 font-semibold text-white">
                            ${escapeHtml(item.description)}
                          </td>
                          <td class="py-3.5 px-4">
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${getCategoryBadgeClass(item.category)}">
                              ${escapeHtml(item.category)}
                            </span>
                          </td>
                          <td class="py-3.5 px-4 text-right font-mono tabular-nums font-bold text-white">
                            -${formatCurrency(item.average_amount)}
                          </td>
                          <td class="py-3.5 px-4 text-center font-mono tabular-nums text-xs text-slate-300">
                            <span class="px-2 py-0.5 rounded-md bg-slate-800 border border-slate-700">
                              ${item.frequency} times
                            </span>
                          </td>
                          <td class="py-3.5 px-4 text-center font-mono tabular-nums text-xs text-slate-400">
                            ${escapeHtml(formatDate(item.first_seen))}
                          </td>
                          <td class="py-3.5 px-4 text-center font-mono tabular-nums text-xs text-slate-400">
                            ${escapeHtml(formatDate(item.last_seen))}
                          </td>
                          <td class="py-3.5 px-4 text-center">
                            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider bg-sky-500/10 text-sky-300 border border-sky-500/20">
                              ${escapeHtml(item.estimated_cadence)}
                            </span>
                          </td>
                        </tr>
                      `;
                      })
                      .join('')}
                  </tbody>
                </table>
              </div>
            `
          }
        </div>
      </div>
    `;
  }
}

export function initRecurringComponent(container) {
  return new RecurringComponent(container);
}

