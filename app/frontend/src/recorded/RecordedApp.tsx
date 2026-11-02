import { CloudOff } from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { parseMe, type Me } from '../api/me';
import { RECORDED_DEMO_BACKEND } from '../config/backends';
import { SessionContext, type Session } from '../session/SessionContext';
import { AppShell } from '../shell/AppShell';
import { BASE_PATH } from '../shell/basePath';
import { ThemeProvider } from '../theme/ThemeProvider';
import { buttonClass } from '../ui/classes';
import { CenteredMessage } from '../ui/CenteredMessage';
import { ErrorBoundary } from '../ui/ErrorBoundary';
import { Spinner } from '../ui/Spinner';
import { loadFixture, loadManifest } from './manifest';
import { showRecordedNotice } from './notice';

type Phase = { kind: 'loading' } | { kind: 'ready'; me: Me } | { kind: 'error'; message: string };

const NO_SIGN_IN =
  'This recorded demo has no sign-in: it replays responses recorded from the real backend, and nothing you change is saved.';

/**
 * Stands in for sign-in and SessionProvider: loads the recorded manifest and the recorded /me (the fake
 * demo user), then renders the normal app shell. The API client answers from the recording.
 */
function RecordedSession({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    loadManifest()
      .then((manifest) => {
        const meRoute = manifest.routes.get('GET /me');
        if (!meRoute) throw new Error("The recording has no account (GET /me) to show.");
        return loadFixture(meRoute.file);
      })
      .then(
        (data) => {
          if (!cancelled) setPhase({ kind: 'ready', me: parseMe(data) });
        },
        (error: unknown) => {
          if (!cancelled) setPhase({ kind: 'error', message: error instanceof Error ? error.message : 'Unknown error.' });
        },
      );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const session = useMemo<Session | null>(
    () =>
      phase.kind === 'ready'
        ? {
            me: phase.me,
            backend: RECORDED_DEMO_BACKEND,
            refreshMe: () => Promise.resolve(),
            signOut: () => {
              showRecordedNotice(NO_SIGN_IN);
              return Promise.resolve();
            },
          }
        : null,
    [phase],
  );

  if (session) return <SessionContext value={session}>{children}</SessionContext>;
  if (phase.kind === 'error') {
    return (
      <CenteredMessage
        title="The recorded demo couldn't load"
        icon={<CloudOff aria-hidden="true" className="h-8 w-8" />}
        actions={
          <button
            type="button"
            className={buttonClass('primary')}
            onClick={() => {
              setPhase({ kind: 'loading' });
              setAttempt((value) => value + 1);
            }}
          >
            Try again
          </button>
        }
      >
        <p>{phase.message}</p>
      </CenteredMessage>
    );
  }
  return <CenteredMessage title="Loading the recorded demo…" icon={<Spinner className="h-8 w-8" />} busy />;
}

const router = createBrowserRouter(
  [
    {
      path: '*',
      element: (
        <RecordedSession>
          <AppShell />
        </RecordedSession>
      ),
    },
  ],
  { basename: BASE_PATH || '/' },
);

/** The recorded demo's root (src/recorded/main.tsx): the normal shell, no sign-in, no backend. */
export function RecordedApp() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <RouterProvider router={router} />
      </ThemeProvider>
    </ErrorBoundary>
  );
}
