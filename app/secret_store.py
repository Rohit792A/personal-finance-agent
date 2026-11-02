# secret_store.py
"""Secret access for the backend: environment variables first, then Google Secret Manager.

Why this module exists (see DEC-022 / DEC-023 in decision.md):
- Credentials must never live in source code, in the Docker image, or in Supabase.
- On Cloud Run, a secret can be injected as an environment variable (`--set-secrets`),
  so reading it needs no network call.
- In Cloud Shell and local development, values are read from Secret Manager using your
  gcloud login (Application Default Credentials), so no secret files sit on disk.

Notes:
- Uses the Secret Manager REST API through `google-auth`, which the app already depends on,
  so no extra client library is added to the Docker image.
- Values are cached in memory for a few minutes to avoid a network round trip per request.
  `set_secret()` updates the cache immediately.
- Secrets the app itself can change at runtime (for example the Gmail App Password saved from
  the Bank Sync screen) should NOT be injected as environment variables on Cloud Run, because
  an injected value is frozen when the instance starts.
- Never log secret values. Only secret names appear in log messages and errors.
"""
import base64
import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

import google.auth
from google.auth.transport.requests import AuthorizedSession

logger = logging.getLogger(__name__)

_API = "https://secretmanager.googleapis.com/v1"
_CACHE_TTL_SECONDS = 300
_HTTP_TIMEOUT_SECONDS = 10

_cache: Dict[str, Tuple[float, Optional[str]]] = {}
_lock = threading.Lock()
_session: Optional[AuthorizedSession] = None


class SecretNotFoundError(RuntimeError):
    """Raised when a required secret is missing from both the environment and Secret Manager."""


def _project_id() -> str:
    # Imported lazily because config.py also imports this module lazily (no import cycle).
    import config

    return config.PROJECT_ID


def _authorized_session() -> AuthorizedSession:
    global _session
    if _session is None:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        _session = AuthorizedSession(credentials)
    return _session


def _secret_path(name: str) -> str:
    return f"{_API}/projects/{_project_id()}/secrets/{name}"


def _fetch_latest(name: str) -> Optional[str]:
    """Returns the latest enabled version of a secret, or None if it doesn't exist yet."""
    url = f"{_secret_path(name)}/versions/latest:access"
    try:
        resp = _authorized_session().get(url, timeout=_HTTP_TIMEOUT_SECONDS)
    except Exception as auth_err:
        # In Cloud Shell / local dev, the GCE metadata proxy can intermittently omit 'email'
        # on refresh; fall back to `gcloud auth print-access-token` or local Firebase CLI token.
        import json
        import subprocess
        import requests

        token: Optional[str] = None
        try:
            token = subprocess.check_output(
                ["gcloud", "auth", "print-access-token"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            ).strip()
        except Exception:
            fb_cfg = os.path.expanduser("~/.config/configstore/firebase-tools.json")
            if os.path.isfile(fb_cfg):
                try:
                    with open(fb_cfg, "r", encoding="utf-8") as fh:
                        token = (json.load(fh).get("tokens") or {}).get("access_token")
                except Exception:
                    token = None

        if not token:
            raise auth_err
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    if resp.status_code == 404:
        return None  # secret or version doesn't exist
    if resp.status_code == 400 and "FAILED_PRECONDITION" in resp.text:
        return None  # latest version is disabled or destroyed
    if resp.status_code == 403:
        raise PermissionError(
            f"No permission to read secret '{name}'. The identity running the backend needs "
            "the Secret Manager Secret Accessor role on it."
        )
    resp.raise_for_status()
    data = resp.json()["payload"]["data"]
    return base64.b64decode(data).decode("utf-8")


def get_secret(
    name: str, env_var: Optional[str] = None, required: bool = True
) -> Optional[str]:
    """Returns a secret value.

    Lookup order: the environment variable `env_var` (if given and non-empty), then the latest
    version of secret `name` in Secret Manager. Raises SecretNotFoundError when `required` and
    the value is missing; otherwise returns None.
    """
    if env_var:
        env_value = os.getenv(env_var)
        if env_value:
            return env_value

    now = time.monotonic()
    with _lock:
        cached = _cache.get(name)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        value = cached[1]
    else:
        value = _fetch_latest(name)
        with _lock:
            _cache[name] = (now, value)

    if not value and required:
        hint = f" or the {env_var} environment variable" if env_var else ""
        raise SecretNotFoundError(
            f"Secret '{name}' is not set. Store it with `scripts/set_secret.sh {name}`{hint}."
        )
    return value or None


def secret_exists(name: str) -> bool:
    """True if the secret has an enabled latest version (uses the same cache as get_secret)."""
    return bool(get_secret(name, required=False))


def set_secret(name: str, value: str, disable_previous: bool = True) -> str:
    """Stores `value` as the newest version of secret `name`, creating the secret if needed.

    With `disable_previous`, older enabled versions are disabled (not destroyed), so only the
    new value can be read but a rollback is still possible. Returns the new version's name.
    """
    if not value:
        raise ValueError(f"Refusing to store an empty value in secret '{name}'.")
    session = _authorized_session()
    project = _project_id()
    payload = {"payload": {"data": base64.b64encode(value.encode("utf-8")).decode("ascii")}}

    # Add the version first and only create the secret if it doesn't exist yet (404). On Cloud
    # Run the backend's service account may update its own secrets but has no project-wide
    # permission to create secrets (DEC-025), so creating first would fail with 403.
    added = session.post(f"{_secret_path(name)}:addVersion", json=payload, timeout=_HTTP_TIMEOUT_SECONDS)
    if added.status_code == 404:
        create = session.post(
            f"{_API}/projects/{project}/secrets",
            params={"secretId": name},
            json={"replication": {"automatic": {}}, "labels": {"app": "personal-finance-agent"}},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        if create.status_code not in (200, 409):  # 409 = created concurrently
            create.raise_for_status()
        added = session.post(
            f"{_secret_path(name)}:addVersion", json=payload, timeout=_HTTP_TIMEOUT_SECONDS
        )
    added.raise_for_status()
    new_version = added.json()["name"]

    if disable_previous:
        listing = session.get(
            f"{_secret_path(name)}/versions",
            params={"filter": "state:ENABLED"},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        listing.raise_for_status()
        for version in listing.json().get("versions", []):
            if version["name"] != new_version:
                session.post(
                    f"{_API}/{version['name']}:disable", json={}, timeout=_HTTP_TIMEOUT_SECONDS
                ).raise_for_status()

    with _lock:
        _cache[name] = (time.monotonic(), value)
    logger.info("Stored a new version of secret '%s'.", name)
    return new_version


def clear_cache() -> None:
    """Forgets cached values (for tests, or after rotating a secret outside the app)."""
    with _lock:
        _cache.clear()
