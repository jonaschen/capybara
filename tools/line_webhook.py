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
from linebot.v3.webhooks import FollowEvent, MessageEvent, PostbackEvent

from tools import daily_push, gcs_profile, known_users, plan_adjuster, state_store
from tools.coach_reply import coach_reply
from tools.onboarding_reply import onboarding_reply
from tools.webhook_dedup import WebhookDeduplicator

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# google-genai's `AFC is enabled with max remote calls: 10` line fires on every
# LLM call and pollutes the daily log review. Lift to WARNING.
logging.getLogger("google_genai.models").setLevel(logging.WARNING)
logger = logging.getLogger("capybara.webhook")


WELCOME_TEXT = (
    "初次見面，很高興認識你。卡皮教練在這。"
    "你也可以叫卡皮、教練、或水豚教練。\n\n"
    "想聊什麼隨時說，卡皮在這聽喔。🐾"
)


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


def _check_push_bearer(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {DAILY_PUSH_SECRET}" if DAILY_PUSH_SECRET else None
    if not expected or auth != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/trigger/daily_push")
async def trigger_daily_push(request: Request):
    _check_push_bearer(request)

    body = await request.json()
    push_type = body.get("push_type", "")
    if push_type not in ("morning", "evening"):
        raise HTTPException(status_code=400, detail="push_type must be 'morning' or 'evening'")

    with ApiClient(line_configuration) as api_client:
        line_api = MessagingApi(api_client)
        result = daily_push.send_daily_push(push_type=push_type, line_api=line_api)
    return result


@app.post("/trigger/onboarding_invite")
async def trigger_onboarding_invite(request: Request):
    """Periodic re-invitation of users who follow but never onboard.
    Cloud Scheduler: capybara-invite-stalled (Wed 11:00 Asia/Taipei).
    Throttled by known_users module: 7-day cooldown, max 3 invites total."""
    _check_push_bearer(request)

    with ApiClient(line_configuration) as api_client:
        line_api = MessagingApi(api_client)
        result = known_users.send_onboarding_invites(line_api=line_api)
    return result


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

        # Always record the user — even if they only ever follow and never
        # speak, we want a known_user.json so /trigger/onboarding_invite
        # can reach them. Owner is excluded from records to keep the invite
        # list focused on real users.
        if user_id and not owner_mode:
            try:
                known_users.record_user_seen(user_id)
            except Exception as exc:
                logger.warning(f"record_user_seen failed for {user_id[:8]}...: {exc}")

        if isinstance(event, MessageEvent):
            _handle_message_event(event, user_id, owner_mode)
        elif isinstance(event, FollowEvent):
            _handle_follow_event(event, user_id)
        elif isinstance(event, PostbackEvent):
            _handle_postback_event(event, user_id, owner_mode)
        else:
            logger.info(f"Unhandled event type: {type(event).__name__}")

    return {"status": "ok"}


_OWNER_HELP_TEXT = (
    "Owner commands:\n"
    "/help — 顯示這個清單\n"
    "/status — 目前狀態與使用者 id\n"
    "/plan — 顯示目前訓練計畫\n"
    "/adjust <理由> — 根據理由調整計畫（例如 /adjust 膝蓋不適）\n"
    "/onboard — 強制進入 onboarding 流程（重新接受訪談）"
)


def _handle_owner_command(user_text: str, user_id: str) -> str | None:
    """Dispatch owner slash commands. Returns the reply text, or None if
    this isn't a recognized owner command (webhook should fall through to
    normal routing)."""
    stripped = user_text.strip()
    if not stripped.startswith("/"):
        return None

    head, _, tail = stripped.partition(" ")
    cmd = head.lower()
    arg = tail.strip()

    if cmd == "/help":
        return _OWNER_HELP_TEXT

    if cmd == "/status":
        state = _user_states.get(user_id, STATE_IDLE)
        return f"owner mode ON\nstate: {state}\nuser_id: {user_id}"

    if cmd == "/plan":
        try:
            plan_md = gcs_profile.read_profile(user_id, filename="training_plan.md")
        except FileNotFoundError:
            return "找不到目前計畫。先完成 onboarding 或執行 /adjust 啟動第一份。"
        return plan_md

    if cmd == "/adjust":
        if not arg:
            return "請提供調整理由，例如：/adjust 膝蓋不適"
        try:
            updated = plan_adjuster.adjust_plan(user_id=user_id, reason=arg)
        except FileNotFoundError:
            return "找不到目前計畫可調整。先完成 onboarding。"
        return f"已調整。\n\n{updated}"

    if cmd == "/onboard":
        _user_states[user_id] = STATE_ONBOARDING
        _conversation_history.pop(user_id, None)
        return "已切回 onboarding。下一則訊息開始訪談。"

    return None


def _resolve_state(user_id: str) -> str:
    """Current-state lookup with lazy first-time detection.

    Resolution order (Cloud Run statelessness — instance restarts must not
    drop a user mid-onboarding):
      1. In-memory _user_states — fastest path for active conversations.
      2. state_store (GCS) — if there's a saved onboarding-in-progress
         state, restore history into _conversation_history and resume
         ONBOARDING. This makes the bot survive cold starts and scale-out.
      3. gcs_profile.profile_exists — first-time fallback. No profile →
         new user → ONBOARDING. Profile present → IDLE.
    """
    if user_id in _user_states:
        return _user_states[user_id]

    saved = state_store.load_onboarding_state(user_id)
    if saved is not None:
        _conversation_history[user_id] = list(saved.get("history", []))
        _user_states[user_id] = STATE_ONBOARDING
        logger.info(f"Restored onboarding state for {user_id[:8]}... ({len(_conversation_history[user_id])} msgs)")
        return STATE_ONBOARDING

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

    # Owner slash commands intercept before state routing. Unknown commands
    # fall through so the developer can still chat normally.
    if owner_mode:
        cmd_reply = _handle_owner_command(user_text, user_id)
        if cmd_reply is not None:
            if reply_token:
                _reply_line(reply_token, cmd_reply)
            return

    # Owner skips auto-onboarding (no GCS profile lookup), defaulting to IDLE.
    # /onboard explicitly opts back in by setting STATE_ONBOARDING in _user_states.
    if owner_mode:
        state = _user_states.get(user_id, STATE_IDLE)
    else:
        state = _resolve_state(user_id)

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
            state_store.clear_onboarding_state(user_id)
        else:
            # Persist after every turn so a Cloud Run restart can resume.
            state_store.save_onboarding_state(user_id, history)
    else:
        reply_text = coach_reply(user_text, user_id=user_id, owner=owner_mode)

    if reply_token:
        _reply_line(reply_token, reply_text)

    # One log line per turn so daily review can see what was said and what
    # was answered. Truncated to keep logs scannable; full text lives in GCS
    # (athlete_profile, training_plan) and the LLM call traces.
    logger.info(
        f"TURN user={user_id[:8]}... state={state} "
        f"in={user_text[:80]!r} out={reply_text[:80]!r}"
    )


def _handle_follow_event(event, user_id: str) -> None:
    """User added the bot as a friend. Send the standard greeting so they
    know the bot is alive and what to do next. FollowEvent has a reply_token
    in linebot v3 — same channel as MessageEvent."""
    reply_token = getattr(event, "reply_token", "") or ""
    logger.info(f"FOLLOW user={user_id[:8]}...")
    if reply_token:
        _reply_line(reply_token, WELCOME_TEXT)


def _handle_postback_event(event, user_id: str, owner_mode: bool) -> None:
    data = getattr(getattr(event, "postback", None), "data", "") or ""
    logger.info(f"Postback received (user={user_id[:8]}..., data={data!r}) — no-op in Phase 1")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
