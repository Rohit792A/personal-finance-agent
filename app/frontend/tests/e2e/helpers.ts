/**
 * Shared helpers for the Playwright suites.
 * The demo login is read from a private file at runtime; it is never printed or written anywhere.
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';

export const DEMO_BACKEND = 'https://finance-backend-demo-328452160072.asia-northeast3.run.app';

export interface ScreenInfo {
  id: string;
  path: string;
  label: string;
}

export const SCREENS: readonly ScreenInfo[] = [
  { id: 'transactions', path: '/transactions', label: 'Transactions' },
  { id: 'analytics', path: '/analytics', label: 'Analytics' },
  { id: 'budgets', path: '/budgets', label: 'Budgets' },
  { id: 'forecast', path: '/forecast', label: 'Recurring & Forecast' },
  { id: 'upload', path: '/upload', label: 'Upload' },
  { id: 'ai-cfo', path: '/ai-cfo', label: 'AI CFO' },
  { id: 'agents', path: '/agents', label: 'Agent Registry' },
  { id: 'bank-sync', path: '/bank-sync', label: 'Bank Sync' },
];

function readDemoCredentials(): { email: string; password: string } {
  const file = path.join(homedir(), '.config', 'personal-finance-agent', 'demo-login.txt');
  const text = readFileSync(file, 'utf8');
  const email = /^EMAIL:\s*(\S+)\s*$/m.exec(text)?.[1];
  const password = /^PASSWORD:\s*(.+?)\s*$/m.exec(text)?.[1];
  if (!email || !password) throw new Error(`EMAIL/PASSWORD lines not found in ${file}`);
  return { email, password };
}

/** Submits the demo email/password form (real Firebase sign-in) without waiting for the outcome. */
export async function submitDemoSignIn(page: Page): Promise<void> {
  const { email, password } = readDemoCredentials();
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible();
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign in to the demo' }).click();
}

/** Real Firebase email/password sign-in with the demo account; resolves once the app shell shows. */
export async function signInAsDemo(page: Page): Promise<void> {
  await submitDemoSignIn(page);
  await expect(page.getByRole('navigation', { name: 'Main' }).first()).toBeVisible({ timeout: 45_000 });
}

/** Console errors and uncaught exceptions seen by the page. */
export function trackConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => {
    errors.push(`pageerror: ${error.message}`);
  });
  return errors;
}

export async function setTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
  const current = await page.evaluate(() => document.documentElement.dataset.theme);
  if (current !== theme) await page.getByTestId('theme-toggle').first().click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
}

export interface ContrastResult {
  violations: number;
  needsReview: number;
  details: string[];
}

/** axe-core color-contrast (WCAG 2 AA: 4.5:1 text, 3:1 large text) within `include`. */
export async function scanContrast(page: Page, include: string, exclude?: string): Promise<ContrastResult> {
  let builder = new AxeBuilder({ page }).withRules(['color-contrast']).include(include);
  if (exclude) builder = builder.exclude(exclude);
  const result = await builder.analyze();
  const details = result.violations.flatMap((rule) =>
    rule.nodes.map((node) => {
      const summary = (node.failureSummary ?? '').replace(/\s+/g, ' ').replace('Fix any of the following: ', '');
      return `${node.target.join(' ')} | ${node.html.slice(0, 140)} | ${summary}`;
    }),
  );
  const needsReview = result.incomplete.reduce((sum, rule) => sum + rule.nodes.length, 0);
  return { violations: details.length, needsReview, details };
}

/** Waits until a legacy screen has rendered its content (not just its loading state). */
export async function waitForScreen(page: Page, screenId: string): Promise<void> {
  const root = page.locator(`#legacy-screen-${screenId}`);
  switch (screenId) {
    case 'transactions':
      await expect(root.locator('#ledger-table-body tr').first()).toBeVisible();
      await expect(root.locator('#ledger-table-body tr.animate-pulse')).toHaveCount(0);
      break;
    case 'analytics':
      await expect(root.locator('#analytics-content-body .animate-fade-in').first()).toBeVisible();
      break;
    case 'budgets':
      await expect(root.locator('#budget-content-body .animate-fade-in').first()).toBeVisible();
      break;
    case 'forecast':
      await expect(root.locator('.animate-fade-in').first()).toBeVisible();
      break;
    case 'upload':
      await expect(root.locator('#drop-zone')).toBeVisible();
      break;
    case 'ai-cfo':
      await expect(root.locator('form, button').first()).toBeVisible();
      break;
    default:
      break;
  }
  // Let toasts and late renders settle.
  await page.waitForTimeout(400);
}
