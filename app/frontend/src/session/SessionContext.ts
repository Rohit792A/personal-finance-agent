import { createContext, useContext } from 'react';
import type { Me } from '../api/me';
import type { BackendTarget } from '../config/backends';

export interface Session {
  me: Me;
  backend: BackendTarget;
  /** Re-reads /me (e.g. to update the demo's remaining AI quota). */
  refreshMe: () => Promise<void>;
  signOut: () => Promise<void>;
}

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession must be used inside a ready <SessionProvider>.');
  return value;
}
