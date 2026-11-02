/**
 * Shell behaviour with a mocked backend (page.route on the demo Cloud Run URL) and REAL Firebase
 * sign-in: the email/password account is routed to the demo backend, which is what gets mocked.
 */
import { expect, test } from '@playwright/test';
import { signInAsDemo, submitDemoSignIn, trackConsoleErrors, waitForScreen } from './helpers';
import { DEMO_ME, mockBackend, XSS } from './mock-backend';

test.describe.configure({ mode: 'serial' });

test('signed-out visitors see only the sign-in screen and no backend calls', async ({ page }) => {
  const requests = await mockBackend(page);
  await page.goto('/budgets?month=2026-10');
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Demo account' })).toBeVisible();
  await expect(page.locator('section.legacy-scope')).toHaveCount(0);
  expect(requests).toEqual([]);
});

test('wrong demo password shows a clear message', async ({ page }) => {
  await mockBackend(page);
  await page.goto('/');
  await page.getByLabel('Email', { exact: true }).fill('nobody@example.com');
  await page.getByRole('button', { name: 'Sign in to the demo' }).click();
  await expect(page.getByRole('alert')).toHaveText('Enter your password.');
  await page.getByLabel('Password', { exact: true }).fill('definitely-not-the-password');
  await page.getByRole('button', { name: 'Sign in to the demo' }).click();
  await expect(page.getByRole('alert')).toContainText(/Wrong email or password|Too many sign-in attempts/);
});

test('demo sign-in routes to the demo backend with a Bearer token; demo badge; Bank Sync panel', async ({ page }) => {
  const errors = trackConsoleErrors(page);
  const requests = await mockBackend(page);
  await page.goto('/');
  await signInAsDemo(page);
  await waitForScreen(page, 'transactions');

  expect(requests.length).toBeGreaterThan(0);
  expect(requests[0]?.path).toBe('/me');
  for (const request of requests) expect(request.authorization).toMatch(/^Bearer [\w-]+\.[\w-]+\.[\w-]+$/);

  await expect(page.getByTestId('demo-banner')).toHaveText(/Demo · sample data, resets nightly/);
  await page.getByTestId('user-menu-button').click();
  await expect(page.getByTestId('user-menu')).toContainText(DEMO_ME.email);
  await expect(page.getByTestId('environment-badge')).toHaveText('Demo');
  await expect(page.getByTestId('user-menu')).toContainText('4 of 5');
  await page.keyboard.press('Escape');

  await page.goto('/bank-sync');
  await expect(page.getByTestId('feature-unavailable-gmail')).toContainText('Gmail sync is turned off in the demo');
  expect(requests.filter((request) => request.path.startsWith('/api/gmail'))).toEqual([]);
  expect(errors).toEqual([]);
});

test('401: one retry with a refreshed token, then sign-out with a message', async ({ page }) => {
  let transactionCalls = 0;
  await mockBackend(page, {
    handlers: {
      'GET /transactions/months': async (route) => {
        transactionCalls += 1;
        await route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"Invalid sign-in token."}' });
      },
    },
  });
  await page.goto('/');
  // The shell shows at most briefly before the client signs out, so don't wait for it.
  await submitDemoSignIn(page);
  await expect(page.getByTestId('sign-in-notice')).toHaveText('Your session has expired. Please sign in again.', {
    timeout: 45_000,
  });
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible();
  expect(transactionCalls).toBeGreaterThanOrEqual(2);
});

test('403 on /me shows the no-access screen with sign out', async ({ page }) => {
  await mockBackend(page, {
    handlers: {
      'GET /me': (route) =>
        route.fulfill({ status: 403, contentType: 'application/json', body: '{"detail":"This account doesn\'t have access."}' }),
    },
  });
  await page.goto('/');
  await submitDemoSignIn(page); // the shell never appears: /me says this account has no access
  await expect(page.getByRole('heading', { name: "This account doesn't have access" })).toBeVisible({ timeout: 45_000 });
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible();
});

test('429 shows the backend detail in the toast', async ({ page }) => {
  const detail = 'Daily AI limit reached for the demo (5 per day). Try again tomorrow.';
  await mockBackend(page, {
    handlers: {
      'GET /insights': (route) => route.fulfill({ status: 429, contentType: 'application/json', body: JSON.stringify({ detail }) }),
    },
  });
  await page.goto('/ai-cfo');
  await signInAsDemo(page);
  await page.locator('#fetch-insights-btn').click();
  await expect(page.locator('#toast-container')).toContainText(detail);
});

test('XSS payloads in data, AI markdown, month labels and errors stay inert', async ({ page }) => {
  const errors = trackConsoleErrors(page);
  await mockBackend(page, {
    handlers: {
      'GET /budgets': (route) =>
        route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: XSS.img }) }),
    },
  });
  await page.goto('/transactions');
  await signInAsDemo(page);
  await waitForScreen(page, 'transactions');
  await expect(page.locator('#ledger-table-body')).toContainText('<img src=x');

  await page.goto('/analytics');
  await waitForScreen(page, 'analytics');
  await page.goto('/budgets');
  await expect(page.locator('#toast-container')).toContainText('<img src=x');
  await page.goto('/ai-cfo');
  await page.locator('#fetch-insights-btn').click();
  await expect(page.locator('#insights-result')).toContainText('You saved');

  const injected = await page.evaluate(() => ({
    flag: (window as unknown as { __xss?: number }).__xss ?? 0,
    imgs: document.querySelectorAll('img[src="x"]').length,
    scripts: document.querySelectorAll('#root script, .legacy-scope script').length,
    iframes: document.querySelectorAll('iframe').length,
    svgOnload: document.querySelectorAll('svg[onload]').length,
    jsLinks: document.querySelectorAll('a[href^="javascript:"]').length,
  }));
  expect(injected).toEqual({ flag: 0, imgs: 0, scripts: 0, iframes: 0, svgOnload: 0, jsLinks: 0 });
  await expect(page.getByTestId('period-picker')).toContainText('September 2026 <img');
  expect(errors.filter((message) => !message.includes('500'))).toEqual([]);
});

test('statement PDF download goes through the authenticated client', async ({ page }) => {
  const requests = await mockBackend(page, {
    me: { features: { gmail_sync: true, uploads: true, ai: true } },
    handlers: {
      'GET /api/gmail/settings': (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ configured: true, email_address: 'x@example.com', last_sync_status: 'OK' }),
        }),
      'GET /api/gmail/statements': (route) => {
        const statement = {
          id: 7,
          bank_name: 'HDFC',
          file_name: 'HDFC_Sep_2026.pdf',
          email_subject: 'Your statement',
          email_date: '2026-10-01',
          is_encrypted: false,
          transactions_imported: 12,
        };
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ total_pdfs: 1, months: [{ month_key: '2026-09', count: 1, statements: [statement] }], statements: [statement] }),
        });
      },
      'GET /api/gmail/statements/7/download': (route) =>
        route.fulfill({ status: 200, contentType: 'application/pdf', body: '%PDF-1.4\n%mock\n' }),
    },
  });
  await page.goto('/bank-sync');
  await signInAsDemo(page);
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download PDF' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('HDFC_Sep_2026.pdf');
  const call = requests.find((request) => request.path === '/api/gmail/statements/7/download');
  expect(call?.authorization).toMatch(/^Bearer /);
});

test('clarification audio is fetched with the token when speech synthesis is unavailable', async ({ page }) => {
  await page.addInitScript(() => {
    // Chromium defines speechSynthesis on window itself (Window is a [Global] interface), not on
    // Window.prototype, so it has to be deleted there for the modal to use the server audio.
    delete (window as unknown as { speechSynthesis?: unknown }).speechSynthesis;
  });
  const requests = await mockBackend(page, {
    handlers: {
      'GET /transactions/3/clarification': (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ id: 3, description: 'UPI', amount: 1850, category: 'Other', question_text: 'What was this?' }),
        }),
      'GET /transactions/3/clarification-audio': (route) =>
        route.fulfill({ status: 200, contentType: 'audio/mpeg', body: Buffer.from([0xff, 0xfb, 0x90, 0x00]) }),
    },
  });
  await page.goto('/transactions');
  await signInAsDemo(page);
  await waitForScreen(page, 'transactions');
  await page.locator('#ledger-table-body .voice-resolve-btn').first().click();
  await expect.poll(() => requests.find((request) => request.path === '/transactions/3/clarification-audio')?.authorization ?? '').toMatch(/^Bearer /);
});
