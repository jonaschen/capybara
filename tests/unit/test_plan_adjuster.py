"""Tests for tools/plan_adjuster.py — in-place training plan adjustment."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mocks.claude_mock import MockClaudeClient  # noqa: E402
from mocks.gcs_mock import MockGCSClient  # noqa: E402
from tools import plan_adjuster  # noqa: E402


_EXISTING_PLAN = """# 訓練計畫 — 增肌
Generated: 2026-03-01
Valid until: 2026-03-29
Last adjusted: 2026-03-01

## 本週重點
先從複合動作打基礎。

## 週課表
| 星期 | 訓練內容 | 時長 | 強度 |
|---|---|---|---|
| 一 | 下肢 深蹲 + 硬舉 | 45 分 | 中 |
| 三 | 上肢推 臥推 | 45 分 | 中 |
| 五 | 上肢拉 引體 | 45 分 | 中 |

## 調整記錄
（尚無調整）
"""


_NEW_BODY = """## 本週重點
避開下蹲動作一週，保留上肢訓練。

## 週課表
| 星期 | 訓練內容 | 時長 | 強度 |
|---|---|---|---|
| 一 | 上肢推 臥推 + 肩推 | 45 分 | 中 |
| 三 | 上肢拉 引體 + 划船 | 45 分 | 中 |
| 五 | 腿推機（避開深蹲） | 30 分 | 低 |

## 調整記錄
（程式會補上新條目）
"""


def _seed_plan(gcs: MockGCSClient, user_id: str, plan_md: str = _EXISTING_PLAN, bucket: str = "b") -> None:
    gcs.bucket(bucket).seed(f"{user_id}/training_plan.md", plan_md)


class TestAdjustPlan:
    def test_writes_updated_plan_with_same_generated_date(self):
        gcs = MockGCSClient()
        _seed_plan(gcs, "U1")
        llm = MockClaudeClient.with_text(_NEW_BODY)

        updated = plan_adjuster.adjust_plan(
            user_id="U1",
            reason="膝蓋不適，避開深蹲",
            client=llm,
            gcs_client=gcs,
            bucket="b",
        )

        assert "Generated: 2026-03-01" in updated  # preserved from existing
        assert f"Last adjusted: {date.today().isoformat()}" in updated

    def test_adds_dated_adjustment_log_entry(self):
        gcs = MockGCSClient()
        _seed_plan(gcs, "U2")
        llm = MockClaudeClient.with_text(_NEW_BODY)

        updated = plan_adjuster.adjust_plan(
            user_id="U2",
            reason="膝蓋不適，避開深蹲",
            client=llm,
            gcs_client=gcs,
            bucket="b",
        )

        today = date.today().isoformat()
        assert f"- {today}: 膝蓋不適，避開深蹲" in updated
        # Log entry appears inside the 調整記錄 section
        adjust_idx = updated.find("## 調整記錄")
        reason_idx = updated.find("膝蓋不適")
        assert adjust_idx != -1 and reason_idx > adjust_idx

    def test_replaces_body_with_llm_output(self):
        gcs = MockGCSClient()
        _seed_plan(gcs, "U3")
        llm = MockClaudeClient.with_text(_NEW_BODY)
        plan_adjuster.adjust_plan(
            user_id="U3",
            reason="膝蓋不適",
            client=llm,
            gcs_client=gcs,
            bucket="b",
        )
        stored = gcs.bucket("b").all_blobs()["U3/training_plan.md"]
        assert "腿推機" in stored
        assert "避開下蹲動作一週" in stored
        # Old body gone
        assert "先從複合動作打基礎" not in stored

    def test_missing_plan_raises(self):
        gcs = MockGCSClient()
        llm = MockClaudeClient.with_text(_NEW_BODY)
        with pytest.raises(FileNotFoundError):
            plan_adjuster.adjust_plan(
                user_id="U_MISSING",
                reason="x",
                client=llm,
                gcs_client=gcs,
                bucket="b",
            )

    def test_llm_receives_current_plan_and_reason(self):
        gcs = MockGCSClient()
        _seed_plan(gcs, "U4")
        llm = MockClaudeClient.with_text(_NEW_BODY)
        plan_adjuster.adjust_plan(
            user_id="U4",
            reason="太累了",
            client=llm,
            gcs_client=gcs,
            bucket="b",
        )
        user_content = llm.messages.calls[0]["messages"][0]["content"]
        assert "先從複合動作打基礎" in user_content  # existing plan included
        assert "太累了" in user_content                 # reason included

    def test_multiple_adjustments_accumulate(self):
        gcs = MockGCSClient()
        _seed_plan(gcs, "U5")
        llm = MockClaudeClient.with_text(_NEW_BODY)

        plan_adjuster.adjust_plan(user_id="U5", reason="reason A", client=llm, gcs_client=gcs, bucket="b")
        plan_adjuster.adjust_plan(user_id="U5", reason="reason B", client=llm, gcs_client=gcs, bucket="b")

        final = gcs.bucket("b").all_blobs()["U5/training_plan.md"]
        assert "reason A" in final
        assert "reason B" in final
