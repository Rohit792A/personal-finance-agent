# API reference

The backend is one FastAPI application: the `app` object in [`app/api.py`](../app/api.py). The SPA in `app/frontend` is its only intended client, and both Cloud Run services (`finance-backend` for the personal environment, `finance-backend-demo` for the demo) run the same code.

This page describes every route, how to authenticate, and the conventions the routes share. The code is the source of truth. For a live, machine-readable schema, run the backend locally with `ENABLE_API_DOCS=1` and open `/docs` (Swagger UI), `/redoc` or `/openapi.json`. Those pages stay off in deployed environments because they list every route.

## Contents

- [Base URLs](#base-urls)
- [Authentication](#authentication)
- [Environments and AI limits](#environments-and-ai-limits)
- [Conventions](#conventions)
- [Endpoints](#endpoints)
  - [Health and session](#health-and-session)
  - [Document import](#document-import)
  - [Transactions](#transactions)
  - [Voice clarification](#voice-clarification)
  - [Analytics, budgets and forecast](#analytics-budgets-and-forecast)
  - [AI CFO](#ai-cfo)
  - [Gmail bank sync (personal only)](#gmail-bank-sync-personal-only)
  - [Agents](#agents)
  - [MCP tools](#mcp-tools)
  - [Evaluation and telemetry](#evaluation-and-telemetry)
  - [Internal routes (Cloud Scheduler only)](#internal-routes-cloud-scheduler-only)
- [Request bodies](#request-bodies)
- [Examples](#examples)

---

## Base URLs

| Where | URL | Started by |
| --- | --- | --- |
| Local, personal environment | `http://127.0.0.1:8080` | `./start_services.sh` |
| Local, demo environment | `http://127.0.0.1:8081` | `./start_services.sh demo` |
| Cloud Run | The service URL of `finance-backend` or `finance-backend-demo` | `scripts/deploy_backend.sh` |

The SPA reads its backend URL from `VITE_API_BASE_URL` (see `app/frontend/.env.example`).

---

## Authentication

Every request needs `Authorization: Bearer <Firebase ID token>`, except:

| Path | Why it's public |
| --- | --- |
| `GET /health` | Liveness probe for Cloud Run |
| `GET /` | Serves the built SPA when present (local only), otherwise the `/health` response |
| `/assets/*` | Static files of a locally built SPA |
| `/docs`, `/redoc`, `/openapi.json` | Only when `ENABLE_API_DOCS=1` |

Browser preflight requests (`OPTIONS`) are answered by CORS without a token. The check is done by `FirebaseAuthMiddleware` in [`app/auth.py`](../app/auth.py), so a new route is protected by default.

**What is checked**

1. The token is an RS256 JWT signed by one of Google's published Firebase certificates (cached according to their `Cache-Control` max-age, at most 6 hours).
2. Audience and issuer match the Firebase project (`FIREBASE_PROJECT_ID`, which defaults to `PROJECT_ID`). Expiry is enforced with 30 seconds of clock skew.
3. The account is on this backend's allowlist, `AUTH_ALLOWED_USERS`: a comma-separated list of `email` or `email:provider` entries. The email must be verified. When a provider is given (`google.com` or `password`), the sign-in must have used it.

A verified token is cached for up to 5 minutes, keyed by its SHA-256 hash, so a page load that fires a dozen requests verifies it once.

**`/internal/*` routes** don't accept Firebase tokens. They need a Google-signed OIDC token whose audience is `INTERNAL_AUDIENCE` (the service URL) and whose email is `SCHEDULER_SA_EMAIL` (Cloud Scheduler's service account). When `INTERNAL_AUDIENCE` is empty, every internal call is refused.

**Failures**

| Status | Meaning | `detail` |
| --- | --- | --- |
| `401` | No token, a malformed or invalid token, or an expired sign-in. The response carries `WWW-Authenticate: Bearer`. | `Sign in required.`, `Invalid sign-in token.` or `Your sign-in has expired. Please sign in again.` |
| `403` | Valid token, but the account isn't allowed into this environment (or the caller of an internal route isn't the scheduler) | `This account doesn't have access to this environment.` |
| `503` | The sign-in check itself failed unexpectedly (for example, the certificates couldn't be fetched) | `Sign-in check failed. Please try again.` |

**CORS.** Browsers may call the API only from the Firebase Hosting site and local development origins (`localhost`/`127.0.0.1` on ports 5173, 4173 and 8080). Override the list with `CORS_ALLOWED_ORIGINS` (comma-separated). CORS limits browsers only; the sign-in check is what protects the data.

---

## Environments and AI limits

`APP_ENV` selects the environment: `personal` (the owner's data, Gmail sync, no AI limits) or `demo` (sample data restored nightly, a shared login, Gmail off, daily AI limits). [`GET /me`](#health-and-session) tells the client which one it is talking to.

In the demo, AI features have daily limits (`AI_DAILY_LIMITS`, default `chat=40,insights=10,upload=10,voice=40,eval=3`). They are counted per day in India time and reset at midnight IST. A request counts when it starts, even if it later fails. When the limit is reached, the route returns `429` and says when the limit resets. A limit of `0` turns the feature off. Personal deployments have no limits unless `AI_DAILY_LIMITS` is set.

| Limit key | Routes |
| --- | --- |
| `upload` | `POST /upload` (counted only after the file passes the type and size checks) |
| `insights` | `GET /insights` |
| `chat` | `POST /insights/chat` |
| `voice` | `GET /transactions/{tx_id}/clarification-audio`, `POST /transactions/{tx_id}/resolve` |
| `eval` | `GET /eval/benchmark` |

Gmail routes return `403` (`Gmail sync is turned off in the demo.`) in the demo.

---

## Conventions

- **Format.** JSON in and out, except `POST /upload` and `POST /transactions/{tx_id}/resolve` (multipart form data), and the audio and PDF downloads (binary).
- **Errors** use FastAPI's shape: `{"detail": "<message>"}` with the status code. Request validation errors are `422` with FastAPI's standard list of field errors in `detail`.
- **Months** are `YYYY-MM`. `ALL` (any case), or leaving the parameter out, means all time.
- **Dates** are `YYYY-MM-DD`. Amounts are in Indian rupees.
- **Transaction types** are `EXPENSE`, `INCOME` and `TRANSFER`.
- **Human approval.** Routes through which an agent changes the ledger or learns a rule take `human_approved`. Without it, the agent's write is refused and the route returns `403`.

---

## Endpoints

### Health and session

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Public. Returns `{status: "healthy", message, timestamp}`. |
| `GET` | `/` | Public. The built SPA's `index.html` when `app/frontend/dist` exists (local only), otherwise the `/health` response. |
| `GET` | `/me` | The signed-in account and what this backend allows. Returns `{email, uid, provider, environment, features: {gmail_sync, uploads, ai}, ai_limits, data_resets_daily}`. `ai_limits` maps each limited feature to `{limit, used, remaining}` and is `null` without limits. The SPA calls this right after sign-in. |

### Document import

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/upload` | Imports one document into the ledger. Multipart fields: `file` (required) and `password` (optional, opens a password-protected PDF; it's used in memory and never stored or logged). |

- **Accepted files**: `.pdf` (bank statements), `.jpg`, `.jpeg`, `.png` (receipts), `.xlsx`, `.xls` (spreadsheets). Maximum 25 MB.
- **Pipeline**: the file is saved under a random folder with a sanitised name, then `process_file` in [`app/main.py`](../app/main.py) routes it by type to the matching Gemini extractor. Rows are reconciled against the ledger, so a re-uploaded statement adds no duplicates. The folder is deleted afterwards in every case.
- **Response**: `{status, message, new_transactions, total_transactions, notifications}`. `notifications` is a list of human-readable messages, including duplicate and page-coverage notes.
- **Errors**: `400` for an unsupported type, or a PDF that needs a password (or got a wrong one). `413` for files over 25 MB, `429` when the demo limit is reached, `500` when extraction fails.

### Transactions

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/transactions/months` | Per-period summaries for the ledger navigator: `{months, years, all}`. Each period has counts, income, expense, net, opening and closing balances, and the number of rows that need review. `all` covers all time. |
| `GET` | `/transactions` | Lists transactions. Returns `{transactions, total}`. Query parameters are below. |
| `POST` | `/transactions` | Adds a transaction by hand. Body: [`TransactionCreate`](#request-bodies). Returns `{status, message, id}`. |
| `PUT` | `/transactions/{transaction_id}` | Updates description, category and amount. Body: [`TransactionUpdate`](#request-bodies). A category edit can also save a learned payee rule, which is returned as `learned_rule` (or `null`). Returns `{status, learned_rule, message}`; `404` if the transaction doesn't exist. |
| `DELETE` | `/transactions/{transaction_id}` | Deletes a transaction and any linked asset entries. Returns `{status, message}`; `404` if it doesn't exist. |

Query parameters of `GET /transactions`:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `month` | all time | `YYYY-MM` or `ALL` |
| `type` | all | `EXPENSE` or `INCOME` exclude self-transfers and the Transfer category. `TRANSFER` matches the transfer type, self-transfers and the Transfer category. |
| `category` | all | Category name, case-insensitive |
| `search` | none | Case-insensitive substring of the description or category |
| `requires_review` | either | `true` for rows flagged for human review, `false` for the rest |
| `order_by` | `date` | One of `date`, `amount`, `description`, `category`, `confidence`, `id`, `type`, `review`. Anything else sorts by date. |
| `order_dir` | `desc` | `asc` or `desc`. Ties are broken by newest `id` first. |
| `limit` | `1000` | Maximum rows returned |

### Voice clarification

Rows the extractors weren't sure about (confidence below 85, or an unknown category) are flagged `requires_human_review`. These routes let the user resolve them by voice or text.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/transactions/{tx_id}/clarification` | The question to ask about a flagged row: `{id, description, amount, category, requires_human_review, confidence, question_text}`. `404` if the row doesn't exist. |
| `GET` | `/transactions/{tx_id}/clarification-audio` | The question as speech (`audio/mpeg`). Optional query parameters `description`, `amount` and `category` override the stored values. Counts against `voice`. `404` for an unknown row, `503` when speech synthesis is unavailable. |
| `POST` | `/transactions/{tx_id}/resolve` | Resolves the row from the user's answer. Multipart fields: `text_reply` or `audio_file` (one is required). Returns `{status, transcribed_text, resolved_category, learned_rule, message}`. Counts against `voice`. `400` when neither field is sent. |

### Analytics, budgets and forecast

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/analytics/summary` | KPIs for a month (`?month=YYYY-MM`) or all time: `{selected_month, kpis, expense_categories, income_categories, monthly_trends}`. |
| `GET` | `/recurring` | Detected recurring payments (subscriptions, utilities, habitual spending): `{total_recurring_items, estimated_monthly_burn, items}`. |
| `GET` | `/forecast` | Spending forecast and preparedness plan: `{reference_date, horizons, preparedness_plan, upcoming_predictions, category_forecasts}`. Each category's 30-day figure weights the previous month at 45%, the historical monthly average at 35% and the same month last year at 20%. |
| `GET` | `/budgets` | Budget against actual for a month (`?month=YYYY-MM`): `{month, total_budget, total_spent, overall_utilization_pct, categories}`. |
| `POST` | `/budgets` | Sets a category's monthly budget. Body: [`BudgetUpdate`](#request-bodies). Returns `{status, message}`. `400` for a negative or too-large amount (over 1e9), or a category name that isn't 1 to 100 characters. |

### AI CFO

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/insights` | An AI-written report on the ledger: `{insights}`. Counts against `insights`. |
| `POST` | `/insights/chat` | One turn of the AI CFO chat. Body: [`ChatRequest`](#request-bodies). Returns `{reply}`. The endpoint is stateless: the client sends the earlier turns in `history`. Counts against `chat`. |

Before a chat message reaches Gemini, it's screened for prompt injection (a local regex screen, then the Model Armor API when `MODEL_ARMOR_TEMPLATE` is set) and personal data is masked (local regex, plus Sensitive Data Protection when `SDP_ENABLED=1`). The advisor picks a model tier for the question (`MODEL_LITE`, `MODEL_NAME` or `MODEL_PRO`). A message that fails the injection screen still gets `200`, with a security-policy notice as the reply instead of a model answer. Unexpected failures return `500`.

### Gmail bank sync (personal only)

All of these return `403` in the demo.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/gmail/settings` | The saved sync settings and sync statistics. |
| `POST` | `/api/gmail/settings` | Saves the settings and verifies the IMAP login. Body: [`GmailSettingsRequest`](#request-bodies). `400` when the address or app password is missing or invalid. |
| `POST` | `/api/gmail/sync` | Syncs now: bank alert emails, and statement attachments unless turned off. Body: [`GmailSyncRequest`](#request-bodies) (optional). `400` when sync isn't configured. |
| `GET` | `/api/gmail/statements` | Statement PDFs exported from Gmail, grouped by month. |
| `GET` | `/api/gmail/statements/{stmt_id}/download` | Downloads one exported statement (`application/pdf`), from the private Cloud Storage bucket or, locally, from disk. `404` if it's missing. |
| `POST` | `/api/gmail/statements/{stmt_id}/unlock` | Unlocks a password-protected statement and imports it, or retries an import that failed. Body: `{password}`; send an empty password to retry a statement that is already unlocked. `400` when the password is missing or wrong, or the statement or its file can't be found. |

### Agents

The agent routes expose the multi-agent layer in [`app/mcp/`](../app/mcp): an orchestrator, four specialist agents (ingestion, reconciliation, clarification and advisor), A2A-style agent cards and an in-process message bus.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/.well-known/agent.json` | The orchestrator's agent card. |
| `GET` | `/a2a/agents/{agent_id}/.well-known/agent.json` | The card of a registered agent. `404` for an unknown agent. |
| `GET` | `/a2a/agents` | The specialist agents and their cards: `{agents}`. |
| `GET` | `/a2a/registry` | Every registered card: `{registry_status, gcp_project_id, gcp_agent_registry_api, total_agents, agent_cards}`. With `?include_gcp_live=true`, it also reads what is registered in Google Cloud's Agent Registry. |
| `POST` | `/a2a/registry/register` | Validates an `AgentCard` and registers it with the in-process A2A bus. Returns `{status, agent_card}`. |
| `POST` | `/a2a/registry/sync-gcp` | Registers the agent cards and the MCP server in Google Cloud's Agent Registry. This is a catalogue only: agent traffic doesn't go through it. |
| `POST` | `/a2a/agents/{agent_id}/tasks/send` | Sends a task to an agent. Body: `{intent, payload, sender_agent, workload_token}`; `skill_id` or `skill` are accepted instead of `intent`, and `sender_agent` defaults to `orchestrator`. |
| `POST` | `/a2a/delegate` | Sends a full `AgentMessage` (`{sender_agent, target_agent, intent, payload, workload_token}`) over the bus. |
| `POST` | `/agents/workflow` | Runs ingestion, reconciliation, clarification and advice over the transactions in the body. Body: [`FourAgentWorkflowRequest`](#request-bodies). |
| `POST` | `/agents/master-resolve` | Proposes category and type fixes across the ledger. Body: [`MasterResolveRequest`](#request-bodies); nothing changes unless `auto_apply` is `true`. |
| `POST` | `/agents/master-resolve/apply` | Applies approved proposals and learns payee rules from them. Body: [`MasterApplyRequest`](#request-bodies). `403` without human approval. |
| `POST` | `/agents/orchestrator/command` | The copilot available on every screen: answers questions about the current screen or runs a ledger action. Body: [`OmnipresentCommandRequest`](#request-bodies); `400` without `message` or `command`. |
| `GET` | `/agents/learned-rules` | The payee rules the clarification agent has learned: `{rules}`. |
| `POST` | `/agents/learned-rules` | Teaches a payee rule. Body: [`LearnedRuleRequest`](#request-bodies). `403` without human approval. |
| `DELETE` | `/agents/learned-rules?pattern=` | Deletes a rule by pattern. Returns `{status: "DELETED" or "NOT_FOUND", pattern}`. |
| `GET` | `/agents/audit-trail` | Recent agent audit-log entries: `{audit_logs}`. `limit` is 1 to 200 (default 50). |

Agents sign their messages with short-lived workload tokens, which are HMAC-signed, issued and verified inside one request. They are a local stand-in for workload identity; see [`app/security/`](../app/security).

### MCP tools

The ledger's tools are served by `LedgerMCPServer` ([`app/mcp/ledger_server.py`](../app/mcp/ledger_server.py)). Each call is checked against the calling agent's permissions; the caller defaults to `orchestrator`.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/mcp/tools` | The tool catalogue: `{protocol_version, server, tools}`. |
| `POST` | `/mcp/execute` | Runs one tool. Body: `{tool_name, arguments}`. Returns the tool's result; `400` when the tool reports an error (including a permission refusal). |
| `POST` | `/mcp/jsonrpc` | A JSON-RPC 2.0 endpoint with the MCP methods `initialize`, `ping`, `tools/list` and `tools/call`. `?agent_id=` sets the calling agent. A failed tool call comes back as an MCP result with `isError: true`, and an unknown method as JSON-RPC error `-32601`. Both use HTTP `200`. |

### Evaluation and telemetry

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/eval/benchmark` | Runs the evaluation suite in [`app/eval/`](../app/eval) (rule-based checks over a golden dataset) and returns its report. Counts against `eval`; `500` if the suite fails. |
| `GET` | `/telemetry/traces` | Recent agent workflow traces and the weakest step across them (failed steps first, then the slowest): `{weakest_step, traces}`. `limit` is 1 to 100 (default 20). |

### Internal routes (Cloud Scheduler only)

These accept only Cloud Scheduler's OIDC token (see [Authentication](#authentication)).

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/internal/gmail-sync` | Hourly, personal only (`404` in the demo). Syncs Gmail when the interval set on the Bank Sync screen has passed. Returns `{status: "synced", alerts_synced, statements_synced, pdfs_exported, emails_processed, synced_at}`, or `{status: "skipped", reason, interval_hours, last_synced_at}`. |
| `POST` | `/internal/demo-reset` | Nightly, demo only (`404` in the personal environment). Restores the sample data and returns `{status: "reset", summary}`. `409` when a safety check refuses the reset, for example when it would run against the personal database. |

---

## Request bodies

Field names and defaults come from the Pydantic models in [`app/api.py`](../app/api.py).

| Model | Fields |
| --- | --- |
| `TransactionCreate` | `date` (`YYYY-MM-DD`), `description`, `category`, `amount`, `type` (default `EXPENSE`) |
| `TransactionUpdate` | `description`, `category`, `amount` |
| `BudgetUpdate` | `category`, `monthly_budget` |
| `ChatRequest` | `message`; `history`: earlier turns as `[{role: "user" or "model", content}]` (default `[]`) |
| `GmailSettingsRequest` | `email_address`; `app_password` (Google app password); `pdf_password` (default `""`); `allowed_senders` (comma-separated allowlist of bank senders); `auto_sync_enabled` (default `true`); `sync_interval_hours` (default `24`) |
| `GmailSyncRequest` | `days_back` (default `7`), `include_statements` (default `true`) |
| `FourAgentWorkflowRequest` | `transactions` (default `[]`); optional `opening_balance`, `closing_balance`, `existing_alerts`, `current_balance`, `upcoming_commitments`, `user_question` |
| `MasterResolveRequest` | `month` (optional), `include_locked_other` (default `true`), `auto_apply` (default `false`), `transaction_ids` (optional) |
| `MasterApplyRequest` | `updates` (default `[]`), `human_approved` (default `true`) |
| `OmnipresentCommandRequest` | `message` or `command`; `current_screen` (default `transactions`); `current_month` (default `ALL`); `search_query` (default `""`); `history` (optional); `auto_execute` (default `true`) |
| `LearnedRuleRequest` | `category`; `payee_pattern` or `description`; `transaction_type` (default `EXPENSE`); `human_approved` (default `true`) |

---

## Examples

The examples call a local demo backend (`./start_services.sh demo`). They need an ID token for the demo account. The demo uses email and password sign-in, so the Firebase Auth REST API can issue one. The web API key is the `apiKey` in [`app/frontend/src/config/firebase.ts`](../app/frontend/src/config/firebase.ts); it identifies the Firebase project and isn't a secret.

```bash
API=http://127.0.0.1:8081
FIREBASE_WEB_API_KEY=...   # the apiKey value from app/frontend/src/config/firebase.ts
read -r -p "Demo email: " EMAIL; read -r -s -p "Demo password: " PASSWORD; echo

TOKEN=$(curl -s "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=$FIREBASE_WEB_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"returnSecureToken\":true}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["idToken"])')
```

ID tokens last an hour. The owner's Google account can't sign in this way; use a token from a signed-in browser session instead.

```bash
# Who am I, and what does this backend allow?
curl -s -H "Authorization: Bearer $TOKEN" "$API/me"

# October 2026 expenses, largest first
curl -s -H "Authorization: Bearer $TOKEN" \
  "$API/transactions?month=2026-10&type=EXPENSE&order_by=amount&order_dir=desc&limit=20"

# Import a statement (multipart)
curl -s -H "Authorization: Bearer $TOKEN" -F "file=@statement.pdf" -F "password=" "$API/upload"

# One chat turn, with the previous turns in history
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"How much did I spend on food last month?","history":[]}' "$API/insights/chat"

# List MCP tools over JSON-RPC
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' "$API/mcp/jsonrpc"
```
