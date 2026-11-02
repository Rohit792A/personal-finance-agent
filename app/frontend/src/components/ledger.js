/**
 * Transactions Ledger & Progressive Drill-Down Vault Component
 * Implements enterprise FinTech progressive disclosure:
 *   Stage 0 ('home')   : Executive Home Overview & Action Command Center
 *   Stage 1 ('years')  : Select Year Vault (2026, 2025, ...)
 *   Stage 2 ('months') : Select Month Statement Folder within Selected Year (Oct, Sep, Aug, ...)
 *   Stage 3 ('ledger') : Focused Monthly Transactions Ledger (Table + Bento Cards + Voice Agent)
 */

import {
  getTransactions,
  updateTransaction,
  deleteTransaction,
  createTransaction,
  getTransactionMonths,
} from '../api/index.ts';
import {
  formatCurrency,
  formatSignedCurrency,
  formatDate,
  formatMonthYear,
  getCategoryBadgeClass,
  getTypeBadgeClass,
} from '../utils/formatters.js';
import { escapeHtml } from '../utils/dom.js';
import { editModal, addTransactionModal, voiceClarificationModal } from './modal.js';
import { toast } from './toast.js';

export class LedgerComponent {
  /**
   * @param {HTMLElement} container
   * @param {Object} [options]
   * @param {(month: string) => void} [options.onMonthChange]
   * @param {(pageId: string) => void} [options.onNavigatePage]
   * @param {{ stage?: string, year?: string|null, month?: string, search?: string }} [options.initialState]
   *   Where to start (the app shell passes its URL state); defaults to the 'home' overview.
   * @param {boolean} [options.autoSelectLatestMonth] false keeps 'ALL' instead of jumping to the latest month.
   * @param {(stage: string, state: { year: string|null, month: string, search: string }) => void} [options.onStageChange]
   * @param {(query: string) => void} [options.onSearchChange] Raw text typed in the ledger search box.
   */
  constructor(container, options = {}) {
    this.container = container;
    this.options = options;
    const initial = options.initialState || {};

    // Progressive Drill-Down State: 'home' | 'years' | 'months' | 'ledger'
    this.drillStage = initial.stage || 'home';
    this.selectedMonth = initial.month || 'ALL';
    this.selectedYear =
      initial.year || (this.selectedMonth !== 'ALL' && this.selectedMonth.length >= 4 ? this.selectedMonth.slice(0, 4) : null);

    this.transactions = [];
    this.filteredTransactions = [];
    this.months = [];
    this.years = [];
    this.allSummary = { count: 0, income: 0, expense: 0, net: 0, review_count: 0, years_count: 0 };

    this.orderBy = 'date';
    this.orderDir = 'desc';
    this.typeFilter = 'ALL';
    this.categoryFilter = 'ALL';
    this.searchQuery = String(initial.search || '').toLowerCase().trim();
    this.onlyReviewFilter = false;
    this.isLoading = false;
    this.reloadQueued = false;
    this.viewMode = 'table';

    this.renderScaffold();
    if (initial.search) {
      const searchInput = this.container.querySelector('#ledger-search');
      if (searchInput) searchInput.value = initial.search;
    }
    this.bindEvents();
    this.initData();
  }

  async initData() {
    await this.loadMonths();
    this.renderDrillStage();
    if (this.drillStage === 'ledger') {
      await this.loadData();
    }
  }

  /**
   * Builds year-level groupings from `this.months` if backend didn't pre-group them
   */
  buildYearsFromMonths(monthsList, apiYears) {
    if (Array.isArray(apiYears) && apiYears.length > 0) {
      return apiYears;
    }
    const map = new Map();
    (monthsList || []).forEach((m) => {
      const mKey = m.month || '';
      const yr = m.year || (mKey.length >= 4 ? mKey.slice(0, 4) : 'Unknown');
      if (!map.has(yr)) {
        map.set(yr, {
          year: yr,
          months_count: 0,
          count: 0,
          income: 0,
          expense: 0,
          net: 0,
          opening_balance: 0,
          closing_balance: 0,
          review_count: 0,
          months: [],
        });
      }
      const group = map.get(yr);
      group.months_count += 1;
      group.count += Number(m.count || 0);
      group.income += Number(m.income || 0);
      group.expense += Number(m.expense || 0);
      group.net += Number(m.net || 0);
      group.review_count += Number(m.review_count || 0);
      group.months.push({
        ...m,
        year: yr,
        month_name: m.month_name || formatMonthYear(mKey).split(' ')[0] || mKey,
        month_short: m.month_short || (formatMonthYear(mKey).split(' ')[0] || mKey).slice(0, 3),
      });
    });
    for (const group of map.values()) {
      if (group.months.length > 0) {
        const sortedDesc = [...group.months].sort((a, b) => String(b.month || '').localeCompare(String(a.month || '')));
        group.opening_balance = Number(sortedDesc[sortedDesc.length - 1].opening_balance || 0);
        group.closing_balance = Number(sortedDesc[0].closing_balance || 0);
      }
    }
    return Array.from(map.values()).sort((a, b) => b.year.localeCompare(a.year));
  }

  renderScaffold() {
    this.container.innerHTML = `
      <div class="space-y-6">
        <!-- STAGE 0, 1, 2: Progressive Navigation Views (Home -> Years -> Months) -->
        <div id="ledger-drilldown-stage" class="space-y-6"></div>

        <!-- STAGE 3: Focused Monthly Transactions Ledger -->
        <div id="ledger-detail-stage" class="hidden bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
          <!-- Interactive Breadcrumb & Prev/Next Month Stepper Bar -->
          <div class="flex flex-col md:flex-row md:items-center justify-between gap-3 pb-4 border-b border-slate-800/90">
            <nav aria-label="Breadcrumb" class="flex items-center gap-1.5 flex-wrap text-xs">
              <button
                type="button"
                id="bc-home-btn"
                class="px-2.5 py-1 rounded-lg bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-white font-medium transition-colors cursor-pointer flex items-center gap-1"
              >
                <span>🏠</span>
                <span>Home</span>
              </button>
              <span class="text-slate-600">/</span>
              <button
                type="button"
                id="bc-years-btn"
                class="px-2.5 py-1 rounded-lg bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-white font-medium transition-colors cursor-pointer flex items-center gap-1"
              >
                <span>📅</span>
                <span>All Years</span>
              </button>
              <span class="text-slate-600">/</span>
              <button
                type="button"
                id="bc-year-months-btn"
                class="px-2.5 py-1 rounded-lg bg-slate-800/80 hover:bg-slate-700 text-indigo-300 hover:text-white font-semibold transition-colors cursor-pointer flex items-center gap-1"
              >
                <span>📂</span>
                <span id="bc-selected-year-label">2026</span>
              </button>
              <span class="text-slate-600">/</span>
              <span
                id="bc-selected-month-label"
                class="px-3 py-1 rounded-lg bg-indigo-500/20 text-indigo-200 border border-indigo-500/30 font-bold"
              >
                October 2026
              </span>
            </nav>

            <!-- 1-Click Adjacent Month Stepper & Change Month Button -->
            <div class="flex items-center gap-2 flex-wrap">
              <button
                type="button"
                id="prev-month-step-btn"
                class="px-3 py-1 rounded-lg bg-slate-950 hover:bg-slate-800 text-slate-300 border border-slate-800 text-xs font-medium transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                title="Previous Month"
              >
                ◀ Older Month
              </button>
              <button
                type="button"
                id="next-month-step-btn"
                class="px-3 py-1 rounded-lg bg-slate-950 hover:bg-slate-800 text-slate-300 border border-slate-800 text-xs font-medium transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                title="Next Month"
              >
                Newer Month ▶
              </button>
              <button
                type="button"
                id="back-to-months-btn"
                class="px-3 py-1 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 text-xs font-semibold transition-colors cursor-pointer"
              >
                🗓️ Switch Month
              </button>
            </div>
          </div>

          <!-- Header & Top Controls -->
          <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4 pb-5 border-b border-slate-800">
            <div>
              <div class="flex items-center gap-3">
                <div class="p-2.5 rounded-xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                  <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
                  </svg>
                </div>
                <div>
                  <div class="flex items-center gap-2.5 flex-wrap">
                    <h2 id="ledger-title-heading" class="text-xl font-bold text-white tracking-tight font-display">Monthly Statement Ledger</h2>
                    <span id="ledger-month-badge" class="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
                      Select Month
                    </span>
                  </div>
                  <p class="text-xs text-slate-400 mt-0.5">Focused statement view with AI categorization, voice clarification, and instant search</p>
                </div>
              </div>
            </div>

            <!-- Quick Action Buttons & View Mode Switcher -->
            <div class="flex items-center gap-2 flex-wrap">
              <div class="inline-flex items-center p-1 rounded-full bg-slate-950/90 border border-slate-800 text-xs">
                <button
                  type="button"
                  id="view-mode-table-btn"
                  class="px-3 py-1 rounded-full font-semibold bg-indigo-600 text-white transition-all cursor-pointer flex items-center gap-1"
                  title="High-density Table View"
                >
                  <span>📋</span>
                  <span>Table</span>
                </button>
                <button
                  type="button"
                  id="view-mode-bento-btn"
                  class="px-3 py-1 rounded-full font-semibold text-slate-400 hover:text-white transition-all cursor-pointer flex items-center gap-1"
                  title="Playground Bento Cards View"
                >
                  <span>🎴</span>
                  <span>Bento Cards</span>
                </button>
              </div>

              <button
                type="button"
                id="add-tx-btn"
                class="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-full bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-600/20 transition-all cursor-pointer"
                title="Add a manual transaction"
              >
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
                </svg>
                <span>Add Entry</span>
              </button>

              <button
                type="button"
                id="export-csv-btn"
                class="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-full bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors cursor-pointer"
                title="Export current filtered view as CSV"
              >
                <svg class="w-4 h-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                <span>CSV</span>
              </button>

              <button
                type="button"
                id="ledger-refresh-btn"
                title="Refresh ledger"
                class="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-full bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors cursor-pointer"
              >
                <svg id="refresh-icon" class="w-3.5 h-3.5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                <span>Refresh</span>
              </button>
            </div>
          </div>

          <!-- Quick Metrics KPI Ribbon (Transactions, Start of Month Balance, Inflow, Outflow, Net Balance, End of Month Balance) -->
          <div class="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3.5" id="ledger-stats">
            <div class="bg-slate-950/70 border border-slate-800/80 p-3.5 rounded-xl">
              <span class="text-[10px] uppercase font-semibold text-slate-400 tracking-wider">Transactions</span>
              <p id="stat-count" class="text-xl font-bold text-white mt-1 tabular-nums">0</p>
              <span class="text-[10px] text-slate-500">In selected period</span>
            </div>

            <div class="bg-slate-950/70 border border-slate-800/80 p-3.5 rounded-xl">
              <span class="text-[10px] uppercase font-semibold text-sky-400 tracking-wider">Start of Month Balance</span>
              <p id="stat-opening-balance" class="text-xl font-bold text-slate-100 mt-1 tabular-nums">₹0.00</p>
              <span id="stat-opening-sub" class="text-[10px] text-slate-400">Opening balance (carry-forward)</span>
            </div>

            <div class="bg-slate-950/70 border border-slate-800/80 p-3.5 rounded-xl">
              <span class="text-[10px] uppercase font-semibold text-emerald-400 tracking-wider">Total Inflow</span>
              <p id="stat-income" class="text-xl font-bold text-emerald-400 mt-1 tabular-nums">+₹0.00</p>
              <span class="text-[10px] text-emerald-500/80">Credits & Income</span>
            </div>

            <div class="bg-slate-950/70 border border-slate-800/80 p-3.5 rounded-xl">
              <span class="text-[10px] uppercase font-semibold text-slate-300 tracking-wider">Total Outflow</span>
              <p id="stat-expense" class="text-xl font-bold text-slate-100 mt-1 tabular-nums">-₹0.00</p>
              <span class="text-[10px] text-slate-400">Expenses & Purchases</span>
            </div>

            <div class="bg-slate-950/70 border border-slate-800/80 p-3.5 rounded-xl">
              <span class="text-[10px] uppercase font-semibold text-indigo-400 tracking-wider">Net Balance</span>
              <p id="stat-net" class="text-xl font-bold text-white mt-1 tabular-nums">+₹0.00</p>
              <span id="stat-savings-rate" class="text-[10px] text-indigo-400 font-medium">0% saved</span>
            </div>

            <div class="bg-slate-950/70 border border-indigo-500/30 p-3.5 rounded-xl">
              <span class="text-[10px] uppercase font-semibold text-amber-400 tracking-wider">End of Month Balance</span>
              <p id="stat-closing-balance" class="text-xl font-bold text-white mt-1 tabular-nums">₹0.00</p>
              <span id="stat-closing-sub" class="text-[10px] text-slate-400">Closing balance (start + net)</span>
            </div>
          </div>

          <!-- Filter & Control Bar -->
          <div class="bg-slate-950/60 p-4 rounded-xl border border-slate-800 space-y-3">
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-12 gap-3 items-center">
              <!-- 1. Search Bar -->
              <div class="lg:col-span-4 relative">
                <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-400 pointer-events-none">
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                </span>
                <input
                  type="text"
                  id="ledger-search"
                  placeholder="Search merchant, notes, category..."
                  class="w-full pl-9 pr-4 py-2 text-xs bg-slate-900 border border-slate-700/80 rounded-xl text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-all"
                />
              </div>

              <!-- 2. Month Filter Dropdown -->
              <div class="lg:col-span-3">
                <div class="flex items-center gap-1.5">
                  <label for="ledger-month-select" class="text-[11px] font-semibold text-slate-400 whitespace-nowrap">Month:</label>
                  <select
                    id="ledger-month-select"
                    class="w-full px-3 py-2 text-xs bg-slate-900 border border-slate-700/80 rounded-xl text-indigo-300 font-medium focus:outline-none focus:ring-2 focus:ring-indigo-500 cursor-pointer"
                  >
                    <option value="ALL">All Available Months</option>
                  </select>
                </div>
              </div>

              <!-- 3. Order By Dropdown -->
              <div class="lg:col-span-3">
                <div class="flex items-center gap-1.5">
                  <label for="ledger-order-by" class="text-[11px] font-semibold text-slate-400 whitespace-nowrap">Sort By:</label>
                  <select
                    id="ledger-order-by"
                    class="w-full px-3 py-2 text-xs bg-slate-900 border border-slate-700/80 rounded-xl text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500 cursor-pointer"
                  >
                    <option value="date:desc">Date (Newest First)</option>
                    <option value="date:asc">Date (Oldest First)</option>
                    <option value="amount:desc">Amount (Highest First)</option>
                    <option value="amount:asc">Amount (Lowest First)</option>
                    <option value="description:asc">Description (A to Z)</option>
                    <option value="category:asc">Category (A to Z)</option>
                    <option value="confidence:desc">AI Confidence (Highest)</option>
                  </select>
                </div>
              </div>

              <!-- 4. Type Filter -->
              <div class="lg:col-span-2">
                <select
                  id="ledger-type-filter"
                  class="w-full px-3 py-2 text-xs bg-slate-900 border border-slate-700/80 rounded-xl text-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-500 cursor-pointer"
                >
                  <option value="ALL">All Types</option>
                  <option value="EXPENSE">Expenses Only</option>
                  <option value="INCOME">Income Only</option>
                  <option value="TRANSFER">Transfers</option>
                </select>
              </div>
            </div>
          </div>

          <!-- Clarification Alert Banner -->
          <div id="ledger-review-banner" class="hidden p-3.5 bg-gradient-to-r from-amber-950/40 via-amber-900/30 to-slate-900/40 border border-amber-500/40 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs shadow-lg shadow-amber-500/5">
            <div class="flex items-center gap-3">
              <div class="p-2 rounded-lg bg-amber-500/20 text-amber-400 border border-amber-500/30 animate-pulse">
                <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <div>
                <span class="font-bold text-amber-200" id="review-banner-count">0 transactions require clarification</span>
                <p class="text-slate-400 text-[11px] mt-0.5">The AI flagged uncertain transactions. Click "Clarify" to speak or type the category.</p>
              </div>
            </div>
            <div class="flex items-center gap-2 self-start sm:self-auto">
              <button id="start-voice-clarify-btn" type="button" class="px-3 py-1.5 rounded-lg bg-amber-500 text-slate-950 hover:bg-amber-400 font-bold text-xs whitespace-nowrap transition-colors cursor-pointer flex items-center gap-1.5 shadow-md shadow-amber-500/20" title="Open AI Voice Agent for first flagged transaction">
                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                </svg>
                <span>Clarify with Voice Agent</span>
              </button>
              <button id="filter-review-btn" type="button" class="px-3 py-1.5 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 border border-amber-500/40 font-semibold text-xs whitespace-nowrap transition-colors cursor-pointer">
                Show Flagged Only
              </button>
            </div>
          </div>

          <!-- Table Container -->
          <div id="ledger-table-wrapper" class="relative overflow-x-auto rounded-2xl border border-slate-800 bg-slate-950/40 shadow-inner">
            <table class="w-full text-left text-sm text-slate-300 border-collapse">
              <thead class="bg-slate-900/90 text-[11px] uppercase tracking-wider font-semibold text-slate-400 border-b border-slate-800 select-none">
                <tr>
                  <th scope="col" class="py-3.5 px-4 cursor-pointer hover:text-indigo-400 transition-colors" data-sort="date">
                    <div class="flex items-center gap-1.5">
                      <span>Date</span>
                      <span id="sort-icon-date" class="text-xs text-indigo-400">▼</span>
                    </div>
                  </th>
                  <th scope="col" class="py-3.5 px-4 cursor-pointer hover:text-indigo-400 transition-colors" data-sort="description">
                    <div class="flex items-center gap-1.5">
                      <span>Description / Merchant</span>
                      <span id="sort-icon-description" class="text-xs text-slate-600">↕</span>
                    </div>
                  </th>
                  <th scope="col" class="py-3.5 px-4 cursor-pointer hover:text-indigo-400 transition-colors" data-sort="category">
                    <div class="flex items-center gap-1.5">
                      <span>Category</span>
                      <span id="sort-icon-category" class="text-xs text-slate-600">↕</span>
                    </div>
                  </th>
                  <th scope="col" class="py-3.5 px-4 text-right cursor-pointer hover:text-indigo-400 transition-colors" data-sort="amount">
                    <div class="flex items-center justify-end gap-1.5">
                      <span>Amount</span>
                      <span id="sort-icon-amount" class="text-xs text-slate-600">↕</span>
                    </div>
                  </th>
                  <th scope="col" class="py-3.5 px-4 text-center">Type</th>
                  <th scope="col" class="py-3.5 px-4 text-center">Actions</th>
                </tr>
              </thead>
              <tbody id="ledger-table-body" class="divide-y divide-slate-800/60 font-normal">
                <!-- Dynamically populated rows -->
              </tbody>
            </table>
          </div>

          <!-- Playground Bento Cards Grid Container -->
          <div id="ledger-bento-grid" class="hidden grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <!-- Dynamically populated interactive Bento transaction cards -->
          </div>

          <!-- Footer / Record Summary -->
          <div class="flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-slate-500 pt-1">
            <span id="ledger-count-summary">Showing 0 transactions</span>
            <div class="flex items-center gap-3">
              <span class="inline-flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full bg-emerald-500"></span> Live Synced
              </span>
              <span>Fast Real-time PostgreSQL Ledger</span>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  /**
   * Transitions between 'home' -> 'years' -> 'months' -> 'ledger'
   */
  navigateToStage(stage, { year, month, search, onlyReview } = {}) {
    if (year !== undefined) {
      this.selectedYear = year;
    }
    if (month !== undefined) {
      this.selectedMonth = month;
      if (month !== 'ALL' && month.length >= 4) {
        this.selectedYear = month.slice(0, 4);
      }
    }
    if (search !== undefined) {
      // Filtering compares lower-cased text (like the search box handler), so normalise here too.
      this.searchQuery = String(search).toLowerCase().trim();
      const searchInput = this.container.querySelector('#ledger-search');
      if (searchInput) searchInput.value = search;
    }
    if (onlyReview !== undefined) {
      this.onlyReviewFilter = Boolean(onlyReview);
    }

    this.drillStage = stage;
    this.renderDrillStage();

    if (stage === 'ledger') {
      const monthSelect = this.container.querySelector('#ledger-month-select');
      if (monthSelect) {
        monthSelect.value = this.selectedMonth;
      }
      this.updateBreadcrumbAndStepper();
      this.loadData();
      if (typeof this.options.onMonthChange === 'function') {
        this.options.onMonthChange(this.selectedMonth);
      }
    }

    if (typeof this.options.onStageChange === 'function') {
      const searchInput = this.container.querySelector('#ledger-search');
      this.options.onStageChange(stage, {
        year: this.selectedYear,
        month: this.selectedMonth,
        search: searchInput ? searchInput.value : this.searchQuery,
      });
    }
  }

  updateBreadcrumbAndStepper() {
    const yearLabelEl = this.container.querySelector('#bc-selected-year-label');
    const monthLabelEl = this.container.querySelector('#bc-selected-month-label');
    const monthBadge = this.container.querySelector('#ledger-month-badge');
    const headingEl = this.container.querySelector('#ledger-title-heading');
    const prevBtn = this.container.querySelector('#prev-month-step-btn');
    const nextBtn = this.container.querySelector('#next-month-step-btn');

    const yr =
      this.selectedYear ||
      (this.selectedMonth && this.selectedMonth !== 'ALL' ? this.selectedMonth.slice(0, 4) : this.years[0]?.year || 'All');

    const foundMonth = this.months.find((m) => m.month === this.selectedMonth);
    const displayLabel = foundMonth
      ? foundMonth.label
      : this.selectedMonth === 'ALL'
      ? 'All Months (Global View)'
      : formatMonthYear(this.selectedMonth);

    if (yearLabelEl) yearLabelEl.textContent = yr;
    if (monthLabelEl) monthLabelEl.textContent = displayLabel;
    if (monthBadge) monthBadge.textContent = displayLabel;
    if (headingEl) {
      headingEl.textContent =
        this.selectedMonth === 'ALL' ? 'All-Time Transactions Ledger' : `${displayLabel} Transactions`;
    }

    // `this.months` is sorted newest first (index 0 = newest, index N = oldest)
    const idx = this.months.findIndex((m) => m.month === this.selectedMonth);
    if (prevBtn) {
      const olderMonth = idx >= 0 && idx + 1 < this.months.length ? this.months[idx + 1] : null;
      prevBtn.disabled = !olderMonth;
      prevBtn.textContent = olderMonth ? `◀ ${olderMonth.month_short || ''} ${olderMonth.year || ''}` : '◀ Older';
      prevBtn.dataset.targetMonth = olderMonth ? olderMonth.month : '';
    }
    if (nextBtn) {
      const newerMonth = idx > 0 ? this.months[idx - 1] : null;
      nextBtn.disabled = !newerMonth;
      nextBtn.textContent = newerMonth ? `${newerMonth.month_short || ''} ${newerMonth.year || ''} ▶` : 'Newer ▶';
      nextBtn.dataset.targetMonth = newerMonth ? newerMonth.month : '';
    }
  }

  /**
   * Renders Stage 0 ('home'), Stage 1 ('years'), or Stage 2 ('months'),
   * or toggles visibility of Stage 3 ('ledger').
   */
  renderDrillStage() {
    const drillContainer = this.container.querySelector('#ledger-drilldown-stage');
    const detailContainer = this.container.querySelector('#ledger-detail-stage');
    if (!drillContainer || !detailContainer) return;

    if (this.drillStage === 'ledger') {
      drillContainer.classList.add('hidden');
      detailContainer.classList.remove('hidden');
      this.updateBreadcrumbAndStepper();
      return;
    }

    detailContainer.classList.add('hidden');
    drillContainer.classList.remove('hidden');

    if (this.drillStage === 'home') {
      drillContainer.innerHTML = this.buildHomeStageHtml();
    } else if (this.drillStage === 'years') {
      drillContainer.innerHTML = this.buildYearsStageHtml();
    } else if (this.drillStage === 'months') {
      drillContainer.innerHTML = this.buildMonthsStageHtml();
    }

    this.bindDrillStageEvents(drillContainer);
  }

  /**
   * STAGE 0: Executive Home Command Center
   */
  buildHomeStageHtml() {
    const latestMonth = this.months[0] || null;
    const latestYear = this.years[0]?.year || new Date().getFullYear().toString();
    const totalTx = this.allSummary.count || 0;
    const totalIncome = this.allSummary.income || 0;
    const totalExpense = this.allSummary.expense || 0;
    const totalNet = totalIncome - totalExpense;
    const savingsPct = totalIncome > 0 ? Math.round((totalNet / totalIncome) * 100) : 0;
    const totalReview = this.allSummary.review_count || 0;

    return `
      <div class="bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8 shadow-2xl space-y-7 relative overflow-hidden">
        <!-- Executive Hero Banner -->
        <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-6 pb-6 border-b border-slate-800/90">
          <div class="space-y-2 max-w-2xl">
            <div class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-500/10 border border-indigo-500/25 text-indigo-300 text-xs font-semibold">
              <span>🏠</span>
              <span>Executive Financial Command Center</span>
            </div>
            <h1 class="text-2xl sm:text-3xl font-extrabold text-white tracking-tight font-display">
              Your Financial Vault &amp; Statement Navigator
            </h1>
            <p class="text-xs sm:text-sm text-slate-400 leading-relaxed">
              Navigate your finances cleanly by <strong class="text-slate-200">Year &rarr; Month &rarr; Statement Transactions</strong>, or jump straight to your latest month or AI forecasting tools below.
            </p>
          </div>

          <!-- Top-Right Global Search Escape Hatch -->
          <form id="home-global-search-form" class="w-full lg:w-80 space-y-1.5">
            <label for="home-global-search-input" class="block text-[11px] font-semibold text-slate-400">
              Quick Global Search (Across All Years)
            </label>
            <div class="flex items-center gap-1.5">
              <input
                type="text"
                id="home-global-search-input"
                placeholder="e.g. Swiggy, SIP, Rent, SBI..."
                class="w-full px-3.5 py-2 text-xs bg-slate-950 border border-slate-700/80 rounded-xl text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <button
                type="submit"
                class="px-3.5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold transition-colors cursor-pointer shrink-0"
              >
                Search
              </button>
            </div>
          </form>
        </div>

        <!-- Primary Navigation CTA Cards (Step-by-Step Drilldown vs 1-Click Current Month) -->
        <div class="grid grid-cols-1 lg:grid-cols-12 gap-5">
          <!-- PRIMARY CTA: Browse Transactions by Year -> Month -->
          <div
            data-drill-action="open-years"
            class="lg:col-span-7 group cursor-pointer rounded-2xl p-6 bg-gradient-to-br from-indigo-950/70 via-slate-900 to-slate-950 border-2 border-indigo-500/40 hover:border-indigo-400 shadow-xl transition-all flex flex-col justify-between gap-5"
          >
            <div class="space-y-3">
              <div class="flex items-center justify-between gap-2">
                <span class="px-3 py-1 rounded-full text-xs font-bold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                  📁 Structured Vault Navigation
                </span>
                <span class="text-xs font-mono text-slate-400">
                  ${this.years.length} Year(s) &bull; ${this.months.length} Month(s) &bull; ${totalTx} Txns
                </span>
              </div>

              <h2 class="text-xl sm:text-2xl font-bold text-white group-hover:text-indigo-200 transition-colors flex items-center gap-2">
                <span>Explore Transactions by Year &amp; Month</span>
                <span class="group-hover:translate-x-1 transition-transform">&rarr;</span>
              </h2>

              <p class="text-xs sm:text-sm text-slate-300 leading-relaxed">
                Step 1: Select a Financial Year (<strong class="text-white">${escapeHtml(this.years.map((y) => y.year).join(', ') || latestYear)}</strong>) &rarr;
                Step 2: Select a Month &rarr;
                Step 3: Inspect &amp; audit that month's transactions without clutter.
              </p>
            </div>

            <div class="flex flex-wrap items-center justify-between gap-3 pt-4 border-t border-indigo-500/20">
              <div class="flex items-center gap-2 flex-wrap">
                ${this.years
                  .slice(0, 4)
                  .map(
                    (y) => `
                  <span class="px-2.5 py-1 rounded-lg bg-slate-950/90 border border-slate-700 text-xs font-mono font-semibold text-indigo-300">
                    📅 ${escapeHtml(y.year)} (${y.months_count}m)
                  </span>
                `
                  )
                  .join('')}
              </div>

              <span class="px-4 py-2 rounded-xl bg-indigo-600 group-hover:bg-indigo-500 text-white text-xs font-bold shadow-lg shadow-indigo-600/30 transition-all">
                Select Year to Begin &rarr;
              </span>
            </div>
          </div>

          <!-- SECONDARY CTA: 1-Click Latest Month Pulse + Action Queue -->
          <div class="lg:col-span-5 flex flex-col justify-between gap-4">
            ${
              latestMonth
                ? `
              <div
                data-drill-action="open-month"
                data-month="${escapeHtml(latestMonth.month)}"
                class="group cursor-pointer rounded-2xl p-5 bg-slate-950/80 hover:bg-slate-950 border border-slate-800 hover:border-emerald-500/50 transition-all space-y-3"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                    ⚡ 1-Click Shortcut &bull; Latest Month
                  </span>
                  <span class="text-xs font-mono text-slate-400">${latestMonth.count} txns</span>
                </div>

                <div class="flex items-baseline justify-between gap-2">
                  <h3 class="text-lg font-bold text-white group-hover:text-emerald-300 transition-colors">
                    🗓️ ${escapeHtml(latestMonth.label)}
                  </h3>
                  <span class="text-xs font-semibold text-emerald-400 group-hover:translate-x-0.5 transition-transform">
                    Open Month &rarr;
                  </span>
                </div>

                <div class="grid grid-cols-3 gap-2 pt-1 text-xs">
                  <div class="p-2 rounded-lg bg-slate-900/90 border border-slate-800">
                    <span class="text-[10px] text-slate-400 block">Inflow</span>
                    <span class="font-mono font-bold text-emerald-400 tabular-nums">+${formatCurrency(latestMonth.income)}</span>
                  </div>
                  <div class="p-2 rounded-lg bg-slate-900/90 border border-slate-800">
                    <span class="text-[10px] text-slate-400 block">Outflow</span>
                    <span class="font-mono font-bold text-slate-100 tabular-nums">-${formatCurrency(latestMonth.expense)}</span>
                  </div>
                  <div class="p-2 rounded-lg bg-slate-900/90 border border-slate-800">
                    <span class="text-[10px] text-slate-400 block">Net</span>
                    <span class="font-mono font-bold ${latestMonth.net >= 0 ? 'text-emerald-400' : 'text-rose-400'} tabular-nums">
                      ${latestMonth.net >= 0 ? '+' : '-'}${formatCurrency(Math.abs(latestMonth.net))}
                    </span>
                  </div>
                </div>

                <div class="grid grid-cols-2 gap-2 text-xs">
                  <div class="p-2 rounded-lg bg-slate-900/70 border border-slate-800/80">
                    <span class="text-[10px] text-sky-400 font-semibold block">Start of Month Balance</span>
                    <span class="font-mono font-bold ${Number(latestMonth.opening_balance || 0) >= 0 ? 'text-slate-100' : 'text-rose-400'} tabular-nums">
                      ${Number(latestMonth.opening_balance || 0) >= 0 ? '+' : '-'}${formatCurrency(Math.abs(Number(latestMonth.opening_balance || 0)))}
                    </span>
                  </div>
                  <div class="p-2 rounded-lg bg-slate-900/70 border border-indigo-500/30">
                    <span class="text-[10px] text-amber-400 font-semibold block">End of Month Balance</span>
                    <span class="font-mono font-bold ${Number(latestMonth.closing_balance || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'} tabular-nums">
                      ${Number(latestMonth.closing_balance || 0) >= 0 ? '+' : '-'}${formatCurrency(Math.abs(Number(latestMonth.closing_balance || 0)))}
                    </span>
                  </div>
                </div>
              </div>
            `
                : ''
            }

            <!-- Flagged Review Queue or Manual Add Card -->
            <div class="rounded-2xl p-5 bg-slate-950/80 border ${
              totalReview > 0 ? 'border-amber-500/40' : 'border-slate-800'
            } flex items-center justify-between gap-4">
              <div class="space-y-1">
                <div class="flex items-center gap-2">
                  <span class="text-sm font-bold ${totalReview > 0 ? 'text-amber-300' : 'text-slate-200'}">
                    ${
                      totalReview > 0
                        ? `⚠️ ${totalReview} Transaction(s) Need Your Review`
                        : '✅ All Transactions Clarified & Verified'
                    }
                  </span>
                </div>
                <p class="text-xs text-slate-400">
                  ${
                    totalReview > 0
                      ? 'Use the AI Voice Agent or 1-click chips to classify uncertain transactions.'
                      : 'Add a manual cash/UPI entry or sync your latest bank emails anytime.'
                  }
                </p>
              </div>

              ${
                totalReview > 0
                  ? `<button
                      type="button"
                      data-drill-action="open-flagged-all"
                      class="px-3.5 py-2 rounded-xl bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold text-xs whitespace-nowrap transition-colors cursor-pointer shrink-0"
                    >
                      🎙️ Review Now
                    </button>`
                  : `<button
                      type="button"
                      data-drill-action="add-manual-tx"
                      class="px-3.5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs whitespace-nowrap transition-colors cursor-pointer shrink-0"
                    >
                      + Add Entry
                    </button>`
              }
            </div>
          </div>
        </div>

        <!-- High-Level All-Time Portfolio Snapshot Ribbon -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3.5">
          <div class="bg-slate-950/60 border border-slate-800/80 p-4 rounded-2xl">
            <span class="text-[10px] uppercase font-semibold text-slate-400 tracking-wider">Historical Coverage</span>
            <p class="text-lg font-bold text-white mt-1 tabular-nums">${this.years.length} Years &bull; ${this.months.length} Months</p>
            <span class="text-[11px] text-slate-500">${totalTx} total verified records</span>
          </div>

          <div class="bg-slate-950/60 border border-slate-800/80 p-4 rounded-2xl">
            <span class="text-[10px] uppercase font-semibold text-emerald-400 tracking-wider">Cumulative Inflow</span>
            <p class="text-lg font-bold text-emerald-400 mt-1 tabular-nums">+${formatCurrency(totalIncome)}</p>
            <span class="text-[11px] text-emerald-500/80">All-time recorded credits</span>
          </div>

          <div class="bg-slate-950/60 border border-slate-800/80 p-4 rounded-2xl">
            <span class="text-[10px] uppercase font-semibold text-slate-300 tracking-wider">Cumulative Outflow</span>
            <p class="text-lg font-bold text-slate-100 mt-1 tabular-nums">-${formatCurrency(totalExpense)}</p>
            <span class="text-[11px] text-slate-400">Excludes self-transfers</span>
          </div>

          <div class="bg-slate-950/60 border border-slate-800/80 p-4 rounded-2xl">
            <span class="text-[10px] uppercase font-semibold text-indigo-400 tracking-wider">Cumulative Net Savings</span>
            <p class="text-lg font-bold ${totalNet >= 0 ? 'text-emerald-400' : 'text-rose-400'} mt-1 tabular-nums">
              ${totalNet >= 0 ? '+' : '-'}${formatCurrency(Math.abs(totalNet))}
            </p>
            <span class="text-[11px] text-indigo-400 font-medium">${savingsPct}% lifetime savings rate</span>
          </div>
        </div>

        <!-- Quick Module Launchpad Tiles -->
        <div class="pt-2 border-t border-slate-800/80">
          <div class="flex items-center justify-between mb-3">
            <span class="text-xs font-bold uppercase tracking-wider text-slate-400">Financial Intelligence Modules</span>
            <button
              type="button"
              data-drill-action="add-manual-tx"
              class="text-xs font-semibold text-emerald-400 hover:text-emerald-300 cursor-pointer"
            >
              + Record Manual Transaction
            </button>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
            <button
              type="button"
              data-nav-page="page-recurring"
              class="p-4 rounded-2xl bg-slate-950/70 hover:bg-slate-950 border border-slate-800 hover:border-violet-500/40 text-left transition-all cursor-pointer group"
            >
              <div class="text-lg mb-1">🔮</div>
              <div class="text-xs font-bold text-white group-hover:text-violet-300">Future Cost Predictor</div>
              <p class="text-[11px] text-slate-400 mt-0.5">7-Day buffer, 30-Day forecast &amp; YoY seasonal spikes</p>
            </button>

            <button
              type="button"
              data-nav-page="page-bank"
              class="p-4 rounded-2xl bg-slate-950/70 hover:bg-slate-950 border border-slate-800 hover:border-emerald-500/40 text-left transition-all cursor-pointer group"
            >
              <div class="text-lg mb-1">🏦</div>
              <div class="text-xs font-bold text-white group-hover:text-emerald-300">Live Bank Sync &amp; PDFs</div>
              <p class="text-[11px] text-slate-400 mt-0.5">Gmail alerts &amp; monthly HDFC, Kotak, SBI statements</p>
            </button>

            <button
              type="button"
              data-nav-page="page-summary"
              class="p-4 rounded-2xl bg-slate-950/70 hover:bg-slate-950 border border-slate-800 hover:border-indigo-500/40 text-left transition-all cursor-pointer group"
            >
              <div class="text-lg mb-1">📊</div>
              <div class="text-xs font-bold text-white group-hover:text-indigo-300">Visual Analytics</div>
              <p class="text-[11px] text-slate-400 mt-0.5">Category breakdown charts &amp; monthly burn trends</p>
            </button>

            <button
              type="button"
              data-nav-page="page-advisor"
              class="p-4 rounded-2xl bg-slate-950/70 hover:bg-slate-950 border border-slate-800 hover:border-amber-500/40 text-left transition-all cursor-pointer group"
            >
              <div class="text-lg mb-1">✨</div>
              <div class="text-xs font-bold text-white group-hover:text-amber-300">AI CFO &amp; Statement Upload</div>
              <p class="text-[11px] text-slate-400 mt-0.5">Ask Gemini 2.5 questions or upload PDF/Excel statements</p>
            </button>
          </div>
        </div>
      </div>
    `;
  }

  /**
   * STAGE 1: Select Year Vault ('years')
   */
  buildYearsStageHtml() {
    return `
      <div class="bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8 shadow-2xl space-y-6">
        <!-- Breadcrumb & Header -->
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-5 border-b border-slate-800">
          <div class="space-y-1.5">
            <nav aria-label="Breadcrumb" class="flex items-center gap-1.5 text-xs">
              <button
                type="button"
                data-drill-action="open-home"
                class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white font-medium transition-colors cursor-pointer"
              >
                🏠 Home Overview
              </button>
              <span class="text-slate-600">/</span>
              <span class="px-2.5 py-1 rounded-lg bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-bold">
                📅 Step 1: Select Financial Year
              </span>
            </nav>
            <h2 class="text-xl sm:text-2xl font-bold text-white tracking-tight font-display pt-1">
              Select a Year to View Monthly Statements
            </h2>
            <p class="text-xs text-slate-400">
              Choose a year below to open its monthly folders, or search across all years.
            </p>
          </div>

          <div class="flex items-center gap-2">
            <button
              type="button"
              data-drill-action="open-home"
              class="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold border border-slate-700 transition-colors cursor-pointer"
            >
              &larr; Back to Home
            </button>
          </div>
        </div>

        <!-- Year Cards Grid -->
        ${
          this.years.length === 0
            ? `
          <div class="p-10 text-center border border-dashed border-slate-800 rounded-2xl space-y-2">
            <p class="text-sm font-semibold text-slate-300">No transaction years available yet</p>
            <p class="text-xs text-slate-500">Sync your Gmail bank statements or upload a PDF/Excel statement to populate your vault.</p>
          </div>
        `
            : `
          <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
            ${this.years
              .map((yr) => {
                const net = yr.income - yr.expense;
                const savingsRate = yr.income > 0 ? Math.round((net / yr.income) * 100) : 0;
                const monthPills = (yr.months || [])
                  .map(
                    (m) => `
                  <span class="px-2.5 py-1 rounded-lg bg-slate-900 border border-slate-800 text-[11px] font-mono text-slate-300">
                    ${escapeHtml(m.month_short || m.label)} (${m.count})
                  </span>
                `
                  )
                  .join('');

                return `
                <div
                  data-drill-action="select-year"
                  data-year="${escapeHtml(yr.year)}"
                  class="group cursor-pointer rounded-2xl p-6 bg-slate-950/80 hover:bg-slate-950 border-2 border-slate-800 hover:border-indigo-500/60 shadow-lg transition-all flex flex-col justify-between gap-5"
                >
                  <div class="space-y-4">
                    <div class="flex items-center justify-between gap-2">
                      <div class="flex items-center gap-3">
                        <div class="w-12 h-12 rounded-2xl bg-indigo-500/15 border border-indigo-500/30 flex items-center justify-center text-xl font-bold text-indigo-300">
                          📅
                        </div>
                        <div>
                          <h3 class="text-2xl font-extrabold text-white group-hover:text-indigo-300 transition-colors font-display">
                            Year ${escapeHtml(yr.year)}
                          </h3>
                          <p class="text-xs text-slate-400">
                            ${yr.months_count} Active Month${yr.months_count > 1 ? 's' : ''} &bull; ${yr.count} Transactions
                          </p>
                        </div>
                      </div>

                      ${
                        yr.review_count > 0
                          ? `<span class="px-2.5 py-1 rounded-full text-[11px] font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30">
                              ⚠️ ${yr.review_count} to review
                            </span>`
                          : `<span class="px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                              ✅ Verified
                            </span>`
                      }
                    </div>

                    <!-- Year Financial Summary -->
                    <div class="grid grid-cols-3 gap-3 pt-1">
                      <div class="p-3 rounded-xl bg-slate-900/90 border border-slate-800/90">
                        <span class="text-[10px] uppercase font-semibold text-emerald-400 block">Inflow</span>
                        <span class="text-sm font-mono font-bold text-emerald-400 tabular-nums mt-0.5 block">
                          +${formatCurrency(yr.income)}
                        </span>
                      </div>
                      <div class="p-3 rounded-xl bg-slate-900/90 border border-slate-800/90">
                        <span class="text-[10px] uppercase font-semibold text-slate-300 block">Outflow</span>
                        <span class="text-sm font-mono font-bold text-slate-100 tabular-nums mt-0.5 block">
                          -${formatCurrency(yr.expense)}
                        </span>
                      </div>
                      <div class="p-3 rounded-xl bg-slate-900/90 border border-slate-800/90">
                        <span class="text-[10px] uppercase font-semibold text-indigo-400 block">Net (${savingsRate}%)</span>
                        <span class="text-sm font-mono font-bold ${
                          net >= 0 ? 'text-emerald-400' : 'text-rose-400'
                        } tabular-nums mt-0.5 block">
                          ${net >= 0 ? '+' : '-'}${formatCurrency(Math.abs(net))}
                        </span>
                      </div>
                    </div>

                    <!-- Recorded Months Preview -->
                    <div class="space-y-1.5">
                      <span class="text-[10px] uppercase tracking-wider font-semibold text-slate-500 block">
                        Available Months in ${escapeHtml(yr.year)}
                      </span>
                      <div class="flex items-center gap-1.5 flex-wrap">
                        ${monthPills}
                      </div>
                    </div>
                  </div>

                  <div class="pt-4 border-t border-slate-800/80 flex items-center justify-between">
                    <span class="text-xs text-slate-400">Click to select a specific month in ${escapeHtml(yr.year)}</span>
                    <span class="px-3.5 py-1.5 rounded-xl bg-indigo-600 group-hover:bg-indigo-500 text-white text-xs font-bold transition-colors">
                      Open ${escapeHtml(yr.year)} Months &rarr;
                    </span>
                  </div>
                </div>
              `;
              })
              .join('')}
          </div>
        `
        }
      </div>
    `;
  }

  /**
   * STAGE 2: Select Month within Selected Year ('months')
   */
  buildMonthsStageHtml() {
    const activeYearObj =
      this.years.find((y) => y.year === this.selectedYear) ||
      this.years[0] || {
        year: this.selectedYear || new Date().getFullYear().toString(),
        months_count: 0,
        count: 0,
        income: 0,
        expense: 0,
        net: 0,
        review_count: 0,
        months: [],
      };

    const yr = activeYearObj.year;
    const monthsInYear = activeYearObj.months || [];

    return `
      <div class="bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8 shadow-2xl space-y-6">
        <!-- Breadcrumb & Year Switcher Header -->
        <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4 pb-5 border-b border-slate-800">
          <div class="space-y-1.5">
            <nav aria-label="Breadcrumb" class="flex items-center gap-1.5 flex-wrap text-xs">
              <button
                type="button"
                data-drill-action="open-home"
                class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white font-medium transition-colors cursor-pointer"
              >
                🏠 Home
              </button>
              <span class="text-slate-600">/</span>
              <button
                type="button"
                data-drill-action="open-years"
                class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white font-medium transition-colors cursor-pointer"
              >
                📅 All Years
              </button>
              <span class="text-slate-600">/</span>
              <span class="px-2.5 py-1 rounded-lg bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-bold">
                📂 ${escapeHtml(yr)} &bull; Step 2: Select Month
              </span>
            </nav>
            <h2 class="text-xl sm:text-2xl font-bold text-white tracking-tight font-display pt-1">
              Select a Month in ${escapeHtml(yr)}
            </h2>
            <p class="text-xs text-slate-400">
              Click any month card below to view its itemized transactions, AI categorizations, and voice clarifications.
            </p>
          </div>

          <!-- Quick Year Switcher Pills -->
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-xs text-slate-400 mr-1">Year:</span>
            ${this.years
              .map(
                (y) => `
              <button
                type="button"
                data-drill-action="select-year"
                data-year="${escapeHtml(y.year)}"
                class="px-3 py-1.5 rounded-xl text-xs font-mono font-bold transition-all cursor-pointer ${
                  y.year === yr
                    ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                    : 'bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700'
                }"
              >
                📅 ${escapeHtml(y.year)}
              </button>
            `
              )
              .join('')}
            <button
              type="button"
              data-drill-action="open-years"
              class="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold border border-slate-700 transition-colors cursor-pointer ml-1"
            >
              &larr; All Years
            </button>
          </div>
        </div>

        <!-- Year Summary Ribbon -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3.5">
          <div class="bg-slate-950/70 border border-slate-800 p-3.5 rounded-xl">
            <span class="text-[10px] uppercase font-semibold text-slate-400">Active Months in ${escapeHtml(yr)}</span>
            <p class="text-lg font-bold text-white mt-0.5 tabular-nums">${activeYearObj.months_count} Months (${activeYearObj.count} txns)</p>
          </div>
          <div class="bg-slate-950/70 border border-slate-800 p-3.5 rounded-xl">
            <span class="text-[10px] uppercase font-semibold text-emerald-400">${escapeHtml(yr)} Inflow</span>
            <p class="text-lg font-bold text-emerald-400 mt-0.5 tabular-nums">+${formatCurrency(activeYearObj.income)}</p>
          </div>
          <div class="bg-slate-950/70 border border-slate-800 p-3.5 rounded-xl">
            <span class="text-[10px] uppercase font-semibold text-slate-300">${escapeHtml(yr)} Outflow</span>
            <p class="text-lg font-bold text-slate-100 mt-0.5 tabular-nums">-${formatCurrency(activeYearObj.expense)}</p>
          </div>
          <div class="bg-slate-950/70 border border-slate-800 p-3.5 rounded-xl">
            <span class="text-[10px] uppercase font-semibold text-indigo-400">${escapeHtml(yr)} Net Balance</span>
            <p class="text-lg font-bold ${
              activeYearObj.net >= 0 ? 'text-emerald-400' : 'text-rose-400'
            } mt-0.5 tabular-nums">
              ${activeYearObj.net >= 0 ? '+' : '-'}${formatCurrency(Math.abs(activeYearObj.net))}
            </p>
          </div>
        </div>

        <!-- Month Folder Cards Grid -->
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          ${monthsInYear
            .map((m) => {
              const net = m.income - m.expense;
              const revCnt = Number(m.review_count || 0);
              const topCat = m.top_category || 'General';
              const spendRatio =
                m.income > 0 ? Math.min(100, Math.round((m.expense / m.income) * 100)) : m.expense > 0 ? 100 : 0;

              return `
              <div
                data-drill-action="open-month"
                data-month="${escapeHtml(m.month)}"
                class="group cursor-pointer rounded-2xl p-5 bg-slate-950/80 hover:bg-slate-950 border-2 ${
                  revCnt > 0
                    ? 'border-amber-500/40 hover:border-amber-400'
                    : 'border-slate-800 hover:border-indigo-500/60'
                } shadow-lg transition-all flex flex-col justify-between gap-4"
              >
                <div class="space-y-3">
                  <div class="flex items-start justify-between gap-2">
                    <div>
                      <span class="text-[11px] font-mono text-indigo-400 font-semibold">${escapeHtml(m.month)}</span>
                      <h3 class="text-lg font-bold text-white group-hover:text-indigo-300 transition-colors font-display">
                        🗓️ ${escapeHtml(m.month_name || m.label)} ${escapeHtml(yr)}
                      </h3>
                    </div>

                    ${
                      revCnt > 0
                        ? `<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30">
                            ⚠️ ${revCnt} to review
                          </span>`
                        : `<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                            ✅ ${m.count} txns
                          </span>`
                    }
                  </div>

                  <div class="flex items-center justify-between text-xs text-slate-400">
                    <span>Transactions: <strong class="text-slate-200 font-mono">${m.count}</strong></span>
                    <span class="px-2 py-0.5 rounded-md bg-slate-900 border border-slate-800 text-[11px] text-slate-300">
                      Top Spend: <strong class="text-indigo-300">${escapeHtml(topCat)}</strong>
                    </span>
                  </div>

                  <div class="grid grid-cols-3 gap-2 pt-1 text-xs">
                    <div class="p-2.5 rounded-xl bg-slate-900/90 border border-slate-800/80">
                      <span class="text-[10px] text-slate-400 block">Inflow</span>
                      <span class="font-mono font-bold text-emerald-400 tabular-nums">+${formatCurrency(m.income)}</span>
                    </div>
                    <div class="p-2.5 rounded-xl bg-slate-900/90 border border-slate-800/80">
                      <span class="text-[10px] text-slate-400 block">Outflow</span>
                      <span class="font-mono font-bold text-slate-100 tabular-nums">-${formatCurrency(m.expense)}</span>
                    </div>
                    <div class="p-2.5 rounded-xl bg-slate-900/90 border border-slate-800/80">
                      <span class="text-[10px] text-slate-400 block">Net</span>
                      <span class="font-mono font-bold ${
                        net >= 0 ? 'text-emerald-400' : 'text-rose-400'
                      } tabular-nums">
                        ${net >= 0 ? '+' : '-'}${formatCurrency(Math.abs(net))}
                      </span>
                    </div>
                  </div>

                  <div class="grid grid-cols-2 gap-2 text-xs">
                    <div class="p-2.5 rounded-xl bg-slate-900/70 border border-slate-800/80">
                      <span class="text-[10px] text-sky-400 font-semibold block">Start of Month Balance</span>
                      <span class="font-mono font-bold ${
                        Number(m.opening_balance || 0) >= 0 ? 'text-slate-100' : 'text-rose-400'
                      } tabular-nums">
                        ${Number(m.opening_balance || 0) >= 0 ? '+' : '-'}${formatCurrency(Math.abs(Number(m.opening_balance || 0)))}
                      </span>
                    </div>
                    <div class="p-2.5 rounded-xl bg-slate-900/70 border border-indigo-500/30">
                      <span class="text-[10px] text-amber-400 font-semibold block">End of Month Balance</span>
                      <span class="font-mono font-bold ${
                        Number(m.closing_balance || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'
                      } tabular-nums">
                        ${Number(m.closing_balance || 0) >= 0 ? '+' : '-'}${formatCurrency(Math.abs(Number(m.closing_balance || 0)))}
                      </span>
                    </div>
                  </div>

                  <!-- Spend Ratio Bar -->
                  <div class="space-y-1">
                    <div class="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                      <div
                        class="h-full rounded-full ${
                          spendRatio > 90 ? 'bg-rose-500' : spendRatio > 70 ? 'bg-amber-400' : 'bg-emerald-500'
                        }"
                        style="width: ${spendRatio}%"
                      ></div>
                    </div>
                  </div>
                </div>

                <div class="pt-3 border-t border-slate-800/80 flex items-center justify-between">
                  <span class="text-[11px] text-slate-400">Open ${escapeHtml(m.month_name || m.label)} statement</span>
                  <span class="text-xs font-bold text-indigo-400 group-hover:text-indigo-300 group-hover:translate-x-0.5 transition-all">
                    View Transactions &rarr;
                  </span>
                </div>
              </div>
            `;
            })
            .join('')}
        </div>
      </div>
    `;
  }

  bindDrillStageEvents(drillContainer) {
    // Global search form on Home screen
    const searchForm = drillContainer.querySelector('#home-global-search-form');
    if (searchForm) {
      searchForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const input = drillContainer.querySelector('#home-global-search-input');
        const q = (input?.value || '').trim().toLowerCase();
        this.navigateToStage('ledger', { month: 'ALL', search: q, onlyReview: false });
      });
    }

    // Drill-down action triggers
    drillContainer.querySelectorAll('[data-drill-action]').forEach((el) => {
      el.addEventListener('click', () => {
        const action = el.getAttribute('data-drill-action');
        if (action === 'open-home') {
          this.navigateToStage('home');
        } else if (action === 'open-years') {
          this.navigateToStage('years');
        } else if (action === 'select-year') {
          const yr = el.getAttribute('data-year');
          this.navigateToStage('months', { year: yr });
        } else if (action === 'open-month') {
          const m = el.getAttribute('data-month');
          this.navigateToStage('ledger', { month: m, search: '', onlyReview: false });
        } else if (action === 'open-flagged-all') {
          this.navigateToStage('ledger', { month: 'ALL', search: '', onlyReview: true });
        } else if (action === 'add-manual-tx') {
          addTransactionModal.open(async (payload) => {
            await createTransaction(payload);
            toast.show('Transaction added successfully!', 'success');
            await this.loadMonths();
            this.renderDrillStage();
          });
        }
      });
    });

    // Module launchpad tiles on Home screen
    drillContainer.querySelectorAll('[data-nav-page]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const targetPage = btn.getAttribute('data-nav-page');
        if (targetPage && typeof this.options.onNavigatePage === 'function') {
          this.options.onNavigatePage(targetPage);
        }
      });
    });
  }

  setViewMode(mode) {
    this.viewMode = mode === 'bento' ? 'bento' : 'table';
    const tableWrapper = this.container.querySelector('#ledger-table-wrapper');
    const bentoGrid = this.container.querySelector('#ledger-bento-grid');
    const tableBtn = this.container.querySelector('#view-mode-table-btn');
    const bentoBtn = this.container.querySelector('#view-mode-bento-btn');

    if (this.viewMode === 'bento') {
      tableWrapper?.classList.add('hidden');
      bentoGrid?.classList.remove('hidden');
      bentoGrid?.classList.add('grid');
      if (bentoBtn)
        bentoBtn.className =
          'px-3 py-1 rounded-full font-semibold bg-indigo-600 text-white transition-all cursor-pointer flex items-center gap-1';
      if (tableBtn)
        tableBtn.className =
          'px-3 py-1 rounded-full font-semibold text-slate-400 hover:text-white transition-all cursor-pointer flex items-center gap-1';
    } else {
      bentoGrid?.classList.add('hidden');
      bentoGrid?.classList.remove('grid');
      tableWrapper?.classList.remove('hidden');
      if (tableBtn)
        tableBtn.className =
          'px-3 py-1 rounded-full font-semibold bg-indigo-600 text-white transition-all cursor-pointer flex items-center gap-1';
      if (bentoBtn)
        bentoBtn.className =
          'px-3 py-1 rounded-full font-semibold text-slate-400 hover:text-white transition-all cursor-pointer flex items-center gap-1';
    }
    this.renderRows();
  }

  bindEvents() {
    const searchInput = this.container.querySelector('#ledger-search');
    const monthSelect = this.container.querySelector('#ledger-month-select');
    const orderBySelect = this.container.querySelector('#ledger-order-by');
    const typeFilter = this.container.querySelector('#ledger-type-filter');
    const refreshBtn = this.container.querySelector('#ledger-refresh-btn');
    const addTxBtn = this.container.querySelector('#add-tx-btn');
    const exportCsvBtn = this.container.querySelector('#export-csv-btn');
    const tableBtn = this.container.querySelector('#view-mode-table-btn');
    const bentoBtn = this.container.querySelector('#view-mode-bento-btn');

    // Breadcrumb & Month Stepper listeners inside Stage 3 ('ledger')
    const bcHomeBtn = this.container.querySelector('#bc-home-btn');
    const bcYearsBtn = this.container.querySelector('#bc-years-btn');
    const bcYearMonthsBtn = this.container.querySelector('#bc-year-months-btn');
    const backToMonthsBtn = this.container.querySelector('#back-to-months-btn');
    const prevMonthBtn = this.container.querySelector('#prev-month-step-btn');
    const nextMonthBtn = this.container.querySelector('#next-month-step-btn');

    bcHomeBtn?.addEventListener('click', () => this.navigateToStage('home'));
    bcYearsBtn?.addEventListener('click', () => this.navigateToStage('years'));
    bcYearMonthsBtn?.addEventListener('click', () => {
      const yr =
        this.selectedYear ||
        (this.selectedMonth && this.selectedMonth !== 'ALL'
          ? this.selectedMonth.slice(0, 4)
          : this.years[0]?.year);
      this.navigateToStage('months', { year: yr });
    });
    backToMonthsBtn?.addEventListener('click', () => {
      const yr =
        this.selectedYear ||
        (this.selectedMonth && this.selectedMonth !== 'ALL'
          ? this.selectedMonth.slice(0, 4)
          : this.years[0]?.year);
      this.navigateToStage('months', { year: yr });
    });

    prevMonthBtn?.addEventListener('click', () => {
      const target = prevMonthBtn.dataset.targetMonth;
      if (target) {
        this.navigateToStage('ledger', { month: target });
      }
    });

    nextMonthBtn?.addEventListener('click', () => {
      const target = nextMonthBtn.dataset.targetMonth;
      if (target) {
        this.navigateToStage('ledger', { month: target });
      }
    });

    tableBtn?.addEventListener('click', () => this.setViewMode('table'));
    bentoBtn?.addEventListener('click', () => this.setViewMode('bento'));

    // Search filter with instant reactive response
    searchInput.addEventListener('input', (e) => {
      this.searchQuery = e.target.value.toLowerCase().trim();
      this.applyFilter();
      if (typeof this.options.onSearchChange === 'function') {
        this.options.onSearchChange(e.target.value);
      }
    });

    // Month filter change
    monthSelect.addEventListener('change', (e) => {
      this.selectedMonth = e.target.value;
      if (this.selectedMonth !== 'ALL' && this.selectedMonth.length >= 4) {
        this.selectedYear = this.selectedMonth.slice(0, 4);
      }
      this.updateBreadcrumbAndStepper();
      this.loadData();
      if (typeof this.options.onMonthChange === 'function') {
        this.options.onMonthChange(this.selectedMonth);
      }
    });

    // Order By change
    orderBySelect.addEventListener('change', (e) => {
      const [col, dir] = e.target.value.split(':');
      this.orderBy = col || 'date';
      this.orderDir = dir || 'desc';
      this.updateHeaderSortIcons();
      this.loadData();
    });

    // Type filter change
    typeFilter.addEventListener('change', (e) => {
      this.typeFilter = e.target.value;
      this.applyFilter();
    });

    // Refresh button
    refreshBtn.addEventListener('click', () => {
      this.initData();
    });

    // Add manual transaction
    addTxBtn.addEventListener('click', () => {
      addTransactionModal.open(async (payload) => {
        await createTransaction(payload);
        toast.show('Transaction added successfully!', 'success');
        await this.loadMonths();
        await this.loadData();
      });
    });

    // Export CSV
    exportCsvBtn.addEventListener('click', () => {
      this.exportCSV();
    });

    // Review banner filter button
    const filterReviewBtn = this.container.querySelector('#filter-review-btn');
    if (filterReviewBtn) {
      filterReviewBtn.addEventListener('click', () => {
        this.onlyReviewFilter = !this.onlyReviewFilter;
        this.updateReviewBanner();
        this.applyFilter();
      });
    }

    // Review banner "Clarify with Voice Agent" button
    const startVoiceBtn = this.container.querySelector('#start-voice-clarify-btn');
    if (startVoiceBtn) {
      startVoiceBtn.addEventListener('click', () => {
        const unreviewed = this.transactions.find((tx) => Boolean(tx.requires_human_review));
        if (unreviewed) {
          this.openVoiceResolveModal(unreviewed.id);
        } else {
          toast.show('All transactions in this period are already clarified!', 'info');
        }
      });
    }

    // Clickable table header sorting
    this.container.querySelectorAll('th[data-sort]').forEach((th) => {
      th.addEventListener('click', () => {
        const col = th.getAttribute('data-sort');
        if (this.orderBy === col) {
          this.orderDir = this.orderDir === 'asc' ? 'desc' : 'asc';
        } else {
          this.orderBy = col;
          this.orderDir = col === 'amount' ? 'desc' : 'asc';
        }

        const key = `${this.orderBy}:${this.orderDir}`;
        const foundOpt = Array.from(orderBySelect.options).find((opt) => opt.value === key);
        if (foundOpt) {
          orderBySelect.value = key;
        }

        this.updateHeaderSortIcons();
        this.loadData();
      });
    });
  }

  updateHeaderSortIcons() {
    const cols = ['date', 'description', 'category', 'amount'];
    cols.forEach((c) => {
      const icon = this.container.querySelector(`#sort-icon-${c}`);
      if (!icon) return;
      if (this.orderBy === c) {
        icon.textContent = this.orderDir === 'asc' ? '▲' : '▼';
        icon.className = 'text-xs text-indigo-400 font-bold';
      } else {
        icon.textContent = '↕';
        icon.className = 'text-xs text-slate-600';
      }
    });
  }

  async loadMonths() {
    try {
      const res = await getTransactionMonths();
      const monthSelect = this.container.querySelector('#ledger-month-select');

      const rawMonths = res.months || [];
      // Compute chronological opening_balance (start of month) and closing_balance (end of month)
      const sortedAsc = [...rawMonths].sort((a, b) => String(a.month || '').localeCompare(String(b.month || '')));
      let runningBal = 0;
      const balMap = new Map();
      sortedAsc.forEach((m) => {
        const inc = Number(m.income || 0);
        const exp = Number(m.expense || 0);
        const netVal = m.net !== undefined ? Number(m.net) : inc - exp;
        const openBal = m.opening_balance !== undefined ? Number(m.opening_balance) : runningBal;
        const closeBal = m.closing_balance !== undefined ? Number(m.closing_balance) : openBal + netVal;
        runningBal = closeBal;
        balMap.set(m.month, { opening_balance: openBal, closing_balance: closeBal });
      });

      this.months = rawMonths.map((m) => {
        const computed = balMap.get(m.month) || { opening_balance: 0, closing_balance: Number(m.net || 0) };
        return {
          ...m,
          opening_balance: computed.opening_balance,
          closing_balance: computed.closing_balance,
        };
      });

      this.years = this.buildYearsFromMonths(this.months, res.years);
      this.allSummary = res.all || {
        count: this.months.reduce((s, m) => s + Number(m.count || 0), 0),
        income: this.months.reduce((s, m) => s + Number(m.income || 0), 0),
        expense: this.months.reduce((s, m) => s + Number(m.expense || 0), 0),
        net: this.months.reduce((s, m) => s + Number(m.net || 0), 0),
        opening_balance: 0,
        closing_balance: this.months.reduce((s, m) => s + Number(m.net || 0), 0),
        review_count: this.months.reduce((s, m) => s + Number(m.review_count || 0), 0),
        years_count: this.years.length,
      };

      if (!this.selectedYear && this.years.length > 0) {
        this.selectedYear = this.years[0].year;
      }

      if (monthSelect) {
        let html = `<option value="ALL">All Available Months (${this.allSummary.count || 0})</option>`;
        this.months.forEach((m) => {
          html += `<option value="${escapeHtml(m.month)}">${escapeHtml(m.label)} (${m.count} txns &bull; In: ${formatCurrency(m.income)})</option>`;
        });
        monthSelect.innerHTML = html;

        if (
          this.options.autoSelectLatestMonth !== false &&
          (!this.selectedMonth || this.selectedMonth === 'ALL') &&
          this.months.length > 0
        ) {
          this.selectedMonth = this.months[0].month;
        }
        monthSelect.value = this.selectedMonth;
      }

      if (typeof this.options.onMonthChange === 'function' && this.selectedMonth) {
        this.options.onMonthChange(this.selectedMonth);
      }
    } catch (err) {
      console.warn('[Ledger] Failed to load months:', err);
    }
  }

  async loadData() {
    if (this.isLoading) {
      // A month/sort change arrived while a request was in flight: reload once it finishes, so the
      // table never shows a different selection than the controls.
      this.reloadQueued = true;
      return;
    }

    try {
      this.isLoading = true;
      this.setLoadingState(true);

      const items = await getTransactions({
        month: this.selectedMonth,
        orderBy: this.orderBy,
        orderDir: this.orderDir,
        type: this.typeFilter !== 'ALL' ? this.typeFilter : undefined,
      });
      if (this.reloadQueued) return; // stale; the finally block loads the newer selection

      this.transactions = items;
      this.updateReviewBanner();
      this.applyFilter();
    } catch (error) {
      if (this.reloadQueued) return;
      toast.show(`Ledger fetch error: ${error.message}`, 'error', 6000);
      this.renderErrorState(error.message);
    } finally {
      this.isLoading = false;
      this.setLoadingState(false);
      if (this.reloadQueued) {
        this.reloadQueued = false;
        this.loadData();
      }
    }
  }

  setLoadingState(loading) {
    const refreshIcon = this.container.querySelector('#refresh-icon');
    if (loading) {
      refreshIcon?.classList.add('animate-spin');
      this.renderSkeleton();
    } else {
      refreshIcon?.classList.remove('animate-spin');
    }
  }

  renderSkeleton() {
    const tbody = this.container.querySelector('#ledger-table-body');
    if (!tbody) return;

    tbody.innerHTML = Array(5)
      .fill(0)
      .map(
        () => `
        <tr class="animate-pulse">
          <td class="py-4 px-4"><div class="h-4 bg-slate-800 rounded w-20"></div></td>
          <td class="py-4 px-4"><div class="h-4 bg-slate-800 rounded w-44"></div></td>
          <td class="py-4 px-4"><div class="h-5 bg-slate-800 rounded-full w-24"></div></td>
          <td class="py-4 px-4 text-right"><div class="h-4 bg-slate-800 rounded w-16 ml-auto"></div></td>
          <td class="py-4 px-4 text-center"><div class="h-5 bg-slate-800 rounded-full w-16 mx-auto"></div></td>
          <td class="py-4 px-4 text-center"><div class="h-7 bg-slate-800 rounded-lg w-14 mx-auto"></div></td>
        </tr>
      `
      )
      .join('');
  }

  updateReviewBanner() {
    const banner = this.container.querySelector('#ledger-review-banner');
    const countEl = this.container.querySelector('#review-banner-count');
    const filterBtn = this.container.querySelector('#filter-review-btn');
    if (!banner || !countEl) return;

    const unreviewed = this.transactions.filter((tx) => Boolean(tx.requires_human_review));

    if (unreviewed.length > 0) {
      banner.classList.remove('hidden');
      countEl.textContent = `${unreviewed.length} transaction${unreviewed.length === 1 ? ' requires' : 's require'} clarification`;
      if (filterBtn) {
        filterBtn.textContent = this.onlyReviewFilter ? 'Show All Transactions' : 'Show Flagged Only';
      }
    } else {
      banner.classList.add('hidden');
      this.onlyReviewFilter = false;
    }
  }

  applyFilter() {
    this.filteredTransactions = this.transactions.filter((tx) => {
      if (this.onlyReviewFilter) {
        const needsReview = Boolean(tx.requires_human_review);
        if (!needsReview) return false;
      }

      if (this.typeFilter !== 'ALL') {
        const txType = (tx.type || 'EXPENSE').toUpperCase();
        const isTransfer =
          txType === 'TRANSFER' ||
          Boolean(tx.is_self_transfer) ||
          (tx.category || '').toLowerCase() === 'transfer';
        if (this.typeFilter === 'TRANSFER') {
          if (!isTransfer) return false;
        } else {
          if (isTransfer || txType !== this.typeFilter) return false;
        }
      }

      if (this.searchQuery) {
        const desc = (tx.description || '').toLowerCase();
        const cat = (tx.category || '').toLowerCase();
        if (!desc.includes(this.searchQuery) && !cat.includes(this.searchQuery)) {
          return false;
        }
      }

      return true;
    });

    this.renderRows();
    this.updateStats();
  }

  isOpeningBalanceRow(tx) {
    const desc = String(tx?.description || '').trim();
    return /^(prev(?:ious|ous)?\s+balance|opening\s+balance|initial\s+balance|balance\s+b\/?f)/i.test(desc);
  }

  isClosingBalanceRow(tx) {
    const desc = String(tx?.description || '').trim();
    return /^(month\s+end\s+balance|closing\s+balance)/i.test(desc);
  }

  updateStats() {
    const totalCountEl = this.container.querySelector('#stat-count');
    const openingBalEl = this.container.querySelector('#stat-opening-balance');
    const openingSubEl = this.container.querySelector('#stat-opening-sub');
    const incomeEl = this.container.querySelector('#stat-income');
    const expenseEl = this.container.querySelector('#stat-expense');
    const netEl = this.container.querySelector('#stat-net');
    const savingsRateEl = this.container.querySelector('#stat-savings-rate');
    const closingBalEl = this.container.querySelector('#stat-closing-balance');
    const closingSubEl = this.container.querySelector('#stat-closing-sub');

    let totalIncome = 0;
    let totalExpense = 0;

    this.filteredTransactions.forEach((tx) => {
      if (this.isOpeningBalanceRow(tx) || this.isClosingBalanceRow(tx)) return;
      const amt = Number(tx.amount) || 0;
      const type = (tx.type || 'EXPENSE').toUpperCase();
      if (type === 'INCOME') {
        totalIncome += amt;
      } else if (type === 'EXPENSE') {
        totalExpense += amt;
      }
    });

    const net = totalIncome - totalExpense;
    const savingsRate = totalIncome > 0 ? Math.round((net / totalIncome) * 100) : 0;

    // Compute Start of Month (Opening) and End of Month (Closing) balances.
    // Prioritize official statement-reconciled balances (`monthMeta.opening_balance` & `monthMeta.closing_balance`, DEC-054)
    // across Kotak Mahindra Bank (SA + ActivMoney FD) and HDFC Bank (SA + Sweep-In FD).
    const monthMeta = this.months.find((m) => m.month === this.selectedMonth);
    const explicitOpeningTx = this.transactions.find((tx) => this.isOpeningBalanceRow(tx));
    const sortedMonthsAsc = [...this.months].sort((a, b) => (a.month || '').localeCompare(b.month || ''));
    const openingBalance =
      this.selectedMonth === 'ALL'
        ? sortedMonthsAsc.length > 0
          ? Number(sortedMonthsAsc[0].opening_balance ?? 0)
          : 0
        : monthMeta && monthMeta.opening_balance !== undefined
        ? Number(monthMeta.opening_balance)
        : explicitOpeningTx
        ? Number(explicitOpeningTx.amount || 0)
        : 0;

    let unfilteredIncome = 0;
    let unfilteredExpense = 0;
    this.transactions.forEach((tx) => {
      if (this.isOpeningBalanceRow(tx) || this.isClosingBalanceRow(tx)) return;
      const amt = Number(tx.amount) || 0;
      const type = (tx.type || 'EXPENSE').toUpperCase();
      const isTransfer =
        type === 'TRANSFER' ||
        Boolean(tx.is_self_transfer) ||
        (tx.category || '').toLowerCase() === 'transfer';
      if (isTransfer) return;
      if (type === 'INCOME') unfilteredIncome += amt;
      else if (type === 'EXPENSE') unfilteredExpense += amt;
    });

    const monthNet =
      this.transactions.length > 0
        ? unfilteredIncome - unfilteredExpense
        : monthMeta
        ? Number(monthMeta.net || 0)
        : net;
    const closingBalance =
      this.selectedMonth === 'ALL'
        ? sortedMonthsAsc.length > 0
          ? Number(sortedMonthsAsc[sortedMonthsAsc.length - 1].closing_balance ?? openingBalance + monthNet)
          : openingBalance + monthNet
        : monthMeta && monthMeta.reconciled && monthMeta.closing_balance !== undefined
        ? Number(monthMeta.closing_balance)
        : openingBalance + monthNet;

    const bankBreakdown = monthMeta?.bank_breakdown;

    if (totalCountEl) totalCountEl.textContent = this.filteredTransactions.length.toString();
    if (openingBalEl) {
      openingBalEl.textContent = `${openingBalance >= 0 ? '+' : '-'}${formatCurrency(Math.abs(openingBalance))}`;
      openingBalEl.className = `text-xl font-bold mt-1 tabular-nums ${
        openingBalance >= 0 ? 'text-slate-100' : 'text-rose-400'
      }`;
    }
    if (openingSubEl) {
      openingSubEl.textContent =
        this.selectedMonth === 'ALL'
          ? 'All-time starting baseline (Kotak + HDFC)'
          : bankBreakdown
          ? `Kotak ${formatCurrency(Number(bankBreakdown.kotak_open || 0))} • HDFC ${formatCurrency(Number(bankBreakdown.hdfc_open || 0))}`
          : explicitOpeningTx
          ? 'Initial balance (Previous Balance)'
          : 'Prior months carry-forward';
    }
    if (incomeEl) incomeEl.textContent = `+${formatCurrency(totalIncome)}`;
    if (expenseEl) expenseEl.textContent = `-${formatCurrency(totalExpense)}`;
    if (netEl) {
      netEl.textContent = `${net >= 0 ? '+' : '-'}${formatCurrency(Math.abs(net))}`;
      netEl.className = `text-xl font-bold mt-1 tabular-nums ${net >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
    }
    if (savingsRateEl) {
      savingsRateEl.textContent = `${savingsRate >= 0 ? `${savingsRate}% saved` : 'Deficit'}`;
      savingsRateEl.className = `text-[10px] font-semibold ${
        savingsRate >= 20 ? 'text-emerald-400' : savingsRate >= 0 ? 'text-amber-400' : 'text-rose-400'
      }`;
    }
    if (closingBalEl) {
      closingBalEl.textContent = `${closingBalance >= 0 ? '+' : '-'}${formatCurrency(Math.abs(closingBalance))}`;
      closingBalEl.className = `text-xl font-bold mt-1 tabular-nums ${
        closingBalance >= 0 ? 'text-emerald-400' : 'text-rose-400'
      }`;
    }
    if (closingSubEl) {
      closingSubEl.textContent =
        this.selectedMonth === 'ALL'
          ? 'Latest reconciled balance (Kotak + HDFC)'
          : bankBreakdown
          ? `Kotak ${formatCurrency(Number(bankBreakdown.kotak_close || 0))} • HDFC ${formatCurrency(Number(bankBreakdown.hdfc_close || 0))}`
          : 'Balance at month end (start + net)';
    }
  }

  renderRows() {
    const tbody = this.container.querySelector('#ledger-table-body');
    const bentoGrid = this.container.querySelector('#ledger-bento-grid');
    const countSummary = this.container.querySelector('#ledger-count-summary');
    if (!tbody) return;

    if (this.filteredTransactions.length === 0) {
      const emptyMarkup = `
        <div class="flex flex-col items-center justify-center py-12 px-4 text-center text-slate-500">
          <div class="w-12 h-12 rounded-2xl bg-slate-900 border border-slate-800 flex items-center justify-center text-slate-500 mb-3">
            <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </div>
          <p class="text-sm font-semibold text-slate-300">No transactions match your current filters</p>
          <p class="text-xs text-slate-500 mt-1 max-w-sm">Try choosing a different month, clearing the search term, or uploading a statement.</p>
        </div>
      `;
      tbody.innerHTML = `<tr><td colspan="6">${emptyMarkup}</td></tr>`;
      if (bentoGrid) {
        bentoGrid.innerHTML = `<div class="col-span-full">${emptyMarkup}</div>`;
      }
      if (countSummary) countSummary.textContent = 'Showing 0 transactions';
      return;
    }

    if (countSummary) {
      countSummary.textContent = `Showing ${this.filteredTransactions.length} of ${this.transactions.length} records in this view`;
    }

    tbody.innerHTML = this.filteredTransactions
      .map((tx) => {
        const txType = (tx.type || 'EXPENSE').toUpperCase();
        const isIncome = txType === 'INCOME';
        const isTransfer = txType === 'TRANSFER';
        const formattedAmt = formatSignedCurrency(tx.amount, txType);
        const amtColor = isIncome
          ? 'text-emerald-400 font-semibold'
          : isTransfer
          ? 'text-sky-300 font-medium'
          : 'text-slate-100 font-medium';
        const needsReview = Boolean(tx.requires_human_review);
        const rowClass = needsReview
          ? 'ledger-row-flagged bg-amber-950/25 border-l-4 border-l-amber-500 hover:bg-amber-900/35 transition-colors group'
          : 'hover:bg-slate-800/40 transition-colors group';
        const categoryPillClass = needsReview
          ? 'bg-amber-500/20 text-amber-300 border-amber-500/40 font-semibold'
          : getCategoryBadgeClass(tx.category);
        const categoryLabel = needsReview
          ? `⚠️ Needs Review (${tx.category || 'Uncategorized'})`
          : tx.category || 'Other';

        return `
          <tr class="${rowClass}" data-id="${escapeHtml(tx.id)}">
            <!-- Date -->
            <td class="py-3.5 px-4 text-xs font-mono tabular-nums text-slate-400 whitespace-nowrap">
              ${escapeHtml(formatDate(tx.date))}
            </td>

            <!-- Description -->
            <td class="py-3.5 px-4">
              <div class="font-medium text-slate-200 text-sm max-w-xs md:max-w-sm truncate" title="${escapeHtml(tx.description)}">
                ${escapeHtml(tx.description)}
              </div>
              ${
                needsReview
                  ? `<div class="inline-flex items-center gap-1 mt-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30 animate-pulse">
                       <svg class="w-3 h-3 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                         <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                       </svg>
                       <span>Needs AI Clarification</span>
                     </div>`
                  : tx.confidence
                  ? `<div class="text-[10px] text-slate-500 tabular-nums">AI Confidence: ${Math.round(tx.confidence)}%</div>`
                  : ''
              }
            </td>

            <!-- Category -->
            <td class="py-3.5 px-4 whitespace-nowrap">
              <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${categoryPillClass}">
                ${escapeHtml(categoryLabel)}
              </span>
            </td>

            <!-- Amount -->
            <td class="py-3.5 px-4 text-right whitespace-nowrap font-mono tabular-nums text-sm ${amtColor}">
              ${formattedAmt}
            </td>

            <!-- Type -->
            <td class="py-3.5 px-4 text-center whitespace-nowrap">
              <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] uppercase font-bold tracking-wider border ${getTypeBadgeClass(
                tx.type
              )}">
                ${escapeHtml(txType)}
              </span>
            </td>

            <!-- Actions -->
            <td class="py-3.5 px-4 text-center whitespace-nowrap">
              <div class="flex items-center justify-center gap-1.5">
                ${
                  needsReview
                    ? `<button
                        type="button"
                        data-action="voice-resolve"
                        data-id="${escapeHtml(tx.id)}"
                        class="voice-resolve-btn inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold rounded-full text-amber-200 bg-amber-600/30 hover:bg-amber-500/40 border border-amber-500/50 shadow-sm transition-all cursor-pointer"
                        title="Clarify category with voice or text"
                      >
                        <svg class="w-3.5 h-3.5 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                        </svg>
                        <span>Clarify</span>
                      </button>`
                    : ''
                }

                <button
                  type="button"
                  data-action="edit"
                  data-id="${escapeHtml(tx.id)}"
                  class="edit-btn inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-full text-indigo-300 bg-indigo-950/60 hover:bg-indigo-900/80 border border-indigo-800/60 transition-colors cursor-pointer"
                  title="Edit Transaction #${escapeHtml(tx.id)}"
                >
                  <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                  <span>Edit</span>
                </button>

                <button
                  type="button"
                  data-action="delete"
                  data-id="${escapeHtml(tx.id)}"
                  class="delete-btn inline-flex items-center p-1 text-xs font-medium rounded-full text-slate-400 hover:text-rose-400 hover:bg-rose-950/40 border border-transparent hover:border-rose-900/60 transition-colors cursor-pointer"
                  title="Delete Transaction"
                >
                  <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>
            </td>
          </tr>
        `;
      })
      .join('');

    if (bentoGrid) {
      bentoGrid.innerHTML = this.filteredTransactions
        .map((tx) => {
          const txType = (tx.type || 'EXPENSE').toUpperCase();
          const isIncome = txType === 'INCOME';
          const isTransfer = txType === 'TRANSFER';
          const formattedAmt = formatSignedCurrency(tx.amount, txType);
          const amtColor = isIncome ? 'text-emerald-400' : isTransfer ? 'text-sky-300' : 'text-slate-100';
          const needsReview = Boolean(tx.requires_human_review);
          const confPct = tx.confidence ? Math.round(tx.confidence) : 95;
          const categoryPillClass = needsReview
            ? 'bg-amber-500/20 text-amber-300 border-amber-500/40 font-semibold'
            : getCategoryBadgeClass(tx.category);
          const categoryLabel = needsReview ? '⚠️ Needs Review' : tx.category || 'Other';

          return `
            <div class="labs-tx-card p-4 flex flex-col justify-between gap-3 ${
              needsReview ? 'ring-1 ring-amber-500/40' : ''
            }" data-id="${escapeHtml(tx.id)}">
              <div class="space-y-2">
                <div class="flex items-center justify-between gap-2">
                  <span class="text-[11px] font-mono tabular-nums text-slate-400">${escapeHtml(formatDate(tx.date))}</span>
                  <div class="flex items-center gap-1.5">
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-medium border ${categoryPillClass}">
                      ${escapeHtml(categoryLabel)}
                    </span>
                    <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[9px] uppercase font-bold tracking-wider border ${getTypeBadgeClass(
                      tx.type
                    )}">
                      ${escapeHtml(txType)}
                    </span>
                  </div>
                </div>

                <div class="font-semibold text-slate-100 text-sm line-clamp-2 break-words" title="${escapeHtml(
                  tx.description
                )}">
                  ${escapeHtml(tx.description)}
                </div>
              </div>

              <div class="pt-2 border-t border-white/5 flex items-end justify-between gap-2">
                <div>
                  <div class="text-lg font-bold font-mono tabular-nums ${amtColor}">${formattedAmt}</div>
                  ${
                    needsReview
                      ? `<span class="text-[10px] font-semibold text-amber-300 flex items-center gap-1">⚠️ Needs Clarification (${confPct}%)</span>`
                      : `<span class="text-[10px] text-slate-500 tabular-nums">AI Confidence: ${confPct}%</span>`
                  }
                </div>

                <div class="flex items-center gap-1.5">
                  ${
                    needsReview
                      ? `<button type="button" data-id="${escapeHtml(
                          tx.id
                        )}" class="voice-resolve-btn px-2.5 py-1 text-xs font-semibold rounded-full text-amber-200 bg-amber-600/30 hover:bg-amber-500/40 border border-amber-500/50 cursor-pointer">🎙️ Clarify</button>`
                      : ''
                  }
                  <button type="button" data-id="${escapeHtml(
                    tx.id
                  )}" class="edit-btn px-2.5 py-1 text-xs font-medium rounded-full text-indigo-300 bg-indigo-950/60 hover:bg-indigo-900/80 border border-indigo-800/60 cursor-pointer">Edit</button>
                  <button type="button" data-id="${escapeHtml(
                    tx.id
                  )}" class="delete-btn p-1.5 text-xs rounded-full text-slate-400 hover:text-rose-400 hover:bg-rose-950/40 cursor-pointer" title="Delete">✕</button>
                </div>
              </div>
            </div>
          `;
        })
        .join('');

      bentoGrid.querySelectorAll('.labs-tx-card').forEach((card) => {
        card.addEventListener('mousemove', (e) => {
          const rect = card.getBoundingClientRect();
          card.style.setProperty('--mouse-x', `${e.clientX - rect.left}px`);
          card.style.setProperty('--mouse-y', `${e.clientY - rect.top}px`);
        });
      });
    }

    // Attach Voice Resolve, Edit, and Delete button listeners across both Table and Bento views
    this.container.querySelectorAll('.voice-resolve-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-id');
        this.openVoiceResolveModal(id);
      });
    });

    this.container.querySelectorAll('.edit-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-id');
        this.openEditModal(id);
      });
    });

    this.container.querySelectorAll('.delete-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-id');
        this.confirmDelete(id);
      });
    });
  }

  openVoiceResolveModal(id) {
    const tx = this.transactions.find((t) => String(t.id) === String(id));
    if (!tx) {
      toast.show(`Transaction #${id} not found.`, 'error');
      return;
    }

    voiceClarificationModal.open(tx, async (result, resolvedId) => {
      toast.show(`Transaction classified as ${result.resolved_category}!`, 'success');
      const target = this.transactions.find((t) => String(t.id) === String(resolvedId));
      if (target) {
        target.category = result.resolved_category;
        target.requires_human_review = false;
        target.confidence = 100;
      }
      this.updateReviewBanner();
      this.applyFilter();
      this.updateStats();
      await this.loadMonths();
    });
  }

  async confirmDelete(id) {
    const tx = this.transactions.find((t) => String(t.id) === String(id));
    const desc = tx?.description || `#${id}`;
    if (!confirm(`Are you sure you want to delete transaction "${desc}"?`)) {
      return;
    }

    try {
      await deleteTransaction(id);
      toast.show('Transaction deleted.', 'info');
      await this.loadMonths();
      await this.loadData();
    } catch (err) {
      toast.show(`Failed to delete transaction: ${err.message}`, 'error');
    }
  }

  openEditModal(id) {
    const tx = this.transactions.find((t) => String(t.id) === String(id));
    if (!tx) {
      toast.show(`Transaction #${id} not found.`, 'error');
      return;
    }

    editModal.open(tx, async (updatedPayload) => {
      await updateTransaction(tx.id, updatedPayload);
      toast.show(`Transaction #${tx.id} updated successfully!`, 'success');
      await this.loadMonths();
      await this.loadData();
    });
  }

  exportCSV() {
    if (this.filteredTransactions.length === 0) {
      toast.show('No transactions to export.', 'warning');
      return;
    }

    // Quote every text cell, and neutralise spreadsheet formulas: bank/UPI remarks are outside
    // text, and a cell starting with = + - @ (or tab/CR) would run as a formula in Excel/Sheets.
    const csvCell = (value) => {
      let text = String(value ?? '');
      if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
      return `"${text.replace(/"/g, '""')}"`;
    };
    const headers = ['ID', 'Date', 'Description', 'Category', 'Amount', 'Type', 'Confidence'];
    const rows = this.filteredTransactions.map((tx) => [
      tx.id,
      csvCell(tx.date),
      csvCell(tx.description),
      csvCell(tx.category),
      tx.amount,
      csvCell(tx.type || 'EXPENSE'),
      tx.confidence ? `${Math.round(tx.confidence)}%` : '100%',
    ]);

    const csvText = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
    // Download as a Blob: a data: URI built with encodeURI() was cut off at the first '#'.
    // The UTF-8 BOM makes Excel detect the encoding so '₹' and other non-ASCII text display correctly.
    const blob = new Blob(['\uFEFF', csvText], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `transactions-${this.selectedMonth || 'all'}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    // Revoke on a later tick so the browser has started the download before the URL is released.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast.show('Exported transactions CSV successfully!', 'success');
  }

  renderErrorState(message) {
    const tbody = this.container.querySelector('#ledger-table-body');
    if (!tbody) return;

    tbody.innerHTML = `
      <tr>
        <td colspan="6" class="py-8 px-4 text-center">
          <div class="max-w-md mx-auto p-4 rounded-xl bg-rose-950/40 border border-rose-800/60 text-rose-300">
            <p class="text-sm font-semibold">Unable to fetch transactions</p>
            <p class="text-xs text-rose-300/80 mt-1 mb-3">${escapeHtml(message)}</p>
            <button
              type="button"
              id="retry-fetch-btn"
              class="px-4 py-1.5 rounded-lg text-xs font-semibold bg-rose-900/80 hover:bg-rose-800 text-white transition-colors"
            >
              Retry Connection
            </button>
          </div>
        </td>
      </tr>
    `;

    tbody.querySelector('#retry-fetch-btn')?.addEventListener('click', () => this.loadData());
  }

  /**
   * External method to set selected month (e.g. from Analytics or Budgets page)
   * @param {string} month
   */
  setMonth(month) {
    this.selectedMonth = month;
    if (month && month !== 'ALL' && month.length >= 4) {
      this.selectedYear = month.slice(0, 4);
    }
    const select = this.container.querySelector('#ledger-month-select');
    if (select) select.value = month;
    this.updateBreadcrumbAndStepper();
    if (this.drillStage === 'ledger') {
      this.loadData();
    }
  }

  /**
   * Returns user to the Executive Home Overview screen
   */
  goHome() {
    this.navigateToStage('home');
  }
}

export function initLedgerComponent(container, options = {}) {
  return new LedgerComponent(container, options);
}
