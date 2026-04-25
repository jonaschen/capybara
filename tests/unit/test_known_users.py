"""Tests for tools/known_users.py — per-user metadata + invitation logic."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mocks.gcs_mock import MockGCSClient  # noqa: E402
from mocks.line_mock import MockLINEAPI  # noqa: E402
from tools import known_users  # noqa: E402


def _seed_profile(gcs: MockGCSClient, user_id: str, bucket: str = "b"):
    """Mark a user as already onboarded by writing athlete_profile.md."""
    gcs.bucket(bucket).seed(f"{user_id}/athlete_profile.md", "# profile\n")


# ─── record_user_seen / load_known_user ────────────────────────────────────


class TestRecordUserSeen:
    def test_first_record_writes_blob(self):
        gcs = MockGCSClient()
        known_users.record_user_seen("U1", gcs_client=gcs, bucket="b")
        assert "U1/known_user.json" in gcs.bucket("b").all_blobs()

    def test_first_record_initializes_metadata(self):
        gcs = MockGCSClient()
        known_users.record_user_seen("U2", gcs_client=gcs, bucket="b")
        meta = known_users.load_known_user("U2", gcs_client=gcs, bucket="b")
        assert meta["user_id"] == "U2"
        assert meta["first_seen"]
        assert meta["last_seen"] == meta["first_seen"]
        assert meta["last_invited"] is None
        assert meta["invite_count"] == 0

    def test_subsequent_record_updates_last_seen_only(self):
        gcs = MockGCSClient()
        known_users.record_user_seen("U3", gcs_client=gcs, bucket="b")
        first = known_users.load_known_user("U3", gcs_client=gcs, bucket="b")

        # bump time forward
        known_users.record_user_seen(
            "U3",
            gcs_client=gcs,
            bucket="b",
            now=datetime.fromisoformat(first["first_seen"]) + timedelta(hours=1),
        )
        second = known_users.load_known_user("U3", gcs_client=gcs, bucket="b")
        assert second["first_seen"] == first["first_seen"]
        assert second["last_seen"] != first["last_seen"]
        assert second["invite_count"] == 0  # untouched
        assert second["last_invited"] is None

    def test_load_returns_none_when_missing(self):
        gcs = MockGCSClient()
        assert known_users.load_known_user("U_NEW", gcs_client=gcs, bucket="b") is None


# ─── mark_invited ──────────────────────────────────────────────────────────


class TestMarkInvited:
    def test_bumps_count_and_sets_timestamp(self):
        gcs = MockGCSClient()
        known_users.record_user_seen("U_M", gcs_client=gcs, bucket="b")
        when = datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc)
        known_users.mark_invited("U_M", gcs_client=gcs, bucket="b", now=when)
        meta = known_users.load_known_user("U_M", gcs_client=gcs, bucket="b")
        assert meta["invite_count"] == 1
        assert meta["last_invited"] == when.isoformat()

    def test_multiple_invites_accumulate(self):
        gcs = MockGCSClient()
        known_users.record_user_seen("U_MI", gcs_client=gcs, bucket="b")
        base = datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc)
        for i in range(3):
            known_users.mark_invited(
                "U_MI", gcs_client=gcs, bucket="b", now=base + timedelta(days=i * 7)
            )
        meta = known_users.load_known_user("U_MI", gcs_client=gcs, bucket="b")
        assert meta["invite_count"] == 3


# ─── eligible_for_invite ──────────────────────────────────────────────────


class TestEligibility:
    def _make(self, **overrides) -> dict:
        base = {
            "user_id": "U_X",
            "first_seen": "2026-04-01T00:00:00+00:00",
            "last_seen": "2026-04-01T00:00:00+00:00",
            "last_invited": None,
            "invite_count": 0,
        }
        base.update(overrides)
        return base

    def test_never_invited_is_eligible(self):
        meta = self._make()
        now = datetime(2026, 4, 25, tzinfo=timezone.utc)
        assert known_users.eligible_for_invite(meta, now=now) is True

    def test_invited_within_7d_is_not_eligible(self):
        meta = self._make(last_invited="2026-04-20T11:00:00+00:00", invite_count=1)
        now = datetime(2026, 4, 25, tzinfo=timezone.utc)  # only 5 days later
        assert known_users.eligible_for_invite(meta, now=now) is False

    def test_invited_8_days_ago_is_eligible(self):
        meta = self._make(last_invited="2026-04-15T11:00:00+00:00", invite_count=1)
        now = datetime(2026, 4, 23, 11, 0, tzinfo=timezone.utc)
        assert known_users.eligible_for_invite(meta, now=now) is True

    def test_third_invite_then_done(self):
        meta = self._make(last_invited="2026-04-01T11:00:00+00:00", invite_count=3)
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        assert known_users.eligible_for_invite(meta, now=now) is False


# ─── send_onboarding_invites ──────────────────────────────────────────────


class TestSendInvites:
    def test_pushes_to_eligible_users_only(self):
        gcs = MockGCSClient()
        line = MockLINEAPI()

        # U_INVITE_ME: known but no profile, never invited → should be invited
        known_users.record_user_seen("U_INVITE_ME", gcs_client=gcs, bucket="b")
        # U_HAS_PROFILE: known + already onboarded → must be skipped
        known_users.record_user_seen("U_HAS_PROFILE", gcs_client=gcs, bucket="b")
        _seed_profile(gcs, "U_HAS_PROFILE", bucket="b")
        # U_RECENT: known, no profile, but invited 2 days ago → cooldown skip
        known_users.record_user_seen("U_RECENT", gcs_client=gcs, bucket="b")
        when_recent = datetime(2026, 4, 23, tzinfo=timezone.utc)
        known_users.mark_invited("U_RECENT", gcs_client=gcs, bucket="b", now=when_recent)

        now = datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc)
        result = known_users.send_onboarding_invites(
            line_api=line, gcs_client=gcs, bucket="b", now=now
        )

        assert result["invited"] == 1
        assert {m["to"] for m in line.sent} == {"U_INVITE_ME"}
        # Eligibility flags reported per-user for log review
        statuses = {r["user_id"]: r["status"] for r in result["details"]}
        assert statuses["U_INVITE_ME"] == "invited"
        assert statuses["U_HAS_PROFILE"] == "skip_has_profile"
        assert statuses["U_RECENT"] == "skip_cooldown"

    def test_max_3_invites_then_stops(self):
        gcs = MockGCSClient()
        line = MockLINEAPI()
        known_users.record_user_seen("U_MAXED", gcs_client=gcs, bucket="b")
        # Force invite_count = 3, last_invited > 7 days ago
        for i in range(3):
            when = datetime(2026, 4, 1 + i * 7, tzinfo=timezone.utc)
            known_users.mark_invited("U_MAXED", gcs_client=gcs, bucket="b", now=when)

        now = datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc)
        result = known_users.send_onboarding_invites(
            line_api=line, gcs_client=gcs, bucket="b", now=now
        )
        assert result["invited"] == 0
        assert any(r["user_id"] == "U_MAXED" and r["status"] == "skip_maxed" for r in result["details"])

    def test_invite_text_matches_design(self):
        gcs = MockGCSClient()
        line = MockLINEAPI()
        known_users.record_user_seen("U_TXT", gcs_client=gcs, bucket="b")
        known_users.send_onboarding_invites(
            line_api=line, gcs_client=gcs, bucket="b",
            now=datetime(2026, 4, 25, tzinfo=timezone.utc),
        )
        text = line.sent[0]["text"]
        # Soft/warm outreach tone: bot speaks of itself in third person at
        # the start, soft-particle invitation at the end.
        assert "卡皮教練" in text
        assert "很久沒聽到" in text or "沒聽到你" in text
        assert "隨時" in text and "聊聊" in text
        assert "🐾" in text

    def test_invite_records_mark(self):
        gcs = MockGCSClient()
        line = MockLINEAPI()
        known_users.record_user_seen("U_MK", gcs_client=gcs, bucket="b")
        now = datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc)
        known_users.send_onboarding_invites(
            line_api=line, gcs_client=gcs, bucket="b", now=now
        )
        meta = known_users.load_known_user("U_MK", gcs_client=gcs, bucket="b")
        assert meta["invite_count"] == 1
        assert meta["last_invited"] == now.isoformat()

    def test_line_failure_recorded_but_no_mark(self):
        gcs = MockGCSClient()
        line = MockLINEAPI(fail_users={"U_BLOCKED"})
        known_users.record_user_seen("U_BLOCKED", gcs_client=gcs, bucket="b")
        now = datetime(2026, 4, 25, tzinfo=timezone.utc)
        result = known_users.send_onboarding_invites(
            line_api=line, gcs_client=gcs, bucket="b", now=now
        )
        # Failed delivery: don't bump invite_count so we retry next cycle
        assert result["invited"] == 0
        assert result["failed"] == 1
        meta = known_users.load_known_user("U_BLOCKED", gcs_client=gcs, bucket="b")
        assert meta["invite_count"] == 0
        assert meta["last_invited"] is None
