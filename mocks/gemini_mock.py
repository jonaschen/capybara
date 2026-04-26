"""
mocks/gemini_mock.py

Stand-in for tools.gemini_client.get_gemini_client(). Same shape as
MockClaudeClient — `.messages.create(...)` returns a Response with
`.content[0].text` and `.usage.{input,output}_tokens`.

Records every call's kwargs in `.messages.calls`, including multimodal
content blocks (text + image), so tests can assert on the prompt
structure and image presence.
"""

from __future__ import annotations

from typing import Any, Callable


class _Content:
    def __init__(self, text: str):
        self.text = text
        self.type = "text"


class _Usage:
    def __init__(self, input_tokens: int = 100, output_tokens: int = 50):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, text: str, input_tokens: int = 100, output_tokens: int = 50):
        self.content = [_Content(text)]
        self.stop_reason = "end_turn"
        self.usage = _Usage(input_tokens, output_tokens)


class _Messages:
    def __init__(self, responder: Callable[[dict], str]):
        self._responder = responder
        self.calls: list[dict] = []

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        return _Response(self._responder(kwargs))


class MockGeminiClient:
    """Drop-in for tools.gemini_client.get_gemini_client()."""

    def __init__(self, responder: Callable[[dict], str] | None = None):
        if responder is None:
            responder = lambda _: "卡皮看了你的數據，後半段配速掉了一點。🐾"
        self.messages = _Messages(responder)

    @classmethod
    def with_text(cls, text: str) -> "MockGeminiClient":
        """Build a client whose every call returns the given raw text."""
        return cls(responder=lambda _: text)

    @classmethod
    def with_exception(cls, exc: Exception) -> "MockGeminiClient":
        """Build a client whose every call raises the given exception."""
        def _raise(_kwargs):
            raise exc
        return cls(responder=_raise)
