"""
tools/plan_adjuster.py

In-place training plan adjustment. Reads the current training_plan.md,
asks the LLM to produce a new body (本週重點 / 週課表 / 調整記錄), then
deterministically preserves the original Generated date, updates
Last adjusted to today, and prepends the adjustment reason as a dated
bullet under 調整記錄.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from tools import gcs_profile

logger = logging.getLogger(__name__)


ADJUST_SYSTEM_PROMPT = """你是水豚教練，調整一份現有的訓練計畫。

學員提出了一個調整理由。請根據理由改寫計畫的三個區段（只輸出這三段，不要輸出標題或日期）：

## 本週重點
（一句話，反映調整後的本週方向。）

## 週課表
| 星期 | 訓練內容 | 時長 | 強度 |
|---|---|---|---|
| 一 | ... | XX 分 | 低/中/高 |
| 二 | ... | ... | ... |
...
| 日 | ... | ... | ... |

## 調整記錄
（保留原本的記錄，程式會自動補上本次調整條目。）

規則：
- 只改動必要的部分——調整理由是什麼，就只改那部分。學員還能做的動作保留。
- 若理由是傷痛，避開該部位的動作，改為不影響該部位的替代方案。
- 若理由是「太難」「太累」，降低強度一級或減少組數，不直接休練。
- 若理由是「太簡單」，加強度一級或延長時長 10–20%，不要一次加太多。
- 強度分四級：極低 / 低 / 中 / 高。
- 不用 emoji。不說加油類空話。
- 繁體中文。

從「## 本週重點」開始輸出。"""


_GENERATED_RE = re.compile(r"^Generated: (.+)$", re.MULTILINE)
_HEADER_RE = re.compile(r"^# 訓練計畫 — (.+)$", re.MULTILINE)
_ADJUST_SECTION_RE = re.compile(r"^## 調整記錄\s*\n(.*?)(?=\n##|\Z)", re.DOTALL | re.MULTILINE)


def _extract_header_fields(plan_md: str) -> tuple[str, str]:
    """Return (goal, original_generated_date). Raises if missing."""
    h = _HEADER_RE.search(plan_md)
    g = _GENERATED_RE.search(plan_md)
    if not h or not g:
        raise ValueError("Existing plan is missing header or Generated line")
    return h.group(1).strip(), g.group(1).strip()


def _merge_adjust_log(existing_section: str, new_entry: str) -> str:
    """Prepend new_entry under the heading, keeping prior entries. Strips the
    '（尚無調整）' / '（程式會補上新條目）' placeholder if present."""
    placeholder_re = re.compile(r"^\s*（[^）]*）\s*$", re.MULTILINE)
    cleaned = placeholder_re.sub("", existing_section).strip()
    lines = [new_entry]
    if cleaned:
        lines.append(cleaned)
    return "\n".join(lines) + "\n"


def adjust_plan(
    user_id: str,
    reason: str,
    client=None,
    gcs_client=None,
    bucket: str | None = None,
) -> str:
    """Adjust the user's current training plan based on a reason. Returns
    the new plan Markdown. Raises FileNotFoundError if no plan exists."""
    if client is None:
        from tools.gemini_client import get_llm_client
        client = get_llm_client()

    existing = gcs_profile.read_profile(
        user_id, filename="training_plan.md", client=gcs_client, bucket=bucket
    )
    goal, generated_date = _extract_header_fields(existing)

    user_msg = (
        f"調整理由：{reason}\n\n"
        f"目前計畫：\n{existing}"
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=ADJUST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    new_body = response.content[0].text.strip()

    # Source of truth for the log is the file we just read — not the LLM's
    # output. Drop whatever 調整記錄 the LLM produced and rebuild it ourselves
    # by prepending the new dated entry to the prior log.
    existing_log_match = _ADJUST_SECTION_RE.search(existing)
    prior_log = existing_log_match.group(1) if existing_log_match else ""

    new_body_log_match = _ADJUST_SECTION_RE.search(new_body)
    if new_body_log_match:
        new_body = new_body[: new_body_log_match.start()].rstrip()

    new_entry = f"- {date.today().isoformat()}: {reason}"
    merged_section = _merge_adjust_log(prior_log, new_entry)
    new_body = f"{new_body.rstrip()}\n\n## 調整記錄\n{merged_section}"

    today = date.today().isoformat()
    header = (
        f"# 訓練計畫 — {goal}\n"
        f"Generated: {generated_date}\n"
        f"Valid until: {generated_date}\n"
        f"Last adjusted: {today}\n\n"
    )
    updated = header + new_body.strip() + "\n"

    gcs_profile.write_profile(
        user_id,
        updated,
        filename="training_plan.md",
        client=gcs_client,
        bucket=bucket,
    )
    return updated
