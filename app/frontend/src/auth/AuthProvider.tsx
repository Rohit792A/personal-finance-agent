import {
  GoogleAuthProvider,
  onAuthStateChanged,
  signOut as firebaseSignOut,
  signInWithEmailAndPassword,
  signInWithPopup,
  type User,
} from 'firebase/auth';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { auth } from '../config/firebase';
import { AuthContext, type AuthContextValue, type AuthStatus } from './AuthContext';

interface AuthState {
  status: AuthStatus;
  user: User | null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading', user: null });
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(
    () =>
      onAuthStateChanged(auth, (user) => {
        setState(user ? { status: 'signed-in', user } : { status: 'signed-out', user: null });
      }),
    [],
  );

  const signInWithGoogle = useCallback(async () => {
    setNotice(null);
    const provider = new GoogleAuthProvider();
    provider.setCustomParameters({ prompt: 'select_account' });
    await signInWithPopup(auth, provider);
  }, []);

  const signInWithPassword = useCallback(async (email: string, password: string) => {
    setNotice(null);
    await signInWithEmailAndPassword(auth, email.trim(), password);
  }, []);

  const signOut = useCallback(async (message?: string) => {
    setNotice(message ?? null);
    await firebaseSignOut(auth);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ ...state, notice, signInWithGoogle, signInWithPassword, signOut }),
    [state, notice, signInWithGoogle, signInWithPassword, signOut],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
