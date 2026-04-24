"""Tests for tools/onboarding_reply.py — ONBOARDING state handler."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mocks.claude_mock import MockClaudeClient  # noqa: E402
from mocks.gcs_mock import MockGCSClient  # noqa: E402
from tools import onboarding_reply as ob  # noqa: E402


_MINIMAL_SYSTEM_PROMPT = "你是水豚教練，開始訪談新學員。完成時加上 [PROFILE_COMPLETE]。"


class TestSystemPromptFile:
    """Guard against accidental regressions in the production onboarding XML."""

    def test_file_exists(self):
        assert ob.SYSTEM_PROMPT_PATH.exists()

    def test_file_contains_required_greeting(self):
        text = ob.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        assert "初次見面，很高興認識你。我是水豚教練。你可以叫我教練，或是卡皮、卡皮教練。" in text

    def test_file_lists_all_four_aliases(self):
        text = ob.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        for alias in ("水豚教練", "教練", "卡皮", "卡皮教練"):
            assert alias in text, f"alias {alias!r} missing from onboarding prompt"

    def test_file_documents_completion_marker(self):
        text = ob.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        assert "[PROFILE_COMPLETE]" in text

    def test_file_handles_uncertain_users_and_frustration(self):
        """Onboarding should give space when the user is unsure and acknowledge
        prior training frustration before moving on."""
        text = ob.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        assert "不確定" in text
        assert "挫折" in text

_COMPLETE_JSON = json.dumps(
    {
        "goal": "增肌",
        "current_level": "完全新手",
        "available_days": 3,
        "session_duration": 45,
        "equipment": "健身房",
        "injury_history": "無",
        "race_target": "無",
    },
    ensure_ascii=False,
)


class TestOnboardingReply:
    def test_first_turn_returns_non_empty_reply(self):
        client = MockClaudeClient.with_text("你好，我是水豚教練。你最近想練什麼？")
        history: list[dict] = []
        reply, is_complete = ob.onboarding_reply(
            user_text="你好",
            user_id="U_NEW",
            history=history,
            system_prompt=_MINIMAL_SYSTEM_PROMPT,
            client=client,
            gcs_client=MockGCSClient(),
            bucket="capybara-profiles",
        )
        assert reply
        assert is_complete is False
        # User and assistant turn appended to history
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "你好"
        assert history[1]["role"] == "assistant"

    def test_history_is_forwarded_to_llm(self):
        client = MockClaudeClient.with_text("那每週幾天可以練？")
        history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "最近想練什麼？"},
        ]
        ob.onboarding_reply(
            user_text="想增肌",
            user_id="U_H",
            history=history,
            system_prompt=_MINIMAL_SYSTEM_PROMPT,
            client=client,
            gcs_client=MockGCSClient(),
            bucket="capybara-profiles",
        )
        call = client.messages.calls[0]
        msgs = call["messages"]
        # First three historical + latest user msg
        assert msgs[0]["content"] == "你好"
        assert msgs[-1]["content"] == "想增肌"

    def test_profile_complete_marker_triggers_extraction(self):
        # First call: interview reply with marker
        # Second call: extraction JSON
        llm_reply_with_marker = "好，我大概掌握了你的情況。[PROFILE_COMPLETE]"
        client = MockClaudeClient(
            responder=lambda kwargs: (
                _COMPLETE_JSON
                if any("擷取" in (m.get("content") or "") or "資料擷取" in (kwargs.get("system") or "")
                       for m in kwargs.get("messages", []))
                or "資料擷取" in (kwargs.get("system") or "")
                else llm_reply_with_marker
            )
        )
        gcs = MockGCSClient()
        history: list[dict] = [
            {"role": "user", "content": "想增肌"},
            {"role": "assistant", "content": "每週幾天可練？"},
        ]

        reply, is_complete = ob.onboarding_reply(
            user_text="一週三次，一次 45 分鐘，在健身房",
            user_id="U_DONE",
            history=history,
            system_prompt=_MINIMAL_SYSTEM_PROMPT,
            client=client,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )

        assert is_complete is True
        # Marker stripped from user-visible reply
        assert "[PROFILE_COMPLETE]" not in reply
        # Profile written to GCS
        assert "U_DONE/athlete_profile.md" in gcs.bucket("capybara-profiles").all_blobs()

    def test_user_says_finish_triggers_completion(self):
        # Single LLM call needed — extraction only (user forced finish)
        client = MockClaudeClient.with_text(_COMPLETE_JSON)
        gcs = MockGCSClient()
        history = [
            {"role": "user", "content": "想增肌"},
            {"role": "assistant", "content": "每週幾天？"},
            {"role": "user", "content": "三天"},
            {"role": "assistant", "content": "每次多久？"},
        ]
        reply, is_complete = ob.onboarding_reply(
            user_text="結束",
            user_id="U_QUIT",
            history=history,
            system_prompt=_MINIMAL_SYSTEM_PROMPT,
            client=client,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        assert is_complete is True
        blobs = gcs.bucket("capybara-profiles").all_blobs()
        assert "U_QUIT/athlete_profile.md" in blobs
        assert "U_QUIT/training_plan.md" in blobs

    def test_completion_message_includes_week_focus(self):
        """When plan body contains '本週重點', the onboarding completion message surfaces it."""
        plan_body = (
            "## 本週重點\n"
            "先從深蹲硬舉起步，動作先於重量。\n"
            "\n"
            "## 週課表\n"
            "| 星期 | 訓練內容 | 時長 | 強度 |\n"
            "|---|---|---|---|\n"
            "| 一 | 下肢 | 45 分 | 中 |\n"
            "\n"
            "## 調整記錄\n"
            "（尚無調整）\n"
        )

        def responder(kwargs: dict) -> str:
            system = kwargs.get("system") or ""
            if "資料擷取工具" in system:
                return _COMPLETE_JSON
            if "產生第一份" in system:
                return plan_body
            return ""  # onboarding turn unused on quit path

        client = MockClaudeClient(responder=responder)
        gcs = MockGCSClient()
        history = [{"role": "user", "content": "想增肌"}]
        reply, is_complete = ob.onboarding_reply(
            user_text="結束",
            user_id="U_FOCUS",
            history=history,
            system_prompt=_MINIMAL_SYSTEM_PROMPT,
            client=client,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        assert is_complete is True
        assert "本週重點：先從深蹲硬舉起步" in reply

    def test_marker_stripped_but_rest_visible(self):
        """Interview reply ends with marker — user shouldn't see it but the rest stays."""
        raw = "好的，我已經有足夠資訊了。 [PROFILE_COMPLETE]"
        client = MockClaudeClient(
            responder=lambda kwargs: (
                _COMPLETE_JSON
                if "資料擷取工具" in (kwargs.get("system") or "")
                else raw
            )
        )
        reply, _ = ob.onboarding_reply(
            user_text="好",
            user_id="U_S",
            history=[],
            system_prompt=_MINIMAL_SYSTEM_PROMPT,
            client=client,
            gcs_client=MockGCSClient(),
            bucket="capybara-profiles",
        )
        assert "[PROFILE_COMPLETE]" not in reply
        assert "好的，我已經有足夠資訊了" in reply


class TestWebhookIntegration:
    """Webhook-level behaviour: state transitions, new-user detection, routing."""

    def test_new_user_without_profile_enters_onboarding(self):
        """First message from user with no GCS profile → onboarding_reply is called, not coach_reply."""
        from fastapi.testclient import TestClient
        from linebot.v3.webhooks import MessageEvent
        from unittest.mock import MagicMock

        import tools.line_webhook as webhook_mod
        from tools.line_webhook import _event_dedup, _user_states, app

        _event_dedup._seen = {}
        _user_states.clear()

        ev = MagicMock(spec=MessageEvent)
        ev.webhook_event_id = "evt_new"
        ev.reply_token = "rt_new"
        ev.source = MagicMock()
        ev.source.user_id = "U_BRAND_NEW"
        ev.message = MagicMock()
        ev.message.type = "text"
        ev.message.text = "你好"

        client = TestClient(app)
        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=False), \
             patch("tools.line_webhook.onboarding_reply",
                   return_value=("歡迎，想練什麼？", False)) as mock_onb, \
             patch("tools.line_webhook.coach_reply") as mock_coach, \
             patch("tools.line_webhook._reply_line"):
            r = client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert r.status_code == 200
        assert mock_onb.called
        assert not mock_coach.called
        assert _user_states.get("U_BRAND_NEW") == webhook_mod.STATE_ONBOARDING

    def test_existing_user_with_profile_routes_to_coach(self):
        from fastapi.testclient import TestClient
        from linebot.v3.webhooks import MessageEvent
        from unittest.mock import MagicMock

        from tools.line_webhook import _event_dedup, _user_states, app

        _event_dedup._seen = {}
        _user_states.clear()

        ev = MagicMock(spec=MessageEvent)
        ev.webhook_event_id = "evt_existing"
        ev.reply_token = "rt_existing"
        ev.source = MagicMock()
        ev.source.user_id = "U_VETERAN"
        ev.message = MagicMock()
        ev.message.type = "text"
        ev.message.text = "今天要練什麼"

        client = TestClient(app)
        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=True), \
             patch("tools.line_webhook.onboarding_reply") as mock_onb, \
             patch("tools.line_webhook.coach_reply", return_value="Zone 2 40 分") as mock_coach, \
             patch("tools.line_webhook._reply_line"):
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert mock_coach.called
        assert not mock_onb.called

    def test_onboarding_completion_transitions_to_idle(self):
        from fastapi.testclient import TestClient
        from linebot.v3.webhooks import MessageEvent
        from unittest.mock import MagicMock

        import tools.line_webhook as webhook_mod
        from tools.line_webhook import _event_dedup, _user_states, app

        _event_dedup._seen = {}
        _user_states.clear()
        _user_states["U_FINISH"] = webhook_mod.STATE_ONBOARDING

        ev = MagicMock(spec=MessageEvent)
        ev.webhook_event_id = "evt_finish"
        ev.reply_token = "rt_finish"
        ev.source = MagicMock()
        ev.source.user_id = "U_FINISH"
        ev.message = MagicMock()
        ev.message.type = "text"
        ev.message.text = "結束"

        client = TestClient(app)
        with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
             patch("tools.line_webhook.gcs_profile.profile_exists", return_value=False), \
             patch("tools.line_webhook.onboarding_reply",
                   return_value=("完成。訓練計畫已建立。", True)), \
             patch("tools.line_webhook._reply_line"):
            client.post("/callback", headers={"X-Line-Signature": "s"}, content=b"{}")

        assert _user_states.get("U_FINISH") == webhook_mod.STATE_IDLE
