/**
 * Which backend an account talks to.
 *
 * Both Cloud Run services run the same code with different data and settings:
 *   - Google sign-in (the owner)       -> personal backend (real data, Gmail sync)
 *   - email/password (the demo login)  -> demo backend (sample data, reset nightly)
 * The rule uses the token's `firebase.sign_in_provider` claim, so no email address is in the bundle.
 * Each backend also enforces its own allowlist, so a wrong routing can never expose data.
 */
import type { Environment } from '../api/me';

export const BACKEND_URLS: Record<Environment, string> = {
  personal: 'https://finance-backend-328452160072.asia-northeast1.run.app',
  demo: 'https://finance-backend-demo-328452160072.asia-northeast3.run.app',
};

export interface BackendTarget {
  /** Origin without a trailing slash; '' = same origin (Vite proxy). */
  baseUrl: string;
  /** Environment the routing rule expects; null when a dev override is active. */
  expected: Environment | null;
}

export function environmentForProvider(signInProvider: string | null): Environment | null {
  switch (signInProvider) {
    case 'google.com':
      return 'personal';
    case 'password':
      return 'demo';
    default:
      return null;
  }
}

/** Dev-server override from VITE_API_BASE_URL (undefined when unset or in production builds). */
export function devBackendOverride(): string | undefined {
  if (!import.meta.env.DEV) return undefined;
  const raw = import.meta.env.VITE_API_BASE_URL;
  return raw === undefined ? undefined : raw.trim().replace(/\/+$/, '');
}

/** Returns null when the sign-in method has no backend (e.g. anonymous). */
export function resolveBackend(signInProvider: string | null): BackendTarget | null {
  const override = devBackendOverride();
  if (override !== undefined) return { baseUrl: override, expected: null };
  const environment = environmentForProvider(signInProvider);
  return environment ? { baseUrl: BACKEND_URLS[environment], expected: environment } : null;
}
