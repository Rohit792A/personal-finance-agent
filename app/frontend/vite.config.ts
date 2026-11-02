import type { IncomingMessage } from 'node:http';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv, type ProxyOptions } from 'vite';

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

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const backendUrl = env.BACKEND_URL ?? `http://127.0.0.1:${env.BACKEND_PORT ?? '8080'}`;

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

  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      open: false,
      allowedHosts,
      proxy,
    },
    preview: {
      host: '0.0.0.0',
      port: 4173,
      strictPort: true,
      allowedHosts,
      proxy,
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
