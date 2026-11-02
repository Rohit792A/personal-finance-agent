/**
 * Recorded demo: a small notice that a change wasn't saved (or that a feature needs the live app).
 * Plain DOM rather than React, so the API layer can show it for every screen, legacy ones included.
 */
const NOTICE_ID = 'recorded-demo-notice';
const VISIBLE_MS = 4500;

let hideTimer: number | undefined;

export function showRecordedNotice(message: string): void {
  let notice = document.getElementById(NOTICE_ID);
  if (!notice) {
    notice = document.createElement('div');
    notice.id = NOTICE_ID;
    notice.setAttribute('role', 'status');
    notice.setAttribute('aria-live', 'polite');
    notice.className =
      'fixed left-1/2 top-20 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 rounded-lg border ' +
      'border-accent/30 bg-surface px-4 py-3 text-sm text-fg shadow-lg';
    notice.dataset.testid = NOTICE_ID;
    document.body.append(notice);
  }
  const element = notice;
  element.textContent = message;
  element.hidden = false;
  window.clearTimeout(hideTimer);
  hideTimer = window.setTimeout(() => {
    element.hidden = true;
  }, VISIBLE_MS);
}
