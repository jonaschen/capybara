"""Tests for tools/daily_push.py — morning/evening scheduled push content."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mocks.claude_mock import MockClaudeClient  # noqa: E402
from mocks.gcs_mock import MockGCSClient  # noqa: E402
from mocks.line_mock import MockLINEAPI  # noqa: E402
from tools import daily_push  # noqa: E402


_PROFILE = {
    "goal": "增肌",
    "current_level": "完全新手",
    "available_days": 3,
    "session_duration": 45,
    "equipment": "健身房",
    "injury_history": "無",
    "race_target": "無",
}

_PLAN = """# 訓練計畫 — 增肌
Generated: 2026-04-24
Valid until: 2026-05-22
Last adjusted: 2026-04-24

## 本週重點
先從複合動作打基礎，動作先於重量。

## 週課表
| 星期 | 訓練內容 | 時長 | 強度 |
|---|---|---|---|
| 一 | 下肢 深蹲 + 硬舉 | 45 分 | 中 |
| 三 | 上肢推 臥推 + 肩推 | 45 分 | 中 |
| 五 | 上肢拉 引體 + 划船 | 45 分 | 中 |

## 調整記錄
（尚無調整）
"""


def _seed_user(gcs, user_id: str, profile: str = "p", plan: str | None = _PLAN, bucket: str = "b"):
    b = gcs.bucket(bucket)
    b.seed(f"{user_id}/athlete_profile.md", profile)
    if plan is not None:
        b.seed(f"{user_id}/training_plan.md", plan)


class TestGenerateMorningPush:
    def test_returns_llm_text(self):
        client = MockClaudeClient.with_text("今天：Zone 2 跑步 35 分鐘。\n💡 前 5 分鐘慢走熱身。")
        out = daily_push.generate_morning_push(profile=_PROFILE, plan_md=_PLAN, client=client)
        assert "Zone 2" in out
        assert "💡" in out

    def test_llm_sees_plan_and_profile(self):
        client = MockClaudeClient.with_text("x")
        daily_push.generate_morning_push(profile=_PROFILE, plan_md=_PLAN, client=client)
        call = client.messages.calls[0]
        user_content = call["messages"][0]["content"]
        assert "本週重點" in user_content  # plan included
        assert "增肌" in user_content       # profile included

    def test_morning_system_prompt_mentions_tip(self):
        client = MockClaudeClient.with_text("x")
        daily_push.generate_morning_push(profile=_PROFILE, plan_md=_PLAN, client=client)
        system = client.messages.calls[0]["system"]
        assert "💡" in system


class TestGenerateEveningPush:
    def test_returns_short_companionship_line(self):
        client = MockClaudeClient.with_text("今天不管練了什麼都沒關係。明天還在。🐾")
        out = daily_push.generate_evening_push(profile=_PROFILE, plan_md=_PLAN, client=client)
        assert out
        assert "明天" in out

    def test_evening_system_prompt_forbids_demands(self):
        client = MockClaudeClient.with_text("x")
        daily_push.generate_evening_push(profile=_PROFILE, plan_md=_PLAN, client=client)
        system = client.messages.calls[0]["system"]
        assert "不要求" in system or "陪伴" in system


class TestSendDailyPush:
    def test_pushes_to_all_onboarded_users(self):
        gcs = MockGCSClient()
        _seed_user(gcs, "U1", bucket="bucket")
        _seed_user(gcs, "U2", bucket="bucket")
        llm = MockClaudeClient.with_text("今天：跑步。\n💡 慢一點。")
        line = MockLINEAPI()

        result = daily_push.send_daily_push(
            push_type="morning",
            line_api=line,
            llm_client=llm,
            gcs_client=gcs,
            bucket="bucket",
        )
        assert result["pushed"] == 2
        assert result["failed"] == 0
        assert len(line.sent) == 2
        assert {msg["to"] for msg in line.sent} == {"U1", "U2"}

    def test_skips_user_without_plan(self):
        gcs = MockGCSClient()
        _seed_user(gcs, "U_ONBOARDED", bucket="bucket")
        _seed_user(gcs, "U_NO_PLAN", plan=None, bucket="bucket")
        llm = MockClaudeClient.with_text("x")
        line = MockLINEAPI()

        result = daily_push.send_daily_push(
            push_type="morning",
            line_api=line,
            llm_client=llm,
            gcs_client=gcs,
            bucket="bucket",
        )
        assert result["pushed"] == 1
        assert {msg["to"] for msg in line.sent} == {"U_ONBOARDED"}

    def test_invalid_push_type_raises(self):
        gcs = MockGCSClient()
        llm = MockClaudeClient.with_text("x")
        line = MockLINEAPI()
        with pytest.raises(ValueError):
            daily_push.send_daily_push(
                push_type="afternoon",
                line_api=line,
                llm_client=llm,
                gcs_client=gcs,
                bucket="bucket",
            )

    def test_line_failure_counted(self):
        gcs = MockGCSClient()
        _seed_user(gcs, "U_OK", bucket="bucket")
        _seed_user(gcs, "U_FAIL", bucket="bucket")
        llm = MockClaudeClient.with_text("x")
        line = MockLINEAPI(fail_users={"U_FAIL"})

        result = daily_push.send_daily_push(
            push_type="evening",
            line_api=line,
            llm_client=llm,
            gcs_client=gcs,
            bucket="bucket",
        )
        assert result["pushed"] == 1
        assert result["failed"] == 1

    def test_empty_user_set_returns_zero(self):
        gcs = MockGCSClient()
        llm = MockClaudeClient.with_text("x")
        line = MockLINEAPI()
        result = daily_push.send_daily_push(
            push_type="morning",
            line_api=line,
            llm_client=llm,
            gcs_client=gcs,
            bucket="bucket",
        )
        assert result["pushed"] == 0
        assert result["failed"] == 0
        assert result["results"] == []
