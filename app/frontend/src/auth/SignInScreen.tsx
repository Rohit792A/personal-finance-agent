import { Eye, EyeOff, Info } from 'lucide-react';
import { useId, useState, type SubmitEvent } from 'react';
import { buttonClass, inputClass } from '../ui/classes';
import { Logo } from '../ui/Logo';
import { Spinner } from '../ui/Spinner';
import { ThemeToggle } from '../ui/ThemeToggle';
import { useAuth } from './AuthContext';
import { describeAuthError } from './authErrors';

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" className="h-5 w-5">
      <path
        fill="#FFC107"
        d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34.1 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"
      />
      <path
        fill="#FF3D00"
        d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34.1 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"
      />
      <path
        fill="#4CAF50"
        d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"
      />
      <path
        fill="#1976D2"
        d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"
      />
    </svg>
  );
}

type Pending = 'google' | 'password' | null;

export function SignInScreen() {
  const { signInWithGoogle, signInWithPassword, notice } = useAuth();
  const [pending, setPending] = useState<Pending>(null);
  const [googleError, setGoogleError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const ids = useId();

  async function handleGoogle() {
    setPending('google');
    setGoogleError(null);
    setPasswordError(null);
    try {
      await signInWithGoogle();
    } catch (error) {
      setGoogleError(describeAuthError(error));
    } finally {
      setPending(null);
    }
  }

  async function handlePassword(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setGoogleError(null);
    if (!email.trim()) {
      setPasswordError('Enter your email address.');
      return;
    }
    if (!password) {
      setPasswordError('Enter your password.');
      return;
    }
    setPending('password');
    setPasswordError(null);
    try {
      await signInWithPassword(email, password);
    } catch (error) {
      setPasswordError(describeAuthError(error));
    } finally {
      setPending(null);
    }
  }

  const busy = pending !== null;
  const emailId = `${ids}-email`;
  const passwordId = `${ids}-password`;
  const passwordErrorId = `${ids}-password-error`;

  return (
    <div className="relative flex min-h-dvh items-center justify-center bg-canvas px-4 py-10 text-fg">
      <ThemeToggle className="absolute right-4 top-4" />
      <main className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <Logo />
          <span className="text-lg font-semibold">Personal Finance AI</span>
        </div>

        <div className="rounded-xl border border-line bg-surface p-6 shadow-sm">
          <h1 className="text-xl font-semibold">Sign in</h1>
          <p className="mt-1 text-sm text-fg-muted">Your transactions, budgets and AI insights in one place.</p>

          {notice ? (
            <div
              role="status"
              className="mt-4 flex items-start gap-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-sm text-warning"
              data-testid="sign-in-notice"
            >
              <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{notice}</span>
            </div>
          ) : null}

          <button
            type="button"
            onClick={() => void handleGoogle()}
            disabled={busy}
            className={`${buttonClass('secondary')} mt-5 w-full`}
          >
            {pending === 'google' ? <Spinner /> : <GoogleMark />}
            <span>{pending === 'google' ? 'Waiting for Google…' : 'Continue with Google'}</span>
          </button>
          {googleError ? (
            <p role="alert" className="mt-2 text-sm text-danger">
              {googleError}
            </p>
          ) : null}

          <div className="my-6 flex items-center gap-3 text-xs text-fg-muted" aria-hidden="true">
            <span className="h-px flex-1 bg-line" />
            or
            <span className="h-px flex-1 bg-line" />
          </div>

          <form onSubmit={(event) => void handlePassword(event)} noValidate aria-labelledby={`${ids}-demo-title`}>
            <h2 id={`${ids}-demo-title`} className="text-sm font-semibold">
              Demo account
            </h2>
            <p className="mt-1 text-xs text-fg-muted">
              Use the demo email and password you were given. The demo uses sample data.
            </p>

            <label htmlFor={emailId} className="mt-4 block text-sm font-medium">
              Email
            </label>
            <input
              id={emailId}
              type="email"
              autoComplete="username"
              inputMode="email"
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
              }}
              disabled={busy}
              aria-invalid={passwordError !== null}
              aria-describedby={passwordError ? passwordErrorId : undefined}
              className={`${inputClass} mt-1 h-10`}
            />

            <label htmlFor={passwordId} className="mt-3 block text-sm font-medium">
              Password
            </label>
            <div className="relative mt-1">
              <input
                id={passwordId}
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value);
                }}
                disabled={busy}
                aria-invalid={passwordError !== null}
                aria-describedby={passwordError ? passwordErrorId : undefined}
                className={`${inputClass} h-10 pr-10`}
              />
              <button
                type="button"
                onClick={() => {
                  setShowPassword((value) => !value);
                }}
                className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-lg text-fg-muted hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                aria-pressed={showPassword}
              >
                {showPassword ? (
                  <EyeOff aria-hidden="true" className="h-4 w-4" />
                ) : (
                  <Eye aria-hidden="true" className="h-4 w-4" />
                )}
              </button>
            </div>

            {passwordError ? (
              <p id={passwordErrorId} role="alert" className="mt-2 text-sm text-danger">
                {passwordError}
              </p>
            ) : null}

            <button type="submit" disabled={busy} className={`${buttonClass('primary')} mt-4 w-full`}>
              {pending === 'password' ? <Spinner /> : null}
              <span>{pending === 'password' ? 'Signing in…' : 'Sign in to the demo'}</span>
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-xs text-fg-muted">Google sign-in is for the account owner.</p>
      </main>
    </div>
  );
}
