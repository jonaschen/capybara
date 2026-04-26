"""
tools/chat_store.py

Per-user IDLE-mode chat history persistence (Phase B-lite). Used so that
when Cloud Run scales out / restarts, the bot can recall what was said
in recent conversations rather than starting cold every cold-start.

Storage path: {user_id}/chat_history.json
Backends: GCS (when GCS_PROFILES_BUCKET set) or /tmp/capybara_profiles/
local fallback. Mirrors gcs_profile.py / state_store.py exactly.

History is auto-trimmed to MAX_ROUNDS rounds on save (1 round = 1 user
message + 1 assistant message). Older messages are dropped — bot has
short-term continuity, not long-term memory. Long-term recall (cross-week
"你上週說工作壓力大" callbacks) is Phase B-full and out of scope.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FILENAME = "chat_history.json"
MAX_ROUNDS = 10  # 1 round = user msg + assistant msg
MAX_MESSAGES = MAX_ROUNDS * 2

_LOCAL_ROOT = Path("/tmp/capybara_profiles")


def _resolve_bucket(bucket: str | None) -> str | None:
    return bucket or os.environ.get("GCS_PROFILES_BUCKET") or None


def _get_client():
    from google.cloud import storage
    return storage.Client()


def _blob_path(user_id: str) -> str:
    return f"{user_id}/{FILENAME}"


def _trim(history: list[dict]) -> list[dict]:
    """Keep at most MAX_MESSAGES, dropping oldest. If trimming lands on an
    odd boundary (assistant first), drop one more to keep user-first order."""
    if len(history) <= MAX_MESSAGES:
        return list(history)
    trimmed = history[-MAX_MESSAGES:]
    if trimmed and trimmed[0].get("role") == "assistant":
        trimmed = trimmed[1:]
    return trimmed


def save_chat_history(
    user_id: str,
    history: list[dict],
    gcs_client=None,
    bucket: str | None = None,
) -> None:
    trimmed = _trim(history)
    payload = json.dumps(
        {"history": trimmed, "saved_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False,
    )
    resolved = _resolve_bucket(bucket)
    if resolved:
        c = gcs_client or _get_client()
        blob = c.bucket(resolved).blob(_blob_path(user_id))
        blob.upload_from_string(payload, content_type="application/json")
        logger.info(f"Saved chat history gs://{resolved}/{_blob_path(user_id)} ({len(trimmed)} msgs)")
        return
    path = _LOCAL_ROOT / user_id / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    logger.info(f"Saved chat history {path} ({len(trimmed)} msgs)")


def load_chat_history(
    user_id: str,
    gcs_client=None,
    bucket: str | None = None,
) -> list[dict]:
    """Return the saved history list, or [] if no save exists."""
    resolved = _resolve_bucket(bucket)
    if resolved:
        c = gcs_client or _get_client()
        blob = c.bucket(resolved).blob(_blob_path(user_id))
        if not blob.exists():
            return []
        try:
            payload = json.loads(blob.download_as_text())
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(f"Corrupt chat_history.json for {user_id[:8]}...: {exc}")
            return []
        return payload.get("history", []) or []
    path = _LOCAL_ROOT / user_id / FILENAME
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(f"Corrupt chat_history.json for {user_id[:8]}...: {exc}")
        return []
    return payload.get("history", []) or []


def clear_chat_history(
    user_id: str,
    gcs_client=None,
    bucket: str | None = None,
) -> None:
    """Idempotent: silently no-ops if no history exists for this user."""
    resolved = _resolve_bucket(bucket)
    if resolved:
        c = gcs_client or _get_client()
        blob = c.bucket(resolved).blob(_blob_path(user_id))
        if blob.exists():
            blob.delete()
            logger.info(f"Cleared chat history gs://{resolved}/{_blob_path(user_id)}")
        return
    path = _LOCAL_ROOT / user_id / FILENAME
    if path.exists():
        path.unlink()
        logger.info(f"Cleared chat history {path}")
