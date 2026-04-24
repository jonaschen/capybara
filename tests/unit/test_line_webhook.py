"""Tests for tools/line_webhook.py — FastAPI app, dispatch, owner detection."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("OWNER_LINE_USER_ID", "U_OWNER")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from tools.line_webhook import (  # noqa: E402
    _conversation_history,
    _event_dedup,
    _user_states,
    app,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Each test starts with clean dedup cache, user states, and history."""
    _event_dedup._seen = {}  # type: ignore[attr-defined]
    _user_states.clear()
    _conversation_history.clear()
    yield
    _event_dedup._seen = {}  # type: ignore[attr-defined]
    _user_states.clear()
    _conversation_history.clear()


def _fake_message_event(text: str, user_id: str, webhook_event_id: str = "evt_1") -> MagicMock:
    """Build a MagicMock that isinstance-matches MessageEvent."""
    from linebot.v3.webhooks import MessageEvent

    ev = MagicMock(spec=MessageEvent)
    ev.webhook_event_id = webhook_event_id
    ev.reply_token = "rt_" + webhook_event_id
    ev.source = MagicMock()
    ev.source.user_id = user_id
    ev.message = MagicMock()
    ev.message.type = "text"
    ev.message.text = text
    return ev


class TestHealth:
    def test_health_returns_200(self):
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200


class TestTriggerDailyPush:
    def test_rejects_missing_bearer(self, monkeypatch):
        monkeypatch.setattr("tools.line_webhook.DAILY_PUSH_SECRET", "s3cr3t")
        client = TestClient(app)
        r = client.post("/trigger/daily_push", json={"push_type": "morning"})
        assert r.status_code == 401

    def test_rejects_wrong_bearer(self, monkeypatch):
        monkeypatch.setattr("tools.line_webhook.DAILY_PUSH_SECRET", "s3cr3t")
        client = TestClient(app)
        r = client.post(
            "/trigger/daily_push",
            json={"push_type": "morning"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401

    def test_rejects_bad_push_type(self, monkeypatch):
        monkeypatch.setattr("tools.line_webhook.DAILY_PUSH_SECRET", "s3cr3t")
        client = TestClient(app)
        r = client.post(
            "/trigger/daily_push",
            json={"push_type": "afternoon"},
            headers={"Authorization": "Bearer s3cr3t"},
        )
        assert r.status_code == 400

    def test_accepts_valid_bearer_and_forwards_to_send_daily_push(self, monkeypatch):
        monkeypatch.setattr("tools.line_webhook.DAILY_PUSH_SECRET", "s3cr3t")
        canned = {"pushed": 2, "failed": 0, "results": [{"user_id": "U1", "status": "ok"}]}
        client = TestClient(app)
        with patch("tools.line_webhook.daily_push.send_daily_push", return_value=canned) as mock_send:
            r = client.post(
                "/trigger/daily_push",
                json={"push_type": "morning"},
                headers={"Authorization": "Bearer s3cr3t"},
            )
        assert r.status_code == 200
        assert r.json() == canned
        assert mock_send.called
        assert mock_send.call_args.kwargs.get("push_type") == "morning"


class TestCallback:
    def test_text_message_dispatches_to_coach_reply(self):
        client = TestClient(app)
        ev = _fake_message_event("增肌怎麼開始", user_id="U123")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.coach_reply", return_value="今天先做肌力基礎。") as mock_reply, \
             patch("tools.line_webhook._reply_line") as mock_send:
            r = client.post(
                "/callback",
                headers={"X-Line-Signature": "dummy"},
                content=b"{}",
            )

        assert r.status_code == 200
        # coach_reply was called with the user's text
        assert mock_reply.called
        call = mock_reply.call_args
        args, kwargs = call.args, call.kwargs
        # user_text is the first positional or keyword-accessible
        sent_text = args[0] if args else kwargs.get("user_text")
        assert sent_text == "增肌怎麼開始"
        # owner flag is False for non-owner user
        assert kwargs.get("owner", False) is False
        # _reply_line was called with the coach's response
        assert mock_send.called
        send_args = mock_send.call_args.args
        assert send_args[0] == ev.reply_token
        assert send_args[1] == "今天先做肌力基礎。"

    def test_owner_user_id_sets_owner_flag(self):
        client = TestClient(app)
        ev = _fake_message_event("今天練什麼", user_id="U_OWNER", webhook_event_id="evt_o")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.coach_reply", return_value="Zone 2 40 分") as mock_reply, \
             patch("tools.line_webhook._reply_line"):
            client.post(
                "/callback",
                headers={"X-Line-Signature": "dummy"},
                content=b"{}",
            )

        assert mock_reply.called
        assert mock_reply.call_args.kwargs.get("owner") is True

    def test_duplicate_webhook_event_id_is_skipped(self):
        client = TestClient(app)
        ev = _fake_message_event("今天練什麼", user_id="U123", webhook_event_id="evt_dup")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.coach_reply", return_value="ok") as mock_reply, \
             patch("tools.line_webhook._reply_line"):
            # First delivery
            r1 = client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")
            # Second delivery — should be deduped
            r2 = client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert mock_reply.call_count == 1
