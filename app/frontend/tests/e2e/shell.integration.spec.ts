/**
 * Integration pass against a real backend (E2E_BACKEND, e.g. the local demo backend on :8081).
 * Real Firebase sign-in, no mocks. Also produces the contrast report (light and dark, every page):
 * test-results/contrast-report.json.
 */
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import {
  SCREENS,
  scanContrast,
  setTheme,
  signInAsDemo,
  trackConsoleErrors,
  waitForScreen,
  type ContrastResult,
} from './helpers';

const BACKEND = process.env.E2E_BACKEND ?? '';

test.describe.configure({ mode: 'serial' });

async function openScreen(page: Page, label: string, path: string) {
  await page.getByRole('navigation', { name: 'Main' }).first().getByRole('link', { name: label, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${path.replace(/[/]/g, '\\/')}(\\?|$)`));
}

test('signed-out visitors only see the sign-in screen', async ({ page }) => {
  const backendCalls: string[] = [];
  page.on('request', (request) => {
    if (BACKEND && request.url().startsWith(BACKEND)) backendCalls.push(request.url());
  });
  await page.goto('/analytics?month=2026-09');
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0);
  await expect(page.locator('.legacy-scope section, section.legacy-scope')).toHaveCount(0);
  expect(backendCalls).toEqual([]);
});

test('demo sign-in, every page renders against the real backend, contrast scan', async ({ page }) => {
  // 14 page visits with screenshots and two axe scans each; the full sample ledger alone takes ~30 s.
  test.setTimeout(360_000);
  const errors = trackConsoleErrors(page);
  const gmailCalls: string[] = [];
  const unauthenticated: string[] = [];
  page.on('request', (request) => {
    const url = request.url();
    if (!url.startsWith(BACKEND)) return;
    if (url.includes('/api/gmail/')) gmailCalls.push(url);
    if (request.method() !== 'OPTIONS' && !(request.headers().authorization ?? '').startsWith('Bearer ')) {
      unauthenticated.push(url);
    }
  });

  await page.goto('/');
  await signInAsDemo(page);
  await expect(page).toHaveURL(/\/transactions(\?|$)/);
  await expect(page.getByTestId('demo-banner')).toContainText('Demo');
  await expect(page.getByTestId('demo-banner')).toContainText('sample data');

  const report: Record<string, { shell: ContrastResult; legacy: ContrastResult | null }> = {};
  mkdirSync('test-results/screens', { recursive: true });

  for (const theme of ['light', 'dark'] as const) {
    await setTheme(page, theme);
    for (const screen of SCREENS) {
      await openScreen(page, screen.label, screen.path);
      if (screen.id === 'bank-sync') {
        await expect(page.getByTestId('feature-unavailable-gmail')).toContainText('Gmail sync is turned off in the demo');
      }
      // Also lets the sidebar's colour transition finish, so the screenshot and the scan see final colours.
      await waitForScreen(page, screen.id);
      await page.screenshot({
        path: `test-results/screens/${theme}-${screen.id}.png`,
        fullPage: true,
        // Capped: the whole sample ledger is a 1440 × 70,000 px, 14 MB image. Shorter pages stay whole.
        clip: { x: 0, y: 0, width: 1440, height: 8000 },
      });
      const shell = await scanContrast(page, 'body', '.legacy-scope');
      const legacy =
        screen.id === 'bank-sync' || screen.id === 'agents'
          ? null
          : await scanContrast(page, `#legacy-screen-${screen.id}`);
      report[`${theme}:${screen.id}`] = { shell, legacy };
    }
  }

  writeFileSync('test-results/contrast-report.json', JSON.stringify(report, null, 2));
  const rows = Object.entries(report).map(
    ([key, value]) =>
      `${key.padEnd(22)} shell=${String(value.shell.violations)} (review ${String(value.shell.needsReview)})  legacy=${
        value.legacy ? `${String(value.legacy.violations)} (review ${String(value.legacy.needsReview)})` : 'n/a'
      }`,
  );
  console.log(`\nContrast (axe color-contrast, WCAG AA):\n${rows.join('\n')}`);

  expect(errors, 'console errors').toEqual([]);
  expect(gmailCalls, 'the demo never calls Gmail routes').toEqual([]);
  expect(unauthenticated, 'every backend request carries a Bearer token').toEqual([]);
  for (const [key, value] of Object.entries(report)) {
    expect(value.shell.details, `shell contrast ${key}`).toEqual([]);
  }
});

test('period picker, search, deep links and back/forward', async ({ page }) => {
  const errors = trackConsoleErrors(page);
  await page.goto('/transactions');
  await signInAsDemo(page);
  await waitForScreen(page, 'transactions');

  const picker = page.getByTestId('period-picker');
  const options = await picker.locator('option').allTextContents();
  expect(options[0]).toBe('All time');
  expect(options.length).toBeGreaterThan(2);
  const monthValue = await picker.locator('option').nth(1).getAttribute('value');
  const monthLabel = (options[1] ?? '').trim();
  if (!monthValue) throw new Error('no month option');

  await picker.selectOption(monthValue);
  await expect(page).toHaveURL(new RegExp(`month=${monthValue}`));
  await expect(page.locator('#ledger-title-heading')).toContainText(monthLabel);

  // Month is global: Analytics opens on the same month.
  await openScreen(page, 'Analytics', '/analytics');
  await expect(page).toHaveURL(new RegExp(`/analytics\\?month=${monthValue}`));
  await waitForScreen(page, 'analytics');
  await expect(page.locator('#analytics-month-select')).toHaveValue(monthValue);

  // Search from another screen lands on Transactions with the query applied in the ledger.
  await page.getByTestId('global-search').fill('Salary');
  await page.getByTestId('global-search').press('Enter');
  await expect(page).toHaveURL(new RegExp(`/transactions\\?month=${monthValue}&q=Salary`));
  await expect(page.locator('#ledger-search')).toHaveValue('Salary');

  // Back / forward restore the screen and the URL state.
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/analytics\\?month=${monthValue}$`));
  await expect(page.locator('#legacy-screen-analytics')).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/q=Salary/);
  await expect(page.locator('#legacy-screen-transactions')).toBeVisible();

  // Deep link straight to a month + All time.
  await page.goto(`/budgets?month=${monthValue}`);
  await expect(page.getByRole('navigation', { name: 'Main' }).first()).toBeVisible();
  await waitForScreen(page, 'budgets');
  await expect(page.locator('#budget-month-select')).toHaveValue(monthValue);
  await page.getByTestId('period-picker').selectOption('ALL');
  await expect(page).toHaveURL(/\/budgets$/);
  await expect(page.locator('#budget-month-select')).toHaveValue('ALL');

  expect(errors, 'console errors').toEqual([]);
});

test('theme toggle persists across reloads', async ({ page }) => {
  await page.goto('/transactions');
  await signInAsDemo(page);
  await setTheme(page, 'dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await setTheme(page, 'light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});
