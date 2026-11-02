/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Development server only (ignored in production builds): send every account's requests to this
   * backend instead of routing by sign-in method. An empty value means same-origin, i.e. through the
   * Vite proxy (Cloud Shell web preview). Set it in .env.development.local.
   */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
