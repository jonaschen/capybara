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
from typing import Any

from tools.voice import CAPYBARA_VOICE

logger = logging.getLogger("capybara.image_reply")


_NOT_TRAINING_SENTINEL = "NOT_TRAINING_DATA"


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

學員剛傳來一張圖片。請你：

1. 先判斷這是不是訓練數據（配速圖、心率圖、距離/時間/區間、訓練紀錄表都算）。
   **不是的話，只回覆 {_NOT_TRAINING_SENTINEL} 這串字，不要其他文字。**

2. 是訓練數據的話，按照卡皮的方式回應：
   - 一句話說看到了什麼（要具體：說數字、趨勢、區間）
   - 結合 athlete_profile 給解讀（不只看數字，連結到目標、計畫）
   - 如果有值得注意的，講最重要的一件
   - 視情況問一個問題開啟對話，但不要每則都問
   - 不超過 150 字
   - 用第三人稱自稱（卡皮教練 / 卡皮），絕對不用「我」
"""


def analyze_training_image(
    image_bytes: bytes,
    mime_type: str,
    athlete_profile: str,
    client=None,
) -> tuple[str | None, dict[str, Any]]:
    """Single multimodal LLM call. Returns (reply_text, debug_info).

    reply_text is None when the LLM emits the NOT_TRAINING_DATA sentinel,
    or when the call raised — the caller handles both via a fallback push.
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
            max_tokens=400,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as exc:
        logger.warning(f"image_reply LLM call failed: {exc}")
        debug["error"] = str(exc)
        return None, debug

    text = (response.content[0].text or "").strip()
    usage = getattr(response, "usage", None)
    debug["in_tok"] = getattr(usage, "input_tokens", 0) if usage else 0
    debug["out_tok"] = getattr(usage, "output_tokens", 0) if usage else 0

    if _NOT_TRAINING_SENTINEL in text:
        return None, debug

    return text, debug
