"""
tools/bedrock_claude_client.py

Claude wrapper with an Anthropic-SDK-compatible interface. Backed by either
the Anthropic Messages API directly (default — simpler on Cloud Run) or
AWS Bedrock (when USE_BEDROCK=true).

Usage:
    from tools.bedrock_claude_client import get_claude_client
    client = get_claude_client()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system="...",
        messages=[{"role": "user", "content": "..."}],
    )
    text = response.content[0].text
"""

from __future__ import annotations

import json
import os

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


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


class _BedrockMessages:
    """Mimics anthropic.Anthropic().messages, backed by Bedrock InvokeModel."""

    def __init__(self, bedrock_client):
        self._bedrock = bedrock_client

    def create(
        self,
        model: str | None = None,
        max_tokens: int = 1000,
        system: str | None = None,
        messages: list | None = None,
        temperature: float | None = None,
        **kwargs,
    ) -> _Message:
        body: dict = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": messages or [],
        }
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature

        from botocore.exceptions import ClientError  # lazy

        try:
            response = self._bedrock.invoke_model(
                modelId=MODEL_ID,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "AccessDeniedException":
                raise RuntimeError(
                    f"Bedrock access denied. Check model access for {MODEL_ID} "
                    f"and IAM permissions (AmazonBedrockFullAccess)."
                ) from e
            raise

        result = json.loads(response["body"].read())
        text = result["content"][0]["text"]
        usage = result.get("usage", {})
        return _Message(
            text=text,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )


class BedrockClaudeClient:
    """Drop-in replacement for anthropic.Anthropic(), backed by AWS Bedrock."""

    def __init__(self):
        import boto3  # lazy — only needed when USE_BEDROCK=true
        self._bedrock = boto3.client(service_name="bedrock-runtime", region_name=REGION)
        self.messages = _BedrockMessages(self._bedrock)


def get_claude_client():
    """
    Resolve Claude client based on USE_BEDROCK env var.

    USE_BEDROCK=true  → BedrockClaudeClient (AWS Bedrock)
    USE_BEDROCK=false → anthropic.Anthropic() (direct API — default for capybara)
    """
    use_bedrock = os.environ.get("USE_BEDROCK", "false").lower() == "true"
    if use_bedrock:
        return BedrockClaudeClient()
    import anthropic
    return anthropic.Anthropic()
