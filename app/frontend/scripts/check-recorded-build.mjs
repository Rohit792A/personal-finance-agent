#!/usr/bin/env node
/**
 * Checks a recorded demo build (npm run build:recorded) before it is published:
 * - the fixtures are there (dist/recorded/manifest.json and the files it names);
 * - the code (JS, CSS, HTML) contains no URL of a backend, Firebase or Google service, and none of the
 *   Firebase SDK, so the published site can't call them. The fixtures are data, not code, and are not
 *   scanned here (tests/test_recorded_fixtures.py checks them).
 *
 * Usage, from app/frontend: node scripts/check-recorded-build.mjs [dist]
 * Exit code 0 when clean, 1 otherwise.
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const dist = process.argv[2] ?? 'dist';

/** Hosts the recorded demo must never call (any subdomain). */
const FORBIDDEN_HOSTS = [
  'run.app',
  'web.app',
  'firebaseapp.com',
  'firebaseio.com',
  'firebasestorage.app',
  'googleapis.com',
  'google.com',
  'gstatic.com',
  'googleusercontent.com',
  'cloudfunctions.net',
];

/**
 * Links that are in the bundle but are not requests: the Bank Sync screen's help link for Gmail app
 * passwords (a plain <a href>; the recorded demo shows "Gmail sync is turned off in the demo" instead).
 */
const ALLOWED_URLS = new Set(['https://myaccount.google.com/apppasswords']);

/** Firebase SDK and sign-in markers that only appear when the SDK is bundled. */
const FORBIDDEN_MARKERS = ['@firebase/', 'identitytoolkit', 'securetoken.google', 'firebase/auth', 'AIzaSy'];

function filesUnder(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? filesUnder(path) : [path];
  });
}

const problems = [];

const manifestPath = join(dist, 'recorded', 'manifest.json');
if (!existsSync(manifestPath)) {
  problems.push(`${manifestPath} is missing: build with npm run build:recorded.`);
} else {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const files = new Set(Object.values(manifest.datasets ?? {}));
  for (const section of ['routes', 'chat', 'copilot', 'agent_tasks', 'mcp_tools']) {
    for (const entry of manifest[section] ?? []) files.add(entry.file);
  }
  for (const file of files) {
    if (!existsSync(join(dist, 'recorded', file))) problems.push(`dist/recorded/${file} (named in the manifest) is missing.`);
  }
}

const code = filesUnder(dist).filter((path) => /\.(?:js|mjs|css|html)$/.test(path) && !path.includes(`${join(dist, 'recorded')}`));
const urlPattern = /https?:\/\/[^\s"'`()<>\\]+/g;
for (const path of code) {
  const text = readFileSync(path, 'utf8');
  for (const match of text.matchAll(urlPattern)) {
    const url = match[0];
    let host = '';
    try {
      host = new URL(url).hostname.toLowerCase();
    } catch {
      continue;
    }
    const forbidden = FORBIDDEN_HOSTS.find((domain) => host === domain || host.endsWith(`.${domain}`));
    if (forbidden && !ALLOWED_URLS.has(url)) problems.push(`${relative(dist, path)}: ${url}`);
  }
  for (const marker of FORBIDDEN_MARKERS) {
    if (text.includes(marker)) problems.push(`${relative(dist, path)}: contains "${marker}" (Firebase SDK)`);
  }
}

if (problems.length > 0) {
  console.error(`Recorded demo build check failed:\n- ${problems.join('\n- ')}`);
  process.exit(1);
}
console.log(`Recorded demo build check passed: ${String(code.length)} code files, no backend, Firebase or Google URLs.`);
