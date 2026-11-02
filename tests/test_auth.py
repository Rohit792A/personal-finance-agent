# tests/test_auth.py
"""Phase 1 sign-in checks (app/auth.py, DEC-025).

Tokens are real RS256 JWTs signed with a throwaway key whose certificate is injected into the
certificate caches, so the actual verification code runs (signature, audience, issuer, expiry,
allowlist, provider pinning, scheduler OIDC). No network or database access is needed.
"""
import datetime
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from google.auth import crypt, jwt as google_jwt

import api
import auth
import config

PROJECT = config.FIREBASE_PROJECT_ID
OWNER = "rkushwaha8610@gmail.com"
ORIGIN = "https://personal-finance-agent-507010.web.app"
INTERNAL_AUD = "https://finance-backend-test.a.run.app"


def _keypair(kid: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pfa-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    signer = crypt.RSASigner.from_string(pem_key, key_id=kid)
    return signer, cert.public_bytes(serialization.Encoding.PEM).decode()


SIGNER, CERT = _keypair("kid-1")
OTHER_SIGNER, _ = _keypair("kid-1")  # same kid, different key: signature must fail


def firebase_token(signer=SIGNER, **overrides):
    now = int(time.time())
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "auth_time": now - 60,
        "iat": now - 60,
        "exp": now + 3600,
        "sub": "uid-owner",
        "user_id": "uid-owner",
        "email": OWNER,
        "email_verified": True,
        "firebase": {"sign_in_provider": "google.com"},
    }
    claims.update(overrides)
    return google_jwt.encode(signer, claims).decode()


def scheduler_token(**overrides):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": INTERNAL_AUD,
        "iat": now - 10,
        "exp": now + 3600,
        "sub": "1234567890",
        "email": config.SCHEDULER_SA_EMAIL,
        "email_verified": True,
    }
    claims.update(overrides)
    return google_jwt.encode(SIGNER, claims).decode()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(auth._firebase_certs, "get", lambda force_refresh=False: {"kid-1": CERT})
    monkeypatch.setattr(auth._google_oidc_certs, "get", lambda force_refresh=False: {"kid-1": CERT})
    monkeypatch.setattr(config, "AUTH_ALLOWED_USERS", f"{OWNER}:google.com")
    monkeypatch.setattr(config, "INTERNAL_AUDIENCE", INTERNAL_AUD)
    monkeypatch.setattr(config, "IS_DEMO", False)
    monkeypatch.setattr(config, "APP_ENV", "personal")
    monkeypatch.setattr(config, "AI_DAILY_LIMITS", {})
    auth.clear_caches()
    yield
    auth.clear_caches()


client = TestClient(api.app)


def get_me(token=None, **headers):
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.get("/me", headers=headers)


def test_health_is_public():
    assert client.get("/health").status_code == 200


def test_missing_token_is_401_with_cors_headers():
    resp = get_me(Origin=ORIGIN)
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"
    # CORS wraps the sign-in check, so the browser can read the 401.
    assert resp.headers.get("access-control-allow-origin") == ORIGIN


def test_preflight_needs_no_token():
    resp = client.options("/me", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    })
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ORIGIN


def test_allowed_owner_gets_in():
    resp = get_me(firebase_token())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == OWNER and body["environment"] == "personal"
    assert body["features"]["gmail_sync"] is True and body["ai_limits"] is None


@pytest.mark.parametrize("token", [
    "not-a-jwt",
    firebase_token(signer=OTHER_SIGNER),                       # bad signature
    firebase_token(aud="some-other-project"),                  # wrong audience
    firebase_token(iss="https://securetoken.google.com/x"),    # wrong issuer
    firebase_token(exp=int(time.time()) - 600, iat=int(time.time()) - 4000),  # expired
    firebase_token(sub=""),                                    # no subject
    firebase_token(auth_time=int(time.time()) + 3600),         # auth in the future
])
def test_invalid_tokens_are_401(token):
    assert get_me(token).status_code == 401


def test_unsigned_token_is_401():
    header = google_jwt._helpers.unpadded_urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}')
    payload = google_jwt._helpers.unpadded_urlsafe_b64encode(
        b'{"aud":"%s","email":"%s"}' % (PROJECT.encode(), OWNER.encode())
    )
    assert get_me(f"{header.decode()}.{payload.decode()}.").status_code == 401


@pytest.mark.parametrize("overrides", [
    {"email": "someone.else@gmail.com"},                        # not on the allowlist
    {"email_verified": False},                                  # unverified address
    {"firebase": {"sign_in_provider": "password"}},             # owner email, wrong provider
    {"email": None},
])
def test_valid_but_not_allowed_is_403(overrides):
    resp = get_me(firebase_token(**overrides))
    assert resp.status_code == 403
    assert resp.json()["detail"] == auth.NO_ACCESS_DETAIL


def test_unknown_key_id_is_401():
    signer, _ = _keypair("kid-unknown")
    assert get_me(firebase_token(signer=signer)).status_code == 401


def test_every_api_route_requires_sign_in():
    """Secure by default: walk all routes; only the documented public ones answer without a token."""
    public = {"/health", "/"}
    for route in api.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if path in public or path.startswith("/assets") or not methods:
            continue
        url = path.replace("{tx_id}", "1").replace("{transaction_id}", "1").replace("{stmt_id}", "1")
        for method in methods - {"HEAD", "OPTIONS"}:
            resp = client.request(method, url)
            assert resp.status_code == 401, f"{method} {path} answered {resp.status_code} without sign-in"


def test_internal_routes_reject_user_tokens():
    resp = client.post("/internal/demo-reset", headers={"Authorization": f"Bearer {firebase_token()}"})
    assert resp.status_code == 401  # wrong issuer/audience for an internal route


def test_internal_routes_accept_scheduler_token():
    # Auth passes; the personal backend then hides the demo-only route.
    resp = client.post("/internal/demo-reset", headers={"Authorization": f"Bearer {scheduler_token()}"})
    assert resp.status_code == 404


@pytest.mark.parametrize("overrides,status", [
    ({"email": "attacker@evil-project.iam.gserviceaccount.com"}, 403),
    ({"email_verified": False}, 403),
    ({"aud": "https://other-service.a.run.app"}, 401),
    ({"iss": "https://evil.example.com"}, 401),
])
def test_internal_routes_reject_wrong_scheduler_tokens(overrides, status):
    resp = client.post("/internal/gmail-sync", headers={"Authorization": f"Bearer {scheduler_token(**overrides)}"})
    assert resp.status_code == status


def test_internal_routes_disabled_without_audience(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_AUDIENCE", "")
    resp = client.post("/internal/gmail-sync", headers={"Authorization": f"Bearer {scheduler_token()}"})
    assert resp.status_code == 403


def test_demo_turns_gmail_off_and_limits_ai(monkeypatch):
    monkeypatch.setattr(config, "IS_DEMO", True)
    monkeypatch.setattr(config, "APP_ENV", "demo")
    monkeypatch.setattr(config, "AI_DAILY_LIMITS", {"chat": 0})
    demo_email = "demo@example.com"
    monkeypatch.setattr(config, "AUTH_ALLOWED_USERS", f"{demo_email}:password")
    token = firebase_token(email=demo_email, firebase={"sign_in_provider": "password"})
    headers = {"Authorization": f"Bearer {token}"}

    # The owner's Google account is not allowed into the demo backend.
    assert get_me(firebase_token()).status_code == 403

    gmail = client.get("/api/gmail/settings", headers=headers)
    assert gmail.status_code == 403 and "turned off in the demo" in gmail.json()["detail"]

    chat = client.post("/insights/chat", json={"message": "hi", "history": []}, headers=headers)
    assert chat.status_code == 429 and "daily limit" in chat.json()["detail"]


def test_allowlist_parsing():
    assert auth.parse_allowlist(" A@x.com:google.com, b@y.com ,,") == {
        "a@x.com": "google.com",
        "b@y.com": None,
    }
