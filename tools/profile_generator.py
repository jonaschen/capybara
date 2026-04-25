"""
tools/profile_generator.py

Extract a structured athlete profile from an onboarding transcript via one
Claude call with a JSON-only system prompt, render to athlete_profile.md,
and persist via gcs_profile.write_profile.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from tools import gcs_profile

logger = logging.getLogger(__name__)


EXTRACTION_SYSTEM_PROMPT = """你是資料擷取工具。從以下對話抄本中擷取運動員輪廓欄位，輸出嚴格的 JSON（無其他文字）。

欄位：
- goal (string): 訓練目標，例如「增肌」「減脂」「完成半程鐵人」「提升體能」等
- current_level (string): 目前體能水平，例如「完全新手」「有基礎但不規律」「規律訓練 X 個月」
- available_days (integer): 每週可訓練天數
- session_duration (integer): 每次訓練大約分鐘
- equipment (string): 器材可用性，例如「健身房」「家裡有啞鈴」「無器材」「有自行車」
- injury_history (string): 傷病史，無傷請填「無」
- race_target (string): 賽事目標，無請填「無」
- weight_height (string, optional): 體重與身高，若減脂目標請盡量取得，格式「體重 Xkg、身高 Ycm」

規則：
- 僅根據對話內容擷取，不得編造。對話未提及的非必要欄位可省略。
- 欄位若使用者未明確回答，填 "未提供"。
- 輸出格式為單一 JSON 物件，不要 Markdown code fence、不要前後說明文字。
"""


REQUIRED_FIELDS = [
    "goal",
    "current_level",
    "available_days",
    "session_duration",
    "equipment",
    "injury_history",
    "race_target",
]


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _strip_fence(text: str) -> str:
    m = _FENCE_RE.match(text)
    return m.group(1) if m else text


def _extract_json(raw: str) -> dict[str, Any]:
    cleaned = _strip_fence(raw.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Profile extraction returned non-JSON: {raw[:200]}") from e


def render_profile_md(data: dict[str, Any]) -> str:
    """Deterministic renderer. Never calls an LLM."""
    today = date.today().isoformat()
    lines = [
        "# 卡皮教練學員輪廓",
        f"Generated: {today}",
        "",
        "## 核心資料",
        "```yaml",
    ]
    for key in REQUIRED_FIELDS:
        if key in data:
            lines.append(f"{key}: {data[key]}")
    if "weight_height" in data:
        lines.append(f"weight_height: {data['weight_height']}")
    lines.append("```")
    lines.append("")
    lines.append("> 本檔案由 onboarding 對話擷取生成，寫入後不再覆寫。"
                 "若需修改請新增版本紀錄，保留原始資料。")
    return "\n".join(lines) + "\n"


def generate_profile(
    transcript: str,
    user_id: str,
    client=None,
    gcs_client=None,
    bucket: str | None = None,
) -> dict[str, Any]:
    """Run the full pipeline: extract → render → persist. Returns extracted dict."""
    if client is None:
        from tools.gemini_client import get_llm_client
        client = get_llm_client()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": transcript}],
    )
    raw = response.content[0].text
    data = _extract_json(raw)

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        logger.warning(f"Profile missing fields: {missing}")

    md = render_profile_md(data)
    gcs_profile.write_profile(
        user_id,
        md,
        client=gcs_client,
        bucket=bucket,
    )
    return data
