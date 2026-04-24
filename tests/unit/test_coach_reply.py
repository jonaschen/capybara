"""Tests for tools/coach_reply.py — domain detection and reply assembly."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure env vars exist BEFORE importing coach_reply (it may read at import time)
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("OWNER_LINE_USER_ID", "U_OWNER")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

# Add repo root to path so tools + mocks resolve
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mocks.claude_mock import MockClaudeClient  # noqa: E402
from tools.coach_reply import (  # noqa: E402
    MANDATORY_INJURY_DISCLAIMER,
    coach_reply,
    detect_domain,
)


# ─── Domain detection ───────────────────────────────────────────────────────

class TestDetectDomain:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("我想完成半程鐵人", "triathlon"),
            ("三鐵 T1 轉換區怎麼練", "triathlon"),
            ("open water 開放水域好可怕", "triathlon"),
            ("增肌怎麼開始", "strength"),
            ("深蹲 1RM 怎麼測", "strength"),
            ("漸進超負荷是什麼", "strength"),
            ("我想減脂", "fat_loss"),
            ("TDEE 怎麼算熱量赤字", "fat_loss"),
            ("體脂 20% 算高嗎", "fat_loss"),
            ("賽前飲食怎麼安排", "nutrition"),
            ("電解質要補多少", "nutrition"),
            ("蛋白質一天吃多少", "nutrition"),
            ("DOMS 延遲性痠痛怎麼處理", "recovery"),
            ("睡眠不足會影響訓練嗎", "recovery"),
            ("泡沫滾筒怎麼用", "recovery"),
            ("膝蓋外側痛", "injury"),
            ("下背拉傷了", "injury"),
            ("肩膀受傷要去復健嗎", "injury"),
        ],
    )
    def test_domain_keywords_match(self, text: str, expected: str):
        assert detect_domain(text) == expected

    def test_fallback_to_general_fitness(self):
        assert detect_domain("你好") == "general_fitness"
        assert detect_domain("今天天氣不錯") == "general_fitness"

    def test_empty_string_falls_back(self):
        assert detect_domain("") == "general_fitness"


# ─── Reply assembly ─────────────────────────────────────────────────────────

class TestCoachReply:
    def test_returns_llm_generated_text(self):
        client = MockClaudeClient.with_text("今天先走 30 分鐘，明天再跑。")
        reply = coach_reply("推薦一個輕鬆的訓練", client=client)
        assert "今天先走 30 分鐘" in reply

    def test_injects_disclaimer_on_injury_domain(self):
        client = MockClaudeClient.with_text("先冰敷休息 48 小時。")
        reply = coach_reply("膝蓋外側痛", client=client)
        assert MANDATORY_INJURY_DISCLAIMER in reply
        # The LLM's actual advice is still present.
        assert "先冰敷休息 48 小時" in reply

    def test_non_injury_domain_has_no_disclaimer(self):
        client = MockClaudeClient.with_text("今天跑 Zone 2 40 分鐘。")
        reply = coach_reply("今天要練什麼", client=client)
        assert MANDATORY_INJURY_DISCLAIMER not in reply

    def test_owner_mode_skips_disclaimer_on_injury(self):
        client = MockClaudeClient.with_text("先 RICE 48h，然後做輕度活動度。")
        reply = coach_reply("膝蓋外側痛", owner=True, client=client)
        assert MANDATORY_INJURY_DISCLAIMER not in reply

    def test_owner_mode_appends_debug_footer(self):
        client = MockClaudeClient.with_text("Zone 2 跑步 40 分")
        reply = coach_reply("今天練什麼", owner=True, client=client)
        # Debug footer format per spec: [🏊 domain: X | tokens: Xin/Xout]
        assert "🏊" in reply
        assert "domain:" in reply
        assert "tokens:" in reply

    def test_non_owner_has_no_debug_footer(self):
        client = MockClaudeClient.with_text("今天跑步")
        reply = coach_reply("今天練什麼", client=client)
        assert "🏊" not in reply
        assert "domain:" not in reply

    def test_llm_receives_user_text_in_messages(self):
        client = MockClaudeClient.with_text("ok")
        coach_reply("增肌怎麼開始", client=client)
        calls = client.messages.calls
        assert len(calls) == 1
        messages = calls[0].get("messages", [])
        assert any(
            "增肌怎麼開始" in (m.get("content") or "")
            for m in messages
            if isinstance(m, dict)
        )

    def test_llm_receives_system_prompt(self):
        client = MockClaudeClient.with_text("ok")
        coach_reply("隨便問", client=client)
        system = client.messages.calls[0].get("system", "")
        assert system  # non-empty system prompt present
