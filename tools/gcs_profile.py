"""
tools/gcs_profile.py

Per-user profile storage with two backends:
  - GCS (google.cloud.storage) when GCS_PROFILES_BUCKET is set
  - Local filesystem fallback at /tmp/capybara_profiles/ for dev

Path convention: {user_id}/{filename}. Default filename is athlete_profile.md.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_FILENAME = "athlete_profile.md"
_LOCAL_ROOT = Path("/tmp/capybara_profiles")


def _resolve_bucket(bucket: str | None) -> str | None:
    return bucket or os.environ.get("GCS_PROFILES_BUCKET") or None


def _get_client():
    """Lazy import so tests that only use local fallback don't need google-cloud-storage."""
    from google.cloud import storage
    return storage.Client()


def _blob_path(user_id: str, filename: str) -> str:
    return f"{user_id}/{filename}"


def profile_exists(
    user_id: str,
    filename: str = DEFAULT_FILENAME,
    client=None,
    bucket: str | None = None,
) -> bool:
    resolved_bucket = _resolve_bucket(bucket)
    if resolved_bucket:
        c = client or _get_client()
        return c.bucket(resolved_bucket).blob(_blob_path(user_id, filename)).exists()
    return (_LOCAL_ROOT / user_id / filename).exists()


def read_profile(
    user_id: str,
    filename: str = DEFAULT_FILENAME,
    client=None,
    bucket: str | None = None,
) -> str:
    resolved_bucket = _resolve_bucket(bucket)
    if resolved_bucket:
        c = client or _get_client()
        blob = c.bucket(resolved_bucket).blob(_blob_path(user_id, filename))
        if not blob.exists():
            raise FileNotFoundError(f"gs://{resolved_bucket}/{_blob_path(user_id, filename)}")
        return blob.download_as_text()
    path = _LOCAL_ROOT / user_id / filename
    if not path.exists():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8")


def list_user_ids(
    client=None,
    bucket: str | None = None,
) -> list[str]:
    """Enumerate user IDs that have at least one blob stored. Sorted."""
    resolved_bucket = _resolve_bucket(bucket)
    if resolved_bucket:
        c = client or _get_client()
        seen: set[str] = set()
        for blob in c.bucket(resolved_bucket).list_blobs():
            parts = blob.name.split("/", 1)
            if len(parts) == 2 and parts[0]:
                seen.add(parts[0])
        return sorted(seen)
    if not _LOCAL_ROOT.exists():
        return []
    return sorted(p.name for p in _LOCAL_ROOT.iterdir() if p.is_dir())


def write_profile(
    user_id: str,
    content: str,
    filename: str = DEFAULT_FILENAME,
    client=None,
    bucket: str | None = None,
) -> None:
    resolved_bucket = _resolve_bucket(bucket)
    if resolved_bucket:
        c = client or _get_client()
        blob = c.bucket(resolved_bucket).blob(_blob_path(user_id, filename))
        blob.upload_from_string(content, content_type="text/markdown")
        logger.info(f"Wrote gs://{resolved_bucket}/{_blob_path(user_id, filename)}")
        return
    path = _LOCAL_ROOT / user_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info(f"Wrote {path}")
