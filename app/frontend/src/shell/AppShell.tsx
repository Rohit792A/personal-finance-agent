import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router';
import { LegacyBridge } from '../legacy/bridge';
import { LegacyScreens } from '../legacy/LegacyScreens';
import { RECORDED_DEMO } from '../recorded/mode';
import { RecordedBanner } from '../recorded/RecordedBanner';
import { useSession } from '../session/SessionContext';
import { AgentRegistryScreen } from './AgentRegistryScreen';
import { routerPathname } from './basePath';
import { DemoBanner } from './DemoBanner';
import { FeatureUnavailable } from './FeatureUnavailable';
import { MasterCopilotDock } from './MasterCopilotDock';
import { NotFound } from './NotFound';
import { HOME_PATH, screenForPath, type ScreenId } from './screens';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { parseUrlState, serializeUrlState } from './urlState';
import { useMonths } from './useMonths';

/** The signed-in application: sidebar, top bar, demo (or recorded demo) banner and the screens. */
export function AppShell() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const { me } = useSession();
  const screen = screenForPath(pathname);
  const urlState = useMemo(() => parseUrlState(search), [search]);

  // One bridge per session; the data router's navigate function is stable.
  const [bridge] = useState(
    () =>
      new LegacyBridge(
        {
          // The router's path, without the recorded demo's base path on GitHub Pages.
          location: () => ({ pathname: routerPathname(window.location.pathname), search: window.location.search }),
          navigate: (to, options) => {
            void navigate(to, options);
          },
        },
        urlState,
      ),
  );
  const dataVersion = useSyncExternalStore(bridge.subscribe, bridge.getDataVersion);
  const months = useMonths(dataVersion);

  const [navOpen, setNavOpen] = useState(false);
  const [jarvisOpen, setJarvisOpen] = useState(false);
  const [lastPathname, setLastPathname] = useState(pathname);
  if (pathname !== lastPathname) {
    setLastPathname(pathname);
    setNavOpen(false);
  }

  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const focusedPathname = useRef(pathname);

  // Drop unknown or invalid parameters without adding a history entry.
  useEffect(() => {
    if (pathname === '/') return;
    const canonical = serializeUrlState(urlState);
    if (search !== canonical) void navigate({ pathname, search: canonical }, { replace: true });
  }, [pathname, search, urlState, navigate]);

  useEffect(() => {
    document.title = screen ? `${screen.label} · Personal Finance AI` : 'Page not found · Personal Finance AI';
  }, [screen]);

  // After moving to another screen, put focus on the page (keyboard and screen-reader users).
  useEffect(() => {
    if (focusedPathname.current === pathname) return;
    focusedPathname.current = pathname;
    mainRef.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [pathname]);

  // The drawer only exists below the lg breakpoint.
  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 1024px)');
    const onChange = () => {
      if (desktop.matches) setNavOpen(false);
    };
    desktop.addEventListener('change', onChange);
    return () => {
      desktop.removeEventListener('change', onChange);
    };
  }, []);

  const closeNav = useCallback(() => {
    setNavOpen(false);
    menuButtonRef.current?.focus();
  }, []);

  const changeMonth = useCallback(
    (month: string) => {
      void navigate({ pathname, search: serializeUrlState({ ...urlState, month }) });
    },
    [navigate, pathname, urlState],
  );

  const unavailable = useMemo(() => {
    const panels: Partial<Record<ScreenId, ReactNode>> = {
      agents: <AgentRegistryScreen key={dataVersion} />,
    };
    const common = { environment: me.environment, month: urlState.month, uploadsEnabled: me.features.uploads };
    if (!me.features.gmail_sync) panels['bank-sync'] = <FeatureUnavailable feature="gmail" {...common} />;
    if (!me.features.uploads) panels.upload = <FeatureUnavailable feature="uploads" {...common} />;
    if (!me.features.ai) panels['ai-cfo'] = <FeatureUnavailable feature="ai" {...common} />;
    return panels;
  }, [dataVersion, me.environment, me.features, urlState.month]);

  if (pathname === '/') return <Navigate to={{ pathname: HOME_PATH, search }} replace />;

  const title = screen?.label ?? 'Page not found';

  return (
    <div className="min-h-dvh bg-canvas text-fg">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-fg focus:shadow-lg focus:outline focus:outline-2 focus:outline-focus"
      >
        Skip to content
      </a>
      <Sidebar open={navOpen} month={urlState.month} onClose={closeNav} />
      <div className="flex min-h-dvh flex-col lg:pl-64" inert={navOpen}>
        <TopBar
          title={title}
          month={urlState.month}
          q={urlState.q}
          months={months}
          onMonthChange={changeMonth}
          onOpenNav={() => {
            setNavOpen(true);
          }}
          menuButtonRef={menuButtonRef}
          jarvisOpen={jarvisOpen}
          onToggleJarvis={() => {
            setJarvisOpen((prev) => !prev);
          }}
        />
        {RECORDED_DEMO ? <RecordedBanner /> : me.environment === 'demo' ? <DemoBanner me={me} /> : null}
        <main
          id="main-content"
          ref={mainRef}
          tabIndex={-1}
          className="flex-1 px-4 py-6 focus:outline-none sm:px-6 lg:px-8"
          data-testid="main-content"
        >
          <h1 className="sr-only">{title}</h1>
          <div className="mx-auto w-full max-w-[1600px]">
            {screen ? null : <NotFound />}
            <LegacyScreens bridge={bridge} activeScreen={screen?.id ?? null} unavailable={unavailable} />
          </div>
        </main>
        <MasterCopilotDock
          open={jarvisOpen}
          onToggleOpen={setJarvisOpen}
          screenId={screen?.id ?? null}
          screenLabel={title}
          month={urlState.month}
          searchQuery={urlState.q}
          onLedgerMutated={() => {
            bridge.refreshAll();
          }}
        />
      </div>
    </div>
  );
}
