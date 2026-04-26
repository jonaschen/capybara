"""
mocks/line_mock.py

Stand-in for linebot.v3 MessagingApi. Records every push_message and
reply_message call in an in-memory list so tests can assert on them.

Usage
-----
    api = MockLINEAPI()
    api.push_message(PushMessageRequest(to="U1", messages=[TextMessage(text="hi")]))
    assert api.sent[0]["to"] == "U1"
    assert "hi" in api.sent[0]["text"]
"""

from __future__ import annotations

from typing import Any


class _FollowersResponse:
    """Mimics linebot.v3.messaging.models.GetFollowersResponse."""

    def __init__(self, user_ids: list[str], next_token: str | None = None):
        self.user_ids = user_ids
        self.next = next_token


class MockLINEAPI:
    def __init__(self, fail_users: set[str] | None = None):
        self.sent: list[dict[str, Any]] = []
        self._fail_users = fail_users or set()
        self._followers: list[str] = []
        self._followers_page_size: int = 1000
        # Image content keyed by message_id, used by get_message_content().
        # Tests seed via .seed_image(message_id, bytes, mime).
        self._image_contents: dict[str, tuple[bytes, str]] = {}

    def push_message(self, request: Any) -> None:
        to = getattr(request, "to", None) or request["to"]
        if to in self._fail_users:
            raise RuntimeError(f"mock: push to {to} blocked")
        messages = getattr(request, "messages", None) or request["messages"]
        for m in messages:
            text = getattr(m, "text", None) or m.get("text", "")
            self.sent.append({"kind": "push", "to": to, "text": text})

    def reply_message(self, request: Any) -> None:
        reply_token = getattr(request, "reply_token", None) or request["reply_token"]
        messages = getattr(request, "messages", None) or request["messages"]
        for m in messages:
            text = getattr(m, "text", None) or m.get("text", "")
            self.sent.append({"kind": "reply", "reply_token": reply_token, "text": text})

    def get_followers(
        self, start: str | None = None, limit: int | None = None, **kwargs: Any
    ) -> _FollowersResponse:
        page = self._followers_page_size
        idx = 0
        if start is not None:
            idx = int(start)
        end = idx + page
        chunk = self._followers[idx:end]
        next_token = str(end) if end < len(self._followers) else None
        return _FollowersResponse(user_ids=chunk, next_token=next_token)

    # ─── Image content (MessagingApiBlob shape) ──────────────────────────

    def seed_image(self, message_id: str, data: bytes, mime: str = "image/jpeg") -> None:
        """Test helper: register image bytes that get_message_content will return."""
        self._image_contents[message_id] = (data, mime)

    def get_message_content(self, message_id: str) -> bytes:
        """Mimic linebot.v3.messaging.MessagingApiBlob.get_message_content."""
        if message_id not in self._image_contents:
            raise RuntimeError(f"mock: no seeded content for message_id={message_id!r}")
        return self._image_contents[message_id][0]

    def get_message_content_mime(self, message_id: str) -> str:
        """Companion accessor; real SDK returns this via Content-Type header."""
        if message_id not in self._image_contents:
            return "image/jpeg"
        return self._image_contents[message_id][1]
