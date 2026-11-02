/**
 * Recorded demo: a build of this app that needs no backend, for GitHub Pages.
 *
 * `npm run build:recorded` (or VITE_RECORDED_DEMO=1 at build time) starts from src/recorded/main.tsx:
 * there is no Firebase sign-in and no backend URL, and the API client answers every request in the
 * browser from the JSON fixtures that scripts/record_demo.py captured from the real backend
 * (recorded-demo/recorded/, served next to the app). Writes are acknowledged but not stored.
 *
 * RECORDED_DEMO is a build-time constant (vite.config.ts defines VITE_RECORDED_DEMO in every build),
 * so normal builds drop the recorded-demo code paths and modules entirely.
 */
export const RECORDED_DEMO: boolean = import.meta.env.VITE_RECORDED_DEMO === '1';

/** Only http(s) links are used; anything else is treated as unset. */
function linkOrEmpty(value: string | undefined): string {
  const url = (value ?? '').trim();
  return /^https?:\/\//i.test(url) ? url : '';
}

/** Recorded demo banner: the source code (VITE_REPO_URL) and the demo video (VITE_VIDEO_URL). */
export const REPO_URL = linkOrEmpty(import.meta.env.VITE_REPO_URL);
export const VIDEO_URL = linkOrEmpty(import.meta.env.VITE_VIDEO_URL);

/** Normal builds: the recorded demo's address (VITE_RECORDED_DEMO_URL), offered when the backend is unreachable. */
export const RECORDED_DEMO_URL = linkOrEmpty(import.meta.env.VITE_RECORDED_DEMO_URL);
