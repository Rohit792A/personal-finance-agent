import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { AuthProvider } from './auth/AuthProvider';
import { useAuth } from './auth/AuthContext';
import { SignInScreen } from './auth/SignInScreen';
import { SessionProvider } from './session/SessionProvider';
import { AppShell } from './shell/AppShell';
import { ThemeProvider } from './theme/ThemeProvider';
import { CenteredMessage } from './ui/CenteredMessage';
import { ErrorBoundary } from './ui/ErrorBoundary';
import { Spinner } from './ui/Spinner';

/**
 * Signed-out visitors only ever see the sign-in screen (whatever the URL; it is kept, so a deep
 * link opens after sign-in). Signed-in users get the shell once their backend's /me succeeded.
 */
function AuthGate() {
  const { status, user } = useAuth();
  if (status === 'loading') {
    return <CenteredMessage title="Loading…" icon={<Spinner className="h-8 w-8" />} busy />;
  }
  if (status === 'signed-out' || !user) return <SignInScreen />;
  return (
    <SessionProvider key={user.uid} user={user}>
      <AppShell />
    </SessionProvider>
  );
}

const router = createBrowserRouter([
  {
    path: '*',
    element: (
      <AuthProvider>
        <AuthGate />
      </AuthProvider>
    ),
  },
]);

export function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <RouterProvider router={router} />
      </ThemeProvider>
    </ErrorBoundary>
  );
}
