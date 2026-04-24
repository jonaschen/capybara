"""Tests for owner command dispatch (/help, /status, /plan, /adjust)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ["LINE_CHANNEL_SECRET"] = "test_secret"
os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "test_token"
os.environ["OWNER_LINE_USER_ID"] = "U_OWNER"
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

import tools.line_webhook as webhook_mod  # noqa: E402
from tools.line_webhook import (  # noqa: E402
    _conversation_history,
    _event_dedup,
    _user_states,
    app,
)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    _event_dedup._seen = {}
    _user_states.clear()
    _conversation_history.clear()
    # Ensure OWNER var is honored even if another test mutated it
    monkeypatch.setattr(webhook_mod, "OWNER_LINE_USER_ID", "U_OWNER")
    yield
    _event_dedup._seen = {}
    _user_states.clear()
    _conversation_history.clear()


def _fake_event(text: str, user_id: str, event_id: str = "evt_cmd"):
    from linebot.v3.webhooks import MessageEvent

    ev = MagicMock(spec=MessageEvent)
    ev.webhook_event_id = event_id
    ev.reply_token = "rt_" + event_id
    ev.source = MagicMock()
    ev.source.user_id = user_id
    ev.message = MagicMock()
    ev.message.type = "text"
    ev.message.text = text
    return ev


def _post(event, expect_status: int = 200) -> list[tuple[str, str]]:
    """Run the webhook with a captured _reply_line; return list of (token, text)."""
    captured: list[tuple[str, str]] = []
    client = TestClient(app)
    with patch("tools.line_webhook.parser.parse", return_value=[event]), \
         patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
         patch("tools.line_webhook._reply_line",
               side_effect=lambda t, x: captured.append((t, x))):
        r = client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")
    assert r.status_code == expect_status
    return captured


class TestHelp:
    def test_help_lists_commands(self):
        out = _post(_fake_event("/help", "U_OWNER"))
        assert len(out) == 1
        _, text = out[0]
        assert "/help" in text
        assert "/status" in text
        assert "/plan" in text
        assert "/adjust" in text


class TestStatus:
    def test_status_shows_state_and_owner(self):
        _user_states["U_OWNER"] = "IDLE"
        out = _post(_fake_event("/status", "U_OWNER"))
        _, text = out[0]
        assert "IDLE" in text
        assert "owner" in text.lower() or "管理" in text


class TestPlan:
    def test_plan_returns_current_training_plan(self):
        plan = "# 訓練計畫 — 增肌\n本週重點：深蹲"
        with patch("tools.line_webhook.gcs_profile.read_profile", return_value=plan):
            out = _post(_fake_event("/plan", "U_OWNER"))
        _, text = out[0]
        assert "深蹲" in text

    def test_plan_handles_missing_plan_gracefully(self):
        with patch(
            "tools.line_webhook.gcs_profile.read_profile",
            side_effect=FileNotFoundError,
        ):
            out = _post(_fake_event("/plan", "U_OWNER"))
        _, text = out[0]
        # Friendly message, not a traceback
        assert "找不到" in text or "尚未" in text or "not found" in text.lower()


class TestAdjust:
    def test_adjust_triggers_plan_adjuster_with_reason(self):
        updated = "# 訓練計畫 — 增肌\nGenerated: 2026-03-01\n## 本週重點\n暫停深蹲"
        with patch("tools.line_webhook.plan_adjuster.adjust_plan",
                   return_value=updated) as mock_adj:
            out = _post(_fake_event("/adjust 膝蓋不適", "U_OWNER"))
        assert mock_adj.called
        assert mock_adj.call_args.kwargs.get("reason") == "膝蓋不適"
        assert mock_adj.call_args.kwargs.get("user_id") == "U_OWNER"
        _, text = out[0]
        assert "暫停深蹲" in text or "本週重點" in text

    def test_adjust_without_reason_prompts(self):
        out = _post(_fake_event("/adjust", "U_OWNER"))
        _, text = out[0]
        # Helpful nudge, not a silent no-op
        assert "/adjust" in text
        assert "理由" in text or "reason" in text.lower()


class TestNonOwnerFallthrough:
    def test_non_owner_slash_message_goes_to_coach_reply(self):
        """Non-owner typing /help should be treated as a regular message, not privileged."""
        with patch("tools.line_webhook.coach_reply", return_value="我聽不懂這個指令。") as mock_coach:
            out = _post(_fake_event("/help", "U_REGULAR"))
        assert mock_coach.called
        # Coach reply passed the raw text through (no owner dispatch)
        call_args = mock_coach.call_args
        user_text = call_args.args[0] if call_args.args else call_args.kwargs.get("user_text")
        assert user_text == "/help"
        assert call_args.kwargs.get("owner") is False
