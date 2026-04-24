"""Tests for tools/plan_generator.py — initial training plan generation."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mocks.claude_mock import MockClaudeClient  # noqa: E402
from mocks.gcs_mock import MockGCSClient  # noqa: E402
from tools import plan_generator  # noqa: E402


_STRENGTH_PROFILE = {
    "goal": "增肌",
    "current_level": "完全新手",
    "available_days": 3,
    "session_duration": 45,
    "equipment": "健身房",
    "injury_history": "無",
    "race_target": "無",
}

_TRIATHLON_PROFILE = {
    "goal": "完成半程鐵人",
    "current_level": "規律訓練 6 個月",
    "available_days": 5,
    "session_duration": 60,
    "equipment": "健身房 + 自行車",
    "injury_history": "無",
    "race_target": "2026 秋季台東 113",
    "weight_height": "體重 72kg、身高 175cm",
}


_STRENGTH_PLAN_BODY = """## 本週重點
先從複合動作打基礎，動作先於重量。

## 週課表
| 星期 | 訓練內容 | 時長 | 強度 |
|---|---|---|---|
| 一 | 下肢 深蹲 + 硬舉 | 45 分 | 中 |
| 三 | 上肢推 臥推 + 肩推 | 45 分 | 中 |
| 五 | 上肢拉 引體 + 划船 | 45 分 | 中 |
| 其他 | 休息或輕度走路 | — | 極低 |

## 調整記錄
（尚無調整）
"""


class TestGeneratePlan:
    def test_writes_plan_to_gcs(self):
        llm = MockClaudeClient.with_text(_STRENGTH_PLAN_BODY)
        gcs = MockGCSClient()
        plan_generator.generate_plan(
            profile=_STRENGTH_PROFILE,
            user_id="U_P1",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        assert "U_P1/training_plan.md" in gcs.bucket("capybara-profiles").all_blobs()

    def test_plan_contains_spec_required_sections(self):
        llm = MockClaudeClient.with_text(_STRENGTH_PLAN_BODY)
        gcs = MockGCSClient()
        md = plan_generator.generate_plan(
            profile=_STRENGTH_PROFILE,
            user_id="U_P2",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        assert md.startswith("# 訓練計畫 —")
        assert "Generated:" in md
        assert "Valid until:" in md
        assert "Last adjusted:" in md
        assert "## 本週重點" in md
        assert "## 週課表" in md
        assert "## 調整記錄" in md

    def test_dates_are_deterministic_not_from_llm(self):
        """Generator injects today + 4 weeks regardless of what the LLM said."""
        # LLM returns body without any date — the wrapper adds them
        llm = MockClaudeClient.with_text(_STRENGTH_PLAN_BODY)
        gcs = MockGCSClient()
        md = plan_generator.generate_plan(
            profile=_STRENGTH_PROFILE,
            user_id="U_P3",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        today = date.today().isoformat()
        valid_until = (date.today() + timedelta(weeks=4)).isoformat()
        assert f"Generated: {today}" in md
        assert f"Valid until: {valid_until}" in md

    def test_header_contains_goal(self):
        llm = MockClaudeClient.with_text(_STRENGTH_PLAN_BODY)
        gcs = MockGCSClient()
        md = plan_generator.generate_plan(
            profile=_TRIATHLON_PROFILE,
            user_id="U_TRI",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        assert "# 訓練計畫 — 完成半程鐵人" in md

    def test_llm_receives_profile_in_user_message(self):
        llm = MockClaudeClient.with_text(_STRENGTH_PLAN_BODY)
        gcs = MockGCSClient()
        plan_generator.generate_plan(
            profile=_TRIATHLON_PROFILE,
            user_id="U_LLM",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        call = llm.messages.calls[0]
        user_content = call["messages"][0]["content"]
        assert "完成半程鐵人" in user_content
        assert "2026 秋季台東 113" in user_content

    def test_llm_gets_plan_system_prompt(self):
        llm = MockClaudeClient.with_text(_STRENGTH_PLAN_BODY)
        gcs = MockGCSClient()
        plan_generator.generate_plan(
            profile=_STRENGTH_PROFILE,
            user_id="U_SYS",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        system = llm.messages.calls[0]["system"]
        assert "本週重點" in system
        assert "週課表" in system

    def test_returns_plan_text(self):
        llm = MockClaudeClient.with_text(_STRENGTH_PLAN_BODY)
        gcs = MockGCSClient()
        md = plan_generator.generate_plan(
            profile=_STRENGTH_PROFILE,
            user_id="U_RET",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        # Round-trip: returned text equals what's persisted
        stored = gcs.bucket("capybara-profiles").all_blobs()["U_RET/training_plan.md"]
        assert md == stored


class TestExtractWeekFocus:
    def test_extracts_本週重點_line(self):
        md = _STRENGTH_PLAN_BODY
        focus = plan_generator.extract_week_focus(md)
        assert focus == "先從複合動作打基礎，動作先於重量。"

    def test_returns_empty_when_missing(self):
        assert plan_generator.extract_week_focus("no focus section") == ""
