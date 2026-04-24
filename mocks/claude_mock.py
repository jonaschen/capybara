"""
mocks/claude_mock.py

Stand-in for bedrock_claude_client.get_claude_client(). Returns a client
whose .messages.create(...) emits a caller-configured response.

The default canned response is a coach-tone one-liner appropriate for 水豚教練.
Callers typically override with .with_text() or a custom responder.
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


class MockClaudeClient:
    def __init__(self, responder: Callable[[dict], str] | None = None):
        if responder is None:
            responder = lambda _: "今天先跑 Zone 2 三十分鐘，心率 130–145 bpm。"
        self.messages = _Messages(responder)

    @classmethod
    def with_text(cls, text: str) -> "MockClaudeClient":
        """Build a client whose every call returns the given raw text."""
        return cls(responder=lambda _: text)
