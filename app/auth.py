# auth.py
"""Sign-in checks for every API route (Phase 1, see DEC-022 / DEC-025 in decision.md).

How it works:
- The browser signs in with Firebase Auth (Google for the owner, email + password for the
  demo) and sends `Authorization: Bearer <Firebase ID token>` with every request.
- `FirebaseAuthMiddleware` verifies that token on every route except a short public list
  (`/health`, the local SPA files and, when enabled, the API docs). New routes are therefore
  protected by default; nothing has to remember to add a check.
- A valid token is not enough: the account must also be on this backend's allowlist
  (`config.AUTH_ALLOWED_USERS`, optionally pinned to a sign-in provider). The personal backend
  only lets in the owner's Google account, the demo backend only the demo login.
- `/internal/*` routes are for Cloud Scheduler. They don't accept Firebase tokens; they need a
  Google-signed OIDC token for the scheduler's service account (`config.SCHEDULER_SA_EMAIL`)
  whose audience is this service's URL (`config.INTERNAL_AUDIENCE`).

Tokens are verified locally with Google's published certificates (cached per their
Cache-Control max-age) using `google-auth`, which the app already depends on, so no Firebase
Admin SDK is needed. All "who is this user" logic lives here, which keeps the door open for
multi-user later (see docs/OVERHAUL_PLAN.md section 7).
"""
import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, Optional

import requests
from google.auth import jwt as google_jwt
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

import config

logger = logging.getLogger(__name__)

FIREBASE_CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
)
GOOGLE_OIDC_CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
GOOGLE_OIDC_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CLOCK_SKEW_SECONDS = 30

NO_ACCESS_DETAIL = "This account doesn't have access to this environment."


@dataclass(frozen=True)
class AuthUser:
    """The signed-in person, as established from a verified Firebase ID token."""

    uid: str
    email: str
    provider: str


class AuthError(Exception):
    """A request that must be rejected: 401 (no/invalid token) or 403 (not allowed)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def parse_allowlist(raw: str) -> Dict[str, Optional[str]]:
    """Parses "email[:provider],..." into {email: required provider or None}."""
    allowed: Dict[str, Optional[str]] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        email, _, provider = item.partition(":")
        if email.strip():
            allowed[email.strip().lower()] = provider.strip() or None
    return allowed


class _CertCache:
    """Google's public signing certificates, cached according to Cache-Control max-age."""

    def __init__(self, url: str):
        self.url = url
        self._certs: Optional[Dict[str, str]] = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get(self, force_refresh: bool = False) -> Dict[str, str]:
        now = time.time()
        with self._lock:
            if self._certs and not force_refresh and now < self._expires_at:
                return self._certs
        resp = requests.get(self.url, timeout=10)
        resp.raise_for_status()
        match = re.search(r"max-age=(\d+)", resp.headers.get("Cache-Control", ""))
        max_age = min(int(match.group(1)), 6 * 3600) if match else 3600
        certs = resp.json()
        with self._lock:
            self._certs = certs
            self._expires_at = now + max_age
        return certs


_firebase_certs = _CertCache(FIREBASE_CERTS_URL)
_google_oidc_certs = _CertCache(GOOGLE_OIDC_CERTS_URL)


def _decode(token: str, cert_cache: _CertCache, audience: str) -> dict:
    """Verifies signature (RS256 only), expiry and audience; refreshes certs once on key rotation."""
    try:
        header = google_jwt.decode_header(token)
    except Exception as e:  # malformed token
        raise AuthError(401, "Invalid sign-in token.") from e
    if header.get("alg") != "RS256" or not header.get("kid"):
        raise AuthError(401, "Invalid sign-in token.")
    for attempt in (1, 2):
        certs = cert_cache.get(force_refresh=(attempt == 2))
        if header["kid"] not in certs and attempt == 1:
            continue  # key rotated since we cached the certificates
        try:
            return google_jwt.decode(
                token, certs=certs, audience=audience, clock_skew_in_seconds=CLOCK_SKEW_SECONDS
            )
        except Exception as e:
            message = str(e).lower()
            if "expired" in message or "too early" in message:
                raise AuthError(401, "Your sign-in has expired. Please sign in again.") from e
            raise AuthError(401, "Invalid sign-in token.") from e
    raise AuthError(401, "Invalid sign-in token.")


def verify_firebase_token(token: str) -> dict:
    """Returns the claims of a valid Firebase ID token issued for this Firebase project."""
    project = config.FIREBASE_PROJECT_ID
    claims = _decode(token, _firebase_certs, audience=project)
    if claims.get("iss") != f"https://securetoken.google.com/{project}":
        raise AuthError(401, "Invalid sign-in token.")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 128:
        raise AuthError(401, "Invalid sign-in token.")
    auth_time = claims.get("auth_time")
    if not isinstance(auth_time, (int, float)) or auth_time > time.time() + CLOCK_SKEW_SECONDS:
        raise AuthError(401, "Invalid sign-in token.")
    return claims


def check_allowed(claims: dict) -> AuthUser:
    """Applies this backend's allowlist to verified Firebase claims."""
    email = str(claims.get("email") or "").strip().lower()
    provider = str((claims.get("firebase") or {}).get("sign_in_provider") or "")
    allowed = parse_allowlist(config.AUTH_ALLOWED_USERS)
    if not email or claims.get("email_verified") is not True or email not in allowed:
        raise AuthError(403, NO_ACCESS_DETAIL)
    required_provider = allowed[email]
    if required_provider and provider != required_provider:
        raise AuthError(403, NO_ACCESS_DETAIL)
    return AuthUser(uid=str(claims["sub"]), email=email, provider=provider)


# Verified tokens are cached until they expire (max 5 minutes), so a page load that fires a
# dozen requests verifies its token once.
_TOKEN_CACHE_MAX = 512
_token_cache: "OrderedDict[str, tuple[float, AuthUser]]" = OrderedDict()
_token_cache_lock = threading.Lock()


def authenticate_user(token: str) -> AuthUser:
    """Verifies a Firebase ID token and the allowlist. Raises AuthError (401 / 403)."""
    if not token:
        raise AuthError(401, "Sign in required.")
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.time()
    with _token_cache_lock:
        cached = _token_cache.get(key)
        if cached and cached[0] > now:
            _token_cache.move_to_end(key)
            return cached[1]
    claims = verify_firebase_token(token)
    user = check_allowed(claims)
    expires = min(float(claims.get("exp", now)), now + 300)
    with _token_cache_lock:
        _token_cache[key] = (expires, user)
        while len(_token_cache) > _TOKEN_CACHE_MAX:
            _token_cache.popitem(last=False)
    return user


def verify_scheduler_token(token: str) -> dict:
    """Verifies a Cloud Scheduler OIDC token: Google-signed, right audience and service account."""
    if not config.INTERNAL_AUDIENCE:
        raise AuthError(403, "Internal routes are not enabled on this server.")
    if not token:
        raise AuthError(401, "Sign in required.")
    claims = _decode(token, _google_oidc_certs, audience=config.INTERNAL_AUDIENCE)
    if claims.get("iss") not in GOOGLE_OIDC_ISSUERS:
        raise AuthError(401, "Invalid token.")
    if claims.get("email_verified") is not True or claims.get("email") != config.SCHEDULER_SA_EMAIL:
        raise AuthError(403, "This caller can't use internal routes.")
    return claims


def clear_caches() -> None:
    """Forgets cached tokens (for tests)."""
    with _token_cache_lock:
        _token_cache.clear()


def _bearer_token(scope) -> str:
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer":
                return token.strip()
            return ""
    return ""


class FirebaseAuthMiddleware:
    """ASGI middleware: every request needs a valid, allowed sign-in unless its path is public.

    Must be added BEFORE CORSMiddleware (so CORS is the outer layer): preflight requests are
    answered by CORS, and 401/403 responses still carry CORS headers the browser can read.
    """

    def __init__(
        self,
        app,
        public_paths: tuple = ("/health", "/"),
        public_prefixes: tuple = ("/assets/",),
        internal_prefix: str = "/internal/",
    ):
        self.app = app
        self.public_paths = set(public_paths)
        self.public_prefixes = tuple(public_prefixes)
        self.internal_prefix = internal_prefix
        # Resolved at call time so tests can replace them.
        self._authenticate: Callable[[str], AuthUser] = lambda t: authenticate_user(t)
        self._verify_internal: Callable[[str], dict] = lambda t: verify_scheduler_token(t)

    def _is_public(self, path: str) -> bool:
        if path in self.public_paths:
            return True
        if config.ENABLE_API_DOCS and path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            return True
        return path.startswith(self.public_prefixes)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS" or self._is_public(scope["path"]):
            await self.app(scope, receive, send)
            return
        token = _bearer_token(scope)
        state = scope.setdefault("state", {})
        try:
            if not token:
                raise AuthError(401, "Sign in required.")
            if scope["path"].startswith(self.internal_prefix):
                state["scheduler"] = await run_in_threadpool(self._verify_internal, token)
            else:
                state["user"] = await run_in_threadpool(self._authenticate, token)
        except AuthError as e:
            if e.status_code == 403:
                logger.warning("Rejected request to %s: %s", scope["path"], e.detail)
            headers = {"WWW-Authenticate": "Bearer"} if e.status_code == 401 else None
            response = JSONResponse({"detail": e.detail}, status_code=e.status_code, headers=headers)
            await response(scope, receive, send)
            return
        except Exception:
            logger.exception("Sign-in check failed unexpectedly")
            response = JSONResponse(
                {"detail": "Sign-in check failed. Please try again."}, status_code=503
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
