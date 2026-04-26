"""Tests for tools/image_reply.py — multimodal training-screenshot interpretation."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")
os.environ.setdefault("GEMINI_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mocks.gemini_mock import MockGeminiClient  # noqa: E402
from tools.image_reply import analyze_training_image, build_image_prompt  # noqa: E402
from tools.voice import CAPYBARA_VOICE  # noqa: E402


_FAKE_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes" * 50  # ~850 bytes
_PROFILE = "目標：完成台東 226\n每週訓練：4 天\n體重：68 kg"


# ─── Prompt construction ──────────────────────────────────────────────────────

class TestBuildImagePrompt:
    def test_includes_athlete_profile(self):
        prompt = build_image_prompt(_PROFILE)
        assert "台東 226" in prompt
        assert "每週訓練：4 天" in prompt

    def test_empty_profile_uses_fallback_text(self):
        prompt = build_image_prompt("")
        assert "尚無訓練檔案" in prompt

    def test_embeds_capybara_voice(self):
        prompt = build_image_prompt(_PROFILE)
        assert CAPYBARA_VOICE in prompt

    def test_mentions_sentinel(self):
        """LLM must know to emit NOT_TRAINING_DATA for non-training images."""
        prompt = build_image_prompt(_PROFILE)
        assert "NOT_TRAINING_DATA" in prompt

    def test_word_limit_constraint(self):
        prompt = build_image_prompt(_PROFILE)
        assert "150 字" in prompt

    def test_requests_structured_summary_block(self):
        """Phase B-lite: webhook needs <summary>...</summary> for chat history."""
        prompt = build_image_prompt(_PROFILE)
        assert "<summary>" in prompt
        assert "</summary>" in prompt
        assert "[訓練截圖：" in prompt


# ─── analyze_training_image ───────────────────────────────────────────────────

_TRAINING_REPLY = (
    "<summary>[訓練截圖：跑步, 10km, 配速趨勢：後半掉速]</summary>\n"
    "卡皮看到你今天跑了 10K，後半段配速掉了一些。"
    "結合你想完成台東 226 的目標，有氧基礎還可以再厚一點。🐾"
)


class TestAnalyzeTrainingImage:
    def test_returns_text_for_training_image(self):
        client = MockGeminiClient.with_text(_TRAINING_REPLY)
        reply, summary, debug = analyze_training_image(
            _FAKE_BYTES, "image/png", _PROFILE, client=client,
        )
        assert reply is not None
        assert "台東" in reply
        # Summary stripped from user-facing reply
        assert "<summary>" not in reply
        assert summary == "[訓練截圖：跑步, 10km, 配速趨勢：後半掉速]"
        assert debug["error"] is None
        assert debug["size_kb"] > 0

    def test_returns_none_on_sentinel(self):
        client = MockGeminiClient.with_text(
            "<summary>[傳了一張非訓練數據圖片]</summary>NOT_TRAINING_DATA"
        )
        reply, summary, debug = analyze_training_image(
            _FAKE_BYTES, "image/jpeg", _PROFILE, client=client,
        )
        assert reply is None
        assert "非訓練" in summary
        assert debug["error"] is None

    def test_sentinel_substring_also_treated_as_non_training(self):
        """LLM may pad sentinel with whitespace or extra punctuation."""
        client = MockGeminiClient.with_text(
            "<summary>[傳了一張非訓練數據圖片]</summary>\nNOT_TRAINING_DATA  "
        )
        reply, summary, _ = analyze_training_image(
            _FAKE_BYTES, "image/jpeg", _PROFILE, client=client,
        )
        assert reply is None
        assert "非訓練" in summary

    def test_sentinel_without_summary_tag_falls_back(self):
        """LLM forgot the tag but still emitted the sentinel — handle gracefully."""
        client = MockGeminiClient.with_text("NOT_TRAINING_DATA")
        reply, summary, _ = analyze_training_image(
            _FAKE_BYTES, "image/jpeg", _PROFILE, client=client,
        )
        assert reply is None
        # Generic non-training marker
        assert "非訓練" in summary

    def test_returns_none_on_llm_exception(self):
        """LLM failure must NOT raise — webhook needs to send a friendly fallback."""
        client = MockGeminiClient.with_exception(RuntimeError("API blew up"))
        reply, summary, debug = analyze_training_image(
            _FAKE_BYTES, "image/jpeg", _PROFILE, client=client,
        )
        assert reply is None
        assert "API blew up" in debug["error"]
        # History gets a failure marker so cold-start chain isn't broken
        assert "讀取失敗" in summary or "處理失敗" in summary

    def test_missing_summary_tag_uses_generic_marker(self):
        """LLM gave a real reply but forgot the tag — still extract a usable summary."""
        client = MockGeminiClient.with_text("看起來你跑了 10K，配速很穩。🐾")
        reply, summary, _ = analyze_training_image(
            _FAKE_BYTES, "image/jpeg", _PROFILE, client=client,
        )
        assert reply == "看起來你跑了 10K，配速很穩。🐾"
        assert summary == "[傳來訓練數據截圖]"

    def test_passes_image_bytes_in_user_message(self):
        client = MockGeminiClient.with_text(_TRAINING_REPLY)
        analyze_training_image(_FAKE_BYTES, "image/png", _PROFILE, client=client)
        call = client.messages.calls[0]
        content = call["messages"][0]["content"]
        assert isinstance(content, list)
        image_block = next(b for b in content if b.get("type") == "image")
        assert image_block["source"]["data"] == _FAKE_BYTES
        assert image_block["source"]["media_type"] == "image/png"

    def test_system_prompt_carries_profile(self):
        client = MockGeminiClient.with_text(_TRAINING_REPLY)
        analyze_training_image(_FAKE_BYTES, "image/jpeg", _PROFILE, client=client)
        system = client.messages.calls[0]["system"]
        assert "台東 226" in system

    def test_debug_contains_token_counts(self):
        client = MockGeminiClient.with_text(_TRAINING_REPLY)
        _, _, debug = analyze_training_image(
            _FAKE_BYTES, "image/jpeg", _PROFILE, client=client,
        )
        assert debug["in_tok"] == 100
        assert debug["out_tok"] == 50

    def test_debug_size_kb_rounds_to_one_decimal(self):
        client = MockGeminiClient.with_text(_TRAINING_REPLY)
        _, _, debug = analyze_training_image(
            b"x" * 2048, "image/jpeg", _PROFILE, client=client,
        )
        assert debug["size_kb"] == 2.0
