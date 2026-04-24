"""Tests for tools/profile_generator.py — extract fields from a transcript, write markdown."""

from __future__ import annotations

import json
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
from tools import profile_generator  # noqa: E402


_COMPLETE_PROFILE = {
    "goal": "完成半程鐵人",
    "current_level": "規律訓練 6 個月",
    "available_days": 4,
    "session_duration": 60,
    "equipment": "健身房 + 自行車",
    "injury_history": "2024 年左膝內側半月板輕微磨損，已康復",
    "race_target": "2026 秋季台東 113",
    "weight_height": "體重 72kg、身高 175cm",
}


def _mock_with_json(payload: dict, fenced: bool = False) -> MockClaudeClient:
    raw = json.dumps(payload, ensure_ascii=False)
    if fenced:
        raw = f"```json\n{raw}\n```"
    return MockClaudeClient.with_text(raw)


class TestGenerateProfile:
    def test_happy_path_extracts_and_writes(self):
        llm = _mock_with_json(_COMPLETE_PROFILE)
        gcs = MockGCSClient()

        data = profile_generator.generate_profile(
            transcript="（新手想完賽 113，每週四練）",
            user_id="U_HAPPY",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )

        assert data["goal"] == "完成半程鐵人"
        assert data["available_days"] == 4
        # Markdown written to correct GCS path
        blobs = gcs.bucket("capybara-profiles").all_blobs()
        assert "U_HAPPY/athlete_profile.md" in blobs

    def test_markdown_contains_all_fields(self):
        llm = _mock_with_json(_COMPLETE_PROFILE)
        gcs = MockGCSClient()

        profile_generator.generate_profile(
            transcript="x",
            user_id="U_MD",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )

        md = gcs.bucket("capybara-profiles").all_blobs()["U_MD/athlete_profile.md"]
        assert "完成半程鐵人" in md
        assert "規律訓練 6 個月" in md
        assert "健身房 + 自行車" in md
        assert "2026 秋季台東 113" in md
        assert "2024 年左膝" in md

    def test_markdown_fence_is_stripped(self):
        llm = _mock_with_json(_COMPLETE_PROFILE, fenced=True)
        gcs = MockGCSClient()

        data = profile_generator.generate_profile(
            transcript="x",
            user_id="U_FENCE",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        assert data["goal"] == "完成半程鐵人"

    def test_missing_critical_field_logs_warning(self, caplog):
        incomplete = dict(_COMPLETE_PROFILE)
        del incomplete["goal"]
        llm = _mock_with_json(incomplete)
        gcs = MockGCSClient()

        with caplog.at_level("WARNING"):
            profile_generator.generate_profile(
                transcript="x",
                user_id="U_INC",
                client=llm,
                gcs_client=gcs,
                bucket="capybara-profiles",
            )
        assert any("goal" in rec.message.lower() for rec in caplog.records)

    def test_invalid_json_raises(self):
        llm = MockClaudeClient.with_text("這不是 JSON 欸")
        gcs = MockGCSClient()
        with pytest.raises(ValueError):
            profile_generator.generate_profile(
                transcript="x",
                user_id="U_BAD",
                client=llm,
                gcs_client=gcs,
                bucket="capybara-profiles",
            )

    def test_llm_receives_transcript_in_user_message(self):
        llm = _mock_with_json(_COMPLETE_PROFILE)
        gcs = MockGCSClient()
        profile_generator.generate_profile(
            transcript="教練，我想完賽半鐵。每週可以練四次。",
            user_id="U_PROMPT",
            client=llm,
            gcs_client=gcs,
            bucket="capybara-profiles",
        )
        call = llm.messages.calls[0]
        messages = call.get("messages", [])
        assert any("每週可以練四次" in m.get("content", "") for m in messages)


class TestRenderProfileMd:
    def test_renders_full_template(self):
        md = profile_generator.render_profile_md(_COMPLETE_PROFILE)
        # YAML-ish front section the coach will read back
        assert "goal:" in md
        assert "current_level:" in md
        assert "available_days:" in md
        # Raw values appear
        assert "完成半程鐵人" in md
        assert "72kg" in md

    def test_renders_with_missing_optional_field(self):
        partial = dict(_COMPLETE_PROFILE)
        del partial["weight_height"]
        md = profile_generator.render_profile_md(partial)
        # Other fields still present
        assert "完成半程鐵人" in md
        # Missing optional field either omitted or rendered as empty — not crashing
        assert "weight_height" not in md or "weight_height:" in md  # tolerant
