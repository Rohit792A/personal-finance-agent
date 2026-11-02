/**
 * Formatting utilities for currency, dates, and badges.
 */

const CURRENCY_FORMATTER = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * Format a numeric amount to a standard currency string.
 * @param {number|string} amount
 * @returns {string}
 */
export function formatCurrency(amount) {
  const num = typeof amount === 'number' ? amount : parseFloat(amount);
  if (isNaN(num)) return '₹0.00';
  return CURRENCY_FORMATTER.format(num);
}

/**
 * Format currency with explicit colorblind-accessible sign prefix (+, -, or ⇄).
 * @param {number|string} amount
 * @param {string} type - 'INCOME' | 'EXPENSE' | 'TRANSFER'
 * @returns {string}
 */
export function formatSignedCurrency(amount, type = 'EXPENSE') {
  const num = Math.abs(typeof amount === 'number' ? amount : parseFloat(amount) || 0);
  const base = CURRENCY_FORMATTER.format(num);
  const upper = (type || 'EXPENSE').toUpperCase();
  if (upper === 'INCOME') return `+${base}`;
  if (upper === 'TRANSFER') return `⇄ ${base}`;
  return `-${base}`;
}

/**
 * Format date string into a user-friendly format (e.g., 'Sep 06, 2026').
 * @param {string} dateStr
 * @returns {string}
 */
export function formatDate(dateStr) {
  if (!dateStr) return 'N/A';
  try {
    const parsed = new Date(dateStr);
    if (isNaN(parsed.getTime())) return dateStr;
    return parsed.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      timeZone: 'UTC', // Ensure consistent date display
    });
  } catch {
    return dateStr;
  }
}

/**
 * Format YYYY-MM or YYYY-MM-DD into readable Month Year (e.g. 'July 2026').
 * @param {string} dateStr
 * @returns {string}
 */
export function formatMonthYear(dateStr) {
  if (!dateStr || dateStr === 'ALL') return 'All Months';
  try {
    const parts = dateStr.slice(0, 7).split('-');
    if (parts.length === 2) {
      const year = parseInt(parts[0], 10);
      const month = parseInt(parts[1], 10) - 1;
      const d = new Date(Date.UTC(year, month, 1));
      return d.toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
    }
    return dateStr;
  } catch {
    return dateStr;
  }
}

/**
 * Return Tailwind badge classes based on transaction category.
 * @param {string} category
 * @returns {string}
 */
export function getCategoryBadgeClass(category = '') {
  const cat = (category || '').toLowerCase();

  if (cat.includes('food') || cat.includes('dining') || cat.includes('grocery')) {
    return 'bg-amber-500/15 text-amber-300 border-amber-500/30';
  }
  if (cat.includes('paycheck') || cat.includes('income') || cat.includes('bonus') || cat.includes('salary')) {
    return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30';
  }
  if (cat.includes('transfer')) {
    return 'bg-sky-500/15 text-sky-300 border-sky-500/30';
  }
  if (cat.includes('rent') || cat.includes('home') || cat.includes('utilities')) {
    return 'bg-blue-500/15 text-blue-300 border-blue-500/30';
  }
  if (cat.includes('health') || cat.includes('medical')) {
    return 'bg-rose-500/15 text-rose-300 border-rose-500/30';
  }
  if (cat.includes('travel') || cat.includes('transportation')) {
    return 'bg-purple-500/15 text-purple-300 border-purple-500/30';
  }
  if (cat.includes('invest') || cat.includes('sip') || cat.includes('stocks') || cat.includes('saving')) {
    return 'bg-teal-500/15 text-teal-300 border-teal-500/30';
  }
  if (cat.includes('debt')) {
    return 'bg-orange-500/15 text-orange-300 border-orange-500/30';
  }
  return 'bg-slate-700/50 text-slate-300 border-slate-600/50';
}

/**
 * Return Tailwind badge classes for transaction type.
 * @param {string} type
 * @returns {string}
 */
export function getTypeBadgeClass(type = '') {
  const upper = (type || 'EXPENSE').toUpperCase();
  if (upper === 'INCOME') {
    return 'bg-emerald-950/80 text-emerald-400 border-emerald-700/60';
  }
  if (upper === 'TRANSFER') {
    return 'bg-sky-950/80 text-sky-400 border-sky-700/60';
  }
  return 'bg-slate-800/90 text-slate-200 border-slate-600/60';
}
