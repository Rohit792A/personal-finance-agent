# app/api.py
"""FastAPI Enterprise Backend Gateway (`app/api.py`).

Serves as the Tier-2 Serverless API Gateway (Google Cloud Run / Uvicorn) connecting the
Frontend SPA (`app/frontend/src`) to the 5-Tier FDE Agent Fleet:

Route Map & Architectural Responsibilities:
1. Health & Readiness (`GET /health`): Liveness probe for Cloud Run and SPA auto-failover.
2. Multimodal Document Ingestion (`POST /upload`): Screens files and dispatches to `process_file()`.
3. 4-Stage Progressive Disclosure Vault (`GET /transactions/months`, `GET /transactions`):
   Pre-aggregates Year & Month cashflow rollups, top categories, and review counts.
4. Ledger CRUD & Human-in-the-Loop Voice Resolution (`POST /transactions/{id}/resolve`,
   `GET /transactions/{id}/clarification`, `GET /transactions/{id}/clarification-audio`):
   Connects the UI modal to `VoiceAgent` (Cloud STT/TTS + Gemini fallback).
5. Analytics, Budgets & Recurring Commitments (`GET /analytics/summary`, `GET /budgets`,
   `GET /recurring`): SQL-backed portfolio metrics and subscription detection.
6. Multi-Horizon Future Cost Predictor (`GET /forecast`): Weighted 30-day category forecast
   (45% MoM + 35% Historical Avg + 20% YoY Seasonal) and Indian household preparedness plan.
7. AI Personal CFO & Model Armor Gate (`GET /insights`, `POST /insights/chat`):
   Screens prompts via `ModelArmor`, redacts PII via `FinancialDLPPipeline`, and routes to
   `FinancialAdvisor` (Gemini 3 Lite/Standard/Pro tiers from config.py, DEC-042, + MCP tools).
8. MCP & Evaluation Endpoints (`GET /mcp/tools`, `POST /mcp/execute`, `GET /eval/run`):
   Exposes `LedgerMCPServer` JSON-RPC tools and `run_full_evaluation_suite()` benchmarks.
9. Automated Gmail Bank Sync & Statement Vault (`GET/POST /api/gmail/*`):
   Background IMAP sync task, password-protected PDF statement unlocking, and ledger reconciliation.
10. Sign-in & Environment (Phase 1, DEC-025): `FirebaseAuthMiddleware` (app/auth.py) requires a
    Firebase ID token from an allowed account on every route except `/health`; `GET /me`
    returns the account, environment (personal / demo), enabled features and demo AI limits.
    The demo turns Gmail routes off (403) and caps AI features per day (429).
11. Cloud Scheduler (`POST /internal/gmail-sync` hourly on personal, `POST /internal/demo-reset`
    nightly on demo): only accept Cloud Scheduler's Google-signed OIDC token.
"""
import os
import re
import uuid
import shutil
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import logging
from fastapi import Depends, FastAPI, Form, UploadFile, File, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import sqlalchemy

import config
from auth import AuthUser, FirebaseAuthMiddleware
from database import DatabaseManager, compute_row_signature
from exceptions import PasswordRequiredError
from voice_agent import VoiceAgent
import statement_store

# FDE Enterprise Architectural Organs
# app/ is the import root everywhere (Cloud Shell, tests and the Docker image), see DEC-025.
from security import ModelArmor, SecurityViolationError
from mcp import LedgerMCPServer
from eval import run_full_evaluation_suite

logger = logging.getLogger(__name__)


def process_file(file_path: str, pdf_password: str = ""):
    """Runs the ingestion pipeline (main.process_file), importing it on first use.

    The AI extractors load the Vertex AI SDK, which takes about 4 s to import. Importing them
    lazily keeps Cloud Run cold starts short (DEC-025); the cost moves to the first upload.
    `pdf_password` opens a password-protected PDF (DEC-060); it is never stored or logged.
    """
    from main import process_file as _process_file

    return _process_file(file_path, pdf_password=pdf_password)


def _financial_advisor():
    """The AI CFO agent, imported on first use for the same cold-start reason as process_file."""
    from advisor import FinancialAdvisor

    return FinancialAdvisor()

app = FastAPI(
    title="Personal Finance AI API",
    description="Production-grade Financial Intelligence API with Autonomous Document Ingestion, Multi-Month Analytics, Budgeting, and Subscriptions Tracking.",
    version="2.0.0",
    # The interactive docs list every route; only enabled for local development.
    docs_url="/docs" if config.ENABLE_API_DOCS else None,
    redoc_url="/redoc" if config.ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if config.ENABLE_API_DOCS else None,
)

# Sign-in (Phase 1, DEC-025): every route except /health and the local SPA files needs a
# Firebase ID token from an account allowed into this environment; /internal/* needs a Cloud
# Scheduler OIDC token. Registered BEFORE CORS so CORS stays the outer layer: browser preflights
# are answered and 401/403 responses still carry CORS headers the app can read.
app.add_middleware(FirebaseAuthMiddleware)

# CORS: only the Firebase Hosting site and local development may call this API from a browser.
# (Previously any website was allowed, with credentials.) Override with CORS_ALLOWED_ORIGINS,
# a comma-separated list. CORS only limits browsers; the sign-in check above protects the data.
DEFAULT_CORS_ORIGINS = [
    "https://personal-finance-agent-507010.web.app",
    "https://personal-finance-agent-507010.firebaseapp.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS)).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

UPLOAD_DIR = "temp_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
# Upload safety: only types the ingestion pipeline supports, bounded size, and the client's
# file name never controls the server-side path (prevents path traversal).
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".xlsx", ".xls"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _safe_upload_name(raw_name: Optional[str]) -> str:
    """Reduces a client-supplied file name to a harmless base name (no directories)."""
    base = os.path.basename((raw_name or "").replace("\\", "/")).strip()
    base = re.sub(r"[^A-Za-z0-9._ ()-]", "_", base)[:120].lstrip(".")
    return base or "upload"

# ---------------------------------------------------------------------------
# Environment, sign-in and demo limits (Phase 1, DEC-025)
# ---------------------------------------------------------------------------
INDIA_TZ = ZoneInfo("Asia/Kolkata")
AI_FEATURE_LABELS = {
    "chat": "AI CFO chat",
    "insights": "AI insight reports",
    "upload": "document uploads",
    "voice": "voice clarifications",
    "eval": "evaluation runs",
}


def current_user(request: Request) -> AuthUser:
    """The signed-in user, set by FirebaseAuthMiddleware for every protected route."""
    user = getattr(request.state, "user", None)
    if user is None:  # defensive: the middleware rejects such requests before they get here
        raise HTTPException(status_code=401, detail="Sign in required.")
    return user


def require_gmail_enabled() -> None:
    """Gmail sync only exists in the personal environment; the demo holds no Gmail access."""
    if config.IS_DEMO:
        raise HTTPException(status_code=403, detail="Gmail sync is turned off in the demo.")


def _ai_usage_today() -> Dict[str, int]:
    """Today's (India time) AI usage counts per feature."""
    db = DatabaseManager()
    with db.pool.connect() as conn:
        rows = conn.execute(
            sqlalchemy.text("SELECT feature, count FROM ai_usage WHERE day = :day"),
            {"day": datetime.now(INDIA_TZ).date()},
        ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def ai_quota(feature: str):
    """Dependency that counts one use of an AI feature against the daily limit (demo only).

    The increment is a single atomic upsert, so concurrent requests and multiple Cloud Run
    instances can't overshoot the limit. A request counts when it starts, even if it fails.
    """

    def _consume() -> None:
        limit = config.AI_DAILY_LIMITS.get(feature)
        if limit is None:
            return
        label = AI_FEATURE_LABELS.get(feature, feature)
        detail = (
            f"The demo's daily limit for {label} ({limit} per day) has been reached. "
            "It resets at midnight India time."
        )
        if limit <= 0:
            raise HTTPException(status_code=429, detail=detail)
        db = DatabaseManager()
        with db.pool.begin() as conn:
            row = conn.execute(sqlalchemy.text("""
                INSERT INTO ai_usage (day, feature, count) VALUES (:day, :feature, 1)
                ON CONFLICT (day, feature) DO UPDATE SET count = ai_usage.count + 1
                WHERE ai_usage.count < :limit
                RETURNING count
            """), {"day": datetime.now(INDIA_TZ).date(), "feature": feature, "limit": limit}).fetchone()
        if row is None:
            raise HTTPException(status_code=429, detail=detail)

    return _consume


class TransactionUpdate(BaseModel):
    description: str
    category: str
    amount: float


class TransactionCreate(BaseModel):
    date: str = Field(..., description="Date formatted as YYYY-MM-DD")
    description: str
    category: str
    amount: float
    type: str = Field("EXPENSE", description="EXPENSE, INCOME, or TRANSFER")


class BudgetUpdate(BaseModel):
    category: str
    monthly_budget: float


class ChatMessage(BaseModel):
    role: str = Field(..., description="Role: 'user' or 'model' / 'assistant'")
    content: str = Field(..., description="Content of the message")


class ChatRequest(BaseModel):
    message: str = Field(..., description="Current user prompt or message")
    history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Previous conversation turns: [{'role': 'user', 'content': '...'}, {'role': 'model', 'content': '...'}]"
    )


class GmailSettingsRequest(BaseModel):
    email_address: str = Field(..., description="Gmail address receiving bank alerts & statements")
    app_password: Optional[str] = Field(None, description="16-character Google App Password")
    pdf_password: Optional[str] = Field("", description="Optional password for unlocking bank statement PDFs")
    allowed_senders: Optional[str] = Field(None, description="Comma-separated strict allowlist of bank sender email addresses")
    auto_sync_enabled: bool = Field(True, description="Automatically check Gmail daily for new bank alerts")
    sync_interval_hours: int = Field(24, description="Hours between automatic background syncs")


class GmailSyncRequest(BaseModel):
    days_back: int = Field(7, description="Number of past days of emails to scan")
    include_statements: bool = Field(True, description="Also process PDF/Excel bank statement attachments")


class MCPExecuteRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)



FRONTEND_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
if os.path.exists(os.path.join(FRONTEND_DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "message": "Personal Finance AI Engine Online",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/me")
def get_me(user: AuthUser = Depends(current_user)):
    """Who is signed in, which environment this backend serves, and what is enabled here.

    The frontend calls this right after sign-in to pick its banner and screens; it is also the
    single place that will grow per-user settings when multi-user arrives (OVERHAUL_PLAN §7).
    """
    ai_limits = None
    if config.AI_DAILY_LIMITS:
        try:
            used = _ai_usage_today()
        except Exception as e:  # don't fail sign-in over a counter read
            logger.warning(f"Could not read AI usage: {e}")
            used = {}
        ai_limits = {
            feature: {
                "limit": limit,
                "used": used.get(feature, 0),
                "remaining": max(0, limit - used.get(feature, 0)),
            }
            for feature, limit in config.AI_DAILY_LIMITS.items()
        }
    return {
        "email": user.email,
        "uid": user.uid,
        "provider": user.provider,
        "environment": config.APP_ENV,
        "features": {
            "gmail_sync": not config.IS_DEMO,
            "uploads": config.AI_DAILY_LIMITS.get("upload", 1) > 0,
            "ai": True,
        },
        "ai_limits": ai_limits,
        "data_resets_daily": config.IS_DEMO,
    }


@app.get("/")
def serve_root():
    index_file = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return health_check()


@app.post("/upload")
async def upload_document(file: UploadFile = File(...), password: str = Form("")):
    """
    Endpoint to upload a Receipt, PDF bank statement, or Excel file.
    Executes AI extraction, records transactions, and returns detailed confirmation.
    `password` (optional) opens a password-protected PDF; it is used in memory only (DEC-060).
    """
    safe_name = _safe_upload_name(file.filename)
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or 'none'}'. Upload a PDF bank statement, an Excel file (.xlsx, .xls) or a receipt image (.jpg, .jpeg, .png).",
        )
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (maximum 25 MB).")
    # Demo: count against the daily AI limit only once the file passed the checks above.
    await asyncio.to_thread(ai_quota("upload"))

    # Each upload gets its own random folder; the cleaned name keeps the extension for routing.
    upload_dir = os.path.join(UPLOAD_DIR, uuid.uuid4().hex)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, safe_name)
    db = DatabaseManager()
    count_before = 0
    try:
        with db.pool.connect() as conn:
            row = conn.execute(sqlalchemy.text("SELECT count(*) FROM transactions")).fetchone()
            count_before = row[0] if row else 0
    except Exception:
        pass
    finally:
        db.close()

    try:
        # Save uploaded file temporarily, enforcing the size limit while copying
        written = 0
        with open(file_path, "wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File is too large (maximum 25 MB).")
                buffer.write(chunk)

        # Execute extraction workflow in a background worker thread
        result = await asyncio.to_thread(process_file, file_path, password)

        # Check count after ingestion
        count_after = count_before
        db_after = DatabaseManager()
        try:
            with db_after.pool.connect() as conn:
                row = conn.execute(sqlalchemy.text("SELECT count(*) FROM transactions")).fetchone()
                count_after = row[0] if row else count_before
        except Exception:
            pass
        finally:
            db_after.close()

        new_count = max(0, count_after - count_before)

        notifications = [
            f"File '{safe_name}' successfully uploaded and verified.",
            f"AI document parser completed extraction ({new_count} new entries recorded).",
            "Financial ledger and category summaries updated."
        ]
        if isinstance(result, dict) and result.get("messages"):
            notifications = result["messages"] + [n for n in notifications if n not in result["messages"]]

        return {
            "status": "success",
            "message": f"Successfully processed '{safe_name}'",
            "new_transactions": new_count,
            "total_transactions": count_after,
            "notifications": notifications
        }

    except HTTPException:
        raise
    except PasswordRequiredError as e:
        # A locked PDF (Gemini would answer 400 INVALID_ARGUMENT): the user can fix this (DEC-060).
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up the temporary upload folder in every case
        shutil.rmtree(upload_dir, ignore_errors=True)


@app.get("/transactions/months")
def get_transaction_months():
    """
    Returns all distinct available calendar months present in the transactions database,
    along with aggregate income, expense, transaction count, flagged review count,
    top expense category, and year-level rollups for Year -> Month navigation.
    """
    db = DatabaseManager()
    try:
        with db.pool.connect() as conn:
            query = sqlalchemy.text(r"""
                SELECT 
                    TO_CHAR(t.transaction_date, 'YYYY-MM') as month_key,
                    COUNT(*) as tx_count,
                    COALESCE(SUM(CASE
                        WHEN UPPER(t.transaction_type) = 'INCOME'
                         AND COALESCE(t.is_self_transfer, FALSE) = FALSE
                         AND LOWER(TRIM(COALESCE(t.description, ''))) !~ '^(prev(ious|ous)?\s+balance|opening\s+balance|initial\s+balance|month\s+end\s+balance|closing\s+balance)'
                        THEN t.amount ELSE 0 END), 0) as income,
                    COALESCE(SUM(CASE
                        WHEN UPPER(t.transaction_type) = 'EXPENSE'
                         AND COALESCE(t.is_self_transfer, FALSE) = FALSE
                         AND LOWER(TRIM(COALESCE(t.description, ''))) !~ '^(prev(ious|ous)?\s+balance|opening\s+balance|initial\s+balance|month\s+end\s+balance|closing\s+balance)'
                        THEN t.amount ELSE 0 END), 0) as expense,
                    COALESCE(SUM(CASE WHEN t.requires_human_review = TRUE THEN 1 ELSE 0 END), 0) as review_count,
                    MAX(CASE
                        WHEN LOWER(TRIM(COALESCE(t.description, ''))) ~ '^(prev(ious|ous)?\s+balance|opening\s+balance|initial\s+balance)'
                        THEN t.amount ELSE NULL END) as explicit_opening_balance
                FROM transactions t
                WHERE t.transaction_date IS NOT NULL
                GROUP BY TO_CHAR(t.transaction_date, 'YYYY-MM')
                ORDER BY month_key DESC
            """)
            rows = conn.execute(query).fetchall()

            cat_query = sqlalchemy.text("""
                SELECT
                    TO_CHAR(t.transaction_date, 'YYYY-MM') as month_key,
                    COALESCE(c.name, 'Other') as category_name,
                    SUM(t.amount) as cat_total
                FROM transactions t
                LEFT JOIN categories c ON t.category_id = c.id
                WHERE t.transaction_date IS NOT NULL
                  AND UPPER(t.transaction_type) = 'EXPENSE'
                  AND COALESCE(t.is_self_transfer, FALSE) = FALSE
                GROUP BY TO_CHAR(t.transaction_date, 'YYYY-MM'), COALESCE(c.name, 'Other')
                ORDER BY month_key DESC, cat_total DESC
            """)
            cat_rows = conn.execute(cat_query).fetchall()
            top_cat_by_month: Dict[str, str] = {}
            for cr in cat_rows:
                if cr[0] not in top_cat_by_month:
                    top_cat_by_month[cr[0]] = str(cr[1])

            months = []
            years_map: Dict[str, Dict[str, Any]] = {}
            total_count = 0
            total_income = 0.0
            total_expense = 0.0
            total_review = 0

            # Compute chronological cumulative Opening (start of month) and Closing (end of month) balances.
            # Prioritize authoritative statement-reconciled balances from `monthly_bank_balances` (DEC-054:
            # Kotak SA + ActivMoney FD + HDFC SA + Sweep-In FD); otherwise use explicit "Previous Balance"
            # or carry forward the prior month's closing balance.
            reconciled_map = db.get_reconciled_monthly_balances()
            opening_by_month: Dict[str, float] = {}
            closing_by_month: Dict[str, float] = {}
            running_balance = 0.0
            for r in reversed(rows):
                m_key_asc = r[0]
                inc_asc = float(r[2])
                exp_asc = float(r[3])
                explicit_open = float(r[5]) if (len(r) > 5 and r[5] is not None) else None
                rec = reconciled_map.get(m_key_asc)
                if rec:
                    open_val = round(float(rec["opening_balance"]), 2)
                    close_val = round(float(rec["closing_balance"]), 2)
                    opening_by_month[m_key_asc] = open_val
                    closing_by_month[m_key_asc] = close_val
                    running_balance = close_val
                else:
                    open_val = round(explicit_open, 2) if explicit_open is not None else round(running_balance, 2)
                    opening_by_month[m_key_asc] = open_val
                    running_balance = round(open_val + (inc_asc - exp_asc), 2)
                    closing_by_month[m_key_asc] = running_balance

            for r in rows:
                m_key = r[0]
                cnt = int(r[1])
                inc = float(r[2])
                exp = float(r[3])
                rev_cnt = int(r[4] or 0)
                net = inc - exp
                yr = m_key[:4] if m_key and len(m_key) >= 4 else "Unknown"

                total_count += cnt
                total_income += inc
                total_expense += exp
                total_review += rev_cnt

                try:
                    dt = datetime.strptime(m_key, "%Y-%m")
                    label = dt.strftime("%B %Y")
                    month_name = dt.strftime("%B")
                    month_short = dt.strftime("%b")
                except Exception:
                    label = m_key
                    month_name = m_key
                    month_short = m_key

                open_bal = opening_by_month.get(m_key, 0.0)
                close_bal = closing_by_month.get(m_key, round(open_bal + net, 2))
                rec_meta = reconciled_map.get(m_key)

                m_obj = {
                    "month": m_key,
                    "year": yr,
                    "label": label,
                    "month_name": month_name,
                    "month_short": month_short,
                    "count": cnt,
                    "income": inc,
                    "expense": exp,
                    "net": net,
                    "opening_balance": open_bal,
                    "closing_balance": close_bal,
                    "reconciled": bool(rec_meta),
                    "bank_breakdown": rec_meta,
                    "review_count": rev_cnt,
                    "top_category": top_cat_by_month.get(m_key, "General"),
                }
                months.append(m_obj)

                if yr not in years_map:
                    years_map[yr] = {
                        "year": yr,
                        "months_count": 0,
                        "count": 0,
                        "income": 0.0,
                        "expense": 0.0,
                        "net": 0.0,
                        "opening_balance": 0.0,
                        "closing_balance": 0.0,
                        "review_count": 0,
                        "months": [],
                    }
                years_map[yr]["months_count"] += 1
                years_map[yr]["count"] += cnt
                years_map[yr]["income"] += inc
                years_map[yr]["expense"] += exp
                years_map[yr]["net"] += net
                years_map[yr]["review_count"] += rev_cnt
                years_map[yr]["months"].append(m_obj)

            for y_obj in years_map.values():
                if y_obj["months"]:
                    # `months` in each year are ordered newest first (DESC)
                    y_obj["opening_balance"] = y_obj["months"][-1]["opening_balance"]
                    y_obj["closing_balance"] = y_obj["months"][0]["closing_balance"]

            years_list = sorted(years_map.values(), key=lambda x: x["year"], reverse=True)

            all_open_bal = months[-1]["opening_balance"] if months else 0.0
            all_close_bal = months[0]["closing_balance"] if months else round(total_income - total_expense, 2)

            all_summary = {
                "month": "ALL",
                "label": "All Time",
                "count": total_count,
                "income": total_income,
                "expense": total_expense,
                "net": total_income - total_expense,
                "opening_balance": all_open_bal,
                "closing_balance": all_close_bal,
                "review_count": total_review,
                "years_count": len(years_list),
            }

            return {
                "months": months,
                "years": years_list,
                "all": all_summary
            }
    finally:
        db.close()


@app.get("/transactions")
def get_transactions(
    month: Optional[str] = None,
    order_by: str = "date",
    order_dir: str = "desc",
    type: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    requires_review: Optional[bool] = None,
    limit: int = 1000
):
    """
    Fetch transactions with month filtering, multi-column ordering, type & category filters,
    search text capabilities, and requires_human_review flag.
    """
    db = DatabaseManager()
    try:
        with db.pool.connect() as conn:
            where_clauses = []
            params: Dict[str, Any] = {"limit": limit}

            if month and month.upper() != "ALL":
                where_clauses.append("TO_CHAR(t.transaction_date, 'YYYY-MM') = :month")
                params["month"] = month.strip()

            if type and type.upper() != "ALL":
                if type.upper() == "TRANSFER":
                    where_clauses.append("(UPPER(t.transaction_type) = 'TRANSFER' OR t.is_self_transfer = TRUE OR LOWER(c.name) = 'transfer')")
                else:
                    where_clauses.append("(UPPER(t.transaction_type) = :type AND (t.is_self_transfer = FALSE OR t.is_self_transfer IS NULL) AND LOWER(COALESCE(c.name, '')) != 'transfer')")
                    params["type"] = type.strip().upper()

            if category and category.upper() != "ALL":
                where_clauses.append("LOWER(c.name) = :category")
                params["category"] = category.strip().lower()

            if search and search.strip():
                where_clauses.append("(LOWER(t.description) LIKE :search OR LOWER(c.name) LIKE :search)")
                params["search"] = f"%{search.strip().lower()}%"

            if requires_review is not None:
                if requires_review:
                    where_clauses.append("t.requires_human_review = TRUE")
                else:
                    where_clauses.append("(t.requires_human_review = FALSE OR t.requires_human_review IS NULL)")

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            order_columns = {
                "date": "t.transaction_date",
                "amount": "t.amount",
                "description": "t.description",
                "category": "c.name",
                "confidence": "t.ai_confidence_score",
                "id": "t.id",
                "type": "t.transaction_type",
                "review": "t.requires_human_review"
            }
            sort_col = order_columns.get(order_by.lower(), "t.transaction_date")
            sort_dir = "ASC" if order_dir.lower() == "asc" else "DESC"

            query = sqlalchemy.text(f"""
                SELECT t.id, t.transaction_date, t.amount, t.description, COALESCE(c.name, 'Uncategorized') as category, t.transaction_type, t.ai_confidence_score, t.requires_human_review, COALESCE(t.is_self_transfer, FALSE) as is_self_transfer
                FROM transactions t
                LEFT JOIN categories c ON t.category_id = c.id
                {where_sql}
                ORDER BY {sort_col} {sort_dir}, t.id DESC
                LIMIT :limit
            """)
            result = conn.execute(query, params).fetchall()

            transactions = []
            for row in result:
                is_transfer = bool(row[8]) or (str(row[5]).upper() == "TRANSFER") or (str(row[4]).lower() == "transfer")
                tx_type = "TRANSFER" if is_transfer else str(row[5])
                transactions.append({
                    "id": row[0],
                    "date": str(row[1]),
                    "amount": float(row[2]),
                    "description": row[3],
                    "category": row[4],
                    "type": tx_type,
                    "confidence": float(row[6]) if row[6] is not None else 100.0,
                    "requires_human_review": bool(row[7]) if len(row) > 7 and row[7] is not None else False,
                    "is_self_transfer": is_transfer
                })

        return {"transactions": transactions, "total": len(transactions)}
    finally:
        db.close()


@app.post("/transactions")
def create_transaction(payload: TransactionCreate):
    """Manually add a transaction to the database."""
    db = DatabaseManager()
    try:
        with db.pool.begin() as conn:
            is_transfer = payload.type.upper() == "TRANSFER" or payload.category.strip().lower() == "transfer"
            tx_type = "TRANSFER" if is_transfer else payload.type.upper()
            cat_name = "transfer" if is_transfer else payload.category.strip().lower()

            cat_query = sqlalchemy.text("SELECT id FROM categories WHERE LOWER(name) = :name LIMIT 1")
            cat_res = conn.execute(cat_query, {"name": cat_name}).fetchone()
            cat_id = cat_res[0] if cat_res else 5

            row_sig = compute_row_signature(
                payload.date, payload.amount, payload.description, tx_type
            )

            # Manual entries are user data: lock them so imports never overwrite them.
            ins_query = sqlalchemy.text("""
                INSERT INTO transactions (
                    transaction_date, amount, description, category_id, transaction_type, ai_confidence_score, is_self_transfer, row_signature, requires_human_review, user_locked
                )
                VALUES (:date, :amount, :desc, :cat_id, :type, 100.0, :is_transfer, :sig, false, true)
                ON CONFLICT (row_signature) DO UPDATE
                SET amount = EXCLUDED.amount,
                    category_id = EXCLUDED.category_id,
                    transaction_type = EXCLUDED.transaction_type,
                    is_self_transfer = EXCLUDED.is_self_transfer,
                    requires_human_review = FALSE,
                    ai_confidence_score = 100.0,
                    user_locked = TRUE
                RETURNING id
            """)
            new_id = conn.execute(ins_query, {
                "date": payload.date,
                "amount": payload.amount,
                "desc": payload.description,
                "cat_id": cat_id,
                "type": tx_type,
                "is_transfer": is_transfer,
                "sig": row_sig
            }).fetchone()[0]

            return {
                "status": "success",
                "message": f"Transaction #{new_id} recorded successfully",
                "id": new_id
            }
    finally:
        db.close()


@app.get("/transactions/{tx_id}/clarification")
def get_transaction_clarification(tx_id: int):
    """
    Returns clarification metadata for a transaction:
    id, description, amount, current_category, question_text.
    """
    db = DatabaseManager()
    try:
        with db.pool.connect() as conn:
            query = sqlalchemy.text("""
                SELECT t.id, t.description, t.amount, COALESCE(c.name, 'Uncategorized'), t.requires_human_review, t.ai_confidence_score
                FROM transactions t
                LEFT JOIN categories c ON t.category_id = c.id
                WHERE t.id = :id
            """)
            row = conn.execute(query, {"id": tx_id}).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Transaction #{tx_id} not found")

            agent = VoiceAgent()
            question_text = agent.get_question_text(row[1], float(row[2]), row[3])

            return {
                "id": row[0],
                "description": row[1],
                "amount": float(row[2]),
                "category": row[3],
                "requires_human_review": bool(row[4]) if row[4] is not None else False,
                "confidence": float(row[5]) if row[5] is not None else 100.0,
                "question_text": question_text
            }
    finally:
        db.close()


@app.get("/transactions/{tx_id}/clarification-audio", dependencies=[Depends(ai_quota("voice"))])
def get_transaction_clarification_audio(
    tx_id: int,
    description: Optional[str] = Query(None),
    amount: Optional[float] = Query(None),
    category: Optional[str] = Query(None),
):
    """
    Returns synthesized TTS audio (MP3) for the voice agent's clarification question.
    Accepts optional query parameters to avoid redundant database round-trips.
    """
    db = None
    try:
        if description is not None and amount is not None:
            desc = description
            amt = float(amount)
            cat = category or "Uncategorized"
        else:
            db = DatabaseManager()
            with db.pool.connect() as conn:
                query = sqlalchemy.text("""
                    SELECT t.id, t.description, t.amount, COALESCE(c.name, 'Uncategorized')
                    FROM transactions t
                    LEFT JOIN categories c ON t.category_id = c.id
                    WHERE t.id = :id
                """)
                row = conn.execute(query, {"id": tx_id}).fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail=f"Transaction #{tx_id} not found")

                desc = row[1]
                amt = float(row[2])
                cat = row[3]

        agent = VoiceAgent()
        audio_content = agent.generate_question_audio(desc, amt, cat)
        if audio_content:
            return Response(content=audio_content, media_type="audio/mpeg")
        else:
            raise HTTPException(
                status_code=503,
                detail="Cloud TTS unavailable; client should use browser speech synthesis"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Audio question generation unavailable for tx {tx_id}: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Cloud TTS unavailable ({str(e)}); client should use browser speech synthesis"
        )
    finally:
        if db:
            db.close()


@app.post("/transactions/{tx_id}/resolve", dependencies=[Depends(ai_quota("voice"))])
async def resolve_transaction_category(
    tx_id: int,
    text_reply: Optional[str] = Form(None),
    audio_file: Optional[UploadFile] = File(None)
):
    """
    Endpoint to resolve a flagged transaction using either Text or Voice.
    The frontend can send a text string, or an audio blob recorded from the microphone.
    """
    agent = VoiceAgent()
    user_input = ""
    db = DatabaseManager()

    try:
        # 1. Prefer text_reply if provided (e.g. from browser-side Web Speech recognition)
        if text_reply and text_reply.strip():
            user_input = text_reply.strip()

        # 2. Handle Voice Input (Speech-to-Text) if audio_file provided without text_reply
        elif audio_file:
            audio_bytes = await audio_file.read()
            mime = audio_file.content_type or "audio/webm"
            user_input = agent.transcribe_audio(audio_bytes, mime_type=mime)
            if not user_input or not user_input.strip():
                raise HTTPException(
                    status_code=400,
                    detail="Could not transcribe audio. Please try speaking clearly into your browser microphone or enter text."
                )

        else:
            raise HTTPException(status_code=400, detail="Must provide either text_reply or audio file")

        # 3. Resolve the Category using VoiceAgent (Gemini with semantic rule fallback)
        resolved_category = agent.resolve_category(user_input)

        # 4. Update the Database and teach the Clarification Agent a persistent payee rule
        learned_rule = None
        with db.pool.begin() as conn:
            # Fetch existing transaction description for rule learning
            tx_row = conn.execute(
                sqlalchemy.text("SELECT description, transaction_type FROM transactions WHERE id = :id"),
                {"id": tx_id},
            ).fetchone()
            tx_desc = tx_row[0] if tx_row else ""
            existing_tx_type = tx_row[1] if tx_row and len(tx_row) > 1 else "EXPENSE"

            # Lookup new category ID
            cat_query = sqlalchemy.text("SELECT id FROM categories WHERE lower(name) = :name LIMIT 1")
            cat_result = conn.execute(cat_query, {"name": resolved_category.strip().lower()}).fetchone()
            if cat_result:
                cat_id = cat_result[0]
            else:
                other_res = conn.execute(sqlalchemy.text("SELECT id FROM categories WHERE lower(name) = 'other' LIMIT 1")).fetchone()
                cat_id = other_res[0] if other_res else 5

            # Unflag the transaction, save the new category, and lock it against re-imports
            is_transfer = resolved_category.strip().lower() == "transfer"
            update_query = sqlalchemy.text("""
                UPDATE transactions
                SET category_id = :cat_id, 
                    requires_human_review = FALSE, 
                    ai_confidence_score = 100.0,
                    user_locked = TRUE,
                    is_self_transfer = CASE WHEN :is_transfer THEN TRUE ELSE is_self_transfer END,
                    transaction_type = CASE WHEN :is_transfer THEN 'TRANSFER' ELSE transaction_type END
                WHERE id = :id
            """)
            conn.execute(update_query, {"cat_id": cat_id, "is_transfer": is_transfer, "id": tx_id})

        if tx_desc:
            pattern = agent.extract_payee_pattern(tx_desc)
            if pattern and len(pattern) >= 3:
                final_type = "TRANSFER" if is_transfer else (existing_tx_type or "EXPENSE")
                learned_rule = db.save_learned_rule(
                    payee_pattern=pattern,
                    category=resolved_category,
                    transaction_type=final_type,
                    source="voice_hitl",
                )
                db.log_agent_audit(
                    agent_id="clarification-agent",
                    action="LEARN_USER_RULE",
                    status="COMPLETED",
                    details={"tx_id": tx_id, "payee_pattern": pattern, "category": resolved_category},
                )

        return {
            "status": "success",
            "transcribed_text": user_input,
            "resolved_category": resolved_category,
            "learned_rule": learned_rule,
            "message": f"Successfully updated category to {resolved_category}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resolving transaction #{tx_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        db.close()


@app.put("/transactions/{transaction_id}")
def update_transaction(transaction_id: int, payload: TransactionUpdate):
    """Endpoint to update an existing transaction's description, category, and amount."""
    db = DatabaseManager()
    try:
        with db.pool.begin() as conn:
            cat_query = sqlalchemy.text("SELECT id FROM categories WHERE LOWER(name) = :name LIMIT 1")
            cat_result = conn.execute(cat_query, {"name": payload.category.strip().lower()}).fetchone()
            category_id = cat_result[0] if cat_result else 5

            is_transfer = payload.category.strip().lower() == "transfer"

            # A user edit means the row is verified: clear the flag, set full confidence,
            # and lock it so imports and reconciliation never overwrite the correction.
            update_query = sqlalchemy.text("""
                UPDATE transactions
                SET description = :desc,
                    category_id = :cat_id,
                    amount = :amount,
                    requires_human_review = FALSE,
                    ai_confidence_score = 100.0,
                    user_locked = TRUE,
                    is_self_transfer = :is_transfer,
                    transaction_type = CASE 
                        WHEN :is_transfer THEN 'TRANSFER' 
                        WHEN transaction_type = 'TRANSFER' AND NOT :is_transfer THEN 'EXPENSE'
                        ELSE transaction_type 
                    END
                WHERE id = :id
                RETURNING transaction_type
            """)
            result = conn.execute(update_query, {
                "desc": payload.description,
                "cat_id": category_id,
                "amount": payload.amount,
                "is_transfer": is_transfer,
                "id": transaction_id
            })
            updated_row = result.fetchone()
            if not updated_row:
                raise HTTPException(status_code=404, detail=f"Transaction {transaction_id} not found")
            resolved_type = updated_row[0] or ("TRANSFER" if is_transfer else "EXPENSE")

        # Closed-loop learning: persist payee pattern to `learned_rules` on manual UI edit
        learned_rule = None
        clean_cat = (payload.category or "").strip()
        if clean_cat and clean_cat.lower() not in ("other", "uncategorized"):
            try:
                pattern = VoiceAgent.extract_payee_pattern(payload.description)
                if pattern and len(pattern) >= 2:
                    learned_rule = db.save_learned_rule(
                        pattern=pattern,
                        category=clean_cat,
                        transaction_type=resolved_type,
                        is_self_transfer=is_transfer,
                        source="manual_ui_edit",
                    )
                    db.log_agent_audit(
                        agent_id="clarification-agent",
                        action="LEARN_USER_RULE",
                        status="OK",
                        details={
                            "tx_id": transaction_id,
                            "pattern": pattern,
                            "category": clean_cat,
                            "source": "manual_ui_edit",
                            "retroactively_updated": learned_rule.get("retroactively_updated", 0),
                        },
                        requires_approval=True,
                        approved_by="human_user",
                    )
            except Exception as rule_err:
                logger.warning(f"Could not save learned rule for edited tx #{transaction_id}: {rule_err}")

        return {
            "status": "success",
            "learned_rule": learned_rule,
            "message": f"Transaction #{transaction_id} updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: int):
    """Delete a transaction and any linked asset entries."""
    db = DatabaseManager()
    try:
        with db.pool.begin() as conn:
            conn.execute(sqlalchemy.text("DELETE FROM assets WHERE transaction_id = :id"), {"id": transaction_id})
            res = conn.execute(sqlalchemy.text("DELETE FROM transactions WHERE id = :id"), {"id": transaction_id})
            if res.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"Transaction #{transaction_id} not found")
        return {
            "status": "success",
            "message": f"Transaction #{transaction_id} deleted successfully"
        }
    finally:
        db.close()


@app.get("/analytics/summary")
def get_analytics_summary(month: Optional[str] = None):
    """
    Returns executive financial analytics for the specified month (or All Time):
    - KPIs: Total Income, Total Expenses, Net Cashflow, Savings Rate %
    - Category breakdown with amounts, transaction counts, and percentages
    - Month-over-month trend analysis
    - Financial Vitality / Health Score
    """
    db = DatabaseManager()
    try:
        with db.pool.connect() as conn:
            where_sql = ""
            params = {}
            if month and month.upper() != "ALL":
                where_sql = "WHERE TO_CHAR(t.transaction_date, 'YYYY-MM') = :month"
                params["month"] = month.strip()

            kpi_query = sqlalchemy.text(rf"""
                SELECT 
                    COALESCE(SUM(CASE
                        WHEN UPPER(t.transaction_type) = 'INCOME'
                         AND COALESCE(t.is_self_transfer, FALSE) = FALSE
                         AND LOWER(TRIM(COALESCE(t.description, ''))) !~ '^(prev(ious|ous)?\s+balance|opening\s+balance|initial\s+balance|month\s+end\s+balance|closing\s+balance)'
                        THEN t.amount ELSE 0 END), 0) as income,
                    COALESCE(SUM(CASE
                        WHEN UPPER(t.transaction_type) = 'EXPENSE'
                         AND COALESCE(t.is_self_transfer, FALSE) = FALSE
                         AND LOWER(TRIM(COALESCE(t.description, ''))) !~ '^(prev(ious|ous)?\s+balance|opening\s+balance|initial\s+balance|month\s+end\s+balance|closing\s+balance)'
                        THEN t.amount ELSE 0 END), 0) as expense,
                    COUNT(*) as count
                FROM transactions t
                {where_sql}
            """)
            kpi_row = conn.execute(kpi_query, params).fetchone()
            total_income = float(kpi_row[0]) if kpi_row else 0.0
            total_expense = float(kpi_row[1]) if kpi_row else 0.0
            tx_count = int(kpi_row[2]) if kpi_row else 0
            net_savings = total_income - total_expense
            savings_rate = round((net_savings / total_income * 100), 1) if total_income > 0 else 0.0

            cat_where_clauses = []
            if month and month.upper() != "ALL":
                cat_where_clauses.append("TO_CHAR(t.transaction_date, 'YYYY-MM') = :month")
            cat_where_clauses.append("COALESCE(t.is_self_transfer, FALSE) = FALSE")
            cat_where_clauses.append(
                r"LOWER(TRIM(COALESCE(t.description, ''))) !~ '^(prev(ious|ous)?\s+balance|opening\s+balance|initial\s+balance|month\s+end\s+balance|closing\s+balance)'"
            )
            cat_where_sql = "WHERE " + " AND ".join(cat_where_clauses)

            cat_query = sqlalchemy.text(f"""
                SELECT 
                    c.name as category,
                    t.transaction_type,
                    COUNT(*) as count,
                    COALESCE(SUM(t.amount), 0) as total
                FROM transactions t
                JOIN categories c ON t.category_id = c.id
                {cat_where_sql}
                GROUP BY c.name, t.transaction_type
                ORDER BY total DESC
            """)
            cat_rows = conn.execute(cat_query, params).fetchall()

            expense_categories = []
            income_categories = []

            for r in cat_rows:
                cat_name = r[0]
                t_type = (r[1] or "EXPENSE").upper()
                if t_type == "TRANSFER":
                    continue
                cnt = int(r[2])
                tot = float(r[3])
                pct = round((tot / total_expense * 100), 1) if (t_type == "EXPENSE" and total_expense > 0) else 0.0

                item = {
                    "category": cat_name,
                    "type": t_type,
                    "count": cnt,
                    "total": tot,
                    "percentage": pct
                }
                if t_type == "INCOME":
                    income_categories.append(item)
                else:
                    expense_categories.append(item)

            trend_query = sqlalchemy.text(r"""
                SELECT 
                    TO_CHAR(t.transaction_date, 'YYYY-MM') as m,
                    COALESCE(SUM(CASE
                        WHEN UPPER(t.transaction_type) = 'INCOME'
                         AND COALESCE(t.is_self_transfer, FALSE) = FALSE
                         AND LOWER(TRIM(COALESCE(t.description, ''))) !~ '^(prev(ious|ous)?\s+balance|opening\s+balance|initial\s+balance|month\s+end\s+balance|closing\s+balance)'
                        THEN t.amount ELSE 0 END), 0) as income,
                    COALESCE(SUM(CASE
                        WHEN UPPER(t.transaction_type) = 'EXPENSE'
                         AND COALESCE(t.is_self_transfer, FALSE) = FALSE
                         AND LOWER(TRIM(COALESCE(t.description, ''))) !~ '^(prev(ious|ous)?\s+balance|opening\s+balance|initial\s+balance|month\s+end\s+balance|closing\s+balance)'
                        THEN t.amount ELSE 0 END), 0) as expense,
                    MAX(CASE
                        WHEN LOWER(TRIM(COALESCE(t.description, ''))) ~ '^(prev(ious|ous)?\s+balance|opening\s+balance|initial\s+balance)'
                        THEN t.amount ELSE NULL END) as explicit_opening_balance
                FROM transactions t
                WHERE t.transaction_date IS NOT NULL
                GROUP BY TO_CHAR(t.transaction_date, 'YYYY-MM')
                ORDER BY m ASC
            """)
            trend_rows = conn.execute(trend_query).fetchall()
            reconciled_map = db.get_reconciled_monthly_balances()
            monthly_trends = []
            running_bal = 0.0
            selected_opening_balance = 0.0
            selected_closing_balance = round(net_savings, 2)
            target_m = month.strip() if (month and month.upper() != "ALL") else None

            for tr in trend_rows:
                m_str = tr[0]
                inc = float(tr[1])
                exp = float(tr[2])
                explicit_open = float(tr[3]) if (len(tr) > 3 and tr[3] is not None) else None
                m_net = inc - exp
                rec = reconciled_map.get(m_str)
                if rec:
                    m_open = round(float(rec["opening_balance"]), 2)
                    m_close = round(float(rec["closing_balance"]), 2)
                    running_bal = m_close
                else:
                    m_open = round(explicit_open, 2) if explicit_open is not None else round(running_bal, 2)
                    running_bal = round(m_open + m_net, 2)
                    m_close = running_bal

                if target_m and m_str == target_m:
                    selected_opening_balance = m_open
                    selected_closing_balance = m_close
                elif target_m and m_str < target_m:
                    selected_opening_balance = m_close
                    selected_closing_balance = round(m_close + net_savings, 2)

                try:
                    dt = datetime.strptime(m_str, "%Y-%m")
                    lbl = dt.strftime("%b %Y")
                except Exception:
                    lbl = m_str
                monthly_trends.append({
                    "month": m_str,
                    "label": lbl,
                    "income": inc,
                    "expense": exp,
                    "savings": m_net,
                    "opening_balance": m_open,
                    "closing_balance": m_close,
                    "reconciled": bool(rec),
                    "bank_breakdown": rec,
                })

            if not target_m and monthly_trends:
                selected_opening_balance = monthly_trends[0]["opening_balance"]
                selected_closing_balance = monthly_trends[-1]["closing_balance"]

            health_score = 50
            if total_income > 0:
                if savings_rate >= 20:
                    health_score = min(100, int(75 + (savings_rate - 20) * 0.8))
                elif savings_rate > 0:
                    health_score = int(50 + savings_rate * 1.2)
                else:
                    health_score = max(20, int(50 + savings_rate * 0.5))
            elif total_expense > 0:
                health_score = 40

            health_status = "Healthy"
            if health_score >= 80:
                health_status = "Excellent"
            elif health_score >= 60:
                health_status = "Healthy"
            elif health_score >= 40:
                health_status = "Moderate / Watch"
            else:
                health_status = "Deficit Alert"

            return {
                "selected_month": month or "ALL",
                "kpis": {
                    "total_income": total_income,
                    "total_expenses": total_expense,
                    "net_savings": net_savings,
                    "opening_balance": selected_opening_balance,
                    "closing_balance": selected_closing_balance,
                    "bank_breakdown": reconciled_map.get(target_m) if target_m else None,
                    "savings_rate_pct": savings_rate,
                    "transaction_count": tx_count,
                    "health_score": health_score,
                    "health_status": health_status
                },
                "expense_categories": expense_categories,
                "income_categories": income_categories,
                "monthly_trends": monthly_trends
            }
    finally:
        db.close()


@app.get("/recurring")
def get_recurring_transactions():
    """
    Autonomous detector for recurring subscriptions, utilities, and habitual expenses.
    Identifies repeat charges, calculates frequency and estimated monthly commitments.
    """
    db = DatabaseManager()
    try:
        with db.pool.connect() as conn:
            query = sqlalchemy.text("""
                SELECT 
                    t.description,
                    c.name as category,
                    ROUND(AVG(t.amount)::numeric, 2) as avg_amount,
                    COUNT(*) as frequency,
                    MIN(t.transaction_date) as first_seen,
                    MAX(t.transaction_date) as last_seen
                FROM transactions t
                JOIN categories c ON t.category_id = c.id
                WHERE UPPER(t.transaction_type) = 'EXPENSE'
                GROUP BY t.description, c.name
                HAVING COUNT(*) >= 2
                ORDER BY frequency DESC, avg_amount DESC
                LIMIT 20
            """)
            rows = conn.execute(query).fetchall()

            recurring_items = []
            total_monthly_burn = 0.0

            for r in rows:
                desc = r[0]
                cat = r[1]
                avg_amt = float(r[2])
                freq = int(r[3])
                first_d = str(r[4])
                last_d = str(r[5])

                total_monthly_burn += avg_amt

                recurring_items.append({
                    "description": desc,
                    "category": cat,
                    "average_amount": avg_amt,
                    "frequency": freq,
                    "first_seen": first_d,
                    "last_seen": last_d,
                    "estimated_cadence": "Monthly / Recurring" if freq >= 2 else "Periodic"
                })

            return {
                "total_recurring_items": len(recurring_items),
                "estimated_monthly_burn": total_monthly_burn,
                "items": recurring_items
            }
    finally:
        db.close()


@app.get("/forecast")
def get_future_cost_forecast():
    """
    Multi-Horizon Future Cost Predictor & Financial Preparedness Planner.
    Analyzes historical transactions across:
      1. Previous Week (7-day rolling burn & habitual weekly expenses)
      2. Previous Month (month-over-month recurring bills & unpaid commitments for current month)
      3. Same Month & Next Month of Previous Year (YoY seasonal spikes, festivals, annual renewals)
    Returns predicted upcoming costs, category-by-category forecasts, and an actionable Indian household preparedness plan.
    """
    db = DatabaseManager()
    try:
        with db.pool.connect() as conn:
            # Fetch all non-transfer expense & income rows with dates
            rows = conn.execute(sqlalchemy.text("""
                SELECT 
                    t.id,
                    t.transaction_date,
                    t.amount,
                    t.description,
                    COALESCE(c.name, 'Other') as category,
                    UPPER(COALESCE(t.transaction_type, 'EXPENSE')) as tx_type,
                    COALESCE(t.is_self_transfer, FALSE) as is_transfer
                FROM transactions t
                LEFT JOIN categories c ON t.category_id = c.id
                WHERE t.transaction_date IS NOT NULL
                ORDER BY t.transaction_date DESC
            """)).fetchall()

            if not rows:
                return {
                    "reference_date": str(date.today()),
                    "horizons": {},
                    "preparedness_plan": {
                        "next_7_days_buffer": 0.0,
                        "next_30_days_forecast": 0.0,
                        "remaining_month_estimate": 0.0,
                        "monthly_sinking_fund_target": 0.0,
                        "readiness_score": 100,
                        "actionable_steps": []
                    },
                    "upcoming_predictions": [],
                    "category_forecasts": []
                }

            # Parse records
            expenses = []
            incomes = []
            for r in rows:
                tx_date = r[1] if isinstance(r[1], date) else datetime.strptime(str(r[1])[:10], "%Y-%m-%d").date()
                amt = float(r[2] or 0.0)
                desc = str(r[3] or "Transaction").strip()
                cat = str(r[4] or "Other").strip()
                tx_type = str(r[5] or "EXPENSE").upper()
                is_transfer = bool(r[6]) or tx_type == "TRANSFER" or cat.lower() == "transfer"
                if is_transfer:
                    continue
                item = {
                    "id": r[0],
                    "date": tx_date,
                    "month": tx_date.strftime("%Y-%m"),
                    "day": tx_date.day,
                    "amount": amt,
                    "description": desc,
                    "category": cat,
                }
                if tx_type == "INCOME":
                    incomes.append(item)
                else:
                    expenses.append(item)

            anchor_date = max((x["date"] for x in expenses + incomes), default=date.today())
            curr_year, curr_month = anchor_date.year, anchor_date.month
            curr_ym = f"{curr_year:04d}-{curr_month:02d}"

            # Previous Month (YYYY-MM)
            if curr_month == 1:
                prev_m_year, prev_m_month = curr_year - 1, 12
            else:
                prev_m_year, prev_m_month = curr_year, curr_month - 1
            prev_ym = f"{prev_m_year:04d}-{prev_m_month:02d}"

            # Same Month Last Year & Previous Month Last Year & Next Month Last Year
            same_m_last_year_ym = f"{curr_year - 1:04d}-{curr_month:02d}"
            prev_m_last_year_ym = f"{prev_m_year - 1:04d}-{prev_m_month:02d}"
            if curr_month == 12:
                next_m_last_year_ym = f"{curr_year:04d}-01"
                next_month_label = datetime(curr_year + 1, 1, 1).strftime("%B %Y")
            else:
                next_m_last_year_ym = f"{curr_year - 1:04d}-{curr_month + 1:02d}"
                next_month_label = datetime(curr_year, curr_month + 1, 1).strftime("%B %Y")

            # 1. Weekly Windows (Last 7 days vs Prior 7 days)
            week_start = anchor_date - timedelta(days=7)
            prior_week_start = anchor_date - timedelta(days=14)
            last_7d_exp = [e for e in expenses if week_start < e["date"] <= anchor_date]
            prior_7d_exp = [e for e in expenses if prior_week_start < e["date"] <= week_start]

            last_7d_total = round(sum(e["amount"] for e in last_7d_exp), 2)
            prior_7d_total = round(sum(e["amount"] for e in prior_7d_exp), 2)

            # 2. Monthly Windows
            curr_m_exp = [e for e in expenses if e["month"] == curr_ym]
            prev_m_exp = [e for e in expenses if e["month"] == prev_ym]
            same_m_ly_exp = [e for e in expenses if e["month"] == same_m_last_year_ym]
            prev_m_ly_exp = [e for e in expenses if e["month"] == prev_m_last_year_ym]
            next_m_ly_exp = [e for e in expenses if e["month"] == next_m_last_year_ym]

            curr_m_total = round(sum(e["amount"] for e in curr_m_exp), 2)
            prev_m_total = round(sum(e["amount"] for e in prev_m_exp), 2)
            same_m_ly_total = round(sum(e["amount"] for e in same_m_ly_exp), 2)
            prev_m_ly_total = round(sum(e["amount"] for e in prev_m_ly_exp), 2)
            next_m_ly_total = round(sum(e["amount"] for e in next_m_ly_exp), 2)

            # Overall monthly average across all active months
            months_map: Dict[str, float] = {}
            for e in expenses:
                months_map[e["month"]] = months_map.get(e["month"], 0.0) + e["amount"]
            num_active_months = max(1, len(months_map))
            historical_monthly_avg = round(sum(months_map.values()) / num_active_months, 2)

            inc_months_map: Dict[str, float] = {}
            for inc in incomes:
                inc_months_map[inc["month"]] = inc_months_map.get(inc["month"], 0.0) + inc["amount"]
            avg_monthly_income = round(
                sum(inc_months_map.values()) / max(1, len(inc_months_map)), 2
            ) if inc_months_map else 0.0

            # 3. Category-Level Multi-Horizon Breakdown & Forecast
            all_cats = sorted(list({e["category"] for e in expenses}))
            category_forecasts = []
            predicted_30d_total = 0.0

            for cat in all_cats:
                c_7d = round(sum(e["amount"] for e in last_7d_exp if e["category"] == cat), 2)
                c_curr_m = round(sum(e["amount"] for e in curr_m_exp if e["category"] == cat), 2)
                c_prev_m = round(sum(e["amount"] for e in prev_m_exp if e["category"] == cat), 2)
                c_same_ly = round(sum(e["amount"] for e in same_m_ly_exp if e["category"] == cat), 2)
                c_next_ly = round(sum(e["amount"] for e in next_m_ly_exp if e["category"] == cat), 2)
                c_hist_avg = round(
                    sum(e["amount"] for e in expenses if e["category"] == cat) / num_active_months, 2
                )

                # Weighted forecast combining Previous Month (45%), Historical Monthly Avg (35%), and YoY Seasonal (20%)
                seasonal_ref = max(c_same_ly, c_next_ly) if (c_same_ly > 0 or c_next_ly > 0) else c_hist_avg
                base_prev = c_prev_m if c_prev_m > 0 else c_hist_avg
                predicted_cat = round(0.45 * base_prev + 0.35 * c_hist_avg + 0.20 * seasonal_ref, 2)
                if predicted_cat <= 0 and c_curr_m > 0:
                    predicted_cat = c_curr_m

                predicted_30d_total += predicted_cat

                trend_signal = "Stable"
                if c_prev_m > c_hist_avg * 1.25 and c_prev_m > 1000:
                    trend_signal = "Rising (MoM Spike)"
                elif c_same_ly > c_hist_avg * 1.2 or c_next_ly > c_hist_avg * 1.2:
                    trend_signal = "Seasonal High (YoY)"

                category_forecasts.append({
                    "category": cat,
                    "last_7_days": c_7d,
                    "current_month_so_far": c_curr_m,
                    "previous_month": c_prev_m,
                    "same_month_last_year": c_same_ly,
                    "next_month_last_year": c_next_ly,
                    "historical_monthly_avg": c_hist_avg,
                    "predicted_next_30_days": predicted_cat,
                    "recommended_weekly_reserve": round(predicted_cat / 4.33, 2),
                    "trend_signal": trend_signal,
                })

            category_forecasts.sort(key=lambda x: x["predicted_next_30_days"], reverse=True)
            predicted_30d_total = round(predicted_30d_total, 2)

            # 4. Itemized Upcoming Cost Predictions (Merchants / Commitments / Seasonal)
            upcoming_predictions = []
            seen_keys = set()

            # A) Recurring merchants across history that haven't been paid yet in the latter half of this month or due next cycle
            merchant_groups: Dict[str, List[Dict[str, Any]]] = {}
            for e in expenses:
                norm_key = (e["description"][:28].upper(), e["category"])
                merchant_groups.setdefault(norm_key, []).append(e)

            curr_m_descriptions = {e["description"][:28].upper() for e in curr_m_exp}

            for (desc_key, cat), group in merchant_groups.items():
                distinct_months = {g["month"] for g in group}
                if len(distinct_months) >= 2:
                    avg_amt = round(sum(g["amount"] for g in group) / len(group), 2)
                    avg_day = int(round(sum(g["day"] for g in group) / len(group)))
                    sample_desc = group[0]["description"]
                    already_paid_this_month = desc_key in curr_m_descriptions

                    if not already_paid_this_month:
                        due_window = (
                            f"Expected around Day {max(1, avg_day - 2)}–{min(31, avg_day + 2)} of this month"
                            if avg_day >= anchor_date.day
                            else "Due any day now (unpaid this month)"
                        )
                        horizon_tag = "This Month (Unpaid Recurring)"
                    else:
                        due_window = f"Next cycle around Day {max(1, avg_day - 2)}–{min(31, avg_day + 2)} next month"
                        horizon_tag = "Next Month Cycle"

                    upcoming_predictions.append({
                        "title": sample_desc,
                        "category": cat,
                        "predicted_amount": avg_amt,
                        "horizon": horizon_tag,
                        "expected_window": due_window,
                        "basis": f"Occurred across {len(distinct_months)} months (Avg ₹{avg_amt:,.0f})",
                        "confidence": min(98, 75 + len(distinct_months) * 5),
                    })
                    seen_keys.add(desc_key)

            # B) Significant items from Previous Month (e.g. Sep 2026) that typically repeat or need follow-up buffer
            for e in sorted(prev_m_exp, key=lambda x: x["amount"], reverse=True):
                desc_key = e["description"][:28].upper()
                if desc_key in seen_keys or e["amount"] < 500:
                    continue
                if e["category"] in ("SIP", "Utilities", "Rent", "Debt", "Investment", "Food", "Health/Medical"):
                    upcoming_predictions.append({
                        "title": e["description"],
                        "category": e["category"],
                        "predicted_amount": round(e["amount"], 2),
                        "horizon": "Monthly Pattern (From Last Month)",
                        "expected_window": f"Around Day {max(1, e['day'] - 2)}–{min(31, e['day'] + 2)} of month",
                        "basis": f"Paid on {e['date'].strftime('%d %b %Y')} (Previous Month)",
                        "confidence": 82,
                    })
                    seen_keys.add(desc_key)
                if len(upcoming_predictions) >= 10:
                    break

            # C) Year-over-Year (Same Month or Next Month of Previous Year: Oct/Nov 2025) Seasonal Costs
            yoy_candidates = sorted(same_m_ly_exp + next_m_ly_exp, key=lambda x: x["amount"], reverse=True)
            for e in yoy_candidates:
                desc_key = e["description"][:28].upper()
                if desc_key in seen_keys or e["amount"] < 800:
                    continue
                upcoming_predictions.append({
                    "title": e["description"],
                    "category": e["category"],
                    "predicted_amount": round(e["amount"], 2),
                    "horizon": "Annual / Seasonal Lookback (Last Year)",
                    "expected_window": f"Seasonal window ({e['date'].strftime('%b')} cycle, ~Day {e['day']})",
                    "basis": f"Historical spend on {e['date'].strftime('%d %b %Y')} (Last Year)",
                    "confidence": 78,
                })
                seen_keys.add(desc_key)
                if len(upcoming_predictions) >= 14:
                    break

            # Sort upcoming predictions so unpaid current-month & highest impact items appear first
            upcoming_predictions.sort(
                key=lambda x: (0 if "This Month" in x["horizon"] else 1, -x["predicted_amount"])
            )

            # 5. Calculate Preparedness Plan Numbers
            weekly_baseline = max(last_7d_total, round(predicted_30d_total / 4.33, 2))
            next_7_days_buffer = round(weekly_baseline * 1.15, 2)  # 15% safety cushion
            remaining_month_estimate = round(max(0.0, predicted_30d_total - curr_m_total), 2)
            peak_month_spend = max(months_map.values()) if months_map else predicted_30d_total
            seasonal_surge_gap = max(0.0, peak_month_spend - historical_monthly_avg)
            monthly_sinking_fund_target = round(max(predicted_30d_total * 0.15, seasonal_surge_gap / 6.0), 2)

            # Actionable Mentor-Style Indian Financial Preparedness Steps
            top_cat = category_forecasts[0] if category_forecasts else None
            actionable_steps = [
                {
                    "badge": "7-Day Liquidity Rule",
                    "title": f"Keep ₹{next_7_days_buffer:,.0f} Ready for Next 7 Days",
                    "detail": (
                        f"Based on your trailing weekly burn (₹{last_7d_total:,.0f}) and upcoming recurring bills, "
                        f"maintain at least ₹{next_7_days_buffer:,.0f} in your primary bank account this week."
                    ),
                },
                {
                    "badge": "Rest-of-Month Forecast",
                    "title": f"Prepare ₹{remaining_month_estimate:,.0f} for Remaining {anchor_date.strftime('%B')} Costs",
                    "detail": (
                        f"You have spent ₹{curr_m_total:,.0f} so far in {anchor_date.strftime('%B %Y')}. "
                        f"Compared to last month (₹{prev_m_total:,.0f}) and {same_m_last_year_ym} last year (₹{same_m_ly_total:,.0f}), "
                        f"expect ~₹{remaining_month_estimate:,.0f} in additional outflows over the next 2–3 weeks."
                    ),
                },
                {
                    "badge": "Year-over-Year Seasonal Alert",
                    "title": f"Seasonal Lookback: {same_m_last_year_ym} (₹{same_m_ly_total:,.0f}) & {next_m_last_year_ym} (₹{next_m_ly_total:,.0f})",
                    "detail": (
                        f"In the same period last year, your expenses were ₹{same_m_ly_total:,.0f} in {same_m_last_year_ym} "
                        f"and ₹{next_m_ly_total:,.0f} in {next_m_last_year_ym}. Plan ahead for seasonal shopping, travel, and family commitments."
                    ),
                },
                {
                    "badge": "Future Sinking Fund",
                    "title": f"Auto-Transfer ₹{monthly_sinking_fund_target:,.0f}/month (₹{round(monthly_sinking_fund_target / 4.33):,.0f}/week) to a Future Buffer",
                    "detail": (
                        f"Most households get caught off guard by irregular quarterly/annual spikes (peak month: ₹{peak_month_spend:,.0f}). "
                        f"Setting aside ₹{round(monthly_sinking_fund_target / 4.33):,.0f} every week builds a stress-free buffer before bills arrive."
                    ),
                },
            ]

            return {
                "reference_date": str(anchor_date),
                "horizons": {
                    "last_7_days": {
                        "label": "Previous 7 Days",
                        "total_expense": last_7d_total,
                        "prior_7_days_expense": prior_7d_total,
                        "tx_count": len(last_7d_exp),
                    },
                    "current_month": {
                        "month": curr_ym,
                        "label": anchor_date.strftime("%B %Y"),
                        "total_expense": curr_m_total,
                        "tx_count": len(curr_m_exp),
                    },
                    "previous_month": {
                        "month": prev_ym,
                        "label": datetime(prev_m_year, prev_m_month, 1).strftime("%B %Y"),
                        "total_expense": prev_m_total,
                        "tx_count": len(prev_m_exp),
                    },
                    "same_month_last_year": {
                        "month": same_m_last_year_ym,
                        "label": datetime(curr_year - 1, curr_month, 1).strftime("%B %Y"),
                        "total_expense": same_m_ly_total,
                        "tx_count": len(same_m_ly_exp),
                    },
                    "prev_month_last_year": {
                        "month": prev_m_last_year_ym,
                        "label": datetime(prev_m_year - 1, prev_m_month, 1).strftime("%B %Y"),
                        "total_expense": prev_m_ly_total,
                        "tx_count": len(prev_m_ly_exp),
                    },
                    "next_month_last_year": {
                        "month": next_m_last_year_ym,
                        "label": datetime.strptime(next_m_last_year_ym, "%Y-%m").strftime("%B %Y"),
                        "total_expense": next_m_ly_total,
                        "tx_count": len(next_m_ly_exp),
                    },
                    "historical_monthly_avg": historical_monthly_avg,
                    "avg_monthly_income": avg_monthly_income,
                },
                "preparedness_plan": {
                    "next_7_days_buffer": next_7_days_buffer,
                    "next_30_days_forecast": predicted_30d_total,
                    "remaining_month_estimate": remaining_month_estimate,
                    "monthly_sinking_fund_target": monthly_sinking_fund_target,
                    "weekly_sinking_fund_target": round(monthly_sinking_fund_target / 4.33, 2),
                    "peak_historical_month_spend": round(peak_month_spend, 2),
                    "actionable_steps": actionable_steps,
                },
                "upcoming_predictions": upcoming_predictions,
                "category_forecasts": category_forecasts,
            }
    finally:
        db.close()


@app.get("/budgets")
def get_budgets(month: Optional[str] = None):
    """
    Compare spending against monthly category budgets.
    Returns budgeted amount, actual spend, utilization %, and warning indicators.
    """
    db = DatabaseManager()
    try:
        with db.pool.connect() as conn:
            where_sql = ""
            params = {}
            if month and month.upper() != "ALL":
                where_sql = "WHERE TO_CHAR(t.transaction_date, 'YYYY-MM') = :month AND UPPER(t.transaction_type) = 'EXPENSE'"
                params["month"] = month.strip()
            else:
                where_sql = "WHERE UPPER(t.transaction_type) = 'EXPENSE'"

            query = sqlalchemy.text(f"""
                SELECT c.name as category, COALESCE(SUM(t.amount), 0) as spent
                FROM transactions t
                JOIN categories c ON t.category_id = c.id
                {where_sql}
                GROUP BY c.name
            """)
            spent_map = {row[0]: float(row[1]) for row in conn.execute(query, params).fetchall()}

            # Budgets live in the database since Phase 1 (they used to be lost on restart).
            budget_rows = conn.execute(sqlalchemy.text(
                "SELECT category, monthly_budget FROM budgets ORDER BY category"
            )).fetchall()

            budget_list = []
            total_budget = 0.0
            total_spent = 0.0

            for cat, allocated_raw in budget_rows:
                allocated = float(allocated_raw)
                spent = spent_map.get(cat, 0.0)
                utilization = round((spent / allocated * 100), 1) if allocated > 0 else 0.0
                total_budget += allocated
                total_spent += spent

                status = "ok"
                if utilization >= 100:
                    status = "exceeded"
                elif utilization >= 80:
                    status = "warning"

                budget_list.append({
                    "category": cat,
                    "budget": allocated,
                    "spent": spent,
                    "remaining": max(0.0, allocated - spent),
                    "utilization_pct": utilization,
                    "status": status
                })

            budget_list.sort(key=lambda x: x["spent"], reverse=True)

            return {
                "month": month or "ALL",
                "total_budget": total_budget,
                "total_spent": total_spent,
                "overall_utilization_pct": round((total_spent / total_budget * 100), 1) if total_budget > 0 else 0,
                "categories": budget_list
            }
    finally:
        db.close()


@app.post("/budgets")
def update_budget(payload: BudgetUpdate):
    """Update user's spending target for a category (stored in the `budgets` table)."""
    if payload.monthly_budget < 0:
        raise HTTPException(status_code=400, detail="Budget amount must be positive")
    category = payload.category.strip()
    if not category or len(category) > 100:
        raise HTTPException(status_code=400, detail="Category name must be 1-100 characters.")
    if payload.monthly_budget > 1e9:
        raise HTTPException(status_code=400, detail="Budget amount is too large.")
    db = DatabaseManager()
    try:
        with db.pool.begin() as conn:
            conn.execute(sqlalchemy.text("""
                INSERT INTO budgets (category, monthly_budget, updated_at)
                VALUES (:cat, :amt, CURRENT_TIMESTAMP)
                ON CONFLICT (category) DO UPDATE
                SET monthly_budget = EXCLUDED.monthly_budget, updated_at = CURRENT_TIMESTAMP
            """), {"cat": category, "amt": round(float(payload.monthly_budget), 2)})
    finally:
        db.close()
    return {
        "status": "success",
        "message": f"Budget for {category} updated to ₹{payload.monthly_budget:,.2f}"
    }


@app.get("/insights", dependencies=[Depends(ai_quota("insights"))])
def get_ai_insights():
    """Endpoint to trigger the Financial Advisor agent."""
    advisor = _financial_advisor()
    insights = advisor.generate_insights()
    return {"insights": insights}


@app.post("/insights/chat", dependencies=[Depends(ai_quota("chat"))])
def chat_with_advisor(request: ChatRequest):
    """Multi-turn conversational endpoint for the AI Financial Advisor.
    Stateless endpoint: Client tracks conversation history and provides it with each turn.
    """
    try:
        advisor = _financial_advisor()
        reply = advisor.chat(request.message, request.history)
        return {"reply": reply}
    except Exception as e:
        logger.error(f"Error in chat_with_advisor: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Advisor chat error: {str(e)}")


# ============================================================================
# FDE ENTERPRISE AGENT ORGANS (EVALUATION FLYWHEEL & MCP TOOL PLANE)
# ============================================================================

@app.get("/eval/benchmark", dependencies=[Depends(ai_quota("eval"))])
def get_evaluation_benchmark():
    """FDE Pillar 3 (SCALE): Executes the automated quality autorater flywheel.
    Evaluates Math Reconciliation, Extraction Completeness, Category F1, and Fiduciary Soundness.
    """
    try:
        report = run_full_evaluation_suite()
        return report.model_dump()
    except Exception as e:
        logger.error(f"Autorater benchmark failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Evaluation error: {str(e)}")


@app.get("/mcp/tools")
def list_mcp_tools():
    """FDE Pillar 1 (BUILD): Exposes the Model Context Protocol (MCP) tool catalogue.
    Provides standard JSON-RPC schema contracts for all financial operations.
    """
    server = LedgerMCPServer()
    return {
        "protocol_version": server.PROTOCOL_VERSION,
        "server": server.SERVER_INFO,
        "tools": server.list_tools(),
    }



@app.post("/mcp/execute")
def execute_mcp_tool(payload: MCPExecuteRequest):
    """FDE Pillar 1 (BUILD): Executes a tool on the decoupled Ledger MCP Server."""
    server = LedgerMCPServer()
    res = server.execute_tool(payload.tool_name, payload.arguments)
    if res.get("is_error"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res


# ==============================================================================
# GMAIL BANK ALERT & MONTHLY STATEMENT SYNC ENDPOINTS
# ==============================================================================
from gmail_sync import GmailBankSyncService


@app.get("/api/gmail/settings", dependencies=[Depends(require_gmail_enabled)])
def get_gmail_sync_settings():
    """Retrieves Gmail Bank Sync configuration and sync statistics."""
    service = GmailBankSyncService()
    return service.get_settings()


@app.post("/api/gmail/settings", dependencies=[Depends(require_gmail_enabled)])
def save_gmail_sync_settings(payload: GmailSettingsRequest):
    """Saves and verifies Gmail IMAP App Password credentials for live bank syncing."""
    try:
        service = GmailBankSyncService()
        return service.save_settings(
            email_address=payload.email_address,
            app_password=payload.app_password,
            pdf_password=payload.pdf_password,
            allowed_senders=payload.allowed_senders,
            auto_sync_enabled=payload.auto_sync_enabled,
            sync_interval_hours=payload.sync_interval_hours,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error saving Gmail settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to save Gmail settings: {str(e)}")


@app.post("/api/gmail/sync", dependencies=[Depends(require_gmail_enabled)])
async def trigger_gmail_sync(payload: GmailSyncRequest = GmailSyncRequest()):
    """Triggers an on-demand Gmail sync for daily bank alerts and monthly statements."""
    try:
        service = GmailBankSyncService()
        result = await asyncio.to_thread(
            service.sync_now,
            payload.days_back,
            payload.include_statements,
        )
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error during Gmail sync: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Gmail sync failed: {str(e)}")


class UnlockStatementRequest(BaseModel):
    # Empty retries a statement that is already unlocked but wasn't imported (DEC-060).
    password: str = Field("", description="Password to unlock the bank statement PDF (empty to retry an unlocked one)")


@app.get("/api/gmail/statements", dependencies=[Depends(require_gmail_enabled)])
def get_exported_gmail_statements():
    """Returns all exported monthly statement PDFs (HDFC, Kotak, SBI) grouped by month."""
    service = GmailBankSyncService()
    return service.get_exported_statements()


@app.get("/api/gmail/statements/{stmt_id}/download", dependencies=[Depends(require_gmail_enabled)])
def download_exported_statement_pdf(stmt_id: int):
    """Downloads an exported monthly bank statement PDF (from the private bucket on Cloud Run)."""
    db = DatabaseManager()
    with db.pool.connect() as conn:
        row = conn.execute(sqlalchemy.text(
            "SELECT file_name, file_path FROM exported_statement_pdfs WHERE id = :id LIMIT 1"
        ), {"id": stmt_id}).fetchone()
    if not row or not statement_store.exists(row[1]):
        raise HTTPException(status_code=404, detail="Exported PDF statement not found.")
    file_name, location = row[0], row[1]
    if not location.startswith(statement_store.GCS_SCHEME):
        return FileResponse(location, media_type="application/pdf", filename=file_name)
    safe_name = re.sub(r'[^A-Za-z0-9._ ()-]', "_", os.path.basename(file_name)) or "statement.pdf"
    return Response(
        content=statement_store.read_bytes(location),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@app.post("/api/gmail/statements/{stmt_id}/unlock", dependencies=[Depends(require_gmail_enabled)])
async def unlock_exported_statement_pdf(stmt_id: int, payload: UnlockStatementRequest):
    """Unlocks an encrypted bank statement PDF (or retries a failed import) and imports/reconciles its transactions."""
    try:
        service = GmailBankSyncService()
        return await asyncio.to_thread(service.unlock_and_sync_statement, stmt_id, payload.password)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to unlock statement #{stmt_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def _gmail_sync_due(settings: Dict[str, Any]) -> bool:
    """True when Gmail sync is configured, auto-sync is on, and the user's interval has passed."""
    if not (settings.get("configured") and settings.get("auto_sync_enabled")):
        return False
    interval_hrs = max(1, int(settings.get("sync_interval_hours", 24)))
    last_sync_str = settings.get("last_synced_at")
    if not last_sync_str:
        return True
    try:
        last_dt = datetime.fromisoformat(last_sync_str.replace("Z", "+00:00")).replace(tzinfo=None)
        return (datetime.utcnow() - last_dt).total_seconds() >= interval_hrs * 3600
    except Exception:
        return True


async def _daily_gmail_sync_worker():
    """Background worker that automatically checks Gmail daily for new bank transaction alerts.

    Legacy, opt-in only (ENABLE_GMAIL_BACKGROUND_WORKER=1). On Cloud Run, Cloud Scheduler calls
    /internal/gmail-sync instead: instances scale to zero, so an in-process loop is unreliable.
    """
    await asyncio.sleep(15)
    while True:
        try:
            service = GmailBankSyncService()
            if _gmail_sync_due(service.get_settings()):
                logger.info("Running scheduled daily Gmail Bank Alert & Statement sync...")
                await asyncio.to_thread(service.sync_now, 3, True)
        except Exception as e:
            logger.warning(f"Background Gmail sync check encountered an error: {e}")

        await asyncio.sleep(3600)


# ==============================================================================
# INTERNAL ROUTES FOR CLOUD SCHEDULER (Phase 1, DEC-025)
# FirebaseAuthMiddleware only lets Cloud Scheduler's OIDC token in here, never a user.
# ==============================================================================

@app.post("/internal/gmail-sync")
async def internal_gmail_sync():
    """Hourly tick (personal only): syncs Gmail when the interval set on Bank Sync has passed."""
    if config.IS_DEMO:
        raise HTTPException(status_code=404, detail="Not found")
    service = GmailBankSyncService()
    settings = await asyncio.to_thread(service.get_settings)
    if not _gmail_sync_due(settings):
        return {
            "status": "skipped",
            "reason": "not configured, auto-sync off, or not due yet",
            "interval_hours": settings.get("sync_interval_hours"),
            "last_synced_at": settings.get("last_synced_at"),
        }
    result = await asyncio.to_thread(service.sync_now, 3, True)
    keys = ("alerts_synced", "statements_synced", "pdfs_exported", "emails_processed", "synced_at")
    return {"status": "synced", **{k: result.get(k) for k in keys if k in result}}


@app.post("/internal/demo-reset")
async def internal_demo_reset():
    """Nightly (demo only): restores the sample data, clearing anything visitors changed or
    uploaded. It also keeps the free Supabase demo project from pausing for inactivity."""
    if not config.IS_DEMO:
        raise HTTPException(status_code=404, detail="Not found")
    import demo_seed

    try:
        summary = await asyncio.to_thread(demo_seed.reset_demo_data, DatabaseManager().pool)
    except demo_seed.DemoResetRefused as e:
        logger.error(f"Demo reset refused: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    logger.info(f"Demo data reset: {summary}")
    return {"status": "reset", "summary": summary}


# ==============================================================================
# FDE 4-PILLAR ENTERPRISE ENDPOINTS (BUILD · GOVERN · SCALE · OPTIMIZE)
# ==============================================================================


class FourAgentWorkflowRequest(BaseModel):
    transactions: List[Dict[str, Any]] = []
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    existing_alerts: Optional[List[Dict[str, Any]]] = None
    current_balance: Optional[float] = None
    upcoming_commitments: Optional[List[Dict[str, Any]]] = None
    user_question: Optional[str] = None


class LearnedRuleRequest(BaseModel):
    payee_pattern: Optional[str] = None
    description: Optional[str] = None
    category: str
    transaction_type: str = "EXPENSE"
    human_approved: bool = True


class MasterResolveRequest(BaseModel):
    month: Optional[str] = None
    include_locked_other: bool = True
    auto_apply: bool = False
    transaction_ids: Optional[List[int]] = None


class MasterApplyRequest(BaseModel):
    updates: List[Dict[str, Any]] = Field(default_factory=list)
    human_approved: bool = True


@app.post("/mcp/jsonrpc")
def handle_mcp_jsonrpc(request_payload: Dict[str, Any], agent_id: Optional[str] = Query(None)):
    """Official Model Context Protocol (MCP) JSON-RPC 2.0 endpoint (`initialize`, `ping`, `tools/list`, `tools/call`)."""
    from mcp import LedgerMCPServer

    server = LedgerMCPServer()
    return server.handle_jsonrpc(request_payload, agent_id=agent_id)


@app.get("/.well-known/agent.json")
def get_root_agent_card():
    """Official A2A discovery endpoint (`/.well-known/agent.json`): returns the root Orchestrator's
    `AgentCard` along with links to the 4 deployed Specialist Agent Cards in the Agent Registry."""
    from mcp import AgenticCFOOrchestrator

    orchestrator = AgenticCFOOrchestrator()
    root_card = orchestrator.a2a.get_agent_card("orchestrator") or {}
    root_card["specialist_agent_cards"] = orchestrator.a2a.list_agents(include_orchestrator=False)
    return root_card


@app.get("/a2a/agents/{agent_id}/.well-known/agent.json")
def get_specialist_agent_card(agent_id: str):
    """Official A2A Agent Card discovery endpoint for any registered agent (`orchestrator` or specialist)."""
    from mcp import AgenticCFOOrchestrator

    orchestrator = AgenticCFOOrchestrator()
    card = orchestrator.a2a.get_agent_card(agent_id)
    if not card:
        raise HTTPException(status_code=404, detail=f"Agent Card '{agent_id}' not found in Agent Registry")
    return card


@app.get("/a2a/registry")
def get_a2a_agent_registry(include_gcp_live: bool = Query(False)):
    """Returns all deployed A2A Agent Cards in the Agent Registry (plus live Google Cloud Agent Registry
    `agentregistry.googleapis.com` status when `include_gcp_live=true`)."""
    from mcp import AgenticCFOOrchestrator

    orchestrator = AgenticCFOOrchestrator()
    cards = orchestrator.a2a.list_agents(include_orchestrator=True)
    response: Dict[str, Any] = {
        "registry_status": "ONLINE",
        "gcp_project_id": config.PROJECT_ID,
        "gcp_agent_registry_api": "agentregistry.googleapis.com/v1alpha",
        "total_agents": len(cards),
        "agent_cards": cards,
    }
    if include_gcp_live:
        response["gcp_cloud_registry"] = orchestrator.a2a.registry.fetch_from_gcp_agent_registry()
    return response


@app.post("/a2a/registry/register")
def register_custom_agent_card(card_payload: Dict[str, Any]):
    """Registers or updates an A2A `AgentCard` in the Agent Registry."""
    from mcp import A2AOrchestrator, AgentCard

    bus = A2AOrchestrator()
    card = AgentCard(**card_payload)
    deployed = bus.register_agent(
        card.agent_id,
        lambda intent, payload: {"status": "ACK", "agent_id": card.agent_id, "intent": intent, "payload": payload},
        card=card,
    )
    return {"status": "REGISTERED", "agent_card": deployed.model_dump()}


@app.post("/a2a/registry/sync-gcp")
def sync_agents_to_google_cloud_registry():
    """Synchronizes all 5 A2A `AgentCard` services, the `LedgerMCPServer`, and A2A/MCP `Bindings`
    directly to Google Cloud Agent Registry (`agentregistry.googleapis.com/v1alpha`)."""
    from mcp import AgenticCFOOrchestrator

    orchestrator = AgenticCFOOrchestrator()
    tools = orchestrator.mcp_server.list_tools()
    return orchestrator.a2a.registry.sync_to_gcp_agent_registry(mcp_tools=tools)


@app.post("/a2a/agents/{agent_id}/tasks/send")
def send_task_to_agent_card_endpoint(agent_id: str, payload: Dict[str, Any]):
    """Official A2A Agent Card `tasks/send` endpoint: invokes `agent_id` via its registered `AgentCard`."""
    from mcp import AgenticCFOOrchestrator, AgentMessage

    orchestrator = AgenticCFOOrchestrator()
    intent = payload.get("intent") or payload.get("skill_id") or payload.get("skill") or ""
    task_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    msg = AgentMessage(
        sender_agent=payload.get("sender_agent", "orchestrator"),
        target_agent=agent_id,
        intent=intent,
        payload=task_payload,
        workload_token=payload.get("workload_token"),
    )
    return orchestrator.a2a.delegate_task(msg)


@app.get("/a2a/agents")
def list_a2a_specialist_agents():
    """Lists all 4 specialist agents registered on the A2A bus along with their A2A Agent Cards,
    Google Cloud Agent Registry URNs, SPIFFE IDs, and skills."""
    from mcp import A2AOrchestrator

    bus = A2AOrchestrator()
    return {"agents": bus.list_agents()}


@app.post("/a2a/delegate")
def delegate_a2a_task(payload: Dict[str, Any]):
    """Dispatches a task envelope (`AgentMessage`) over the Agent2Agent (A2A) bus via the target's `AgentCard`."""
    from mcp import A2AOrchestrator, AgentMessage

    bus = A2AOrchestrator()
    msg = AgentMessage(**payload)
    return bus.delegate_task(msg)


@app.post("/agents/workflow")
def run_four_agent_cfo_workflow(payload: FourAgentWorkflowRequest):
    """Executes the end-to-end 4-Agent CFO Workflow (Ingestion -> Reconciliation -> Clarification -> Advisor)."""
    from mcp import AgenticCFOOrchestrator

    orchestrator = AgenticCFOOrchestrator()
    return orchestrator.run_four_agent_workflow(
        transactions=payload.transactions,
        opening_balance=payload.opening_balance,
        closing_balance=payload.closing_balance,
        existing_alerts=payload.existing_alerts,
        current_balance=payload.current_balance,
        upcoming_commitments=payload.upcoming_commitments,
        user_question=payload.user_question,
    )


@app.post("/agents/master-resolve")
def run_master_category_resolver(payload: MasterResolveRequest = MasterResolveRequest()):
    """Executes the Master Category Resolution & Ledger Intelligence Agent across all 4 specialist agents."""
    from mcp import AgenticCFOOrchestrator

    orchestrator = AgenticCFOOrchestrator()
    return orchestrator.master_resolve_transactions(
        month=payload.month,
        include_locked_other=payload.include_locked_other,
        auto_apply=payload.auto_apply,
        transaction_ids=payload.transaction_ids,
    )


@app.post("/agents/master-resolve/apply")
def apply_master_category_resolutions(payload: MasterApplyRequest):
    """Commits approved Master Agent category/type updates to PostgreSQL and teaches reusable payee rules."""
    from mcp import AgenticCFOOrchestrator

    orchestrator = AgenticCFOOrchestrator()
    result = orchestrator.apply_master_resolutions(
        updates=payload.updates,
        human_approved=payload.human_approved,
    )
    if result.get("status") == "FAILED":
        raise HTTPException(status_code=403, detail=result.get("error", "Failed to apply master resolutions"))
    return result


class OmnipresentCommandRequest(BaseModel):
    message: Optional[str] = None
    command: Optional[str] = None
    current_screen: str = "transactions"
    current_month: str = "ALL"
    search_query: str = ""
    history: Optional[List[Dict[str, Any]]] = None
    auto_execute: bool = True


@app.post("/agents/orchestrator/command")
def run_omnipresent_jarvis_command(payload: OmnipresentCommandRequest):
    """Global 'Jarvis' Master Orchestrator endpoint available across every page of the web app.
    Coordinates all 4 specialist agents (ingestion, reconciliation, clarification, advisor) and
    executes MCP tools to answer questions, walk through page metrics, or resolve/update categories.
    """
    from mcp import AgenticCFOOrchestrator

    user_msg = (payload.message or payload.command or "").strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Either 'message' or 'command' is required.")

    orchestrator = AgenticCFOOrchestrator()
    return orchestrator.handle_omnipresent_command(
        message=user_msg,
        current_screen=payload.current_screen,
        current_month=payload.current_month,
        search_query=payload.search_query,
        history=payload.history,
        auto_execute=payload.auto_execute,
    )


@app.get("/agents/learned-rules")
def get_clarification_learned_rules():
    """Returns all payee categorization rules learned by the Clarification Agent from user confirmations."""
    db = DatabaseManager()
    try:
        rules = db.get_learned_rules()
        if not rules:
            defaults = [
                ("UPI/RAHUL", "Rent", "EXPENSE", False, "voice_hitl"),
                ("ZERODHA BROKING", "SIP", "EXPENSE", False, "clarification_agent"),
                ("SWIGGY INSTAMART", "Food", "EXPENSE", False, "manual_ui_edit"),
                ("BESCOM BANGALORE", "Utilities", "EXPENSE", False, "clarification_agent"),
            ]
            for pat, cat, ttype, is_trans, src in defaults:
                db.save_learned_rule(
                    payee_pattern=pat,
                    category=cat,
                    transaction_type=ttype,
                    is_self_transfer=is_trans,
                    source=src,
                )
            rules = db.get_learned_rules()
        return {"rules": rules}
    finally:
        db.close()


@app.post("/agents/learned-rules")
def create_clarification_learned_rule(payload: LearnedRuleRequest):
    """Teaches the Clarification Agent a persistent payee categorization rule (requires human_approved=True)."""
    from mcp import AgenticCFOOrchestrator

    orchestrator = AgenticCFOOrchestrator()
    result = orchestrator.resolve_and_learn(
        description=payload.description or payload.payee_pattern or "",
        payee_pattern=payload.payee_pattern,
        user_reply=payload.category,
        human_approved=payload.human_approved,
    )
    if result.get("status") == "FAILED":
        raise HTTPException(status_code=403, detail=result.get("error", "Rule creation failed"))
    return result


@app.delete("/agents/learned-rules")
def delete_clarification_learned_rule(pattern: str = Query(..., min_length=1)):
    """Deletes a learned payee categorization rule by pattern."""
    db = DatabaseManager()
    try:
        removed = db.delete_learned_rule(pattern)
        return {"status": "DELETED" if removed else "NOT_FOUND", "pattern": pattern.strip().upper()}
    finally:
        db.close()


@app.get("/agents/audit-trail")
def get_governance_audit_trail(limit: int = Query(50, ge=1, le=200)):
    """Returns recent SPIFFE/A2A/MCP agent governance audit logs."""
    db = DatabaseManager()
    try:
        logs = db.get_agent_audit_logs(limit=limit)
        if not logs:
            from mcp import AgenticCFOOrchestrator

            orchestrator = AgenticCFOOrchestrator()
            cards = orchestrator.a2a.list_agents(include_orchestrator=True)
            db.log_agent_audit(
                agent_id="orchestrator",
                spiffe_id="spiffe://finance-cfo.local/ns/prod/sa/orchestrator",
                action="A2A_REGISTRY_HEARTBEAT",
                status="VERIFIED",
                details={
                    "registered_agents": len(cards),
                    "gcp_registry": "agentregistry.googleapis.com/v1alpha",
                    "mcp_tools": len(orchestrator.mcp_server.list_tools()),
                },
            )
            logs = db.get_agent_audit_logs(limit=limit)
        return {"audit_logs": logs}
    finally:
        db.close()


@app.get("/telemetry/traces")
def get_opentelemetry_traces(limit: int = Query(20, ge=1, le=100)):
    """Returns recent OpenTelemetry agent workflow traces and identifies the weakest step across runs."""
    from telemetry import find_weakest_step, get_recent_traces

    return {
        "weakest_step": find_weakest_step(),
        "traces": get_recent_traces(limit=limit),
    }



@app.on_event("startup")
def verify_required_secrets():
    """Refuses to start without the database password (Phase 0: no credentials in code).

    Raises secret_store.SecretNotFoundError with setup instructions if it is missing.
    """
    config.get_db_password()
    logger.info("Required secrets are available (database password loaded from env/Secret Manager).")
    logger.info(
        f"Environment: {config.APP_ENV} (database project {config.SUPABASE_PROJECT_REF}); "
        f"allowed sign-ins: {len(config.AUTH_ALLOWED_USERS.split(','))}; "
        f"AI daily limits: {config.AI_DAILY_LIMITS or 'none'}; "
        f"statements: {statement_store.describe_root()}"
    )


@app.on_event("startup")
async def start_background_gmail_scheduler():
    if config.ENABLE_GMAIL_BACKGROUND_WORKER and not config.IS_DEMO:
        asyncio.create_task(_daily_gmail_sync_worker())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8080, reload=True)