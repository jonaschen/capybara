"""
tools/plan_generator.py

Generate an initial 4-week training plan from a completed athlete profile.
One LLM call produces the body (本週重點 + 週課表 + 調整記錄 sections); the
header + Generated/Valid until/Last adjusted lines are injected deterministically
so dates never depend on the model.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

from tools import gcs_profile

logger = logging.getLogger(__name__)


PLAN_SYSTEM_PROMPT = """你是水豚教練，為新學員產生第一份 4 週訓練計畫。

只輸出以下三個區段（不要輸出標題、不要輸出日期欄位，那些由程式補上）：

## 本週重點
（一句話，說明這週的重點——具體、不抽象。）

## 週課表
| 星期 | 訓練內容 | 時長 | 強度 |
|---|---|---|---|
| 一 | （內容） | XX 分 | 低/中/高 |
...
| 日 | （內容） | XX 分 | 極低/低/中 |

## 調整記錄
（尚無調整）

規則：
- 週課表必須涵蓋一到日七天（休息日也要列，內容寫「休息」或「輕度活動」）。
- 強度分四級：極低 / 低 / 中 / 高。新手不排「高」。
- 目標是三鐵的話，週課表要平衡游泳、騎車、跑步。
- 目標是增肌的話，以複合動作為主（深蹲、硬舉、臥推、引體、划船、肩推）。
- 目標是減脂的話，走肌力 + 低強度有氧組合，不排極端熱量赤字暗示。
- 用學員的 available_days 設定訓練日數，剩餘排休息。
- session_duration 是每次訓練分鐘，直接套用到時長。
- 傷病史有內容的話，避開該部位動作。
- 專業詞彙（Zone 2、RPE、1RM 等）第一次出現時在同行括號解釋。
- 不用 emoji。不說加油類空話。

只輸出上述三個區段，從「## 本週重點」開始。"""


_FOCUS_RE = re.compile(r"^## 本週重點\s*\n(.+?)(?=\n##|\Z)", re.DOTALL | re.MULTILINE)


def extract_week_focus(plan_md: str) -> str:
    m = _FOCUS_RE.search(plan_md)
    if not m:
        return ""
    return m.group(1).strip().splitlines()[0].strip()


def _compose_plan(goal: str, llm_body: str) -> str:
    today = date.today().isoformat()
    valid_until = (date.today() + timedelta(weeks=4)).isoformat()
    header = (
        f"# 訓練計畫 — {goal}\n"
        f"Generated: {today}\n"
        f"Valid until: {valid_until}\n"
        f"Last adjusted: {today}\n\n"
    )
    body = llm_body.strip()
    if not body.startswith("## 本週重點"):
        logger.warning("Plan body missing '## 本週重點' header — prepending it")
    return header + body + ("\n" if not body.endswith("\n") else "")


def generate_plan(
    profile: dict[str, Any],
    user_id: str,
    client=None,
    gcs_client=None,
    bucket: str | None = None,
) -> str:
    """Run the full pipeline: LLM → deterministic compose → persist. Returns plan text."""
    if client is None:
        from tools.gemini_client import get_llm_client
        client = get_llm_client()

    user_msg = (
        "根據以下學員資料產生第一份計畫：\n\n"
        f"```json\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n```"
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=PLAN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    body = response.content[0].text

    goal = profile.get("goal", "一般體能")
    plan_md = _compose_plan(goal, body)

    gcs_profile.write_profile(
        user_id,
        plan_md,
        filename="training_plan.md",
        client=gcs_client,
        bucket=bucket,
    )
    return plan_md
