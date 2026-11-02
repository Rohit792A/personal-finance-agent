# Developer Guide

This guide covers how the codebase is structured, how to run and test the backend and frontend locally, how to add new endpoints, MCP tools, or specialist agents, and how deployments and troubleshooting work.

For high-level system design, see [docs/APPLICATION_ARCHITECTURE.md](APPLICATION_ARCHITECTURE.md) and [docs/FDE_ENTERPRISE_ARCHITECTURE.md](FDE_ENTERPRISE_ARCHITECTURE.md). For the full HTTP API contract, see [docs/API.md](API.md). For contribution rules and commit conventions, see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Contents

- [Entry points and import root](#entry-points-and-import-root)
- [Backend module map](#backend-module-map)
- [Frontend module map](#frontend-module-map)
- [Local development workflows](#local-development-workflows)
- [Database and schema lifecycle](#database-and-schema-lifecycle)
- [Testing guide](#testing-guide)
- [Common extension recipes](#common-extension-recipes)
- [Deployment and rollback](#deployment-and-rollback)
- [Troubleshooting](#troubleshooting)

---

## Entry points and import root

| Component | Entry point | How it runs |
| --- | --- | --- |
| **HTTP API server** | [`app/api.py`](../app/api.py) (`app: FastAPI`) | `cd app && uvicorn api:app --host 127.0.0.1 --port 8080` (or via [`./start_services.sh`](../start_services.sh) / [`Dockerfile`](../Dockerfile)) |
| **Document import pipeline** | [`app/main.py`](../app/main.py) (`process_file()`) | Called by `POST /upload`, `GmailBankSyncService`, and the Ingestion Agent (`ingestion-agent`). Not an HTTP server. |
| **React SPA** | [`app/frontend/src/main.tsx`](../app/frontend/src/main.tsx) | `cd app/frontend && npm run dev` (Vite dev server on `http://localhost:5173`) |
| **Static recorded demo** | [`app/frontend/src/recorded/main.tsx`](../app/frontend/src/recorded/main.tsx) | `cd app/frontend && npm run build:recorded && npm run preview:recorded` (replays recorded fixtures; no backend needed) |

### `app/` is the Python import root

Across Cloud Run (`WORKDIR /app` in [`Dockerfile`](../Dockerfile)), [`start_services.sh`](../start_services.sh) (`cd "$ROOT_DIR/app"`), and [`tests/conftest.py`](../tests/conftest.py) (`sys.path.insert(0, APP_DIR)`), `app/` is always on `sys.path`.

- **Always import backend modules by their plain name**: `import config`, `from database import DatabaseManager`, `from security import ModelArmor`.
- **Never import as `from app.x import ...`**, because importing the same file under both `x` and `app.x` creates two module instances in Python (`sys.modules["config"]` vs `sys.modules["app.config"]`), splitting module-level caches and singletons.
- **Avoid heavy imports or network calls at module import time.** In [`app/api.py`](../app/api.py), `process_file` and `FinancialAdvisor` are imported lazily inside functions so the Vertex AI SDK (~4 s import) does not slow down Cloud Run cold starts or `/health` checks.

---

## Backend module map

```text
app/
├── api.py                  # FastAPI server: middleware, REST routes, MCP JSON-RPC, A2A routes, AI quotas
├── auth.py                 # FirebaseAuthMiddleware (RS256 JWT + allowlist) & Cloud Scheduler OIDC checks
├── config.py               # Env loader (.env + os.environ), Gemini 3 tier settings, personal vs demo defaults
├── secret_store.py         # Resolves secrets from env vars first, then Google Secret Manager
├── owner_profile.py        # Loads optional git-ignored app/private/owner_profile.json
├── database.py             # DatabaseManager: SQLAlchemy pool, idempotent DDL + RLS lockdown, reconciliation
├── dedupe.py               # Pure planning functions for cross-statement duplicate detection
├── date_guard.py           # Repairs misread statement years on AI-extracted transaction dates
├── demo_seed.py            # Deterministic sample data and guarded nightly reset for APP_ENV=demo
├── main.py                 # Document ingestion orchestrator: process_file(file_path, pdf_password)
├── statement_agent.py      # Page-chunked PDF bank statement reader (pypdf + Gemini 3) with coverage checks
├── statement_store.py      # Stores Gmail-exported PDFs on local disk or private GCS bucket (STATEMENT_BUCKET)
├── extractor.py            # Receipt & bill image extractor
├── ai_importer.py          # Excel (.xlsx / .xls) ledger importer
├── gmail_sync.py           # IMAP bank alert parser, PDF attachment exporter, password unlocker
├── advisor.py              # FinancialAdvisor: Model Armor -> DLP -> MCP context -> tiered Gemini 3 -> DLP restore
├── context_cache.py        # Optional explicit Vertex AI context cache for the advisor's system persona
├── voice_agent.py          # Speech-to-Text / Text-to-Speech clarification loop and payee rule learning
├── gemini.py               # Shared helper for extracting text safely from Vertex AI responses
├── models.py               # Pydantic schemas for structured Gemini extraction outputs
├── exceptions.py           # Custom domain exceptions (e.g. PasswordRequiredError)
├── security/
│   ├── auth.py             # AgentIdentityManager, SPIFFE-style IDs, HMAC workload tokens, HITL approval gates
│   ├── model_armor.py      # Prompt-injection & document screening (local regex + optional Model Armor API)
│   ├── model_armor_api.py  # Google Cloud Model Armor API client wrapper (MODEL_ARMOR_TEMPLATE)
│   ├── dlp.py              # Reversible PII tokenization (<PAN_REDACTED_1>, etc.) before LLM calls
│   └── sdp_api.py          # Google Cloud Sensitive Data Protection (DLP API) client wrapper (SDP_ENABLED=1)
├── mcp/
│   ├── ledger_server.py    # LedgerMCPServer: 9 financial tools + JSON-RPC 2.0 handler + per-agent ACLs
│   ├── a2a_orchestrator.py # A2AOrchestrator bus, 4 specialist agents, and AgenticCFOOrchestrator workflows
│   ├── agent_registry.py   # A2A AgentCard models and Google Cloud Agent Registry sync
│   └── client.py           # In-process MCP client used by agents
├── telemetry/
│   ├── tracer.py           # OpenTelemetry spans, bounded in-memory ring buffer, optional Cloud Trace export
│   └── usage.py            # Extracts prompt/candidate/cached token counts from Vertex AI usage_metadata
└── eval/
    ├── autorater.py        # Rule-based evaluation suite (reconciliation math, completeness, F1, safety)
    └── golden_dataset.json # Synthetic golden test cases for the evaluation suite
```

---

## Frontend module map

```text
app/frontend/src/
├── main.tsx                # React root: mounts ThemeProvider -> AuthProvider -> SessionProvider -> App
├── App.tsx                 # Route switch and shell layout
├── config/firebase.ts      # Public Firebase Web SDK config
├── auth/                   # Firebase sign-in (Google for personal, email/password for demo)
├── session/                # Calls GET /me after sign-in; holds environment & daily AI quota state
├── api/                    # Typed fetch client (injects Bearer token, handles 401 refresh) & domain modules
├── shell/                  # Top-level screens (Transactions, Analytics, Budgets, Forecast, CFO, Bank Sync, Agents)
├── components/             # Shared visual components and legacy chart helpers
├── ui/                     # Design-system primitives
├── theme/                  # Dark / light theme tokens
└── utils/                  # Formatting, currency, and HTML sanitization helpers
```

---

## Local development workflows

### 1. Running the full stack against the cloud demo database

If you have `gcloud` authenticated with access to the project's Secret Manager:

```bash
./start_services.sh demo --frontend
```

This starts:
- the demo backend on `http://127.0.0.1:8081` (`APP_ENV=demo`, reading `pfa-db-password-demo` from Secret Manager), and
- the Vite dev server on `http://127.0.0.1:5173` with `VITE_API_BASE_URL=http://127.0.0.1:8081`.

Stop both with `./start_services.sh stop`.

### 2. Running against a local PostgreSQL container (no cloud secrets needed)

You can run the backend completely independent of Supabase and Secret Manager by starting a local PostgreSQL container and setting `DATABASE_URL` and `DB_PASS`:

```bash
docker run -d --name pfa-dev-pg -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:55432:5432 postgres:17

cd app
APP_ENV=demo \
DATABASE_URL=postgresql://postgres@127.0.0.1:55432/postgres \
DB_PASS=local \
ENABLE_API_DOCS=1 \
../venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8081 --reload
```

On first connection, `DatabaseManager` automatically creates all tables, views, indexes, and row-level security policies. To seed the local database with the deterministic demo dataset:

```bash
APP_ENV=demo \
DATABASE_URL=postgresql://postgres@127.0.0.1:55432/postgres \
DB_PASS=local \
venv/bin/python scripts/demo_reset.py
```

With `ENABLE_API_DOCS=1`, interactive Swagger documentation is available at `http://127.0.0.1:8081/docs`.

---

## Database and schema lifecycle

All schema management lives in [`DatabaseManager._init_db()`](../app/database.py).

- **Idempotent initialization**: `_init_db()` runs once per process (guarded by `_SCHEMA_INITIALIZED_ENGINES`) and executes `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, index creation, and `CREATE OR REPLACE VIEW`.
- **Automatic lockdown**: Every table has `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`, and privileges are revoked from `PUBLIC`, `anon`, and `authenticated` so Supabase's PostgREST data API cannot read or modify rows even if exposed. Views use `security_invoker = true`.
- **Adding a column or table**:
  1. Add the `CREATE TABLE IF NOT EXISTS` or `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statement in `DatabaseManager._init_db()`.
  2. If creating a new table, add its name to the RLS/REVOKE loop in `_init_db()` so it gets the same security posture.
  3. Add a test in `tests/test_reconcile_db.py` (or a new unit test) verifying the schema and behavior.

---

## Testing guide

### Test tiers

| Tier | Command | What it runs | Credentials / network needed? |
| --- | --- | --- | --- |
| **1. Unit & API tests** (CI default) | `venv/bin/python -m pytest tests -q` | 180+ tests across auth, deduplication, date guard, statement chunking, owner profile, model config, Model Armor, SDP, tracing, context cache, and demo seed | **None.** Runs offline; skips tiers 2 and 3 automatically. |
| **2. Postgres reconciliation tests** | `TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55433/postgres venv/bin/python -m pytest tests/test_reconcile_db.py -q` | Real SQL reconciliation, idempotent statement re-imports, and learned-rule retroactive updates against throwaway schemas | Local PostgreSQL container only. |
| **3. Live integration tests** | `PFA_LIVE_TESTS=1 venv/bin/python -m pytest tests/test_fde_integration.py -q` | End-to-end calls against the configured database and Vertex AI | **Yes** — opt-in only because it touches the live database. |
| **4. Frontend static checks** (CI default) | `cd app/frontend && npm run typecheck && npm run lint && npm run build` | TypeScript project references (`tsc -b`), strict ESLint (`--max-warnings=0`), and production Vite bundle | None. |
| **5. End-to-end browser tests** | `cd app/frontend && npm run test:e2e` | Playwright + `@axe-core/playwright` accessibility checks | Reads demo login from `~/.config/personal-finance-agent/demo-login.txt`. |

### Testing FastAPI routes without Firebase tokens

In unit tests, `FirebaseAuthMiddleware` resolves `self._authenticate` at request time. You can stub authentication on `TestClient(api.app)` by monkeypatching `auth.authenticate_user`:

```python
import api
import auth
from fastapi.testclient import TestClient

def test_protected_route(monkeypatch):
    monkeypatch.setattr(
        auth,
        "authenticate_user",
        lambda token: auth.AuthUser(uid="u1", email="test@example.com", provider="password"),
    )
    client = TestClient(api.app)
    resp = client.get("/me", headers={"Authorization": "Bearer fake-token"})
    assert resp.status_code == 200
```

### Golden guardrail regression test

[`tests/test_guardrails_unchanged.py`](../tests/test_guardrails_unchanged.py) verifies that when the optional Google Cloud governance flags (`MODEL_ARMOR_TEMPLATE`, `SDP_ENABLED`, `ENABLE_CLOUD_TRACE`, `ENABLE_VERTEX_CONTEXT_CACHE`) are unset, the regex screens, PII redaction tokens, and advisor prompts match [`tests/fixtures/guardrails_golden.json`](../tests/fixtures/guardrails_golden.json) byte-for-byte and construct zero Google Cloud API clients. Run this test whenever you edit [`app/security/`](../app/security) or [`app/advisor.py`](../app/advisor.py).

---

## Common extension recipes

### Adding a new API route

1. Define request/response Pydantic models in [`app/api.py`](../app/api.py).
2. Add the route handler on `app`. It is automatically protected by `FirebaseAuthMiddleware` (do not add it to `public_paths` unless it is an unauthenticated probe).
3. If the route calls Gemini or Speech APIs, attach `dependencies=[Depends(ai_quota("<feature>"))]` so demo daily limits apply.
4. If the route is personal-only (like Gmail sync), attach `dependencies=[Depends(require_gmail_enabled)]`.
5. Add a typed client function in [`app/frontend/src/api/`](../app/frontend/src/api) and document the route in [`docs/API.md`](API.md).

### Adding a new MCP tool

1. In [`app/mcp/ledger_server.py`](../app/mcp/ledger_server.py), register the tool schema in `LedgerMCPServer._register_tools()` and implement its handler method.
2. In [`app/security/auth.py`](../app/security/auth.py), add the tool name to `allowed_tools` on the relevant agent identities in `AgentIdentityManager`, and add it to `approval_required_tools` if it mutates data.
3. Add a unit test exercising both an allowed call and a permission/approval refusal.

---

## Deployment and rollback

All production deployments use [`scripts/deploy_backend.sh`](../scripts/deploy_backend.sh), which builds one container image in Artifact Registry and deploys it to both Cloud Run services (`finance-backend-demo` in `asia-northeast3` first, then `finance-backend` in `asia-northeast1`) and runs smoke checks (`/health` -> `200`, unauthenticated `/me` -> `401`, `/transactions` -> `401`).

```bash
# Build once and deploy to demo first, then personal:
scripts/deploy_backend.sh build

# Or build and deploy to demo only, verify, then promote the exact image tag to personal:
scripts/deploy_backend.sh build demo
scripts/deploy_backend.sh <image-tag> personal
```

To roll a service back to a previous revision or image tag, re-run `scripts/deploy_backend.sh <previous-image-tag> <demo|personal>` or route traffic to the previous Cloud Run revision.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `SecretNotFoundError: Could not read secret 'pfa-db-password-...'` on startup | Neither `DB_PASS` nor Google Secret Manager credentials are available. | Set `DB_PASS` (and `DATABASE_URL` if using local Postgres) in `.env`, or run `gcloud auth application-default login`. |
| `ValueError: Refusing to start: APP_ENV=demo is pointed at the personal database.` | `APP_ENV=demo` was set alongside `SUPABASE_PROJECT_REF` or `DB_PASSWORD_SECRET` from the personal environment. | Unset the personal overrides in `.env` so `config.py` picks the demo defaults, or pass a local `DATABASE_URL`. |
| `401 Invalid sign-in token.` or `403 This account doesn't have access to this environment.` | The Firebase ID token is expired, for another project, or its email/provider is not listed in `AUTH_ALLOWED_USERS`. | Check `AUTH_ALLOWED_USERS` in `.env` (format: `email:provider`, e.g. `user@example.com:password`). |
| `404` on `/docs` or `/openapi.json` | Interactive API docs are off by default (`ENABLE_API_DOCS=0`). | Start the server with `ENABLE_API_DOCS=1`. |
| `400 This PDF is password-protected` on statement upload | The PDF is encrypted and no password (or the wrong password) was supplied. | Pass the password in the `password` form field on `POST /upload` or via the Bank Sync unlock modal. |
| `ModuleNotFoundError` when running `pytest` or `uvicorn` | Running `uvicorn` from the repo root instead of `app/`, or using system Python instead of `venv/bin/python`. | Run `venv/bin/python -m pytest tests -q` from the repo root, and run `uvicorn api:app` from inside `app/` (or use `./start_services.sh`). |
