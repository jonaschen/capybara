"""
tools/state_store.py

Per-user onboarding-state persistence. Used so that when Cloud Run scales
out or restarts mid-interview, the user's conversation history survives
and the bot picks up where it left off rather than starting onboarding
from scratch.

Storage path: {user_id}/onboarding_state.json
Backends: GCS (when GCS_PROFILES_BUCKET set) or /tmp/capybara_profiles/
local fallback. Mirrors gcs_profile.py's resolution logic exactly.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FILENAME = "onboarding_state.json"
_LOCAL_ROOT = Path("/tmp/capybara_profiles")


def _resolve_bucket(bucket: str | None) -> str | None:
    return bucket or os.environ.get("GCS_PROFILES_BUCKET") or None


def _get_client():
    from google.cloud import storage
    return storage.Client()


def _blob_path(user_id: str) -> str:
    return f"{user_id}/{FILENAME}"


def save_onboarding_state(
    user_id: str,
    history: list[dict],
    gcs_client=None,
    bucket: str | None = None,
) -> None:
    payload = json.dumps(
        {"history": history, "saved_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False,
    )
    resolved = _resolve_bucket(bucket)
    if resolved:
        c = gcs_client or _get_client()
        blob = c.bucket(resolved).blob(_blob_path(user_id))
        blob.upload_from_string(payload, content_type="application/json")
        logger.info(f"Saved onboarding state gs://{resolved}/{_blob_path(user_id)}")
        return
    path = _LOCAL_ROOT / user_id / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    logger.info(f"Saved onboarding state {path}")


def load_onboarding_state(
    user_id: str,
    gcs_client=None,
    bucket: str | None = None,
) -> dict[str, Any] | None:
    """Return {"history": [...], "saved_at": iso8601} or None when missing."""
    resolved = _resolve_bucket(bucket)
    if resolved:
        c = gcs_client or _get_client()
        blob = c.bucket(resolved).blob(_blob_path(user_id))
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    path = _LOCAL_ROOT / user_id / FILENAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def clear_onboarding_state(
    user_id: str,
    gcs_client=None,
    bucket: str | None = None,
) -> None:
    """Idempotent: silently no-ops if no state exists for this user."""
    resolved = _resolve_bucket(bucket)
    if resolved:
        c = gcs_client or _get_client()
        blob = c.bucket(resolved).blob(_blob_path(user_id))
        if blob.exists():
            blob.delete()
            logger.info(f"Cleared onboarding state gs://{resolved}/{_blob_path(user_id)}")
        return
    path = _LOCAL_ROOT / user_id / FILENAME
    if path.exists():
        path.unlink()
        logger.info(f"Cleared onboarding state {path}")
