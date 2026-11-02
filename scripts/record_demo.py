#!/usr/bin/env python3
"""Record the backend's API responses for the static "recorded demo" (GitHub Pages).

The live Cloud Run backends stop when the Google Cloud credit ends. The recorded demo is the same
React app built with VITE_RECORDED_DEMO=1: it never signs in and never calls a backend, Firebase or
Google; every API call is answered from the JSON files this script writes to
app/frontend/recorded-demo/recorded/ (the replay code is app/frontend/src/recorded/).

How the responses are made (no production code, deployment or real database is involved):
- The real FastAPI app (app/api.py, APP_ENV=demo) runs in this process under FastAPI's TestClient.
- The database is a throwaway local PostgreSQL that this script starts in a temporary folder (initdb
  and pg_ctl from the local PostgreSQL install), or one you pass with --database-url (localhost
  only). It is seeded with the demo environment's sample data exactly as the nightly demo reset does
  (app/demo_seed.py: an 18-month fictional ledger). SQLite is not an option: the database layer uses
  PostgreSQL features (array_agg, xmax, DO blocks).
- Sign-in: FirebaseAuthMiddleware is ASGI middleware, so FastAPI's dependency_overrides can't reach
  it. As tests/test_auth.py and tests/test_statement_pages.py do, this process replaces the token
  signature check (auth.verify_firebase_token) with one that accepts a single fake token for the fake
  demo user demo@example.com. The middleware and the allowlist check run unchanged.
- The AI CFO chat, the insight report, two Jarvis questions, the ASK_CFO skill and one upload of a
  synthetic statement PDF (built with the helper in tests/test_statement_pages.py) call Gemini on
  Vertex AI through the app's normal code path, with Application Default Credentials. Only the
  synthetic demo data is sent. Token usage is printed and stored in the manifest, and an estimated
  spend limit (--max-spend-inr) stops the run.
- Guards in this process: Secret Manager, Firebase certificate downloads and Google Cloud Agent
  Registry calls raise; Model Armor, Sensitive Data Protection, Cloud Trace export and explicit
  context caching stay off; the owner profile path points at a file that doesn't exist; the script
  refuses to run when the repository has a .env file (config.py would load it).
- Every response is scrubbed (tokens, e-mail addresses other than the fake demo one, the owner's
  name) before it is written; tests/test_recorded_fixtures.py checks the result.

Usage, from the repository root:
    venv/bin/python scripts/record_demo.py                      # temporary PostgreSQL, real Gemini
    venv/bin/python scripts/record_demo.py --no-ai              # skip every Gemini call (no cost)
    venv/bin/python scripts/record_demo.py --today 2026-11-02   # pin the sample data's last day
    venv/bin/python scripts/record_demo.py --database-url postgresql://postgres@127.0.0.1:55434/postgres

Files are written to a temporary folder and swapped in only when the whole run succeeded, so a failed
run (for example expired credentials) never leaves half a recording behind. A Gemini answer that
comes back as the app's offline fallback is retried after refreshing the credentials, and the run
stops instead of saving it.

Exit codes: 0 done, 1 failed, 2 refused by a safety check.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app"
DEFAULT_OUT = REPO_ROOT / "app" / "frontend" / "recorded-demo" / "recorded"
MANIFEST_FORMAT = 1

DEMO_EMAIL = "demo@example.com"  # RFC 2606 example domain: obviously not a real account
DEMO_UID = "recorded-demo-user"
# Accepted only by the token check this process installs (see install_guards); worthless anywhere else.
FAKE_TOKEN = "recorded-demo-token"
AUTH_HEADERS = {"Authorization": f"Bearer {FAKE_TOKEN}"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# The AI CFO chat (src/components/insights.js). The first three are the screen's own quick prompts.
CHAT_QUESTIONS = [
    "How can I cut dining expenses?",
    "Can I increase my SIP to ₹25,000?",
    "Identify my biggest stealth spending leak",
    "How did my spending change over the last three months?",
    "Am I on track with my budgets this month?",
    "Which recurring payments could I cancel or renegotiate?",
    "How big should my emergency fund be?",
    "Can I afford a ₹15,000 trip next month?",
]

# Jarvis copilot (src/shell/MasterCopilotDock.tsx quick prompts). The first two go to Gemini; the
# last one runs the master category resolver, which writes to the throwaway database, so it is
# recorded after every ledger read.
COPILOT_QUESTIONS = [
    "What is my true monthly salary & surplus?",
    "Walk me through Transactions (All Months (19m Avg))",
]
COPILOT_AUDIT_PROMPT = "Audit & resolve categories"
COPILOT_CONTEXT = {"current_screen": "transactions", "current_month": "ALL", "search_query": "", "history": []}

# Request bodies the Agent Registry screen sends (src/api/agents.ts). The replay matches on the
# agent, skill or tool only, so these just have to be representative.
WORKFLOW_PAYLOAD = {
    "transactions": [
        {"date": "2026-10-22", "amount": 15000, "description": "UPI/RAHUL/778192837182/Monthly",
         "category": "Other", "confidence": 45, "transaction_type": "EXPENSE"},
        {"date": "2026-10-22", "amount": 450, "description": "SWIGGY INSTAMART ORDER 99812",
         "category": "Food", "confidence": 96, "transaction_type": "EXPENSE"},
    ],
    "opening_balance": 100000,
    "closing_balance": 84550,
    "current_balance": 22000,
    "upcoming_commitments": [{"name": "HDFC Home Loan EMI", "amount": 35000, "due_in_days": 5}],
}
MASTER_RESOLVE_PAYLOAD = {"month": "ALL", "include_locked_other": True, "auto_apply": False}
SKILL_PAYLOADS: Dict[str, Dict[str, Any]] = {
    "RUN_FOUR_AGENT_WORKFLOW": {
        "opening_balance": 100000,
        "closing_balance": 84550,
        "current_balance": 250000,
        "transactions": [
            {"date": "2026-10-22", "amount": 15000, "description": "UPI/RAHUL/778192837182/Monthly",
             "category": "Other", "confidence": 45, "transaction_type": "EXPENSE"},
        ],
    },
    "MASTER_RESOLVE_TRANSACTIONS": dict(MASTER_RESOLVE_PAYLOAD),
    "SCREEN_AND_SANITIZE_TRANSACTIONS": {
        "transactions": [
            {"date": "2026-10-25", "amount": 450, "description": "UPI/SWIGGY/987654321098/Lunch PAN ABCDE1234F",
             "category": "Food", "transaction_type": "EXPENSE"},
        ],
    },
    "CHECK_SELF_TRANSFER": {"description": "Sweep Transfer From: 1234567890", "amount": 15000},
    "VERIFY_MATH_INTEGRITY": {
        "opening_balance": 100000, "closing_balance": 110250.5, "total_credits": 50000, "total_debits": 39749.5,
    },
    # agents.ts uses a real person's name in this narration; the recording uses an obviously fake one.
    "RECONCILE_BATCH": {
        "transactions": [
            {"date": "2026-08-01", "amount": 10000, "description": "UPI/DEMO USER/657928664113/To be back",
             "category": "Other", "transaction_type": "EXPENSE"},
        ],
    },
    "RESOLVE_AMBIGUOUS_BATCH": {
        "transactions": [
            {"date": "2026-09-05", "amount": 45, "description": "HungerBox [Kotak Statement] UPI/HungerBox/090508936480",
             "category": "Other", "confidence": 50, "transaction_type": "EXPENSE"},
        ],
    },
    "CHECK_PROACTIVE_ALERTS": {"current_balance": 250000},
    "FORECAST_SAVINGS": {},
    "ASK_CFO": {"question": "Summarize my current cashflow health and top savings opportunity."},
}
AI_SKILLS = {"ASK_CFO"}
# The screen sends this skill an empty payload, i.e. an empty command for Gemini: not worth replaying.
SKIPPED_SKILLS = {
    "OMNIPRESENT_JARVIS_COMMAND": "The Agent Registry screen sends it an empty command; the Jarvis "
                                  "copilot (bottom right) replays real commands instead.",
}
MCP_DEFAULT_ARGS: Dict[str, Dict[str, Any]] = {
    "get_financial_summary": {},
    "get_recent_transactions": {"limit": 10},
    "check_budget_health": {"category": "ALL"},
    "get_learned_rules": {},
    "list_statements": {},
    "get_ingested_files": {"limit": 10},
    "get_bank_sync_status": {},
    "calculate_balance_reconciliation": {
        "opening_balance": 100000, "closing_balance": 110250.5, "total_credits": 50000, "total_debits": 39749.5,
    },
    "calculate_emi_and_shortfall": {},
    "project_savings_capacity": {},
}

UPLOAD_FILE_NAME = "Kotak_Bank_2026-04_statement.pdf"  # the name tests/test_statement_pages.py uses
UPLOAD_PAGES, UPLOAD_ROWS_PER_PAGE = 2, 6

# Replies the app returns when Gemini didn't answer (advisor.py, a2a_orchestrator.py).
AI_FALLBACK_MARKERS = (
    "resilient fallback mode",
    "Vertex AI is currently initializing or offline",
    "Trouble analyzing finances",
    "### Master CFO Summary · ",
    "No response returned from AI Advisor",
)
# a2a_orchestrator.py's Jarvis prompt still labels the salary "Typical TCS Monthly Salary", an
# owner-specific leftover. A copilot reply that repeats it is not recorded.
OWNER_SPECIFIC_REPLY = re.compile(r"\bTCS\b")

# Estimated Vertex AI prices in USD per million tokens (input, output incl. thinking). Every prompt
# token is priced as uncached input, so the estimate errs high. Check Cloud Billing for real charges.
DEFAULT_PRICES_USD_PER_M = {
    "gemini-3.5-flash": (0.50, 3.00),
    "gemini-3.5-flash-lite": (0.10, 0.40),
}
FALLBACK_PRICE_USD_PER_M = (1.00, 5.00)

# Scrubbing: applied to every string in every response before it is written.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
SCRUBS: List[Tuple[str, re.Pattern, Any]] = [
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), "[redacted-jwt]"),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [redacted]"),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{35}"), "[redacted-api-key]"),
    ("oauth_token", re.compile(r"ya29\.[0-9A-Za-z_-]+"), "[redacted-oauth-token]"),
    ("workload_token_signature", re.compile(r"(\|\d{9,11}\|)[0-9a-f]{64}"), r"\1[redacted-signature]"),
    ("fake_token", re.compile(re.escape(FAKE_TOKEN)), "[redacted]"),
    ("email", _EMAIL, lambda m: m.group(0) if m.group(0).lower() == DEMO_EMAIL else "[redacted-email]"),
    ("owner_name", re.compile(r"(?i)\brohit\s+kushwaha\b"), "DEMO USER"),
    ("owner_handle", re.compile(r"(?i)rkushwaha8610"), "demo-owner"),
]


class RecordingError(RuntimeError):
    """The run cannot continue; nothing has been written to the fixtures folder."""


class Refused(RuntimeError):
    """A safety check said no (exit code 2)."""


def log(message: str) -> None:
    print(f"[record_demo] {message}", flush=True)


# ---------------------------------------------------------------------------------------------
# Command line and safety checks
# ---------------------------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record API responses for the static recorded demo.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"fixtures folder (default: {DEFAULT_OUT})")
    parser.add_argument("--database-url", default="",
                        help="a throwaway local PostgreSQL to use instead of starting one (localhost only, empty)")
    parser.add_argument("--today", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD",
                        help="last day of the sample data (default: today in India)")
    parser.add_argument("--no-ai", action="store_true",
                        help="skip every Gemini call (chat, insights, Jarvis questions, ASK_CFO, upload)")
    parser.add_argument("--max-spend-inr", type=float, default=300.0,
                        help="stop before the next Gemini call once the estimated spend reaches this (default 300)")
    parser.add_argument("--inr-per-usd", type=float, default=90.0, help="exchange rate for the estimate (default 90)")
    parser.add_argument("--project", default="personal-finance-agent-507010", help="Vertex AI project")
    parser.add_argument("--location", default="global", help="Vertex AI location (Gemini 3 is served from global)")
    return parser.parse_args(argv)


def check_database_url(url: str) -> None:
    """Only a local, throwaway database: never Supabase or any other remote server."""
    parsed = urlparse(url)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise Refused(f"--database-url must be a postgresql:// URL, not {parsed.scheme or 'empty'}://.")
    if (parsed.hostname or "") not in LOCAL_HOSTS:
        raise Refused(f"--database-url must point at localhost, not {parsed.hostname!r}: the recorder never "
                      "connects to a remote database.")
    if "supabase" in url.lower():
        raise Refused("--database-url names Supabase: the recorder only uses a throwaway local database.")


def check_repository() -> None:
    if (REPO_ROOT / ".env").exists():
        raise Refused(f"{REPO_ROOT / '.env'} exists. config.py would load it into this process; move it "
                      "aside (or record from a clean checkout) so no local settings or secrets are read.")
    if os.getenv("PFA_LIVE_TESTS"):
        raise Refused("PFA_LIVE_TESTS is set. Unset it: the recorder never uses the live databases.")


# ---------------------------------------------------------------------------------------------
# Throwaway PostgreSQL
# ---------------------------------------------------------------------------------------------

def _pg_binary(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    def version(path: Path) -> int:
        part = path.parts[-3]
        return int(part) if part.isdigit() else 0
    candidates = sorted(Path("/usr/lib/postgresql").glob(f"*/bin/{name}"), key=version)
    return str(candidates[-1]) if candidates else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _locale() -> str:
    try:
        available = subprocess.run(["locale", "-a"], capture_output=True, text=True, check=False).stdout.lower()
    except OSError:
        available = ""
    for name, spelled in (("en_US.UTF-8", "en_us.utf8"), ("C.UTF-8", "c.utf8")):
        if spelled in available:
            return name
    return "C"


@contextlib.contextmanager
def temporary_postgres():
    """Starts an empty PostgreSQL cluster in a temporary folder (127.0.0.1, random port) and removes it."""
    initdb, pg_ctl = _pg_binary("initdb"), _pg_binary("pg_ctl")
    if not initdb or not pg_ctl:
        raise RecordingError("PostgreSQL server binaries (initdb, pg_ctl) were not found. Install PostgreSQL, or "
                             "start a throwaway one (docker run -d -e POSTGRES_HOST_AUTH_METHOD=trust -p "
                             "127.0.0.1:55434:5432 postgres:17) and pass --database-url.")
    root = Path(tempfile.mkdtemp(prefix="pfa-record-pg-"))
    data_dir, socket_dir = root / "data", root / "socket"
    socket_dir.mkdir()
    port = _free_port()
    started = False
    try:
        subprocess.run([initdb, "-D", str(data_dir), "-U", "postgres", "--auth=trust", "-E", "UTF8",
                        f"--locale={_locale()}"], check=True, stdout=subprocess.DEVNULL)
        options = f"-p {port} -k {socket_dir} -c listen_addresses=127.0.0.1 -c fsync=off"
        subprocess.run([pg_ctl, "-D", str(data_dir), "-o", options, "-l", str(root / "postgres.log"), "-w",
                        "start"], check=True, stdout=subprocess.DEVNULL)
        started = True
        log(f"Started a throwaway PostgreSQL on 127.0.0.1:{port} ({root}).")
        yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    finally:
        if started:
            subprocess.run([pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"], check=False,
                           stdout=subprocess.DEVNULL)
        shutil.rmtree(root, ignore_errors=True)
        log("Removed the throwaway PostgreSQL.")


# ---------------------------------------------------------------------------------------------
# The app, in this process
# ---------------------------------------------------------------------------------------------

def configure_environment(database_url: str, args: argparse.Namespace, workdir: Path) -> None:
    """Pins every setting the app reads, before any app module is imported (config.py reads them once)."""
    for key in ("MODEL_NAME", "GEMINI_MODEL", "MODEL_LITE", "MODEL_PRO", "AGENT_WIF_SIGNING_KEY",
                "GMAIL_APP_PASSWORD", "GMAIL_PDF_PASSWORD", "CORS_ALLOWED_ORIGINS"):
        os.environ.pop(key, None)
    os.environ.update({
        "APP_ENV": "demo",
        "DATABASE_URL": database_url,
        # Only read if something asks config.get_db_password(); the throwaway database trusts local logins.
        "DB_PASS": "local-recording-only",
        "AUTH_ALLOWED_USERS": f"{DEMO_EMAIL}:password",
        "AI_DAILY_LIMITS": "",  # no quota: /me reports none, and no request is refused with 429
        "PROJECT_ID": args.project,
        "FIREBASE_PROJECT_ID": args.project,
        "LOCATION": args.location,
        "OWNER_PROFILE_PATH": str(workdir / "no-owner-profile.json"),  # never created
        "STATEMENT_BUCKET": "",
        "MODEL_ARMOR_TEMPLATE": "",
        "SDP_ENABLED": "0",
        "ENABLE_CLOUD_TRACE": "0",
        "ENABLE_VERTEX_CONTEXT_CACHE": "0",
        "ENABLE_API_DOCS": "0",
        "ENABLE_GMAIL_BACKGROUND_WORKER": "0",
        "INTERNAL_AUDIENCE": "",
        "INSTANCE_CONNECTION_NAME": "",
    })


def install_guards(modules: Dict[str, Any], ai_enabled: bool) -> None:
    """Makes the services the recording must not use fail loudly, and accepts the fake demo user."""
    auth, secret_store, agent_registry = modules["auth"], modules["secret_store"], modules["agent_registry"]

    def no_secret_manager(*_args, **_kwargs):
        raise RecordingError("Something asked for a secret. The recording never reads Secret Manager.")

    def no_certificate_download(force_refresh: bool = False):
        raise RecordingError("Something tried to download Google's sign-in certificates.")

    def no_agent_registry(*_args, **_kwargs):
        raise RecordingError("Something tried to call Google Cloud Agent Registry.")

    secret_store.get_secret = no_secret_manager
    secret_store.set_secret = no_secret_manager
    secret_store._authorized_session = no_secret_manager
    auth._firebase_certs.get = no_certificate_download
    auth._google_oidc_certs.get = no_certificate_download
    agent_registry._get_gcp_access_token = no_agent_registry
    agent_registry.AgentRegistry._gcp_request = no_agent_registry

    def verify_recording_token(token: str) -> dict:
        """Claims for the fake demo user, for the one fake token; check_allowed() still applies the allowlist."""
        if token != FAKE_TOKEN:
            raise auth.AuthError(401, "Invalid sign-in token.")
        now = int(time.time())
        return {"sub": DEMO_UID, "email": DEMO_EMAIL, "email_verified": True,
                "firebase": {"sign_in_provider": "password"}, "auth_time": now - 60, "exp": now + 3600}

    auth.verify_firebase_token = verify_recording_token

    if not ai_enabled:
        from vertexai.generative_models import ChatSession, GenerativeModel

        def ai_switched_off(*_args, **_kwargs):
            raise RecordingError("Gemini is switched off for this run (--no-ai).")

        GenerativeModel.generate_content = ai_switched_off
        ChatSession.send_message = ai_switched_off


def import_app(workdir: Path) -> Dict[str, Any]:
    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))  # app/ is the import root (DEC-025)
    os.chdir(workdir)  # api.py creates temp_uploads/ in the working directory
    import auth  # noqa: E402  (imported after the environment is pinned)
    import config
    import secret_store
    import api
    import demo_seed
    from database import DatabaseManager
    from mcp import agent_registry
    from telemetry import usage
    return {"auth": auth, "config": config, "secret_store": secret_store, "api": api, "demo_seed": demo_seed,
            "DatabaseManager": DatabaseManager, "agent_registry": agent_registry, "usage": usage}


def refresh_credentials(attempts: int = 3) -> None:
    """Refreshes Application Default Credentials, retrying a temporary metadata-server failure."""
    import google.auth
    import google.auth.transport.requests

    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            credentials.refresh(google.auth.transport.requests.Request())
            return
        except Exception as error:  # google.auth raises several types; all mean "not now"
            last_error = error
            log(f"Credential refresh failed (attempt {attempt}/{attempts}): {type(error).__name__}")
            time.sleep(5 * attempt)
    raise RecordingError(f"Google credentials aren't working ({type(last_error).__name__}: {last_error}). "
                         "Nothing was written; fix Application Default Credentials and run again, or use --no-ai.")


# ---------------------------------------------------------------------------------------------
# Writing fixtures
# ---------------------------------------------------------------------------------------------

def to_json(value: Any, level: int = 0) -> str:
    """JSON with one line per flat object (e.g. a ledger row), indented otherwise: compact and diffable."""
    pad = " " * level
    if isinstance(value, dict):
        if not value:
            return "{}"
        if all(not isinstance(v, (dict, list)) for v in value.values()):
            return json.dumps(value, ensure_ascii=False)
        items = [f"{pad} {json.dumps(k, ensure_ascii=False)}: {to_json(v, level + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(not isinstance(v, (dict, list)) for v in value):
            return json.dumps(value, ensure_ascii=False)
        items = [f"{pad} {to_json(v, level + 1)}" for v in value]
        return "[\n" + ",\n".join(items) + f"\n{pad}]"
    return json.dumps(value, ensure_ascii=False)


def scrub(value: Any, counts: Dict[str, int]) -> Any:
    if isinstance(value, dict):
        return {key: scrub(item, counts) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, counts) for item in value]
    if isinstance(value, str):
        for name, pattern, replacement in SCRUBS:
            new_value, hits = pattern.subn(replacement, value)
            if hits and new_value != value:
                counts[name] = counts.get(name, 0) + hits
            value = new_value
    return value


def route_key(method: str, path: str, query: Optional[Dict[str, Any]] = None) -> str:
    """'GET /budgets?month=2026-05': the key app/frontend/src/recorded/transport.ts looks up."""
    pairs = sorted((str(k), str(v)) for k, v in (query or {}).items())
    return f"{method} {path}" + ("?" + "&".join(f"{k}={v}" for k, v in pairs) if pairs else "")


def git_output(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


class SpendGuard:
    """Estimated Vertex AI spend from the token counts the app records for every Gemini reply."""

    def __init__(self, usage_module, max_inr: float, inr_per_usd: float):
        self.usage = usage_module
        self.max_inr = max_inr
        self.inr_per_usd = inr_per_usd

    def estimate_inr(self) -> float:
        usd = 0.0
        for model, bucket in self.usage.usage_snapshot().get("by_model", {}).items():
            price_in, price_out = DEFAULT_PRICES_USD_PER_M.get(model, FALLBACK_PRICE_USD_PER_M)
            output = bucket.get("candidates_tokens", 0) + bucket.get("thoughts_tokens", 0)
            usd += bucket.get("prompt_tokens", 0) * price_in / 1e6 + output * price_out / 1e6
        return round(usd * self.inr_per_usd, 2)

    def check(self, label: str) -> None:
        spent = self.estimate_inr()
        if spent >= self.max_inr:
            raise RecordingError(f"Estimated spend ₹{spent} reached --max-spend-inr ₹{self.max_inr} before {label}.")


class Recorder:
    def __init__(self, client, out_dir: Path, spend: SpendGuard, ai_enabled: bool):
        self.client = client
        self.out = out_dir
        self.spend = spend
        self.ai_enabled = ai_enabled
        self.scrub_counts: Dict[str, int] = {}
        self.routes: List[Dict[str, Any]] = []
        self.datasets: Dict[str, str] = {}
        self.chat: List[Dict[str, Any]] = []
        self.copilot: List[Dict[str, Any]] = []
        self.agent_tasks: List[Dict[str, Any]] = []
        self.mcp_tools: List[Dict[str, Any]] = []
        self.not_recorded: List[Dict[str, str]] = []

    def request(self, method: str, path: str, *, query=None, json_body=None, files=None,
                expect: Tuple[int, ...] = (200,)) -> Tuple[int, Any]:
        response = self.client.request(method, path, params=query, json=json_body, files=files, headers=AUTH_HEADERS)
        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text[:300]}
        if response.status_code not in expect:
            detail = body.get("detail") if isinstance(body, dict) else body
            raise RecordingError(f"{method} {path} answered HTTP {response.status_code}: {detail}")
        return response.status_code, body

    def ai_request(self, label: str, method: str, path: str, reply_of: Callable[[Any], str], **kwargs) -> Tuple[int, Any]:
        """A Gemini-backed request: priced, and retried after a credential refresh if Gemini didn't answer."""
        problem = ""
        for attempt in range(1, 4):
            self.spend.check(label)
            status, body = self.request(method, path, expect=(200, 500, 502, 503), **kwargs)
            reply = reply_of(body) if status == 200 else ""
            if status != 200:
                problem = f"HTTP {status}: {body.get('detail') if isinstance(body, dict) else body}"
            elif not reply.strip():
                problem = "an empty reply"
            elif any(marker in reply for marker in AI_FALLBACK_MARKERS):
                problem = "the app's offline fallback instead of a Gemini answer"
            else:
                return status, body
            log(f"{label}: got {problem} (attempt {attempt}/3); refreshing credentials and retrying.")
            refresh_credentials()
            time.sleep(5 * attempt)
        raise RecordingError(f"{label}: Gemini didn't answer after 3 attempts ({problem}). Nothing was written.")

    def save(self, file_name: str, body: Any) -> None:
        clean = scrub(body, self.scrub_counts)
        (self.out / file_name).write_text(to_json(clean) + "\n", encoding="utf-8")

    def route(self, method: str, path: str, file_name: str, *, query=None, json_body=None,
              expect: Tuple[int, ...] = (200,)) -> Any:
        status, body = self.request(method, path, query=query, json_body=json_body, expect=expect)
        self.save(file_name, body)
        self.routes.append({"key": route_key(method, path, query), "file": file_name, "status": status})
        return body


# ---------------------------------------------------------------------------------------------
# What gets recorded
# ---------------------------------------------------------------------------------------------

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def record_ledger_screens(rec: Recorder, seeded_rows: int) -> Dict[str, Any]:
    """Transactions (and the review queue), analytics, budgets, recurring and forecast, for every month."""
    me = rec.route("GET", "/me", "me.json")
    if me.get("email") != DEMO_EMAIL or me.get("environment") != "demo":
        raise RecordingError(f"/me answered for {me.get('email')!r} in {me.get('environment')!r}, not the demo user.")
    months_body = rec.route("GET", "/transactions/months", "transactions-months.json")
    months = [m["month"] for m in months_body.get("months", [])]

    # The whole ledger in one response. The replay derives every list the Transactions screen asks
    # for (month, type filter, sort, review queue) from it, with the backend's filter and sort rules.
    status, ledger = rec.request("GET", "/transactions", query={"limit": 100000})
    if ledger.get("total") != seeded_rows:
        raise RecordingError(f"/transactions returned {ledger.get('total')} rows; the seed loaded {seeded_rows}.")
    rec.save("transactions-all.json", ledger)
    rec.datasets["ledger"] = "transactions-all.json"

    rec.route("GET", "/analytics/summary", "analytics-summary-all.json")
    rec.route("GET", "/budgets", "budgets-all.json")
    for month in months:
        rec.route("GET", "/analytics/summary", f"analytics-summary-{month}.json", query={"month": month})
        rec.route("GET", "/budgets", f"budgets-{month}.json", query={"month": month})
    rec.route("GET", "/recurring", "recurring.json")
    rec.route("GET", "/forecast", "forecast.json")
    log(f"Recorded the ledger screens: {len(ledger.get('transactions', []))} transactions, {len(months)} months.")
    return {"months": months, "review_count": sum(m.get("review_count", 0) for m in months_body.get("months", []))}


def record_ai_cfo(rec: Recorder) -> None:
    status, body = rec.ai_request("insights", "GET", "/insights", lambda b: str(b.get("insights", "")))
    rec.save("insights.json", body)
    rec.routes.append({"key": route_key("GET", "/insights"), "file": "insights.json", "status": status})
    for number, question in enumerate(CHAT_QUESTIONS, 1):
        status, body = rec.ai_request(f"chat {number}", "POST", "/insights/chat", lambda b: str(b.get("reply", "")),
                                      json_body={"message": question, "history": []})
        file_name = f"chat-{number:02d}.json"
        rec.save(file_name, body)
        rec.chat.append({"question": question, "file": file_name, "status": status})
        log(f"Recorded chat {number}/{len(CHAT_QUESTIONS)}: {question}")


def record_agent_registry(rec: Recorder) -> None:
    """Agent Registry screen: every skill badge, every MCP tool, the 4-agent check and the master resolver."""
    registry = rec.route("GET", "/a2a/registry", "a2a-registry.json")
    tools = rec.route("GET", "/mcp/tools", "mcp-tools.json")
    rec.route("POST", "/agents/workflow", "agents-workflow.json", json_body=WORKFLOW_PAYLOAD)
    rec.route("POST", "/agents/master-resolve", "agents-master-resolve.json", json_body=MASTER_RESOLVE_PAYLOAD)

    for card in registry.get("agent_cards", []):
        agent_id = card.get("agent_id", "")
        for skill in card.get("skills", []):
            intent = skill.get("id", "")
            if intent in SKIPPED_SKILLS:
                rec.not_recorded.append({"what": f"{agent_id}:{intent}", "why": SKIPPED_SKILLS[intent]})
                continue
            if intent in AI_SKILLS and not rec.ai_enabled:
                rec.not_recorded.append({"what": f"{agent_id}:{intent}", "why": "recorded with --no-ai"})
                continue
            path = f"/a2a/agents/{agent_id}/tasks/send"
            body_in = {"intent": intent, "payload": SKILL_PAYLOADS.get(intent, {})}
            if intent in AI_SKILLS:
                def answer_of(body):
                    result = body.get("result") if isinstance(body, dict) else None
                    return str(result.get("answer", "")) if isinstance(result, dict) else ""
                status, body = rec.ai_request(f"{agent_id}:{intent}", "POST", path, answer_of, json_body=body_in)
            else:
                status, body = rec.request("POST", path, json_body=body_in)
            file_name = f"a2a-{slug(agent_id)}-{slug(intent)}.json"
            rec.save(file_name, body)
            rec.agent_tasks.append({"agent_id": agent_id, "intent": intent, "file": file_name, "status": status})

    for tool in tools.get("tools", []):
        name = tool.get("name", "")
        status, body = rec.request("POST", "/mcp/execute", json_body={"tool_name": name,
                                                                       "arguments": MCP_DEFAULT_ARGS.get(name, {})},
                                   expect=(200, 400))
        file_name = f"mcp-{slug(name)}.json"
        rec.save(file_name, body)
        rec.mcp_tools.append({"tool": name, "file": file_name, "status": status})

    # Read after the actions above, as the screen does after each click.
    rec.route("GET", "/agents/learned-rules", "agents-learned-rules.json")
    rec.route("GET", "/agents/audit-trail", "agents-audit-trail.json", query={"limit": 20})
    rec.route("GET", "/telemetry/traces", "telemetry-traces.json", query={"limit": 10})
    log(f"Recorded the Agent Registry: {len(rec.agent_tasks)} skills, {len(rec.mcp_tools)} MCP tools.")


def record_copilot(rec: Recorder) -> None:
    questions = COPILOT_QUESTIONS if rec.ai_enabled else []
    for question in questions:
        status, body = rec.ai_request(f"copilot '{question}'", "POST", "/agents/orchestrator/command",
                                      lambda b: str(b.get("reply", "")),
                                      json_body={"message": question, "auto_execute": True, **COPILOT_CONTEXT})
        if OWNER_SPECIFIC_REPLY.search(str(body.get("reply", ""))):
            rec.not_recorded.append({"what": f"copilot: {question}",
                                     "why": "the reply repeated the owner-specific 'TCS' salary label from the Jarvis prompt"})
            log(f"Not recording copilot '{question}': the reply repeats the 'TCS' label.")
            continue
        file_name = f"copilot-{len(rec.copilot) + 1:02d}.json"
        rec.save(file_name, body)
        rec.copilot.append({"message": question, "file": file_name, "status": status})
    if not rec.ai_enabled:
        rec.not_recorded.extend({"what": f"copilot: {q}", "why": "recorded with --no-ai"} for q in COPILOT_QUESTIONS)

    # Writes to the throwaway database (auto_execute), so it comes after every ledger read.
    status, body = rec.request("POST", "/agents/orchestrator/command",
                               json_body={"message": COPILOT_AUDIT_PROMPT, "auto_execute": True, **COPILOT_CONTEXT})
    file_name = f"copilot-{len(rec.copilot) + 1:02d}.json"
    rec.save(file_name, body)
    rec.copilot.append({"message": COPILOT_AUDIT_PROMPT, "file": file_name, "status": status})
    report = body.get("master_report") if isinstance(body, dict) else None
    if isinstance(report, dict) and report.get("applied_result"):
        # The resolver run with auto-apply on the whole ledger: what the Agent Registry screen's
        # "Auto-Resolve & Update All" (POST /agents/master-resolve, auto_apply=true) answers.
        rec.save("agents-master-resolve-applied.json", report)
        rec.datasets["master_resolve_applied"] = "agents-master-resolve-applied.json"


def record_upload(rec: Recorder) -> None:
    """One upload of a synthetic two-page statement, built with the helper the tests use."""
    if not rec.ai_enabled:
        rec.not_recorded.append({"what": "POST /upload", "why": "recorded with --no-ai"})
        return
    tests_dir = str(REPO_ROOT / "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from test_statement_pages import make_pdf, statement_pages

    pdf = make_pdf(statement_pages(UPLOAD_PAGES, UPLOAD_ROWS_PER_PAGE))

    def notes_of(body):
        return " ".join(str(n) for n in body.get("notifications", [])) if isinstance(body, dict) else ""

    status, body = rec.ai_request("upload", "POST", "/upload", notes_of,
                                  files={"file": (UPLOAD_FILE_NAME, pdf, "application/pdf")})
    rec.save("upload.json", body)
    rec.routes.append({"key": route_key("POST", "/upload"), "file": "upload.json", "status": status})
    log(f"Recorded the upload of a synthetic {UPLOAD_PAGES}-page statement: {body.get('message')}")


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------

def build_manifest(rec: Recorder, modules: Dict[str, Any], args: argparse.Namespace, today: date,
                   seed_summary: Dict[str, Any], ledger_info: Dict[str, Any]) -> Dict[str, Any]:
    config, usage = modules["config"], modules["usage"]
    # The backend is app/ without the frontend (where the fixtures themselves are being written).
    dirty = git_output("status", "--porcelain", "--", "app", ":(exclude)app/frontend", "scripts/record_demo.py")
    recorded_at = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = usage.usage_snapshot()
    return {
        "format": MANIFEST_FORMAT,
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "recorded_on": today.isoformat(),
        "source": {
            "repository_commit": git_output("rev-parse", "HEAD"),
            "uncommitted_backend_changes": bool(dirty),
            "recorder": "scripts/record_demo.py",
            "backend": "app/api.py under FastAPI TestClient, APP_ENV=demo",
            "database": "throwaway local PostgreSQL seeded by app/demo_seed.py",
        },
        "data": {
            "seed_today": today.isoformat(),
            "transactions": seed_summary.get("transactions"),
            "first_month": seed_summary.get("first_month"),
            "last_month": seed_summary.get("last_month"),
            "months": len(ledger_info["months"]),
            "review_count": ledger_info["review_count"],
        },
        "identity": {"email": DEMO_EMAIL, "provider": "password"},
        "ai": {
            "enabled": rec.ai_enabled,
            "project": args.project if rec.ai_enabled else "",
            "location": args.location if rec.ai_enabled else "",
            "models": {"standard": config.MODEL_NAME, "lite": config.MODEL_LITE, "pro": config.MODEL_PRO},
            "token_usage": {key: snapshot.get(key, 0) for key in (
                "calls_reported", "prompt_tokens", "cached_content_tokens", "candidates_tokens",
                "thoughts_tokens", "total_tokens")},
            "token_usage_by_model": {model: {key: bucket.get(key, 0) for key in (
                "calls_reported", "prompt_tokens", "candidates_tokens", "thoughts_tokens", "total_tokens")}
                for model, bucket in snapshot.get("by_model", {}).items()},
            "estimated_cost_inr": rec.spend.estimate_inr(),
            "pricing_note": "Estimate from Vertex AI's reported token counts at assumed list prices (USD per "
                            "million tokens, input/output: " + ", ".join(
                                f"{m} {p[0]}/{p[1]}" for m, p in DEFAULT_PRICES_USD_PER_M.items())
                            + f"; INR {args.inr_per_usd}/USD). Cloud Billing has the actual charge.",
        },
        "datasets": rec.datasets,
        "routes": rec.routes,
        "chat": rec.chat,
        "copilot": rec.copilot,
        "agent_tasks": rec.agent_tasks,
        "mcp_tools": rec.mcp_tools,
        "not_recorded": rec.not_recorded,
        "scrubbed": dict(sorted(rec.scrub_counts.items())),
    }


def record(database_url: str, args: argparse.Namespace, workdir: Path, staging: Path) -> Dict[str, Any]:
    configure_environment(database_url, args, workdir)
    modules = import_app(workdir)
    ai_enabled = not args.no_ai
    install_guards(modules, ai_enabled)
    if ai_enabled:
        refresh_credentials()

    demo_seed, DatabaseManager = modules["demo_seed"], modules["DatabaseManager"]
    today = args.today or demo_seed.india_today()
    engine = DatabaseManager().pool  # creates the schema
    demo_seed.mark_database_as_demo(engine)
    seed_summary = demo_seed.reset_demo_data(engine, today=today)
    log(f"Seeded {seed_summary['transactions']} demo transactions ({seed_summary['first_month']} to "
        f"{seed_summary['last_month']}, last day {today}).")

    from fastapi.testclient import TestClient

    modules["usage"].reset_usage()
    spend = SpendGuard(modules["usage"], args.max_spend_inr, args.inr_per_usd)
    # Not used as a context manager: the app's startup hooks (Secret Manager check) stay off.
    rec = Recorder(TestClient(modules["api"].app), staging, spend, ai_enabled)
    try:
        ledger_info = record_ledger_screens(rec, seed_summary["transactions"])
        if ai_enabled:
            record_ai_cfo(rec)
        else:
            rec.not_recorded.append({"what": "AI CFO insights and chat", "why": "recorded with --no-ai"})
        record_agent_registry(rec)
        record_copilot(rec)
        record_upload(rec)
        manifest = build_manifest(rec, modules, args, today, seed_summary, ledger_info)
        (staging / "manifest.json").write_text(to_json(manifest) + "\n", encoding="utf-8")
        return manifest
    finally:
        DatabaseManager.dispose_shared_pool()


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    out_dir = args.out.resolve()
    try:
        check_repository()
        if args.database_url:
            check_database_url(args.database_url)
    except Refused as refusal:
        print(f"Refused: {refusal}", file=sys.stderr)
        return 2

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".recording-", dir=out_dir.parent))
    workdir = Path(tempfile.mkdtemp(prefix="pfa-record-work-"))
    original_cwd = os.getcwd()
    try:
        if args.database_url:
            manifest = record(args.database_url, args, workdir, staging)
        else:
            with temporary_postgres() as url:
                manifest = record(url, args, workdir, staging)
    except (RecordingError, Refused) as error:
        print(f"Recording failed: {error}", file=sys.stderr)
        shutil.rmtree(staging, ignore_errors=True)
        return 2 if isinstance(error, Refused) else 1
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(workdir, ignore_errors=True)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    staging.chmod(0o755)
    staging.rename(out_dir)
    files = sorted(out_dir.glob("*.json"))
    size_kb = sum(f.stat().st_size for f in files) / 1024
    log(f"Wrote {len(files)} files ({size_kb:,.0f} KB) to {out_dir}.")
    ai = manifest["ai"]
    log(f"Gemini: {ai['token_usage']['calls_reported']} calls, {ai['token_usage']['total_tokens']:,} tokens, "
        f"estimated ₹{ai['estimated_cost_inr']}.")
    if manifest["scrubbed"]:
        log(f"Scrubbed: {manifest['scrubbed']}")
    for item in manifest["not_recorded"]:
        log(f"Not recorded: {item['what']} ({item['why']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
