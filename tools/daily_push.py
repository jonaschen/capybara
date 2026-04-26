"""
tools/daily_push.py

Scheduled proactive push. Cloud Scheduler hits /trigger/daily_push twice
a day: 07:00 morning (today's training + one tip) and 21:00 evening
(one-line companionship, no demands).

Both modes iterate every user with a training_plan.md in GCS, build a
per-user message, and push via LINE Messaging API.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tools import gcs_profile
from tools.voice import CAPYBARA_VOICE

logger = logging.getLogger(__name__)


MORNING_SYSTEM_PROMPT = f"""你是卡皮教練，早上 7 點傳一段話給學員。

{CAPYBARA_VOICE}

輸出格式（兩行）：
（第一行：今天的訓練任務，一句話，不超過 30 字。用學員週課表今天那天的內容。）
💡 （第二行：一個具體、可執行的提示，連結到今天的動作或狀態。）

額外規則：
- 具體到動作、時間、心率或組數。不說「加油」「你可以的」。
- 如果今天是休息日，第一行寫「今天休息。」第二行給睡眠或伸展提示。
- 「💡」這個 emoji 是格式必要，不算進 emoji 上限。其他 emoji 只能用 🐾。
- 語言：繁體中文。"""


EVENING_SYSTEM_PROMPT = f"""你是卡皮教練，晚上 9 點傳一段話給學員。

{CAPYBARA_VOICE}

輸出格式：一到兩句，純粹陪伴，不要求任何行動。

額外規則：
- 不催促、不檢討今天練了沒。
- 不下指令，不問問題。
- 不給訓練建議。
- 可以提到「明天」或「休息」等自然語彙。
- 語言：繁體中文。"""


def _compose_user_message(profile: dict[str, Any], plan_md: str) -> str:
    return (
        "學員資料：\n"
        f"```json\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n```\n\n"
        "目前訓練計畫：\n"
        f"{plan_md}"
    )


def generate_morning_push(profile: dict[str, Any], plan_md: str, client) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        system=MORNING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _compose_user_message(profile, plan_md)}],
    )
    return response.content[0].text.strip()


def generate_evening_push(profile: dict[str, Any], plan_md: str, client) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=150,
        system=EVENING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _compose_user_message(profile, plan_md)}],
    )
    return response.content[0].text.strip()


def _load_profile_dict(user_id: str, gcs_client, bucket: str | None) -> dict[str, Any]:
    """Read athlete_profile.md and extract the YAML block into a dict.
    Falls back to an empty dict on parse failure — push still proceeds with plan-only context."""
    try:
        md = gcs_profile.read_profile(user_id, client=gcs_client, bucket=bucket)
    except FileNotFoundError:
        return {}
    data: dict[str, Any] = {}
    in_yaml = False
    for line in md.splitlines():
        if line.strip().startswith("```yaml"):
            in_yaml = True
            continue
        if line.strip().startswith("```") and in_yaml:
            break
        if in_yaml and ":" in line:
            k, _, v = line.partition(":")
            data[k.strip()] = v.strip()
    return data


def _push_line(line_api, user_id: str, text: str) -> None:
    """Thin wrapper so we can pass either the mock or a real linebot MessagingApi."""
    from linebot.v3.messaging import PushMessageRequest, TextMessage

    line_api.push_message(
        PushMessageRequest(to=user_id, messages=[TextMessage(text=text)])
    )


def send_daily_push(
    push_type: str,
    line_api,
    llm_client=None,
    gcs_client=None,
    bucket: str | None = None,
) -> dict[str, Any]:
    """Fan out a push to every user with a training_plan.md."""
    if push_type not in ("morning", "evening"):
        raise ValueError(f"push_type must be 'morning' or 'evening', got {push_type!r}")

    if llm_client is None:
        from tools.gemini_client import get_llm_client
        llm_client = get_llm_client()

    generate = generate_morning_push if push_type == "morning" else generate_evening_push

    user_ids = gcs_profile.list_user_ids(client=gcs_client, bucket=bucket)
    results: list[dict[str, Any]] = []
    pushed = failed = skipped = 0

    for uid in user_ids:
        if not gcs_profile.profile_exists(uid, filename="training_plan.md", client=gcs_client, bucket=bucket):
            logger.info(f"Skip {uid}: no training_plan.md yet")
            skipped += 1
            continue
        try:
            plan_md = gcs_profile.read_profile(uid, filename="training_plan.md", client=gcs_client, bucket=bucket)
            profile = _load_profile_dict(uid, gcs_client, bucket)
            text = generate(profile=profile, plan_md=plan_md, client=llm_client)
            _push_line(line_api, uid, text)
            logger.info(f"Pushed {push_type} to {uid[:8]}... ({len(text)} chars)")
            results.append({"user_id": uid, "status": "ok"})
            pushed += 1
        except Exception as exc:
            logger.warning(f"Push failed for {uid}: {exc}")
            results.append({"user_id": uid, "status": "failed", "error": str(exc)})
            failed += 1

    logger.info(
        f"Push {push_type} done: pushed={pushed} failed={failed} skipped={skipped} "
        f"total_users={len(user_ids)}"
    )
    return {"pushed": pushed, "failed": failed, "results": results}
