"""
End-to-end Phase 1 verification: POST a LINE webhook event for the owner
asking "增肌怎麼開始", exercise the real webhook handler and real coach_reply,
and verify the reply comes back through LINE with the owner debug footer.

Only the LLM (MockClaudeClient) and LINE SDK (mocked reply_message) are
faked — everything else is the production code path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ["OWNER_LINE_USER_ID"] = "U_OWNER"
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from mocks.claude_mock import MockClaudeClient  # noqa: E402
import tools.line_webhook as webhook_mod  # noqa: E402
from tools.line_webhook import _event_dedup, app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_dedup():
    _event_dedup._seen = {}
    # Owner env may be read at import; ensure it stays set
    webhook_mod.OWNER_LINE_USER_ID = "U_OWNER"
    yield
    _event_dedup._seen = {}


def _fake_message_event(text: str, user_id: str):
    from linebot.v3.webhooks import MessageEvent

    ev = MagicMock(spec=MessageEvent)
    ev.webhook_event_id = "evt_e2e_1"
    ev.reply_token = "rt_e2e"
    ev.source = MagicMock()
    ev.source.user_id = user_id
    ev.message = MagicMock()
    ev.message.type = "text"
    ev.message.text = text
    return ev


def test_owner_strength_question_full_flow():
    """Phase 1 exit criterion: owner sends '增肌怎麼開始' and gets a coherent reply."""
    client = TestClient(app)
    ev = _fake_message_event("增肌怎麼開始", user_id="U_OWNER")

    mock_llm = MockClaudeClient.with_text("先從複合動作起，一週三次深蹲、臥推、硬舉。")
    captured: list[tuple[str, str]] = []

    def fake_reply_line(reply_token: str, text: str) -> None:
        captured.append((reply_token, text))

    with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
         patch("tools.gemini_client.get_llm_client", return_value=mock_llm), \
         patch("tools.line_webhook._reply_line", side_effect=fake_reply_line):
        r = client.post(
            "/callback",
            headers={"X-Line-Signature": "dummy"},
            content=b"{}",
        )

    assert r.status_code == 200
    assert len(captured) == 1
    reply_token, reply_text = captured[0]
    assert reply_token == "rt_e2e"
    assert "先從複合動作起" in reply_text
    # Owner mode debug footer present
    assert "🏊" in reply_text
    assert "domain: strength" in reply_text
    assert "tokens:" in reply_text
    # Injury disclaimer NOT injected (non-injury domain)
    assert "不是醫療診斷" not in reply_text


def test_non_owner_injury_question_injects_disclaimer():
    """Regular user with an injury question gets the mandatory disclaimer prepended."""
    client = TestClient(app)
    ev = _fake_message_event("膝蓋外側痛怎麼辦", user_id="U_REGULAR")

    mock_llm = MockClaudeClient.with_text("先停下來觀察 48 小時，避免加重活動。")
    captured: list[tuple[str, str]] = []

    with patch("tools.line_webhook.parser.parse", return_value=[ev]), \
         patch("tools.gemini_client.get_llm_client", return_value=mock_llm), \
         patch("tools.line_webhook._reply_line", side_effect=lambda t, x: captured.append((t, x))):
        r = client.post(
            "/callback",
            headers={"X-Line-Signature": "dummy"},
            content=b"{}",
        )

    assert r.status_code == 200
    assert len(captured) == 1
    _, reply_text = captured[0]
    assert "不是醫療診斷" in reply_text
    assert "先停下來觀察 48 小時" in reply_text
    # Non-owner: no debug footer
    assert "🏊" not in reply_text
