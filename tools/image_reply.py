"""
tools/image_reply.py

Personalized reply for training-data screenshots sent via LINE.

Single multimodal LLM call:
  image bytes + athlete_profile + CAPYBARA_VOICE  →  卡皮的個人化回應
                                                  OR sentinel "NOT_TRAINING_DATA"

Returns (reply_text | None, debug_info). reply_text is None when:
  - LLM judged the image is not training data (sentinel returned)
  - LLM call raised — error captured in debug_info, never re-raised

Owner mode does not change the reply text — the webhook layer appends a
debug footer ([🏊 image | size: ... | tokens: ...]) using debug_info.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from tools.voice import CAPYBARA_VOICE

logger = logging.getLogger("capybara.image_reply")


_NOT_TRAINING_SENTINEL = "NOT_TRAINING_DATA"
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_GENERIC_TRAINING_SUMMARY = "[傳來訓練數據截圖]"
_NON_TRAINING_SUMMARY = "[傳了一張非訓練數據圖片]"
_FETCH_FAILED_SUMMARY = "[傳了一張圖片，卡皮讀取失敗]"
_HISTORY_FAILED_SUMMARY = "[傳了一張圖片，卡皮處理失敗]"


def build_image_prompt(athlete_profile: str) -> str:
    """System prompt for image interpretation. Embeds CAPYBARA_VOICE so the
    bot's reply inherits the central voice rules."""
    profile_block = athlete_profile.strip() if athlete_profile else "尚無訓練檔案。"
    return f"""你是卡皮教練。

{CAPYBARA_VOICE}

以下是這位學員的訓練背景：
<athlete_profile>
{profile_block}
</athlete_profile>

學員剛傳來一張圖片。請你按照以下**兩段格式**回應：

**第一段：結構化摘要**（用於對話歷史，學員看不到這段）

格式：`<summary>[訓練截圖：{{活動類型}}, {{距離}}km, 配速趨勢：{{趨勢}}]</summary>`
- 活動類型：跑步 / 騎車 / 游泳 / 重訓 / 其他
- 距離 / 時間 / 配速：圖裡看不到的欄位寫「未知」
- 趨勢：穩定 / 後半掉速 / 前慢後快 / 負分跑 / 無法判斷

如果不是訓練數據（配速圖、心率圖、距離/時間/區間、訓練紀錄表都算訓練數據；其他都不是），
第一段寫：`<summary>[傳了一張非訓練數據圖片]</summary>`

**第二段：給學員看的回應**

非訓練數據的話，第二段只寫 {_NOT_TRAINING_SENTINEL} 這串字，不要其他文字。

是訓練數據的話：
- 一句話說看到了什麼（要具體：說數字、趨勢、區間）
- 結合 athlete_profile 給解讀（不只看數字，連結到目標、計畫）
- 如果有值得注意的，講最重要的一件
- 視情況問一個問題開啟對話，但不要每則都問
- 不超過 150 字
- 用第三人稱自稱（卡皮教練 / 卡皮），絕對不用「我」

如果學員前文稱呼你為「皮卡」，不要糾正，幽默接受（例如「好啦，比較熟的好朋友會叫卡皮『皮卡』，因為一樣可愛嘛 🐾」）。但卡皮自己**仍然只用卡皮教練 / 卡皮**自稱。
"""


def _split_summary_and_reply(raw: str) -> tuple[str, str]:
    """Extract <summary>...</summary> and the user-facing reply.

    Returns (summary_str, reply_str). Falls back to generic summary if the
    tag is missing (LLM didn't follow the format)."""
    match = _SUMMARY_RE.search(raw)
    if not match:
        # No tag — store the whole text as reply, generic summary
        return _GENERIC_TRAINING_SUMMARY, raw.strip()
    summary = match.group(1).strip()
    reply = (raw[: match.start()] + raw[match.end():]).strip()
    if not summary.startswith("[") or not summary.endswith("]"):
        summary = _GENERIC_TRAINING_SUMMARY
    return summary, reply


def analyze_training_image(
    image_bytes: bytes,
    mime_type: str,
    athlete_profile: str,
    client=None,
) -> tuple[str | None, str, dict[str, Any]]:
    """Single multimodal LLM call. Returns (reply_text, history_summary, debug).

    reply_text is None when the LLM emits the NOT_TRAINING_DATA sentinel,
    or when the call raised — the caller handles both via a fallback push.

    history_summary is always a non-empty string suitable for storing as the
    user message content in chat history (Phase B-lite). When the image was
    not training data, this is the generic [傳了一張非訓練數據圖片] marker;
    when LLM extraction succeeded, it carries activity/distance/trend.
    """
    debug: dict[str, Any] = {
        "size_kb": round(len(image_bytes) / 1024, 1),
        "in_tok": 0,
        "out_tok": 0,
        "error": None,
    }

    if client is None:
        from tools.gemini_client import get_llm_client
        client = get_llm_client()

    system_prompt = build_image_prompt(athlete_profile)
    user_content = [
        {"type": "text", "text": "請看這張圖片。"},
        {"type": "image", "source": {
            "type": "bytes", "media_type": mime_type, "data": image_bytes,
        }},
    ]

    try:
        response = client.messages.create(
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as exc:
        logger.warning(f"image_reply LLM call failed: {exc}")
        debug["error"] = str(exc)
        return None, _HISTORY_FAILED_SUMMARY, debug

    raw = (response.content[0].text or "").strip()
    usage = getattr(response, "usage", None)
    debug["in_tok"] = getattr(usage, "input_tokens", 0) if usage else 0
    debug["out_tok"] = getattr(usage, "output_tokens", 0) if usage else 0

    summary, reply = _split_summary_and_reply(raw)

    if _NOT_TRAINING_SENTINEL in reply:
        # If the LLM correctly tagged the summary as non-training, prefer that;
        # else fall back to the generic non-training marker.
        if "非訓練" not in summary:
            summary = _NON_TRAINING_SUMMARY
        return None, summary, debug

    return reply, summary, debug
