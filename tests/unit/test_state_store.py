"""Tests for tools/state_store.py — GCS-backed onboarding state persistence."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mocks.gcs_mock import MockGCSClient  # noqa: E402
from tools import state_store  # noqa: E402


_HISTORY = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "想練什麼？"},
    {"role": "user", "content": "增肌"},
]


class TestGCSBackend:
    def test_save_then_load_roundtrip(self):
        client = MockGCSClient()
        state_store.save_onboarding_state(
            "U_A", _HISTORY, gcs_client=client, bucket="b"
        )
        loaded = state_store.load_onboarding_state("U_A", gcs_client=client, bucket="b")
        assert loaded is not None
        assert loaded["history"] == _HISTORY
        assert "saved_at" in loaded

    def test_load_returns_none_when_missing(self):
        client = MockGCSClient()
        assert state_store.load_onboarding_state("U_NEW", gcs_client=client, bucket="b") is None

    def test_clear_removes_state(self):
        client = MockGCSClient()
        state_store.save_onboarding_state("U_C", _HISTORY, gcs_client=client, bucket="b")
        assert state_store.load_onboarding_state("U_C", gcs_client=client, bucket="b") is not None
        state_store.clear_onboarding_state("U_C", gcs_client=client, bucket="b")
        assert state_store.load_onboarding_state("U_C", gcs_client=client, bucket="b") is None

    def test_clear_is_idempotent(self):
        client = MockGCSClient()
        # Clearing a never-saved user must not raise
        state_store.clear_onboarding_state("U_NEVER", gcs_client=client, bucket="b")

    def test_path_convention_user_id_slash_state_json(self):
        client = MockGCSClient()
        state_store.save_onboarding_state("U_PATH", _HISTORY, gcs_client=client, bucket="b")
        assert "U_PATH/onboarding_state.json" in client.bucket("b").all_blobs()

    def test_overwrites_existing_state(self):
        client = MockGCSClient()
        state_store.save_onboarding_state("U_O", _HISTORY[:1], gcs_client=client, bucket="b")
        state_store.save_onboarding_state("U_O", _HISTORY, gcs_client=client, bucket="b")
        loaded = state_store.load_onboarding_state("U_O", gcs_client=client, bucket="b")
        assert loaded["history"] == _HISTORY


class TestLocalFallback:
    def test_save_load_clear_local(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GCS_PROFILES_BUCKET", raising=False)
        monkeypatch.setattr(state_store, "_LOCAL_ROOT", tmp_path)

        assert state_store.load_onboarding_state("U_X") is None
        state_store.save_onboarding_state("U_X", _HISTORY)
        loaded = state_store.load_onboarding_state("U_X")
        assert loaded["history"] == _HISTORY
        assert (tmp_path / "U_X" / "onboarding_state.json").exists()
        state_store.clear_onboarding_state("U_X")
        assert state_store.load_onboarding_state("U_X") is None

    def test_clear_missing_file_no_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GCS_PROFILES_BUCKET", raising=False)
        monkeypatch.setattr(state_store, "_LOCAL_ROOT", tmp_path)
        state_store.clear_onboarding_state("U_NEVER")  # must not raise


class TestEnvBucketResolution:
    def test_uses_env_bucket_when_not_passed(self, monkeypatch):
        client = MockGCSClient()
        monkeypatch.setenv("GCS_PROFILES_BUCKET", "env-bucket")
        state_store.save_onboarding_state("U_E", _HISTORY, gcs_client=client)
        assert "U_E/onboarding_state.json" in client.bucket("env-bucket").all_blobs()
