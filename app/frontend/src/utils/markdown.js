/**
 * Markdown Rendering Helper
 * Converts AI-generated markdown to HTML and sanitizes it with DOMPurify before it reaches innerHTML.
 * Model output can echo merchant names, email text or prompt-injected content, so it is never trusted.
 */

import { marked } from 'marked';
import DOMPurify from 'dompurify';

/**
 * Configure marked for safe, clean parsing
 */
marked.setOptions({
  gfm: true,
  breaks: true,
});

// Advisor markdown only needs text formatting, lists, tables, code and links. Everything else
// (images, media, SVG, forms, inline styles, classes, ids, data-* attributes) is dropped: none of it
// is needed, and it can be abused (a remote image URL can leak data, an injected form or full-screen
// overlay can phish for credentials, an injected id can clash with the app's own elements).
const ALLOWED_TAGS = [
  'a', 'b', 'blockquote', 'br', 'code', 'del', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr',
  'i', 'li', 'ol', 'p', 'pre', 's', 'strong', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul',
];
const ALLOWED_ATTR = ['href', 'title', 'align', 'start'];

/**
 * Render markdown to sanitized HTML that is safe to assign to innerHTML.
 * DOMPurify also removes scripts, event-handler attributes and javascript: URLs; text inside
 * disallowed tags is kept as plain text.
 *
 * @param {string} markdown
 * @returns {string}
 */
export function renderMarkdown(markdown) {
  const rawHtml = marked.parse(String(markdown ?? ''));
  return DOMPurify.sanitize(rawHtml, { ALLOWED_TAGS, ALLOWED_ATTR, ALLOW_DATA_ATTR: false });
}
