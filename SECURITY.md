# Security Policy

This application stores and analyses personal financial data, so security reports are taken seriously and handled first.

## Reporting a vulnerability

**Please don't open a public issue, discussion or pull request for a security problem.**

1. Use GitHub's private reporting: **Security → Report a vulnerability** on this repository.
2. If that isn't available, email **rkushwaha8610@gmail.com** with the subject `SECURITY: personal-finance-agent`.

Please include:
- what an attacker could do, and under which conditions (signed in or not, which environment);
- the steps or a proof of concept, and the affected endpoint, file or commit;
- whether you accessed any data that isn't yours. If you did, stop and say so; don't keep it.

What to expect:
- an acknowledgement within **3 days**;
- an assessment and a fix plan within **10 days**, sooner for anything that exposes data;
- credit in the fix's changelog entry if you want it.

Please give a reasonable amount of time to fix the issue before disclosing it publicly.

## Supported versions

Only the latest code on `main` is supported. The live deployment is rebuilt from it.

## Scope

In scope:
- the backend API (`app/`): authentication and authorization, the `/internal/*` scheduler routes, uploads and file handling, and data exposure between environments (demo vs personal);
- the web app (`app/frontend/`): XSS, token handling, unsafe rendering of AI output;
- the AI paths: prompt injection that leads to data access, unapproved writes or tool misuse;
- secrets or personal data committed to this repository.

Out of scope:
- denial of service, rate-limit exhaustion and volumetric testing against the live services;
- social engineering, phishing and physical attacks;
- findings that need a compromised Google account or device;
- reports from automated scanners without a demonstrated impact.

Please test against a local copy where you can. Don't run automated scans against the live Cloud Run or Firebase endpoints.

## How the application is protected

- **Authentication on every route.** Firebase ID tokens (RS256) are verified against Google's certificates and an allowlist of `email[:provider]` pairs. `/internal/*` accepts only Cloud Scheduler OIDC tokens with the expected service account and audience. `/health` is the only public route.
- **Two isolated environments.** Personal and demo run as separate Cloud Run services with separate databases and service accounts. A demo backend refuses to start against the personal database.
- **Secrets.** Database, Gmail and statement passwords live in Google Secret Manager, which each service account can read only for its own secrets. Nothing secret is committed. The Firebase web config in `app/frontend/src/config/firebase.ts` is public by design.
- **Database.** Row-level security is on for every table, Supabase's `anon` and `authenticated` roles have no access, and views use `security_invoker`.
- **Uploads.** Uploads are checked against a type allowlist and capped at 25 MB. Each one goes into its own random folder under a sanitised file name. PDF passwords are used in memory and never logged or stored.
- **Browser.** CORS is limited to the hosting site and localhost. AI and user content is sanitised (DOMPurify) before rendering.
- **AI and agents.** Input is screened for prompt injection before it reaches the model, and PII is masked before prompts. Each agent has its own identity with a least-privilege MCP tool allowlist and short-lived signed workload tokens. Writes need human approval, and agent actions are recorded in an audit log.
- **Personal data stays out of git.** Statements, uploads, screenshots of real transactions and owner-specific values (`app/private/`) are git-ignored. The public history starts from a scrubbed snapshot.
