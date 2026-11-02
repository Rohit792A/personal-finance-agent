/** Maps Firebase Auth error codes to short, actionable messages. */

const MESSAGES: Record<string, string | null> = {
  // Another popup request replaced this one: nothing to tell the user.
  'auth/cancelled-popup-request': null,
  'auth/popup-closed-by-user': 'The Google sign-in window was closed before sign-in finished. Try again.',
  'auth/user-cancelled': 'Sign-in was cancelled. Try again.',
  'auth/popup-blocked': 'Your browser blocked the Google sign-in window. Allow pop-ups for this site and try again.',
  'auth/network-request-failed': "Can't reach the sign-in service. Check your internet connection and try again.",
  'auth/too-many-requests': 'Too many sign-in attempts. Wait a few minutes, then try again.',
  'auth/invalid-credential': 'Wrong email or password.',
  'auth/invalid-login-credentials': 'Wrong email or password.',
  'auth/wrong-password': 'Wrong email or password.',
  'auth/user-not-found': 'Wrong email or password.',
  'auth/invalid-email': 'Enter a valid email address.',
  'auth/missing-email': 'Enter your email address.',
  'auth/missing-password': 'Enter your password.',
  'auth/user-disabled': 'This account has been disabled.',
  'auth/operation-not-allowed': 'This sign-in method is turned off for this app.',
  'auth/unauthorized-domain':
    "Sign-in isn't enabled for this web address. Add it under Firebase Authentication > Settings > Authorized domains.",
  'auth/account-exists-with-different-credential': 'This email already uses a different sign-in method.',
  'auth/web-storage-unsupported': 'Your browser is blocking storage that sign-in needs. Allow site data and try again.',
};

const FALLBACK = 'Sign-in failed. Try again.';

/** Returns the message to show, or null when the error needs no message. */
export function describeAuthError(error: unknown): string | null {
  const code =
    typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string' ? error.code : '';
  if (Object.hasOwn(MESSAGES, code)) return MESSAGES[code] ?? null;
  return FALLBACK;
}
