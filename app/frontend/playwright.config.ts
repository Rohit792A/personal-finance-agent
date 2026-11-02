import { defineConfig, devices } from '@playwright/test';

/**
 * Two modes, both with REAL Firebase sign-in (demo account from
 * ~/.config/personal-finance-agent/demo-login.txt, read at runtime and never printed):
 *
 *   npm run test:e2e                         backend mocked with page.route (demo Cloud Run URL)
 *   E2E_BACKEND=http://127.0.0.1:8081 \
 *     npm run test:e2e -- integration        real local backend (dev-server override)
 *
 * Specs named *.integration.spec.ts need E2E_BACKEND; the others mock the backend. The recorded demo's
 * spec (*.recorded.spec.ts) has its own config: playwright.recorded.config.ts.
 */
const backend = process.env.E2E_BACKEND;
const port = 5173; // the backend's CORS allowlist contains http://localhost:5173

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: backend ? /.*\.integration\.spec\.ts/ : /^(?!.*\.(?:integration|recorded)\.spec\.ts$).*\.spec\.ts$/,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: 'test-results',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: `http://localhost:${String(port)}`,
    viewport: { width: 1440, height: 900 },
    // Off on purpose: traces record typed text, which would include the demo password.
    trace: 'off',
  },
  webServer: {
    command: 'npx vite --port 5173 --strictPort',
    url: `http://localhost:${String(port)}`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: backend ? { VITE_API_BASE_URL: backend } : {},
  },
});
