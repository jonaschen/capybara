"""
tools/onboarding_reply.py

ONBOARDING state handler. Drives the conversational interview, detects
completion via a [PROFILE_COMPLETE] marker or explicit "結束", runs
profile extraction, and persists athlete_profile.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tools import plan_generator, profile_generator

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "agents" / "onboarding" / "system_prompt.xml"

COMPLETION_MARKER = "[PROFILE_COMPLETE]"
QUIT_KEYWORDS = {"結束", "不要了", "停", "quit", "exit"}

_HISTORY_CAP = 20


def _load_system_prompt() -> str:
    if not SYSTEM_PROMPT_PATH.exists():
        raise RuntimeError(
            f"Missing {SYSTEM_PROMPT_PATH}. Draft it before running onboarding."
        )
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _transcript_from_history(history: list[dict]) -> str:
    """Flatten a conversation history into a plain transcript for extraction."""
    lines = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        label = "學員" if role == "user" else "教練"
        lines.append(f"{label}：{content}")
    return "\n".join(lines)


def _strip_marker(text: str) -> str:
    return text.replace(COMPLETION_MARKER, "").strip()


def onboarding_reply(
    user_text: str,
    user_id: str,
    history: list[dict],
    system_prompt: str | None = None,
    client=None,
    gcs_client=None,
    bucket: str | None = None,
) -> tuple[str, bool]:
    """Run one turn of the onboarding interview.

    Returns (user_visible_reply, is_complete). When is_complete=True, the
    athlete_profile.md has been written to GCS and the caller should
    transition the user back to IDLE.

    `history` is mutated in place: the new user turn and assistant turn
    are appended.
    """
    if client is None:
        from tools.gemini_client import get_llm_client
        client = get_llm_client()

    if system_prompt is None:
        system_prompt = _load_system_prompt()

    # Append user turn so it's visible to both the LLM and the transcript.
    history.append({"role": "user", "content": user_text})

    # User-forced quit path — skip the interview LLM call, go straight to extraction.
    if user_text.strip().lower() in QUIT_KEYWORDS:
        closing = _finalize(
            history=history,
            user_id=user_id,
            client=client,
            gcs_client=gcs_client,
            bucket=bucket,
            lead_in="好，我先用現在的資訊建立計畫。之後隨時可以補充。",
        )
        history.append({"role": "assistant", "content": closing})
        return closing, True

    # Normal interview turn
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=system_prompt,
        messages=list(history),
    )
    raw_reply = response.content[0].text

    is_complete = COMPLETION_MARKER in raw_reply
    visible_reply = _strip_marker(raw_reply)
    history.append({"role": "assistant", "content": visible_reply})

    # Cap history growth (rolling window)
    if len(history) > _HISTORY_CAP:
        del history[: len(history) - _HISTORY_CAP]

    if is_complete:
        visible_reply = _finalize(
            history=history,
            user_id=user_id,
            client=client,
            gcs_client=gcs_client,
            bucket=bucket,
            lead_in=visible_reply,
        )

    return visible_reply, is_complete


def _finalize(
    history: list[dict],
    user_id: str,
    client,
    gcs_client,
    bucket: str | None,
    lead_in: str,
) -> str:
    """Extract profile, generate plan, return composed completion message."""
    transcript = _transcript_from_history(history)
    profile_data = profile_generator.generate_profile(
        transcript=transcript,
        user_id=user_id,
        client=client,
        gcs_client=gcs_client,
        bucket=bucket,
    )
    plan_md = plan_generator.generate_plan(
        profile=profile_data,
        user_id=user_id,
        client=client,
        gcs_client=gcs_client,
        bucket=bucket,
    )
    focus = plan_generator.extract_week_focus(plan_md)
    tail = f"本週重點：{focus}" if focus else "訓練計畫已建立。"
    return f"{lead_in}\n\n{tail} 🐾"
