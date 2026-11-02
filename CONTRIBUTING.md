# Contributing

Thanks for your interest. This is a personal project whose source is published for reading and evaluation (see [LICENSE](LICENSE)), so the ground rules differ from a typical open-source repository:

- **Issues are welcome**: bug reports, questions and suggestions. Please use the issue templates.
- **Pull requests need agreement first.** Open an issue describing the change before writing code. Unsolicited PRs may be closed. By submitting a contribution, you agree that it may be used, changed and relicensed by the author as part of this project.
- **Security problems** go through [SECURITY.md](SECURITY.md), never a public issue.
- Please be respectful; see the [Code of Conduct](CODE_OF_CONDUCT.md).

The rest of this guide describes how the project itself is developed. Follow it if you're invited to contribute.

## Development setup

See [Getting started](README.md#getting-started) in the README. In short:

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements-dev.txt
(cd app/frontend && npm ci)
```

`app/` is the backend's import root: modules import each other by plain name (`import database`, `from security import ...`). Tests add `app/` to `sys.path` through `tests/conftest.py`.

## Before you push

All of these must pass. CI runs the first four.

```bash
venv/bin/python -m pytest tests -q            # backend unit tests (no credentials needed)
(cd app/frontend && npm run typecheck)
(cd app/frontend && npm run lint)             # zero warnings allowed
(cd app/frontend && npm run build)
```

For changes to reconciliation, deduplication or imports, also run the Postgres-backed tests ([README → Testing](README.md#testing)). Run the live suite (`PFA_LIVE_TESTS=1`) only on purpose: it touches the real personal database.

## Conventions

- **Tests come with the change.** Every behaviour change or bug fix gets a test that fails without it. Tests never call Google Cloud, Gemini or a remote database; mock at the boundary, as `tests/test_statement_pages.py` does.
- **No personal data, ever.** Use synthetic values in tests, fixtures, examples and docs: made-up names, `9000000001`-style numbers, round amounts. Owner-specific values belong in the git-ignored `app/private/owner_profile.json` (see `app/owner_profile.py`). Real statements belong in the git-ignored `data/`.
- **No secrets in code or config.** Credentials go to Secret Manager (`scripts/set_secret.sh <name>`). If a secret is ever committed, rotate it first, then clean the history.
- **Python**: type hints on public functions, docstrings that say *why*, `logging` (not `print`) in `app/`, and no network calls at import time.
- **Frontend**: TypeScript strict, ESLint `strictTypeChecked` with zero warnings, and sanitise any HTML built from user or AI content.
- **Commits**: small and focused, in the imperative mood, with an optional type prefix (`feat(ledger): …`, `fix(api): …`, `docs: …`, `ci: …`).

## Pull request checklist

The [pull request template](.github/PULL_REQUEST_TEMPLATE.md) repeats this list:

- [ ] Linked to an agreed issue
- [ ] Tests added or updated; all checks above pass
- [ ] Documentation ([docs/API.md](docs/API.md), [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)) updated where relevant
- [ ] No personal data, secrets or real statements in the diff
- [ ] User-visible changes noted in [CHANGELOG.md](CHANGELOG.md) under *Unreleased*
