# statement_store.py
"""Where Gmail-exported bank statement PDFs are kept (Phase 1, see DEC-025 in decision.md).

- On Cloud Run (`config.STATEMENT_BUCKET` set): in a private Cloud Storage bucket
  (uniform access, public access prevention enforced) that only the personal backend's service
  account can read and write. Cloud Run's own disk is wiped whenever an instance stops, so PDFs
  can't live there.
- In local development (no bucket configured): under `data/monthly_statements/<YYYY-MM>/`, as
  before.

A statement's location is stored in `exported_statement_pdfs.file_path`, either as a local path
or as `gs://<bucket>/<YYYY-MM>/<file name>`. Locations are only ever resolved inside the
configured bucket, so a tampered database row can't make the backend read other buckets.
"""
import contextlib
import logging
import os
import shutil
import tempfile
from typing import Iterator, Optional, Tuple

import config

logger = logging.getLogger(__name__)

GCS_SCHEME = "gs://"
LOCAL_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "monthly_statements"
)

_client = None


def _storage_client():
    global _client
    if _client is None:
        from google.cloud import storage  # lazy: not needed in local mode

        _client = storage.Client(project=config.PROJECT_ID)
    return _client


def uses_bucket() -> bool:
    return bool(config.STATEMENT_BUCKET)


def describe_root() -> str:
    """Human-readable storage root (shown on the Bank Sync screen)."""
    return f"{GCS_SCHEME}{config.STATEMENT_BUCKET}" if uses_bucket() else LOCAL_ROOT


def _parse_gcs(location: str) -> Tuple[str, str]:
    bucket, _, blob_name = location[len(GCS_SCHEME):].partition("/")
    if not config.STATEMENT_BUCKET or bucket != config.STATEMENT_BUCKET or not blob_name:
        raise ValueError("Statement location is outside the configured statements bucket.")
    if ".." in blob_name.split("/"):
        raise ValueError("Invalid statement location.")
    return bucket, blob_name


def _blob(location: str):
    bucket, blob_name = _parse_gcs(location)
    return _storage_client().bucket(bucket).blob(blob_name)


def staging_dir() -> str:
    """A fresh private temp folder for processing a statement; caller removes it."""
    return tempfile.mkdtemp(prefix="pfa-stmt-")


def save(local_path: str, month_key: str, file_name: str) -> str:
    """Stores a statement file and returns its location (gs:// URI or local path)."""
    safe_month = "".join(ch for ch in month_key if ch.isalnum() or ch == "-")[:16] or "unknown"
    safe_name = os.path.basename(file_name)
    if uses_bucket():
        location = f"{GCS_SCHEME}{config.STATEMENT_BUCKET}/{safe_month}/{safe_name}"
        content_type = "application/pdf" if safe_name.lower().endswith(".pdf") else None
        _blob(location).upload_from_filename(local_path, content_type=content_type)
        logger.info("Stored statement %s in the statements bucket.", safe_name)
        return location
    dest_dir = os.path.join(LOCAL_ROOT, safe_month)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, safe_name)
    if os.path.abspath(local_path) != os.path.abspath(dest):
        shutil.copyfile(local_path, dest)
    return dest


def exists(location: Optional[str]) -> bool:
    if not location:
        return False
    if location.startswith(GCS_SCHEME):
        try:
            return _blob(location).exists()
        except ValueError:
            return False
    return os.path.exists(location)


def read_bytes(location: str) -> bytes:
    if location.startswith(GCS_SCHEME):
        return _blob(location).download_as_bytes()
    with open(location, "rb") as f:
        return f.read()


def replace(location: str, local_path: str) -> None:
    """Overwrites a stored statement (e.g. with its unlocked copy)."""
    if location.startswith(GCS_SCHEME):
        _blob(location).upload_from_filename(local_path, content_type="application/pdf")
    elif os.path.abspath(local_path) != os.path.abspath(location):
        shutil.copyfile(local_path, location)


@contextlib.contextmanager
def local_copy(location: str) -> Iterator[str]:
    """Yields a local path to work on. Bucket files are downloaded to a temp copy that is deleted
    afterwards; local files are used in place."""
    if not location.startswith(GCS_SCHEME):
        yield location
        return
    tmp = staging_dir()
    try:
        _, blob_name = _parse_gcs(location)
        path = os.path.join(tmp, os.path.basename(blob_name))
        _blob(location).download_to_filename(path)
        yield path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
