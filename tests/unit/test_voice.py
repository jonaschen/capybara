"""Tests for tools/voice.py — central voice spec + cross-file no-『我』 guards.

The user's voice spec is non-negotiable: bot must NEVER use 「我」 in any
hardcoded user-facing string, and every system prompt that drives bot
speech must inject CAPYBARA_VOICE so the LLM follows the same rule.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.voice import CAPYBARA_VOICE  # noqa: E402


class TestVoiceConstant:
    @pytest.mark.parametrize(
        "rule_header",
        ["【自稱】", "【節奏】", "【語氣詞】", "【不製造罪惡感】", "【邀請而非要求】", "【emoji】"],
    )
    def test_all_six_rules_present(self, rule_header: str):
        assert rule_header in CAPYBARA_VOICE

    def test_self_reference_rule_is_strict(self):
        assert "絕對不用「我」" in CAPYBARA_VOICE
        assert "永遠用「卡皮教練」或「卡皮」稱呼自己" in CAPYBARA_VOICE

    def test_taiwan_particles_listed(self):
        assert "「喔」" in CAPYBARA_VOICE
        assert "「囉」" in CAPYBARA_VOICE
        assert "「啊」" in CAPYBARA_VOICE

    def test_banned_particles_listed(self):
        # 「哦」 and 「唷」 are explicitly forbidden in the voice
        assert "「哦」" in CAPYBARA_VOICE
        assert "「唷」" in CAPYBARA_VOICE


class TestNoFirstPersonInHardcodedText:
    """Every hardcoded user-facing string must follow rule 1 — no 「我」."""

    def test_welcome_text(self):
        from tools.line_webhook import WELCOME_TEXT
        assert "我" not in WELCOME_TEXT, f"WELCOME_TEXT contains 我: {WELCOME_TEXT!r}"

    def test_invite_text(self):
        from tools.known_users import INVITE_TEXT
        assert "我" not in INVITE_TEXT, f"INVITE_TEXT contains 我: {INVITE_TEXT!r}"

    def test_mandatory_injury_disclaimer(self):
        from tools.coach_reply import MANDATORY_INJURY_DISCLAIMER
        assert "我" not in MANDATORY_INJURY_DISCLAIMER


class TestVoiceInjectedIntoPrompts:
    """Every LLM-driving system prompt must embed CAPYBARA_VOICE so the
    bot's generated speech inherits the voice rules."""

    def test_coach_system_prompt(self):
        from tools.coach_reply import COACH_SYSTEM_PROMPT
        assert CAPYBARA_VOICE in COACH_SYSTEM_PROMPT

    def test_morning_push_prompt(self):
        from tools.daily_push import MORNING_SYSTEM_PROMPT
        assert CAPYBARA_VOICE in MORNING_SYSTEM_PROMPT

    def test_evening_push_prompt(self):
        from tools.daily_push import EVENING_SYSTEM_PROMPT
        assert CAPYBARA_VOICE in EVENING_SYSTEM_PROMPT

    def test_onboarding_xml_carries_voice_rules(self):
        """XML can't import Python; it carries a copy of the voice block.
        Guard against drift by checking the key rule phrases survive."""
        from tools import onboarding_reply as ob
        text = ob.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        assert "絕對不用「我」" in text
        assert "永遠用「卡皮教練」或「卡皮」稱呼自己" in text
        assert "【自稱】" in text
        assert "【邀請而非要求】" in text

    def test_image_reply_prompt(self):
        from tools.image_reply import build_image_prompt
        prompt = build_image_prompt("目標：完成台東 226")
        assert CAPYBARA_VOICE in prompt
        # Spot-check key voice rule phrases survive verbatim
        assert "絕對不用「我」" in prompt
        assert "卡皮教練" in prompt
