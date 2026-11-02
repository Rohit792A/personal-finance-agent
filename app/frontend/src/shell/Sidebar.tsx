import { X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { NavLink } from 'react-router';
import { Logo } from '../ui/Logo';
import { SCREENS } from './screens';
import { screenSearch } from './urlState';

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ` +
  `focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus ` +
  (isActive ? 'bg-accent-soft text-accent-text' : 'text-fg-muted hover:bg-surface-2 hover:text-fg');

function Brand() {
  return (
    <div className="flex items-center gap-2 px-2">
      <Logo className="h-8 w-8" />
      <span className="text-base font-semibold text-fg">Finance AI</span>
    </div>
  );
}

function NavItems({ month, onNavigate }: { month: string; onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="flex-1 overflow-y-auto px-3 py-4">
      <ul className="space-y-1">
        {SCREENS.map((screen) => {
          const Icon = screen.icon;
          return (
            <li key={screen.id}>
              <NavLink
                to={{ pathname: screen.path, search: screenSearch(month) }}
                className={linkClass}
                onClick={onNavigate}
                end
              >
                <Icon aria-hidden="true" className="h-5 w-5 shrink-0" />
                <span>{screen.label}</span>
              </NavLink>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** Persistent sidebar on large screens; a modal drawer below the lg breakpoint. */
export function Sidebar({ open, month, onClose }: { open: boolean; month: string; onClose: () => void }) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [open, onClose]);

  return (
    <>
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-line bg-surface lg:flex">
        <div className="flex h-14 items-center border-b border-line px-3">
          <Brand />
        </div>
        <NavItems month={month} />
      </aside>

      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/50" aria-hidden="true" onClick={onClose} />
          <div className="absolute inset-y-0 left-0 flex w-72 max-w-[85%] flex-col border-r border-line bg-surface shadow-xl">
            <div className="flex h-14 items-center justify-between border-b border-line px-3">
              <Brand />
              <button
                ref={closeButtonRef}
                type="button"
                onClick={onClose}
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus"
                aria-label="Close navigation"
              >
                <X aria-hidden="true" className="h-5 w-5" />
              </button>
            </div>
            <NavItems month={month} onNavigate={onClose} />
          </div>
        </div>
      ) : null}
    </>
  );
}
