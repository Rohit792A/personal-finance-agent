import { TriangleAlert } from 'lucide-react';
import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router';
import { SCREENS, type ScreenId } from '../shell/screens';
import { parseUrlState } from '../shell/urlState';
import { buttonClass } from '../ui/classes';
import { Spinner } from '../ui/Spinner';
import { legacyContainerId, type Legacy, type LegacyBridge } from './bridge';

interface LegacyScreensProps {
  bridge: LegacyBridge;
  activeScreen: ScreenId | null;
  /** Screens replaced by a React panel (feature turned off); their legacy code never loads. */
  unavailable: Partial<Record<ScreenId, ReactNode>>;
}

/**
 * Persistent containers for the legacy screens. Every screen has one container that stays in the
 * DOM for the whole session; only its visibility changes with the route, so a screen keeps its
 * state (filters, chat history, scroll) and is never initialised twice.
 */
export function LegacyScreens({ bridge, activeScreen, unavailable }: LegacyScreensProps) {
  const [legacy, setLegacy] = useState<Legacy | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const { search } = useLocation();
  const urlState = useMemo(() => parseUrlState(search), [search]);
  const activeIsUnavailable = activeScreen !== null && unavailable[activeScreen] !== undefined;

  useEffect(() => {
    bridge.activate();
    return () => {
      bridge.deactivate();
    };
  }, [bridge]);

  // Load the legacy screens only now that the user is signed in and /me succeeded.
  useEffect(() => {
    let cancelled = false;
    import('./index.js').then(
      (module) => {
        if (cancelled) return;
        bridge.attach(module);
        setLegacy(module);
      },
      () => {
        if (!cancelled) setLoadFailed(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [bridge]);

  // URL -> mounted screens. Runs before mounting so a newly mounted screen starts from the URL state.
  useEffect(() => {
    bridge.sync(urlState, activeScreen);
  }, [bridge, urlState, activeScreen]);

  useEffect(() => {
    if (!legacy || activeScreen === null || activeIsUnavailable) return;
    const container = document.getElementById(legacyContainerId(activeScreen));
    if (container) bridge.mount(activeScreen, container);
  }, [bridge, legacy, activeScreen, activeIsUnavailable]);

  return (
    <>
      {SCREENS.map((screen) => {
        const isActive = screen.id === activeScreen;
        const replacement = unavailable[screen.id];
        return (
          <Fragment key={screen.id}>
            {isActive && replacement !== undefined ? replacement : null}
            {isActive && replacement === undefined && !legacy ? (
              loadFailed ? (
                <LoadFailed />
              ) : (
                <div className="flex items-center justify-center gap-2 py-24 text-sm text-fg-muted" role="status">
                  <Spinner /> Loading {screen.label}…
                </div>
              )
            ) : null}
            <section
              id={legacyContainerId(screen.id)}
              data-screen={screen.id}
              aria-label={screen.label}
              className="legacy-scope"
              hidden={!isActive || replacement !== undefined}
            />
          </Fragment>
        );
      })}
    </>
  );
}

function LoadFailed() {
  return (
    <div className="mx-auto max-w-md rounded-xl border border-line bg-surface p-6 text-center" role="alert">
      <TriangleAlert aria-hidden="true" className="mx-auto h-8 w-8 text-warning" />
      <h2 className="mt-3 text-base font-semibold">This page couldn't load</h2>
      <p className="mt-1 text-sm text-fg-muted">The app may have been updated. Reload to get the latest version.</p>
      <button
        type="button"
        className={`${buttonClass('primary', 'sm')} mt-4`}
        onClick={() => {
          window.location.reload();
        }}
      >
        Reload
      </button>
    </div>
  );
}
