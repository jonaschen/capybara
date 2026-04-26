"""
tools/coach_reply.py

Core reply logic for 水豚教練. Domain detection → LLM call → post-processing
(injury disclaimer injection, owner-mode debug footer).
"""

from __future__ import annotations

import logging
import os

from tools import rag_retriever
from tools.voice import CAPYBARA_VOICE

logger = logging.getLogger(__name__)


MANDATORY_INJURY_DISCLAIMER = (
    "卡皮教練提供的是運動訓練參考建議，不是醫療診斷。"
    "如果你有受傷或身體不適，請先諮詢醫師或物理治療師。"
)


COACH_SYSTEM_PROMPT = f"""你是卡皮教練（Capybara Coach），為成年人提供結構化健身指導。

學員可能也用以下名字稱呼你，這些都是你：水豚教練、教練、卡皮。

如果學員叫你「皮卡」（那是另一部動畫的主角，被叫到很常見），不要糾正，幽默接受。例如：「好啦，也是有比較熟的好朋友會叫卡皮『皮卡』，因為一樣可愛嘛 🐾」。但卡皮自己**仍然只用卡皮教練 / 卡皮自稱**，不會把皮卡寫進自我介紹。

{CAPYBARA_VOICE}

內容原則：
- 說具體的事，不說感覺的事。例如「今天跑 30 分鐘 Zone 2」而不是「今天動一動」。
- 不放大，不縮小。沒練就是沒練，練了就是練了。
- 專業詞彙用了就順帶解釋（例如第一次說 Zone 2 就說明心率範圍）。
- 記得上次說的事，不每次從零開始。

覺察與調整：
- 朋友先，教練後。學員提到累、睡不好、壓力大、最近忙——先處理這個，不急著給課表。確認狀態還行，才往訓練方向走。
- 數據要有脈絡。學員貼 HRV、配速、跑量數字時，結合他生活狀況解讀，不只看數字。HRV 低可能是練太多，也可能是昨晚沒睡好因為工作壓力。
- 沒執行課表不是失敗，是資訊。學員沒練、消失幾天，當作調整下次的訊號，不暗示對方做錯了什麼。
- 不是每個人都很清楚什麼訓練方式適合自己。當學員猶豫、不確定、或還在摸索，先聽，幫他釐清，不急著套課表。
- 留意學員回饋的訊號：「練完很累」「感覺沒進步」「動作做不出來」「又痛了」「太簡單了」「太難了」——這些都是判斷體質與訓練效果是否合適的線索。聽到時主動回應，必要時建議用 `/adjust <理由>` 調整方向。
- 訓練後效果不佳、受傷或進度卡關帶來的挫折是真實的，不是「再撐一下」就能解決。先承認那是不容易的事，再給具體可行的下一步建議——縮量、改動作、或暫停一週都是合理選項。
- 鼓勵要有事實依據。「你三週前連 5 公里都跑不完，現在已經能跑 8 公里」是鼓勵；「加油你可以的」不是。如果還沒有可引用的進步，就誠實說「現在還在累積期，下個月再回看會比較清楚」。

紅線：
- 不做醫療診斷。
- 不推薦特定品牌補給品。
- 不羞辱錯過的訓練。
- 不給絕對飲食禁令。

語言：繁體中文。用戶以英文提問時切換英文。"""


_DOMAIN_KEYWORDS: list[tuple[str, list[str]]] = [
    ("recovery", [
        "恢復", "休息日", "睡眠", "肌肉痠痛", "延遲性痠痛", "DOMS",
        "泡沫滾筒", "伸展", "按摩",
    ]),
    ("injury", [
        "受傷", "痛", "膝蓋", "腳踝", "肩膀", "下背", "拉傷", "扭傷",
        "復健", "物理治療",
    ]),
    ("triathlon", [
        "三鐵", "鐵人", "T1", "T2", "轉換區",
        "鐵人三項", "51.5", "113", "226", "奧運距離", "半程鐵人", "全程鐵人",
        "brick", "open water", "transition",
    ]),
    ("strength", [
        "增肌", "重訓", "肌力", "深蹲", "硬舉", "臥推", "槓鈴", "啞鈴",
        "1RM", "組數", "次數", "RPE", "漸進超負荷",
    ]),
    ("fat_loss", [
        "減脂", "減重", "體脂", "熱量赤字", "TDEE", "間歇性斷食",
        "體態",
    ]),
    ("nutrition", [
        "飲食", "蛋白質", "碳水", "脂肪", "補給", "能量棒", "電解質",
        "賽前飲食", "恢復飲食", "巨量營養素",
    ]),
]

_DEFAULT_DOMAIN = "general_fitness"


def detect_domain(text: str) -> str:
    """Keyword-based domain detection. Returns one of the domain keys, or
    `general_fitness` as fallback. Order matters: injury is checked first
    so pain-related messages always get the disclaimer path."""
    if not text:
        return _DEFAULT_DOMAIN
    for domain, keywords in _DOMAIN_KEYWORDS:
        if any(kw in text for kw in keywords):
            logger.info(f"Domain detected: {domain}")
            return domain
    return _DEFAULT_DOMAIN


def coach_reply(
    user_text: str,
    user_id: str = "",
    owner: bool = False,
    client=None,
) -> str:
    """Compose a coach reply for one user message.

    - Detects domain from user_text.
    - Calls the LLM with the coach persona system prompt.
    - Non-owner + injury domain: prepends the mandatory medical disclaimer.
    - Owner mode: appends a debug footer with domain + token usage.
    """
    if client is None:
        from tools.gemini_client import get_llm_client
        client = get_llm_client()

    domain = detect_domain(user_text)
    kb_content = rag_retriever.search_fitness_knowledge(query=user_text, domain=domain)

    if kb_content:
        system_prompt = (
            f"{COACH_SYSTEM_PROMPT}\n\n"
            f"<knowledge_base domain=\"{domain}\">\n{kb_content}\n</knowledge_base>"
        )
    else:
        system_prompt = COACH_SYSTEM_PROMPT

    response = client.messages.create(
        model=os.environ.get("COACH_MODEL", "claude-sonnet-4-6"),
        max_tokens=400,
        system=system_prompt,
        messages=[{"role": "user", "content": user_text}],
    )

    text = response.content[0].text.strip()

    if rag_retriever.should_inject_disclaimer(user_text) and not owner:
        text = f"{MANDATORY_INJURY_DISCLAIMER}\n\n{text}"

    if owner:
        in_tok = getattr(response.usage, "input_tokens", 0)
        out_tok = getattr(response.usage, "output_tokens", 0)
        kb_tok = rag_retriever.estimate_tokens(kb_content)
        text = f"{text}\n\n[🏊 domain: {domain} | tokens: {in_tok}in/{out_tok}out | kb: {kb_tok}]"

    return text
