import type { User } from 'firebase/auth';
import { createContext, useContext } from 'react';

export type AuthStatus = 'loading' | 'signed-out' | 'signed-in';

export interface AuthContextValue {
  status: AuthStatus;
  user: User | null;
  /** One-off message for the sign-in screen, e.g. after the session expired. */
  notice: string | null;
  /** Rejects with the Firebase error; see describeAuthError. */
  signInWithGoogle: () => Promise<void>;
  signInWithPassword: (email: string, password: string) => Promise<void>;
  signOut: (notice?: string) => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>.');
  return value;
}
