"""Tests for tools/chat_store.py — GCS-backed IDLE chat history (Phase B-lite)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mocks.gcs_mock import MockGCSClient  # noqa: E402
from tools import chat_store  # noqa: E402


_HISTORY = [
    {"role": "user", "content": "今天可以練什麼"},
    {"role": "assistant", "content": "今天 Zone 2 跑 40 分。"},
    {"role": "user", "content": "好"},
    {"role": "assistant", "content": "練完跟卡皮說感覺。🐾"},
]


class TestGCSBackend:
    def test_save_then_load_roundtrip(self):
        client = MockGCSClient()
        chat_store.save_chat_history("U_A", _HISTORY, gcs_client=client, bucket="b")
        loaded = chat_store.load_chat_history("U_A", gcs_client=client, bucket="b")
        assert loaded == _HISTORY

    def test_load_returns_empty_when_missing(self):
        client = MockGCSClient()
        loaded = chat_store.load_chat_history("U_NONE", gcs_client=client, bucket="b")
        assert loaded == []

    def test_clear_removes_blob(self):
        client = MockGCSClient()
        chat_store.save_chat_history("U_C", _HISTORY, gcs_client=client, bucket="b")
        chat_store.clear_chat_history("U_C", gcs_client=client, bucket="b")
        assert chat_store.load_chat_history("U_C", gcs_client=client, bucket="b") == []

    def test_clear_idempotent_when_missing(self):
        client = MockGCSClient()
        # Should not raise
        chat_store.clear_chat_history("U_GHOST", gcs_client=client, bucket="b")


class TestTrimming:
    def test_save_trims_to_max_messages(self):
        client = MockGCSClient()
        # Build 30 messages (15 rounds) — should trim to 20 (10 rounds).
        long_history = []
        for i in range(15):
            long_history.append({"role": "user", "content": f"u{i}"})
            long_history.append({"role": "assistant", "content": f"a{i}"})
        chat_store.save_chat_history("U_LONG", long_history, gcs_client=client, bucket="b")
        loaded = chat_store.load_chat_history("U_LONG", gcs_client=client, bucket="b")
        assert len(loaded) == chat_store.MAX_MESSAGES
        # Newest preserved
        assert loaded[-1]["content"] == "a14"
        # Oldest dropped
        assert loaded[0]["content"] == "u5"

    def test_under_cap_unchanged(self):
        client = MockGCSClient()
        chat_store.save_chat_history("U_S", _HISTORY, gcs_client=client, bucket="b")
        loaded = chat_store.load_chat_history("U_S", gcs_client=client, bucket="b")
        assert loaded == _HISTORY

    def test_trim_realigns_to_user_first(self):
        """If trimming lands on assistant first (odd boundary), drop one more."""
        client = MockGCSClient()
        # 22 messages — trimming last 20 would put assistant at index 0 only if
        # the original order is user, assistant, user, assistant... Index 22-20=2
        # Index 2 of pattern u,a,u,a,... is "user", so OK. Build a pathological:
        # start with assistant (rare but possible after replay), 21 total → trim to 20
        odd = [{"role": "assistant", "content": "x"}]
        for i in range(20):
            odd.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"})
        # Total 21. _trim keeps last 20 → starts at index 1 ("user", "m0") → fine
        # Force the pathological by making a 22-msg list where last 20 starts on assistant:
        bad = [
            {"role": "user", "content": "u_x"},
            {"role": "user", "content": "u_y"},  # double-user (would be unusual)
        ]
        for i in range(20):
            bad.append({"role": "assistant" if i % 2 == 0 else "user", "content": f"b{i}"})
        # Last 20 starts at index 2 → "assistant" → trim should drop it → 19 messages
        chat_store.save_chat_history("U_ODD", bad, gcs_client=client, bucket="b")
        loaded = chat_store.load_chat_history("U_ODD", gcs_client=client, bucket="b")
        assert loaded[0]["role"] == "user"
        assert len(loaded) == chat_store.MAX_MESSAGES - 1


class TestLocalFallback:
    def test_local_fallback_roundtrip(self, tmp_path, monkeypatch):
        # Force local mode by clearing GCS_PROFILES_BUCKET.
        monkeypatch.setenv("GCS_PROFILES_BUCKET", "")
        monkeypatch.setattr(chat_store, "_LOCAL_ROOT", tmp_path)
        chat_store.save_chat_history("U_LOC", _HISTORY)
        loaded = chat_store.load_chat_history("U_LOC")
        assert loaded == _HISTORY
        chat_store.clear_chat_history("U_LOC")
        assert chat_store.load_chat_history("U_LOC") == []
