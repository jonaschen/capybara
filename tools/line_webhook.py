"""
tools/line_webhook.py

水豚教練 (Capybara Coach) — LINE Webhook Server

HTTP entry point for the deployed container. Phase 1 routes:

  GET  /health    — Cloud Run liveness probe (200 OK)
  POST /callback  — LINE Messaging API webhook

All text messages are dispatched through IDLE state → coach_reply(). Future
phases add ONBOARDING and COACHING state branches here.

Run locally:
  source .env
  .venv/bin/python3 tools/line_webhook.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, PostbackEvent

from tools import gcs_profile
from tools.coach_reply import coach_reply
from tools.onboarding_reply import onboarding_reply
from tools.webhook_dedup import WebhookDeduplicator

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("capybara.webhook")


LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
OWNER_LINE_USER_ID = os.environ.get("OWNER_LINE_USER_ID", "")
DAILY_PUSH_SECRET = os.environ.get("DAILY_PUSH_SECRET", "")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    logger.warning(
        "LINE_CHANNEL_SECRET or LINE_CHANNEL_ACCESS_TOKEN not set — "
        "signature verification will fail in production."
    )
if not OWNER_LINE_USER_ID:
    logger.warning("OWNER_LINE_USER_ID not set — owner mode disabled.")

parser = WebhookParser(LINE_CHANNEL_SECRET)
line_configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
_event_dedup = WebhookDeduplicator(ttl_seconds=300, cleanup_threshold=200)


STATE_IDLE = "IDLE"
STATE_ONBOARDING = "ONBOARDING"

_user_states: dict[str, str] = {}
_conversation_history: dict[str, list[dict]] = {}


app = FastAPI(title="水豚教練 (Capybara Coach)")


def is_owner(user_id: str) -> bool:
    return bool(OWNER_LINE_USER_ID) and user_id == OWNER_LINE_USER_ID


def _reply_line(reply_token: str, text: str) -> None:
    """Send a reply message via LINE Messaging API."""
    with ApiClient(line_configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )


@app.get("/health")
def health():
    return {"status": "ok", "service": "capybara-coach"}


@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        event_id = getattr(event, "webhook_event_id", None)
        if _event_dedup.is_duplicate(event_id):
            logger.info(f"Duplicate event skipped: {event_id}")
            continue

        user_id = getattr(getattr(event, "source", None), "user_id", "") or ""
        owner_mode = is_owner(user_id)

        if isinstance(event, MessageEvent):
            _handle_message_event(event, user_id, owner_mode)
        elif isinstance(event, PostbackEvent):
            _handle_postback_event(event, user_id, owner_mode)
        else:
            logger.info(f"Unhandled event type: {type(event).__name__}")

    return {"status": "ok"}


def _resolve_state(user_id: str) -> str:
    """Current-state lookup with lazy first-time detection.

    If we've never seen this user in memory, check GCS: no profile means
    they're new, so enter ONBOARDING. Profile present means back in IDLE.
    """
    if user_id in _user_states:
        return _user_states[user_id]
    if gcs_profile.profile_exists(user_id):
        _user_states[user_id] = STATE_IDLE
    else:
        _user_states[user_id] = STATE_ONBOARDING
    return _user_states[user_id]


def _handle_message_event(event, user_id: str, owner_mode: bool) -> None:
    message = getattr(event, "message", None)
    if message is None or getattr(message, "type", "") != "text":
        logger.info(f"Non-text message skipped: {getattr(message, 'type', 'unknown')}")
        return

    user_text = getattr(message, "text", "") or ""
    reply_token = getattr(event, "reply_token", "") or ""

    # Owner (developer) always skips onboarding — no interview needed.
    state = STATE_IDLE if owner_mode else _resolve_state(user_id)

    if state == STATE_ONBOARDING:
        history = _conversation_history.setdefault(user_id, [])
        reply_text, is_complete = onboarding_reply(
            user_text=user_text,
            user_id=user_id,
            history=history,
        )
        if is_complete:
            _user_states[user_id] = STATE_IDLE
            _conversation_history.pop(user_id, None)
    else:
        reply_text = coach_reply(user_text, user_id=user_id, owner=owner_mode)

    if reply_token:
        _reply_line(reply_token, reply_text)


def _handle_postback_event(event, user_id: str, owner_mode: bool) -> None:
    data = getattr(getattr(event, "postback", None), "data", "") or ""
    logger.info(f"Postback received (user={user_id[:8]}..., data={data!r}) — no-op in Phase 1")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
