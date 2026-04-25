"""
tools/known_users.py

Records every LINE user the bot has interacted with — full user_id, first
seen, last seen, last invited, invite count — and runs periodic invitations
to users who followed but never completed onboarding.

Storage: per-user blob `{user_id}/known_user.json` (so concurrent webhook
instances don't race on a single global list). Enumerable via
gcs_profile.list_user_ids since they share the same bucket layout.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from tools import gcs_profile

logger = logging.getLogger(__name__)

FILENAME = "known_user.json"
INVITE_COOLDOWN = timedelta(days=7)
MAX_INVITES = 3

INVITE_TEXT = (
    "卡皮教練很久沒聽到你分享狀況了。"
    "想試試訓練計畫的話，隨時找卡皮聊聊喔。🐾"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(user_id: str, gcs_client, bucket) -> dict[str, Any] | None:
    try:
        raw = gcs_profile.read_profile(
            user_id, filename=FILENAME, client=gcs_client, bucket=bucket
        )
    except FileNotFoundError:
        return None
    return json.loads(raw)


def _write_json(user_id: str, payload: dict, gcs_client, bucket) -> None:
    gcs_profile.write_profile(
        user_id,
        json.dumps(payload, ensure_ascii=False),
        filename=FILENAME,
        client=gcs_client,
        bucket=bucket,
    )


def record_user_seen(
    user_id: str,
    gcs_client=None,
    bucket: str | None = None,
    now: datetime | None = None,
) -> None:
    """Idempotent. First call initializes; later calls only bump last_seen."""
    if not user_id:
        return
    when = (now or _now()).isoformat()
    existing = _read_json(user_id, gcs_client, bucket)
    if existing is None:
        payload = {
            "user_id": user_id,
            "first_seen": when,
            "last_seen": when,
            "last_invited": None,
            "invite_count": 0,
        }
    else:
        existing["last_seen"] = when
        payload = existing
    _write_json(user_id, payload, gcs_client, bucket)


def load_known_user(
    user_id: str,
    gcs_client=None,
    bucket: str | None = None,
) -> dict[str, Any] | None:
    return _read_json(user_id, gcs_client, bucket)


def mark_invited(
    user_id: str,
    gcs_client=None,
    bucket: str | None = None,
    now: datetime | None = None,
) -> None:
    when = (now or _now()).isoformat()
    meta = _read_json(user_id, gcs_client, bucket)
    if meta is None:
        meta = {
            "user_id": user_id,
            "first_seen": when,
            "last_seen": when,
            "last_invited": when,
            "invite_count": 1,
        }
    else:
        meta["last_invited"] = when
        meta["invite_count"] = meta.get("invite_count", 0) + 1
    _write_json(user_id, meta, gcs_client, bucket)


def eligible_for_invite(
    meta: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """Eligibility = below max invite count AND past the cooldown window."""
    if meta.get("invite_count", 0) >= MAX_INVITES:
        return False
    last_str = meta.get("last_invited")
    if last_str is None:
        return True
    try:
        last = datetime.fromisoformat(last_str)
    except ValueError:
        return True
    when = now or _now()
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (when - last) >= INVITE_COOLDOWN


def _push_invite(line_api, user_id: str) -> None:
    from linebot.v3.messaging import PushMessageRequest, TextMessage

    line_api.push_message(
        PushMessageRequest(to=user_id, messages=[TextMessage(text=INVITE_TEXT)])
    )


def send_onboarding_invites(
    line_api,
    gcs_client=None,
    bucket: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Iterate every known user, push the invite when they're eligible.

    Eligibility excludes:
      - Users with athlete_profile.md (already onboarded)
      - Users invited within the last INVITE_COOLDOWN
      - Users invited MAX_INVITES times already
    Failed pushes do NOT bump invite_count so the next cycle retries.
    """
    when = now or _now()
    user_ids = gcs_profile.list_user_ids(client=gcs_client, bucket=bucket)
    invited = failed = 0
    details: list[dict[str, Any]] = []

    for uid in user_ids:
        meta = _read_json(uid, gcs_client, bucket)
        if meta is None:
            # User has GCS artifacts but no known_user.json — pre-record gap.
            continue

        if gcs_profile.profile_exists(uid, client=gcs_client, bucket=bucket):
            details.append({"user_id": uid, "status": "skip_has_profile"})
            continue
        if meta.get("invite_count", 0) >= MAX_INVITES:
            details.append({"user_id": uid, "status": "skip_maxed"})
            continue
        if not eligible_for_invite(meta, now=when):
            details.append({"user_id": uid, "status": "skip_cooldown"})
            continue

        try:
            _push_invite(line_api, uid)
            mark_invited(uid, gcs_client=gcs_client, bucket=bucket, now=when)
            details.append({"user_id": uid, "status": "invited"})
            invited += 1
        except Exception as exc:
            logger.warning(f"Invite push failed for {uid}: {exc}")
            details.append({"user_id": uid, "status": "failed", "error": str(exc)})
            failed += 1

    return {"invited": invited, "failed": failed, "details": details}
