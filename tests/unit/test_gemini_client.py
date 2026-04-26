"""Tests for tools/gemini_client.py — multimodal content block support.

The wrapper accepts Anthropic-style content blocks. String content keeps the
existing single-text-part shape; list content (text + image blocks) maps to
Gemini's multi-part `parts` list with `inline_data` for images.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gemini_client import GeminiClient  # noqa: E402


class _Captured:
    """Captures generate_content args; emits a minimal stub response."""

    def __init__(self):
        self.calls: list[dict] = []
        self.models = self  # so `client.models.generate_content` works

    def generate_content(self, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})

        class _Resp:
            text = "ok"
            usage_metadata = None

        return _Resp()


def _build(captured: _Captured):
    """Returns a GeminiClient where the underlying genai client is captured."""
    with patch("tools.gemini_client._create_genai_client", return_value=captured):
        client = GeminiClient(api_key="test_key")
    return client


class TestStringContentBackCompat:
    """String content (the existing call shape) must keep working unchanged."""

    def test_single_text_part(self):
        cap = _Captured()
        client = _build(cap)
        with patch("tools.gemini_client._create_genai_client", return_value=cap):
            client.messages.create(messages=[{"role": "user", "content": "hello"}])
        contents = cap.calls[0]["contents"]
        assert contents == [{"role": "user", "parts": [{"text": "hello"}]}]

    def test_assistant_role_mapped_to_model(self):
        cap = _Captured()
        client = _build(cap)
        with patch("tools.gemini_client._create_genai_client", return_value=cap):
            client.messages.create(messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hey"},
            ])
        contents = cap.calls[0]["contents"]
        assert contents[1]["role"] == "model"


class TestMultimodalContentBlocks:
    """List-form content (Anthropic-style blocks) must produce Gemini parts
    with text + inline_data entries."""

    def test_text_block_only(self):
        cap = _Captured()
        client = _build(cap)
        with patch("tools.gemini_client._create_genai_client", return_value=cap):
            client.messages.create(messages=[{
                "role": "user",
                "content": [{"type": "text", "text": "describe"}],
            }])
        parts = cap.calls[0]["contents"][0]["parts"]
        assert parts == [{"text": "describe"}]

    def test_image_block_base64_decodes_to_bytes(self):
        """Anthropic-style base64 source decodes to bytes for Gemini SDK."""
        import base64
        raw = b"\x89PNG\r\n\x1a\nfake"
        b64 = base64.b64encode(raw).decode()

        cap = _Captured()
        client = _build(cap)
        with patch("tools.gemini_client._create_genai_client", return_value=cap):
            client.messages.create(messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "what's this?"},
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png", "data": b64,
                    }},
                ],
            }])
        parts = cap.calls[0]["contents"][0]["parts"]
        assert {"text": "what's this?"} in parts
        inline = next(p for p in parts if "inline_data" in p)
        assert inline["inline_data"]["mime_type"] == "image/png"
        assert inline["inline_data"]["data"] == raw  # bytes

    def test_image_block_raw_bytes_passthrough(self):
        """Passing bytes directly (skipping base64 round-trip) also works."""
        raw = b"\xff\xd8\xff\xe0fake jpeg"
        cap = _Captured()
        client = _build(cap)
        with patch("tools.gemini_client._create_genai_client", return_value=cap):
            client.messages.create(messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "interpret"},
                    {"type": "image", "source": {
                        "type": "bytes", "media_type": "image/jpeg", "data": raw,
                    }},
                ],
            }])
        parts = cap.calls[0]["contents"][0]["parts"]
        inline = next(p for p in parts if "inline_data" in p)
        assert inline["inline_data"]["mime_type"] == "image/jpeg"
        assert inline["inline_data"]["data"] == raw

    def test_image_default_mime_when_missing(self):
        cap = _Captured()
        client = _build(cap)
        with patch("tools.gemini_client._create_genai_client", return_value=cap):
            client.messages.create(messages=[{
                "role": "user",
                "content": [{"type": "image", "source": {
                    "type": "bytes", "data": b"x",
                }}],
            }])
        parts = cap.calls[0]["contents"][0]["parts"]
        inline = next(p for p in parts if "inline_data" in p)
        assert inline["inline_data"]["mime_type"] == "image/jpeg"


class TestSystemPromptAndConfig:
    def test_system_passed_via_config(self):
        cap = _Captured()
        client = _build(cap)
        with patch("tools.gemini_client._create_genai_client", return_value=cap):
            client.messages.create(
                system="you are a coach",
                messages=[{"role": "user", "content": "hi"}],
            )
        config = cap.calls[0]["config"]
        # config is a GenerateContentConfig instance with system_instruction
        assert getattr(config, "system_instruction", None) == "you are a coach"
