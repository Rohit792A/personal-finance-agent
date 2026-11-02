# Architecture & Agent Decision Log (`decision.md`)

> **Status:** Active (Always-On via `AGENTS.md`)  
> **Purpose:** Tracks all architectural, design, and implementation decisions made by the Antigravity (`agy`) agent across the codebase. Whenever `agy` modifies, adds, or refactors files, a new entry **must** be recorded here.

---

## How to Record a New Decision
Every time `agy` makes changes to the codebase, append or prepend an entry using the following format:
- **Date / Timestamp**: `YYYY-MM-DD HH:MM UTC`
- **Project / Scope**: `personal-finance-agent` | `ats-resume-tailor` | `workspace`
- **Files Changed**: Clickable links to the modified/created files
- **Decision & Rationale**: Why this approach was chosen over alternatives
- **Flow Impact**: Whether [`flow.md`](file:///home/rkushwaha8610/flow.md) was updated to reflect new file connections

---

## Decision History

> **ATS Resume Tailor decisions live in [`ats-resume-tailor/decision.md`](file:///home/rkushwaha8610/ats-resume-tailor/decision.md)** (moved there on 2026-10-20 at the owner's request: DEC-040, DEC-039, DEC-038, DEC-037, DEC-036, DEC-002). DEC numbers stay unique across both logs.

### [DEC-065] Go-Live of the DEC-063 Governance Layers: Model Armor (us-central1 Templates), Sensitive Data Protection and Cloud Trace On in Both Backends (`personal-finance-agent`)
- **Date**: `2026-11-02 09:07 UTC`
- **Scope**: `personal-finance-agent`
  - The deploy script and project `personal-finance-agent-507010`.
  - The owner approved go-live explicitly ("approve go-live").
- **Files Changed**: [`scripts/deploy_backend.sh`](file:///home/rkushwaha8610/personal-finance-agent/scripts/deploy_backend.sh): new `MODEL_ARMOR_REGION`, plus three variables on each service: `MODEL_ARMOR_TEMPLATE`, `SDP_ENABLED=1`, `ENABLE_CLOUD_TRACE=1`. They go in the script because `--set-env-vars` replaces every variable on each deploy.
- **Google Cloud changes (owner-approved)**:
  - Enabled `modelarmor.googleapis.com` and `dlp.googleapis.com`.
  - Created two Model Armor templates in `us-central1`, `pfa-guard-personal` and `pfa-guard-demo`:
    - prompt-injection & jailbreak filter at `MEDIUM_AND_ABOVE`;
    - malicious-URI filter;
    - responsible-AI filters (hate speech, harassment, dangerous, sexually explicit), each at `MEDIUM_AND_ABOVE`;
    - Model Armor's SDP basic config disabled (the app does its own reversible SDP redaction);
    - sanitize-operation logging off, so prompts and narrations never reach Cloud Logging.
  - IAM, 6 bindings at project level, no conditions: `roles/modelarmor.user`, `roles/dlp.user` and `roles/cloudtrace.agent` for `pfa-backend-personal` and `pfa-backend-demo`.
  - Deployed image `a3045ca-20261102-090118`, demo first: `finance-backend-demo-00015-kqt` (Seoul), then `finance-backend-00014-dkt` (Tokyo).
- **Decision & Rationale**:
  - **Templates in `us-central1`, not next to the services**:
    - On 2026-11-02 the Model Armor API answered `PERMISSION_DENIED` ("Read access to project … denied") for `asia-northeast1`, `asia-northeast3`, `asia-southeast1` and `europe-west4`. The same happened in `ats-resume-tailor-510019`, where the API had been on for weeks, so propagation wasn't the cause. Only `us-central1` answered.
    - Screening therefore adds a round trip across the Pacific. The app's 5 s timeout and regex fallback cover slow calls.
    - Prompts and narrations are screened in the US. Gemini already runs on the `global` endpoint.
  - **One template per environment**: separate labels, and each service account can later be limited to its own template with an IAM condition.
  - **`ENABLE_VERTEX_CONTEXT_CACHE` left unset**: the persona is under the 4,096-token minimum for explicit caching (measured in DEC-063).
- **Verification**:
  - **Model Armor**: a direct `sanitizeUserPrompt` call on `pfa-guard-demo` returned `NO_MATCH_FOUND` for a normal finance question and `MATCH_FOUND` (`pi_and_jailbreak`) for an injection attempt.
  - **DLP**: the five default infoTypes (India PAN and Aadhaar, email, phone, card) all exist among the 262 the API supports.
  - **IAM**: the six bindings are listed in the project policy.
  - **Each service**:
    - The deploy script's checks passed: `/health` 200, `/me` 401, `/transactions` 401.
    - The three variables are on the serving revision.
    - No application warnings after start-up; the only warnings were the smoke checks' 401s.
  - **Still to do**: an end-to-end check of a signed-in chat (Cloud Trace spans, `decision_layer`). It needs the owner's login.
- **Incident during go-live**:
  - Between about 08:41 and 09:00 UTC, Cloud Shell's built-in credential service returned account details without an email. gcloud lost its active account, and Python ADC failed with `RefreshError`.
  - The account was selected again with `gcloud config set account` (session config only). ADC recovered by itself at about 09:00.
  - Running agents paused paid calls during the outage.
- **Rollback**:
  - `gcloud run services update <service> --region <region> --remove-env-vars=MODEL_ARMOR_TEMPLATE,SDP_ENABLED,ENABLE_CLOUD_TRACE`, then revert this script change.
  - The app goes back to the regex layers immediately.
- **Lifetime**: these layers run until the free-trial credit ends (about 27 Nov 2026). The owner won't upgrade, so both services stop then.
- **Flow Impact**: [`flow.md`](file:///home/rkushwaha8610/flow.md): the `deploy_backend.sh` row now lists the three variables. No new file connections.

---

### [DEC-064] Credit-End Backup: Read-Only Export of Both Google Cloud Projects to Local Disk, With a Reusable Script for the Final Run (`workspace`)
- **Date**: `2026-11-02 08:45 UTC`
- **Scope**: `workspace`
  - Reads `ats-resume-tailor-510019` and `personal-finance-agent-507010`.
  - Writes only under `~/backups/credit-end-2026-11/`, outside both git repos.
  - No repo code, Google Cloud resource, API or IAM changed; no deploy.
- **Request**: the free-trial credit is expected to end around 27 Nov 2026 and the owner won't upgrade, so services will stop and their data may later be deleted. Back up now everything that would be lost, and leave a script for a final run around 24 Nov.
- **Files Changed** (created, outside both repos):
  - [`export_gcp_data.sh`](file:///home/rkushwaha8610/backups/credit-end-2026-11/export_gcp_data.sh): wrapper that runs the exporter with the resume tailor's venv (it has `google-cloud-firestore`).
  - [`export_gcp_data.py`](file:///home/rkushwaha8610/backups/credit-end-2026-11/export_gcp_data.py): the exporter.
  - The first snapshot `20261102T083712Z/` (with `MANIFEST.md`, `RESTORE.md`, `SHA256SUMS`) and its `.tar.gz` (12.7 MiB). Folders are 700, files 600.
- **Decision & Rationale**:
  - **Read-only by construction**:
    - gcloud calls go through an allow-list of list / describe / get-iam-policy commands.
    - The only download command is `storage rsync` from a bucket to a local folder, without delete flags.
    - Secret values can't be read.
  - **Firestore is read with the Python client**, not `gcloud firestore export`, which writes to a bucket (a change on Google Cloud).
  - **Everything is discovered at run time**: buckets, Cloud Run services and regions, Scheduler jobs in every location, secret names, Firestore databases, Artifact Registry. The final run picks up anything new.
  - **Restorable without Google Cloud**: `local-restore/` matches the resume tailor's local file-storage mode and the finance app's local statement layout, and `RESTORE.md` gives the steps.
  - **Comparable, repeatable runs**:
    - Firestore JSON is written with sorted keys, because Firestore returns fields in a different order on each read.
    - `MANIFEST.md` lists the files changed since the previous snapshot.
    - Each run writes a new folder; a lock file blocks concurrent runs, and free space is checked first.
  - **Rejected**:
    - A managed Firestore export (changes Google Cloud).
    - Downloading the container images: about 3.3 GB on a 4.8 GB disk, and they can be rebuilt.
    - Exporting secret values.
    - Dumping the Supabase databases: they're outside Google Cloud and survive. Optional `pg_dump` steps are in `RESTORE.md`.
- **Verification**:
  - Every bucket object matched Cloud Storage's size and MD5, `sha256sum -c` passed for every file, and the archive was re-hashed.
  - The tracker records validate against the resume tailor's `ApplicationRecord`.
  - Local mode was tested on a temporary copy: the tracker and its PDFs were served.
  - `--dry-run` was tested, and the resume-tailor repo is unchanged.
- **Follow-ups**:
  - Download the archive off Cloud Shell, whose home directory is deleted after long inactivity.
  - Keep the database and Gmail app passwords in a password manager; secret values aren't exported.
  - Re-run around 24 Nov.
- **Flow Impact**: added a "Credit-End Backup Tooling" section to [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-063] Real Google Cloud Governance Behind Flags: Model Armor API, Sensitive Data Protection, Cloud Trace Export and Explicit Context Caching (`personal-finance-agent`)
- **Date**: `2026-11-02 08:27 UTC`
- **Scope**: `personal-finance-agent` (security, telemetry and caching layers; with the four flags unset the behaviour is unchanged; no deploy, no API enabled, no IAM change)
- **Request ("Week 1 truth fixes", via the parent agent)**: the owner's resume names Model Armor, Cloud DLP, Cloud Trace and context caching, but the code only had local stand-ins. Make the four real, each behind a flag that defaults off.
- **What the code did before (checked)**:
  - `security/model_armor.py` was a regex screen; it called no Google API.
  - `security/dlp.py` was regex redaction with reversible tokens; it called no Google API.
  - `telemetry/tracer.py` used the OpenTelemetry SDK with an `InMemorySpanExporter` only (unbounded, read by nothing), and every span was its own trace.
  - `context_cache.py` created a Vertex `CachedContent` only with `ENABLE_VERTEX_CONTEXT_CACHE` (never set), and nothing passed it to a model; the "hits" were a local counter.
  - Also found: the README said the app used `google-genai`; generation actually uses the `vertexai` SDK (`google-cloud-aiplatform`), and `google-genai` is imported nowhere.
- **Files Changed**:
  - **Created:**
    - [`app/security/model_armor_api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/model_armor_api.py), [`app/security/sdp_api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/sdp_api.py), [`app/telemetry/usage.py`](file:///home/rkushwaha8610/personal-finance-agent/app/telemetry/usage.py)
    - [`tests/test_guardrails_unchanged.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_guardrails_unchanged.py) + [`tests/fixtures/guardrails_golden.json`](file:///home/rkushwaha8610/personal-finance-agent/tests/fixtures/guardrails_golden.json), [`tests/test_model_armor_api.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_model_armor_api.py), [`tests/test_sdp.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_sdp.py), [`tests/test_tracing.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_tracing.py), [`tests/test_context_cache.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_context_cache.py)
  - **Modified:**
    - [`app/security/model_armor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/model_armor.py), [`app/security/dlp.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/dlp.py), [`app/telemetry/tracer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/telemetry/tracer.py), [`app/telemetry/__init__.py`](file:///home/rkushwaha8610/personal-finance-agent/app/telemetry/__init__.py), [`app/context_cache.py`](file:///home/rkushwaha8610/personal-finance-agent/app/context_cache.py)
    - [`app/security/auth.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/auth.py), [`app/security/__init__.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/__init__.py) (docstrings only: the HMAC tokens are a local stand-in for SPIFFE / Workload Identity)
    - [`app/advisor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/advisor.py), [`app/main.py`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py), [`app/mcp/a2a_orchestrator.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py)
    - [`app/statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py), [`app/extractor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/extractor.py), [`app/ai_importer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py), [`app/voice_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py) (one span per Gemini call)
    - [`requirements.txt`](file:///home/rkushwaha8610/personal-finance-agent/requirements.txt), [`.env.example`](file:///home/rkushwaha8610/personal-finance-agent/.env.example), [`README.md`](file:///home/rkushwaha8610/personal-finance-agent/README.md), [`CHANGELOG.md`](file:///home/rkushwaha8610/personal-finance-agent/CHANGELOG.md), this log, [`flow.md`](file:///home/rkushwaha8610/flow.md)
- **Decision & Rationale**:
  - **Model Armor API (`MODEL_ARMOR_TEMPLATE` = full template name)**:
    - The regex screen still runs first, always. Only text it passes goes to the official `google-cloud-modelarmor` client on the template's regional endpoint, `modelarmor.{location}.rep.googleapis.com`: chat prompts (`screen_prompt`, advisor and copilot), document text (`inspect_prompt` / `inspect_prompts`: statement rows, receipts, the ingestion agent) and, new, the advisor's replies (`screen_model_response`, step 6b).
    - One attempt with a 5 s timeout (`MODEL_ARMOR_TIMEOUT_SECONDS`), no client retries (the default retry runs up to 60 s). Any error, or an invocation `FAILURE`, logs a warning and keeps the regex result, so Model Armor being down never fails a request.
    - Blocking depends on the use. Prompts and replies are blocked by any matched filter except `sdp`: sensitive data is tokenised by the SDP layer, and blocking on it would refuse ordinary questions about one's own accounts. Document rows are flagged only by `pi_and_jailbreak` and `malicious_uris` (what the regex document screen looks for), so a responsible-AI false positive can't wipe a ledger narration.
    - Statement rows go in batches (≤ 50 rows / 8,000 characters per request), and a flagged batch is split in halves until the matching rows are found. Per-row calls would have added tens of seconds to a 300-row import.
    - The deciding layer (`regex`, `model_armor_api`, `regex_fallback`) is recorded on the existing spans (`model_armor_screen_prompt`, new `model_armor_screen_response`, the ingestion `screen_rows` span), on the API call's own span, and in the ingestion agent's result as `guardrail_layers`. That key is added only when a Google API is configured.
  - **Sensitive Data Protection (`SDP_ENABLED=1`)**:
    - The regex layer runs first, unchanged. Its output goes to `dlp_v2.DlpServiceClient.inspect_content` (project `config.PROJECT_ID`, `SDP_LOCATION` default `global`, `SDP_MIN_LIKELIHOOD` default LIKELY, quotes not requested).
    - InfoTypes: built-in India PAN and Aadhaar, email, phone and credit card (configurable via `SDP_INFO_TYPES`, because the names can only be confirmed with `infoTypes.list` once the API is on), plus two custom regex types: `PFA_UPI_ID`, and `PFA_BANK_ACCOUNT_NUMBER` (9-18 digits, reported only with a hotword such as "A/c" nearby).
    - Each finding becomes the same kind of `[TYPE_n]` token in the same token map (numbering continues after the regex tokens), so `restore()` de-tokenises replies exactly as before. SDP never sees values the regex already tokenised.
    - Reference numbers: in document rows with `preserve_reference_numbers`, the bank-account type is not requested and any finding that touches a 12-digit RRN (`database._REFERENCE_NUMBER`, DEC-034) or a 9-12 digit reference token (`_extract_reference_id`) is dropped. SDP's Aadhaar detector matches 12-digit numbers, and reconciliation needs them; a bare 12-digit Aadhaar therefore stays as today (the regex doesn't mask it either). A test pins the regex to the database's.
    - Rows are inspected in one batched request; a truncated result is re-inspected in halves. Any API error keeps the regex result.
  - **Cloud Trace (`ENABLE_CLOUD_TRACE=1`)**:
    - A `BatchSpanProcessor` with the official `CloudTraceSpanExporter` is added next to the in-memory exporter, into `config.PROJECT_ID`. If it can't start, a warning is logged and traces stay in memory.
    - Spans are now linked. One root span per request (`advisor.chat`, `advisor.generate_insights`, `ingestion.process_file`, and `@traced_request` on the four-agent workflow, master resolve and copilot command); `with tracer.start_span(...)` and `tracer.activate()` make a span the parent of what runs inside. MCP tool calls, the Model Armor / SDP API calls and every Gemini call (`model_call_span`, with token usage) join the request's trace. The parallel statement-page calls run in a copy of the caller's context.
    - The root span is not added to the in-memory summaries, so `GET /telemetry/traces` and `find_weakest_step()` rank the same steps; the response gains `cloud_trace_export` only with the flag set.
    - The in-memory exporter is now bounded (last 2,048 spans). The SDK's version kept every span for the life of the process, and this change adds spans.
  - **Context caching that actually saves tokens (`ENABLE_VERTEX_CONTEXT_CACHE=1`)**:
    - Explicit caches are created with the same `vertexai` SDK the advisor generates with (`vertexai.caching.CachedContent.create`): one per model (tiers on one model share it), lazily, holding the static persona, recreated 2 minutes before expiry.
    - The advisor then builds the model with `GenerativeModel.from_cached_content` and sends no system instruction (the API rejects one next to `cached_content`). The per-turn context (ledger summary, compacted history, guidelines) goes into the user message.
    - Failed creation, or a failed request with the cache, switches explicit caching off for that model, and the request goes out exactly as before. The uncached prompts are byte-identical to the old ones (golden test).
    - `usage_metadata` from every Gemini reply is always recorded (`telemetry/usage.py`): on the model call's span and in totals that `PrefixContextCacheManager.get_stats()` returns (`vertex_usage`), including `cached_content_token_count`, which also counts implicit caching. The local registry's "hits" stay, documented as bookkeeping.
    - **Measured**: gemini-3.5-flash only caches 4,096+ tokens and the persona is 167, so with today's prompt explicit caching won't activate. Enabling the flag costs one rejected create per model per instance. The code is ready for a larger static prefix.
  - **Docstrings and docs say what is local and what calls Google**: the HMAC workload tokens are called a stand-in for SPIFFE; the regex layers are called local. The README's `google-genai` claim is corrected.
  - **Rejected**:
    - Model Armor's own SDP filter instead of the DLP API: no reversible tokens, and blocking on it refuses normal questions.
    - Caching the per-turn ledger summary: it would put personal data in cache resources and change every turn.
    - Migrating to `google-genai`: out of scope, although the `vertexai` generative-models SDK prints deprecation warnings.
    - `gcloud run services update --update-env-vars` for go-live: `deploy_backend.sh` uses `--set-env-vars`, which removes every other variable on the next deploy.
- **Verification**:
  - Backend: **248 passed, 15 skipped**: the 188 / 15 baseline plus 60 new tests. The golden test compares regex screens, redaction/restore and the exact advisor prompts with output captured from the old code, and fails if any Google client is created with the flags unset. The API tests use real `modelarmor_v1` / `dlp_v2` message types.
  - Dependencies: `google-cloud-modelarmor==0.7.4`, `google-cloud-dlp==3.40.0`, `opentelemetry-exporter-gcp-trace==1.15.0` (+ `google-cloud-trace==1.21.0`, `opentelemetry-resourcedetector-gcp==1.14.0`) were installed in `venv/` with the existing pins as constraints. A fresh-install resolution of `requirements-dev.txt` matches all 98 pins, and `pip check` is clean.
  - Real Vertex AI, synthetic text only (project `personal-finance-agent-507010`, `global`, gemini-3.5-flash):
    - The persona counted 167 tokens; creating a cache from it was rejected ("minimum token count to start explicit caching is 4096").
    - A synthetic 6,674-token persona was cached (5-minute TTL), and two advisor chat turns through it reported `cached_content_token_count` 6,675 of 6,805 prompt tokens each.
    - A third turn without the cache reported 0 cached tokens.
    - The cache was deleted, and a list showed none left. Cost well under ₹10.
  - Read-only checks:
    - `roles/modelarmor.user`, `roles/dlp.user` and `roles/cloudtrace.agent` exist with the expected permissions.
    - Both backend service accounts exist.
    - `modelarmor.asia-northeast1.rep.googleapis.com` and `modelarmor.asia-northeast3.rep.googleapis.com` resolve in DNS; an invented region doesn't.
- **Not verified**:
  - Model Armor and DLP calls against the live APIs (not enabled).
  - The SDP infoType names.
  - Model Armor's region list in the docs (the documentation tool returned Forbidden).
  - Cloud Trace export against the live API.
- **Go-live (needs the owner's approval; nothing was executed)**:
  - Enable `modelarmor.googleapis.com` and `dlp.googleapis.com`.
  - Create a `pfa-guard` template in `asia-northeast1` (personal) and `asia-northeast3` (demo).
  - Grant the three roles to `pfa-backend-personal` and `pfa-backend-demo`.
  - Add the variables to the `--set-env-vars` lists in `scripts/deploy_backend.sh`.
  - Deploy demo first, then personal. Rollback: remove the variables and redeploy; the code paths go back to the regex layers.
- **Deployment**: none.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md):
  - new nodes for `model_armor_api.py`, `sdp_api.py`, `telemetry/usage.py`, the Model Armor, Sensitive Data Protection and Cloud Trace services, and the explicit-cache link from `context_cache.py` to Vertex AI;
  - spans from the readers;
  - table rows for the new modules and tests;
  - DEC-063 notes on the `advisor.py`, `main.py`, statement reader, orchestrator, `context_cache.py` and `tracer.py` rows.

---

### [DEC-062] Public GitHub Release: Personal Data Moved Out of the Code, Clean Public History, Standard Repository Docs and CI (`personal-finance-agent`)
- **Date**: `2026-11-02 06:15 UTC`
- **Scope**: `personal-finance-agent` (repository publication; small behaviour-preserving code changes)
- **Request (owner)**: "commit my code to my GitHub" with the documentation a good developer includes. The owner chose: this project only, a **public** repository with personal data scrubbed and a clean history, **all rights reserved**, and the interview/resume notes kept on disk but out of git.
- **Files Changed**:
  - **Created:**
    - [`app/owner_profile.py`](file:///home/rkushwaha8610/personal-finance-agent/app/owner_profile.py)
    - [`tests/test_owner_profile.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_owner_profile.py)
    - [`docs/examples/owner_profile.example.json`](file:///home/rkushwaha8610/personal-finance-agent/docs/examples/owner_profile.example.json)
    - [`LICENSE`](file:///home/rkushwaha8610/personal-finance-agent/LICENSE), [`SECURITY.md`](file:///home/rkushwaha8610/personal-finance-agent/SECURITY.md), [`CONTRIBUTING.md`](file:///home/rkushwaha8610/personal-finance-agent/CONTRIBUTING.md), [`CODE_OF_CONDUCT.md`](file:///home/rkushwaha8610/personal-finance-agent/CODE_OF_CONDUCT.md), [`CHANGELOG.md`](file:///home/rkushwaha8610/personal-finance-agent/CHANGELOG.md), [`.env.example`](file:///home/rkushwaha8610/personal-finance-agent/.env.example), [`requirements-dev.txt`](file:///home/rkushwaha8610/personal-finance-agent/requirements-dev.txt)
    - [`.github/workflows/ci.yml`](file:///home/rkushwaha8610/personal-finance-agent/.github/workflows/ci.yml), [`.github/dependabot.yml`](file:///home/rkushwaha8610/personal-finance-agent/.github/dependabot.yml), [`.github/PULL_REQUEST_TEMPLATE.md`](file:///home/rkushwaha8610/personal-finance-agent/.github/PULL_REQUEST_TEMPLATE.md), [`.github/ISSUE_TEMPLATE/`](file:///home/rkushwaha8610/personal-finance-agent/.github/ISSUE_TEMPLATE/) (bug report, feature request, config)
  - **Modified:**
    - [`README.md`](file:///home/rkushwaha8610/personal-finance-agent/README.md) (rewritten), [`.gitignore`](file:///home/rkushwaha8610/personal-finance-agent/.gitignore), [`Dockerfile`](file:///home/rkushwaha8610/personal-finance-agent/Dockerfile)
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py), [`app/mcp/a2a_orchestrator.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py), [`app/security/auth.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/auth.py)
    - [`app/frontend/src/api/agents.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/agents.ts), [`app/frontend/src/shell/AgentRegistryScreen.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/AgentRegistryScreen.tsx), [`app/frontend/src/shell/MasterCopilotDock.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/MasterCopilotDock.tsx)
    - [`tests/test_reconcile.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_reconcile.py), [`tests/test_reconcile_db.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_reconcile_db.py), [`tests/test_dedupe.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_dedupe.py), [`tests/test_date_guard.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_date_guard.py), [`tests/test_statement_pages.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_statement_pages.py), [`tests/test_fde_integration.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_fde_integration.py)
    - [`docs/UX_ENTERPRISE_AUDIT.md`](file:///home/rkushwaha8610/personal-finance-agent/docs/UX_ENTERPRISE_AUDIT.md), this log, [`flow.md`](file:///home/rkushwaha8610/flow.md)
  - **Untracked (kept on disk, git-ignored):** `docs/INTERVIEW_PREPARATION_GUIDE.md`, `docs/RESUME_PROJECT_DOCUMENTATION.md`, `docs/assets/ux-audit/mockup_transactions_light.jpg`.
- **What the pre-publication audit found**:
  - **No live credential in any commit or in the working tree.** The flagged items were the Firebase web API key (public by design), a `[PASSWORD]` placeholder and a throwaway local test password. Phase 0 had read the Gmail and PDF passwords from environment variables, not literals.
  - **Personal data in code, tests and docs, including old commits:**
    - `database.py` hardcoded the owner's full Kotak and HDFC account numbers and 19 months of real balances (DEC-054 seed).
    - `a2a_orchestrator.py` hardcoded two own FD account numbers, the owner's name, nine family/friend names and personal UPI remarks (DEC-057), plus the real current balance in the master resolver's advisor step.
    - Tests used the owner's UPI mobile number, an FD account number, the home-branch IFSC and a real payee name.
    - `agents.ts` samples used real balances and a salary credit.
    - This log had account numbers, salary and income figures, the employer, balances and 15+ third-party names.
    - The UX audit quoted real totals, and the light-mode mockup reused real figures.
  - **Other problems for a public repository:**
    - A fixed default HMAC key for agent workload tokens was in the code.
    - `pytest` wasn't in any requirements file.
    - `npm run lint` failed with 2 errors.
    - A plain `pytest tests`, the command the README gave, ran `tests/test_fde_integration.py` against the **live personal database**. It refreshed the test learned rule `upi/rahul` and wrote `agent_audit_log` rows. This happened once more during this work's baseline run, as it had in earlier sessions. Master resolve ran with `auto_apply: false`, so no transaction changed.
- **Decision & Rationale**:
  - **Owner-only data moves to a git-ignored file that still ships to Cloud Run.** [`owner_profile.py`](file:///home/rkushwaha8610/personal-finance-agent/app/owner_profile.py) reads `app/private/owner_profile.json` (or `OWNER_PROFILE_PATH`).
    - `.gitignore` excludes `app/private/`. `.gcloudignore` and `.dockerignore` don't, so `scripts/deploy_backend.sh` keeps building it into the image from this machine. The Dockerfile makes the folder readable by `appuser`, so a local `chmod 600` can't silently switch the rules off.
    - Without the file (a fresh clone, CI), the resolver keeps its generic keywords and no balances are seeded.
    - The profile was **generated from the private commit's code**. Checks: the 19 balance rows are identical, and the generic keywords plus the profile lists reproduce the old tuples exactly. **981 resolver decisions** (old code vs new, every token × three types × three amounts) matched with **0 differences**.
    - The advisor step now reads the latest reconciled closing balance (`_latest_reconciled_balance`; in production that is the same 2026-10 figure the code had hardcoded). With no reconciled balances it falls back to 25,000, the 4-agent workflow's default. So the demo no longer computes alerts on the owner's real balance.
  - **Workload tokens** are signed with `AGENT_WIF_SIGNING_KEY` or a random per-process key. Tokens are issued and verified inside one request (checked at every `issue_workload_token`/`verify_workload_token` call site), so nothing breaks.
  - **Tests, samples and docs got synthetic values** (`9000000001`, `6000000002`, `KKBK0000001`, `SURESH KUMAR`, round amounts). This log's entries were redacted in place: `₹[redacted]`, "a family member", relative changes for the dedupe totals. The engineering content is unchanged.
  - **The live suite is opt-in** (`PFA_LIVE_TESTS=1`), so following the README can't touch production again.
  - **Clean public history.** The full private history (23 commits, including the pending DEC-054 to DEC-061 work, committed first as `1da4752`) is in a verified bundle: `~/backups/personal-finance-agent/2026-11-02-pre-public/personal-finance-agent-private-history.bundle` (folder 700, file 600; restore test passed). The public `main` starts from the scrubbed snapshot.
    - **Rejected: rewriting the old commits with `git filter-repo --replace-text`.** A single missed value in any old version of a 200 KB log would be public forever.
  - **Repository docs**:
    - a README rewrite (features, architecture diagram, stack, layout, setup, configuration, testing, deployment, security, docs index);
    - an all-rights-reserved LICENSE that names GitHub's view/fork grant;
    - SECURITY (private reporting, scope, protections);
    - CONTRIBUTING (issues welcome, PRs by agreement, the DEC/flow protocol, the no-personal-data rule);
    - a Code of Conduct (Contributor Covenant 2.1 by reference);
    - a CHANGELOG grouped by milestone and DEC;
    - `.env.example` with comments on their own lines, because `config.py`'s loader keeps inline comments in values;
    - issue and PR templates.
  - **CI**: GitHub Actions runs `pytest` (Python 3.12, `requirements-dev.txt`, no credentials) and the frontend `npm ci`, typecheck, lint and build (Node 24). Dependabot opens monthly grouped PRs. The two ESLint errors were fixed without changing behaviour: an `Object.fromEntries` filter instead of `delete`, and `for…of` over `Array.from(results)`.
  - **Left as is**:
    - `file:///` links in this log and `flow.md`. GitHub renders them as plain text, and relative links would break when the files are opened through the workspace symlinks.
    - The legacy Streamlit `app/frontend.py` and `app/config.toml`.
    - The `VIKASH` first-name heuristic in resolver tier 2: one first name, nothing that identifies anyone.
- **Verification**:
  - Backend: **188 passed, 15 skipped** locally (14 Postgres-only, plus the live suite). The baseline before this change was 183 passed, 1 failed (the known `test_fastapi_fde_endpoints`, already failing per DEC-060) and 14 skipped.
  - CI replay on a clean export of the index (`git checkout-index`), with no `.env`, no `app/private/` and no Google credentials (`env -i`, metadata server blocked): fresh `pip install -r requirements-dev.txt` and pytest; `npm ci` from the lockfile, then typecheck, lint and build all pass.
  - Pre-publication scan of every file to be published: no hits for any account number, the mobile number, the IFSC, payee names, balance or salary figures (in all number formats) or secret patterns.
- **Deployment**: no deploy in this change. The next `scripts/deploy_backend.sh` run from this machine includes `app/private/owner_profile.json` in the image, so production behaviour stays the same.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md):
  - new nodes and rows for `owner_profile.py` (from `database.py` and `a2a_orchestrator.py`), `test_owner_profile.py` and the GitHub CI/Dependabot workflow;
  - the opt-in live suite;
  - the per-process signing key.

---

### [DEC-061] Password-Protected PDFs Are Unlocked Before Gemini Sees Them; the Upload Screen Takes a PDF Password (`personal-finance-agent`)
- **Date**: `2026-11-01 06:40 UTC`
- **Scope**: `personal-finance-agent` (upload path, statement reader, Upload screen) and a data change approved by the owner
- **Files Changed**:
  - **Modified:**
    - [`app/exceptions.py`](file:///home/rkushwaha8610/personal-finance-agent/app/exceptions.py)
    - [`app/statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py)
    - [`app/main.py`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py)
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
    - [`app/frontend/src/api/upload.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/upload.ts)
    - [`app/frontend/src/components/upload.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/upload.js)
    - [`tests/test_statement_pages.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_statement_pages.py)
- **Problem (owner: "check why the latest upload failed")**:
  - `Acct Statement_XXXX_01112026_11.47.08.pdf` was uploaded twice, at 06:18:50 and 06:19:10 UTC. Both `POST /upload` returned 500 after 0.3 s.
  - The logs show `400 … gemini-3.5-flash:generateContent: Request contains an invalid argument` on the first call, before anything was read. The upload message said only "Extraction process failed".
- **Root cause**:
  - The statement needs a password to open. Gemini rejects such a PDF with exactly this 400.
  - A probe on synthetic one-page PDFs confirmed it:
    - a plain PDF works;
    - an encrypted PDF that opens without a password (owner restrictions only) works;
    - an encrypted PDF that needs a password to open returns `400 BadRequest` in a fraction of a second.
  - Only Bank Sync unlocks statements (the saved statement passwords, or Unlock & Sync). The Upload screen sent the file as it was, and had no way to take a password.
- **Decision & Rationale**:
  - **No locked PDF reaches Gemini**:
    - [`statement_agent.pdf_needs_password`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py) is true when `pypdf` can't open the file with an empty password. [`decrypt_pdf`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py) returns an unlocked copy using the first password that works.
    - If a locked file still reaches `StatementExtractor`, it raises `PasswordRequiredError` (a new `ExtractionError` subclass) with a plain message, before any AI call.
    - A PDF that opens without a password is now split page by page too. Before, any encrypted file was read whole, which brings back the DEC-060 output limit.
  - **Uploads are unlocked first** ([`main._unlock_for_reading`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py)):
    - It tries the password sent with the upload, then the statement passwords saved for Bank Sync (`pfa-statement-passwords` in Secret Manager, via `gmail_sync._load_statement_passwords`; personal backend only).
    - The unlocked copy replaces the temporary file. The SHA-256 is still taken from the file as uploaded, so uploading the same locked file again is recognised as already imported.
    - Passwords are used in memory only, never logged or stored.
    - Messages: "This PDF is password-protected … Enter its password in the "PDF password" box …", or "That password doesn't open this PDF …".
  - **`POST /upload` takes an optional `password` form field** ([`api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)) and answers `PasswordRequiredError` with **400** and that message, instead of a 500.
  - **Upload screen** ([`upload.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/upload.js), [`upload.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/upload.ts)):
    - A "PDF password" box appears when a PDF is selected; it is cleared when the file changes.
    - A 4xx error is shown at once, without the 6-second ledger polling meant for timeouts. A password error opens and focuses the box.
  - **Alternatives rejected**:
    - Only a clearer error: you still couldn't upload a locked statement.
    - Sending the password to Gemini: Gemini has no such option.
    - Storing typed passwords: they stay in the request only. Saved passwords remain a Bank Sync setting.
- **Data changes**:
  - At 05:46 UTC the owner ran **Unlock & Sync** on the April–October 2026 statement (#11), on DEC-060's backend.
    - 361 read in 61.5 s; **111 added, 250 already in the ledger**, 35 marked for review because their type differed. The ledger went from 1,218 to 1,329 rows.
  - At 06:28 UTC, after the owner said "yes" to re-importing the FY 2025-26 statement (#10), one transaction:
    - deleted its single `ingested_files` row (hash `885fd37ab06753a7…`, 122 rows, dated 2026-11-01 04:33);
    - marked statement #10 `IMPORT_FAILED` and unlocked, so Bank Sync shows **Retry import**.
    - Clicking Retry import on Cloud Run reads all 971 rows: the 122 already imported match their references, and the rest are added or matched to Excel rows.
- **Verification**:
  - `tests/test_statement_pages.py`: 5 new tests (35 in the file).
    - Locked and open-without-password PDFs are told apart, and the open-without-password one is still split.
    - `decrypt_pdf` uses the first password that works.
    - A locked PDF never reaches the model.
    - The upload unlock order: the typed password, then the saved ones.
    - `/upload` passes the password through and answers a locked file with 400.
  - Whole suite: **176 passed, 14 skipped**. `tsc -b` and ESLint pass.
- **Deployment (2026-11-01)**:
  - Image `finance-backend:2cb35f7-20261101-063241`: demo first (`finance-backend-demo-00014-dst`), then personal (**`finance-backend-00013-z9t`**, 100% of traffic). Smoke checks passed on both: `/health` 200, `/me` and `/transactions` 401.
  - Frontend: `firebase deploy --only hosting` (06:37 UTC). The live chunk contains the "PDF password" box.
  - Rollback to DEC-060's backend: `gcloud run services update-traffic finance-backend --project personal-finance-agent-507010 --region asia-northeast1 --to-revisions=finance-backend-00012-k97=100`.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md):
  - a new edge `main.py` → `gmail_sync.py` (saved statement passwords);
  - the rows for the Upload screen, `main.py` and `statement_agent.py`.

---

### [DEC-060] Long Bank Statements Are Read a Few Pages per Gemini Call; Failed Bank Sync Imports Can Be Retried (`personal-finance-agent`)
- **Date**: `2026-11-01 05:20 UTC`
- **Scope**: `personal-finance-agent` (backend statement import path, Bank Sync screen, upload timeouts)
- **Files Changed**:
  - **Created:**
    - [`tests/test_statement_pages.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_statement_pages.py)
  - **Modified:**
    - [`app/statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py)
    - [`app/main.py`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py)
    - [`app/gmail_sync.py`](file:///home/rkushwaha8610/personal-finance-agent/app/gmail_sync.py)
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
    - [`app/frontend/src/components/bankSync.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/bankSync.js)
    - [`app/frontend/src/api/bank.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/bank.ts)
    - [`app/frontend/src/api/upload.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/upload.ts)
    - [`app/frontend/src/api/config.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.ts)
    - [`tests/test_model_config.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_model_config.py)
    - [`tests/test_reconcile_db.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_reconcile_db.py)
- **Problem (owner's report)**: the Kotak statement for April–October 2026 came in through Bank Sync, but none of its transactions reached the ledger.
- **Root cause (Cloud Run logs, 2026-11-01)**:
  - `Kotak_Bank_2026-10_XXXXXXXXXX_01-04-2026_31-10-2026.pdf` (statement #11, 13 pages, **361 rows**) was sent to Gemini in **one call**. Both Unlock & Sync attempts (04:32 and 04:42 UTC, about 2 minutes each) failed with `Gemini returned invalid JSON: Unterminated string … (char 56259)` and `(char 87900)`: the reply was **cut off at the output limit**. The import rolled back, `POST /api/gmail/statements/11/unlock` returned 500, and **0 rows** were saved. The card kept showing "🔒 Password Protected", though the stored copy had already been unlocked.
  - A probe on 2 pages (71 rows) measured **7.3k answer tokens plus 7.4k thinking tokens**. Thinking counts against the reply budget, so the whole 361-row statement needs more than 64k in one reply.
  - The same limit had already hit the 12-month statement #10 (`…_01-04-2025_31-03-2026.pdf`, 30 pages, **971 rows**) **silently**: Gemini returned valid JSON with only the first **122 rows** (1 Apr–24 May 2025). The import reported success ("Imported (122 txns)"), and the file's hash is now in `ingested_files`, so a plain re-import says "already imported".
- **Decision & Rationale**:
  - **Read local PDFs a few pages per call** ([`StatementExtractor`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py)):
    - `pypdf` (already a dependency) splits the file into spans of `PAGES_PER_CALL = 2` pages.
    - Up to `PARALLEL_CALLS = 6` spans are read at once in a `ThreadPoolExecutor`, and the rows are joined in page order. Both settings can be overridden with `STATEMENT_PAGES_PER_CALL` and `STATEMENT_PARALLEL_CALLS`.
    - Every call sets `max_output_tokens=65535`.
    - The prompt is unchanged, with two additions when a call sees only some pages: which pages these are, and that leftover text at the top of a page continuing a row from the previous page is not a transaction.
    - Files of 1–2 pages, files that can't be split, and `gs://` URIs are read in one call, as before.
  - **No silent partial imports**:
    - A reply that stops at the limit (finish reason `MAX_TOKENS`) or isn't valid JSON is read again in halves, down to single pages. If a single page fails twice, the extraction raises `ExtractionError` and **nothing** is imported.
    - `count_row_lines` counts the text lines that start like a transaction row (an optional serial number, then a date). When a call returns more than `max(2, 5%)` fewer rows than that, its pages are read again one by one and the larger result is kept. If a gap remains, it goes into `coverage_notes`, which `process_file` shows as a "⚠️" upload message.
    - `ResourceExhausted`, `ServiceUnavailable`, `InternalServerError` and `DeadlineExceeded` are retried twice, after 2 s and 4 s.
  - **Bank Sync can retry, and the cards show the real status** ([`unlock_and_sync_statement`](file:///home/rkushwaha8610/personal-finance-agent/app/gmail_sync.py), [`bankSync.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/bankSync.js)):
    - When an import fails, the statement is marked `IMPORT_FAILED`, not encrypted (the stored copy is already unlocked). The password is optional in `UnlockStatementRequest`, so **Retry import** works with an empty one. A locked statement without a password gets a clear message.
    - A card says "✅ Imported" only when `sync_status = IMPORTED_AND_RECONCILED`. Before, any unlocked statement showed "✅ Imported (0 txns)", including a failed sync import (`EXPORTED_ONLY`).
    - [`process_file`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py) returns `summary` (`_batch_counts`). The Bank Sync message now says "read N transactions, X added to your ledger, Y were already in it". `transactions_imported` keeps the larger of the stored and new counts, so re-running an already imported file no longer resets it to 0.
  - **Client timeouts**: `IMPORT_TIMEOUT_MS` is 10 minutes for upload, Unlock & Sync and Gmail sync; Cloud Run allows 15. After a client timeout, the card polls for up to 2 minutes and stops on `IMPORT_FAILED`.
  - **The "already in your ledger (from …)" note no longer names the file being imported** ([`save_transactions_batch`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)):
    - It reported each recognised row's first asset. A row that had no file before (entered by hand, or written by an agent or script) got this import's asset in the same transaction, so the note credited the import itself.
    - The import preview below showed 44 of the 251 recognised rows as coming "from 'Kotak_Bank_2026-10_…_31-10-2026.pdf'".
    - The lookup now skips the current file (`gcs_uri IS DISTINCT FROM CAST(:current AS TEXT)`), so such rows count as "entered by hand".
  - **Alternatives rejected**:
    - Only raising `max_output_tokens`: the 361-row reply needs more than the 65,535-token maximum once thinking is counted, and the 971-row one far more.
    - A `response_schema`: it constrains the shape of the reply, not its length.
    - Prompting month by month with the whole PDF each time: every call pays for the full input, and rows near the boundaries get missed.
    - A background job with polling: Cloud Run throttles CPU outside requests, and one synchronous request now finishes well inside the limit.
- **Verification**:
  - `tests/test_statement_pages.py`: 30 new tests. The suite: **171 passed, 14 skipped** (the Postgres-only `test_reconcile_db.py` tests).
  - `test_reconcile_db.py`, run against a throwaway local `postgres:17` (the setup in its docstring): **13 passed, 1 failed**.
    - The new test (a row without a file isn't credited to the import) **passes with the fix and fails without it**.
    - The failure, `test_overlapping_monthly_and_quarterly_statements`, fails the same way without this change. It expects `'BMRCL METRO RIDE'`, but the description merging that was already in the working tree and deployed (`combine_excel_and_bank_description`) now produces `'BMRCL METRO RIDE UPI/BMRCL/…/metro ride'`.
  - `tsc -b`, ESLint and `vite build` pass.
  - Dry runs on the real statements (extraction only, no database writes):
    - April–October 2026: **361 of 361 rows** in 7 calls, **53 s**.
    - FY 2025-26: **971 of 971 rows** in 15 calls, **117 s**.
    - Per-month counts match the PDFs exactly, and so do dates and amounts.
  - **Import preview, nothing committed**: the full `process_file` pipeline on the April–October statement, with every database transaction rolled back (ledger still 1,218 rows).
    - Result: **361 read, 110 would be added, 251 already in the ledger**. Match rules: 170 same reference, 68 same wording, 8 labelled differently, 4 Excel + statement, 1 identical row.
    - **38** recognised rows came back with a different type and would be marked for review; rows the owner edited (`user_locked`) are skipped.
    - New rows by month: Apr 47, May 19, Jun 0, Jul 0, Aug 2, Sep 2, Oct 40.
    - A same-amount check (±3 days) found a ledger row near about 20 of the 110 new rows: possible duplicates of Excel or Gmail-alert rows in April, May and October that the rules didn't match.
  - `tests/test_fde_integration.py` was **not** part of the verification. It calls the live personal database and Gemini: `POST /agents/learned-rules` refreshes test rule `upi/rahul` (id 1; no transaction matches it) and writes `agent_audit_log` rows. Its `test_fastapi_fde_endpoints` already failed before this change: it expects the Jarvis reply to contain "Typical Monthly Salary".
- **Deployment (2026-11-01)**:
  - One image, `finance-backend:2cb35f7-20261101-053333`, built from the working tree. Like the previous deploy, it includes the uncommitted DEC-053 to DEC-058 changes; no other file had changed since revision `finance-backend-00010-6qq` (27 Oct).
  - Demo first: `finance-backend-demo-00013-2pw`. Then personal: **`finance-backend-00012-k97`**, 100% of traffic. Smoke checks passed on both: `/health` 200, `/me` and `/transactions` 401.
  - Revision `finance-backend-00011-rb8` served briefly in between: the first build of this fix, without the source-label change.
  - Frontend: `vite build`, then `firebase deploy --only hosting` (05:19 UTC). The live lazy chunk contains "Retry import".
  - Rollback to the version before this fix: `gcloud run services update-traffic finance-backend --project personal-finance-agent-507010 --region asia-northeast1 --to-revisions=finance-backend-00010-6qq=100`.
- **Data, left to the owner**:
  - The April–October statement (#11) is **not imported yet**. The ledger still has 1,218 rows, and #11 is still `PASSWORD_REQUIRED`; the stored copy is already unlocked. Unlock & Sync now imports it, with the preview's numbers above.
  - The FY 2025-26 statement (#10) is still missing **849 of its 971 rows** (25 May 2025 to 31 Mar 2026). Its hash is in `ingested_files`, so re-importing it takes two steps, both waiting for the owner's decision: remove that one `ingested_files` row, then retry the import.
    - Before deciding: those months already hold the Monthly budget Excel rows, and Excel totals that don't match a bank line one-for-one would then be counted twice.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md):
  - the `StatementExtractor` node;
  - the rows for `main.py`, `statement_agent.py`, `gmail_sync.py` and `upload.js`;
  - new rows for the Bank Sync Unlock & Sync / Retry import path and for `tests/test_statement_pages.py`.

---

### [DEC-058] Omnipresent "Jarvis" Master Orchestrator Voice + Text Copilot (`MasterCopilotDock`) & 1-Month vs 19-Month Salary/Summary Normalization (`personal-finance-agent`)
- **Date**: `2026-10-27 01:55 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - **Created:**
    - [`app/frontend/src/shell/MasterCopilotDock.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/MasterCopilotDock.tsx)
  - **Modified:**
    - [`app/mcp/ledger_server.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py)
    - [`app/mcp/client.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/client.py)
    - [`app/mcp/a2a_orchestrator.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py)
    - [`app/mcp/agent_registry.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/agent_registry.py)
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
    - [`app/secret_store.py`](file:///home/rkushwaha8610/personal-finance-agent/app/secret_store.py)
    - [`app/frontend/src/api/config.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.ts)
    - [`app/frontend/src/api/agents.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/agents.ts)
    - [`app/frontend/src/shell/TopBar.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/TopBar.tsx)
    - [`app/frontend/src/shell/AppShell.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/AppShell.tsx)
    - [`tests/test_fde_integration.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_fde_integration.py)
- **Decision & Rationale**:
  - **Fixed Multi-Year / 19-Month Cumulative vs Single-Month Salary Inflation in MCP Tool Calls (`ledger_server.py` & `client.py`)**:
    - Previously, when `get_financial_summary` or `project_savings_capacity` was called with `month=None` (`ALL`), it queried the `financial_summary` view across all 19 months (`2025-04` through `2026-10`) and returned the 19-month cumulative income sum (`₹[redacted]`) inside the `"monthly_income"` field, and `check_budget_health` compared 19 months of cumulative spend against a 1-month budget limit.
    - Updated [`LedgerMCPServer._tool_get_financial_summary()`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py) to exclude balance markers (`Previous Balance`, `Opening Balance`, `Closing Balance`) and `is_self_transfer = TRUE`, and when `month` is `None`/`ALL`, compute `active_months_count` (`19` months), `typical_monthly_salary`, true 1-month average `monthly_income`, 1-month average `monthly_expense`, `monthly_surplus`, `latest_complete_month` (`2026-09`), and per-category `monthly_avg_amount` alongside `cumulative_all_months_income`.
    - Updated [`_tool_check_budget_health()`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py), [`_tool_project_savings()`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py), and [`MCPClient.get_context_snapshot()`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/client.py) so all agents and Gemini prompts use 1-month figures for monthly salary, savings projections, and budget health checks while labeling 19-month cumulative sums explicitly.
  - **Built the Omnipresent "Jarvis" Master Orchestrator Voice + Text Copilot Across the Entire Web App (`MasterCopilotDock.tsx`, `TopBar.tsx`, `AppShell.tsx`, `POST /agents/orchestrator/command`)**:
    - Implemented [`AgenticCFOOrchestrator.handle_omnipresent_command()`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py), registered the `OMNIPRESENT_JARVIS_COMMAND` skill on `orchestrator` in [`agent_registry.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/agent_registry.py), and exposed `POST /agents/orchestrator/command` in [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py).
    - Mounted [`MasterCopilotDock.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/MasterCopilotDock.tsx) in [`AppShell.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/AppShell.tsx) (plus a quick-launch `"Jarvis AI"` button in [`TopBar.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/TopBar.tsx)) so the Master Orchestrator follows the user across all 8 screens (`transactions`, `analytics`, `budgets`, `forecast`, `upload`, `bank-sync`, `ai-cfo`, `agents`) with Web Speech API voice input, `speechSynthesis` spoken replies, context-aware walkthrough chips, direct transaction `#id` & payee rule updates, and automatic `bridge.refreshAll()` page synchronization whenever `ledger_mutated` is `true`.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md) to include `MasterCopilotDock.tsx`, `POST /agents/orchestrator/command`, and the `AppShell.tsx` / `TopBar.tsx` -> `MasterCopilotDock.tsx` -> `agents.ts` -> `api.py` connections.

---

### [DEC-057] Master Multi-Agent Category Resolution Engine (`master_resolve_transactions`) & Interactive Web App Invocation (`personal-finance-agent`)
- **Date**: `2026-10-27 01:15 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - **Modified:**
    - [`app/mcp/a2a_orchestrator.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py)
    - [`app/mcp/ledger_server.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py)
    - [`app/mcp/agent_registry.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/agent_registry.py)
    - [`app/security/auth.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/auth.py)
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
    - [`app/frontend/src/api/config.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.ts)
    - [`app/frontend/src/api/agents.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/agents.ts)
    - [`app/frontend/src/shell/AgentRegistryScreen.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/AgentRegistryScreen.tsx)
    - [`tests/test_fde_integration.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_fde_integration.py)
- **Decision & Rationale**:
  - **Built the Master Multi-Agent Category Resolution & Ledger Intelligence Engine (`AgenticCFOOrchestrator.master_resolve_transactions` & `apply_master_resolutions`)**:
    - United all 4 specialist agents (`ingestion-agent`, `reconciliation-agent`, `clarification-agent`, `advisor-agent`) under the Root Orchestrator (`orchestrator`) to inspect unverified (`requires_human_review = TRUE`), low-confidence (`< 85%`), or `Other`/`Uncategorized` transactions across the PostgreSQL ledger and produce evidence-backed category & `transaction_type` decisions:
      1. **`INTERNAL_BANK_SWEEP` (`reconciliation-agent`)**: Detects Kotak ActivMoney / HDFC Sweep-In FD movements (`Sweep Transfer From/To`, `FD PREMAT PROCEEDS`) -> `TRANSFER | Transfer`.
      2. **`SELF_TRANSFER_MATCH` & `SPLIT_TRANSFER_PAIR` (`reconciliation-agent`)**: Detects own-account transfers (`Rohit Kushwaha`, `To be back`) and links split verification transfers (`₹1.00 To check` + `₹14,999.00 To be back` = `₹15,000.00` FD sweep) -> `TRANSFER | Transfer`.
      3. **`REIMBURSEMENT_PAIR_MATCH` (`reconciliation-agent`)**: Correlates person-to-person UPI inflows against exact-amount expense outflows within ±5 days (e.g., `#1254 ₹288.48` inflow from a family member matching `#1249 ₹288.48` `PCD/8839/KPN FF` grocery expense; `#1260 ₹40.00` inflow from a friend matching `#1259 ₹40.00` `Blue book`) -> `TRANSFER | Transfer`.
      4. **`CASHBACK_PROMO_RULE` (`clarification-agent`)**: Identifies bank/UPI promotional cashback credits (`811 SUPER CASHBACK`, `CASHBACK EARNED`, `ONE97 COMMUNICA ... Promotional`) -> `INCOME | Bonus`.
      5. **`MERCHANT_REMARK_SEMANTICS` & `CROSS_ROW_VPA_MERCHANT_HISTORY` (`clarification-agent`)**: Parses merchant tokens (`KPN FARM FRESH`, `PCD/8839/KPN FF`, `NEW THEJUS HOME`), UPI remarks (`Chips`, `Sprite`, `Masala`, `Nadda`, `Printout`, `Blue book`), and correlates VPA/merchant history across rows (`UPI/39797933973@SBI` linked to `#1268 Sprite` -> `Food`; `UPI/SRI CHOWDESHWAR` linked to `#1258 Masala` -> `Food`).
      6. **`LEARNED_MEMORY_RULE` & `PEER_TRANSFER_INFLOW`**: Enforces `learned_rules` (with pre-fetched batch caching in [`DatabaseManager.match_learned_rule()`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)) and classifies peer/family UPI settlements (payee names now kept in the git-ignored owner profile, DEC-062) -> `TRANSFER | Transfer`.
  - **Added Official MCP Tool `resolve_and_update_transactions` & FastAPI Endpoints (`POST /agents/master-resolve`, `POST /agents/master-resolve/apply`)**:
    - Registered `resolve_and_update_transactions` in [`LedgerMCPServer`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py) and [`AgentIdentityManager`](file:///home/rkushwaha8610/personal-finance-agent/app/security/auth.py) (with HITL approval gate) to atomically update `transactions` (`category_id`, `transaction_type`, `is_self_transfer`, `requires_human_review = FALSE`, `ai_confidence_score = 100.0`, `user_locked = TRUE`) and teach normalized payee patterns in `learned_rules`.
  - **Made All 5 Agents, Skills, and MCP Tools Directly Invokable on the Frontend Web App (`/agents` in [`AgentRegistryScreen.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/AgentRegistryScreen.tsx))**:
    - Added the **Master Transaction Category Resolver & Ledger Intelligence Console** on `/agents` with **"Analyze Ledger (Dry Run)"**, **"Auto-Resolve & Update All"**, per-row editable category/type dropdowns, per-row **"Apply"** buttons, and **"Commit All & Teach Rules"**.
    - Made every skill badge on all 5 A2A Agent Cards clickable (`POST /a2a/agents/{agent_id}/tasks/send`) and added a **"Run Tool"** button to every tool in the Official MCP Server Tool Catalogue (`POST /mcp/execute`).
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md) to document `/agents/master-resolve`, `/agents/master-resolve/apply`, `resolve_and_update_transactions`, and the interactive `AgentRegistryScreen.tsx` -> `agents.ts` -> `api.py` connections.

---

### [DEC-055] May 2026 & Feb 2026 Excel-over-Bank Consolidation, Categorization & `extract_payee_pattern` Hardening (`personal-finance-agent`)
- **Date**: `2026-10-26 02:55 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - **Modified:**
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
    - [`app/voice_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py)
    - [`app/ai_importer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py)
    - [`tests/test_fde_integration.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_fde_integration.py)
- **Decision & Rationale**:
  - **Consolidated Duplicate Excel + Bank Statement Rows into ONE Row (`<Excel Description> [Kotak/HDFC Statement] <Bank Narration & Ref>`)**:
    - In `2026-05` (`Monthly budget May 2026.xlsx` uploaded on top of 120 backfilled Kotak & HDFC statement rows) and `2026-02` (`Monthly budget Feb 2026.xlsx`), treated the user's Excel sheet as the **final truth** for category, `transaction_type`, and personal net expense share:
      1. Updated all 24 `BANK+EXCEL_MATCH` rows in `2026-05` to prepend the Excel description (`HungerBox`, `Education Loan EMI`, `PG Rent May`, `Turf cricket`, `Travel to turf`, `snack`, `Bus`, etc.) onto the `[Kotak Statement]` / `[HDFC Statement]` narration and UPI/RRN reference number, and applied the Excel sheet's category and type.
      2. Consolidated all 34 `EXCEL_NEW` rows in `2026-05` (including multi-swipe daily totals such as `HungerBox` breakfast+lunch pairs, the `2026-05-01 ₹237.00` DMart/Avenue Food Plaza/Water split, and the `2026-05-24 ₹205.50` 8-item outing) and the 6 remaining duplicate pairs in `2026-02` with their corresponding bank rows into a single row each, re-pointing `assets` and archiving all 64 removed duplicate/template rows to `/home/rkushwaha8610/backups/personal-finance-agent/2026-10-26/may_2026_consolidated_removed.json`.
      3. Consolidated the 4 inter-account self-transfer pairs in `2026-05` (`#1382+#1444`, `#1385+#1448`, `#1394+#1460`, `#1398+#1463`) into 1 row each, categorized all cashbacks (`CASHBACK EARNED ONBF`, `UPI/NPCI BHIM`) as `INCOME | Bonus`, and categorized all friend bill-splits/reimbursements (15 friends; names redacted) as `TRANSFER | Transfer` (`is_self_transfer=True`) so `2026-05` total expenses match the Excel sheet with `0` `Other` rows remaining.
  - **Hardened `VoiceAgent.extract_payee_pattern()` & Future Excel-over-Bank Reconciliation**:
    - Updated [`VoiceAgent.extract_payee_pattern()`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py) to strip `[Kotak Statement]` / `[HDFC Statement]` / `[... Alert]` tags and extract embedded `UPI/...`, `PCD/...`, or `@` VPA handles cleanly, purged polluted `[kotak statement] ...` rules from `learned_rules`, and upserted 29 clean payee rules.
    - Updated [`find_reconcilable_transaction()`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py), [`combine_excel_and_bank_description()`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py), and [`save_transactions_batch()`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py) so future Excel uploads automatically merge descriptions with bank statement rows and enforce Excel categories/types, and updated [`AISpreadsheetImporter.import_sheet_verbatim()`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py) to filter out `₹0.00` template placeholder rows.
- **Flow Impact**: Existing module and endpoint connections in [`flow.md`](file:///home/rkushwaha8610/flow.md) remain unchanged.

---

### [DEC-054] 1-Year & 19-Month HDFC + Kotak Bank Statement Reconciliation (`monthly_bank_balances`, Missing Months & Salary Backfill) (`personal-finance-agent`)
- **Date**: `2026-10-25 17:18 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - **Modified:**
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
    - [`app/frontend/src/api/transactions.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/transactions.ts)
    - [`app/frontend/src/components/ledger.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js)
    - [`app/frontend/src/components/analytics.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/analytics.js)
- **Decision & Rationale**:
  - **Cross-Checked 1-Year Kotak & HDFC Statements Against Ledger**:
    - Parsed the 1-year Kotak statement (`01 Apr 2025 – 31 Mar 2026`, 971 rows in [`Kotak_01-04-2025_31-03-2026.pdf`](file:///home/rkushwaha8610/Kotak_01-04-2025_31-03-2026.pdf)), the monthly Kotak PDFs (`Apr 2026 – Sep 2026`, decrypted with `rohi0709`), the HDFC SmartStatements (`01 Apr 2026 – 24 Oct 2026`, decrypted with `ROHI0709`), and all 625 HDFC transaction & balance alert emails (`15 May 2025 – 31 Mar 2026`).
    - Reconciled the **Combined Total Balance (`Kotak SA + ActivMoney FD + HDFC SA + Sweep-In FD`)** across all 19 months (`2025-04` through `2026-10`), aligning the `31 Jul / 01 Aug 2025` midnight FD sweep transfers by `Value Date: 01-08-2025` so every single month's `closing_balance` equals the next month's `opening_balance` (from `01 Apr 2025` through `31 Mar 2026` to `24 Oct 2026`; figures redacted) with zero artificial drops during internal FD sweeps.
  - **Backfilled Missing Months & Missing Salary Credits**:
    - Preserved the user's curated Excel rows for existing months (`2025-05` to `2026-01`, `2026-03`, `2026-04`), backfilled the **3 missing months** (`2025-04`: 40 Kotak rows; `2026-02`: 75 Kotak + 8 HDFC rows; `2026-05`: 67 Kotak + 53 HDFC rows, deduplicating inter-account transfers by 12-digit RRN), and backfilled the **5 missing salary credits** (`2025-09-30`, `2025-10-31`, `2025-11-28`, `2026-01-30`, `2026-06-30`; amounts redacted), totaling 248 inserted rows.
  - **Persisted Authoritative `monthly_bank_balances` Table & Per-Bank Breakdown in UI**:
    - Added `monthly_bank_balances` in [`DatabaseManager._verify_schema()`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py) and `DatabaseManager.get_reconciled_monthly_balances()`, wired into `GET /transactions/months` and `GET /analytics/summary` in [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py), and displayed the per-bank split (`Kotak ₹... • HDFC ₹...`) in [`ledger.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js) and [`analytics.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/analytics.js).
- **Flow Impact**: Existing API endpoints (`GET /transactions/months`, `GET /analytics/summary`) and component connections in [`flow.md`](file:///home/rkushwaha8610/flow.md) remain unchanged.

---

### [DEC-053] Start of Month (Opening) Balance, End of Month (Closing) Balance, and `Previous Balance` Reclassification (`personal-finance-agent`)
- **Date**: `2026-10-23 07:14 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - **Modified:**
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
    - [`app/frontend/src/api/transactions.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/transactions.ts)
    - [`app/frontend/src/components/ledger.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js)
    - [`app/frontend/src/components/analytics.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/analytics.js)
- **Decision & Rationale**:
  - **Reclassified `Previous Balance` / `Prevous Balance` Out of `INCOME` and Bound to `Start of Month Balance` (`opening_balance`)**:
    - In the uploaded historical Excel sheets (`2025-05` through `2026-04`), each sheet's initial `Prevous Balance` / `Previous Balance` row (one per sheet; amounts redacted) had been extracted from the right-hand table as `INCOME`.
    - Updated those database rows to `transaction_type = 'TRANSFER'` (`is_self_transfer = TRUE`, `user_locked = TRUE`) and added deterministic enforcement in [`DatabaseManager.save_transactions_batch()`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py) so `Previous Balance` / `Opening Balance` / `Initial Balance` / `Month end balance` rows are never counted as `INCOME` or `EXPENSE`.
    - Updated `GET /transactions/months` and `GET /analytics/summary` in [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py) as well as [`LedgerComponent.updateStats()`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js) so that when a month has an explicit `Previous Balance` / `Opening Balance` entry, that exact amount is used as the month's **Start of Month Balance (`opening_balance`)**, and months without an explicit entry automatically carry forward the prior month's **End of Month Balance (`closing_balance = opening_balance + net`)**.
  - **Placed Start & End of Month Balance Boxes Across All Logical Statement & Summary Views**:
    1. **Monthly Statement Ledger KPI Ribbon (`#ledger-stats` in [`ledger.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js))**: Expanded the 4-box ribbon (`Transactions`, `Total Inflow`, `Total Outflow`, `Net Balance`) into a 6-box ribbon adding **Start of Month Balance (`#stat-opening-balance`)** and **End of Month Balance (`#stat-closing-balance`)**.
    2. **Year → Month Folder Cards (`Stage 2` & `Stage 0` Home Shortcut in [`ledger.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js))**: Added dedicated **Start of Month Balance** and **End of Month Balance** tiles inside each Month Folder card.
    3. **Executive Financial Summary & Analytics (`/summary` in [`analytics.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/analytics.js))**: Added **Start of Month Balance** and **End of Month Balance** KPI cards to the Executive KPI Strip and `Start -> End` balance trajectories in the Monthly Trends panel.
- **Flow Impact**: Existing API endpoints (`GET /transactions/months`, `GET /analytics/summary`) and component connections in [`flow.md`](file:///home/rkushwaha8610/flow.md) remain unchanged.

---

### [DEC-052] Web App `Agent Registry` React UI (`/agents`), Cloud Run & Firebase Hosting Deployment, and Full Documentation (`personal-finance-agent`)
- **Date**: `2026-10-23 06:18 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - **New:**
    - [`app/frontend/src/api/agents.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/agents.ts)
    - [`app/frontend/src/shell/AgentRegistryScreen.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/AgentRegistryScreen.tsx)
  - **Modified:**
    - [`app/frontend/src/api/config.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.ts)
    - [`app/frontend/src/api/index.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/index.ts)
    - [`app/frontend/src/shell/screens.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/screens.ts)
    - [`app/frontend/src/shell/AppShell.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/AppShell.tsx)
    - [`app/frontend/vite.config.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/vite.config.ts)
    - [`app/frontend/tests/e2e/helpers.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/tests/e2e/helpers.ts)
    - [`app/frontend/tests/e2e/mock-backend.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/tests/e2e/mock-backend.ts)
    - [`app/frontend/tests/e2e/shell.integration.spec.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/tests/e2e/shell.integration.spec.ts)
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
    - [`README.md`](file:///home/rkushwaha8610/personal-finance-agent/README.md)
    - [`docs/FDE_ENTERPRISE_ARCHITECTURE.md`](file:///home/rkushwaha8610/personal-finance-agent/docs/FDE_ENTERPRISE_ARCHITECTURE.md)
    - [`app/frontend/FRONTEND_GUIDE.md`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/FRONTEND_GUIDE.md)
- **Decision & Rationale**:
  - **Native React `Agent Registry` Screen (`/agents`)**:
    - Built [`AgentRegistryScreen.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/AgentRegistryScreen.tsx) and typed API module [`agents.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/agents.ts) inside the React 19 shell so signed-in users (both Personal and Demo) can visually inspect and interact with:
      1. All **5 A2A Agent Cards** (`orchestrator`, `ingestion-agent`, `reconciliation-agent`, `clarification-agent`, `advisor-agent`) with their live Google Cloud Agent Registry URNs (`urn:agent:projects-328452160072:...`), SPIFFE Workload IDs (`spiffe://finance-cfo.local/ns/prod/sa/...`), and registered skills.
      2. **4-Stage Closed-Loop Categorization Memory (`learned_rules`)**: Displays learned payee patterns (`UPI/RAHUL -> Rent`, `ZERODHA BROKING -> SIP`, etc.), hit counts, and an interactive form to teach new payee rules (`POST /agents/learned-rules`) or delete rules (`DELETE /agents/learned-rules`).
      3. **Official MCP Tool Catalogue & SPIFFE Governance Audit Trail**: Lists all 11 tools on `LedgerMCPServer`, OpenTelemetry weakest-step latency diagnostics (`/telemetry/traces`), and immutable A2A/SPIFFE audit logs (`/agents/audit-trail`), plus a live **Verify 4-Agent Pipeline** button (`POST /agents/workflow`).
  - **Production Deployment & Documentation**:
    - Deployed the updated container image to both Cloud Run backends (`finance-backend` in `asia-northeast1` and `finance-backend-demo` in `asia-northeast3`) via [`scripts/deploy_backend.sh`](file:///home/rkushwaha8610/personal-finance-agent/scripts/deploy_backend.sh) and deployed the new React SPA build to Firebase Hosting (`https://personal-finance-agent-507010.web.app`).
    - Made `decision.md` and `flow.md` real tracked files inside `personal-finance-agent/` (with `/home/rkushwaha8610/decision.md` and `/home/rkushwaha8610/flow.md` symlinked to them) so GitHub renders the full architectural decision history and code flow diagrams natively.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md) to include [`AgentRegistryScreen.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/shell/AgentRegistryScreen.tsx) and [`src/api/agents.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/agents.ts).

---

### [DEC-051] 4-Stage Closed-Loop Transaction Categorization Learning Flywheel (`personal-finance-agent`)
- **Date**: `2026-10-22 21:46 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - **Modified:**
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
    - [`app/statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py)
    - [`app/extractor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/extractor.py)
    - [`app/ai_importer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py)
    - [`app/secret_store.py`](file:///home/rkushwaha8610/personal-finance-agent/app/secret_store.py)
    - [`tests/test_fde_integration.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_fde_integration.py)
- **Decision & Rationale**:
  - Closed every loop in the transaction categorization pipeline so that **every time a transaction is clarified or edited by the user**, the categorization/clarification agents learn from it and apply that knowledge to both existing unverified ledger rows and all future imports:
    1. **Stage 1 — Learn from Every Clarification & Manual UI Edit (`learned_rules`)**:
       - Both `POST /transactions/{tx_id}/resolve` (voice/text HITL modal), `PUT /transactions/{transaction_id}` (manual table edit in [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)), and `POST /agents/learned-rules` (`ClarificationSpecialistAgent.handle_task("learn_user_rule", ...)`) extract a normalized, reference-stripped payee pattern via [`VoiceAgent.extract_payee_pattern()`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py) (e.g., `"UPI/RAHUL/778192837182/Monthly"` → `"UPI/RAHUL"`) and upsert it into the PostgreSQL `learned_rules` table (`DatabaseManager.save_learned_rule()`).
    2. **Stage 2 — Retroactive Ledger Propagation**:
       - Enhanced [`DatabaseManager.save_learned_rule()`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py) so that whenever a payee rule is learned or updated, it immediately updates any existing **unlocked** (`NOT COALESCE(user_locked, FALSE)`) rows in `transactions` matching `LOWER(description) LIKE :pat_like` that are currently flagged (`requires_human_review = TRUE`) or assigned to a generic category (`Other`/`Uncategorized`), setting their category, `requires_human_review = FALSE`, and `ai_confidence_score >= 99.0`.
    3. **Stage 3 — Few-Shot Memory Injection into Gemini Extraction Prompts**:
       - Added [`DatabaseManager.format_learned_rules_prompt()`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py) and injected it into the Gemini multimodal extraction prompts in [`StatementExtractor.extract()`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py), [`ReceiptExtractor.extract()`](file:///home/rkushwaha8610/personal-finance-agent/app/extractor.py), and [`AISpreadsheetImporter.import_sheet_verbatim()`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py) so Gemini sees the user's verified `Pattern -> Category / Type` rules during initial extraction.
    4. **Stage 4 — Deterministic Enforcement at Ingestion & Batch Save**:
       - Enforced [`DatabaseManager.match_learned_rule()`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py) inside [`DatabaseManager.save_transactions_batch()`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py) (in addition to `ClarificationSpecialistAgent` and `app/main.py`) so every ingestion channel (PDF statements, receipt photos, Excel sheets, and Gmail bank alerts) deterministically applies learned rules (`confidence = 99.0`, `requires_human_review = False`) and increments `learned_rules.hit_count`.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md) to document the `format_learned_rules_prompt()` and `match_learned_rule()` connections across `statement_agent.py`, `extractor.py`, `ai_importer.py`, `api.py`, and `database.py`.

---

### [DEC-050] Google Cloud Agent Platform (`aiplatform.googleapis.com`), Agent Registry (`agentregistry.googleapis.com`), Agent Identity (`agentidentity.googleapis.com`), and A2A Agent Card Discovery & Invocation (`personal-finance-agent`)
- **Date**: `2026-10-22 21:14 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - **New:**
    - [`app/mcp/agent_registry.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/agent_registry.py)
    - [`scripts/register_gcp_agents.py`](file:///home/rkushwaha8610/personal-finance-agent/scripts/register_gcp_agents.py)
  - **Modified:**
    - [`app/mcp/a2a_orchestrator.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py)
    - [`app/mcp/__init__.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/__init__.py)
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
    - [`tests/test_fde_integration.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_fde_integration.py)
- **Decision & Rationale**:
  - **Google Cloud Agent Registry (`agentregistry.googleapis.com`) & Agent Identity (`agentidentity.googleapis.com`) Provisioning**:
    - Enabled `agentregistry.googleapis.com`, `agentidentity.googleapis.com`, and `cloudapiregistry.googleapis.com` alongside `aiplatform.googleapis.com` (*"Agent Platform API: One platform to build, deploy, scale and optimize sophisticated agents and models"*) in project `personal-finance-agent-507010` (`328452160072`).
    - Provisioned all **5 A2A Agents** (`A2A_AGENT_CARD` spec, `protocolVersion="0.3.0"`, `preferredTransport="HTTP+JSON"`), **1 Official MCP Server** (`TOOL_SPEC` with all 11 tools), **1 Agent Identity AuthProvider** (`projects/personal-finance-agent-507010/locations/global/authProviders/pfa-spiffe-wif-auth`), and **9 A2A/MCP Bindings** in Google Cloud Agent Registry (`locations/global`):
      1. **Orchestrator (`pfa-orchestrator`)** → `urn:agent:projects-328452160072:projects:328452160072:locations:global:agentregistry:services:pfa-orchestrator` (`projects/personal-finance-agent-507010/locations/global/agents/agentregistry-00000000-0000-0000-f3a6-55ca6e3a659e`)
      2. **Ingestion Specialist Agent (`pfa-ingestion-agent`)** → `urn:agent:projects-328452160072:projects:328452160072:locations:global:agentregistry:services:pfa-ingestion-agent` (`projects/personal-finance-agent-507010/locations/global/agents/agentregistry-00000000-0000-0000-0574-b585fc943f60`)
      3. **Reconciliation Specialist Agent (`pfa-reconciliation-agent`)** → `urn:agent:projects-328452160072:projects:328452160072:locations:global:agentregistry:services:pfa-reconciliation-agent` (`projects/personal-finance-agent-507010/locations/global/agents/agentregistry-00000000-0000-0000-a37c-90b82aae55c5`)
      4. **Clarification & Memory Specialist Agent (`pfa-clarification-agent`)** → `urn:agent:projects-328452160072:projects:328452160072:locations:global:agentregistry:services:pfa-clarification-agent` (`projects/personal-finance-agent-507010/locations/global/agents/agentregistry-00000000-0000-0000-3d1c-8510086f68e3`)
      5. **Strategic CFO Advisor Agent (`pfa-advisor-agent`)** → `urn:agent:projects-328452160072:projects:328452160072:locations:global:agentregistry:services:pfa-advisor-agent` (`projects/personal-finance-agent-507010/locations/global/agents/agentregistry-00000000-0000-0000-c99c-fc09c66c58f4`)
      6. **Personal Finance Ledger MCP Server (`pfa-ledger-mcp-server`)** → `urn:mcp:projects-328452160072:projects:328452160072:locations:global:agentregistry:services:pfa-ledger-mcp-server` (`projects/personal-finance-agent-507010/locations/global/mcpServers/agentregistry-00000000-0000-0000-d197-083385f3b504`)
  - **Runtime Agent Card Discovery & Card-Based Invocation**:
    - Implemented [`AgentRegistry`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/agent_registry.py) and [`AgentCard`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/agent_registry.py) (persisted to the new `agent_registry` table in [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py) and synced with `agentregistry.googleapis.com` via `sync_to_gcp_agent_registry()` and `fetch_from_gcp_agent_registry()`).
    - Wired [`A2AOrchestrator.delegate_task()`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py) and [`AgenticCFOOrchestrator.run_four_agent_workflow()`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py) so every A2A delegation resolves the target agent via its registered `AgentCard` (`invoke_via_agent_card`), validates the requested `skill` against `card.skills`, verifies the SPIFFE workload token against `card.authentication`, dispatches to the endpoint bound to `card.url`, and records `agent_cards_invoked` (including `gcp_agent_urn` and `gcp_service_name`).
    - Exposed `GET /.well-known/agent.json`, `GET /a2a/agents/{agent_id}/.well-known/agent.json`, `GET /a2a/registry`, `POST /a2a/registry/register`, `POST /a2a/registry/sync-gcp`, and `POST /a2a/agents/{agent_id}/tasks/send` in [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py).
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md) to include [`app/mcp/agent_registry.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/agent_registry.py), [`scripts/register_gcp_agents.py`](file:///home/rkushwaha8610/personal-finance-agent/scripts/register_gcp_agents.py), and the Google Cloud Agent Registry (`agentregistry.googleapis.com`) connection.

---

### [DEC-049] End-to-End 4-Agent CFO Workflow, Official MCP SDK Protocol (`mcp` + `mcp-types`), and All 4 FDE Bootcamp Pillars Implemented (`personal-finance-agent`)
- **Date**: `2026-10-22 20:22 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - **New:**
    - [`app/security/auth.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/auth.py)
    - [`app/context_cache.py`](file:///home/rkushwaha8610/personal-finance-agent/app/context_cache.py)
    - [`app/eval/golden_dataset.json`](file:///home/rkushwaha8610/personal-finance-agent/app/eval/golden_dataset.json)
  - **Modified:**
    - [`app/mcp/ledger_server.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py)
    - [`app/mcp/client.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/client.py)
    - [`app/mcp/a2a_orchestrator.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py)
    - [`app/mcp/__init__.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/__init__.py)
    - [`app/security/model_armor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/model_armor.py)
    - [`app/security/dlp.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/dlp.py)
    - [`app/security/__init__.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/__init__.py)
    - [`app/telemetry/tracer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/telemetry/tracer.py)
    - [`app/telemetry/__init__.py`](file:///home/rkushwaha8610/personal-finance-agent/app/telemetry/__init__.py)
    - [`app/eval/autorater.py`](file:///home/rkushwaha8610/personal-finance-agent/app/eval/autorater.py)
    - [`app/eval/__init__.py`](file:///home/rkushwaha8610/personal-finance-agent/app/eval/__init__.py)
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
    - [`app/advisor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/advisor.py)
    - [`app/voice_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py)
    - [`app/main.py`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py)
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
    - [`requirements.txt`](file:///home/rkushwaha8610/personal-finance-agent/requirements.txt)
    - [`tests/test_fde_integration.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_fde_integration.py)
- **Decision & Rationale**:
  - **Pillar 1 — BUILD (4-Agent CFO Workflow + Official MCP SDK + A2A Delegation)**:
    - Implemented the exact **Orchestrator + 4 Specialist Agents** architecture from Slide 2 of the FDE Pitch Deck ([`AgenticCFOOrchestrator`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py) & [`A2AOrchestrator`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py)):
      1. **Ingestion Agent (`ingestion-agent`)**: Screens document rows with `ModelArmor.screen_transaction_batch()` and `FinancialDLPPipeline.redact_transaction_descriptions()` (preserving 12-digit UPI/IMPS reference numbers for reconciliation while masking PII).
      2. **Reconciliation Agent (`reconciliation-agent`)**: Detects self-transfers between own accounts, reconciles rows against Gmail alerts, deduplicates within batches, and verifies opening/closing balance math via the MCP `calculate_balance_reconciliation` calculator tool.
      3. **Clarification Agent (`clarification-agent`)**: Applies learned user rules from the new `learned_rules` table (`DatabaseManager.match_learned_rule()`), flags remaining low-confidence (`< 85%`) rows with spoken/text questions, and learns new persistent payee rules (`extract_payee_pattern()`, e.g., `"UPI/RAHUL/7781..."` → `"UPI/RAHUL"` → `"Rent"`) whenever the user resolves a flagged transaction (`POST /transactions/{tx_id}/resolve` and `POST /agents/learned-rules`).
      4. **Advisor Agent (`advisor-agent`)**: Evaluates upcoming commitments via the MCP `calculate_emi_and_shortfall` and `check_budget_health` tools to raise proactive cash/EMI shortfall alerts before due dates, and answers financial questions with MCP context.
    - Upgraded [`LedgerMCPServer`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py) and [`MCPClient`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/client.py) to use the official `mcp` (`mcp==2.3.0`, `MCPServer`) and `mcp-types` (`mcp-types==2.3.0`) SDKs with full JSON-RPC 2.0 protocol support (`initialize`, `ping`, `tools/list`, `tools/call` at `POST /mcp/jsonrpc`) and 11 tools across all 4 tool domains (`ledger_db`, `document_store`, `bank_connectors`, `calculators`). Bridged `app/mcp/__init__.py` `__path__` with `site-packages/mcp` so `app/` at `sys.path[0]` coexists cleanly with the official PyPI `mcp` package.
  - **Pillar 2 — GOVERN (Model Armor, Cloud DLP, SPIFFE Identity & Least-Privilege Gateway, HITL Gates)**:
    - Created [`AgentIdentityManager`](file:///home/rkushwaha8610/personal-finance-agent/app/security/auth.py) assigning SPIFFE IDs (`spiffe://finance-agent.internal/sa/{orchestrator,ingestion-agent,reconciliation-agent,clarification-agent,advisor-agent}`), HMAC workload tokens, per-agent MCP tool allowlists (`AgentPermissionError`), and Human-in-the-Loop approval gates (`HumanApprovalRequiredError` when `save_learned_rule` is called without `human_approved=True`), backed by the `agent_audit_log` table.
    - Extended `ModelArmor` and `FinancialDLPPipeline` into the document ingestion pipeline ([`app/main.py`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py)) in addition to chat.
  - **Pillar 3 — SCALE (Golden Dataset Autorater + OpenTelemetry SDK Tracing & Weakest-Step Analysis)**:
    - Integrated the official `opentelemetry-api` and `opentelemetry-sdk` packages (`TracerProvider`, `SimpleSpanProcessor`, `InMemorySpanExporter`) in [`app/telemetry/tracer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/telemetry/tracer.py) with `find_weakest_step()` (`GET /telemetry/traces`) to rank the highest-error / highest-latency step across agent trajectories.
    - Created [`app/eval/golden_dataset.json`](file:///home/rkushwaha8610/personal-finance-agent/app/eval/golden_dataset.json) and wired [`app/eval/autorater.py`](file:///home/rkushwaha8610/personal-finance-agent/app/eval/autorater.py) to load it and include OpenTelemetry bottleneck analysis.
  - **Pillar 4 — OPTIMIZE (Prefix Context Caching + Dynamic Model Tiering + Sliding-Window Compaction)**:
    - Created [`PrefixContextCacheManager`](file:///home/rkushwaha8610/personal-finance-agent/app/context_cache.py) and wired it into [`FinancialAdvisor`](file:///home/rkushwaha8610/personal-finance-agent/app/advisor.py) to cache static fiduciary instructions, tool schemas, and ledger snapshots across turns.
- **Flow Impact**: Updated the Mermaid architecture graph and file-to-file connection table in [`flow.md`](file:///home/rkushwaha8610/flow.md) to include `app/security/auth.py`, `app/context_cache.py`, `app/eval/golden_dataset.json`, and the 4-Agent / MCP / Telemetry flows.

---

### [DEC-048] Workspace `CLAUDE.md` Added Alongside `AGENTS.md` to Share `agent.md` Model-Routing & Decision/Flow Protocol Across Both Runtimes (`workspace`)
- **Date**: `2026-10-22 19:04 UTC`
- **Scope**: `workspace`
- **Files Changed**:
  - **New:** [`CLAUDE.md`](file:///home/rkushwaha8610/CLAUDE.md)
  - **Modified:** [`agent.md`](file:///home/rkushwaha8610/agent.md), [`decision.md`](file:///home/rkushwaha8610/decision.md), [`flow.md`](file:///home/rkushwaha8610/flow.md)
- **Decision & Rationale**:
  - Created [`/home/rkushwaha8610/CLAUDE.md`](file:///home/rkushwaha8610/CLAUDE.md) so the **Claude Code CLI** automatically loads the same workspace decision/flow tracking protocol and imports [`agent.md`](file:///home/rkushwaha8610/agent.md) via `@agent.md`.
  - Avoided importing the full ~150 KB [`decision.md`](file:///home/rkushwaha8610/decision.md) directly inside [`CLAUDE.md`](file:///home/rkushwaha8610/CLAUDE.md) so Claude Code's context window stays lightweight and fast for scoped terminal TDD loops, while instructing Claude Code to read the top of [`decision.md`](file:///home/rkushwaha8610/decision.md) / [`ats-resume-tailor/decision.md`](file:///home/rkushwaha8610/ats-resume-tailor/decision.md) and update them with the next shared `[DEC-XXX]` number whenever files change.
  - Added Section 4 (`Mandatory Agent Behavior in Claude Code`) to [`/home/rkushwaha8610/agent.md`](file:///home/rkushwaha8610/agent.md) so both **Gemini 4 Argon (High)** (in `agy`) and **Claude Code** share one canonical division-of-labor specification.
- **Flow Impact**: Updated the "Workspace: Rules & Model Routing" section in [`flow.md`](file:///home/rkushwaha8610/flow.md) to include [`CLAUDE.md`](file:///home/rkushwaha8610/CLAUDE.md).

---

### [DEC-047] Workspace Model-Routing Instructions (`agent.md`) Linked Into Always-On `AGENTS.md` (`workspace`)
- **Date**: `2026-10-22 17:33 UTC`
- **Scope**: `workspace`
- **Files Changed**:
  - **New:** [`agent.md`](file:///home/rkushwaha8610/agent.md)
  - **Modified:** [`AGENTS.md`](file:///home/rkushwaha8610/AGENTS.md), [`flow.md`](file:///home/rkushwaha8610/flow.md)
- **Decision & Rationale**:
  - Created [`/home/rkushwaha8610/agent.md`](file:///home/rkushwaha8610/agent.md) to codify the division of labor between **Gemini 4 Argon (High)** (Staff Architect & Deep Problem Solver: problem framing, cross-repo architecture, complex/multi-file/multimodal implementation, deep root-cause debugging, and architectural/security QA) and **Claude Code** (Surgical Pair Programmer & CLI Sprinter: scoped 1–4 file implementation, tight single-package TDD loops, boilerplate/scripts, quick stack-trace fixes, and ergonomic passes), as well as `invoke_subagent` model tiering (`inherit`/`pro`, `flash`, `flash_lite`).
  - Included `@[agent.md](/home/rkushwaha8610/agent.md)` in [`/home/rkushwaha8610/AGENTS.md`](file:///home/rkushwaha8610/AGENTS.md) **above** `## Active Decision Log Include`. Because Antigravity caps each expanded rule file at 24,000 bytes and [`decision.md`](file:///home/rkushwaha8610/decision.md) is ~148 KB, placing the `@[agent.md]` include before the decision log guarantees that the model-routing instructions are injected in full into `<user_rules>` on every turn without truncation.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md) to document the workspace rule include chain (`AGENTS.md` → `agent.md`, `decision.md`, `ats-resume-tailor/decision.md`).

---

### [DEC-046] FDE Pitch Reframed as a Proposal: Agentic AI CFO (Multi-Agent Design), No Built-Project Evidence; v1 Archived (`workspace`)
- **Date**: `2026-10-21 20:20 UTC`
- **Scope**: `workspace`, folder `fde-bootcamp-pitch/`. Supersedes DEC-045's content and framing; the folder layout and build approach stay.
  - **Owner's feedback on v1 (2026-10-21):**
    - The deck should read as a proposal for something not yet built.
    - No tests, and no mention of the bug that filed a month of transactions under 2023.
    - The solution should clearly solve hard problems with agentic AI.
    - It should be a problem statement plus a proposed solution for the same kinds of financial problems the app tackles, not necessarily the exact app.
- **Files Changed**:
  - [`build_deck.py`](file:///home/rkushwaha8610/fde-bootcamp-pitch/build_deck.py): rewritten.
    - **Slide 1:** problem statement.
      - Four hard problems: fragmented data, reconciliation across sources, ambiguity that needs judgment, records but no decisions.
      - Purpose, target users, and a "why agentic AI" band.
    - **Slide 2:** proposed solution.
      - A multi-agent diagram:
        - sources → orchestrator (ADK, Gemini, A2A)
        - four specialist agents (ingestion, reconciliation, clarification, advisor) over MCP tools, inside a dashed guardrail frame
        - outcomes
      - Four pillar cards: "Learned in the bootcamp" → "In this design".
      - Value add (users, BFSI clients, TCS) and the ask (a 6-week pilot).
    - The product name lives in `PRODUCT`; the helpers gained dashed lines, rounded corners and arrows.
  - **New outputs:** [`FDE_Pitch_Agentic_AI_CFO.pptx`](file:///home/rkushwaha8610/fde-bootcamp-pitch/FDE_Pitch_Agentic_AI_CFO.pptx) and [`FDE_Pitch_Agentic_AI_CFO.pdf`](file:///home/rkushwaha8610/fde-bootcamp-pitch/FDE_Pitch_Agentic_AI_CFO.pdf); [`previews/slide-1.png`](file:///home/rkushwaha8610/fde-bootcamp-pitch/previews/slide-1.png) and [`previews/slide-2.png`](file:///home/rkushwaha8610/fde-bootcamp-pitch/previews/slide-2.png) re-rendered.
  - [`PITCH_NOTES.md`](file:///home/rkushwaha8610/fde-bootcamp-pitch/PITCH_NOTES.md): rewritten.
    - framing
    - a worked example (one job, end to end)
    - a 6-week pilot plan and pilot metrics labelled as targets
    - answers to likely questions, written for a proposal
    - "keep it a proposal" rules
    - The demo script and the number sources from the built app were removed.
  - [`README.md`](file:///home/rkushwaha8610/fde-bootcamp-pitch/README.md): new file names and the archive location.
  - **Moved (not deleted)** to `~/backups/fde-bootcamp-pitch/2026-10-21-v1-built-project/` (folder 700): the v1 `FDE_Pitch_Personal_Finance_AI.pptx` and `.pdf`. That keeps the built-project version from being opened by mistake.
- **Decision & Rationale**:
  - **Proposal, not evidence.**
    - **Removed:** test counts, the 2023 date bug, "live" and "used on my own bank data", all measured numbers (40/40, 15 → 2, 520 rows, latency), and "first user: me".
    - **Wording:** the deck describes what the system will do. The only numbers are pilot targets, and they appear in PITCH_NOTES, labelled as targets.
  - **Why it reads as agentic.** It shows planning (orchestrator), delegation (A2A), tool use (MCP: the ledger DB, document store, connectors, calculators), memory (the clarification agent learns the user's rules), human-in-the-loop (approval for changes) and proactive alerts (EMI or cash shortfall). Slide 1 explains why this needs agents, not one prompt: the work is multi-step and multi-source.
  - **Bootcamp tools now belong in the design.** Model Armor, DLP, MCP, A2A, autoraters, OpenTelemetry and caching are proposed parts of the design, not claims about anything built. So the honesty constraint from DEC-045 still holds: nothing is presented as built.
  - **BFSI framing:** Account Aggregator as a source, and lending, reconciliation and banking-app use cases, so the delivery manager sees client value. Compliance is presented as a design intent: DPDP review with the client.
  - **Kept from DEC-045:** the generated deck, Arial with theme fonts set to Arial, no theme shadows, manual line breaks, and wraps checked with Liberation Sans metrics.
- **Verified:**
  - Built and rendered both slides, with notes on each.
  - Previews checked at 110 dpi, and the diagram at 200 dpi: no overflow, no orphaned words, no hyphen breaks.
  - The slide text contains nothing from the built app's history.
- **Open:**
  - "MY ASK" and the working name "Agentic AI CFO" are the owner's to tailor.
  - The pillar grouping question from DEC-045 stays open; PITCH_NOTES § 7 covers it.
- **Flow Impact**: Updated the "Workspace: FDE Bootcamp Pitch" section in [`flow.md`](file:///home/rkushwaha8610/flow.md): new output names, the slide text now sourced from the owner's bootcamp notes rather than the finance agent's code, and the archived v1.

---

### [DEC-045] FDE Bootcamp Pitch: 2-Slide Problem-Statement Deck With Personal Finance AI as the Case, Generated by a Script, Every Claim Matched to the Code (`workspace`)
- **Date**: `2026-10-21 20:00 UTC`
- **Scope**: `workspace`. A new folder, `fde-bootcamp-pitch/` (no git repo), outside both projects. Nothing in `personal-finance-agent` or `ats-resume-tailor` changed.
  - **Owner's request (2026-10-21):** a 2-slide pitch for the next day, to his delivery manager.
    - Content: a problem statement (purpose, value add, target users) covering what he learned in the 4-day FDE bootcamp, with the finance agent as the case.
    - Location: a separate folder in Cloud Shell.
- **Files Changed** (all new, in [`fde-bootcamp-pitch/`](file:///home/rkushwaha8610/fde-bootcamp-pitch)):
  - [`build_deck.py`](file:///home/rkushwaha8610/fde-bootcamp-pitch/build_deck.py): python-pptx generator.
    - All slide text is in constants at the top.
    - `--render` exports the PDF and the PNG previews through LibreOffice and pdftoppm.
  - [`FDE_Pitch_Personal_Finance_AI.pptx`](file:///home/rkushwaha8610/fde-bootcamp-pitch/FDE_Pitch_Personal_Finance_AI.pptx): 16:9, speaker notes on both slides.
  - Rendered outputs: [`FDE_Pitch_Personal_Finance_AI.pdf`](file:///home/rkushwaha8610/fde-bootcamp-pitch/FDE_Pitch_Personal_Finance_AI.pdf), [`previews/slide-1.png`](file:///home/rkushwaha8610/fde-bootcamp-pitch/previews/slide-1.png), [`previews/slide-2.png`](file:///home/rkushwaha8610/fde-bootcamp-pitch/previews/slide-2.png).
  - [`PITCH_NOTES.md`](file:///home/rkushwaha8610/fde-bootcamp-pitch/PITCH_NOTES.md): framing, demo script (demo login only), likely questions, say / don't say, the source of each number, pre-meeting checklist.
  - [`README.md`](file:///home/rkushwaha8610/fde-bootcamp-pitch/README.md) and [`requirements.txt`](file:///home/rkushwaha8610/fde-bootcamp-pitch/requirements.txt): python-pptx 1.0.2, installed in the folder's own `.venv`.
- **Decision & Rationale**:
  - **Framing.** A delivery manager weighs delivery capability and value for TCS.
    - So the deck pitches the problem, with the finance agent as evidence: GenAI reading financial documents is easy; trusting what it reads is hard.
    - It ends on value for TCS (BFSI document work) and an ask.
    - **Slide 1:**
      - the problem statement and three pain points
      - purpose
      - target users: individuals, BFSI client teams, TCS delivery teams
      - the bootcamp's core lesson
    - **Slide 2:**
      - a five-step flow
      - per pillar, "Learned in the bootcamp" next to "Applied in the agent"
      - a "Next" line
      - four proof numbers
      - value for TCS and the ask
  - **Claims match the code.** They follow the claims table in [`linkedin_profile_fde.txt`](file:///home/rkushwaha8610/linkedin-profile/linkedin_profile_fde.txt) (2026-10-20), re-checked today.
    - **Bootcamp topics only.** Model Armor, Cloud DLP, MCP, A2A, autoraters, OpenTelemetry and prefix caching appear only as bootcamp topics or on the "Next" line.
    - **What's built:**
      - regex prompt-injection screening and PII redaction on chat messages
      - an MCP-style tool layer
      - a rule-based evaluation suite
    - **Numbers:**
      - 146 tests: today's pytest run, 146 passed and 13 skipped.
      - 40/40 rows exact (DEC-042).
      - 15 → 2 of 67 duplicates (DEC-034).
      - 0 false alarms on 520 rows (DEC-035).
      - Flash-Lite in 1–3 s (DEC-042).
      - Twice under 2023 (DEC-033, DEC-035).
      - Min instances 0 (`deploy_backend.sh`).
    - **The old deck** (`Personal_Finance_AI_Pitch_Deck.pptx`, 8 slides) wasn't reused: it presents unbuilt features as built, and its screenshots show real balances.
    - **Gartner statistic** on abandoned GenAI pilots: left off the slides.
      - Its page returned 403 from Cloud Shell, so the wording couldn't be checked.
      - PITCH_NOTES lists it as optional, to be quoted from the source.
  - **Generated, not drawn by hand.** A text change is one edit and a rebuild, and the build renders previews for a visual check.
    - **Font:** Arial only.
      - PowerPoint and Google Slides both have it, and LibreOffice swaps in the metric-compatible Liberation Sans, so the previews wrap the same way.
      - The theme fonts are set to Arial too, so text typed in later matches.
    - **Shadows:** the theme style (`p:style`) is removed from every shape and connector. LibreOffice drew the theme's drop shadow from it even with effects turned off.
    - **Line breaks:** manual breaks where natural wrapping left orphan words. The one-line labels were measured with Liberation Sans metrics.
  - **Tooling.**
    - LibreOffice Impress, fonts-liberation and poppler-utils were installed with apt in Cloud Shell. They live on the root filesystem and are lost on restart; the reinstall command is in the folder's README.
    - python-pptx lives in `fde-bootcamp-pitch/.venv` (54 MB), so the finance project's venv is untouched.
- **Verified:**
  - Built and rendered: 2 slides, both with notes; no `p:style` left; theme fonts are Arial.
  - Previews checked at 110 and 220 dpi: no overflow, and no clipped or orphaned text.
- **Open:**
  - **"MY ASK"** is a placeholder for the owner to tailor.
  - **Pillar mapping:** "Scale = evaluation" follows the owner's LinkedIn text and FDE doc, but his 13 Oct notes put evaluation under Optimize. He needs to confirm it.
  - **Frontend labels** still say "Gemini 2.5" (DEC-042 open item). PITCH_NOTES flags this for the demo.
  - **Finance project `docs/`** still overclaim (DEC-042 open item); unchanged.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md): a new section, "Workspace: FDE Bootcamp Pitch", with the build graph and a connection table.

---

### [DEC-042] Finance App Off Retiring Gemini 2.5: Gemini 3.5 Flash / 3.5 Flash-Lite on the Global Endpoint (3.8 Flash Stalls Today), Multi-Part Replies Read, No Low Temperatures; Deployed (`personal-finance-agent`)
- **Date**: `2026-10-20 03:30 UTC`
- **Scope**: `personal-finance-agent`. Backend code (commits `945a254`, `0c01030` and `08bf5eb` on local `main`, no remote), deployed to both Cloud Run services, demo first.
  - Closes DEC-039's open "Finance app (urgent)" item.
  - Owner's go-ahead (2026-10-20): "you can change the Gemini 2.5 to Gemini 3 and deploy it the finance app".
- **Files Changed**:
  - **New:**
    - [`app/gemini.py`](file:///home/rkushwaha8610/personal-finance-agent/app/gemini.py): `reply_text`
    - [`tests/test_model_config.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_model_config.py): 38 tests
  - **Modified:**
    - [`app/config.py`](file:///home/rkushwaha8610/personal-finance-agent/app/config.py): `LOCATION` defaults to `global`; `MODEL_NAME` / `MODEL_LITE` / `MODEL_PRO` via `model_setting`, which uses `is_retired_model`.
    - [`app/advisor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/advisor.py):
      - `ModelTier` holds tier names; `ModelTier.model` reads the model from config.
      - The router returns the tier.
      - No temperature or thinking budget; replies are read with `reply_text`.
    - [`app/statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py), [`app/extractor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/extractor.py), [`app/ai_importer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py): `MODEL_NAME` and `reply_text`; the importer's `temperature 0.0` is gone.
    - [`app/voice_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py): `MODEL_LITE` and `reply_text`.
    - [`scripts/deploy_backend.sh`](file:///home/rkushwaha8610/personal-finance-agent/scripts/deploy_backend.sh):
      - an optional `all|demo|personal` target;
      - traffic goes to the new revision after each deploy;
      - the smoke checks name the serving revision.
    - [`tests/test_fde_integration.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_fde_integration.py): the tiering test follows the new router.
    - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py) (docstring) and [`README.md`](file:///home/rkushwaha8610/personal-finance-agent/README.md).
  - **One-off scripts, outside the repo** (synthetic data only, never the personal database):
    - Vertex AI probes: [`probe_models.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/56ffbc72-324e-42ae-9b70-c7b8c85d14eb/scratch/probe_models.py), [`probe_thinking.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/56ffbc72-324e-42ae-9b70-c7b8c85d14eb/scratch/probe_thinking.py), [`latency_sample.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/56ffbc72-324e-42ae-9b70-c7b8c85d14eb/scratch/latency_sample.py), [`bench_statement.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/56ffbc72-324e-42ae-9b70-c7b8c85d14eb/scratch/bench_statement.py)
    - [`live_check.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/56ffbc72-324e-42ae-9b70-c7b8c85d14eb/scratch/live_check.py): every AI code path from Cloud Shell, with no database
    - [`demo_smoke.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/56ffbc72-324e-42ae-9b70-c7b8c85d14eb/scratch/demo_smoke.py): every AI path through the demo backend's API, demo login (never printed)
- **Decision & Rationale**:
  - **Why.** Vertex AI's model-versions page lists gemini-2.5-pro, -flash and -flash-lite as retiring on October 20, 2026, today.
    - The app used 2.5 everywhere: the config default, the advisor's three tiers, and four hard-coded modules.
    - Cloud Run sets neither `MODEL_NAME` nor `LOCATION`, so the code defaults applied.
  - **Endpoint.** In this project the 3.x models answer on `global` and return 404 on `us-central1`, so `LOCATION` defaults to `global`. Both service accounts hold `roles/aiplatform.user` at project level.
  - **Models.** All three are overridable per environment.
    - **Standard (`MODEL_NAME`) and Pro (`MODEL_PRO`): `gemini-3.5-flash`.** Statements, receipts, spreadsheets, insights and the CFO chat; Pro takes the planning questions.
    - **Lite (`MODEL_LITE`): `gemini-3.5-flash-lite`.** Quick look-ups in the chat, and the voice clarifications (2.5 Flash before). Those are short, interactive tasks, at about 0.7 s per category.
    - **A retired 1.x/2.x name** left in the environment falls back to the default with a startup warning instead of stopping the backend: a retired model fails every call, and the ledger must keep working.
    - **Why not 3.8 Flash**, Google's first-listed replacement and the first deploy: today it stalled on Vertex AI's side.
      - **First demo deploy:** a receipt took 106 s and a CFO chat 303 s; the frontend gives up at 90 s.
        - Vertex AI's own `model_invocation_latencies` showed 105,688 and 302,591 ms. The outputs were small (1–2K tokens), so the time went to waiting, not generating.
        - The ATS drafter had a 304 s call at 02:49 (DEC-043).
      - **Concurrent chat-sized synthetic requests:**
        - 3.8 Flash: 1 of 6 took 147 s, and 3 of 20 took over 120 s.
        - 3.5 Flash: 26 of 26 under 25 s.
        - 3.5 Flash-Lite: 26 of 26 at 2–3 s.
      - **The same 40-row synthetic statement**, 40/40 exact on every model:
        - 3.8 Flash: 30 s
        - 3.5 Flash: 33 s
        - 3.5 Flash-Lite: 9 s
        - the old 2.5 Flash: 42 s (us-central1, still answering at 02:53)
      - **Price** per 1M tokens in/out (standard, global):
        - 3.5 Flash: $1.50/$9.00, and it thinks more (650–1,900 thought tokens per chat answer, billed as output).
        - 3.8 Flash: $0.75/$3.75 until 31 Dec 2026, then $1.50/$7.50.
        - 3.5 Flash-Lite: $0.30/$2.50.
        - The old models: 2.5 Flash $0.30/$2.50, 2.5 Flash-Lite $0.10/$0.40.
        - At this app's volume that is cents a day, and the demo's daily AI limits cap it there.
  - **Temperature and thinking.**
    - **Temperature:** Google's Gemini 3 guide advises keeping the default temperature (1.0), because lower values may loop or degrade. The chat's `0.35` and the spreadsheet importer's `0.0` are removed; `top_p 0.95` and `response_mime_type` stay.
    - **Thinking budget:** the Pro tier's `thinking_budget: 1024` is gone.
      - On gemini-3.8-flash it turned thinking off: 0 thought tokens, against 1,332–1,903 at the default on the same planning question.
      - The pinned SDK (google-cloud-aiplatform 2.0.1, whose request types have no `thinking_level`) can't set a level instead.
  - **Replies.** The SDK's `response.text` raises "Multiple content parts are not supported" when a reply has more than one part, and Gemini 3 attaches thought signatures to parts and may split an answer.
    - `gemini.reply_text` joins the non-thought text parts, the way google-genai reads a reply.
    - Its errors never include the reply, which holds financial data.
  - **Per-call timeout: not added.**
    - `generate_content` and `ChatSession.send_message` take no timeout, and `vertexai.init` has no timeout option either; the underlying call has no default timeout.
    - A real deadline would mean patching the SDK's private `_prediction_client`, and a thread-based timeout would leave the stalled call running. Neither is clean.
    - So a stalled call still holds its request until Vertex answers.
    - google-genai (already in `requirements.txt`) offers `HttpOptions(timeout=...)` and `thinking_level`.
  - **Deploy script.**
    - It deployed both services, personal first. It now takes `all|demo|personal`, so the demo goes first.
    - After the demo rollback had pinned traffic to `00005-s6l`, the next `gcloud run deploy` created `00007-g7h` at 0 percent, and the script's smoke checks passed against the old revision.
    - Each deploy now runs `update-traffic --to-latest`, and the smoke line names the serving revision.
- **Verified:**
  - **Tests:** 146 passed, 13 skipped (`TEST_DATABASE_URL` unset). The 38 new tests read real SDK reply objects with Vertex AI faked, and need no credentials.
  - **Live from Cloud Shell** (real Vertex AI on `global`, synthetic inputs, no database, final defaults):
    - statement, 40 rows: 40/40 exact (32.8 s)
    - receipt: correct (3.5 s)
    - spreadsheet: 9/9 exact, with verbatim descriptions (9.8 s)
    - insights: 13.3 s
    - chat standard / lite / pro: 8.3 / 1.2 / 15.8 s
    - voice categories: 3/3 (about 0.7 s each)
    - voice transcription through the Gemini fallback: 3.4 s
  - **Deploys:**
    - **First demo deploy:** image `945a254-20261020-025454` as `finance-backend-demo-00006-rz9`. The smoke test failed on latency (above), and traffic went back to `finance-backend-demo-00005-s6l`.
    - **Demo, live:** image `0c01030-20261020-031803` as `finance-backend-demo-00007-g7h`, 100% of traffic.
      - Smoke test through the API with the demo login:
        - statement 4/4 exact (22.2 s)
        - receipt correct (4.6 s)
        - insights: 18.2 s
        - chat standard / lite / pro: 15.8 / 2.8 / 14.2 s
        - a text clarification resolved to Health/Medical (1.6 s)
      - Logs clean.
      - The rows land in the demo database, which resets nightly. Today's demo use: 4 of 10 uploads, 2 insights, 4 chats, 1 voice clarification.
    - **Prod, live:** the same image as `finance-backend-00006-mss`, 100% of traffic.
      - Before: `finance-backend-00005-crm`, image `4386df5-20261019-122929`.
      - /health 200, /me 401, /transactions 401; startup logs clean.
    - **Rollback:**
      - prod: `gcloud run services update-traffic finance-backend --project personal-finance-agent-507010 --region asia-northeast1 --to-revisions=finance-backend-00005-crm=100`
      - demo: `gcloud run services update-traffic finance-backend-demo --project personal-finance-agent-507010 --region asia-northeast3 --to-revisions=finance-backend-demo-00005-s6l=100`
      - Both old revisions run Gemini 2.5, which stops working once it's retired.
- **Open:**
  - **Prod AI paths not exercised directly:** they need the owner's Google sign-in.
    - The demo verified the same image.
    - The personal service account has the same Vertex AI role. Impersonating it to check was denied, and IAM was left unchanged.
  - **3.8 Flash:** revisit once its delays settle; it is cheaper until 31 Dec 2026.
    - Change the defaults in `config.py`, or set `MODEL_NAME` / `MODEL_PRO` on the service.
    - An env var set with `gcloud run services update` is reset by the deploy script's `--set-env-vars`.
  - **SDK:** `vertexai.generative_models` warns that it was deprecated on 2025-06-24 with removal on 2026-06-24. The pinned 2.0.1 still works; move to google-genai.
  - **Categories:** the statement and receipt prompts don't list the app's categories.
    - So Gemini 3 picks its own names ("Groceries", "Food & Dining"), and those rows are flagged for review.
    - Putting the list in the prompts is a small follow-up.
  - **Frontend text** still says "Gemini 2.5 Flash" (`components/upload.js`) and "Ask Gemini 2.5 questions" (`components/ledger.js`). Changing it needs a Firebase Hosting deploy.
  - **Docs:** `docs/` (architecture, pitch, resume and interview guides) still describe Gemini 2.5; they were not changed.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md):
  - the new `app/gemini.py` node, with edges from the five AI modules
  - the model-per-tier edge to `config.py` and the Vertex AI global node
  - `tests/test_model_config.py`
  - the rows for `config.py`, the readers, `advisor.py` / `voice_agent.py` and `deploy_backend.sh`

---

### [DEC-035] August 2025 Upload Also Saved in 2023: Rows Moved to 2025, Year Guard Added to Every AI Reader; Deployed (`personal-finance-agent`)
- **Date**: `2026-10-19 12:35 UTC`
- **Scope**: `personal-finance-agent`. Personal database (data fix), backend code (commit `4386df5`), deployed to both Cloud Run services. Closes DEC-033's open item.
- **Files Changed**:
  - **New:**
    - [`app/date_guard.py`](file:///home/rkushwaha8610/personal-finance-agent/app/date_guard.py): `period_from_filename`, `prompt_context`, `fix_years`
    - [`tests/test_date_guard.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_date_guard.py): 33 tests
  - **Modified:**
    - [`app/statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py), [`app/extractor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/extractor.py): date rules in the prompt, `fix_years` after validation, `self.date_notes`.
    - [`app/ai_importer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py): the same checks before saving; notes come back in `result["notes"]`.
  - **Data:** the 52 rows of `Monthly budget August 2025.pdf` (#1169–#1220): `transaction_date` and `row_signature` only.
  - **One-off scripts, outside the repo:**
    - [`fix_upload_year.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/2f21a412-e5ca-48aa-af49-cf8b769c75b9/scratch/fix_upload_year.py): general version of the DEC-033 fix (`--source/--from-year/--to-year/--expect`, dry run by default, `--apply`, `--undo`)
    - read-only: [`inspect_wrong_year.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/2f21a412-e5ca-48aa-af49-cf8b769c75b9/scratch/inspect_wrong_year.py), [`inspect_august_context.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/2f21a412-e5ca-48aa-af49-cf8b769c75b9/scratch/inspect_august_context.py), [`replay_date_guard.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/2f21a412-e5ca-48aa-af49-cf8b769c75b9/scratch/replay_date_guard.py)
    - [`demo_date_e2e.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/2f21a412-e5ca-48aa-af49-cf8b769c75b9/scratch/demo_date_e2e.py): demo backend only
  - **Undo data** in `~/backups/personal-finance-agent/2026-10-19/` (folder 700, files 600):
    - `fix_august_2025_dates_20261019T121543Z_undo.csv`
    - `..._rows_before.json`
- **Decision & Rationale**:
  - **What happened.** At 12:06:59 and 12:07:17 UTC the owner uploaded `Monthly budget August 2025.pdf` twice. Both uploads hit the importer from before DEC-034.
    - Every row was dated 2023, the same failure as March (DEC-033).
    - The PDF's Income table also carries rows from earlier months (Salary 30 Jun, "Previous Balance" 1 Jul, three ₹0 rows dated 18 May, 21 Jun and 30 Jun). Those landed in May–July 2023.
    - The second upload added 5 copies (#1216–#1220) that the old matching missed.
  - **Data fix:**
    - Same method as DEC-033, generalised: every row of this upload dated 2023 moves to 2025 (same day and month), and its signature is recomputed. Nothing was deleted.
    - Exactly 52 rows, and no other 2023 rows exist.
    - #1171 "Rent August" had been edited by the owner (`user_locked`); its date was still corrected.
    - #1211 (₹0, blank) would have become identical to #246, so it kept its old signature (signatures are unique).
    - Verified: 52/52 rows moved, nothing left in 2023.
  - **Code fix** (the DEC-033 open item; done because the same failure recurred), in two layers:
    - **Prompt.** `prompt_context` gives the model today's date (India time, like `api.py`), the file name, and the period that name states.
      - A missing year must come from the document's statement period, then the file name, then the most recent past date with that day and month.
      - It must never come from the model's own knowledge.
      - The name is reduced to `[A-Za-z0-9._ ()-]` so it can't add instructions.
    - **Check** (deterministic). `fix_years` uses the period in the file name.
      - Name patterns: `March 2026`, `2026-09`, `1 Jun 26-31 Aug 26`, `July 25 to Aug 25 2026`, `IMG20260830…`.
      - Ignored: bare years ("Order 2024"), two-digit years more than 10 years old, a lone "may", and years after next year.
      - A date more than 183 days outside the period gets the year that puts it back inside, keeping day and month. Budget sheets carry rows from earlier months, hence the margin.
      - With no period in the name, only a date after today (with 2 days' grace) moves back a year. Anything else is left alone and noted.
      - It returns notes.
    - **Kept out of `main.py` and `database.py`:** the other session (DEC-034) was editing both at the time. The readers hold the notes (`date_notes`, `result["notes"]`) for a later `main.py` hook.
  - **Verified:**
    - Tests: `test_date_guard.py` 33/33 (real file names, both incidents, window edges, prompt text and name cleaning; all three readers end to end with a fake Gemini). Full suite 108 passed, 13 skipped.
    - **Replay on the personal ledger** (read-only):
      - All 520 rows that have a source file are left unchanged. No false alarms on correct data.
      - Fed the original dates of the two broken uploads, the guard reproduces the manual fixes exactly: 60/60 and 52/52.
    - **Deploy:**
      - Image `4386df5-20261019-122929`, serving as `finance-backend-00005-crm` (Tokyo) and `finance-backend-demo-00005-s6l` (Seoul).
      - Smoke checks 200/401/401 on both, startup logs clean.
      - Rollback: `scripts/deploy_backend.sh a264541-20261019-122221`.
    - **Live check on the demo backend:**
      - Uploaded a synthetic `Monthly budget August 2025.pdf` whose dates have no year ("1-Aug", "30-Jul").
      - 5/5 rows were saved in 2025 (2025-07-30 … 2025-08-14).
      - No `Date check` log line appeared, so the prompt alone was enough this time; `fix_years` is the safety net.
  - **Coordination with session b40159a1** (DEC-032/034):
    - Both sessions work in one tree, and `deploy_backend.sh` uploads the tree, not the commit.
    - Their build `fca1ff4a` (12:22:26) picked up an early copy of `date_guard.py` that nothing imported. Checked in the source tarball: no change in behaviour.
    - After they confirmed their deploy was finished, `4386df5` was committed and deployed from a tree identical to the commit.
    - They will show the owner the duplicate-cleanup dry run for August.
  - **Open:**
    - **Duplicates from the double upload** (dry run `dedupe-20261019T122826Z`, read-only):
      - Remove: #1220, #1216, #1217, #1218.
      - Review: #1211/#246, #1210/#298 ("Previous Balance" ₹[redacted] saved as INCOME, which counts it as July income; #298 is the owner's TRANSFER), #1219/#1209 ("sarnosa"/"samosa").
      - Waiting for the owner, through the cleanup session.
    - **#1212 Salary ₹[redacted]** is dated 30 Jun 2025, as read from the sheet. June already has salary row #248 on 30 June, so this is probably the July salary. Waiting for the owner.
    - **Upload messages:** the date notes aren't shown in the upload messages yet (a 3-line `main.py` hook).
    - **No date field in the app's edit route:** `PUT /transactions/{id}` can't change a date, so the owner can't fix one in the app.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md):
  - the new `DateGuard` node and `tests/test_date_guard.py`
  - edges from `statement_agent.py`, `extractor.py` and `ai_importer.py`
  - new table rows for `app/date_guard.py` and its tests

---

### [DEC-034] Duplicates Stopped at Import Time: Reference Matching Across Labels, Type Changes Only With Proof, One Import per Statement File; Deployed (`personal-finance-agent`)
- **Date**: `2026-10-19 12:28 UTC`
- **Scope**: `personal-finance-agent` (backend import path, deployed to both Cloud Run services; DEC-032's cleanup tool committed)
- **Files Changed** (commits on local `main`, no remote):
  - `08dca81`: the DEC-032 cleanup tool, committed as built ([`app/dedupe.py`](file:///home/rkushwaha8610/personal-finance-agent/app/dedupe.py), [`scripts/dedupe_transactions.py`](file:///home/rkushwaha8610/personal-finance-agent/scripts/dedupe_transactions.py), [`tests/test_dedupe.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_dedupe.py)).
  - `d097665` (first version) and `a264541` (merge policy corrected after the demo check):
    - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py):
      - new helpers: `extract_reference_numbers` (replaces `extract_utr_numbers`), `description_similarity`, `is_generic_category`, `reconcile_rule`, `account_hint` / `account_hints`, `merged_labels`, `FileAlreadyImported`, `find_ingested_file`;
      - `find_reconcilable_transaction` rewritten;
      - `save_transactions_batch(file_hash=, file_name=)` now fills `last_batch_summary`, and `save_transaction` delegates to it.
    - [`app/main.py`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py): the same-PDF guard and the upload messages (`_duplicate_notes`, `_already_imported_message`).
    - [`app/dedupe.py`](file:///home/rkushwaha8610/personal-finance-agent/app/dedupe.py): imports the shared reference and similarity helpers instead of keeping its own copies.
    - [`tests/test_reconcile.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_reconcile.py) (15 tests) and [`tests/test_reconcile_db.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_reconcile_db.py) (13 tests; a real Postgres via `TEST_DATABASE_URL`, local hosts only).
  - One-off scripts, outside the repo:
    - [`replay_check.sh`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/b40159a1-72e2-44ca-8ad0-37ff82698c67/scratch/replay_check.sh) with [`replay_duplicates.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/b40159a1-72e2-44ca-8ad0-37ff82698c67/scratch/replay_duplicates.py): throwaway restore only
    - [`rule_pairs.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/b40159a1-72e2-44ca-8ad0-37ff82698c67/scratch/rule_pairs.py): read-only
    - [`demo_e2e_duplicates.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/b40159a1-72e2-44ca-8ad0-37ff82698c67/scratch/demo_e2e_duplicates.py): demo backend only, synthetic data
- **Decision & Rationale**:
  - **Why.** After the DEC-032 cleanup, the owner asked to commit the tool and to stop new duplicates. Every import (statement PDFs, Excel, Gmail alerts, receipts) goes through `find_reconcilable_transaction`. Duplicates got through for three reasons:
    - **Same-type filter:** it only compared rows with the same type label. A re-import that labelled a row differently (#1097 INCOME vs #1049 TRANSFER) was never matched, and neither were the HDFC alert (EXPENSE) and the Kotak line (TRANSFER) of one transfer.
    - **Phone numbers as references:** 9–12-digit numbers counted as references. Two different transfers on consecutive days that both carry the owner's phone number would have been merged, losing one.
    - **No same-file check for PDFs:** two Gmail syncs processed the same September statement email, 43 s apart.
  - **Which existing row is the incoming transaction** (`reconcile_rule`). Candidates are rows of any type with the same amount within 2 days:
    - **Both rows carry 12-digit references:** the same payment if they share one, whatever the labels say. Different payments otherwise.
    - **Otherwise, only the same calendar date counts:**
      - same type: a Gmail alert vs a statement or receipt row, a shared merchant word, or the same letters ("KOTAK811" vs "KOTAK811/620116025326", found by the replay);
      - different type: one side is a transfer and the wording is similar.
    - An expense and an income are never merged without a shared reference.
    - The best candidate wins: the strongest rule, then the closest date, then the same type, then the oldest row. Rows of one batch never merge.
    - The cleanup tool uses the same definitions.
  - **What happens to the matched row's labels** (`merged_labels`):
    - **The type changes only with proof.** The proof is the same reference in records of two different banks (`account_hints`: a Gmail alert's tag, or the bank named in any of the row's file names). The money then moved between the owner's own accounts, and the row becomes a transfer.
    - **Any other type disagreement** keeps the row's labels, marks it for review, and is counted in the upload message.
    - **Same type:** the incoming category wins, except that "Other" never replaces a specific one. The same rule applies on exact-signature upserts.
    - Confidence and the review flag change only when the labels change. User-locked rows keep their labels and text.
    - **Why the first version was replaced:** `d097665` made any incoming transfer label, or expense vs income with one reference, a transfer. The demo check below showed that a re-exported statement misread as income turned a coffee purchase into a transfer.
  - **Provenance:** a recognised copy's file is recorded once on the matched row (`assets`), so a row keeps every record it came from. The app doesn't list assets per row; statement downloads use `exported_statement_pdfs`.
  - **One import per statement PDF:**
    - `process_file` hashes the file and skips it before any AI call if `ingested_files` already has the hash.
    - `save_transactions_batch` claims the hash inside the same transaction, so two simultaneous imports can't both save (tested with two threads).
    - This covers manual uploads and Gmail-exported statements; Excel already had a hash check.
  - **Upload messages:**
    - "N of M transactions were already in your ledger (from '<file>' / your Gmail alerts), so they weren't added again."
    - "… came with a different type …; the ledger kept its label and marked them for review."
    - "'<file>' was already imported on <date> (N transactions), so nothing was added again."
    - For receipts: "This receipt matches a transaction already in your ledger …"
    - A note when identical lines inside one file are saved once (a known limit, see below).
  - **Verified:**
    - Tests: unit 15/15; Postgres 17 13/13 (including the concurrent same-file import); full suite 75 passed, with 13 skipped when `TEST_DATABASE_URL` isn't set.
    - **Replay on real data:**
      - Setup: a throwaway copy restored from the 11:35 backup, with the DEC-032 plan applied. The 67 removed duplicates were fed back through the importer in their original files and order.
      - The old importer added 15 back.
      - The final version adds 2: #1055/#1056, whose amounts the first September import misread. No matching rule can catch a wrong amount.
      - The 9 HDFC alert legs became transfers with no review flag. Two copies from the same account that disagreed were marked for review.
    - **Rule comparison on today's ledger** (884 rows, read-only): of 167 same-amount pairs within 2 days, the new rules match 1 pair the old ones didn't, and drop none.
      - The pair is #772/#69, still an open question (a Kotak→HDFC "Electri bill" transfer vs the HDFC "ELECTRI BILL JULY" expense).
      - In a future import, a match like that keeps the existing row's type and is marked for review.
    - **Deploys:**
      - `d097665-20261019-120541`, then `a264541-20261019-122221`.
      - Now serving: `finance-backend-00004-7rk` (Tokyo) and `finance-backend-demo-00004-ndt` (Seoul).
      - Smoke checks 200/401/401 on both; startup logs clean.
      - A manual run of the hourly Gmail tick returned 200, but skipped the sync because the Bank Sync interval hasn't passed.
      - Rollback: `scripts/deploy_backend.sh phase1b-95f7b08-20261018-203119`.
    - **End to end on the demo backend** (synthetic statements, 5 of the demo's 10 daily uploads; the rows go at tonight's demo reset):
      - first statement: 4 new;
      - the same file again: skipped before any AI call;
      - an overlapping re-export: 3 of 4 recognised, 1 added.
      - With `a264541`: a re-issued statement printing a coffee purchase as a credit left the row an expense, marked for review, and the message reported 2 type disagreements.
      - The first version had made the earlier coffee row (#1450) a transfer. That demo row is reset tonight.
  - **Known limits / open:**
    - A wrong amount from the AI reader isn't something the matching can see.
    - Identical lines inside one file (same date, amount, text and type) are still saved once (`row_signature`). The upload now says so.
    - Files imported before today have no hash, so a re-upload of one goes through row matching only. The March 2026 budget PDF would add its rows again under March 2023 (dates misread), so it shouldn't be re-uploaded until DEC-033's open item is fixed.
    - #772/#69 is still open.
    - **August 2025 double upload:** the owner uploaded `Monthly budget August 2025.pdf` twice, at 12:06:59 and 12:07:17 UTC, just before the first deploy went live (12:08:35).
      - The old importer saved 5 copies, #1216–#1220. Session 2f21a412 then moved the file's 52 rows from 2023 to 2025 (DEC-035).
      - A read-only dry run (`dedupe-20261019T122909Z`) lists 4 of the copies to remove; #1219 (OCR "sarnosa" for "samosa") is in review.
      - The same file's "Previous Balance" ₹[redacted] (#1210) and its ₹0 blank row (#1211) are in review against the older spreadsheet rows #298 and #246.
      - Waiting for the owner. With the new same-file guard, that second upload would have been skipped.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md):
  - `main.py` → `ai_importer.compute_file_hash` and `database.FileAlreadyImported` / `find_ingested_file`;
  - `dedupe.py` → the shared database helpers;
  - new `tests/test_reconcile.py` and `tests/test_reconcile_db.py`;
  - the `main.py` and `database.py` rows describe the new matching, merge and same-file rules.

---

### [DEC-032] Duplicate-Transaction Cleanup: Rules, Reviewable Plan, Reversible Apply/Undo; Applied on the Personal Database, 67 Rows Removed (`personal-finance-agent`)
- **Date**: `2026-10-19 11:34 UTC` (written); applied `11:36 UTC` after the owner replied "apply".
- **Scope**: `personal-finance-agent` (new maintenance tool, plus a data change in the personal database: 67 rows moved to `transactions_removed`, 18 kept rows relabelled). The number was reserved for this work while DEC-033 was written in another session.
- **Files Changed** (new, not committed):
  - [`app/dedupe.py`](file:///home/rkushwaha8610/personal-finance-agent/app/dedupe.py): the rules. Pure functions, no database access.
  - [`scripts/dedupe_transactions.py`](file:///home/rkushwaha8610/personal-finance-agent/scripts/dedupe_transactions.py): the dry run (the default, read-only) writes a plan CSV and a report; `--apply PLAN.csv`; `--undo PLAN_ID`.
  - [`tests/test_dedupe.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_dedupe.py): 21 tests.
  - Data, not code: plans and reports in `~/backups/personal-finance-agent/dedupe/` (folder 700, files 600). They hold personal data, so they never go in the repo.
  - One-off scripts, outside the repo:
    - [`approve_plan.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/b40159a1-72e2-44ca-8ad0-37ff82698c67/scratch/approve_plan.py): turns a fresh dry-run plan into the approved one
    - [`verify_apply.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/b40159a1-72e2-44ca-8ad0-37ff82698c67/scratch/verify_apply.py): read-only checks after the apply
    - [`dedupe_integration.sh`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/b40159a1-72e2-44ca-8ad0-37ff82698c67/scratch/dedupe_integration.sh): apply and undo against a throwaway restored Postgres
- **Decision & Rationale**:
  - **Why.** The owner found rows with the same date, the same amount and a similar description, and asked for a rule instead of deleting them by hand. Nothing is removed until the owner has reviewed the plan.
  - **Where the duplicates come from** (read-only analysis):
    - **Two Kotak statements that both cover July:** the monthly `Kotak July 2026.pdf` (imported 7 Sep) and the quarterly `Kotak statement 1 Jun 26-31 Aug 26.pdf` (21 Sep). All 41 July rows match on date and amount.
      - The monthly file has better categories but no reference numbers. The quarterly one has reference numbers and mostly "Other".
      - 8 pairs are labelled differently: TRANSFER in the monthly file, EXPENSE in the quarterly one. Mostly money to the owner's own accounts, or loans "to be back".
    - **`July 25 to Aug 25 2026.pdf` imported twice** (7 Sep 17:33 and 18:31, from two different paths).
    - **`Kotak_Bank_2026-09_….pdf` imported twice from the same path, 43 s apart** (15 Oct 19:34:18 and 19:35:01).
      - The second import saved only the 8 rows the importer couldn't match to the first: 4 of the same lines with a different type, the 3 lines below, and an interest line that the first read missed.
      - On 8 Sep the first import swapped two amounts: #1055 ₹150 and #1056 ₹100. The HDFC alerts and the second import (#1098, #1099) agree on ₹100 for ref …803877 and ₹150 for ref …947151.
      - #1100 (₹10,000, 13 Sep) copies one of the two ₹10,000 transfers from a family member in the first import.
    - **Why the importer let them in:** its match (`find_reconcilable_transaction`) requires the same type label. Fixing that is an open item.
  - **Rules** ([`dedupe.py`](file:///home/rkushwaha8610/personal-finance-agent/app/dedupe.py)). Only rows with the same date and amount are compared, and for each pair the first rule that matches decides:
    - An *import* is a file (the row's first `assets.gcs_uri`) plus the batch's exact `created_at`. A batch is one database transaction, so all its rows share `CURRENT_TIMESTAMP`. One file imported twice therefore counts as two imports.
    - **keep:** two rows of one import (two real payments), or rows whose 12-digit reference numbers differ.
    - **remove:** rows of two imports that cover the same dates, with the same type and some shared wording or reference.
      - "Cover the same dates" means at least 3 date + amount matches, and at least 50% of the smaller import's rows in the shared date range. The type is left out on purpose.
      - Also removed when one row says TRANSFER, the other expense or income, and the wording or reference matches ("labelled differently").
    - **review:**
      - overlapping imports that disagree (expense vs income, or nothing in common);
      - both legs of a transfer (the same reference in two accounts' statements);
      - other pairs of different types that share a reference or wording;
      - similar descriptions with no file or reference to prove it (hand-entered rows);
      - two copies that the owner edited.
    - **remove:** the same reference number (one payment imported twice), or a Gmail alert together with its statement line.
    - Only 12-digit numbers count as references. The 9–11-digit numbers in narrations are phone and account numbers.
    - **Pairing:** rows that are one payment form a cluster, built from remove pairs (reference matches first, then the closest wording). A cluster never holds two rows that can't be one payment (same import, different references, unrelated types), so two imports pair one-to-one.
    - **Which row stays:** one the owner edited, then one with a reference number, then statement > other > alert > receipt > hand-entered, then the oldest. If the kept row's category is only "Other", it takes the first removed copy's category and type.
  - **Apply** (one database transaction):
    - creates `transactions_removed` if it is missing, then re-runs `SECURITY_HARDENING_SQL` (RLS on, API roles revoked);
    - refuses a plan that was already applied;
    - locks every listed row and checks its date, amount and description against the plan (any change → nothing is applied, exit 2);
    - copies each removed row in full (JSON), with the kept row's old label;
    - points the removed row's files (`assets`, the only table that references transactions) at the kept row;
    - relabels the kept row if the plan says so (never a row the owner edited), then deletes the removed row.
  - **Undo** restores the rows, their files and the kept rows' labels. Asks for the Supabase project ref unless `--yes`.
  - **Plan CSV:** one row per pair with `decision` (remove / review / keep), the rule, both rows, and `set_category` / `set_type`. Changing `review` to `remove` accepts a pair. Only `remove` rows are applied, and the file is validated first.
  - **Verified:**
    - Unit tests 21/21; full backend suite 60/60.
    - Integration on a throwaway Postgres 17 restored from the 2026-10-18 backup (917 rows):
      - 71 rows removed, 71 files re-pointed, 28 kept rows relabelled (9 type changes);
      - the file count is unchanged, no file points at a missing row, and RLS is on for `transactions_removed`;
      - a second dry run finds 0 to remove, and re-applying is refused (exit 2);
      - undo is byte-identical (md5 over every transactions and assets row).
    - Live dry runs, read-only:
      - 11:20 UTC: 959 rows, 59 to remove, 19 to review.
      - 11:30 UTC (`dedupe-20261019T113059Z`): 953 rows, **53 to remove** (35 from the two July Kotak statements, 13 from the double `July 25 to Aug 25` import, 5 from the double September import), 17 kept rows relabelled, 19 to review, 40 kept as separate payments.
      - Between the two runs, the owner deleted #5–#8, #708 and #709 by hand (one of each of six pairs). A plan made before that would be refused on apply.
  - **Owner's decisions** (reply "apply" at 11:34 UTC to the recommendations):
    - **September HDFC alerts that were the HDFC leg of a transfer to the owner's Kotak account** (same reference) were removed, because the Kotak row already records each transfer as TRANSFER.
      - #1020, #1011, #980, #970 and #969 were labelled EXPENSE.
      - #1010 and #1000 were labelled INCOME (₹230).
    - **Two SBI legs:** alerts #1019 (EXPENSE ₹3,000) and #1018 (INCOME ₹5) removed; the SBI rows #1040 and #1041 became TRANSFER · Transfer.
    - **Same purchase or transfer recorded twice:** #82 (receipt, its photo now on #782), #959 and #940 removed.
    - **Swapped amounts:** #1055 and #1056 removed through two manual plan rows (kept: #1099 and #1098). Pairing by amount keeps the drift check valid.
    - **Not carried over:** "Bonus" from #73 onto #109; #109 stays "Other".
    - **Left alone:** #449/#451, #612/#613, #51/#747, #53/#749, #758/#55, and #772/#69 (open question to the owner: is "ELECTRI BILL JULY" on HDFC the arrival of the Kotak transfer, or the bill payment?).
  - **Applied (11:36 UTC):**
    - **Backup first:** `~/backups/personal-finance-agent/2026-10-19/personal_public_2026-10-19T1135Z.dump`, verified by a test restore (11 tables, 1,587 rows; 953 transactions, 466 assets).
    - **Plan:** a fresh dry run (`dedupe-20261019T113602Z`, identical to the 11:30 one), then the approved copy `dedupe-20261019T113602Z-approved.csv`. It differs from the dry run in exactly 15 rows: 12 accepted, 2 manual, 1 carry-over dropped.
    - **Result:** 67 rows removed (53 automatic, 12 accepted on review, 2 manual), 67 files re-pointed, 18 kept rows relabelled. Exit 0, no warnings.
  - **Verified after the apply** (read-only):
    - transactions 953 → 886; assets still 466, none pointing at a missing row;
    - `transactions_removed` holds 67 rows, every kept row exists, and none of the removed ids is still in `transactions`;
    - RLS is on for `transactions_removed`, with no `anon` or `authenticated` grants; 18 old labels are saved for undo;
    - spot checks: #1040, #1041, #720, #735, #1071, #1090, #1098 and #1099 are TRANSFER · Transfer (`is_self_transfer` true), #724 is Investment, #83 is Food, #109 is Other;
    - a fresh dry run (`dedupe-20261019T113802Z`) finds 0 to remove. The 6 pairs left alone are still listed for review; #32/#735 are now kept as separate payments (both are transfers, with different wording).
    - **September 2026 totals:**
      - expense down 66%;
      - income down 44%;
      - transfer down 3% (amounts redacted).
      - The difference was mostly transfers between the owner's own accounts counted as spending or income, plus the re-import's copies.
  - **Undo:** from the repo root, run `APP_ENV=personal venv/bin/python scripts/dedupe_transactions.py --undo dedupe-20261019T113602Z-approved`. It restores all 67 rows, their files and the 18 labels.
  - **Open:**
    - **Prevention in the importer:** match on date + amount + reference regardless of type, and warn when an upload overlaps an earlier import of the same account and period. Needs a backend deploy.
    - **Commit:** the three new files are not committed yet.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md). New nodes and rows: `scripts/dedupe_transactions.py` → `app/dedupe.py` → `app/database.py` (`extract_merchant_tokens`, `is_alert_description`); the script → `SECURITY_HARDENING_SQL`, `app/config.py` and the new `transactions_removed` table; `tests/test_dedupe.py` → `app/dedupe.py`.

---

### [DEC-033] March 2026 Budget Upload Saved as March 2023: 60 Rows Moved to 2026, Cause Is the PDF Import Path (`personal-finance-agent`)
- **Date**: `2026-10-19 11:08 UTC`
- **Scope**: `personal-finance-agent` (personal database, data fix only; no code changed)
- **Files Changed**:
  - No project files. Data: 60 rows in the personal `transactions` table (#1109–#1168), columns `transaction_date` and `row_signature` only.
  - One-off scripts, outside the repo:
    - [`fix_march_2026_dates.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/2f21a412-e5ca-48aa-af49-cf8b769c75b9/scratch/fix_march_2026_dates.py): dry run by default, `--apply`, `--undo`
    - [`inspect_march.py`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/2f21a412-e5ca-48aa-af49-cf8b769c75b9/scratch/inspect_march.py): read-only
  - Undo data in [`~/backups/personal-finance-agent/2026-10-19/`](file:///home/rkushwaha8610/backups/personal-finance-agent/2026-10-19) (folder 700, files 600):
    - `fix_march_2026_dates_20261019T110725Z_undo.csv`: id, old and new date and signature
    - `..._rows_before.json`: the 60 rows in full before the change
- **Decision & Rationale**:
  - **What happened.** At 10:55:34 UTC the owner uploaded `Monthly budget March 2026.pdf` on the live site.
    - All 60 rows were saved with the right day and month but the year 2023 (2023-03-01 … 2023-03-31).
    - March 2026 showed only one older row, and a "March 2023" appeared.
  - **Cause** (still in the code):
    - **Wrong reader.** The file was a PDF, so `process_file` sent it to `StatementExtractor`, the bank-statement reader. The earlier budget months (May 2025 – April 2026) were `.xlsx` files and went to the spreadsheet importer.
    - **No reference date.** The PDF prompt ([`statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py#L49-L63)) asks for each date as `YYYY-MM-DD` but tells the model nothing else: no file name (which says March 2026), no statement period, no today's date. Gemini sees only the pages.
    - **No year on the page, most likely.** A PDF holds only the printed text, and the budget template prints some dates without a year.
      - In `data/Monthly budget May 2025.xlsx` the Expenses dates use `M/d/yyyy`, but the Income dates use `m/d/yy` and `d-mmmm` ("1-May").
      - Day and month were right on all 60 rows, and every row got the same wrong year. That points to a year the model couldn't read and filled in from its training-era default.
      - The PDF itself couldn't be checked, because `/upload` deletes the file once it has been processed.
    - **Excel doesn't have this problem.** An Excel date cell stores the full date even when the display hides the year, and pandas passes that full date to the model.
    - **Nothing checks the year.** `ReceiptData` accepts any valid date, and the confidence score covers the category, not the date. These rows scored 95–100, so none was flagged for review.
    - **Receipts:** the receipt prompt ([`extractor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/extractor.py)) has the same gap.
  - **Fix (data):**
    - **Selection by source and date:** rows whose `assets` entry is this upload and whose date is in March 2023. That gave exactly 60, and no other 2023 rows exist.
    - **Per row:** `transaction_date` 2023-03-DD → 2026-03-DD, and `row_signature` recomputed with `compute_row_signature` (the signature includes the date).
      - Before the change, every stored signature matched a recomputation from the row's old values, so the recipe is the one the importer used.
      - With the new signature, a later correct import of March 2026 matches these rows instead of adding copies.
    - **Unchanged on purpose:** category, amount, description, confidence, review flag and `user_locked`. The lock isn't set because only the date was corrected, and imports never change a row's date.
    - **Checks before writing:**
      - none of the new signatures already exists
      - no triggers on `transactions`
      - none of the 60 rows had been edited by the owner
      - the one row already in March 2026 (#603, HungerBox ₹40 on 5 March, imported with the April 2026 sheet on 2026-09-21) has no twin among the 60 (same date, amount and type), so the move creates no duplicates
    - **Safety:**
      - One transaction holding a `SHARE ROW EXCLUSIVE` lock on `transactions`: imports and the duplicate cleanup wait, reads continue.
      - Each update also requires the row's old date and signature to still match.
      - The undo files are written before anything changes.
    - **Why in the database:** the app's own edit route (`PUT /transactions/{id}`) can't change a date.
  - **Verified (11:08 UTC):**
    - 60/60 rows are in March 2026 with correct signatures.
    - No rows are left in 2023, March 2026 now has 61 rows, and the total is still 959.
  - **Undo:** from `app/`, run `APP_ENV=personal PYTHONPATH=. ../venv/bin/python <fix_march_2026_dates.py> --undo <undo.csv>`.
  - **Duplicate cleanup (DEC-032, in progress in another session):**
    - Its 11:02 plan lists one pair from this upload (#1143/#1144, two different ₹2,000 ETF purchases) as `keep`, under 2023-03-20.
    - `--apply` checks only the `remove` rows, so the plan still applies; a fresh dry run will show 2026-03-20.
    - DEC-032 is reserved by that work, so this entry is DEC-033.
  - **Open** (needs the owner's go-ahead, then a backend deploy):
    - Give the PDF and receipt prompts today's date and the file name, and tell the model to take a missing year from the statement period or file name, never to guess it.
    - After extraction, flag rows whose dates fall outside the period named in the file, or after today, instead of saving them silently.
    - Until then: upload budget sheets as `.xlsx`, and don't upload this PDF again (it would add the 60 rows to March 2023 a second time).
- **Flow Impact**: None. No project file was added, removed or changed, so [`flow.md`](file:///home/rkushwaha8610/flow.md) is unchanged.

---

### [DEC-031] Phase 1 Complete: Owner's Google Sign-In Confirmed; Local Backend Stopped, Playwright Browsers Removed (`personal-finance-agent` / `workspace`)
- **Date**: `2026-10-19 10:06 UTC`
- **Scope**: `personal-finance-agent` (plan) and `workspace` (Cloud Shell environment)
- **Files Changed**:
  - [`docs/OVERHAUL_PLAN.md`](file:///home/rkushwaha8610/personal-finance-agent/docs/OVERHAUL_PLAN.md): the last Phase 1 item is ticked and the status says Phase 1 is complete (commit `b2f5712`)
  - Environment only, no project files: deleted `~/.cache/ms-playwright` (681 MB)
- **Decision & Rationale**:
  - **Personal path confirmed by the owner.** At about 10:04 UTC the owner signed in with Google on the live site, and the app loaded all 917 personal transactions from the Tokyo backend.
    - That was the one path the tests couldn't cover (they use the demo login), so "After sign-in, the app talks to the backend that matches the account" is ticked.
    - Phase 1 is complete. Next is Phase 2 (Connect Gmail).
  - **Local demo backend stopped**, at the owner's request.
    - Only the uvicorn process on port 8081 was signalled (`kill <pid>`; it shut down cleanly, and the port is free).
    - `./start_services.sh stop` wasn't used: its `pkill -f vite` pattern matched unrelated processes at that moment (an agent telemetry hook and the checking shell, whose command lines contained the text).
  - **Playwright browsers deleted**, as planned in DEC-027. The home disk went from 72% used (1.3 GB free) to 57% (2.0 GB free).
    - Before the next Playwright run, reinstall with `npx playwright install chromium` (see [FRONTEND_GUIDE.md](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/FRONTEND_GUIDE.md)). It goes back to `~/.cache/ms-playwright` unless `PLAYWRIGHT_BROWSERS_PATH` points somewhere else, such as `/tmp`.
- **Flow Impact**: None. No file was added or removed and no import or call path changed, so [`flow.md`](file:///home/rkushwaha8610/flow.md) is unchanged.

---

### [DEC-030] Phase 1 Frontend Shipped: Committed, Live on Firebase Hosting, Cache Headers Fixed (`personal-finance-agent`)
- **Date**: `2026-10-19 09:58 UTC`
- **Scope**: `personal-finance-agent` (frontend release, Hosting config, plan)
- **Files Changed**:
  - [`app/frontend/firebase.json`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/firebase.json): Cache-Control rules
  - [`app/frontend/FRONTEND_GUIDE.md`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/FRONTEND_GUIDE.md): the deploy section describes the caching
  - [`docs/OVERHAUL_PLAN.md`](file:///home/rkushwaha8610/personal-finance-agent/docs/OVERHAUL_PLAN.md): Phase 1 frontend items ticked, status line
- **Decision & Rationale**:
  - **Commits** on local `main` (the repo has no remote, so nothing was pushed):
    - `6c44846`: the Phase 1 frontend from DEC-029, 84 files, all under `app/frontend/`. The staged diff was scanned for secrets. The only hit is the public Firebase web config in `src/config/firebase.ts`, which the brief says to commit.
    - `58d62d7`: Hosting cache headers (below).
    - `71660af`: plan update.
  - **Deploy:**
    - Built from `6c44846` with no `VITE_*` overrides and no source maps, then `firebase deploy --only hosting` at 09:51 UTC. Redeployed at 09:56 with the header fix, using the same build.
    - It replaced the pre-Phase-1 build (`index-4PAgn90K.js`), which couldn't work with the sign-in-protected backends.
    - Checked first: both Cloud Run backends answer CORS preflights from `web.app` and `firebaseapp.com` with a matching `Access-Control-Allow-Origin`.
    - Rollback: Firebase console → Hosting → release history.
  - **Cache headers fix (the problem existed before Phase 1).**
    - The old rule set `no-cache` only on the literal path `/index.html`, so `/` and every deep link got Firebase's default `max-age=3600`.
    - Effect: a browser that loaded the site within the hour before a deploy keeps the old page and requests asset files the new release doesn't have. The `**` rewrite answers those requests with HTML, and the app loads blank.
    - Now every path gets `no-cache`, and `/assets/**` gets `public, max-age=31536000, immutable`, because Vite puts a content hash in every asset name.
    - When two rules set the same header, Firebase applies the later one. Confirmed on a one-hour preview channel before the live release; the channel was deleted afterwards.
    - Edge case: if a tab open across a deploy asks for an asset that no longer exists, the HTML fallback is cached under that dead URL. That only matters after a rollback to that exact release, and a hard reload fixes it.
  - **Live check (09:57 UTC)** on https://personal-finance-agent-507010.web.app with the real demo login. The smoke test is a scratch script, not in the repo. All 14 checks passed:
    - Signed out, a deep link shows only the sign-in screen and makes no backend calls.
    - Demo sign-in reaches the shell and keeps the deep link; the demo banner shows.
    - Analytics and Transactions load from the Seoul demo backend; Bank Sync shows the Gmail-off panel; sign-out works.
    - Every backend call went to the demo backend with a Bearer token. None failed, there were no Gmail calls and no console errors.
    - The AI screens weren't opened, so no demo AI quota was used.
    - Headers: `/`, deep links and firebaseapp.com get `no-cache`; `/assets/*` gets the long cache. No source maps are served.
  - **Plan:** "React + TypeScript" and "app shell" are ticked. "The app talks to the backend that matches the account" stays open until the owner signs in with Google, because the personal path isn't covered by any test. (Confirmed and ticked in DEC-031.)
- **Flow Impact**: None. No file was added or removed and no import or call path changed, so [`flow.md`](file:///home/rkushwaha8610/flow.md) is unchanged.

---

### [DEC-029] Phase 1 Frontend: React + TypeScript Shell with Firebase Sign-In, Finished After the Disk-Full Stop, Playwright Green (`personal-finance-agent`)
- **Date**: `2026-10-19 09:45 UTC`
- **Scope**: `personal-finance-agent` (frontend, Phase 1; committed as `6c44846` and live since 2026-10-19 09:51 UTC, see DEC-030)
- **Files Changed** (all under [`app/frontend/`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend)):
  - **Built by the Phase 1 frontend subagent (`61e62b6e`, 2026-10-18 19:54–20:43 UTC).** Recorded here because that session and the orchestrator (`a68682e5`) both stopped when the disk filled (DEC-027).
    - New: [`src/main.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/main.tsx), [`src/App.tsx`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/App.tsx), `src/auth/`, `src/session/`, `src/shell/`, `src/legacy/`, `src/config/`, `src/theme/`, `src/ui/`, `src/api/*.ts`, [`vite.config.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/vite.config.ts), [`tailwind.config.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/tailwind.config.ts), [`tailwind.legacy-theme.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/tailwind.legacy-theme.ts), [`eslint.config.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/eslint.config.js), `tsconfig*.json`, [`playwright.config.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/playwright.config.ts), `tests/e2e/`, `.gitignore`
    - Rewritten: [`index.html`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/index.html), [`src/styles.css`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/styles.css), [`.env.example`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env.example), `package.json` / `package-lock.json`
    - Small integration hooks (2–56 changed lines each) in the legacy screens: `src/components/{ledger,analytics,budgets,bankSync,modal,insights,recurring,upload}.js`
    - Removed: `src/main.js`, `src/api/*.js`, `vite.config.js`, `tailwind.config.js`, and the untracked `.env` with the dead tunnel URL
  - **Changed in this session:**
    - [`package.json`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/package.json) / `package-lock.json`: `@playwright/test` 1.64.0 → 1.64.1 (exact pin)
    - [`tests/e2e/helpers.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/tests/e2e/helpers.ts), [`tests/e2e/shell.mocked.spec.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/tests/e2e/shell.mocked.spec.ts), [`tests/e2e/shell.integration.spec.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/tests/e2e/shell.integration.spec.ts)
    - [`src/styles.css`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/styles.css): one rule for the flagged-row badge
    - [`FRONTEND_GUIDE.md`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/FRONTEND_GUIDE.md): rewritten for the new structure (brief task 8)
- **Decision & Rationale**:
  - **Resumed from the files on disk, in this session.** The orchestrator conversation can't be resumed, so the work was finished here, using the subagent's transcript for context.
    - Cloud Shell had restarted overnight, so the port-8081 backend was gone. It was restarted with `./start_services.sh demo` (same Seoul demo database, 1,449 sample transactions).
    - DEC-028's `credentials: 'same-origin'` change in [`client.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/client.ts) is included and was tested with everything else.
  - **What the shell does:**
    - **Sign-in gate.** Signed-out visitors see only the sign-in screen, and the URL is kept for after sign-in. The options are "Continue with Google" (popup; `authDomain` stays on firebaseapp.com) and the demo email/password form, with clear messages for each Firebase error.
    - **Backend routing** ([`backends.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/config/backends.ts)): the token's `sign_in_provider` decides. `google.com` goes to the personal backend (Tokyo) and `password` to the demo backend (Seoul).
      - No email address is in the bundle. Checked: the only addresses are public bank sender addresses in the legacy Bank Sync screen, plus a placeholder.
      - `VITE_API_BASE_URL` overrides the routing on the dev server only, including same-origin proxy mode for Cloud Shell web preview.
    - **`GET /me` gate:** the shell loads only after `/me` succeeds. A 403 shows "This account doesn't have access" and other errors show a retry screen.
    - **One API client** ([`client.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/client.ts)), the app's only `fetch`:
      - adds the Bearer token to every request
      - 401: one forced token refresh, then sign-out with a message
      - 403: re-checks access
      - 429 and other errors carry the backend `detail`, so the legacy toasts show it
      - statement PDFs and clarification audio come as authenticated blobs
      - the old tunnel → Cloud Run failover and the us-central1 fallback are gone
    - **Shell layout:**
      - sidebar with the 7 screens
      - top bar: period picker (months from `/transactions/months`), search (goes to `/transactions?q=`), theme toggle, user menu with the environment badge and the demo AI quota
      - demo banner, and a "Gmail sync is turned off in the demo" panel on Bank Sync
    - **URL state:** one path per screen plus `?month`, `?q` and `?view`/`year`. Back/forward and deep links work. No `?month` means All time.
    - **Legacy screens** stay unchanged inside the shell:
      - Each mounts on its first visit and is never re-initialised; visibility follows the route. Lazy mounting avoids about 10 requests at startup, and the AI-backed forecast is fetched only when its screen is opened.
      - [`bridge.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/legacy/bridge.ts) takes over the old `main.js` duties: month sync, refresh after uploads, and the ledger's cross-links.
      - The ledger, analytics and budgets loaders now queue a reload instead of dropping a month change made mid-load.
    - **Theme:** light by default, with a dark toggle saved in `localStorage`. Colours are CSS-variable tokens. The legacy screens' dark-first palette is remapped inside `.legacy-scope` by [`tailwind.legacy-theme.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/tailwind.legacy-theme.ts).
    - **Dependency versions** (newest releases were checked, then chosen for compatibility):
      - React 19.3.1 and react-router 7.18.4 (the brief asked for v7).
      - firebase 12.19.0 rather than 13.0, which was 11 days old.
      - TypeScript 5.9.3: typescript-eslint 8.71 supports only TypeScript below 6.1, and it can't use TypeScript 7's native compiler.
      - ESLint 9.39.5 with typescript-eslint 8.71.2 (strict type-checked rules), react-hooks 7.1.1, react-refresh 0.5.7.
      - @vitejs/plugin-react 5.2.0 (supports Vite 5) and lucide-react 1.59.0.
      - Vite 5.4.21 and Tailwind 3.4.19 kept, as the brief asked.
  - **Fixed in this session:**
    - **Typecheck and build had broken once the tests were added.** `@axe-core/playwright`'s peer dependency had installed a second `playwright-core` (1.64.1) next to the 1.64.0 copy inside `@playwright/test`, and the two `Page` types clash under `exactOptionalPropertyTypes`.
      - Fix: `@playwright/test` 1.64.1 (exact pin), which leaves a single `playwright-core`.
      - Both versions use Chromium build 1248, already in `~/.cache/ms-playwright`, so nothing was downloaded.
    - **Lint error:** leftover no-op lines in the 403 test.
    - **401 and 403 tests:** they now submit the form with the new `submitDemoSignIn` helper and check the end screen directly. Before, each waited and swallowed a 45 s timeout for a shell that never appears.
    - **Audio test** deleted `speechSynthesis` from `Window.prototype`, but Chromium defines it on `window` itself (checked with a probe), so the server-audio fallback never ran. It now deletes it from `window`, and the audio request carries the Bearer token.
    - **Screenshots capped at 8,000 px.**
      - The full sample ledger had been a 1440 × 69,924 px, 13.7 MB PNG per theme, and saving one of the full-page screenshots was the write that hit ENOSPC. The ledger screenshot is now 1.6 MB.
      - The every-page test's timeout went from 240 s to 360 s. The interrupted run was 3.2 min into its 4 min budget when it failed; today's runs took 3.0 min.
      - Bank Sync now gets the same settle wait as the other screens. Before, its screenshot and scan caught the sidebar mid-transition, with two items highlighted.
    - **Legacy contrast:** the "Needs AI Clarification" badge pulsed to 50% opacity, so its text fell below AA for part of every cycle (axe caught 9 rows at 4.47:1 mid-fade in dark mode).
      - Fix: a CSS-only rule in [`styles.css`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/styles.css) that stops the pulse on that badge only.
      - Skeleton rows and the banner icon keep their pulse. The amber row, the badge and the banner still mark these rows.
  - **Verified (2026-10-19 09:42 UTC):**
    - `npm run typecheck`, `npm run lint` (0 warnings) and `npm run build` pass, with no `.map` files in `dist`.
    - Playwright integration suite against the local demo backend: 4/4 pass (3.4 min), with real Firebase demo sign-in.
    - Mocked suite: 9/9 pass (23 s). It covers the sign-in gate, auth messages, demo routing with a Bearer token, demo badge and quota, the Bank Sync panel, 401 retry then sign-out, 403 no-access, the 429 detail toast, inert XSS payloads, and authenticated PDF and audio requests.
    - Contrast (axe, WCAG AA, every screen in light and dark): **0 violations** in the shell and in the legacy screens.
      - axe left some elements as "needs review" (it couldn't determine their background): 996 / 1,014 on the ledger and 72 on Forecast.
      - The screenshots were checked by eye: readable in both themes. Emoji show as boxes only because Cloud Shell's headless Chromium has no emoji font.
    - Bundle: no personal or demo account email; Firestore and gRPC code are not included.
  - **Open items:**
    - **Shipped in DEC-030** (commit, deploy, live demo check). The owner's Google sign-in, the one path the tests couldn't cover, was confirmed in DEC-031.
    - **Ledger row cap (existed before Phase 1).** `GET /transactions` defaults to `limit=1000`, so the All-time ledger loads only the newest 1,000 of the demo's 1,449 rows, and its totals cover only those, while Analytics counts all 1,449. Personal data has 917 rows today. Fix it in the API or the ledger (Phase 3 or sooner).
    - **npm audit.**
      - 4 high findings in production dependencies, all in the Firestore/gRPC chain of the `firebase` package. They are not in the bundle, because the app imports only `firebase/app` and `firebase/auth`. Switching to `@firebase/app` + `@firebase/auth` would clear them.
      - The rest are dev tooling only (Vite 5 dev server, esbuild, Tailwind 3's watcher and globbing). Clearing them needs Vite 6+ and Tailwind 4.
    - **Disk.** `~/.cache/ms-playwright` (681 MB) was deleted in DEC-031. Reinstall with `npx playwright install chromium` before the next test run.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md). The frontend graph and the file-to-file table now show `main.tsx` → `App.tsx` → auth gate → `SessionProvider` (backend routing, `/me`) → `AppShell` → legacy host (dynamic import of `legacy/index.js` → `components/*.js`) → `api/client.ts` → Cloud Run. The Playwright suites are included, and the removed `main.js`, `api/*.js` and failover rows are gone.

---

### [DEC-028] Cloud Shell Web Preview: API Calls Send Same-Origin Cookies (`personal-finance-agent`)
- **Date**: `2026-10-18 21:24 UTC`
- **Scope**: `personal-finance-agent` (frontend, Phase 1 work in progress, not committed)
- **Files Changed**:
  - [`app/frontend/src/api/client.ts`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/client.ts): `credentials: 'omit'` → `'same-origin'`
- **Decision & Rationale**:
  - **Symptom.** In a local preview through Cloud Shell Web Preview, signing in with the demo account showed "Couldn't connect to your workspace / Can't reach the server".
  - **Cause 1, port 5174** (Vite dev server, same-origin proxy mode: `VITE_API_BASE_URL=` and `BACKEND_URL=http://127.0.0.1:8081`).
    - The Cloud Shell preview proxy authenticates every request with its own cookie.
    - The API client sent requests with `credentials: 'omit'`, so the proxy answered `302` to its `/_cloudshell/forwardAuthCookie` page.
    - fetch can't follow that cross-origin redirect and reports it as a network error. Confirmed with a cookie-less request to the preview URL.
  - **Fix: `'same-origin'`.**
    - Same-origin calls (Vite proxy mode) now carry the proxy's cookie.
    - Production is unchanged. The app calls the Cloud Run backends cross-origin, where `'same-origin'` sends no cookies, exactly like `'omit'`.
    - No CSRF exposure: the backend authenticates only with the Bearer token (DEC-025), so cookies grant nothing.
    - This is the only `fetch` in the app; every API call goes through `client.ts`.
  - **Cause 2, port 8081** (not a bug, so no change).
    - The local demo backend serves the production build from `dist/`.
    - That build ignores `VITE_API_BASE_URL` and sends demo sign-ins to the Cloud Run demo backend, whose CORS correctly rejects `*.cloudshell.dev` origins (preflight `400`).
    - For a local preview, use the Vite dev server instead.
  - **Verified:** Vite serves the updated module, and `tsc --noEmit` on the app reported no errors in `client.ts`.
  - **Note for the Phase 1 frontend session:** this edit sits on top of its uncommitted work, so review it with that work.
- **Flow Impact**: None. No file was added or removed and no import or call path changed, so [`flow.md`](file:///home/rkushwaha8610/flow.md) is unchanged.

---

### [DEC-027] Cloud Shell Disk Full: Caches Cleared and Moved to the Temporary Disk, Fresh Verified Personal-Database Backup (`workspace`)
- **Date**: `2026-10-18 21:13 UTC`
- **Scope**: `workspace` (Cloud Shell environment), plus a backup of the `personal-finance-agent` database
- **Files Changed**:
  - [`~/.npmrc`](file:///home/rkushwaha8610/.npmrc) (new): `cache=/tmp/npm-cache`
  - [`~/.config/pip/pip.conf`](file:///home/rkushwaha8610/.config/pip/pip.conf) (new): `cache-dir = /tmp/pip-cache`
  - Deleted download caches only: `~/.npm` (949 MB) and `~/.cache/pip` (337 MB)
  - Data, not code: [`~/backups/personal-finance-agent/2026-10-18/`](file:///home/rkushwaha8610/backups/personal-finance-agent/2026-10-18) gained `personal_public_2026-10-18T2112Z.dump` and its `.manifest.txt`. They were made by the one-off [`backup_personal_db.sh`](file:///home/rkushwaha8610/.gemini/antigravity-cli/brain/8d2d23c0-8f21-4284-9491-27b51d815927/scratch/backup_personal_db.sh).
- **Decision & Rationale**:
  - **What happened.** The Cloud Shell home disk (4.8 GB) reached 100% at 20:43:41 UTC.
    - The Phase 1 frontend subagent (`61e62b6e`) was running its Playwright integration test; a full-page screenshot write failed with `ENOSPC`.
    - agy could no longer save its own state, and the "start phase 1" session (`a68682e5`) stopped.
    - What filled the disk: the npm cache (947 MB), the Playwright browsers (681 MB), the pip cache (337 MB), on top of the two Python venvs (935 MB and 682 MB).
    - No source file was damaged: all were last written by 20:42. The only casualty was one 0-byte screenshot (`test-results/screens/dark-bank-sync.png`).
    - Test result: 1 passed, 1 failed only because of `ENOSPC`, 2 didn't run.
  - **Freed 1.3 GB, deleting only download caches.** Disk use went from 100% (18 MB free) to 72% (1.3 GB free).
  - **The caches now live on the VM's temporary disk** (`/tmp`, an overlay with about 32 GB free that is cleared when Cloud Shell restarts), so they can't fill the persistent 5 GB home disk again.
    - Set in npm's and pip's own config files rather than environment variables. Both tools read these files on every run, so the change also applies to agent sessions that are already running and to non-interactive shells, which don't source `.bashrc`.
    - Verified: `npm config get cache` returns `/tmp/npm-cache`. All three pips (system, `personal-finance-agent/venv`, `ats-resume-tailor/backend/.venv`) report `/tmp/pip-cache`.
    - Cost: packages are re-downloaded after a Cloud Shell restart.
  - **The Playwright browsers (681 MB) stay on the home disk for now.** The frontend tests need them, and Playwright only supports an environment variable, which running sessions wouldn't pick up. Delete `~/.cache/ms-playwright` once frontend testing is done.
  - **Fresh personal-database backup.** The user deleted the old pre-Phase-0 backup (about 18 MB) at 20:52 to make room.
    - Contents: the `public` schema of the personal project, made with `pg_dump` 17 in Docker (the server is Postgres 17), custom format, 96 KB.
    - Verified by a test restore into a throwaway `postgres:17`: exact row counts matched the live database, 11 tables and 1,512 rows, including 917 transactions and 427 assets.
    - The only restore warning was the harmless "schema public already exists".
    - Password handling: the password is read from Secret Manager and never printed or put on a command line. It is checked to be non-empty before connecting, because an empty password would be a failed login, and failed logins count toward Supabase's IP ban.
    - Storage: folder `700`, files `600`, with a sha256 manifest.
    - Sanity check: the personal database's only `app_meta` row is `budgets_seeded`, not the demo marker, so the nightly demo reset can never treat it as the demo database (DEC-026).
    - Kept as a one-off script rather than a project script. If regular backups are wanted, promote it to `scripts/` and consider copying backups off Cloud Shell.
- **Flow Impact**: None. These are environment config and data only; no code files or file-to-file connections changed, so [`flow.md`](file:///home/rkushwaha8610/flow.md) is unchanged.

---

### [DEC-026] Phase 1 Demo Data: Deterministic 18-Month Sample Ledger, Guarded Nightly Reset (`personal-finance-agent`)
- **Date**: `2026-10-18 20:34 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/demo_seed.py`](file:///home/rkushwaha8610/personal-finance-agent/app/demo_seed.py) (new)
  - [`scripts/demo_reset.py`](file:///home/rkushwaha8610/personal-finance-agent/scripts/demo_reset.py) (new)
  - [`tests/test_demo_seed.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_demo_seed.py) (new)
- **Decision & Rationale**:
  - **Sample data is generated by code, not copied.** About 18 months for a fictional software engineer in Bengaluru:
    - salary with an April hike, an annual bonus and savings interest
    - rent, UPI food and grocery orders, street-vendor payments, bills and subscriptions
    - cabs and fuel, SIPs, a recurring deposit, a loan EMI and insurance
    - card bill payments and transfers between own accounts, recorded as `TRANSFER` so totals don't double count
    - refunds, an annual trip, and a festive spike around Diwali
  - **Realistic narrations, fictional people.** Merchants are real brands so the narrations read like Indian bank statements. People, accounts and the employer are fictional; no personal data is copied.
  - **Stable from night to night.** Randomness is keyed by calendar date, so each nightly reset keeps the same history plus the new day. The review items, the possible duplicate and the user-corrected rows are placed relative to today, so every screen always has something current to show.
  - **What it contains** (for 2026-10-19):
    - 1,449 rows: 1,376 expense, 35 income, 38 transfer; May 2025 to Oct 2026.
    - 9 rows need review, including exactly one possible duplicate: the same ₹2,499 Amazon purchase as a Gmail alert and as a statement line.
    - 5 rows are user-locked.
    - Income is about ₹1.3–1.45 L a month, with bonus months. Oct 2025 (Diwali) is the only deficit month.
    - 8 demo budgets, chosen so a typical month shows on-track, warning and exceeded categories together.
  - **The reset wipes tables, so it has three guards:**
    - `APP_ENV=demo`.
    - The target is not the personal Supabase project, checked in the config, in `DATABASE_URL` and in the engine URL.
    - The database itself holds the marker `app_meta(environment = demo)`. This is checked in the same transaction as the `TRUNCATE`.
    - The marker can only be written into a database with no user data, so the personal database can never be marked.
    - Everything runs in one transaction with lock and statement timeouts; a failure leaves the old data intact. Categories keep their fixed ids (the API falls back to id 5, Other).
  - **Faster creation (user request).** The generator runs in about 1 s, and rows go in as 200-row multi-row inserts.
    - Full load from Cloud Shell: 15 s.
    - On Cloud Run, next to the database: 1.2 s, with the whole Scheduler request taking 2 s.
    - The build agent was stopped mid-tuning, and its planned Docker integration suite was replaced with fast tests that need no database.
  - **Bootstrapped and verified.** The Seoul demo database was marked and loaded with `scripts/demo_reset.py --mark-demo`.
    - Via the deployed demo backend with the real demo login: the months list, the budget mix (Sep 2026: 95 % overall, with warning and exceeded categories) and the review list all look right.
    - Refusals were confirmed for the personal environment and for a demo environment pointed at the personal project.
    - Cloud Scheduler's nightly `/internal/demo-reset` calls the same `reset_demo_data()`. Triggering `pfa-demo-reset-nightly` by hand at 20:36 UTC returned 200.
  - **Tests:** 7 new tests, all passing:
    - the data is the same for the same day
    - 18 months, nothing in the future
    - valid rows with unique signatures
    - review items, one duplicate, the Diwali spike
    - guard refusals
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md):
  - `api.py` → `demo_seed.py` (`/internal/demo-reset`)
  - `scripts/demo_reset.py` → `demo_seed.py` + `DatabaseManager`
  - `demo_seed.py` → `config.py` + `database.py`

---

### [DEC-025] Phase 1 Backend & Infrastructure: Sign-In on Every Route, Two Environments on Cloud Run, Scheduler, Tunnel Retired (`personal-finance-agent`)
- **Date**: `2026-10-18 20:18 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/auth.py`](file:///home/rkushwaha8610/personal-finance-agent/app/auth.py) (new)
  - [`app/statement_store.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_store.py) (new)
  - [`app/config.py`](file:///home/rkushwaha8610/personal-finance-agent/app/config.py)
  - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
  - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
  - [`app/gmail_sync.py`](file:///home/rkushwaha8610/personal-finance-agent/app/gmail_sync.py)
  - [`app/advisor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/advisor.py)
  - [`app/mcp/ledger_server.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py)
  - [`app/secret_store.py`](file:///home/rkushwaha8610/personal-finance-agent/app/secret_store.py)
  - [`Dockerfile`](file:///home/rkushwaha8610/personal-finance-agent/Dockerfile)
  - [`.dockerignore`](file:///home/rkushwaha8610/personal-finance-agent/.dockerignore)
  - [`.gcloudignore`](file:///home/rkushwaha8610/personal-finance-agent/.gcloudignore)
  - [`requirements.txt`](file:///home/rkushwaha8610/personal-finance-agent/requirements.txt)
  - [`scripts/phase1_infra.sh`](file:///home/rkushwaha8610/personal-finance-agent/scripts/phase1_infra.sh) (new)
  - [`scripts/deploy_backend.sh`](file:///home/rkushwaha8610/personal-finance-agent/scripts/deploy_backend.sh) (new)
  - [`scripts/phase1_move_statements_to_gcs.py`](file:///home/rkushwaha8610/personal-finance-agent/scripts/phase1_move_statements_to_gcs.py) (new)
  - [`scripts/set_secret.sh`](file:///home/rkushwaha8610/personal-finance-agent/scripts/set_secret.sh)
  - [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh)
  - [`tests/conftest.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/conftest.py) (new)
  - [`tests/test_auth.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_auth.py) (new)
  - [`tests/test_fde_integration.py`](file:///home/rkushwaha8610/personal-finance-agent/tests/test_fde_integration.py)
  - [`README.md`](file:///home/rkushwaha8610/personal-finance-agent/README.md)
- **Decision & Rationale**:
  - **The exposure was closed first.** The user chose to stop the Cloudflare tunnel at 19:48 UTC.
    - The tunnel address was in the public site's code and the API had no sign-in, so anyone could fetch transactions with a plain HTTP request; CORS only stops browsers.
    - The old address now returns HTTP 530. The app stayed offline until the switch-over.
  - **Sign-in on every route** ([`auth.py`](file:///home/rkushwaha8610/personal-finance-agent/app/auth.py)).
    - An ASGI middleware protects every route by default. Only `/health` and the locally served SPA files are public, so a new route can't be left open by accident.
    - Firebase ID tokens are verified with `google-auth`, which the app already uses, so no Admin SDK was added. The checks:
      - RS256 only
      - audience, issuer, expiry and `auth_time`
      - Google's certificates are cached per their max-age and refreshed once on key rotation
      - verified tokens are cached for up to 5 minutes
    - Each backend has an allowlist pinned to a sign-in method, and the email address must be verified:
      - personal: the owner's Google account only
      - demo: the demo email/password login only
    - Responses: 401 means sign in again; 403 means "this account doesn't have access".
    - The middleware sits inside CORS, so browser preflights work and the frontend can read 401/403 responses.
    - The `/docs` and `/openapi.json` pages are off in production.
  - **Scheduler-only routes.** `/internal/*` accepts only Cloud Scheduler's Google-signed OIDC token for `pfa-scheduler@…`, whose audience is the service URL. User tokens are rejected there.
  - **Two environments from one image.**
    - `APP_ENV` selects the database defaults (project ref, pooler host, password secret).
    - A demo backend refuses to start if it is pointed at the personal database.
    - The demo turns the Gmail routes off (403).
    - The demo has daily AI limits: chat 40, insight reports 10, uploads 10, voice 40, eval 3. They are counted atomically in an `ai_usage` table per India-time day, so the cap holds across Cloud Run instances, and over-limit calls get a 429. Uploads count only after they pass validation.
    - New `GET /me` returns the account, the environment, which features are on, and today's usage. All "who is this user" logic lives in one place, which keeps multi-user possible later.
  - **The demo database is in Seoul (`ap-northeast-2`), not Tokyo** as the plan assumed, so each Cloud Run service sits next to its own database:
    - `finance-backend` in `asia-northeast1` (Tokyo)
    - `finance-backend-demo` in `asia-northeast3` (Seoul)
    - The single image lives in Artifact Registry (`asia-northeast1/pfa`).
  - **The schema bootstraps itself.**
    - `_ensure_schema()` can now create the full schema on an empty database; the demo database was set up this way, with constraint names identical to the personal database.
    - New tables: `budgets`, `ai_usage` and `app_meta`.
    - The existing hardening turned on RLS for the new tables automatically. Verified in the personal database: 0 `anon`/`authenticated` grants on them.
  - **Budgets are stored in the database.** They used to live in server memory and would be lost on Cloud Run. They are seeded once per database from the old defaults; the live server's values were checked first and matched. The MCP budget tool now reads the same table.
  - **The Docker import bug is fixed.** `app.`-prefixed imports (`advisor.py`, `gmail_sync.py`, `api.py`, `ledger_server.py`) are replaced with plain imports, because `app/` is the import root everywhere.
    - Tests import the same way through `tests/conftest.py`, so no module is loaded twice under two names.
    - Added `pypdf`, which Gmail statement handling imports but the image lacked, and `xlrd`, needed for `.xls` uploads.
    - The `vertexai.generative_models` pin was checked: it still works on the pinned `google-cloud-aiplatform==2.0.1`. The move to `google-genai` is still planned.
  - **Hardened build.**
    - The image has no compiler or apt packages and runs as a non-root user.
    - `.gcloudignore` is now an allowlist: Cloud Build receives exactly 32 backend files, so `data/`, `docs/` screenshots, `*.pptx` and `.env` files are never uploaded.
  - **Least-privilege service accounts** replace the default Compute account, which has Editor rights.
    - `pfa-backend-personal`: Vertex AI user; reads the personal database password, the Gmail App Password and the statement passwords; can add versions to the last two; object admin on the statements bucket only.
    - `pfa-backend-demo`: Vertex AI user; reads the demo database password only.
    - `pfa-scheduler`: OIDC identity only.
    - `secret_store.set_secret()` now adds a version first and creates the secret only on 404, so the backend needs no project-wide permission to create secrets.
  - **Statement PDFs are in a private bucket** (user's choice), [`statement_store.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_store.py).
    - Bucket `personal-finance-agent-507010-statements`: uniform access and public access prevention.
    - Gmail exports and unlocks work on temp copies, and asset rows point at the stored copy.
    - Locations are only resolved inside the configured bucket.
    - The 8 PDFs (6 locked) were migrated with a dry run first, the database rows were repointed, and a rerun is a no-op.
    - A real PDF download from the bucket was verified.
  - **Cloud Scheduler jobs:**
    - `pfa-gmail-sync-hourly`, at :05 every hour (India time). It respects the interval set on Bank Sync, currently 24 h.
    - `pfa-demo-reset-nightly`, at 03:30 India time. It also keeps the free demo project from pausing.
    - The old in-process Gmail worker is now opt-in (`ENABLE_GMAIL_BACKGROUND_WORKER=1`), so two servers never sync at once.
  - **The tunnel and watchdog are retired.**
    - [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh) is now a local development launcher: personal backend on :8080, demo on :8081, optional Vite. It no longer touches `.env` or deploys anything.
    - No cron entries exist; this was verified.
    - The Phase 0 server in Cloud Shell was stopped.
    - Deploys are explicit: [`scripts/deploy_backend.sh build`](file:///home/rkushwaha8610/personal-finance-agent/scripts/deploy_backend.sh).
  - **The old us-central1 `finance-backend` service and its image repo were deleted** (user-approved).
    - It had been broken since the password change and ran as the Editor-level default account.
    - The old site's failover was still sending it traffic, and it made 56 failed Supabase logins with the old password between 19:30 and 20:13 UTC, risking a Supabase IP ban.
    - `cloud-run-source-deploy` (4 images with the old hardcoded password baked in) is gone.
  - **Firebase.**
    - The user had already enabled Google and Email/Password sign-in.
    - Registered the web app "Personal Finance AI (web)".
    - Created the demo user (`rkushwaha8610+pfademo@gmail.com`, email verified) with a generated password, written straight to `~/.config/personal-finance-agent/demo-login.txt` (folder 700, file 600) and never printed.
  - **Verified:**
    - 32 of 32 tests pass, including 27 new auth tests with real RS256 tokens. They cover:
      - bad signature, audience or issuer; expired; unsigned; unknown key
      - not allowlisted, unverified email, wrong sign-in method
      - a walk over every route that confirms each one returns 401 without a token
      - CORS headers on 401 responses, and preflights
      - scheduler token checks and the demo gates
    - Deployed smoke tests on both services: `/health` 200; `/me` and `/transactions` 401.
    - With a real demo sign-in:
      - demo `/me` returns 200 (environment `demo`, limits shown)
      - demo Gmail routes return 403
      - demo budgets are seeded
      - the personal backend returns 403 to the demo account
    - A Scheduler run against `/internal/gmail-sync` returned 200 ("not due").
  - **Known:**
    - Cold start was about 18 s with min-instances 0. The Vertex AI SDK now loads on first use rather than at startup, which cut cold start to about 7 s on both services (measured on revisions 00002; local import time went from 5.4 s to 1.8 s).
    - If the remaining wait matters, one warm personal instance costs roughly US$10–15 a month.
    - Firebase public sign-up is still on; the backend allowlist is what lets people in. Turning sign-up off after the owner's first Google sign-in is optional defence in depth.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md):
  - new auth, environment, Scheduler and Cloud Run nodes
  - `statement_store` → Cloud Storage
  - `start_services.sh` is now a local launcher (tunnel removed)
  - updated connection rows

---

### [DEC-024] Phase 0 Frontend: Bank Sync Crash, XSS Hardening, Single Review Rule, Small-Bug Sweep (`personal-finance-agent`)
- **Date**: `2026-10-18 19:03 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed** (commit `a2f34b7`):
  - [`src/utils/markdown.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/utils/markdown.js) (new)
  - [`toast.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/toast.js)
  - [`insights.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/insights.js)
  - [`bankSync.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/bankSync.js)
  - [`ledger.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js)
  - [`budgets.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/budgets.js)
  - [`analytics.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/analytics.js)
  - [`modal.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/modal.js)
  - [`upload.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/upload.js)
  - [`vite.config.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/vite.config.js)
  - `package.json` / `package-lock.json` (+ `dompurify`)
- **Decision & Rationale**:
  - **Bank Sync works again.** All 10 `toast(...)` calls are now `toast.show(...)`, and a missing `bank_name` no longer crashes the statement list.
  - **XSS**:
    - Toasts set message text with `textContent`.
    - AI markdown is rendered only through `renderMarkdown()`, which runs `marked` and then DOMPurify with a strict tag/attribute allowlist. DOMPurify's default config was tested and still let through remote-fetching `<svg><image>`/`<video poster>` and id/class/data attributes, which enable data leaks and DOM clobbering.
    - Email subjects, file names, sync status, settings values and month/year labels are escaped with `escapeHtml`.
  - **One review rule.** The ledger uses `requires_human_review` only, matching the backend (DEC-023).
  - **Small bugs**:
    - CSV export downloads as a Blob, so it is no longer cut off at `#`, and includes a UTF-8 BOM so ₹ displays correctly.
    - The CSV export neutralises formula injection: text cells starting with `= + - @` are prefixed with a quote.
    - The "+ + Add Entry" button is fixed.
    - The budget form shows ₹ and accepts amounts like 2550.
    - The budgets caption now reads "spent of budgeted".
    - The upload fallback import is fixed, and the fallback no longer reports false success.
    - CSV was removed from the upload picker, because the backend rejects it.
    - Source maps are no longer published.
  - **The light theme was not made the default.** A WCAG contrast scan found 8–46 text elements below 3:1 on every page: dark surfaces aren't remapped, and text-white becomes dark-on-dark. Light-first therefore moves to the Phase 1 design-token pass, which matches the "if it holds up" condition in the plan.
  - **Verification.**
    - A Playwright harness passed 40 of 40 checks on synthetic data, including XSS payloads in every field.
    - `npm run build` passes, and no `.map` files are emitted.
  - The work was done by a parallel frontend agent, limited to `app/frontend/`. Its diff was reviewed before committing; the formula-injection, null-guard and plural fixes were added during that review.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md) with:
  - the `utils/markdown.js` node and edges (insights → markdown → marked/dompurify)
  - the components → `utils/dom.js` (`escapeHtml`) edge
  - upload.js → `getTransactions`

---

### [DEC-023] Phase 0 Backend: Secrets to Secret Manager, Database Lockdown, Correctness & Upload Fixes (`personal-finance-agent`)
- **Date**: `2026-10-18 18:48 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/secret_store.py`](file:///home/rkushwaha8610/personal-finance-agent/app/secret_store.py) (new)
  - [`app/config.py`](file:///home/rkushwaha8610/personal-finance-agent/app/config.py)
  - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
  - [`app/gmail_sync.py`](file:///home/rkushwaha8610/personal-finance-agent/app/gmail_sync.py)
  - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
  - [`app/models.py`](file:///home/rkushwaha8610/personal-finance-agent/app/models.py)
  - [`app/ai_importer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py)
  - [`scripts/set_secret.sh`](file:///home/rkushwaha8610/personal-finance-agent/scripts/set_secret.sh) (new)
  - [`scripts/phase0_db_migration.py`](file:///home/rkushwaha8610/personal-finance-agent/scripts/phase0_db_migration.py) (new)
  - [`.gitignore`](file:///home/rkushwaha8610/personal-finance-agent/.gitignore) (new), git repository initialized (commits `7ee5564`, `947b713`)
  - Project root `.env` deleted: it held the Gmail App Password in plain text.
- **Decision & Rationale**:
  - **Safety net first.**
    - Backed up the project files and a full `pg_dump` (via Docker `postgres:17`, because Cloud Shell's `pg_dump` 16 refuses a Postgres 17 server) to `~/backups/personal-finance-agent/2026-10-18/` (folder `chmod 700`, files `chmod 600`).
    - Then `git init`, with the first commit made only *after* credentials left the code, so they never enter history.
  - **Secrets (DEC-022 decision 6).**
    - New `secret_store.py` reads the env var first, then Secret Manager, through the REST API on the existing `google-auth` dependency, so the Docker image needs no new library.
    - The hardcoded Supabase password and URL were removed from `config.py`; `get_db_password()` resolves `DB_PASS` or `pfa-db-password-personal`.
    - The Gmail App Password (`pfa-gmail-app-password`) and statement PDF passwords (`pfa-statement-passwords`) moved out of Supabase. The settings API no longer returns the password's last 4 characters.
    - The app refuses to start without the database secret.
    - Secret names now hold passwords instead of full URLs, because the password contains URL-special characters and URL-encoding would be error-prone during rotation.
    - `.gitignore` also excludes `data/`, screenshots and slides showing real transactions, so a future public push can't leak personal data.
  - **Database lockdown.** `SECURITY_HARDENING_SQL` runs on every startup:
    - turns on RLS for all `public` tables
    - revokes table, sequence and function privileges and the default privileges from `anon`/`authenticated`
    - makes the `financial_summary` view `security_invoker`, because views otherwise bypass RLS
    - It is idempotent, so the Phase 1 demo database is secured automatically.
    - The backend connects as the table owner `postgres` (BYPASSRLS), which was verified unaffected. Verified that `anon` and `authenticated` now get `permission denied` on tables and the view.
  - **Correctness.**
    - The `ReceiptData` validator normalizes confidence 0–1 to 0–100 at the single point every extractor passes through. This was the root cause of the review backlog.
    - The `transactions.user_locked` column is set by edits, voice/text resolutions and manual entries. Reconciliation `UPDATE`s and upserts skip locked rows; when an upsert is skipped, `_existing_id_for_signature()` handles the empty `RETURNING`.
    - There is now one review definition (`requires_human_review`) for badges and lists, and edits reset confidence to 100.
  - **API hardening.**
    - CORS is an explicit allowlist (the Firebase Hosting origins and localhost; `CORS_ALLOWED_ORIGINS` overrides it), replacing `allow_origin_regex=".*"` with credentials.
    - Uploads have a type allowlist, a 25 MB streaming cap, and a random per-upload folder with a sanitized base name, which fixes path traversal. The folder is always cleaned up.
  - **Migration applied 2026-10-18 (dry-run reviewed first).**
    - 474 bug-only review flags cleared, taking the review count from 738 to 264. Rows in "Other" stay in review.
    - 503 confidence values rescaled.
    - Plain-text Gmail password wiped from Supabase.
    - RLS on 8 of 8 tables; 126 `anon`/`authenticated` grants revoked.
  - **Verified.**
    - 5 of 5 existing tests pass.
    - Smoke test on port 8081:
      - Review badge totals equal the review-list size.
      - Gmail settings expose no password characters.
      - A `.txt` upload gets a 400; `../../etc/evil.pdf` is saved as `evil.pdf`.
      - CORS preflight from an unknown origin gets a 400; the Firebase origin gets an allow header.
    - A scheduled Gmail sync during the test logged in over IMAP using the Secret Manager password and imported 1 alert.
  - **Follow-up, 2026-10-18 19:28 UTC: passwords changed and verified.**
    - **Who did what:** the user changed both passwords and stored them with `scripts/set_secret.sh`, so the values never passed through chat.
    - **Database password:** Secret Manager now holds v2, and v1 is disabled. The backend restarted and connected with v2.
    - **Old Cloud Run fallback:** it still has the old password baked in, so it now returns HTTP 500 and can no longer read data.
    - **Gmail App Password:** Secret Manager now holds v3, and v1–v2 are disabled. v3 logs in to IMAP, while the old exposed password is rejected (`AUTHENTICATIONFAILED`).
    - **How the old password was checked:** the old values were read in memory from the backups and never printed. The old database password was deliberately not tried, to avoid Supabase failed-login IP bans; one password per role means a working v2 already proves it is dead.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md):
  - new Secrets subgraph (`secret_store.py`, Secret Manager, `scripts/*`)
  - `gmail_sync.py` route and connections
  - updated rows for `api.py`, `database.py` and `config.py`
  - removed the stale `account_aggregator.py` node, a file that never existed

---

### [DEC-022] Record Overhaul Decisions & Publish the Phased Overhaul Plan (`personal-finance-agent`)
- **Date**: `2026-10-18 18:20 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`docs/OVERHAUL_PLAN.md`](file:///home/rkushwaha8610/personal-finance-agent/docs/OVERHAUL_PLAN.md) (new)
  - [`docs/UX_ENTERPRISE_AUDIT.md`](file:///home/rkushwaha8610/personal-finance-agent/docs/UX_ENTERPRISE_AUDIT.md) (§8 marked as answered, with a link to the plan)
- **Decision & Rationale**: The user answered the open questions from §8 of the audit. Decisions recorded:
  1. **Frontend: React + TypeScript**, migrated screen by screen.
     - A new React shell hosts the existing `init*Component(container)` screens until each one is replaced.
     - Chosen over vanilla JS because maintained libraries (TanStack, shadcn/ui on Radix) provide the enterprise grid, accessible components, URL state and request caching.
     - TypeScript also catches the kind of bug that broke Bank Sync.
  2. **Sign-in: Firebase Auth.** Google sign-in for the owner, email/password for the demo account, and backend ID-token checks with an allowlist on every route. Chosen over Cloud IAP, which targets internal company tools and is awkward for an SPA calling the API from another origin.
  3. **Theme:** light first, with dark as an option.
  4. **Users: two isolated environments now.**
     - **Personal:** real data and Gmail.
     - **Demo:** synthetic data, shareable credentials, a nightly reset, Gmail turned off and AI usage limits.
     - Implemented as two Cloud Run services built from one image, each with its own service account and its own Supabase project.
     - Multi-user is explicitly deferred. The plan avoids new single-row assumptions so it can be added later.
  5. **Bank data: Gmail only.**
     - Account Aggregator is dropped because only RBI-, SEBI-, IRDAI- or PFRDA-regulated entities can be data users (FIUs).
     - IMAP with an App Password will be replaced by the read-only Gmail API (`gmail.readonly` OAuth).
     - The OAuth app will be published "In production" under Google's personal-use exemption, because Testing mode expires refresh tokens every 7 days.
  6. **Secrets: Google Secret Manager.** Never in Supabase, never in code, and nothing sensitive on the Cloud Shell disk.
     - The user first asked for storage in Cloud Shell, then chose Secret Manager after seeing the trade-offs:
       - the always-on demo must run on Cloud Run, which can't read Cloud Shell files
       - Cloud Shell deletes `$HOME` after 120 days without use
  7. **Hosting:** Cloud Run becomes the only backend. The Cloudflare tunnel and cron watchdog will be retired.
  - Documentation only. No application code has changed yet.
- **Flow Impact**: None yet, because this is documentation only. [`flow.md`](file:///home/rkushwaha8610/flow.md) will be updated as each phase changes runtime connections (auth checks, the two Cloud Run services, Secret Manager and the Gmail API).

---

### [DEC-021] Publish UX Pain-Point & Enterprise UI Audit to `docs/` (`personal-finance-agent`)
- **Date**: `2026-10-18 17:51 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`docs/UX_ENTERPRISE_AUDIT.md`](file:///home/rkushwaha8610/personal-finance-agent/docs/UX_ENTERPRISE_AUDIT.md) (new)
  - [`docs/assets/ux-audit/`](file:///home/rkushwaha8610/personal-finance-agent/docs/assets/ux-audit/) (new: 4 current-UI screenshots + 2 AI-generated target-design mockups)
- **Decision & Rationale**:
  - The user pain-point & enterprise-UI audit was originally saved only as an agent artifact outside the repository, so it was not discoverable in the project. Published a portable copy into `docs/` next to the other architecture docs, following the existing conventions (UPPER_SNAKE_CASE file name, absolute `file://` source links).
  - Converted viewer-specific carousel blocks to standard Markdown, removed agent-only `conversation://` links, and stored images under `docs/assets/ux-audit/` with relative paths so the doc renders in VS Code and on GitHub.
  - Documentation only. The audit records the findings (security exposure, the confidence-scale bug behind the 80.6% review backlog, UI gaps, and a phased roadmap); no application fixes have been applied yet.
- **Flow Impact**: None. Documentation only, with no runtime import, call or routing changes, so the existing flow in [`flow.md`](file:///home/rkushwaha8610/flow.md) is unchanged.

---

### [DEC-020] Add FDE 4-Pillar Architectural Docstrings & Codebase Walkthrough Comments (`personal-finance-agent`)
- **Date**: `2026-10-18 10:06 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
  - [`app/main.py`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py)
  - [`app/config.py`](file:///home/rkushwaha8610/personal-finance-agent/app/config.py)
  - [`app/models.py`](file:///home/rkushwaha8610/personal-finance-agent/app/models.py)
  - [`app/statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py)
  - [`app/extractor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/extractor.py)
  - [`app/ai_importer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py)
  - [`app/voice_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py)
  - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
- **Decision & Rationale**:
  - Added comprehensive module-level and class/method architectural docstrings across all core backend modules, explicitly mapping each file's responsibilities to Google Cloud's **4 FDE Pillars (`Build, Govern, Scale, Optimize`)** while preserving all existing inline comments and docstrings.
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-019] Rebuild 8-Slide Pitch Deck in Light Mode with System Architecture & GCP Services Slides (`personal-finance-agent`)
- **Date**: `2026-10-17 21:51 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`Personal_Finance_AI_Pitch_Deck.pptx`](file:///home/rkushwaha8610/personal-finance-agent/Personal_Finance_AI_Pitch_Deck.pptx)
  - [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env)
  - [`app/frontend/.deployed_tunnel_url`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.deployed_tunnel_url)
- **Decision & Rationale**:
  - Rebuilt [`Personal_Finance_AI_Pitch_Deck.pptx`](file:///home/rkushwaha8610/personal-finance-agent/Personal_Finance_AI_Pitch_Deck.pptx) in an executive **Light Mode** palette (`#F8FAFC` alabaster backgrounds, crisp `#FFFFFF` cards with `#CBD5E1` borders, `#0F172A` navy typography, and pastel header banners) and expanded the presentation from 5 slides to **8 widescreen (`16:9`) slides**:
    - **Slides 1–5**: Product Overview, 4-Stage Progressive Disclosure Vault, Voice-Resolved Monthly Ledger, Multi-Horizon Future Cost Predictor (`GET /forecast`), and Live Gmail Bank Sync & AI CFO Studio.
    - **Slide 6 (New)**: 5-Tier Cloud-Native, Zero-Trust Multi-Agent System Architecture diagram built with native PowerPoint vector shapes.
    - **Slide 7 (New)**: Google Cloud Services Matrix detailing how each of the 10 GCP services works and why it is essential.
    - **Slide 8 (New)**: End-to-End Service Execution Pipelines (Multimodal Ingestion + SHA-256 Deduplication, Conversational Voice Clarification Loop, and Stateless AI CFO + MCP Plane).
  - Also restarted the FastAPI backend (`PID 2795` on port `8080`) and Cloudflare tunnel (`https://louis-accomplished-aberdeen-customs.trycloudflare.com`), automatically syncing [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env) and redeploying to Firebase Hosting (`https://personal-finance-agent-507010.web.app`).
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-018] Create System Architecture & Google Cloud Services Pitch Deck Guide (`personal-finance-agent`)
- **Date**: `2026-10-17 21:45 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`docs/GCP_ARCHITECTURE_PITCH_GUIDE.md`](file:///home/rkushwaha8610/personal-finance-agent/docs/GCP_ARCHITECTURE_PITCH_GUIDE.md)
- **Decision & Rationale**:
  - Created a comprehensive architecture and Google Cloud services reference document ([`docs/GCP_ARCHITECTURE_PITCH_GUIDE.md`](file:///home/rkushwaha8610/personal-finance-agent/docs/GCP_ARCHITECTURE_PITCH_GUIDE.md)) and corresponding interactive artifact detailing the 5-tier application architecture, all 10 Google Cloud services used (`Vertex AI Gemini 2.5 Flash/Pro/Flash-Lite`, `Vertex AI Context Caching`, `Google Cloud Run`, `Firebase Hosting`, `Cloud Speech-to-Text`, `Cloud Text-to-Speech Journey Voices`, `Cloud DLP & Model Armor`, `Workload Identity Federation`, `Gmail API / Bank Sync`, `Cloud Build`), service-level Mermaid sequence/flow diagrams, and slide-ready blocks for the pitch deck.
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-017] Create Executive Pitch Presentation & Live Application Screenshot Suite (`personal-finance-agent`)
- **Date**: `2026-10-17 20:19 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`Personal_Finance_AI_Pitch_Deck.pptx`](file:///home/rkushwaha8610/personal-finance-agent/Personal_Finance_AI_Pitch_Deck.pptx)
- **Decision & Rationale**:
  - Captured 8 high-resolution (`1600x1000` @ 2x Retina with full-color SVG emoji rendering) screenshots of the live application running on port `8080` across all 4 progressive disclosure vault stages (`Home Overview`, `Year Vault`, `Month Statement Folders`, `Monthly Statement Ledger`) and core intelligence modules (`Future Cost Predictor`, `Visual Analytics`, `AI CFO Studio`, and `Live Bank Sync & PDFs`).
  - Generated both an interactive Markdown Pitch Presentation artifact (`pitch_presentation.md`) and a downloadable widescreen `16:9` PowerPoint presentation ([`Personal_Finance_AI_Pitch_Deck.pptx`](file:///home/rkushwaha8610/personal-finance-agent/Personal_Finance_AI_Pitch_Deck.pptx)).
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-016] Start FastAPI on Port 8080 & Sync Active Cloudflare Tunnel to Firebase Hosting (`personal-finance-agent`)
- **Date**: `2026-10-17 20:10 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env)
  - [`app/frontend/.deployed_tunnel_url`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.deployed_tunnel_url)
- **Decision & Rationale**:
  - Following a Cloud Shell session restart at `19:44 UTC`, executed [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh) to start the FastAPI server (`PID 2443` on port `8080`) and Cloudflare tunnel (`https://fun-wide-marcus-trout.trycloudflare.com`), automatically syncing [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env), rebuilding the production bundle, and redeploying to Firebase Hosting (`https://personal-finance-agent-507010.web.app`).
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-015] Restore Active Tunnel & Sync Progressive Disclosure Vault to Firebase Hosting (`personal-finance-agent`)
- **Date**: `2026-10-17 18:33 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env)
  - [`app/frontend/.deployed_tunnel_url`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.deployed_tunnel_url)
- **Decision & Rationale**:
  - Following a Cloud Shell session restart, ran [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh) to bring the FastAPI backend (`PID 2586` on port `8080`) and Cloudflare tunnel (`https://curious-female-doe-geneva.trycloudflare.com`) back online, syncing [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env) and redeploying the 4-stage `Home Overview → Year → Month → Monthly Ledger` SPA to Firebase Hosting (`https://personal-finance-agent-507010.web.app`).
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-014] Progressive Disclosure Vault Architecture: `Home Overview → Year → Month → Monthly Ledger` (`personal-finance-agent`)
- **Date**: `2026-10-16 16:26 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
  - [`app/frontend/src/components/ledger.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js)
  - [`app/frontend/src/main.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/main.js)
  - [`app/frontend/index.html`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/index.html)
- **Decision & Rationale**:
  - **Problem**: Rendering all historical transactions immediately on the homepage caused visual/cognitive overload and unnecessary initial DOM/API work.
  - **Enterprise Solution (4-Stage Progressive Drill-Down)**:
    1. **Stage 0 (`home` — Executive Command Center)**: Landing view shows lifetime portfolio KPIs, a prominent **"Explore Transactions by Year & Month"** primary card, a **1-click Latest Month (`Oct 2026`) shortcut**, an action queue for `⚠️ Needs Review` items, a global search escape hatch across all years, and module launchpad tiles.
    2. **Stage 1 (`years` — Select Year Vault)**: Displays interactive Year cards (`2026`, `2025`, ...) with annual inflow, outflow, net savings %, active months pills, and review counts.
    3. **Stage 2 (`months` — Select Month Statement Folder)**: Selecting a year reveals Month cards (`October 2026`, `September 2026`, ...) with monthly cashflow metrics, top spending category badge, spend-to-income progress bar, and review status.
    4. **Stage 3 (`ledger` — Focused Monthly Statement Ledger)**: Clicking a month loads and renders only that month's transactions, complete with an interactive breadcrumb bar (`🏠 Home / 📅 All Years / 📂 2026 / October 2026`) and 1-click **`◀ Older Month | Newer Month ▶`** stepper buttons.
  - **Backend Enrichment (`GET /transactions/months`)**: Upgraded `get_transaction_months()` in [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py#L215-L344) to return pre-aggregated `years` rollups, per-month `review_count`, and `top_category`.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md) to document the 4-stage `Home → Year → Month → Ledger` navigation flow between `main.js`, `ledger.js`, and `app/api.py`.

---

### [DEC-013] Restart Services After Cloud Shell Reset & Sync Active Tunnel URL (`personal-finance-agent`)
- **Date**: `2026-10-16 16:05 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env)
  - [`app/frontend/.deployed_tunnel_url`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.deployed_tunnel_url)
- **Decision & Rationale**:
  - Following a Google Cloud Shell container reset at `15:46 UTC`, executed [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh) to restart the local FastAPI backend (`PID 3768` on port `8080`) and Cloudflare tunnel (`https://formats-mixed-grace-chance.trycloudflare.com`), automatically updating [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env), rebuilding the SPA bundle, and deploying to Firebase Hosting (`https://personal-finance-agent-507010.web.app`).
  - Verified `/api/gmail/settings` (`configured: true`) and `/api/gmail/statements` (`total_pdfs: 8`) are live via the new tunnel.
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-012] Always-Online Backend Architecture: Cloud Run Auto-Failover & Self-Healing Cron/Boot Watchdog (`personal-finance-agent`)
- **Date**: `2026-10-16 12:26 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/src/api/config.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.js)
  - [`app/frontend/src/api/client.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/client.js)
  - [`app/frontend/src/api/transactions.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/transactions.js)
  - [`app/frontend/src/main.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/main.js)
  - [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh)
- **Decision & Rationale**:
  - **Root Cause**: A VM reboot stopped the background `uvicorn` and `cloudflared` processes, and the SPA had no runtime failover when the primary Cloudflare tunnel URL became unreachable.
  - **Fix (2-Layer High Availability)**:
    1. **Automatic Frontend Failover to Cloud Run**: Added `CLOUD_RUN_FALLBACK_URL` (`https://finance-backend-328452160072.us-central1.run.app`), `getActiveApiBaseUrl()`, and `setActiveApiBaseUrl()` in [`config.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.js). Updated [`client.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/client.js) and [`main.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/main.js) (`checkApiHealth`) to automatically fail over to the always-online Cloud Run service if the primary tunnel returns network or gateway (`502`/`503`/`504`/`530`) errors, and automatically switch back once the tunnel is healthy again.
    2. **Boot + 2-Minute Self-Healing Watchdog (`crontab` + `start_services.sh`)**: Upgraded [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh) with `flock` locking, local + public tunnel `/health` verification, automatic tunnel restart + `.env` sync + Firebase Hosting redeploy, and registered `@reboot` and `*/2 * * * *` cron entries so services stay online across VM reboots.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md) to reflect the `start_services.sh` watchdog and `client.js` dual-backend failover between the local FastAPI tunnel and Cloud Run.

---

### [DEC-011] Auto-Sync Cloudflare Tunnel URL to Frontend `.env` & Firebase Hosting (`personal-finance-agent`)
- **Date**: `2026-10-16 10:48 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env)
  - [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh)
- **Decision & Rationale**:
  - Ephemeral Cloudflare tunnels (`trycloudflare.com`) generate a new subdomain each time the service restarts, causing the static SPA on Firebase Hosting (`https://personal-finance-agent-507010.web.app/`) to point to a stale tunnel URL (`https://franchise-location-blessed-danny.trycloudflare.com`).
  - Updated [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env) to the active tunnel URL (`https://waters-assumption-utah-funny.trycloudflare.com`), rebuilt the SPA bundle, and redeployed to Firebase Hosting.
  - Enhanced [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh) so that whenever a new Cloudflare tunnel URL is assigned, it automatically updates [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env), builds the Vite bundle, and deploys to Firebase Hosting.
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-010] Add Multi-Horizon Future Cost Predictor (`/forecast`) & Clean Up Header UI (`personal-finance-agent`)
- **Date**: `2026-10-15 23:09 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
  - [`app/frontend/src/api/config.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.js)
  - [`app/frontend/src/api/transactions.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/transactions.js)
  - [`app/frontend/src/components/recurring.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/recurring.js)
  - [`app/frontend/index.html`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/index.html)
  - [`app/frontend/src/main.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/main.js)
- **Decision & Rationale**:
  - **Header & Status Icon Cleanup**:
    1. Removed the `"Playground"` badge alongside the app title in [`app/frontend/index.html`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/index.html) and renamed the brand header to `"Personal Finance AI"`.
    2. Replaced the `"FastAPI Online"` text pill in [`index.html`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/index.html) and [`main.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/main.js) (`checkApiHealth()`) with a compact pulsing status dot (`bg-emerald-500` when online, `bg-rose-500` when offline).
  - **Multi-Horizon Future Cost Predictor & Indian Household Preparedness Planner (`GET /forecast`)**:
    1. Added `get_future_cost_forecast()` (`GET /forecast`) in [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py#L858-L1150) that analyzes historical transactions across **Previous 7 Days**, **Previous Month (`2026-09`)**, and **Same/Next Month of Previous Year (`2025-10` & `2025-11`)**.
    2. Computes a weighted 30-day category forecast (45% Previous Month + 35% Historical Monthly Avg + 20% YoY Seasonal Benchmark), detects unpaid monthly recurring commitments and seasonal spikes from last year, and produces an actionable 4-step Indian household financial preparedness roadmap (7-Day Liquidity Buffer, Remaining Month Forecast, YoY Seasonal Alert, and Weekly/Monthly Sinking Fund Set-Aside).
    3. Upgraded [`RecurringComponent`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/recurring.js) (`🔮 Future Plan` tab) to render the preparedness KPIs, multi-horizon comparison bar, actionable playbook, predicted upcoming bills table, and category-wise forecast & weekly reserve plan alongside the recurring commitments ledger.
- **Flow Impact**: Updated [`flow.md`](file:///home/rkushwaha8610/flow.md) to document `GET /forecast` between `recurring.js`, `transactions.js`, and `app/api.py`.

---

### [DEC-009] Fix Mobile Voice Agent Category Resolution & Cross-Origin POST Routing (`personal-finance-agent`)
- **Date**: `2026-10-15 22:50 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/src/api/transactions.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/transactions.js)
  - [`app/frontend/src/components/modal.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/modal.js)
- **Decision & Rationale**:
  - **Root Causes Identified from `/tmp/personal_finance_api.log`**:
    1. When the mobile client (`2401:4900:adab:c68b:2cbb:baff:fe02:33d8`) opened transaction `#939` on `https://personal-finance-agent-507010.web.app/`, `GET /transactions/939/clarification` succeeded (`200 OK`, lines 232 & 245 of [`/tmp/personal_finance_api.log`](file:///tmp/personal_finance_api.log#L232-L245)) because `getTransactionClarification()` routed through `apiClient()` (`API_BASE_URL`). However, `resolveTransactionCategory()` in [`transactions.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/transactions.js#L206-L231) used raw `fetch('/transactions/939/resolve')` without `API_BASE_URL` (and `API_BASE_URL` wasn't imported in `transactions.js`), sending the `POST` request to Firebase static hosting (`https://personal-finance-agent-507010.web.app/transactions/939/resolve`) instead of the FastAPI backend.
    2. On mobile browsers (`SpeechRecognition` -> `MediaRecorder` fallback), `recognition.onend` fired immediately after `recognition.onerror`, resetting `this.isRecording = false` while `MediaRecorder` was starting, and iOS Safari `audio/mp4` needed explicit MIME support.
  - **Fix**:
    1. Imported `API_BASE_URL` and routed `resolveTransactionCategory()` through `apiClient()` in [`transactions.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/transactions.js#L6-L231) with dynamic mobile audio extension detection (`mp4`/`m4a`/`aac`/`ogg`/`wav`/`webm`).
    2. Fixed the `SpeechRecognition` -> `MediaRecorder` race condition (`fallbackTriggered` guard), added `audio/mp4` support for iOS Safari, and made `#voice-modal-card` scrollable (`max-h-[90vh] overflow-y-auto`) on mobile viewports in [`modal.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/modal.js#L628-L1190).
    3. Rebuilt and deployed `dist` to Firebase Hosting (`https://personal-finance-agent-507010.web.app/`).
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-008] Deploy Updated Playground & FinTech UI to Firebase Hosting (`https://personal-finance-agent-507010.web.app/`)
- **Date**: `2026-10-15 22:16 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/src/api/config.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.js)
  - [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env)
- **Decision & Rationale**:
  - Updated [`app/frontend/src/api/config.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.js) to dynamically detect whether the SPA is served from Firebase Hosting (`*.web.app` / `*.firebaseapp.com`) or directly from the local FastAPI / Cloudflare tunnel origin so a single build works seamlessly on both origins.
  - Updated [`app/frontend/.env`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/.env) (`VITE_API_BASE_URL`) to point `https://personal-finance-agent-507010.web.app/` to the live FastAPI tunnel (`https://franchise-location-blessed-danny.trycloudflare.com`) so all `/api/gmail/statements` endpoints and exported bank statement PDFs work directly from the Firebase Hosting URL.
  - Built and deployed `app/frontend/dist` to Firebase Hosting (`personal-finance-agent-507010`).
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-007] Incorporate Senior FinTech UX/UI & Accessibility Optimizations (`personal-finance-agent`)
- **Date**: `2026-10-15 21:28 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/src/utils/formatters.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/utils/formatters.js)
  - [`app/frontend/src/components/ledger.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js)
  - [`app/frontend/src/components/modal.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/modal.js)
  - [`app/frontend/src/components/insights.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/insights.js)
  - [`app/frontend/src/components/upload.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/upload.js)
  - [`app/frontend/index.html`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/index.html)
  - [`app/frontend/src/styles.css`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/styles.css)
  - [`app/frontend/src/main.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/main.js)
- **Decision & Rationale**:
  - Incorporated 6 enterprise FinTech UX/UI and accessibility enhancements:
    1. **Colorblind-Accessible Currency Prefixing (`+` / `-` / `⇄`) + `tabular-nums`**: Added `formatSignedCurrency()` in [`formatters.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/utils/formatters.js) and applied `tabular-nums` across all numeric columns and KPI cards in [`ledger.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js) so decimals align vertically and transaction direction never relies on color alone.
    2. **Neutralizing "Red Fatigue" on Normal Expenses**: Changed `EXPENSE` badges and outflow text from alarming rose/red to neutral slate (`bg-slate-800/90 text-slate-200`, `text-slate-100`), reserving amber strictly for `⚠️ Needs Review` rows and rose for budget overruns/deficits.
    3. **Dual-Mode "Design for Trust" (`☀️ Trust` Light Mode)**: Added a 1-click `☀️ Trust` light mode (`data-labs-theme="light"`, soft `bg-slate-50` background, white cards, deep navy `text-blue-900` headers, and `bg-amber-50` flagged rows) alongside the 4 Google Labs dark themes.
    4. **AI Provenance Cue (`border-t-4 border-t-violet-500`)**: Added a prominent purple top border to the AI CFO Insights card in [`insights.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/insights.js) to visually distinguish generative AI synthesis from deterministic ledger data.
    5. **1-Click Quick Category Chips in Resolve Modal**: Added 8 instant category pills (`Food`, `Shopping`, `Utilities`, `Investment`, `Transfer`, `Salary`, `Travel`, `Health`) inside `VoiceClarificationModal` in [`modal.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/modal.js) alongside voice and text input.
    6. **AI Agent Progress Bar in Smart Ingestion Zone**: Added an animated progress bar (`"AI Agents processing document..."`) in [`upload.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/upload.js).
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-006] Upgrade Frontend to Dynamic Google Labs Playground UI (`personal-finance-agent`)
- **Date**: `2026-10-15 19:56 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/index.html`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/index.html)
  - [`app/frontend/src/styles.css`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/styles.css)
  - [`app/frontend/src/main.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/main.js)
  - [`app/frontend/src/components/ledger.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/ledger.js)
- **Decision & Rationale**:
  - Upgraded the SPA to a dynamic, flexible **Google Labs Playground (`labs.google/playground`)** architecture:
    1. **Floating Glass Pill Dock & Ambient Mesh Canvas**: Added animated ambient orbs, dot-matrix canvas, `Space Grotesk` / `Plus Jakarta Sans` typography, and ultra-rounded pill controls.
    2. **3 Dynamic Workspace Layout Modes (`▢ Focus` | `◫ Split` | `⊞ Bento`)**: Users can switch on the fly between single-focus view, a side-by-side Split-Screen Playground (primary view on the left + docked AI Studio or Bank Sync on the right), and an All-in-One Bento Canvas.
    3. **Ledger `📋 Table` vs `🎴 Bento Cards` View Toggle**: Added interactive Bento transaction cards with mouse-tracking radial spotlight glow alongside the high-density table view.
    4. **Live Ambient Theme Vibe Switcher & `⌘K` Command Palette**: Added 4 instant color themes (`Electric`, `Emerald`, `Coral`, `Gold`) and a keyboard-driven `⌘K` / `Ctrl+K` command palette.
- **Flow Impact**: Preserves existing component-to-API flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-005] Auto-Update Statement Import UI Without Page Refresh (`personal-finance-agent`)
- **Date**: `2026-10-15 19:23 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/src/api/client.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/client.js)
  - [`app/frontend/src/api/bank.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/bank.js)
  - [`app/frontend/src/components/bankSync.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/bankSync.js)
- **Decision & Rationale**:
  - **Root Cause**: PDF decryption + Gemini 2.5 Flash extraction + Supabase batch upsert takes ~35–45 seconds. Previously, browser/CDN caching on `GET /api/gmail/statements` or 60-second client/tunnel timeouts prevented the card from immediately flipping to `✅ Imported` once the backend finished, requiring a manual page reload.
  - **Fix**:
    1. Added `cache: 'no-store'` in [`client.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/client.js) and cache-busting query params on `fetchExportedStatementPdfs()` in [`bank.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/bank.js), plus extended the timeout to `180s` (`180000ms`).
    2. Added `markStatementImportedOptimistically()` in [`bankSync.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/bankSync.js) so the statement card flips to `✅ Imported (X txns)` the exact millisecond `POST /api/gmail/statements/{id}/unlock` completes, followed by background state refresh across all tabs (`onSyncSuccess`).
    3. Added automatic 3-second polling recovery if a proxy/tunnel timeout occurs while the server is still extracting the PDF.
- **Flow Impact**: No file connection changes; matches [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-004] Rebuild SPA Bundle with Relative API Origin for Live Server & Tunnel (`personal-finance-agent`)
- **Date**: `2026-10-15 19:15 UTC`
- **Scope**: `personal-finance-agent`
- **Files Changed**:
  - [`app/frontend/dist/index.html`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/dist/index.html)
- **Decision & Rationale**:
  - Rebuilt [`app/frontend/dist`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/dist) with `VITE_API_BASE_URL=""` before launching [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh) so the frontend served on port `8080` and via the Cloudflare tunnel talks directly to the local FastAPI backend (which has the 7 exported PDFs on disk in [`data/monthly_statements/`](file:///home/rkushwaha8610/personal-finance-agent/data/monthly_statements)) instead of calling the remote Cloud Run URL.
- **Flow Impact**: No file connection changes; matches existing flow in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-003] Setup Always-On Decision & Code Flow Tracking (`decision.md`, `flow.md`, `AGENTS.md`)
- **Date**: `2026-10-15 19:06 UTC`
- **Scope**: `workspace` (`/home/rkushwaha8610`)
- **Files Created**:
  - [`decision.md`](file:///home/rkushwaha8610/decision.md)
  - [`flow.md`](file:///home/rkushwaha8610/flow.md)
  - [`AGENTS.md`](file:///home/rkushwaha8610/AGENTS.md)
- **Decision & Rationale**:
  - Created [`decision.md`](file:///home/rkushwaha8610/decision.md) to maintain a persistent, chronological audit trail of all engineering decisions and code modifications made by the `agy` agent.
  - Created [`flow.md`](file:///home/rkushwaha8610/flow.md) to document how files execute into one another and how frontend, backend, agents, services, and databases connect across projects.
  - Created [`AGENTS.md`](file:///home/rkushwaha8610/AGENTS.md) at the workspace root with an `@[decision.md](file:///home/rkushwaha8610/decision.md)` include so that Antigravity's rule discovery system keeps `decision.md` active in the agent's context at all times and enforces automatic updates to both `decision.md` and `flow.md` on every code change.
- **Flow Impact**: Initialized full workspace flow documentation in [`flow.md`](file:///home/rkushwaha8610/flow.md).

---

### [DEC-001] Enterprise Personal Finance AI Architecture (`personal-finance-agent`)
- **Date**: `2026-10-13`
- **Scope**: `personal-finance-agent`
- **Key Files**:
  - [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py)
  - [`app/main.py`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py)
  - [`app/extractor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/extractor.py)
  - [`app/statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py)
  - [`app/ai_importer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py)
  - [`app/advisor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/advisor.py)
  - [`app/voice_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py)
  - [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)
  - [`app/security/model_armor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/model_armor.py)
  - [`app/mcp/ledger_server.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py)
- **Decision & Rationale**:
  - **Extension-Based Document Routing**: `process_file()` in [`app/main.py`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py) routes `.pdf` to `StatementExtractor`, images (`.jpg`/`.png`) to `ReceiptExtractor`, and spreadsheets (`.xlsx`/`.xls`) to `AISpreadsheetImporter`.
  - **Confidence Threshold & Human-in-the-Loop**: Any transaction with AI confidence `< 85.0%` or an unrecognized category is automatically flagged with `requires_human_review = True` and resolved through [`app/voice_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py).
  - **Deterministic Deduplication**: Uses SHA-256 file hashes (`ingested_files.file_hash`) and row signatures (`compute_row_signature` in [`app/database.py`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py)) for idempotent uploads.
  - **Stateless Multi-Turn CFO Chat**: Stores chat history on the client (`app/frontend/src/components/insights.js`) and reconstructs Vertex AI `start_chat(history=...)` on each request in [`app/advisor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/advisor.py) for serverless horizontal scaling on Cloud Run.
  - **Zero-Trust Security & Evaluation**: Added inline prompt-injection screening (`ModelArmor`), PII redaction (`dlp.py`), MCP ledger server (`ledger_server.py`), and golden-dataset evaluation (`autorater.py`).
