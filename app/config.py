# config.py
"""Centralized Environment & Google Cloud Platform (GCP) Configuration.

FDE Architecture Role:
- Loads environment variables from the project root `.env` file without requiring external
  third-party dotenv packages in lightweight container builds.
- Centralizes Google Vertex AI (`PROJECT_ID`, `LOCATION`, and the model of each tier:
  `MODEL_NAME`, `MODEL_LITE`, `MODEL_PRO`) and PostgreSQL
  (`DB_USER`, `DB_NAME`, Supabase pooler host/port, optional `DATABASE_URL` /
  `INSTANCE_CONNECTION_NAME` overrides) settings used across all backend agents and
  persistence services.
- Holds no credentials. The database password is resolved at runtime by `get_db_password()`
  via `secret_store` (the `DB_PASS` environment variable or Google Secret Manager).
- Phase 1 (DEC-025): `APP_ENV` (personal | demo) selects the environment and its database
  defaults, plus the sign-in allowlist (`AUTH_ALLOWED_USERS`), the Cloud Scheduler caller
  (`SCHEDULER_SA_EMAIL`, `INTERNAL_AUDIENCE`), the demo's `AI_DAILY_LIMITS` and the private
  `STATEMENT_BUCKET`. Safety check: a demo backend refuses to start against the personal database.
- Gemini 3 (DEC-042): Gemini 2.5 was retired on 2026-10-20. The Gemini 3 models are served
  from Vertex AI's `global` endpoint (not from us-central1), and a retired Gemini 1.x / 2.x
  name left in the environment falls back to the tier's default with a warning.
"""
import os
import re
import logging
from pathlib import Path

# Load environment variables from project root .env if present
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _val.strip().strip('"').strip("'")
            os.environ.setdefault(_key, _val)

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# GCP Project settings,   Pull from environment variables
PROJECT_ID = os.getenv("PROJECT_ID", "personal-finance-agent-507010")
# Vertex AI serves the Gemini 3 models from the global endpoint; in this project they answer
# 404 NOT_FOUND on us-central1, the old default (DEC-042).
LOCATION = os.getenv("LOCATION", "global")

# Gemini model of each tier (DEC-042), overridable with the environment variable of the same name:
# - MODEL_NAME (standard; GEMINI_MODEL is an older alias): statement PDFs, receipts, spreadsheets,
#   AI insight reports and the AI CFO chat.
# - MODEL_LITE: quick look-ups in the AI CFO chat and the voice clarifications.
# - MODEL_PRO: planning questions in the AI CFO chat (retirement, portfolio, tax, ...).
# Gemini 3 models run at their default temperature and thinking level: Google advises against
# temperatures below 1.0 for them, so the agents no longer set one.
# Why 3.5 Flash and not the newer 3.8 Flash: on 2026-10-20 gemini-3.8-flash stalled for 100-300 s
# on about 1 call in 7, even for short replies (server-side, per Vertex AI's latency metric), past
# the app's 90 s limit for AI requests; 26 chat-sized gemini-3.5-flash calls all took under 25 s.
# Revisit when 3.8 settles.
DEFAULT_MODEL_NAME = "gemini-3.5-flash"
DEFAULT_MODEL_LITE = "gemini-3.5-flash-lite"
DEFAULT_MODEL_PRO = "gemini-3.5-flash"
_LEGACY_GEMINI_ALIASES = {"gemini-pro", "gemini-pro-vision", "gemini-ultra", "gemini-ultra-vision"}


def is_retired_model(name: str) -> bool:
    """True for Gemini 1.x / 2.x model names; Vertex AI retired Gemini 2.5 on 2026-10-20."""
    base = (name or "").strip().lower().rsplit("/", 1)[-1]  # also accepts publishers/google/models/...
    if base in _LEGACY_GEMINI_ALIASES:
        return True
    match = re.match(r"gemini-(?:live-)?(\d+)(?:[.-]|$)", base)
    return bool(match) and int(match.group(1)) < 3


def model_setting(env_names, default: str, environ=os.environ) -> str:
    """The model named by the first of `env_names` that is set, else `default`.

    A retired name falls back to `default` with a warning instead of stopping the backend: every
    call to a retired model fails, while the rest of the app (ledger, analytics, budgets) must
    keep working.
    """
    for env_name in env_names:
        value = (environ.get(env_name) or "").strip()
        if not value:
            continue
        if is_retired_model(value):
            logging.getLogger(__name__).warning(
                "%s=%s names a retired Gemini model; using %s instead (DEC-042).", env_name, value, default
            )
            return default
        return value
    return default


MODEL_NAME = model_setting(("MODEL_NAME", "GEMINI_MODEL"), DEFAULT_MODEL_NAME)
MODEL_LITE = model_setting(("MODEL_LITE",), DEFAULT_MODEL_LITE)
MODEL_PRO = model_setting(("MODEL_PRO",), DEFAULT_MODEL_PRO)

# Which environment this backend serves (Phase 1, DEC-025). Both run the same code:
# - "personal": your real data, Gmail sync, no AI limits.
# - "demo": sample data reset nightly, shareable login, Gmail turned off, AI daily limits.
# The environment picks the database defaults below, so setting APP_ENV=demo alone can never
# leave a demo backend pointed at your real database.
APP_ENV = os.getenv("APP_ENV", "personal").strip().lower()
if APP_ENV not in ("personal", "demo"):
    raise ValueError(f"APP_ENV must be 'personal' or 'demo', not '{APP_ENV}'.")
IS_DEMO = APP_ENV == "demo"

# Supabase project of the personal environment. Safety checks refuse to run demo-only
# operations (such as the nightly reset) against it.
PERSONAL_SUPABASE_PROJECT_REF = "lmjqsvmnsvxwsnlarcap"
_ENV_DATABASE_DEFAULTS = {
    "personal": {
        "project_ref": PERSONAL_SUPABASE_PROJECT_REF,
        "pooler_host": "aws-0-ap-northeast-1.pooler.supabase.com",  # Tokyo
        "password_secret": "pfa-db-password-personal",
    },
    "demo": {
        "project_ref": "amvigyhwiidzmpccvrfa",
        "pooler_host": "aws-0-ap-northeast-2.pooler.supabase.com",  # Seoul
        "password_secret": "pfa-db-password-demo",
    },
}

# Database connection settings
# Only non-secret settings live here. The password is a secret: it comes from the DB_PASS
# environment variable (Cloud Run injects it from Secret Manager) or, in Cloud Shell, straight
# from Secret Manager. It is never stored in code, in the Docker image, or in Supabase.
# Legacy Cloud SQL instance, kept for reference:
# INSTANCE_CONNECTION_NAME = os.getenv("INSTANCE_CONNECTION_NAME", "personal-finance-agent-507010:us-central1:finance-db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_NAME = os.getenv("DB_NAME", "postgres")
SUPABASE_PROJECT_REF = os.getenv("SUPABASE_PROJECT_REF", _ENV_DATABASE_DEFAULTS[APP_ENV]["project_ref"])
SUPABASE_POOLER_HOST = os.getenv("SUPABASE_POOLER_HOST", _ENV_DATABASE_DEFAULTS[APP_ENV]["pooler_host"])
SUPABASE_POOLER_PORT = int(os.getenv("SUPABASE_POOLER_PORT", "6543"))
# Name of the Secret Manager secret holding the database password (one per environment).
DB_PASSWORD_SECRET = os.getenv("DB_PASSWORD_SECRET", _ENV_DATABASE_DEFAULTS[APP_ENV]["password_secret"])
# Optional overrides: a full database URL (for example a local test database), or a legacy
# Cloud SQL instance connection name ("project:region:instance").
DATABASE_URL = os.getenv("DATABASE_URL", "")
INSTANCE_CONNECTION_NAME = os.getenv("INSTANCE_CONNECTION_NAME", "")

if IS_DEMO and not DATABASE_URL and (
    SUPABASE_PROJECT_REF == PERSONAL_SUPABASE_PROJECT_REF
    or DB_PASSWORD_SECRET == _ENV_DATABASE_DEFAULTS["personal"]["password_secret"]
):
    raise ValueError("Refusing to start: APP_ENV=demo is pointed at the personal database.")

# Sign-in (Phase 1, DEC-025): every API route needs a Firebase ID token from an allowed account.
# AUTH_ALLOWED_USERS is a comma-separated list of "email" or "email:provider" entries, where
# provider is the Firebase sign-in method ("google.com" or "password"). Each backend only lets
# in the account(s) of its own environment.
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", PROJECT_ID)
_DEFAULT_ALLOWED_USERS = {
    "personal": "rkushwaha8610@gmail.com:google.com",
    "demo": "rkushwaha8610+pfademo@gmail.com:password",
}
AUTH_ALLOWED_USERS = os.getenv("AUTH_ALLOWED_USERS", _DEFAULT_ALLOWED_USERS[APP_ENV])

# Cloud Scheduler calls /internal/* routes with a Google-signed OIDC token for this service
# account. INTERNAL_AUDIENCE is the token audience (the service's URL); when it is empty,
# /internal/* routes reject every call.
SCHEDULER_SA_EMAIL = os.getenv(
    "SCHEDULER_SA_EMAIL", f"pfa-scheduler@{PROJECT_ID}.iam.gserviceaccount.com"
)
INTERNAL_AUDIENCE = os.getenv("INTERNAL_AUDIENCE", "").rstrip("/")

# Daily AI limits per feature ("chat=40,insights=10,..."), counted per day in India time.
# Empty means unlimited (personal). The demo caps costs by default.
_DEFAULT_AI_DAILY_LIMITS = "chat=40,insights=10,upload=10,voice=40,eval=3" if IS_DEMO else ""
AI_DAILY_LIMITS = {
    feature.strip(): int(limit)
    for feature, _, limit in (
        item.partition("=") for item in os.getenv("AI_DAILY_LIMITS", _DEFAULT_AI_DAILY_LIMITS).split(",")
    )
    if feature.strip() and limit.strip().isdigit()
}

# Private Cloud Storage bucket for Gmail-exported statement PDFs (personal only). When empty,
# PDFs are kept on local disk under data/monthly_statements (local development).
STATEMENT_BUCKET = os.getenv("STATEMENT_BUCKET", "")

# FastAPI's /docs and /openapi.json describe every route; off unless explicitly enabled.
ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", "0") == "1"
# The old in-process hourly Gmail check. Off by default: Cloud Scheduler calls
# /internal/gmail-sync instead, so two servers never sync at the same time.
ENABLE_GMAIL_BACKGROUND_WORKER = os.getenv("ENABLE_GMAIL_BACKGROUND_WORKER", "0") == "1"


def get_db_password() -> str:
    """Returns the database password from DB_PASS or Secret Manager.

    Raises secret_store.SecretNotFoundError (with setup instructions) if neither is available.
    """
    import secret_store  # lazy import: secret_store also imports config lazily

    return secret_store.get_secret(DB_PASSWORD_SECRET, env_var="DB_PASS")
