# Personal Finance AI

**An AI-assisted personal finance ledger for Indian bank accounts.** It reads bank statements, receipts, spreadsheets and Gmail bank alerts with Google Gemini, reconciles them into one deduplicated ledger, and adds analytics, budgets, forecasts, an AI "CFO" chat and voice clarification on top. A small fleet of cooperating agents (A2A) and an MCP server sit behind the same API.

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)
![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?logo=typescript&logoColor=white)
![Gemini on Vertex AI](https://img.shields.io/badge/Gemini_3.5-Vertex_AI-4285F4?logo=googlecloud&logoColor=white)
![License: all rights reserved](https://img.shields.io/badge/license-all_rights_reserved-lightgrey)

**Live app:** [personal-finance-agent-507010.web.app](https://personal-finance-agent-507010.web.app) (sign-in required). A demo account with synthetic data is available on request.

**Recorded demo:** [OWNER.github.io/personal-finance-agent](https://OWNER.github.io/personal-finance-agent/) (placeholder until the first GitHub Pages deployment). No sign-in and no backend: it replays responses recorded from the real backend and Gemini. See [Try the recorded demo](#try-the-recorded-demo).

> [!NOTE]
> This is a personal project that runs in production for its author. The code is published so you can read and evaluate it; it is **not open source** (see [License](#license)). Real financial data never enters this repository: statements, uploads and owner-specific values are git-ignored (see [Privacy](#security-and-privacy)).

---

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Getting started](#getting-started)
- [Try the recorded demo](#try-the-recorded-demo)
- [Configuration](#configuration)
- [Testing](#testing)
- [Deployment](#deployment)
- [Security and privacy](#security-and-privacy)
- [Documentation](#documentation)
- [Project status](#project-status)
- [License](#license)

---

## Features

**Getting data in**
- **Bank statement PDFs** (HDFC, Kotak, SBI and others). Long statements are read a few pages per Gemini call in parallel, with checks for cut-off replies and missing rows, so a partial read never imports silently.
- **Password-protected PDFs** are unlocked in memory before the model sees them: with the password typed on the Upload screen, or with the statement passwords stored in Secret Manager. Passwords are never logged or stored.
- **Receipts and bills** (images) and **spreadsheets** (`.xlsx`/`.xls`) through dedicated AI importers.
- **Gmail Bank Sync**: bank alert emails and monthly statement attachments arrive over IMAP, either hourly through Cloud Scheduler or on demand.

**Keeping the ledger correct**
- **Deduplication at import time.** An incoming row that is already in the ledger (a Gmail alert, an overlapping statement, a re-import, the other side of a transfer between your own accounts) updates that row instead of adding a copy. The evidence is a shared 12-digit UPI/IMPS reference number or matching amount and wording. Each statement file is imported only once (SHA-256).
- **Changes need proof.** A re-import never relabels a row on its own say-so, and rows you edited are locked against automatic changes.
- **A date guard** keeps AI-extracted dates in the statement's period. This fixes the "uploaded in 2026, saved as 2023" class of bugs.
- **A review queue** for low-confidence rows, with text or voice answers (Cloud Speech-to-Text and Text-to-Speech).
- **Closed-loop learning.** Every confirmed category becomes a learned payee rule. The rule is applied to matching unverified rows, used as few-shot memory in extraction prompts and enforced on later imports.

**Understanding your money**
- Transactions, Analytics (month start and end balances, category breakdown), Budgets, Recurring & Forecast, and an **AI CFO** chat grounded in the ledger through MCP tools, with model tiering (Flash-Lite for look-ups, Flash for analysis and planning).
- **A copilot dock** on every screen that answers voice or text commands about the current page.

**Agents and governance**
- **A four-agent workflow**: ingestion, reconciliation, clarification and advisor agents, behind an orchestrator. They talk over an Agent2Agent (A2A) bus with published Agent Cards and are mirrored to Google Cloud Agent Registry.
- **A Ledger MCP server** (official `mcp` SDK, JSON-RPC 2.0) that exposes the ledger as tools.
- **Guardrails**: per-agent identities with least-privilege tool allowlists (HMAC-signed workload tokens, a local stand-in for SPIFFE), human approval for writes, an audit log, prompt-injection screening (local regex; plus the Google Cloud Model Armor API on prompts, document text and replies when configured), PII masking before prompts (local regex with reversible tokens; plus Sensitive Data Protection when enabled), OpenTelemetry traces (optionally exported to Cloud Trace) and an autorater against a golden dataset.

**Two environments from one codebase**
- **Personal**: Google sign-in, owner only.
- **Demo**: email and password sign-in, an 18-month synthetic ledger that resets nightly, Gmail off and daily AI limits.
- Plus a **recorded demo**: a static copy of the app for GitHub Pages that replays responses recorded from the demo environment ([details](#try-the-recorded-demo)).

---

## Architecture

```mermaid
flowchart LR
    U["Browser<br/>React + TypeScript SPA"] -->|"loads the app"| H["Firebase Hosting"]
    U -->|"sign-in"| FA["Firebase Auth"]
    U -->|"HTTPS + Bearer token"| P["Cloud Run: finance-backend<br/>FastAPI (personal)"]
    U -->|"HTTPS + Bearer token"| D["Cloud Run: finance-backend-demo<br/>FastAPI (demo)"]
    CS["Cloud Scheduler"] -->|"OIDC: /internal/gmail-sync"| P
    CS -->|"OIDC: /internal/demo-reset"| D
    P --> DB[("Supabase Postgres<br/>personal")]
    D --> DB2[("Supabase Postgres<br/>demo")]
    P --> V["Vertex AI<br/>Gemini 3.5 Flash / Flash-Lite"]
    D --> V
    P --> SM["Secret Manager"]
    D --> SM
    P --> GCS["Cloud Storage<br/>private statement PDFs"]
    P --> SP["Speech-to-Text /<br/>Text-to-Speech"]
    D --> SP
    P --> GM["Gmail (IMAP)"]
    P -.->|"sync Agent Cards"| AR["Agent Registry"]
```

Inside the backend, uploads flow through `main.process_file` (unlock, hash check, extraction, date guard, reconciliation, batch save). The agents in `mcp/` reach the ledger only through the MCP server's tools. [docs/APPLICATION_ARCHITECTURE.md](docs/APPLICATION_ARCHITECTURE.md) has the detailed diagrams. [flow.md](flow.md) maps every file-to-file connection.

---

## Tech stack

| Layer | Technology |
| --- | --- |
| Backend | Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2 + `pg8000`, Pydantic 2 |
| AI | Gemini 3.5 Flash and Flash-Lite on Vertex AI (`vertexai` SDK from `google-cloud-aiplatform`, global endpoint), Cloud Speech-to-Text, Text-to-Speech |
| Documents | `pypdf` (page splitting, decryption), pandas + openpyxl/xlrd (spreadsheets), Pillow (images) |
| Agents | Official `mcp` SDK (MCP server and JSON-RPC), A2A Agent Cards, Google Cloud Agent Registry |
| Frontend | React 19, TypeScript 5.9, Vite 5, Tailwind CSS 3, React Router 7, Firebase Auth, DOMPurify + marked |
| Data | Supabase Postgres (row-level security on every table), Cloud Storage for statement PDFs |
| Platform | Cloud Run (two services, one image), Cloud Build, Artifact Registry, Secret Manager, Cloud Scheduler, Firebase Hosting |
| Governance (opt-in) | Model Armor API, Sensitive Data Protection (Cloud DLP API), Cloud Trace via OpenTelemetry; each behind a flag (see [Configuration](#configuration)) |
| Quality | pytest, ESLint (`typescript-eslint` strict, type-checked), Playwright + axe-core, GitHub Actions |

---

## Repository layout

```text
.
├── app/                        # Backend import root (modules import each other by plain name)
│   ├── api.py                  # FastAPI app: REST, MCP JSON-RPC, A2A endpoints, AI quotas
│   ├── main.py                 # Import pipeline: unlock → hash check → extract → reconcile → save
│   ├── database.py             # Postgres access, schema, lockdown, cross-source reconciliation
│   ├── statement_agent.py      # Page-chunked Gemini statement reader with coverage checks
│   ├── extractor.py            # Receipt / bill reader
│   ├── ai_importer.py          # Spreadsheet importer
│   ├── gmail_sync.py           # Gmail alerts and statement attachments (IMAP)
│   ├── advisor.py              # AI CFO chat and insight reports (model tiering)
│   ├── voice_agent.py          # Voice clarification and payee-rule learning
│   ├── dedupe.py, date_guard.py
│   ├── owner_profile.py        # Loads owner-only data from the git-ignored app/private/
│   ├── auth.py                 # Firebase ID token + Cloud Scheduler OIDC verification
│   ├── security/               # Agent identities, Model Armor screening, DLP masking
│   ├── mcp/                    # Ledger MCP server, A2A bus, orchestrator, Agent Registry
│   ├── telemetry/, eval/       # Traces; autorater and golden dataset
│   ├── frontend.py             # Legacy Streamlit dashboard (not used by the deployed app)
│   └── frontend/               # React + TypeScript SPA (Vite), Firebase Hosting config, Playwright tests
│       ├── src/recorded/       # Recorded demo mode: replays recorded responses, no sign-in or backend
│       └── recorded-demo/      # The recorded responses (written by scripts/record_demo.py)
├── tests/                      # pytest suite (unit, Postgres-backed, opt-in live suite)
├── scripts/                    # Infra, deploy, secrets, demo reset and recording, dedupe and migration tools
├── docs/                       # Architecture, audit, overhaul plan, handover, examples
├── decision.md                 # Architecture decision log (DEC-001 …)
├── flow.md                     # File-to-file code flow diagrams
├── Dockerfile                  # Backend image (non-root, one image for both environments)
├── requirements.txt            # Pinned runtime dependencies (what the image installs)
├── requirements-dev.txt        # + test tools (pytest)
└── start_services.sh           # Local launcher (personal / demo backend, optional Vite)
```

---

## Getting started

> [!IMPORTANT]
> The code is wired to the author's Google Cloud and Firebase projects. To run your own copy you need your own GCP project (Vertex AI, Secret Manager, Cloud Storage), a Postgres database and a Firebase project. Then point `PROJECT_ID`, the database settings, `AUTH_ALLOWED_USERS` and [`app/frontend/src/config/firebase.ts`](app/frontend/src/config/firebase.ts) at them.

### Prerequisites

- Python 3.12 and Node.js 20 or newer (CI uses Node 24)
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) with Application Default Credentials: `gcloud auth application-default login`
- Docker (optional; only for the Postgres-backed tests)

### Backend

```bash
python3.12 -m venv venv
venv/bin/pip install -r requirements-dev.txt   # runtime deps + pytest
cp .env.example .env            # optional: local overrides, see Configuration

./start_services.sh             # personal backend on http://127.0.0.1:8080
./start_services.sh demo        # demo backend (demo database) on http://127.0.0.1:8081
./start_services.sh stop
```

`start_services.sh` runs `uvicorn api:app` from `app/`. Secrets are read from Secret Manager with your gcloud login. To skip Secret Manager, set `DB_PASS` (and `DATABASE_URL` for a local Postgres) in `.env`. Every route except `/health` needs a Firebase sign-in, so use the web app or send a real ID token.

### Frontend

```bash
cd app/frontend
npm ci
cp .env.example .env.development.local   # then set VITE_API_BASE_URL, e.g. http://127.0.0.1:8081
npm run dev                              # http://localhost:5173
```

Or start the demo backend and the dev server together with `./start_services.sh demo --frontend`.

---

## Try the recorded demo

**[Open the recorded demo](https://OWNER.github.io/personal-finance-agent/)** (placeholder: replace `OWNER` once GitHub Pages has deployed it). No sign-in needed.

The recorded demo is this React app built so that it needs no backend, no Firebase and no Google Cloud, so it keeps working after the Cloud Run services are switched off. It is a static site on GitHub Pages.

- Every screen shows responses recorded from the real backend on the demo environment's synthetic data: the 18-month ledger and its review queue, analytics, budgets, recurring payments and the forecast, the Agent Registry with its skills and MCP tools, and the upload of a synthetic statement.
- The AI CFO chat offers the recorded questions as suggestion chips and replays Gemini's recorded answers; other questions get a note listing the recorded ones. The Jarvis copilot works the same way.
- Changes (edits, budgets, category answers, rules, uploads) are acknowledged but not saved.
- A banner shows the recording date, with links to this repository and the demo video.

How it works: [`scripts/record_demo.py`](scripts/record_demo.py) runs the real FastAPI app in-process (FastAPI `TestClient`) against a throwaway local PostgreSQL seeded with the demo data, calls the endpoints the screens use, and saves the responses in [`app/frontend/recorded-demo/recorded/`](app/frontend/recorded-demo/recorded/) with a manifest (recording date, commit, Gemini token usage). `npm run build:recorded` builds the frontend from `src/recorded/main.tsx`; its API client answers every request from those files ([`src/recorded/transport.ts`](app/frontend/src/recorded/transport.ts)). The normal build is unchanged and contains none of this code.

**Preview it locally:**

```bash
cd app/frontend
npm run build:recorded      # served from /personal-finance-agent/ (set PAGES_BASE_PATH to change it)
npm run preview:recorded    # http://localhost:4173/personal-finance-agent/
npm run test:e2e:recorded   # optional: Playwright checks every screen and that no request leaves the site
```

**Re-record it** (for example after a feature change):

```bash
venv/bin/python scripts/record_demo.py           # temporary local PostgreSQL + Gemini on Vertex AI (ADC)
venv/bin/python scripts/record_demo.py --no-ai   # everything except the Gemini calls; costs nothing
venv/bin/python -m pytest tests/test_recorded_fixtures.py -q
```

The recorder needs the PostgreSQL server binaries (`initdb`, `pg_ctl`) or `--database-url` for an empty local database, and Application Default Credentials with Vertex AI access for the AI answers. It never touches Supabase, Secret Manager or the owner profile, and it refuses to run next to a `.env` file. It prints the Gemini token usage and stops at `--max-spend-inr` (default ₹300); a full recording is 13 Gemini calls, about 52,000 tokens. `tests/test_recorded_fixtures.py` (part of the normal test run) checks the files: complete, real AI answers, and nothing that looks like a credential or personal data.

**Publish it on GitHub Pages:** [`.github/workflows/pages.yml`](.github/workflows/pages.yml) builds the recorded demo, checks that its code calls no backend, Firebase or Google service, and deploys it on every push to `main` (or from **Actions → Recorded demo (GitHub Pages) → Run workflow**).

1. Push the repository to GitHub.
2. In the repository, open **Settings → Pages** and under **Build and deployment** set **Source** to **GitHub Actions**.
3. Run the workflow (or re-run its first, failed run). The site appears at `https://<owner>.github.io/personal-finance-agent/`; replace the placeholder links above with it.
4. Optional, under **Settings → Secrets and variables → Actions → Variables**: `DEMO_VIDEO_URL` (shown in the banner), `DEMO_REPO_URL` (defaults to this repository), `PAGES_BASE_PATH` (defaults to the path GitHub Pages reports).

To point the live app at the recorded demo when its backend is unreachable, build it with `VITE_RECORDED_DEMO_URL=https://<owner>.github.io/personal-finance-agent/ npm run build`.

---

## Configuration

The backend reads environment variables, plus an optional git-ignored `.env` in the repository root. [`.env.example`](.env.example) lists every setting with its default. The most important ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `personal` | `personal` or `demo`: picks the database, sign-in allowlist and AI limits |
| `PROJECT_ID` / `LOCATION` | author's project / `global` | Vertex AI and Secret Manager project. Gemini 3 is served from `global` |
| `MODEL_NAME` / `MODEL_LITE` / `MODEL_PRO` | `gemini-3.5-flash` / `gemini-3.5-flash-lite` / `gemini-3.5-flash` | Model per tier |
| `DB_PASS` / `DATABASE_URL` | Secret Manager / Supabase pooler | Database password and optional full connection URL |
| `AUTH_ALLOWED_USERS` | owner / demo account | Comma-separated `email` or `email:provider` allowlist |
| `STATEMENT_BUCKET` | (local disk) | Private Cloud Storage bucket for statement PDFs |
| `STATEMENT_PAGES_PER_CALL` / `STATEMENT_PARALLEL_CALLS` | `2` / `6` | Statement reading chunk size and parallelism |
| `OWNER_PROFILE_PATH` | `app/private/owner_profile.json` | Owner-only data (see below) |
| `MODEL_ARMOR_TEMPLATE` | (off) | Full Model Armor template name, `projects/{p}/locations/{l}/templates/{id}`. Turns on the Model Armor API for chat prompts, document text and the advisor's replies, after the regex screen (the fallback if the API fails). `MODEL_ARMOR_TIMEOUT_SECONDS` (default 5) bounds each call |
| `SDP_ENABLED` | `0` | `1` adds Sensitive Data Protection (Cloud DLP API) findings to the regex PII redaction; 12-digit reference numbers in statement rows are kept. Tuning: `SDP_INFO_TYPES`, `SDP_MIN_LIKELIHOOD` (`LIKELY`), `SDP_LOCATION` (`global`), `SDP_TIMEOUT_SECONDS` (10) |
| `ENABLE_CLOUD_TRACE` | `0` | `1` also exports the OpenTelemetry spans (one trace per chat, insights request or upload) to Cloud Trace |
| `ENABLE_VERTEX_CONTEXT_CACHE` | `0` | `1` puts the advisor's static persona in an explicit Vertex AI cache per model. gemini-3.5-flash only caches 4,096+ tokens, so with today's ~170-token persona creation fails once and requests go out as usual. Token usage (including implicit-cache hits) is recorded regardless |

The Google APIs above stay off until their flags are set, and the app behaves exactly as without them. Each service account then needs the matching role: `roles/modelarmor.user`, `roles/dlp.user`, `roles/cloudtrace.agent`.

**Secrets** live in Google Secret Manager and are never committed: `pfa-db-password-personal`, `pfa-db-password-demo`, `pfa-gmail-app-password`, `pfa-statement-passwords`. Store one with `scripts/set_secret.sh <name>`. The script uses a hidden prompt, so the value never appears in shell history.

**Owner profile.** The personal deployment knows a few things about its owner: their own account numbers (internal sweeps), how their name appears in UPI narrations (self-transfers), family and friends whose credits are reimbursements, and statement-reconciled monthly balances. These live in `app/private/owner_profile.json`. Git ignores that file, but Cloud Build still ships it in the backend image. Without the file, the rules fall back to generic keywords and no balances are seeded. The format is in [docs/examples/owner_profile.example.json](docs/examples/owner_profile.example.json).

---

## Testing

```bash
# Backend unit tests: no Google credentials, network or database needed
venv/bin/python -m pytest tests -q

# Postgres-backed reconciliation tests against a throwaway local database
docker run -d --name pfa-reconcile-test -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:55433:5432 postgres:17
TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55433/postgres venv/bin/python -m pytest tests/test_reconcile_db.py -q

# Live integration suite: calls the real personal database and Gemini, so it is opt-in
PFA_LIVE_TESTS=1 venv/bin/python -m pytest tests/test_fde_integration.py -q

# Frontend
cd app/frontend
npm run typecheck && npm run lint && npm run build
npm run test:e2e                # Playwright + axe; real demo sign-in, mocked backend (see below)
```

The Playwright suite signs in to Firebase with the demo account. It reads the credentials at runtime from `~/.config/personal-finance-agent/demo-login.txt` and never prints them. `E2E_BACKEND=http://127.0.0.1:8081 npm run test:e2e -- integration` runs the integration spec against a local backend instead of mocks.

GitHub Actions runs the backend unit tests and the frontend type-check, lint and build on every push and pull request ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)). The suites that need credentials (Postgres, live and Playwright) run locally. The recorded demo has its own Playwright spec, which needs no credentials: `npm run build:recorded && npm run test:e2e:recorded`.

---

## Deployment

| What | How |
| --- | --- |
| One-time infrastructure | `scripts/phase1_infra.sh` creates Artifact Registry, least-privilege service accounts, secret access and the private statement bucket (idempotent) |
| Backend (both Cloud Run services, one image, plus Scheduler jobs) | `scripts/deploy_backend.sh build`, or demo first: `scripts/deploy_backend.sh build demo`, then `scripts/deploy_backend.sh <image-tag> personal` |
| Frontend | `cd app/frontend && npm run build && firebase deploy --only hosting` |
| Recorded demo (GitHub Pages) | Automatic on push to `main` ([`.github/workflows/pages.yml`](.github/workflows/pages.yml)); see [Try the recorded demo](#try-the-recorded-demo) |
| Agent Registry sync | `venv/bin/python scripts/register_gcp_agents.py` |
| Demo data reset (normally nightly via Scheduler) | `APP_ENV=demo venv/bin/python scripts/demo_reset.py` |

Each deploy records its image tag and a one-line rollback command in [decision.md](decision.md). [docs/PRODUCTION_HANDOVER_AND_SIGN_OFF.md](docs/PRODUCTION_HANDOVER_AND_SIGN_OFF.md) has the runbook.

---

## Security and privacy

- **Sign-in on every route.** The backend verifies a Firebase ID token and an allowlist on every request. `/internal/*` accepts only Cloud Scheduler OIDC tokens. CORS is limited to the hosting site and localhost.
- **No credentials in code.** Database, Gmail and statement passwords come from Secret Manager. Cloud Run mounts the database password as a secret.
- **The database is locked down.** Every table has row-level security, Supabase's anon and authenticated roles get no access, and views run as the caller (`security_invoker`).
- **Uploads are constrained**: a type allowlist, a 25 MB cap, a random folder per upload and sanitised file names. PDF passwords stay in memory only.
- **AI guardrails**: prompt-injection screening (local regex, plus the Model Armor API when `MODEL_ARMOR_TEMPLATE` is set), PII masking before prompts (local regex, plus Sensitive Data Protection when `SDP_ENABLED=1`), least-privilege agent tools and human approval for writes.
- **Personal data stays out of git.** Statements (`data/`), uploads, screenshots of real transactions and the owner profile (`app/private/`) are all git-ignored. The public history starts from a scrubbed snapshot.

Found a vulnerability? Please follow [SECURITY.md](SECURITY.md) and don't open a public issue.

---

## Documentation

| Document | What's in it |
| --- | --- |
| [docs/APPLICATION_ARCHITECTURE.md](docs/APPLICATION_ARCHITECTURE.md) | System diagrams, agent workflows, data model, idempotency design |
| [docs/FDE_ENTERPRISE_ARCHITECTURE.md](docs/FDE_ENTERPRISE_ARCHITECTURE.md) | The Build / Govern / Scale / Optimize design: MCP, A2A, identity, evaluation, model tiering |
| [docs/GCP_ARCHITECTURE_PITCH_GUIDE.md](docs/GCP_ARCHITECTURE_PITCH_GUIDE.md) | Every Google Cloud service used and why |
| [docs/PRODUCTION_HANDOVER_AND_SIGN_OFF.md](docs/PRODUCTION_HANDOVER_AND_SIGN_OFF.md) | Infrastructure inventory, runbook, acceptance matrix |
| [docs/UX_ENTERPRISE_AUDIT.md](docs/UX_ENTERPRISE_AUDIT.md) and [docs/OVERHAUL_PLAN.md](docs/OVERHAUL_PLAN.md) | The UX audit and the phased plan that followed it |
| [app/frontend/FRONTEND_GUIDE.md](app/frontend/FRONTEND_GUIDE.md) | Frontend structure, styling and sanitisation |
| [decision.md](decision.md) | Every architectural and operational decision, newest first |
| [flow.md](flow.md) | File-to-file code flow diagrams and connection tables |
| [CHANGELOG.md](CHANGELOG.md) | Notable changes by milestone |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development workflow and conventions |

> [!TIP]
> Links in `decision.md` and `flow.md` use local `file:///` paths for the author's editor. On GitHub they show as plain text, and every path is relative to the repository root.

---

## Project status

The project is in active development. Phases 0 (security and correctness) and 1 (sign-in, two environments, React shell) of the [overhaul plan](docs/OVERHAUL_PLAN.md) are complete, along with the agent platform, statement reconciliation and import hardening that followed (see [CHANGELOG.md](CHANGELOG.md)).

---

## License

Copyright © 2026 Rohit Kushwaha. **All rights reserved.** The source is published for reading and evaluation only. No license is granted to copy, modify, distribute or deploy it without written permission. See [LICENSE](LICENSE).

Nothing in this repository is financial advice.
