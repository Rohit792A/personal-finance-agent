# Changelog

Notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The app deploys continuously from `main` rather than in numbered releases, so entries are grouped by milestone and date. `DEC-NNN` numbers point to the full write-up in [decision.md](decision.md).

## [Unreleased]

## 2026-11-02: First public release (DEC-062)

### Added
- Public documentation: this changelog, a rewritten README, LICENSE (all rights reserved), SECURITY, CONTRIBUTING, a Code of Conduct, a backend `.env.example`, and issue and pull request templates.
- GitHub Actions CI runs the backend unit tests and the frontend type-check, lint and build. Dependabot covers pip, npm and GitHub Actions.
- `app/owner_profile.py` loads owner-only data from the git-ignored `app/private/owner_profile.json`: own account numbers, family and friend payee names and the reconciled monthly balances. An example lives in `docs/examples/`.

### Changed
- The live integration suite (`tests/test_fde_integration.py`) runs only with `PFA_LIVE_TESTS=1`, because it writes to the real personal database.
- The master resolver's advisor step uses the latest reconciled closing balance instead of a fixed figure. The demo no longer sees the owner's balance.

### Security
- Agent workload tokens are signed with a random per-process key unless `AGENT_WIF_SIGNING_KEY` is set, so no default signing key ships in the code.
- Personal data was removed from code, tests and docs, and the public history starts from that scrubbed snapshot.

### Fixed
- Two ESLint errors, in the Agent Registry screen and the copilot dock.

## Pre-release history

### 2026-11-01: Statement import hardening (DEC-060, DEC-061)
- Long statements are read two pages per Gemini call, six calls in parallel, with checks for cut-off replies and row coverage. A partial read never imports silently.
- Failed Bank Sync imports can be retried, and statement cards show the real import status.
- Password-protected PDFs are unlocked before Gemini sees them, and the Upload screen takes a PDF password. A locked upload now gets a clear 400 instead of a 500.

### 2026-10-25 to 2026-10-27: Reconciliation and the copilot (DEC-054 to DEC-058)
- Statement-reconciled monthly balances (`monthly_bank_balances`) across Kotak and HDFC, with missing months backfilled.
- Excel-over-bank consolidation, and hardened payee-pattern extraction.
- Master multi-agent category resolution (`/agents/master-resolve`) with a review-and-apply step.
- A copilot dock (voice and text) on every screen. Salary and summary figures are normalised to one month.

### 2026-10-22 to 2026-10-23: Agent platform (DEC-049 to DEC-053)
- A four-agent CFO workflow over A2A with Agent Cards, and a Ledger MCP server on the official MCP SDK with a JSON-RPC endpoint.
- Agents registered in Google Cloud Agent Registry, plus an Agent Registry screen in the web app.
- Closed-loop categorisation learning (`learned_rules`).
- Start- and end-of-month balance KPIs. "Previous Balance" rows no longer count as income.

### 2026-10-20: Gemini 3 (DEC-042)
- Every AI feature moved off the retiring Gemini 2.5 to Gemini 3.5 Flash and Flash-Lite on Vertex AI's global endpoint. The model is set per tier, and a retired model name falls back with a warning.

### 2026-10-19: Duplicates and dates (DEC-032 to DEC-035)
- A reviewable, reversible duplicate-cleanup tool.
- Duplicates are stopped at import time: references are matched across labels, a row's type changes only with proof, and each statement file is imported once.
- A year guard on every AI reader.

### 2026-10-18 to 2026-10-19: Phase 1, sign-in and two environments (DEC-025 to DEC-031)
- Firebase sign-in is verified on every route. Personal and demo backends run on Cloud Run from one image, and Cloud Scheduler drives the Gmail sync and the nightly demo reset.
- A deterministic 18-month sample ledger for the demo.
- A React + TypeScript shell with routing, theming and Firebase sign-in, tested with Playwright.

### 2026-10-18: Phase 0, security and correctness (DEC-023, DEC-024)
- Secrets moved to Secret Manager. Every table got row-level security and its grants were revoked. CORS got an allowlist, and uploads got type and size limits plus a path-traversal fix.
- XSS hardening in the web app, a single "needs review" rule, and confidence normalised to 0–100.

### Before 2026-10-18: First versions (DEC-001 to DEC-022)
- A FastAPI + Gemini backend that extracts statements, receipts and spreadsheets, behind a vanilla-JS single-page app, with voice clarification and forecasting. This period closed with the UX audit and the phased overhaul plan.
