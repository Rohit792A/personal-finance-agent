/**
 * The recorded demo build (npm run build:recorded) as GitHub Pages serves it, with no sign-in and no
 * backend: every screen renders from the recorded fixtures, the AI CFO chat and Jarvis replay recorded
 * answers, writes are acknowledged but not saved, and every request stays on the page's own origin
 * (no backend, Firebase or Google calls).
 *
 *   npm run build:recorded && npm run test:e2e:recorded
 */
import { expect, test, type Page } from '@playwright/test';
import { SCREENS, trackConsoleErrors, waitForScreen } from './helpers';

test.describe.configure({ mode: 'serial' });

interface Manifest {
  recorded_on: string;
  chat: { question: string }[];
  copilot: { message: string }[];
}

/** Every request the page makes; data: and blob: URLs never leave the browser. */
function trackRequests(page: Page): URL[] {
  const urls: URL[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.protocol === 'http:' || url.protocol === 'https:') urls.push(url);
  });
  return urls;
}

function expectSameOrigin(page: Page, urls: URL[]): void {
  const origin = new URL(page.url()).origin;
  expect(urls.length).toBeGreaterThan(0);
  expect(urls.filter((url) => url.origin !== origin).map(String)).toEqual([]);
}

async function loadManifest(page: Page): Promise<Manifest> {
  const response = await page.request.get('recorded/manifest.json');
  expect(response.ok()).toBe(true);
  return (await response.json()) as Manifest;
}

const mainNav = (page: Page) => page.getByRole('navigation', { name: 'Main' }).first();

test('opens on the recorded ledger without sign-in, under the recorded demo banner', async ({ page }) => {
  const errors = trackConsoleErrors(page);
  const urls = trackRequests(page);
  await page.goto('./');
  await expect(page).toHaveURL(/\/personal-finance-agent\/transactions/);
  await expect(page.getByTestId('recorded-demo-banner')).toContainText(
    /Recorded demo · responses captured from the real backend on \d{1,2} [A-Z][a-z]+ \d{4}/,
  );
  await waitForScreen(page, 'transactions');
  await expect(page.getByTestId('demo-banner')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toHaveCount(0);

  await page.getByTestId('user-menu-button').click();
  await expect(page.getByTestId('user-menu')).toContainText('demo@example.com');
  await page.getByTestId('user-menu').getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByTestId('recorded-demo-notice')).toContainText('no sign-in');
  await waitForScreen(page, 'transactions');

  expectSameOrigin(page, urls);
  expect(errors).toEqual([]);
});

test('every screen renders from the recording', async ({ page }) => {
  const errors = trackConsoleErrors(page);
  const urls = trackRequests(page);
  await page.goto('transactions');
  for (const screen of SCREENS) {
    await mainNav(page).getByRole('link', { name: screen.label }).click();
    await expect(page).toHaveURL(new RegExp(`/personal-finance-agent${screen.path}`));
    if (screen.id === 'bank-sync') {
      await expect(page.getByTestId('feature-unavailable-gmail')).toContainText('Gmail sync is turned off in the demo');
    } else if (screen.id === 'agents') {
      await expect(page.getByTestId('agent-card-orchestrator')).toBeVisible();
    } else {
      await waitForScreen(page, screen.id);
    }
  }
  expectSameOrigin(page, urls);
  expect(errors).toEqual([]);
});

test('a deep link with a month opens directly and survives a reload', async ({ page }) => {
  const errors = trackConsoleErrors(page);
  await page.goto('budgets?month=2026-10');
  await waitForScreen(page, 'budgets');
  await page.reload();
  await waitForScreen(page, 'budgets');
  await expect(page).toHaveURL(/\/personal-finance-agent\/budgets\?month=2026-10/);
  expect(errors).toEqual([]);
});

test('the AI CFO replays recorded answers and explains other questions', async ({ page }) => {
  const errors = trackConsoleErrors(page);
  const urls = trackRequests(page);
  const manifest = await loadManifest(page);
  await page.goto('ai-cfo');
  await waitForScreen(page, 'ai-cfo');
  const screen = page.locator('#legacy-screen-ai-cfo');

  const chips = screen.locator('.advisor-quick-prompt');
  await expect(chips).toHaveText(manifest.chat.map((entry) => entry.question));
  await chips.first().click();
  const history = screen.locator('#advisor-chat-history');
  await expect(history.locator('.markdown-content').first()).not.toBeEmpty();
  await expect(history).not.toContainText('can only replay');

  await screen.locator('#advisor-chat-input').fill('What will the weather be tomorrow?');
  await screen.locator('#advisor-chat-submit-btn').click();
  await expect(history).toContainText('This recorded demo can only replay the questions it recorded.');

  await screen.locator('#fetch-insights-btn').click();
  await expect(screen.locator('#insights-result')).toBeVisible();
  await expect(screen.locator('#insights-result')).not.toBeEmpty();

  expectSameOrigin(page, urls);
  expect(errors).toEqual([]);
});

test('Jarvis offers and replays the recorded commands', async ({ page }) => {
  const errors = trackConsoleErrors(page);
  const manifest = await loadManifest(page);
  await page.goto('transactions');
  await waitForScreen(page, 'transactions');
  await page.getByTestId('jarvis-toggle-btn').click();
  const panel = page.getByTestId('jarvis-copilot-panel');
  const first = manifest.copilot[0]?.message ?? '';
  await panel.getByRole('button', { name: first, exact: true }).click();
  await expect(panel).toContainText(first);
  await expect(panel).not.toContainText('can only replay', { timeout: 10_000 });
  await page.getByTestId('jarvis-command-input').fill('Change #1 to Rent');
  await page.getByTestId('jarvis-send-btn').click();
  await expect(panel).toContainText('This recorded demo can only replay the commands it recorded.');
  expect(errors).toEqual([]);
});

test('Agent Registry actions replay their recordings; writes are not saved', async ({ page }) => {
  const errors = trackConsoleErrors(page);
  const urls = trackRequests(page);
  await page.goto('agents');
  await expect(page.getByTestId('agent-card-advisor-agent')).toBeVisible();

  await page.getByTestId('invoke-skill-advisor-agent-ASK_CFO').click();
  await expect(page.getByTestId('agent-card-advisor-agent').locator('pre')).toContainText('"answer"');

  await page.getByTestId('master-analyze-btn').click();
  await expect(page.getByTestId('master-resolution-notice')).toContainText('candidate transactions');

  await page.locator('#learned-rule-pattern').fill('DEMO BOOKSHOP');
  await page.locator('#learned-rule-pattern').press('Enter');
  await expect(page.getByTestId('recorded-demo-notice')).toContainText('nothing was saved');

  expectSameOrigin(page, urls);
  expect(errors).toEqual([]);
});
