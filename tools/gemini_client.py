"""
tools/gemini_client.py

Gemini wrapper with an Anthropic-SDK-compatible interface. Together with
bedrock_claude_client, lets callers switch providers via LLM_PROVIDER.

Usage:
    from tools.gemini_client import get_llm_client
    client = get_llm_client()  # respects LLM_PROVIDER env
    response = client.messages.create(
        max_tokens=400,
        system="...",
        messages=[{"role": "user", "content": "..."}],
    )
    print(response.content[0].text)
"""

from __future__ import annotations

import os


class _Content:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Message:
    def __init__(self, text: str, input_tokens: int, output_tokens: int):
        self.content = [_Content(text)]
        self.stop_reason = "end_turn"
        self.usage = _Usage(input_tokens, output_tokens)


_DEFAULT_MODEL = os.environ.get("GEMINI_MODEL_ID", "gemini-2.0-flash")


def _content_to_parts(content) -> list[dict]:
    """Convert message content (string OR Anthropic-style block list) to
    Gemini parts. String stays single text-part (back-compat). Block list
    maps text → {"text": ...} and image → {"inline_data": {mime_type, data}}.

    Image source forms accepted:
      - {"type": "base64", "media_type": "image/png", "data": "<b64 str>"}  (Anthropic format)
      - {"type": "bytes",  "media_type": "image/jpeg", "data": <bytes>}     (skip round-trip)
    Bytes are what google-genai's inline_data wants; base64 strings are decoded.
    """
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        return [{"text": str(content)}]

    parts: list[dict] = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            parts.append({"text": block.get("text", "")})
        elif btype == "image":
            source = block.get("source", {})
            data = source.get("data", b"")
            if isinstance(data, str):
                import base64
                data = base64.b64decode(data)
            parts.append({"inline_data": {
                "mime_type": source.get("media_type", "image/jpeg"),
                "data": data,
            }})
        else:
            parts.append({"text": str(block)})
    return parts


def _create_genai_client(api_key: str):
    """Create a google.genai.Client. Extracted for test patching."""
    from google import genai
    return genai.Client(api_key=api_key)


class _GeminiMessages:
    """Mimics anthropic.Anthropic().messages, backed by google-genai."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def create(
        self,
        model: str | None = None,
        max_tokens: int = 1000,
        system: str | None = None,
        messages: list | None = None,
        temperature: float | None = None,
        **kwargs,
    ) -> _Message:
        from google.genai import types

        client = _create_genai_client(self._api_key)
        model_id = model if model and model.startswith("gemini") else _DEFAULT_MODEL

        contents = []
        for msg in (messages or []):
            role = "user" if msg["role"] == "user" else "model"
            parts = _content_to_parts(msg["content"])
            contents.append({"role": role, "parts": parts})

        # Do NOT pass max_output_tokens — known SDK bug triggers early truncation.
        config_kwargs: dict = {}
        if system:
            config_kwargs["system_instruction"] = system
        if temperature is not None:
            config_kwargs["temperature"] = temperature

        response = client.models.generate_content(
            model=model_id,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
        )

        text = response.text or ""
        usage_meta = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage_meta, "prompt_token_count", 0) if usage_meta else 0
        output_tokens = getattr(usage_meta, "candidates_token_count", 0) if usage_meta else 0
        return _Message(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


class GeminiClient:
    """Drop-in replacement for anthropic.Anthropic(), backed by Gemini."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        self.messages = _GeminiMessages(self._api_key)


def get_gemini_client(api_key: str | None = None) -> GeminiClient:
    return GeminiClient(api_key=api_key)


def get_llm_client(provider: str | None = None):
    """
    Provider-agnostic factory. Resolves in order:
      1. provider argument
      2. LLM_PROVIDER env var
      3. Default: "claude"
    """
    provider = (provider or os.environ.get("LLM_PROVIDER", "claude")).lower()
    if provider == "gemini":
        return get_gemini_client()
    from tools.bedrock_claude_client import get_claude_client
    return get_claude_client()
