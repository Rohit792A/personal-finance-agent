/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Development server only (ignored in production builds): send every account's requests to this
   * backend instead of routing by sign-in method. An empty value means same-origin, i.e. through the
   * Vite proxy (Cloud Shell web preview). Set it in .env.development.local.
   */
  readonly VITE_API_BASE_URL?: string;
  /**
   * '1' in recorded demo builds (`npm run build:recorded`), '' otherwise: vite.config.ts always
   * defines it. See src/recorded/mode.ts.
   */
  readonly VITE_RECORDED_DEMO?: string;
  /** Recorded demo: link to the source code in the banner. */
  readonly VITE_REPO_URL?: string;
  /** Recorded demo: link to the demo video in the banner. */
  readonly VITE_VIDEO_URL?: string;
  /** Normal builds: the recorded demo's address, offered when the backend can't be reached. */
  readonly VITE_RECORDED_DEMO_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
