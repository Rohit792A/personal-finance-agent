import type { User } from 'firebase/auth';
import { CloudOff, ShieldAlert } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { setApiSession } from '../api/client';
import { isApiError } from '../api/errors';
import { getMe, type Me } from '../api/me';
import { useAuth } from '../auth/AuthContext';
import { resolveBackend, type BackendTarget } from '../config/backends';
import { buttonClass } from '../ui/classes';
import { CenteredMessage } from '../ui/CenteredMessage';
import { Spinner } from '../ui/Spinner';
import { SessionContext, type Session } from './SessionContext';

type Phase =
  | { kind: 'connecting' }
  | { kind: 'ready'; me: Me; backend: BackendTarget }
  | { kind: 'no-access' }
  | { kind: 'unsupported' }
  | { kind: 'error'; message: string };

/** Minimum gap between access re-checks triggered by 403 responses. */
const RECHECK_INTERVAL_MS = 10_000;

/**
 * After sign-in: picks the backend for this account, binds the API client to it and loads /me.
 * Children (the app shell and the legacy screens) render only once /me succeeded.
 * Mount with key={user.uid} so a different account always starts from a clean state.
 */
export function SessionProvider({ user, children }: { user: User; children: ReactNode }) {
  const { signOut } = useAuth();
  const [phase, setPhase] = useState<Phase>({ kind: 'connecting' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const isStale = () => cancelled;
    let lastRecheck = 0;

    const recheckAccess = () => {
      const now = Date.now();
      if (now - lastRecheck < RECHECK_INTERVAL_MS) return;
      lastRecheck = now;
      getMe()
        .then((me) => {
          if (!cancelled) setPhase((current) => (current.kind === 'ready' ? { ...current, me } : current));
        })
        .catch((error: unknown) => {
          if (!cancelled && isApiError(error) && error.status === 403) setPhase({ kind: 'no-access' });
        });
    };

    async function connect() {
      const tokenResult = await user.getIdTokenResult();
      if (isStale()) return;
      const provider = tokenResult.signInProvider ?? tokenResult.claims.firebase?.sign_in_provider ?? null;
      const backend = resolveBackend(provider);
      if (!backend) {
        setPhase({ kind: 'unsupported' });
        return;
      }
      setApiSession({
        baseUrl: backend.baseUrl,
        getIdToken: (forceRefresh) => user.getIdToken(forceRefresh),
        onSessionExpired: (message) => {
          void signOut(message);
        },
        onForbidden: recheckAccess,
      });
      try {
        const me = await getMe();
        if (!isStale()) setPhase({ kind: 'ready', me, backend });
      } catch (error) {
        if (isStale()) return;
        if (isApiError(error) && error.status === 403) setPhase({ kind: 'no-access' });
        else if (isApiError(error) && error.status === 401) return; // the client is already signing out
        else setPhase({ kind: 'error', message: error instanceof Error ? error.message : 'Unknown error.' });
      }
    }

    connect().catch((error: unknown) => {
      if (!isStale()) setPhase({ kind: 'error', message: error instanceof Error ? error.message : 'Unknown error.' });
    });

    return () => {
      cancelled = true;
      setApiSession(null);
    };
  }, [user, signOut, attempt]);

  const refreshMe = useCallback(async () => {
    try {
      const me = await getMe();
      setPhase((current) => (current.kind === 'ready' ? { ...current, me } : current));
    } catch (error) {
      if (isApiError(error) && error.status === 403) setPhase({ kind: 'no-access' });
    }
  }, []);

  const handleSignOut = useCallback(() => signOut(), [signOut]);

  const session = useMemo<Session | null>(
    () => (phase.kind === 'ready' ? { me: phase.me, backend: phase.backend, refreshMe, signOut: handleSignOut } : null),
    [phase, refreshMe, handleSignOut],
  );

  const signOutButton = (
    <button type="button" className={buttonClass('primary')} onClick={() => void handleSignOut()}>
      Sign out
    </button>
  );

  if (session) return <SessionContext value={session}>{children}</SessionContext>;

  switch (phase.kind) {
    case 'no-access':
      return (
        <CenteredMessage
          title="This account doesn't have access"
          icon={<ShieldAlert aria-hidden="true" className="h-8 w-8" />}
          actions={signOutButton}
        >
          <p>
            You're signed in as <strong className="text-fg">{user.email ?? 'an unknown account'}</strong>, but this
            account isn't allowed into Personal Finance AI.
          </p>
          <p>Sign out, then use the owner's Google account or the demo email and password.</p>
        </CenteredMessage>
      );
    case 'unsupported':
      return (
        <CenteredMessage
          title="This sign-in method isn't supported"
          icon={<ShieldAlert aria-hidden="true" className="h-8 w-8" />}
          actions={signOutButton}
        >
          <p>Sign out, then continue with Google or use the demo email and password.</p>
        </CenteredMessage>
      );
    case 'error':
      return (
        <CenteredMessage
          title="Couldn't connect to your workspace"
          icon={<CloudOff aria-hidden="true" className="h-8 w-8" />}
          actions={
            <>
              <button
                type="button"
                className={buttonClass('primary')}
                onClick={() => {
                  setPhase({ kind: 'connecting' });
                  setAttempt((value) => value + 1);
                }}
              >
                Try again
              </button>
              <button type="button" className={buttonClass('secondary')} onClick={() => void handleSignOut()}>
                Sign out
              </button>
            </>
          }
        >
          <p>{phase.message}</p>
        </CenteredMessage>
      );
    default:
      return (
        <CenteredMessage title="Connecting to your workspace…" icon={<Spinner className="h-8 w-8" />} busy>
          <p>Checking your account.</p>
        </CenteredMessage>
      );
  }
}
