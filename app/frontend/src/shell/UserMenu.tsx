import { ChevronDown, LogOut } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import type { Environment } from '../api/me';
import { useSession } from '../session/SessionContext';
import { buttonClass } from '../ui/classes';

const ENVIRONMENT_LABEL: Record<Environment, string> = { personal: 'Personal', demo: 'Demo' };

export function EnvironmentBadge({ environment }: { environment: Environment }) {
  const tone =
    environment === 'demo'
      ? 'border-warning/30 bg-warning-soft text-warning'
      : 'border-accent/20 bg-accent-soft text-accent-text';
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}
      data-testid="environment-badge"
    >
      {ENVIRONMENT_LABEL[environment]}
    </span>
  );
}

const humanize = (key: string): string => {
  const text = key.replace(/[_-]+/g, ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : key;
};

/** Account disclosure: email, environment, the demo's AI quota and sign out. */
export function UserMenu() {
  const { me, signOut, refreshMe } = useSession();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const hasLimits = me.ai_limits !== null && Object.keys(me.ai_limits).length > 0;

  useEffect(() => {
    if (!open) return;
    // The demo's AI quota changes as it is used; show fresh numbers.
    if (hasLimits) void refreshMe();
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !containerRef.current?.contains(event.target)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
    // refreshMe is stable; re-running only when the menu opens or closes is intended.
  }, [open, hasLimits, refreshMe]);

  const initial = (me.email.trim().charAt(0) || '?').toUpperCase();

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => {
          setOpen((value) => !value);
        }}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`Account menu for ${me.email || 'this account'}`}
        className="flex h-9 items-center gap-2 rounded-lg px-1.5 text-sm text-fg hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus"
        data-testid="user-menu-button"
      >
        <span
          aria-hidden="true"
          className="grid h-7 w-7 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent-text"
        >
          {initial}
        </span>
        <span aria-hidden="true" className="hidden max-w-[14rem] truncate md:inline">
          {me.email}
        </span>
        <ChevronDown aria-hidden="true" className="h-4 w-4 text-fg-muted" />
      </button>

      {open ? (
        <div
          id={panelId}
          className="absolute right-0 z-30 mt-2 w-72 rounded-xl border border-line bg-surface p-4 shadow-lg"
          data-testid="user-menu"
        >
          <p className="text-xs text-fg-muted">Signed in as</p>
          <p className="truncate text-sm font-medium text-fg" title={me.email}>
            {me.email}
          </p>
          <div className="mt-2">
            <EnvironmentBadge environment={me.environment} />
          </div>

          {hasLimits && me.ai_limits ? (
            <div className="mt-3 border-t border-line pt-3">
              <p className="text-xs font-medium text-fg-muted">AI requests left today</p>
              <ul className="mt-1 space-y-1 text-sm text-fg">
                {Object.entries(me.ai_limits).map(([feature, limit]) => (
                  <li key={feature} className="flex justify-between gap-3">
                    <span>{humanize(feature)}</span>
                    <span className="tabular-nums text-fg-muted">
                      {limit.remaining} of {limit.limit}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <button
            type="button"
            onClick={() => void signOut()}
            className={`${buttonClass('secondary', 'sm')} mt-4 w-full`}
          >
            <LogOut aria-hidden="true" className="h-4 w-4" />
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
