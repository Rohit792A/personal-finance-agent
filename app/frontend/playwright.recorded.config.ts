import { defineConfig, devices } from '@playwright/test';

/**
 * The recorded demo (README, "Try the recorded demo"), served the way GitHub Pages serves it: the
 * production build from `npm run build:recorded`, under /personal-finance-agent/. No sign-in and no
 * backend; the spec also checks that every request the page makes stays on its own origin.
 *
 *   npm run build:recorded && npm run test:e2e:recorded
 */
const port = 4173;
const basePath = '/personal-finance-agent/';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: /.*\.recorded\.spec\.ts$/,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: 'test-results',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: `http://localhost:${String(port)}${basePath}`,
    viewport: { width: 1440, height: 900 },
    trace: 'off',
  },
  webServer: {
    command: `npx vite preview --mode recorded --port ${String(port)} --strictPort`,
    url: `http://localhost:${String(port)}${basePath}`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
