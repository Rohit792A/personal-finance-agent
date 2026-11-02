/**
 * DOM Helper Utilities
 */

/**
 * Escape raw strings to prevent XSS attacks when rendering dynamic content.
 * @param {string} str
 * @returns {string}
 */
export function escapeHtml(str) {
  if (typeof str !== 'string') {
    return String(str ?? '');
  }
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/**
 * Shorthand querySelector with parent fallback to document.
 * @param {string} selector
 * @param {ParentNode} [parent=document]
 * @returns {Element|null}
 */
export function qs(selector, parent = document) {
  return parent.querySelector(selector);
}

/**
 * Shorthand querySelectorAll returning an array.
 * @param {string} selector
 * @param {ParentNode} [parent=document]
 * @returns {Element[]}
 */
export function qsa(selector, parent = document) {
  return Array.from(parent.querySelectorAll(selector));
}
