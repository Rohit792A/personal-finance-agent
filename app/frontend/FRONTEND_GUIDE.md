# Personal Finance AI: Frontend Guide

The frontend is a **React 19 + TypeScript** single-page app built with **Vite 5** and **Tailwind CSS 3**, with
**Firebase Authentication** for sign-in and **react-router 7** for URL state. It is hosted on Firebase Hosting.

The seven screens are still the original vanilla-JS components (`src/components/*.js`). Since Phase 1 they run
unchanged inside the React shell. Each one moves to React in Phase 3 (see `docs/OVERHAUL_PLAN.md`).

---

## 1. Directory structure

```
app/frontend/
├── index.html                 # #root, pre-paint theme script, loads src/main.tsx
├── vite.config.ts             # dev server, same-origin proxy, build (no source maps, vendor chunks)
├── tailwind.config.ts         # semantic colour tokens (CSS variables) + legacy theme bridge
├── tailwind.legacy-theme.ts   # re-maps the legacy screens' dark-first palette classes for light mode
├── eslint.config.js           # ESLint 9 flat config, strict type-checked rules for all TS
├── tsconfig*.json             # strict TS: app (src/) and node (configs, tests)
├── playwright.config.ts       # end-to-end tests (mocked and integration modes)
├── firebase.json              # Hosting: serves dist/, rewrites every path to /index.html
├── .env.example               # documented dev-only variables (no production settings)
├── tests/e2e/                 # Playwright suites and helpers
└── src/
    ├── main.tsx               # entry: renders <App /> in StrictMode
    ├── App.tsx                # ErrorBoundary → ThemeProvider → router → AuthProvider → AuthGate
    ├── styles.css             # design tokens (light + dark), legacy-screen styles, markdown styles
    ├── config/
    │   ├── firebase.ts        # Firebase web config (public by design) and the auth instance
    │   └── backends.ts        # sign-in method → backend URL; VITE_API_BASE_URL dev override
    ├── auth/                  # AuthProvider (Firebase state), SignInScreen, error messages
    ├── session/               # SessionProvider: picks the backend, binds the API client, loads /me
    ├── shell/                 # AppShell, Sidebar, TopBar, PeriodPicker, SearchBox, UserMenu,
    │                          # DemoBanner, FeatureUnavailable, AgentRegistryScreen, NotFound, screens.ts, urlState.ts
    ├── legacy/
    │   ├── index.js           # the only entry to the legacy screens (loaded after sign-in)
    │   ├── index.d.ts         # types for the legacy init functions and instances
    │   ├── bridge.ts          # LegacyBridge: mounts screens once, syncs them with the URL
    │   └── LegacyScreens.tsx  # one persistent container per screen, visibility follows the route
    ├── api/                   # the single HTTP client and typed endpoint functions
    │   ├── client.ts          # token, base URL, 401/403/429 handling, blob downloads
    │   ├── config.ts          # endpoint paths and timeouts
    │   ├── errors.ts          # ApiError (message = backend `detail`)
    │   ├── agents.ts, me.ts, transactions.ts, bank.ts, insights.ts, upload.ts
    │   └── index.ts           # barrel; the legacy screens import '../api/index.ts'
    ├── theme/                 # light/dark theme context, persisted in localStorage
    ├── ui/                    # small shared pieces (buttons, spinner, logo, messages, boundary)
    ├── components/            # LEGACY vanilla-JS screens (ledger, analytics, budgets, recurring,
    │                          # upload, insights, bankSync, modal, toast)
    └── utils/                 # LEGACY helpers: dom.js (escapeHtml), formatters.js, markdown.js
```

---

## 2. What happens on a visit

1. **Signed out:** whatever the URL, only the sign-in screen renders (`AuthGate` in `App.tsx`). The URL is kept,
   so a deep link opens after sign-in. No backend request is made and the legacy code isn't loaded.
2. **Sign-in:** "Continue with Google" (`signInWithPopup`; `authDomain` stays on `firebaseapp.com`) or the demo
   email and password form. Firebase error codes become short messages (`auth/authErrors.ts`).
3. **Backend selection** (`config/backends.ts`), from the ID token's `firebase.sign_in_provider` claim:
   - `google.com` → personal backend `https://finance-backend-328452160072.asia-northeast1.run.app`
   - `password` → demo backend `https://finance-backend-demo-328452160072.asia-northeast3.run.app`

   No email address is in the bundle; each backend enforces its own allowlist anyway.
4. **`GET /me`** (`session/SessionProvider.tsx`): on success the shell renders with the account, environment,
   enabled features and demo AI quota in React context. A 403 shows "This account doesn't have access" with a
   sign-out button; other failures show "Couldn't connect to your workspace" with Try again.
5. **Shell:** the sidebar, the top bar (period picker, search, theme toggle, user menu) and, in the demo, the
   "Demo · sample data, resets nightly" banner. Screens whose feature is off (`me.features`) show a
   `FeatureUnavailable` panel instead of loading the legacy screen; in the demo that is Bank Sync.
6. **Legacy screens** load with a dynamic `import('./index.js')` only now, and each mounts the first time it is
   shown.

---

## 3. API client rules (`src/api/client.ts`)

Every backend call, from React and from the legacy screens, goes through this one client:

- Adds `Authorization: Bearer <Firebase ID token>` (`user.getIdToken()` refreshes it automatically).
- Sends the request to the signed-in account's backend (`setApiSession`). `/health` is the only public route.
- **401:** retries once with a force-refreshed token, then signs out and shows "Your session has expired".
- **403:** the error carries the backend `detail`. Outside `/me` and `/api/gmail/*` (expected feature gates)
  the shell re-checks access via `/me`.
- **429** and every other error: `ApiError` whose `message` is the backend `detail`, so legacy toasts show it.
- Binary responses (statement PDFs, clarification audio) use `apiFetchBlob`, so they are authenticated too.
  Never build backend URLs into `href`/`src` attributes or call `fetch` directly.
- Requests use `credentials: 'same-origin'` (DEC-028): Cloud Shell web preview needs its cookie on same-origin
  calls; the cross-origin Cloud Run calls still send no cookies, and the backend trusts only the Bearer token.

---

## 4. URL state

| Part | Meaning |
| :--- | :--- |
| Path | One per screen: `/transactions` (home), `/analytics`, `/budgets`, `/forecast`, `/upload`, `/ai-cfo`, `/bank-sync`. `/` redirects to `/transactions`; unknown paths show Not found. |
| `?month=YYYY-MM` | The global period (absent = all time). Shared by Transactions, Analytics and Budgets and by the period picker. |
| `?q=text` | Transactions search (the top-bar search box navigates to `/transactions?q=…`; Ctrl/⌘+K focuses it). |
| `?view=overview\|years\|months&year=YYYY` | The ledger's statement overview stages instead of the table. |

`shell/urlState.ts` parses and writes these in a canonical order; invalid values are dropped without a new
history entry. Back/forward and deep links work (Firebase Hosting rewrites every path to `/index.html`).

---

## 5. Legacy screens inside the shell

- `legacy/LegacyScreens.tsx` renders one `<section class="legacy-scope">` per screen. Each stays in the DOM for
  the whole session and only its `hidden` attribute follows the route, so a screen is **never initialised
  twice** and keeps its filters, chat history and scroll position.
- `legacy/bridge.ts` (`LegacyBridge`) does what the old `main.js` did:
  - applies the URL's month to the ledger, analytics and budgets, and the URL's search and stage to the ledger;
  - writes changes made inside a legacy screen back to the URL (one history entry per user action);
  - after an upload or Gmail sync, refreshes every mounted screen and bumps `dataVersion` so the period
    picker reloads its months;
  - routes the ledger's cross-links (`onNavigatePage`) to the router.
- The legacy files have only small integration hooks (`initialState`, `onStageChange`, `onSearchChange`,
  `navigateToStage`, `setMonth`, authenticated downloads). Don't rewrite them here; rebuild them in React.
- Types for the init functions are in `legacy/index.d.ts`. Legacy `.js` files are excluded from type-checking
  and linting. When a screen moves to React, delete its export from `legacy/index.js`.

---

## 6. Theme and design tokens

- Light is the default; the toggle sets `data-theme="dark"` on `<html>` and persists it in `localStorage`
  (`pfa-theme`). An inline script in `index.html` applies the saved theme before first paint.
- Colours are CSS variables in `styles.css` (`--color-canvas`, `--color-surface`, `--color-fg`, `--color-accent`,
  status colours …), exposed as Tailwind classes (`bg-surface`, `text-fg-muted`, `border-line`, `bg-accent` …).
  New code uses only these semantic classes.
- The legacy screens use raw palette classes (`bg-slate-900`, `text-white` …). `tailwind.legacy-theme.ts` routes
  those through variables that are redefined only inside `.legacy-scope`: inverted slate scale and AA-safe
  colour shades in light mode, lifted muted text in dark mode. Outside `.legacy-scope` they render as stock
  Tailwind.
- Target: WCAG 2 AA contrast (4.5:1 text, 3:1 large text) in both themes; the integration suite measures it.

---

## 7. Security rules

- Data from the server is untrusted. Legacy templates escape it with `escapeHtml` (`utils/dom.js`); AI markdown
  goes only through `renderMarkdown` (`utils/markdown.js`: marked + DOMPurify allowlist); toasts are text-only.
  React code never uses `dangerouslySetInnerHTML`.
- The Firebase web config in `config/firebase.ts` is public by design. Access is enforced by Firebase Auth and
  the backends. No secrets, emails or credentials belong in the bundle.
- Production builds have no source maps (`sourcemap: false`).

---

## 8. Development

Requires Node 24 (Cloud Shell: `/usr/local/nvm/versions/node/v24.21.0`). From `app/frontend/`:

| Command | What it does |
| :--- | :--- |
| `npm run dev` | Vite dev server on http://localhost:5173 |
| `npm run typecheck` | `tsc -b` (strict, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`) |
| `npm run lint` | ESLint, zero warnings allowed |
| `npm run build` | type-check, then the production build in `dist/` |
| `npm run preview` | serves `dist/` on port 4173 |
| `npm run test:e2e` | Playwright (see section 9) |

**Which backend the dev server uses.** Without settings, the same routing as production (section 2). To use a
local backend, put the override in `.env.development.local` (git-ignored, read only by `npm run dev`):

- `VITE_API_BASE_URL=http://127.0.0.1:8081` sends every account to the local demo backend started with
  `../../start_services.sh demo` (its CORS allows `http://localhost:5173`).
- `VITE_API_BASE_URL=` (empty) is same-origin mode for **Cloud Shell web preview**: the browser calls the Vite
  server and the proxy forwards every backend path to `BACKEND_URL` (default `http://127.0.0.1:8080`). Page
  loads of paths shared with the API (`/transactions`, `/budgets` …) still get the app.

Production builds ignore `VITE_API_BASE_URL`. See `.env.example`.

---

## 9. End-to-end tests (Playwright)

Both suites use the **real Firebase sign-in** with the demo account. The login is read at runtime from
`~/.config/personal-finance-agent/demo-login.txt` (`EMAIL:` and `PASSWORD:` lines). Never print, commit or
hard-code it; traces stay off because they would record the typed password.

```bash
# Mocked backend (page.route on the demo Cloud Run URL): 401/403/429 handling, XSS payloads,
# demo badge, Bank Sync panel, authenticated PDF download and audio fetch.
npm run test:e2e

# Integration against a real local demo backend (start it first: ../../start_services.sh demo).
# Every page in light and dark without console errors, URL state, theme persistence,
# and the axe colour-contrast report (test-results/contrast-report.json).
E2E_BACKEND=http://127.0.0.1:8081 npm run test:e2e
```

- Playwright starts its own Vite server on port 5173, so stop any other dev server on that port first.
- Chromium comes from `~/.cache/ms-playwright`; keep `@playwright/test` on a version that uses the installed
  browser build. Install browsers with `npx playwright install chromium` if the cache was deleted.
- Output goes to `test-results/` (git-ignored; screenshots can show account data). Each run clears it, so copy
  `contrast-report.json` elsewhere before running the other suite if you need it.

---

## 10. Deploy

Deploys are explicit and done by the owner after review:

```bash
npm run build && firebase deploy --only hosting
```

`firebase.json` serves `dist/` and rewrites every path to `/index.html` so deep links work. Cache headers:

- every path gets `Cache-Control: no-cache`, so browsers revalidate the page on each visit and pick up a deploy
  immediately (a header rule for `/index.html` alone doesn't cover `/` or deep links);
- `/assets/**` gets `public, max-age=31536000, immutable`: Vite puts a content hash in every asset name, so a
  changed file always has a new URL. When two rules set the same header, Firebase applies the later one.

Check a preview first with `firebase hosting:channel:deploy <name> --expires 1h` if you change these rules.
