/**
 * Toast Notification Manager
 * Manages animated, stackable toast notifications for feedback and alerts.
 */

class ToastManager {
  constructor() {
    this.container = null;
    this._ensureContainer();
  }

  _ensureContainer() {
    if (this.container && document.body.contains(this.container)) {
      return;
    }

    let existing = document.getElementById('toast-container');
    if (existing) {
      this.container = existing;
      return;
    }

    this.container = document.createElement('div');
    this.container.id = 'toast-container';
    this.container.className =
      'fixed bottom-5 right-5 z-50 flex flex-col gap-2.5 max-w-md w-full pointer-events-none px-4 sm:px-0';
    document.body.appendChild(this.container);
  }

  /**
   * Display a toast notification.
   * 
   * @param {string} message - Message text (rendered as plain text, never as HTML)
   * @param {'success'|'error'|'info'|'warning'} [type='info'] - Severity type
   * @param {number} [duration=5000] - Duration in ms before auto-dismiss
   */
  show(message, type = 'info', duration = 5000) {
    this._ensureContainer();

    const icons = {
      success: `
        <svg class="w-5 h-5 text-emerald-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
      `,
      error: `
        <svg class="w-5 h-5 text-rose-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
      `,
      warning: `
        <svg class="w-5 h-5 text-amber-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
        </svg>
      `,
      info: `
        <svg class="w-5 h-5 text-indigo-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
      `,
    };

    const borders = {
      success: 'border-emerald-500/30 bg-slate-900/95 text-emerald-100 shadow-emerald-950/40',
      error: 'border-rose-500/30 bg-slate-900/95 text-rose-100 shadow-rose-950/40',
      warning: 'border-amber-500/30 bg-slate-900/95 text-amber-100 shadow-amber-950/40',
      info: 'border-indigo-500/30 bg-slate-900/95 text-slate-100 shadow-indigo-950/40',
    };

    const toast = document.createElement('div');
    toast.className = `pointer-events-auto flex items-start gap-3 p-4 rounded-xl border backdrop-blur-md shadow-xl transition-all duration-300 transform translate-y-2 opacity-0 ${borders[type] || borders.info}`;
    
    toast.innerHTML = `
      ${icons[type] || icons.info}
      <div class="flex-1 text-sm leading-snug pt-0.5 break-words font-medium" data-toast-message></div>
      <button type="button" aria-label="Dismiss notification" class="text-slate-400 hover:text-white transition-colors flex-shrink-0 -mr-1 -mt-1 p-1 rounded-lg hover:bg-slate-800">
        <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
        </svg>
      </button>
    `;
    // Messages can contain server errors, file names or AI text, so never parse them as HTML.
    toast.querySelector('[data-toast-message]').textContent = String(message ?? '');

    this.container.appendChild(toast);

    // Trigger enter animation
    requestAnimationFrame(() => {
      toast.classList.remove('translate-y-2', 'opacity-0');
      toast.classList.add('translate-y-0', 'opacity-100');
    });

    const remove = () => {
      toast.classList.remove('translate-y-0', 'opacity-100');
      toast.classList.add('opacity-0', 'translate-y-2');
      setTimeout(() => {
        if (toast.parentElement) {
          toast.remove();
        }
      }, 300);
    };

    const closeBtn = toast.querySelector('button');
    closeBtn.addEventListener('click', remove);

    if (duration > 0) {
      setTimeout(remove, duration);
    }
  }

  /**
   * Display a succession of toasts (e.g. for multiple notification messages from an upload response).
   * 
   * @param {string[]} messages
   * @param {'success'|'error'|'info'|'warning'} [type='info']
   * @param {number} [staggerMs=300]
   */
  showBatch(messages = [], type = 'info', staggerMs = 300) {
    if (!Array.isArray(messages)) return;
    messages.forEach((msg, idx) => {
      setTimeout(() => {
        this.show(msg, type);
      }, idx * staggerMs);
    });
  }
}

export const toast = new ToastManager();
