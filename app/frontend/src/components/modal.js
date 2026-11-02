/**
 * Reusable Transaction Edit Modal Component
 */

import { escapeHtml } from '../utils/dom.js';
import { formatCurrency, formatDate } from '../utils/formatters.js';
import {
  getTransactionClarification,
  fetchClarificationAudio,
  resolveTransactionCategory,
} from '../api/transactions.ts';

const STANDARD_CATEGORIES = [
  'Food',
  'Health/Medical',
  'Home',
  'Transportation',
  'Personal',
  'Rent',
  'Utilities',
  'Travel',
  'Debt',
  'Investment',
  'SIP',
  'Stocks',
  'Savings',
  'Paycheck',
  'Bonus',
  'Interest',
  'Transfer',
  'Other',
];

class EditTransactionModal {
  constructor() {
    this.modalEl = null;
    this.currentTransaction = null;
    this.onSaveCallback = null;
    this._init();
  }

  _init() {
    let el = document.getElementById('edit-transaction-modal');
    if (el) {
      this.modalEl = el;
      return;
    }

    this.modalEl = document.createElement('div');
    this.modalEl.id = 'edit-transaction-modal';
    this.modalEl.className = 'fixed inset-0 z-50 flex items-center justify-center p-4 hidden';
    this.modalEl.setAttribute('role', 'dialog');
    this.modalEl.setAttribute('aria-modal', 'true');
    this.modalEl.setAttribute('aria-labelledby', 'modal-title');

    this.modalEl.innerHTML = `
      <!-- Backdrop -->
      <div id="modal-backdrop" class="fixed inset-0 bg-slate-950/80 backdrop-blur-sm transition-opacity opacity-0"></div>

      <!-- Modal Card -->
      <div id="modal-card" class="relative w-full max-w-lg bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl p-6 overflow-hidden transform scale-95 opacity-0 transition-all duration-200">
        <div class="flex items-center justify-between pb-4 mb-4 border-b border-slate-800">
          <div class="flex items-center gap-2.5">
            <div class="p-2 rounded-lg bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
              </svg>
            </div>
            <div>
              <h3 id="modal-title" class="text-lg font-semibold text-white">Edit Transaction</h3>
              <p id="modal-subtitle" class="text-xs text-slate-400">Update details for transaction #<span id="modal-tx-id"></span></p>
            </div>
          </div>
          <button id="modal-close-btn" type="button" class="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors">
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>

        <form id="edit-transaction-form" class="space-y-4">
          <!-- Description -->
          <div>
            <label for="modal-desc" class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
              Description <span class="text-rose-400">*</span>
            </label>
            <input
              type="text"
              id="modal-desc"
              required
              class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all"
              placeholder="e.g. Whole Foods Market"
            />
          </div>

          <!-- Category -->
          <div>
            <label for="modal-category" class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
              Category <span class="text-rose-400">*</span>
            </label>
            <div class="relative">
              <select
                id="modal-category"
                required
                class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all appearance-none pr-10"
              >
                ${STANDARD_CATEGORIES.map(
                  (c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`
                ).join('')}
              </select>
              <div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-3 text-slate-400">
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </div>
            </div>
          </div>

          <!-- Amount -->
          <div>
            <label for="modal-amount" class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
              Amount (₹) <span class="text-rose-400">*</span>
            </label>
            <div class="relative">
              <span class="absolute inset-y-0 left-0 flex items-center pl-3.5 text-slate-400 font-medium text-sm">₹</span>
              <input
                type="number"
                step="0.01"
                min="0.01"
                id="modal-amount"
                required
                class="w-full pl-8 pr-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all"
                placeholder="0.00"
              />
            </div>
          </div>

          <!-- Error Alert Banner -->
          <div id="modal-error" class="hidden p-3 rounded-lg bg-rose-950/60 border border-rose-800 text-rose-300 text-xs font-medium"></div>

          <!-- Actions -->
          <div class="flex items-center justify-end gap-3 pt-4 border-t border-slate-800">
            <button
              id="modal-cancel-btn"
              type="button"
              class="px-4 py-2 text-sm font-medium text-slate-300 hover:text-white hover:bg-slate-800 rounded-xl transition-colors"
            >
              Cancel
            </button>
            <button
              id="modal-save-btn"
              type="submit"
              class="inline-flex items-center justify-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 rounded-xl shadow-lg shadow-indigo-600/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span id="modal-btn-text">Save Changes</span>
              <svg id="modal-btn-spinner" class="hidden animate-spin w-4 h-4 text-white" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
            </button>
          </div>
        </form>
      </div>
    `;

    document.body.appendChild(this.modalEl);

    // Event bindings
    const closeBtn = this.modalEl.querySelector('#modal-close-btn');
    const cancelBtn = this.modalEl.querySelector('#modal-cancel-btn');
    const backdrop = this.modalEl.querySelector('#modal-backdrop');
    const form = this.modalEl.querySelector('#edit-transaction-form');

    closeBtn.addEventListener('click', () => this.close());
    cancelBtn.addEventListener('click', () => this.close());
    backdrop.addEventListener('click', () => this.close());

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !this.modalEl.classList.contains('hidden')) {
        this.close();
      }
    });

    form.addEventListener('submit', (e) => this._handleSubmit(e));
  }

  /**
   * Open the edit modal with target transaction prefilled.
   * 
   * @param {Object} transaction
   * @param {(data: { description: string, category: string, amount: number }) => Promise<void>} onSave
   */
  open(transaction, onSave) {
    this.currentTransaction = transaction;
    this.onSaveCallback = onSave;

    const txIdSpan = this.modalEl.querySelector('#modal-tx-id');
    const descInput = this.modalEl.querySelector('#modal-desc');
    const categorySelect = this.modalEl.querySelector('#modal-category');
    const amountInput = this.modalEl.querySelector('#modal-amount');
    const errorBanner = this.modalEl.querySelector('#modal-error');

    txIdSpan.textContent = transaction.id;
    descInput.value = transaction.description || '';
    amountInput.value = transaction.amount !== undefined ? transaction.amount : '';

    // Handle category selection
    const matchingOption = Array.from(categorySelect.options).find(
      (opt) => opt.value.toLowerCase() === (transaction.category || '').toLowerCase()
    );
    if (matchingOption) {
      categorySelect.value = matchingOption.value;
    } else if (transaction.category) {
      // Add custom category option dynamically
      const newOption = document.createElement('option');
      newOption.value = transaction.category;
      newOption.textContent = transaction.category;
      categorySelect.appendChild(newOption);
      categorySelect.value = transaction.category;
    } else {
      categorySelect.value = 'Other';
    }

    errorBanner.classList.add('hidden');
    errorBanner.textContent = '';

    // Unhide modal
    this.modalEl.classList.remove('hidden');

    requestAnimationFrame(() => {
      const backdrop = this.modalEl.querySelector('#modal-backdrop');
      const card = this.modalEl.querySelector('#modal-card');
      backdrop.classList.remove('opacity-0');
      backdrop.classList.add('opacity-100');
      card.classList.remove('scale-95', 'opacity-0');
      card.classList.add('scale-100', 'opacity-100');
      descInput.focus();
    });
  }

  close() {
    const backdrop = this.modalEl.querySelector('#modal-backdrop');
    const card = this.modalEl.querySelector('#modal-card');

    backdrop.classList.remove('opacity-100');
    backdrop.classList.add('opacity-0');
    card.classList.remove('scale-100', 'opacity-100');
    card.classList.add('scale-95', 'opacity-0');

    setTimeout(() => {
      this.modalEl.classList.add('hidden');
      this.setLoading(false);
    }, 200);
  }

  setLoading(isLoading) {
    const saveBtn = this.modalEl.querySelector('#modal-save-btn');
    const btnText = this.modalEl.querySelector('#modal-btn-text');
    const spinner = this.modalEl.querySelector('#modal-btn-spinner');
    const inputs = this.modalEl.querySelectorAll('input, select, button:not(#modal-save-btn)');

    saveBtn.disabled = isLoading;
    inputs.forEach((input) => (input.disabled = isLoading));

    if (isLoading) {
      btnText.textContent = 'Saving...';
      spinner.classList.remove('hidden');
    } else {
      btnText.textContent = 'Save Changes';
      spinner.classList.add('hidden');
    }
  }

  showError(msg) {
    const errorBanner = this.modalEl.querySelector('#modal-error');
    errorBanner.textContent = msg;
    errorBanner.classList.remove('hidden');
  }

  async _handleSubmit(e) {
    e.preventDefault();

    const desc = this.modalEl.querySelector('#modal-desc').value.trim();
    const cat = this.modalEl.querySelector('#modal-category').value;
    const amt = parseFloat(this.modalEl.querySelector('#modal-amount').value);

    if (!desc) {
      this.showError('Description cannot be empty.');
      return;
    }

    if (isNaN(amt) || amt < 0) {
      this.showError('Please enter a valid positive amount.');
      return;
    }

    try {
      this.setLoading(true);
      if (this.onSaveCallback) {
        await this.onSaveCallback({
          description: desc,
          category: cat,
          amount: amt,
        });
      }
      this.close();
    } catch (err) {
      this.setLoading(false);
      this.showError(err.message || 'Failed to update transaction.');
    }
  }
}

class AddTransactionModal {
  constructor() {
    this.modalEl = null;
    this.onSaveCallback = null;
    this._init();
  }

  _init() {
    let el = document.getElementById('add-transaction-modal');
    if (el) {
      this.modalEl = el;
      return;
    }

    this.modalEl = document.createElement('div');
    this.modalEl.id = 'add-transaction-modal';
    this.modalEl.className = 'fixed inset-0 z-50 flex items-center justify-center p-4 hidden';

    this.modalEl.innerHTML = `
      <div id="add-modal-backdrop" class="fixed inset-0 bg-slate-950/80 backdrop-blur-sm transition-opacity opacity-0"></div>
      <div id="add-modal-card" class="relative w-full max-w-lg bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl p-6 overflow-hidden transform scale-95 opacity-0 transition-all duration-200">
        <div class="flex items-center justify-between pb-4 mb-4 border-b border-slate-800">
          <div class="flex items-center gap-2.5">
            <div class="p-2 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
              </svg>
            </div>
            <div>
              <h3 class="text-lg font-semibold text-white">Add New Transaction</h3>
              <p class="text-xs text-slate-400">Record a manual financial entry</p>
            </div>
          </div>
          <button id="add-modal-close-btn" type="button" class="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors">
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>

        <form id="add-transaction-form" class="space-y-4">
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label for="add-date" class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">Date *</label>
              <input type="date" id="add-date" required class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all"/>
            </div>
            <div>
              <label for="add-type" class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">Type *</label>
              <select id="add-type" required class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all">
                <option value="EXPENSE">Expense</option>
                <option value="INCOME">Income</option>
                <option value="TRANSFER">Transfer</option>
              </select>
            </div>
          </div>

          <div>
            <label for="add-desc" class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">Description *</label>
            <input type="text" id="add-desc" required placeholder="e.g. Grocery Store, Client Payment" class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all"/>
          </div>

          <div class="grid grid-cols-2 gap-3">
            <div>
              <label for="add-category" class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">Category *</label>
              <select id="add-category" required class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all">
                ${STANDARD_CATEGORIES.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('')}
              </select>
            </div>
            <div>
              <label for="add-amount" class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">Amount *</label>
              <input type="number" step="0.01" min="0.01" id="add-amount" required placeholder="0.00" class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all"/>
            </div>
          </div>

          <div id="add-modal-error" class="hidden p-3 rounded-lg bg-rose-950/60 border border-rose-800 text-rose-300 text-xs font-medium"></div>

          <div class="flex items-center justify-end gap-3 pt-4 border-t border-slate-800">
            <button id="add-modal-cancel-btn" type="button" class="px-4 py-2 text-sm font-medium text-slate-300 hover:text-white hover:bg-slate-800 rounded-xl transition-colors">Cancel</button>
            <button id="add-modal-save-btn" type="submit" class="px-5 py-2 text-sm font-semibold text-white bg-emerald-600 hover:bg-emerald-500 rounded-xl shadow-lg shadow-emerald-600/20 transition-all">Record Transaction</button>
          </div>
        </form>
      </div>
    `;

    document.body.appendChild(this.modalEl);

    this.modalEl.querySelector('#add-modal-close-btn').addEventListener('click', () => this.close());
    this.modalEl.querySelector('#add-modal-cancel-btn').addEventListener('click', () => this.close());
    this.modalEl.querySelector('#add-modal-backdrop').addEventListener('click', () => this.close());

    this.modalEl.querySelector('#add-transaction-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const date = this.modalEl.querySelector('#add-date').value;
      const type = this.modalEl.querySelector('#add-type').value;
      const desc = this.modalEl.querySelector('#add-desc').value.trim();
      const cat = this.modalEl.querySelector('#add-category').value;
      const amt = parseFloat(this.modalEl.querySelector('#add-amount').value);

      if (!date || !desc || isNaN(amt) || amt <= 0) {
        const err = this.modalEl.querySelector('#add-modal-error');
        err.textContent = 'Please fill all required fields with valid data.';
        err.classList.remove('hidden');
        return;
      }

      try {
        if (this.onSaveCallback) {
          await this.onSaveCallback({ date, type, description: desc, category: cat, amount: amt });
        }
        this.close();
      } catch (error) {
        const err = this.modalEl.querySelector('#add-modal-error');
        err.textContent = error.message || 'Failed to save transaction.';
        err.classList.remove('hidden');
      }
    });
  }

  open(onSave) {
    this.onSaveCallback = onSave;
    const dateInput = this.modalEl.querySelector('#add-date');
    dateInput.value = new Date().toISOString().slice(0, 10);
    this.modalEl.querySelector('#add-desc').value = '';
    this.modalEl.querySelector('#add-amount').value = '';
    this.modalEl.querySelector('#add-modal-error').classList.add('hidden');

    this.modalEl.classList.remove('hidden');
    requestAnimationFrame(() => {
      this.modalEl.querySelector('#add-modal-backdrop').classList.replace('opacity-0', 'opacity-100');
      const card = this.modalEl.querySelector('#add-modal-card');
      card.classList.replace('scale-95', 'scale-100');
      card.classList.replace('opacity-0', 'opacity-100');
    });
  }

  close() {
    this.modalEl.querySelector('#add-modal-backdrop').classList.replace('opacity-100', 'opacity-0');
    const card = this.modalEl.querySelector('#add-modal-card');
    card.classList.replace('scale-100', 'scale-95');
    card.classList.replace('opacity-100', 'opacity-0');
    setTimeout(() => this.modalEl.classList.add('hidden'), 200);
  }
}

class BudgetModal {
  constructor() {
    this.modalEl = null;
    this.onSaveCallback = null;
    this._init();
  }

  _init() {
    let el = document.getElementById('edit-budget-modal');
    if (el) {
      this.modalEl = el;
      return;
    }

    this.modalEl = document.createElement('div');
    this.modalEl.id = 'edit-budget-modal';
    this.modalEl.className = 'fixed inset-0 z-50 flex items-center justify-center p-4 hidden';

    this.modalEl.innerHTML = `
      <div id="budget-modal-backdrop" class="fixed inset-0 bg-slate-950/80 backdrop-blur-sm transition-opacity opacity-0"></div>
      <div id="budget-modal-card" class="relative w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl p-6 overflow-hidden transform scale-95 opacity-0 transition-all duration-200">
        <div class="flex items-center justify-between pb-4 mb-4 border-b border-slate-800">
          <div class="flex items-center gap-2.5">
            <div class="p-2 rounded-lg bg-amber-500/10 text-amber-400 border border-amber-500/20">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
              </svg>
            </div>
            <div>
              <h3 class="text-lg font-semibold text-white">Adjust Category Budget</h3>
              <p class="text-xs text-slate-400">Set monthly spending cap for <span id="budget-cat-name" class="text-amber-300 font-medium"></span></p>
            </div>
          </div>
          <button id="budget-modal-close-btn" type="button" class="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors">
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>

        <form id="edit-budget-form" class="space-y-4">
          <div>
            <label for="budget-amount-input" class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">Monthly Budget Limit (₹) *</label>
            <input type="number" step="1" min="0" id="budget-amount-input" required class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white text-base focus:outline-none focus:ring-2 focus:ring-amber-500 transition-all"/>
          </div>

          <div id="budget-modal-error" class="hidden p-3 rounded-lg bg-rose-950/60 border border-rose-800 text-rose-300 text-xs font-medium"></div>

          <div class="flex items-center justify-end gap-3 pt-4 border-t border-slate-800">
            <button id="budget-modal-cancel-btn" type="button" class="px-4 py-2 text-sm font-medium text-slate-300 hover:text-white hover:bg-slate-800 rounded-xl transition-colors">Cancel</button>
            <button type="submit" class="px-5 py-2 text-sm font-semibold text-white bg-amber-600 hover:bg-amber-500 rounded-xl shadow-lg shadow-amber-600/20 transition-all">Save Budget</button>
          </div>
        </form>
      </div>
    `;

    document.body.appendChild(this.modalEl);

    this.modalEl.querySelector('#budget-modal-close-btn').addEventListener('click', () => this.close());
    this.modalEl.querySelector('#budget-modal-cancel-btn').addEventListener('click', () => this.close());
    this.modalEl.querySelector('#budget-modal-backdrop').addEventListener('click', () => this.close());

    this.modalEl.querySelector('#edit-budget-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const amt = parseFloat(this.modalEl.querySelector('#budget-amount-input').value);
      if (isNaN(amt) || amt < 0) {
        const err = this.modalEl.querySelector('#budget-modal-error');
        err.textContent = 'Please enter a valid positive budget amount.';
        err.classList.remove('hidden');
        return;
      }
      try {
        if (this.onSaveCallback) {
          await this.onSaveCallback(this.currentCategory, amt);
        }
        this.close();
      } catch (err) {
        const errEl = this.modalEl.querySelector('#budget-modal-error');
        errEl.textContent = err.message || 'Failed to update budget.';
        errEl.classList.remove('hidden');
      }
    });
  }

  open(category, currentBudget, onSave) {
    this.currentCategory = category;
    this.onSaveCallback = onSave;
    this.modalEl.querySelector('#budget-cat-name').textContent = category;
    this.modalEl.querySelector('#budget-amount-input').value = currentBudget || 0;
    this.modalEl.querySelector('#budget-modal-error').classList.add('hidden');

    this.modalEl.classList.remove('hidden');
    requestAnimationFrame(() => {
      this.modalEl.querySelector('#budget-modal-backdrop').classList.replace('opacity-0', 'opacity-100');
      const card = this.modalEl.querySelector('#budget-modal-card');
      card.classList.replace('scale-95', 'scale-100');
      card.classList.replace('opacity-0', 'opacity-100');
    });
  }

  close() {
    this.modalEl.querySelector('#budget-modal-backdrop').classList.replace('opacity-100', 'opacity-0');
    const card = this.modalEl.querySelector('#budget-modal-card');
    card.classList.replace('scale-100', 'scale-95');
    card.classList.replace('opacity-100', 'opacity-0');
    setTimeout(() => this.modalEl.classList.add('hidden'), 200);
  }
}

/**
 * Web-Native Voice & Multimodal Clarification Modal
 * Allows listening to AI question (TTS) and answering via microphone (STT) or text input.
 */
class VoiceClarificationModal {
  constructor() {
    this.modalEl = null;
    this.currentTx = null;
    this.currentQuestionText = '';
    this.onResolvedCallback = null;
    this.mediaRecorder = null;
    this.audioChunks = [];
    this.isRecording = false;
    this.recordingTimer = null;
    this.recordingSeconds = 0;
    this.audioStream = null;
    this.audioElement = new Audio();
    this.isPlayingAudio = false;
    this.isSpeakingSynthesis = false;
    this.recognition = null;

    this.audioElement.addEventListener('ended', () => {
      this._setAudioPlayingState(false);
    });
    this.audioElement.addEventListener('error', () => {
      this._setAudioPlayingState(false);
      // If server audio failed while trying to play, fallback to Web Speech Synthesis
      if (this.currentQuestionText && !this.isSpeakingSynthesis) {
        this._speakWithSpeechSynthesis(this.currentQuestionText);
      }
    });

    // Preload available SpeechSynthesis voices if supported
    if ('speechSynthesis' in window && window.speechSynthesis.onvoiceschanged !== undefined) {
      window.speechSynthesis.onvoiceschanged = () => {
        try {
          window.speechSynthesis.getVoices();
        } catch (_) {}
      };
    }

    this._init();
  }

  _init() {
    let el = document.getElementById('voice-clarification-modal');
    if (el) {
      this.modalEl = el;
      return;
    }

    this.modalEl = document.createElement('div');
    this.modalEl.id = 'voice-clarification-modal';
    this.modalEl.className = 'fixed inset-0 z-50 flex items-center justify-center p-4 hidden';
    this.modalEl.setAttribute('role', 'dialog');
    this.modalEl.setAttribute('aria-modal', 'true');

    this.modalEl.innerHTML = `
      <!-- Backdrop -->
      <div id="voice-modal-backdrop" class="fixed inset-0 bg-slate-950/80 backdrop-blur-md transition-opacity opacity-0"></div>

      <!-- Modal Card -->
      <div id="voice-modal-card" class="relative w-full max-w-lg max-h-[90vh] overflow-y-auto bg-slate-900 border border-amber-500/30 rounded-3xl shadow-2xl p-5 sm:p-6 transform scale-95 opacity-0 transition-all duration-200">
        <!-- Top Glow Effect -->
        <div class="absolute top-0 left-1/2 -translate-x-1/2 w-3/4 h-24 bg-gradient-to-b from-amber-500/10 to-transparent blur-xl pointer-events-none"></div>

        <!-- Header -->
        <div class="flex items-center justify-between pb-4 mb-4 border-b border-slate-800 relative z-10">
          <div class="flex items-center gap-3">
            <div class="p-2.5 rounded-2xl bg-amber-500/15 text-amber-400 border border-amber-500/30 shadow-inner">
              <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
              </svg>
            </div>
            <div>
              <div class="flex items-center gap-2">
                <h3 class="text-base font-bold text-white tracking-tight">AI Voice Clarification</h3>
                <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/40">Voice AI</span>
              </div>
              <p class="text-xs text-slate-400">Speak or type to classify &bull; Interactive Voice Agent</p>
            </div>
          </div>
          <button id="voice-modal-close-btn" type="button" class="text-slate-400 hover:text-white p-1.5 rounded-xl hover:bg-slate-800 transition-colors" title="Close">
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- Transaction Summary Box -->
        <div class="bg-slate-800/60 border border-slate-700/60 rounded-2xl p-4 mb-5 relative z-10">
          <div class="flex items-center justify-between gap-2 mb-1.5">
            <span id="voice-tx-date" class="text-xs font-mono text-slate-400"></span>
            <span id="voice-tx-amount" class="text-base font-bold font-mono text-amber-300"></span>
          </div>
          <div id="voice-tx-desc" class="text-sm font-semibold text-white mb-2 truncate"></div>
          <div class="flex items-center gap-2 text-xs">
            <span class="text-slate-400">Current Category:</span>
            <span id="voice-tx-category" class="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-slate-700 text-slate-200 border border-slate-600"></span>
            <span class="text-amber-400 font-medium text-[11px] ml-auto">⚠️ Needs Human Review</span>
          </div>
        </div>

        <!-- AI Question & Audio Player Section -->
        <div class="bg-gradient-to-r from-indigo-950/40 to-slate-800/40 border border-indigo-500/20 rounded-2xl p-4 mb-5 relative z-10">
          <div class="flex items-start gap-3">
            <button
              id="voice-play-tts-btn"
              type="button"
              class="flex-shrink-0 w-11 h-11 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white flex items-center justify-center shadow-lg shadow-indigo-600/30 transition-all cursor-pointer group"
              title="Play AI Question Audio"
            >
              <svg id="voice-play-icon" class="w-5 h-5 ml-0.5 group-hover:scale-110 transition-transform" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z" />
              </svg>
              <svg id="voice-pause-icon" class="w-5 h-5 hidden" fill="currentColor" viewBox="0 0 24 24">
                <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
              </svg>
            </button>
            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between mb-1">
                <span class="text-xs font-bold text-indigo-300 uppercase tracking-wider flex items-center gap-1.5">
                  AI Question
                  <!-- Sound wave equalizer bars -->
                  <span id="voice-wave-bars" class="inline-flex items-center gap-0.5 opacity-0 transition-opacity">
                    <span class="w-1 h-3 bg-indigo-400 rounded-full animate-bounce"></span>
                    <span class="w-1 h-4 bg-indigo-400 rounded-full animate-bounce" style="animation-delay: 150ms;"></span>
                    <span class="w-1 h-2 bg-indigo-400 rounded-full animate-bounce" style="animation-delay: 300ms;"></span>
                  </span>
                </span>
                <span id="voice-tts-status" class="text-[11px] text-slate-400">Click to listen</span>
              </div>
              <p id="voice-question-text" class="text-xs text-slate-300 leading-relaxed italic">
                Loading question...
              </p>
            </div>
          </div>
        </div>

        <!-- Speech-to-Text & Microphone Section -->
        <div class="text-center py-2 mb-4 relative z-10">
          <div class="inline-block relative">
            <!-- Pulsing ring when recording -->
            <div id="voice-mic-pulse" class="absolute -inset-3 rounded-full bg-rose-500/30 animate-ping hidden"></div>
            
            <button
              id="voice-mic-btn"
              type="button"
              class="relative w-20 h-20 rounded-full bg-gradient-to-tr from-amber-600 to-amber-500 hover:from-amber-500 hover:to-amber-400 text-white flex flex-col items-center justify-center shadow-xl shadow-amber-500/25 border-4 border-slate-900 transition-all cursor-pointer group hover:scale-105 active:scale-95"
            >
              <svg id="voice-mic-icon" class="w-8 h-8 transition-transform group-hover:scale-110" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
              </svg>
              <span id="voice-mic-label" class="text-[10px] font-bold mt-0.5 tracking-tight uppercase">Speak</span>
            </button>
          </div>

          <div id="voice-status-text" class="text-xs font-medium text-slate-300 mt-2.5">
            Tap microphone to speak your reply
          </div>

          <!-- Real-time Live Speech Recognition Transcript -->
          <div id="voice-speech-transcript" class="hidden text-xs font-mono text-amber-200 bg-amber-950/60 border border-amber-500/30 rounded-xl px-3 py-1.5 mt-2 max-w-sm mx-auto shadow-inner"></div>

          <div id="voice-recording-timer" class="text-xs font-mono font-bold text-rose-400 mt-1 hidden">
            00:00
          </div>
        </div>

        <!-- Divider with OR -->
        <div class="flex items-center my-4 relative z-10">
          <div class="flex-grow border-t border-slate-800"></div>
          <span class="px-3 text-[11px] font-semibold uppercase text-slate-500">or 1-click / type category</span>
          <div class="flex-grow border-t border-slate-800"></div>
        </div>

        <!-- 1-Click AI Category Suggestion Chips -->
        <div class="mb-3 relative z-10">
          <div class="text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1.5 flex items-center justify-between">
            <span>Quick Resolve (1-Click):</span>
            <span class="text-amber-400/90 font-normal">Instant AI classification</span>
          </div>
          <div id="voice-quick-chips" class="flex flex-wrap gap-1.5">
            ${['Food', 'Shopping', 'Utilities', 'Investment', 'Transfer', 'Salary', 'Travel', 'Health']
              .map(
                (cat) => `
              <button
                type="button"
                data-quick-cat="${cat}"
                class="px-2.5 py-1 rounded-full text-[11px] font-semibold bg-slate-900 hover:bg-amber-500/20 text-slate-300 hover:text-amber-300 border border-slate-700/80 hover:border-amber-500/50 transition-all cursor-pointer"
              >
                ${cat}
              </button>
            `
              )
              .join('')}
          </div>
        </div>

        <!-- Text Input Fallback -->
        <form id="voice-text-form" class="space-y-3 relative z-10">
          <div class="flex items-center gap-2">
            <input
              id="voice-text-input"
              type="text"
              placeholder="Type the correct category or note (e.g. Groceries, SIP)..."
              class="flex-1 bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-amber-500 focus:ring-1 focus:ring-amber-500"
            />
            <button
              id="voice-text-submit-btn"
              type="submit"
              class="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold border border-indigo-500/50 transition-colors cursor-pointer whitespace-nowrap shadow-sm"
            >
              Save & Resolve
            </button>
          </div>
        </form>

        <!-- Loading Indicator -->
        <div id="voice-loading" class="hidden my-4 p-3 bg-amber-500/10 border border-amber-500/30 rounded-xl flex items-center justify-center gap-2.5 relative z-10">
          <svg class="animate-spin h-4 w-4 text-amber-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
          </svg>
          <span id="voice-loading-text" class="text-xs font-medium text-amber-200">Resolving transaction category...</span>
        </div>

        <!-- Result Box -->
        <div id="voice-result-box" class="hidden my-4 p-4 bg-emerald-950/40 border border-emerald-500/40 rounded-2xl relative z-10">
          <div class="flex items-center gap-2 mb-1 text-emerald-400 font-semibold text-xs">
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
            </svg>
            <span>Resolved Successfully!</span>
          </div>
          <div class="text-xs text-slate-300 mb-1">
            <span class="text-slate-400">Recognized Reply:</span>
            <span id="voice-result-transcription" class="font-medium text-white italic"></span>
          </div>
          <div class="text-xs text-slate-300 flex items-center gap-2">
            <span class="text-slate-400">New Category:</span>
            <span id="voice-result-category" class="px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"></span>
          </div>
        </div>

        <!-- Error Alert -->
        <div id="voice-modal-error" class="hidden mt-3 p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-xs text-rose-300 relative z-10"></div>
      </div>
    `;

    document.body.appendChild(this.modalEl);

    // Wire close handlers
    this.modalEl.querySelector('#voice-modal-close-btn').addEventListener('click', () => this.close());
    this.modalEl.querySelector('#voice-modal-backdrop').addEventListener('click', () => this.close());

    // Wire TTS audio play button
    this.modalEl.querySelector('#voice-play-tts-btn').addEventListener('click', () => this._toggleAudioPlayback());

    // Wire Microphone button
    this.modalEl.querySelector('#voice-mic-btn').addEventListener('click', () => this._toggleRecording());

    // Wire 1-click quick category chips
    this.modalEl.querySelectorAll('[data-quick-cat]').forEach((chip) => {
      chip.addEventListener('click', () => {
        const cat = chip.getAttribute('data-quick-cat');
        if (!cat) return;
        const inputEl = this.modalEl.querySelector('#voice-text-input');
        if (inputEl) inputEl.value = cat;
        this._submitResolution({ textReply: `Category is ${cat}` });
      });
    });

    // Wire Text Form submit
    this.modalEl.querySelector('#voice-text-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const text = this.modalEl.querySelector('#voice-text-input').value.trim();
      if (!text) return;
      this._submitResolution({ textReply: text });
    });
  }

  async open(tx, onResolved) {
    this.currentTx = tx;
    this.onResolvedCallback = onResolved;

    // Reset UI state
    this._resetState();

    // Populate transaction basic details
    this.modalEl.querySelector('#voice-tx-date').textContent = formatDate(tx.date);
    this.modalEl.querySelector('#voice-tx-amount').textContent = formatCurrency(tx.amount);
    this.modalEl.querySelector('#voice-tx-desc').textContent = tx.description || 'Transaction';
    this.modalEl.querySelector('#voice-tx-category').textContent = tx.category || 'Other';

    // Show modal
    this.modalEl.classList.remove('hidden');
    requestAnimationFrame(() => {
      this.modalEl.querySelector('#voice-modal-backdrop').classList.replace('opacity-0', 'opacity-100');
      const card = this.modalEl.querySelector('#voice-modal-card');
      card.classList.replace('scale-95', 'scale-100');
      card.classList.replace('opacity-0', 'opacity-100');
    });

    const catDisp =
      tx.category && tx.category.toLowerCase() !== 'other' ? tx.category : 'Uncategorized';
    const questionText = `I found a ${formatCurrency(tx.amount)} transaction for ${
      tx.description || 'Transaction'
    }. The current category is ${catDisp}, which needs clarification. What should I categorize this as?`;

    this.currentQuestionText = questionText;
    this.modalEl.querySelector('#voice-question-text').textContent = `"${questionText}"`;

    // Immediately speak the question without waiting for a preliminary metadata round-trip
    this._playQuestionAudio(questionText);
  }

  _resetState() {
    this._stopRecordingIfActive();
    this._stopAudioPlayback();
    this.modalEl.querySelector('#voice-modal-error').classList.add('hidden');
    this.modalEl.querySelector('#voice-loading').classList.add('hidden');
    this.modalEl.querySelector('#voice-result-box').classList.add('hidden');
    const transcriptEl = this.modalEl.querySelector('#voice-speech-transcript');
    if (transcriptEl) {
      transcriptEl.textContent = '';
      transcriptEl.classList.add('hidden');
    }
    this.modalEl.querySelector('#voice-text-input').value = '';
    this.modalEl.querySelector('#voice-text-input').disabled = false;
    this.modalEl.querySelector('#voice-text-submit-btn').disabled = false;
    this.modalEl.querySelector('#voice-mic-btn').disabled = false;
    this.modalEl.querySelector('#voice-status-text').textContent = 'Tap microphone to speak your reply';
    this.modalEl.querySelector('#voice-recording-timer').classList.add('hidden');
    this.modalEl.querySelector('#voice-mic-pulse').classList.add('hidden');
    this._setAudioPlayingState(false);
  }

  async _playQuestionAudio(questionText) {
    const textToSpeak = questionText || this.currentQuestionText;
    if (!textToSpeak) return;

    this._stopAudioPlayback();

    // 1. Instant zero-latency playback using browser Web Speech Synthesis (runs synchronously in user gesture)
    if ('speechSynthesis' in window) {
      this._speakWithSpeechSynthesis(textToSpeak);
      return;
    }

    // 2. Fallback to server Neural2 TTS audio if browser does not support SpeechSynthesis.
    // Fetched through the API client so the request carries the sign-in token.
    try {
      const blob = await fetchClarificationAudio(
        this.currentTx.id,
        {
          description: this.currentTx.description || 'Transaction',
          amount: this.currentTx.amount || 0,
          category: this.currentTx.category || 'Other',
        },
        { timeout: 2500 }
      );
      if (blob) {
        const objectUrl = URL.createObjectURL(blob);
        this.audioElement.src = objectUrl;
        this._setAudioPlayingState(true);
        await this.audioElement.play();
      }
    } catch (err) {
      console.info('[VoiceModal] Server TTS audio playback unavailable:', err);
    }
  }

  _speakWithSpeechSynthesis(text) {
    if (!('speechSynthesis' in window)) {
      this.modalEl.querySelector('#voice-tts-status').textContent = 'Audio ready';
      return;
    }

    try {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.0;
      utterance.pitch = 1.0;
      utterance.lang = 'en-US';

      // Select best natural voice
      const voices = window.speechSynthesis.getVoices();
      if (voices && voices.length > 0) {
        const preferredVoice = voices.find((v) => v.lang.startsWith('en') && (
          v.name.includes('Natural') ||
          v.name.includes('Google') ||
          v.name.includes('Samantha') ||
          v.name.includes('Zira') ||
          v.name.includes('Karen') ||
          v.name.includes('Neural')
        )) || voices.find((v) => v.lang.startsWith('en')) || voices[0];

        if (preferredVoice) {
          utterance.voice = preferredVoice;
        }
      }

      utterance.onstart = () => {
        this.isSpeakingSynthesis = true;
        this._setAudioPlayingState(true);
      };

      utterance.onend = () => {
        this.isSpeakingSynthesis = false;
        this._setAudioPlayingState(false);
        this.modalEl.querySelector('#voice-status-text').textContent = 'Tap microphone to speak your reply';
      };

      utterance.onerror = (e) => {
        console.warn('[VoiceModal] Speech synthesis error:', e);
        this.isSpeakingSynthesis = false;
        this._setAudioPlayingState(false);
      };

      window.speechSynthesis.speak(utterance);
    } catch (err) {
      console.warn('[VoiceModal] Failed to speak with SpeechSynthesis:', err);
      this._setAudioPlayingState(false);
    }
  }

  _toggleAudioPlayback() {
    if (this.isPlayingAudio) {
      this._stopAudioPlayback();
    } else {
      this._playQuestionAudio(this.currentQuestionText);
    }
  }

  _stopAudioPlayback() {
    if (this.audioElement) {
      this.audioElement.pause();
      this.audioElement.currentTime = 0;
    }
    if ('speechSynthesis' in window) {
      try {
        window.speechSynthesis.cancel();
      } catch (_) {}
    }
    this.isSpeakingSynthesis = false;
    this._setAudioPlayingState(false);
  }

  _setAudioPlayingState(playing) {
    this.isPlayingAudio = playing;
    const playIcon = this.modalEl.querySelector('#voice-play-icon');
    const pauseIcon = this.modalEl.querySelector('#voice-pause-icon');
    const waveBars = this.modalEl.querySelector('#voice-wave-bars');
    const statusText = this.modalEl.querySelector('#voice-tts-status');

    if (playing) {
      playIcon.classList.add('hidden');
      pauseIcon.classList.remove('hidden');
      waveBars.classList.replace('opacity-0', 'opacity-100');
      statusText.textContent = 'Speaking...';
    } else {
      playIcon.classList.remove('hidden');
      pauseIcon.classList.add('hidden');
      waveBars.classList.replace('opacity-100', 'opacity-0');
      statusText.textContent = 'Click to listen';
    }
  }

  async _toggleRecording() {
    if (this.isRecording) {
      this._stopRecording();
    } else {
      await this._startListening();
    }
  }

  async _startListening() {
    this._stopAudioPlayback();
    this.modalEl.querySelector('#voice-modal-error').classList.add('hidden');
    const transcriptEl = this.modalEl.querySelector('#voice-speech-transcript');
    if (transcriptEl) {
      transcriptEl.textContent = '';
      transcriptEl.classList.add('hidden');
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (SpeechRecognition) {
      this._startSpeechRecognition(SpeechRecognition);
    } else {
      await this._startMediaRecorder();
    }
  }

  _startSpeechRecognition(SpeechRecognition) {
    try {
      this.recognition = new SpeechRecognition();
      this.recognition.continuous = false;
      this.recognition.interimResults = true;
      this.recognition.maxAlternatives = 1;
      this.recognition.lang = 'en-US';

      let recognizedFinal = '';
      let recognizedInterim = '';
      let fallbackTriggered = false;

      this.recognition.onstart = () => {
        this.isRecording = true;
        this._setRecordingUI(true);
        this.modalEl.querySelector('#voice-status-text').textContent = 'Listening... Speak your reply now.';
      };

      this.recognition.onresult = (event) => {
        recognizedFinal = '';
        recognizedInterim = '';

        for (let i = event.resultIndex; i < event.results.length; ++i) {
          const item = event.results[i];
          if (item.isFinal) {
            recognizedFinal += item[0].transcript;
          } else {
            recognizedInterim += item[0].transcript;
          }
        }

        const heardText = recognizedFinal || recognizedInterim;
        if (heardText) {
          const transcriptEl = this.modalEl.querySelector('#voice-speech-transcript');
          if (transcriptEl) {
            transcriptEl.textContent = `Heard: "${heardText}"`;
            transcriptEl.classList.remove('hidden');
          }
          this.modalEl.querySelector('#voice-status-text').textContent = `Listening: "${heardText}"`;
          this.modalEl.querySelector('#voice-text-input').value = heardText;
        }
      };

      this.recognition.onspeechend = () => {
        this.modalEl.querySelector('#voice-status-text').textContent = 'Processing speech...';
        try {
          this.recognition.stop();
        } catch (_) {}
      };

      this.recognition.onend = () => {
        if (fallbackTriggered) return;
        this.isRecording = false;
        this._setRecordingUI(false);

        const finalText = (recognizedFinal || recognizedInterim || this.modalEl.querySelector('#voice-text-input').value).trim();
        if (finalText) {
          this._submitResolution({ textReply: finalText });
        } else {
          this.modalEl.querySelector('#voice-status-text').textContent = 'No speech detected. Tap mic to retry or type below.';
        }
      };

      this.recognition.onerror = async (event) => {
        console.warn('[VoiceModal] Speech recognition error:', event.error);

        if (event.error === 'not-allowed') {
          this.isRecording = false;
          this._setRecordingUI(false);
          this._showError('Microphone access was denied. Please allow microphone permissions or tap a category chip below.');
        } else if (event.error === 'no-speech') {
          this.isRecording = false;
          this._setRecordingUI(false);
          this.modalEl.querySelector('#voice-status-text').textContent = 'No speech detected. Tap mic to retry or tap a category chip below.';
        } else {
          fallbackTriggered = true;
          console.info('[VoiceModal] Falling back to MediaRecorder...');
          await this._startMediaRecorder();
        }
      };

      this.recognition.start();
    } catch (err) {
      console.warn('[VoiceModal] Failed to start SpeechRecognition:', err);
      this._startMediaRecorder();
    }
  }

  async _startMediaRecorder() {
    this.modalEl.querySelector('#voice-modal-error').classList.add('hidden');

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this._showError('Microphone recording is not supported in this browser. Please tap a category chip or type your reply.');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.audioStream = stream;

      const candidateTypes = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/mp4',
        'audio/ogg;codecs=opus',
      ];
      const mimeType =
        typeof MediaRecorder !== 'undefined' && typeof MediaRecorder.isTypeSupported === 'function'
          ? candidateTypes.find((t) => MediaRecorder.isTypeSupported(t)) || ''
          : '';

      const options = mimeType ? { mimeType } : undefined;
      this.mediaRecorder = new MediaRecorder(stream, options);
      this.audioChunks = [];

      this.mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          this.audioChunks.push(e.data);
        }
      };

      this.mediaRecorder.onstop = async () => {
        const recordedMime = this.mediaRecorder.mimeType || mimeType || 'audio/webm';
        const audioBlob = new Blob(this.audioChunks, {
          type: recordedMime,
        });
        if (this.audioStream) {
          this.audioStream.getTracks().forEach((track) => track.stop());
          this.audioStream = null;
        }
        const textReply = this.modalEl.querySelector('#voice-text-input').value.trim() || undefined;
        await this._submitResolution({ audioBlob, textReply });
      };

      this.mediaRecorder.start(250);
      this.isRecording = true;
      this._setRecordingUI(true);

      // Start timer
      this.recordingSeconds = 0;
      const timerEl = this.modalEl.querySelector('#voice-recording-timer');
      timerEl.textContent = '00:00';
      timerEl.classList.remove('hidden');

      this.recordingTimer = setInterval(() => {
        this.recordingSeconds += 1;
        const mins = String(Math.floor(this.recordingSeconds / 60)).padStart(2, '0');
        const secs = String(this.recordingSeconds % 60).padStart(2, '0');
        timerEl.textContent = `${mins}:${secs}`;
      }, 1000);
    } catch (err) {
      console.error('Error accessing microphone:', err);
      let msg = 'Failed to access microphone.';
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        msg = 'Microphone permission was denied. Please allow microphone access in your browser settings.';
      } else if (err.name === 'NotFoundError') {
        msg = 'No microphone device was detected on your system.';
      }
      this._showError(msg);
      this._setRecordingUI(false);
    }
  }

  _stopRecording() {
    if (this.recognition) {
      try {
        this.recognition.stop();
      } catch (_) {}
    }
    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      try {
        this.mediaRecorder.stop();
      } catch (_) {}
    }
    this.isRecording = false;
    clearInterval(this.recordingTimer);
    this._setRecordingUI(false);
  }

  _stopRecordingIfActive() {
    if (this.isRecording) {
      this._stopRecording();
    }
    if (this.recognition) {
      try {
        this.recognition.abort();
      } catch (_) {}
      this.recognition = null;
    }
    if (this.audioStream) {
      this.audioStream.getTracks().forEach((track) => track.stop());
      this.audioStream = null;
    }
  }

  _setRecordingUI(recording) {
    const micBtn = this.modalEl.querySelector('#voice-mic-btn');
    const micLabel = this.modalEl.querySelector('#voice-mic-label');
    const micPulse = this.modalEl.querySelector('#voice-mic-pulse');
    const statusText = this.modalEl.querySelector('#voice-status-text');

    if (recording) {
      micBtn.classList.replace('from-amber-600', 'from-rose-600');
      micBtn.classList.replace('to-amber-500', 'to-rose-500');
      micBtn.classList.add('shadow-rose-500/40');
      micPulse.classList.remove('hidden');
      micLabel.textContent = 'Stop';
      statusText.textContent = 'Listening... Speak your category reply now.';
      statusText.classList.add('text-rose-300');
    } else {
      micBtn.classList.replace('from-rose-600', 'from-amber-600');
      micBtn.classList.replace('to-rose-500', 'to-amber-500');
      micBtn.classList.remove('shadow-rose-500/40');
      micPulse.classList.add('hidden');
      micLabel.textContent = 'Speak';
      statusText.textContent = 'Tap microphone to speak your reply';
      statusText.classList.remove('text-rose-300');
    }
  }

  async _submitResolution({ textReply, audioBlob }) {
    const errorEl = this.modalEl.querySelector('#voice-modal-error');
    const loadingEl = this.modalEl.querySelector('#voice-loading');
    const loadingText = this.modalEl.querySelector('#voice-loading-text');
    const resultBox = this.modalEl.querySelector('#voice-result-box');
    const textInput = this.modalEl.querySelector('#voice-text-input');
    const textSubmitBtn = this.modalEl.querySelector('#voice-text-submit-btn');
    const micBtn = this.modalEl.querySelector('#voice-mic-btn');

    errorEl.classList.add('hidden');
    resultBox.classList.add('hidden');
    loadingEl.classList.remove('hidden');

    loadingText.textContent = 'Resolving transaction category...';

    textInput.disabled = true;
    textSubmitBtn.disabled = true;
    micBtn.disabled = true;

    try {
      const res = await resolveTransactionCategory(this.currentTx.id, {
        textReply,
        audioBlob,
      });

      loadingEl.classList.add('hidden');

      // Show result
      const recognized = res.transcribed_text || textReply || 'Spoken reply';
      this.modalEl.querySelector('#voice-result-transcription').textContent = `"${recognized}"`;
      this.modalEl.querySelector('#voice-result-category').textContent = res.resolved_category || 'Other';
      resultBox.classList.remove('hidden');

      // Speak confirmation out loud to the user
      const confirmedCategory = res.resolved_category || 'Other';
      if ('speechSynthesis' in window) {
        try {
          const utterance = new SpeechSynthesisUtterance(`Categorized as ${confirmedCategory}`);
          utterance.rate = 1.0;
          window.speechSynthesis.speak(utterance);
        } catch (_) {}
      }

      // Update current transaction locally
      this.currentTx.category = res.resolved_category;
      this.currentTx.requires_human_review = false;
      this.currentTx.confidence = 100;

      if (this.onResolvedCallback) {
        this.onResolvedCallback(res, this.currentTx.id);
      }

      // Close modal smoothly after 1.5 seconds
      setTimeout(() => {
        this.close();
      }, 1500);
    } catch (err) {
      loadingEl.classList.add('hidden');
      this._showError(err.message || 'Failed to resolve category. Please try again.');
    } finally {
      textInput.disabled = false;
      textSubmitBtn.disabled = false;
      micBtn.disabled = false;
    }
  }

  _showError(msg) {
    const errEl = this.modalEl.querySelector('#voice-modal-error');
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  }

  close() {
    this._stopRecordingIfActive();
    this._stopAudioPlayback();
    if (this.recognition) {
      try {
        this.recognition.abort();
      } catch (_) {}
      this.recognition = null;
    }

    this.modalEl.querySelector('#voice-modal-backdrop').classList.replace('opacity-100', 'opacity-0');
    const card = this.modalEl.querySelector('#voice-modal-card');
    card.classList.replace('scale-100', 'scale-95');
    card.classList.replace('opacity-100', 'opacity-0');
    setTimeout(() => this.modalEl.classList.add('hidden'), 200);
  }
}

export const editModal = new EditTransactionModal();
export const addTransactionModal = new AddTransactionModal();
export const budgetModal = new BudgetModal();
export const voiceClarificationModal = new VoiceClarificationModal();
