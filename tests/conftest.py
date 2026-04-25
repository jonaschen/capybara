"""
tests/conftest.py

Runs before any test module imports — keeps tests fast and hermetic by
forcing local-fallback paths and stubbing credentials.

Without this, tools.line_webhook's load_dotenv() pulls real values from
.env (production GCS bucket, LINE secrets) at import time, and any test
that exercises the webhook ends up making real GCS calls (~2s per call
for auth + network). Setting GCS_PROFILES_BUCKET="" up front routes
gcs_profile and state_store to /tmp/capybara_profiles/ instead.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Force local-fallback for GCS-backed modules. Empty string, not delete:
# load_dotenv(override=False) only fills missing keys, so "" stays.
os.environ["GCS_PROFILES_BUCKET"] = ""

# Safe placeholders for env vars read at import time.
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("OWNER_LINE_USER_ID", "U_OWNER")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")
os.environ.setdefault("DAILY_PUSH_SECRET", "test-daily-push-secret")

# Make `tools` and `mocks` importable from any test module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
