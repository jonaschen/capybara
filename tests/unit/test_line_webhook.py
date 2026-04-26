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


def _fake_image_event(user_id: str, message_id: str = "img_1", webhook_event_id: str = "evt_img") -> MagicMock:
    """Build a MagicMock MessageEvent carrying an image message."""
    from linebot.v3.webhooks import MessageEvent

    ev = MagicMock(spec=MessageEvent)
    ev.webhook_event_id = webhook_event_id
    ev.reply_token = "rt_" + webhook_event_id
    ev.source = MagicMock()
    ev.source.user_id = user_id
    ev.message = MagicMock()
    ev.message.type = "image"
    ev.message.id = message_id
    return ev


class TestHealth:
    def test_health_returns_200(self):
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200


class TestTriggerOnboardingInvite:
    def test_rejects_missing_bearer(self, monkeypatch):
        monkeypatch.setattr("tools.line_webhook.DAILY_PUSH_SECRET", "s3cr3t")
        client = TestClient(app)
        r = client.post("/trigger/onboarding_invite", json={})
        assert r.status_code == 401

    def test_accepts_valid_bearer_and_forwards_to_known_users(self, monkeypatch):
        monkeypatch.setattr("tools.line_webhook.DAILY_PUSH_SECRET", "s3cr3t")
        canned = {"invited": 2, "failed": 0, "details": [
            {"user_id": "U1", "status": "invited"},
            {"user_id": "U2", "status": "invited"},
        ]}
        client = TestClient(app)
        with patch(
            "tools.line_webhook.known_users.send_onboarding_invites",
            return_value=canned,
        ) as mock_send:
            r = client.post(
                "/trigger/onboarding_invite",
                json={},
                headers={"Authorization": "Bearer s3cr3t"},
            )
        assert r.status_code == 200
        assert r.json() == canned
        assert mock_send.called


class TestRecordUserSeen:
    def test_message_event_records_user(self):
        client = TestClient(app)
        ev = _fake_message_event("你好", user_id="U_RECORD_ME", webhook_event_id="evt_rec")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.coach_reply", return_value="hi"), \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook.known_users.record_user_seen") as mock_rec:
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")
        assert mock_rec.called
        assert mock_rec.call_args.args[0] == "U_RECORD_ME"

    def test_owner_is_not_recorded(self):
        """Owner shouldn't appear in the invite list — they're the developer."""
        client = TestClient(app)
        ev = _fake_message_event("ping", user_id="U_OWNER", webhook_event_id="evt_owner")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.coach_reply", return_value="pong"), \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook.known_users.record_user_seen") as mock_rec:
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")
        assert not mock_rec.called

    def test_follow_event_records_user(self):
        from linebot.v3.webhooks import FollowEvent

        client = TestClient(app)
        ev = MagicMock(spec=FollowEvent)
        ev.webhook_event_id = "evt_f"
        ev.reply_token = "rt_f"
        ev.source = MagicMock()
        ev.source.user_id = "U_FOLLOWED"

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook.known_users.record_user_seen") as mock_rec:
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")
        assert mock_rec.called
        assert mock_rec.call_args.args[0] == "U_FOLLOWED"


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

    def test_turn_is_logged_with_user_text_and_reply_preview(self, caplog):
        """Per-turn log line must include user_text + reply preview so daily
        review can read what happened without diving into LLM call traces."""
        client = TestClient(app)
        ev = _fake_message_event("增肌怎麼開始", user_id="U_LOG", webhook_event_id="evt_log")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.coach_reply", return_value="先做複合動作。"), \
             patch("tools.line_webhook._reply_line"):
            with caplog.at_level("INFO", logger="capybara.webhook"):
                client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        turn_lines = [r.message for r in caplog.records if r.message.startswith("TURN ")]
        assert len(turn_lines) == 1, f"expected 1 TURN log, got {turn_lines!r}"
        line = turn_lines[0]
        assert "U_LOG"[:8] in line
        assert "增肌怎麼開始" in line
        assert "先做複合動作" in line


class TestFollowEvent:
    def test_follow_event_sends_welcome(self):
        from linebot.v3.webhooks import FollowEvent

        client = TestClient(app)
        ev = MagicMock(spec=FollowEvent)
        ev.webhook_event_id = "evt_follow"
        ev.reply_token = "rt_follow"
        ev.source = MagicMock()
        ev.source.user_id = "U_NEW_FRIEND"

        captured: list[tuple[str, str]] = []
        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch(
                 "tools.line_webhook._reply_line",
                 side_effect=lambda token, text: captured.append((token, text)),
             ):
            r = client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert r.status_code == 200
        assert len(captured) == 1
        token, text = captured[0]
        assert token == "rt_follow"
        # Welcome lists the alias options so the new friend knows what to
        # call the bot.
        assert "卡皮" in text
        assert "教練" in text
        # New voice: open-ended invitation, no command/imperative.
        assert "隨時" in text
        # Strict no-我 voice rule.
        from tools.line_webhook import WELCOME_TEXT
        assert "我" not in WELCOME_TEXT


class TestRestartRecovery:
    def test_restart_recovery_restores_onboarding_state(self):
        """Simulates a Cloud Run cold start: in-memory state is empty, but a
        state_store load returns a prior conversation. _resolve_state must
        restore history + state, and the next message routes to onboarding."""
        client = TestClient(app)
        ev = _fake_message_event("我想增肌", user_id="U_RESTART")

        prior_history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "想練什麼？"},
        ]
        from tools.line_webhook import _conversation_history, _user_states

        with patch(
            "tools.line_webhook.state_store.load_onboarding_state",
            return_value={"history": prior_history, "saved_at": "2026-04-25T00:00:00Z"},
        ), patch(
            "tools.line_webhook.state_store.save_onboarding_state"
        ), patch(
            "tools.line_webhook.parser.parse", return_value=[ev]
        ), patch(
            "tools.line_webhook.onboarding_reply",
            return_value=("好的，每週幾天可以練？", False),
        ) as mock_onb, patch(
            "tools.line_webhook.coach_reply"
        ) as mock_coach, patch(
            "tools.line_webhook._reply_line"
        ):
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        # Onboarding (not coach) was called
        assert mock_onb.called
        assert not mock_coach.called
        # History was restored before onboarding ran — the call's history arg
        # must contain the prior turns, not start empty.
        call = mock_onb.call_args
        history_arg = call.kwargs.get("history") or call.args[2]
        assert history_arg[0]["content"] == "你好"
        assert history_arg[1]["content"] == "想練什麼？"
        # In-memory state populated for subsequent messages
        assert _user_states.get("U_RESTART") == "ONBOARDING"

    def test_onboarding_turn_persists_state(self):
        """Each non-final onboarding turn must call state_store.save."""
        client = TestClient(app)
        ev = _fake_message_event("增肌", user_id="U_SAVE")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=False), \
             patch("tools.line_webhook.state_store.load_onboarding_state", return_value=None), \
             patch("tools.line_webhook.state_store.save_onboarding_state") as mock_save, \
             patch("tools.line_webhook.state_store.clear_onboarding_state") as mock_clear, \
             patch("tools.line_webhook.onboarding_reply",
                   return_value=("每週幾天？", False)), \
             patch("tools.line_webhook._reply_line"):
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert mock_save.called
        assert not mock_clear.called

    def test_onboarding_completion_clears_persisted_state(self):
        """Final onboarding turn (is_complete=True) must clear the saved state."""
        client = TestClient(app)
        ev = _fake_message_event("結束", user_id="U_DONE")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=False), \
             patch("tools.line_webhook.state_store.load_onboarding_state", return_value=None), \
             patch("tools.line_webhook.state_store.save_onboarding_state") as mock_save, \
             patch("tools.line_webhook.state_store.clear_onboarding_state") as mock_clear, \
             patch("tools.line_webhook.onboarding_reply",
                   return_value=("好，計畫已建立。", True)), \
             patch("tools.line_webhook._reply_line"):
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert mock_clear.called
        assert not mock_save.called

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


class TestImageMessage:
    """Image events route through _handle_image_message with three branches."""

    def test_onboarding_image_replies_with_defer_text(self):
        """User mid-onboarding sends image → polite defer; image_reply NOT called."""
        from tools.line_webhook import STATE_ONBOARDING

        client = TestClient(app)
        ev = _fake_image_event(user_id="U_OB", webhook_event_id="evt_ob")
        _user_states["U_OB"] = STATE_ONBOARDING

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook._reply_line") as mock_reply, \
             patch("tools.line_webhook.analyze_training_image") as mock_analyze, \
             patch("tools.line_webhook._fetch_image_bytes") as mock_fetch:
            r = client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert r.status_code == 200
        assert mock_reply.called
        sent = mock_reply.call_args.args[1]
        assert "先聊一下基本資料" in sent
        # Main flow not entered
        assert not mock_analyze.called
        assert not mock_fetch.called

    def test_idle_no_profile_image_replies_with_defer_text(self):
        """Edge case: user is in IDLE but their athlete_profile.md is missing
        (e.g., owner who hasn't onboarded, or post-restart state-without-file).
        Should defer politely instead of calling LLM with empty context."""
        from tools.line_webhook import STATE_IDLE

        client = TestClient(app)
        ev = _fake_image_event(user_id="U_NEW", webhook_event_id="evt_new")
        # Force IDLE state so _resolve_state short-circuits (otherwise
        # missing profile would route the user to ONBOARDING).
        _user_states["U_NEW"] = STATE_IDLE

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=False), \
             patch("tools.line_webhook._reply_line") as mock_reply, \
             patch("tools.line_webhook.analyze_training_image") as mock_analyze, \
             patch("tools.line_webhook._fetch_image_bytes") as mock_fetch:
            r = client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert r.status_code == 200
        assert mock_reply.called
        sent = mock_reply.call_args.args[1]
        assert "還沒有你的訓練檔案" in sent
        assert not mock_analyze.called
        assert not mock_fetch.called

    def test_idle_with_profile_buffer_then_personalized_push(self):
        """Main flow: buffer reply → fetch → analyze → push personalized reply."""
        from tools.line_webhook import STATE_IDLE

        client = TestClient(app)
        ev = _fake_image_event(user_id="U_HAS", message_id="img_42", webhook_event_id="evt_has")
        _user_states["U_HAS"] = STATE_IDLE

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.gcs_profile.read_profile", return_value="目標：台東 226"), \
             patch("tools.line_webhook._reply_line") as mock_reply, \
             patch("tools.line_webhook._push_line") as mock_push, \
             patch("tools.line_webhook._fetch_image_bytes",
                   return_value=(b"\x89PNG\r\n\x1a\nfake", "image/png")), \
             patch("tools.line_webhook.analyze_training_image",
                   return_value=("卡皮看到你跑了 10K，後半掉速。🐾",
                                 "[訓練截圖：跑步, 10km, 配速趨勢：後半掉速]",
                                 {"size_kb": 12.3, "in_tok": 850, "out_tok": 90, "error": None})):
            r = client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert r.status_code == 200
        # Buffer reply via reply_token
        assert mock_reply.called
        buffer_text = mock_reply.call_args.args[1]
        assert "稍等卡皮看看" in buffer_text
        # Personalized push (no owner footer)
        assert mock_push.called
        pushed = mock_push.call_args.args
        assert pushed[0] == "U_HAS"
        assert "卡皮看到你跑了 10K" in pushed[1]
        assert "[🏊" not in pushed[1]  # no owner footer for non-owner

    def test_idle_with_profile_non_training_image_pushes_fallback(self):
        from tools.line_webhook import STATE_IDLE

        client = TestClient(app)
        ev = _fake_image_event(user_id="U_FOOD", webhook_event_id="evt_food")
        _user_states["U_FOOD"] = STATE_IDLE

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.gcs_profile.read_profile", return_value=""), \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook._push_line") as mock_push, \
             patch("tools.line_webhook._fetch_image_bytes", return_value=(b"x", "image/jpeg")), \
             patch("tools.line_webhook.analyze_training_image",
                   return_value=(None, "[傳了一張非訓練數據圖片]",
                                 {"size_kb": 0.1, "in_tok": 100, "out_tok": 0, "error": None})):
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert mock_push.called
        pushed_text = mock_push.call_args.args[1]
        assert "不太像訓練數據" in pushed_text

    def test_idle_with_profile_fetch_failure_pushes_failure_text(self):
        """LINE blob fetch failure → push apology text, do not crash."""
        from tools.line_webhook import STATE_IDLE

        client = TestClient(app)
        ev = _fake_image_event(user_id="U_FAIL", webhook_event_id="evt_fail")
        _user_states["U_FAIL"] = STATE_IDLE

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook._push_line") as mock_push, \
             patch("tools.line_webhook._fetch_image_bytes",
                   side_effect=RuntimeError("blob api 500")), \
             patch("tools.line_webhook.analyze_training_image") as mock_analyze:
            r = client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert r.status_code == 200
        assert mock_push.called
        assert "讀圖失敗" in mock_push.call_args.args[1]
        # analyze not called when fetch fails
        assert not mock_analyze.called

    def test_owner_image_appends_debug_footer(self):
        from tools.line_webhook import STATE_IDLE

        client = TestClient(app)
        ev = _fake_image_event(user_id="U_OWNER", webhook_event_id="evt_oi")
        _user_states["U_OWNER"] = STATE_IDLE

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.gcs_profile.read_profile", return_value=""), \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook._push_line") as mock_push, \
             patch("tools.line_webhook._fetch_image_bytes",
                   return_value=(b"\x89PNG\r\n\x1a\nfake", "image/png")), \
             patch("tools.line_webhook.analyze_training_image",
                   return_value=("Zone 2 跑了 40 分。🐾",
                                 "[訓練截圖：跑步, 8km, 配速趨勢：穩定]",
                                 {"size_kb": 5.5, "in_tok": 700, "out_tok": 60, "error": None})):
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        pushed_text = mock_push.call_args.args[1]
        assert "🏊 image" in pushed_text
        assert "size: 5.5kb" in pushed_text
        assert "tokens: 700in/60out" in pushed_text

    def test_image_event_logs_one_image_line(self, caplog):
        """Daily review must see one IMAGE log line per image event."""
        from tools.line_webhook import STATE_IDLE

        client = TestClient(app)
        ev = _fake_image_event(user_id="U_LOG2", webhook_event_id="evt_log2")
        _user_states["U_LOG2"] = STATE_IDLE

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.gcs_profile.read_profile", return_value=""), \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook._push_line"), \
             patch("tools.line_webhook._fetch_image_bytes", return_value=(b"x", "image/jpeg")), \
             patch("tools.line_webhook.analyze_training_image",
                   return_value=("ok", "[訓練截圖：跑步, 5km, 配速趨勢：穩定]",
                                 {"size_kb": 1.0, "in_tok": 500, "out_tok": 50, "error": None})):
            with caplog.at_level("INFO", logger="capybara.webhook"):
                client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        image_lines = [r.message for r in caplog.records if r.message.startswith("IMAGE ")]
        assert len(image_lines) == 1
        line = image_lines[0]
        assert "U_LOG2"[:8] in line
        assert "kind=training" in line


class TestIdleChatHistory:
    """Phase B-lite: IDLE turns persist conversation history to chat_store
    and re-inject on subsequent calls so the bot has short-term continuity."""

    def test_idle_turn_appends_to_chat_store(self):
        from tools.line_webhook import STATE_IDLE

        client = TestClient(app)
        ev = _fake_message_event("今天可以練什麼", user_id="U_HIST", webhook_event_id="evt_h1")
        _user_states["U_HIST"] = STATE_IDLE

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.coach_reply", return_value="今天 Zone 2 跑 40 分。"), \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook.chat_store.save_chat_history") as mock_save:
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert mock_save.called
        saved_history = mock_save.call_args.args[1]
        assert {"role": "user", "content": "今天可以練什麼"} in saved_history
        assert {"role": "assistant", "content": "今天 Zone 2 跑 40 分。"} in saved_history

    def test_idle_history_passed_into_coach_reply(self):
        """Second IDLE turn: prior history should reach coach_reply via the
        history kwarg so the LLM has context."""
        from tools.line_webhook import STATE_IDLE, _conversation_history

        client = TestClient(app)
        _user_states["U_CTX"] = STATE_IDLE
        _conversation_history["U_CTX"] = [
            {"role": "user", "content": "上次卡皮說我有膝蓋舊傷"},
            {"role": "assistant", "content": "對，那邊要小心。"},
        ]
        ev = _fake_message_event("還記得嗎", user_id="U_CTX", webhook_event_id="evt_ctx")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.coach_reply", return_value="記得喔。") as mock_reply, \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook.chat_store.save_chat_history"):
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        kwargs = mock_reply.call_args.kwargs
        history_arg = kwargs.get("history", [])
        assert any("膝蓋舊傷" in m.get("content", "") for m in history_arg)

    def test_cold_start_loads_history_from_gcs(self):
        """First message after Cloud Run restart: in-memory dict is empty,
        webhook hydrates from chat_store before calling coach_reply."""
        from tools.line_webhook import STATE_IDLE

        client = TestClient(app)
        ev = _fake_message_event("我又來了", user_id="U_COLD", webhook_event_id="evt_cold")
        _user_states["U_COLD"] = STATE_IDLE
        # No in-memory entry yet — simulates fresh instance.

        prior = [
            {"role": "user", "content": "上週說工作壓力大"},
            {"role": "assistant", "content": "辛苦了。先休息再說。🐾"},
        ]
        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.chat_store.load_chat_history", return_value=prior) as mock_load, \
             patch("tools.line_webhook.coach_reply", return_value="嗨，工作有好點嗎？") as mock_reply, \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook.chat_store.save_chat_history"):
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert mock_load.called
        kwargs = mock_reply.call_args.kwargs
        history_arg = kwargs.get("history", [])
        assert any("工作壓力大" in m.get("content", "") for m in history_arg)

    def test_image_appends_summary_to_history(self):
        """Image event: structured summary stored as user content, bot reply
        as assistant content. Subsequent text turns can reference both."""
        from tools.line_webhook import STATE_IDLE

        client = TestClient(app)
        ev = _fake_image_event(user_id="U_IMG_H", webhook_event_id="evt_imgh")
        _user_states["U_IMG_H"] = STATE_IDLE

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.gcs_profile.read_profile", return_value=""), \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook._push_line"), \
             patch("tools.line_webhook._fetch_image_bytes",
                   return_value=(b"\x89PNG\r\n\x1a\nfake", "image/png")), \
             patch("tools.line_webhook.analyze_training_image",
                   return_value=("看到你跑了 10K，配速穩。🐾",
                                 "[訓練截圖：跑步, 10km, 配速趨勢：穩定]",
                                 {"size_kb": 5.0, "in_tok": 700, "out_tok": 60, "error": None})), \
             patch("tools.line_webhook.chat_store.save_chat_history") as mock_save:
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        saved = mock_save.call_args.args[1]
        # Image summary stored as user message — NOT empty string, NOT bot's reply
        user_msgs = [m for m in saved if m["role"] == "user"]
        assert any("[訓練截圖：跑步, 10km" in m["content"] for m in user_msgs)
        # Bot's interpretation stored as assistant message
        asst_msgs = [m for m in saved if m["role"] == "assistant"]
        assert any("看到你跑了 10K" in m["content"] for m in asst_msgs)

    def test_owner_history_command_lists_messages(self):
        from tools.line_webhook import STATE_IDLE, _conversation_history

        client = TestClient(app)
        _user_states["U_OWNER"] = STATE_IDLE
        _conversation_history["U_OWNER"] = [
            {"role": "user", "content": "今天要練嗎"},
            {"role": "assistant", "content": "Zone 2 40 分鐘。"},
        ]
        ev = _fake_message_event("/history", user_id="U_OWNER", webhook_event_id="evt_hist")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook._reply_line") as mock_reply:
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        sent = mock_reply.call_args.args[1]
        assert "目前歷史" in sent
        assert "今天要練嗎" in sent
        assert "Zone 2 40 分鐘" in sent

    def test_owner_history_empty_state_message(self):
        from tools.line_webhook import STATE_IDLE

        client = TestClient(app)
        _user_states["U_OWNER"] = STATE_IDLE
        ev = _fake_message_event("/history", user_id="U_OWNER", webhook_event_id="evt_hist2")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.chat_store.load_chat_history", return_value=[]), \
             patch("tools.line_webhook._reply_line") as mock_reply:
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        sent = mock_reply.call_args.args[1]
        assert "沒有對話歷史" in sent

    def test_onboard_command_clears_chat_store(self):
        """/onboard restarts the interview — the IDLE chat history must be
        wiped so it doesn't bleed into the new onboarding context."""
        client = TestClient(app)
        ev = _fake_message_event("/onboard", user_id="U_OWNER", webhook_event_id="evt_ob")

        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook._reply_line"), \
             patch("tools.line_webhook.chat_store.clear_chat_history") as mock_clear:
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert mock_clear.called
        assert mock_clear.call_args.args[0] == "U_OWNER"
