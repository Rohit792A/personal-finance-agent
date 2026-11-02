/**
 * Firebase project used for sign-in.
 *
 * This web config is public by design: it only identifies the Firebase project. Access is enforced
 * by Firebase Auth and by the backends, which verify the ID token and an allowlist on every route.
 * authDomain stays on firebaseapp.com and sign-in uses popups (not redirects): redirect sign-in
 * breaks in browsers that partition third-party storage when authDomain differs from the app's host.
 */
import { initializeApp } from 'firebase/app';
import { getAuth } from 'firebase/auth';

const firebaseConfig = {
  apiKey: 'AIzaSyBHewGkwTtWd1sngxuiGKvnrSHlITkrBQ0',
  authDomain: 'personal-finance-agent-507010.firebaseapp.com',
  projectId: 'personal-finance-agent-507010',
  storageBucket: 'personal-finance-agent-507010.firebasestorage.app',
  messagingSenderId: '328452160072',
  appId: '1:328452160072:web:4c5aa2110571d2c0f0767d',
} as const;

export const firebaseApp = initializeApp(firebaseConfig);
export const auth = getAuth(firebaseApp);
