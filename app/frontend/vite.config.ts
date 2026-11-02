import type { IncomingMessage } from 'node:http';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from 'vite';

/**
 * Every FastAPI route prefix. Only used in same-origin mode (VITE_API_BASE_URL set to an empty value
 * in .env.development.local, e.g. for Cloud Shell web preview): the browser calls the Vite server and
 * Vite forwards these paths to BACKEND_URL. Keys starting with ^ are regular expressions.
 */
const BACKEND_ROUTES =
  '^/(?:me|health|upload|transactions|analytics|recurring|forecast|budgets|insights|api|eval|mcp|a2a|agents|telemetry|internal|docs|redoc|openapi\\.json)(?:[/?]|$)';

/**
 * Some app pages share a path with an API route (/transactions, /analytics, /budgets, /forecast,
 * /upload). A browser navigation (GET asking for HTML) to one of them must get the app, not the API,
 * so deep links and reloads work. API calls ask for JSON or binary data and are proxied as usual.
 */
function serveAppForPageLoads(req: IncomingMessage): string | undefined {
  const path = (req.url ?? '').split('?')[0] ?? '';
  const wantsHtml = (req.headers.accept ?? '').includes('text/html');
  const isApiDocs = /^\/(?:docs|redoc)(?:\/|$)/.test(path);
  return req.method === 'GET' && wantsHtml && !isApiDocs ? '/index.html' : undefined;
}

/** Where GitHub Pages serves the recorded demo unless PAGES_BASE_PATH says otherwise: /<repository>/. */
const DEFAULT_PAGES_BASE_PATH = '/personal-finance-agent/';

/** '/personal-finance-agent/' from 'personal-finance-agent' or '/personal-finance-agent'; '' -> '/'. */
function basePath(raw: string): string {
  const trimmed = raw.trim().replace(/^\/+|\/+$/g, '');
  return trimmed ? `/${trimmed}/` : '/';
}

/**
 * Recorded demo builds start from src/recorded/main.tsx instead of src/main.tsx, so Firebase sign-in
 * and the backend URLs are never part of their bundle.
 */
function recordedDemoEntry(): Plugin {
  return {
    name: 'pfa-recorded-demo-entry',
    transformIndexHtml: {
      order: 'pre',
      handler(html) {
        if (!html.includes('/src/main.tsx')) {
          throw new Error('index.html no longer loads /src/main.tsx; update recordedDemoEntry() in vite.config.ts.');
        }
        return html.replace('/src/main.tsx', '/src/recorded/main.tsx');
      },
    },
  };
}

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const backendUrl = env.BACKEND_URL ?? `http://127.0.0.1:${env.BACKEND_PORT ?? '8080'}`;
  // Recorded demo (src/recorded/mode.ts): `--mode recorded` (npm run build:recorded) or VITE_RECORDED_DEMO=1.
  const recordedDemo = mode === 'recorded' || env.VITE_RECORDED_DEMO === '1';

  if (command === 'build' && env.VITE_API_BASE_URL !== undefined) {
    console.warn(
      '[vite] VITE_API_BASE_URL is set but ignored in production builds: the signed-in account picks the ' +
        'backend. Keep the override in .env.development.local.',
    );
  }

  const proxy: Record<string, ProxyOptions> = {
    [BACKEND_ROUTES]: {
      target: backendUrl,
      changeOrigin: true,
      // Uploads and AI calls can take minutes.
      timeout: 300_000,
      proxyTimeout: 300_000,
      bypass: serveAppForPageLoads,
    },
  };

  // Cloud Shell web preview serves the dev server from *.cloudshell.dev.
  const allowedHosts = ['localhost', '127.0.0.1', '.cloudshell.dev'];

  // The recorded demo calls no backend, so it gets no proxy.
  const backendProxy: Record<string, ProxyOptions> = recordedDemo ? {} : proxy;

  return {
    // GitHub Pages serves the recorded demo from /<repository>/; every other build is served from the root.
    base: recordedDemo ? basePath(env.PAGES_BASE_PATH ?? DEFAULT_PAGES_BASE_PATH) : '/',
    // The recorded fixtures (recorded-demo/recorded/*.json) are copied into recorded demo builds only.
    publicDir: recordedDemo ? 'recorded-demo' : 'public',
    // Always defined, so `import.meta.env.VITE_RECORDED_DEMO === '1'` is a constant and normal builds
    // leave the recorded demo's code out.
    define: { 'import.meta.env.VITE_RECORDED_DEMO': JSON.stringify(recordedDemo ? '1' : '') },
    plugins: recordedDemo ? [react(), recordedDemoEntry()] : [react()],
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      open: false,
      allowedHosts,
      proxy: backendProxy,
    },
    preview: {
      host: '0.0.0.0',
      port: 4173,
      strictPort: true,
      allowedHosts,
      proxy: backendProxy,
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      target: 'es2022',
      rollupOptions: {
        output: {
          manualChunks(id: string) {
            if (id.includes('/node_modules/firebase/') || id.includes('/node_modules/@firebase/')) return 'firebase';
            if (/\/node_modules\/(?:react|react-dom|react-router|scheduler)\//.test(id)) return 'react';
            return undefined;
          },
        },
      },
    },
  };
});
