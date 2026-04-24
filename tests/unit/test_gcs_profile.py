"""Tests for tools/gcs_profile.py — GCS/local-disk per-user profile storage."""

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
from tools import gcs_profile  # noqa: E402


class TestGCSBackend:
    def test_exists_returns_false_for_missing_profile(self):
        client = MockGCSClient()
        assert gcs_profile.profile_exists("U_NEW", client=client, bucket="capybara-profiles") is False

    def test_write_then_read_roundtrip(self):
        client = MockGCSClient()
        gcs_profile.write_profile(
            "U_A",
            "# Profile\ngoal: 增肌\n",
            client=client,
            bucket="capybara-profiles",
        )
        assert gcs_profile.profile_exists("U_A", client=client, bucket="capybara-profiles") is True
        content = gcs_profile.read_profile("U_A", client=client, bucket="capybara-profiles")
        assert "goal: 增肌" in content

    def test_path_convention_user_id_slash_filename(self):
        client = MockGCSClient()
        gcs_profile.write_profile(
            "U_B",
            "hello",
            client=client,
            bucket="capybara-profiles",
        )
        bucket = client.bucket("capybara-profiles")
        assert "U_B/athlete_profile.md" in bucket.all_blobs()

    def test_custom_filename(self):
        client = MockGCSClient()
        gcs_profile.write_profile(
            "U_C",
            "plan body",
            filename="training_plan.md",
            client=client,
            bucket="capybara-profiles",
        )
        bucket = client.bucket("capybara-profiles")
        assert "U_C/training_plan.md" in bucket.all_blobs()
        assert gcs_profile.read_profile(
            "U_C", filename="training_plan.md", client=client, bucket="capybara-profiles"
        ) == "plan body"

    def test_read_missing_raises(self):
        client = MockGCSClient()
        with pytest.raises(FileNotFoundError):
            gcs_profile.read_profile("U_MISSING", client=client, bucket="capybara-profiles")


class TestLocalFallback:
    def test_exists_and_write_fall_back_to_local_when_no_bucket(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GCS_PROFILES_BUCKET", raising=False)
        monkeypatch.setattr(gcs_profile, "_LOCAL_ROOT", tmp_path)

        assert gcs_profile.profile_exists("U_X") is False
        gcs_profile.write_profile("U_X", "local content\n")
        assert gcs_profile.profile_exists("U_X") is True
        assert gcs_profile.read_profile("U_X") == "local content\n"

        # Disk layout matches GCS: {root}/{user_id}/athlete_profile.md
        assert (tmp_path / "U_X" / "athlete_profile.md").exists()

    def test_local_fallback_read_missing_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GCS_PROFILES_BUCKET", raising=False)
        monkeypatch.setattr(gcs_profile, "_LOCAL_ROOT", tmp_path)
        with pytest.raises(FileNotFoundError):
            gcs_profile.read_profile("U_Y")


class TestListUserIds:
    def test_gcs_lists_top_level_prefixes(self):
        client = MockGCSClient()
        gcs_profile.write_profile("U_A", "x", client=client, bucket="b")
        gcs_profile.write_profile("U_B", "y", client=client, bucket="b")
        gcs_profile.write_profile("U_A", "z", filename="training_plan.md", client=client, bucket="b")
        assert gcs_profile.list_user_ids(client=client, bucket="b") == ["U_A", "U_B"]

    def test_gcs_returns_empty_when_bucket_empty(self):
        client = MockGCSClient()
        assert gcs_profile.list_user_ids(client=client, bucket="empty") == []

    def test_local_lists_directory_names(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GCS_PROFILES_BUCKET", raising=False)
        monkeypatch.setattr(gcs_profile, "_LOCAL_ROOT", tmp_path)
        gcs_profile.write_profile("U_X", "x")
        gcs_profile.write_profile("U_Y", "y")
        assert gcs_profile.list_user_ids() == ["U_X", "U_Y"]

    def test_local_returns_empty_when_root_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GCS_PROFILES_BUCKET", raising=False)
        monkeypatch.setattr(gcs_profile, "_LOCAL_ROOT", tmp_path / "does-not-exist")
        assert gcs_profile.list_user_ids() == []


class TestEnvBucketResolution:
    def test_bucket_resolves_from_env_when_not_passed(self, monkeypatch):
        client = MockGCSClient()
        monkeypatch.setenv("GCS_PROFILES_BUCKET", "env-bucket")
        gcs_profile.write_profile("U_E", "env-based", client=client)
        assert "U_E/athlete_profile.md" in client.bucket("env-bucket").all_blobs()
