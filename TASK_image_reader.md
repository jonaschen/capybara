# TASK: 圖片訊息處理——訓練數據截圖解讀

**交辦給：** Claude Code  
**日期：** 2026-04-26  
**Repo：** `capybara-coach`（現有 repo）  
**預估工時：** 2–3 小時  
**前置條件：** Phase 5 完成，`tools/voice.py` 的自稱規則已就位

---

## 功能說明

用戶把跑步 App 的訓練數據截圖（配速圖、心率區間圖、訓練記錄表格）傳給卡皮，
卡皮結合對這個用戶的了解給出個人化的回應。

**支援的圖片類型：**
- 配速趨勢圖（折線圖、區段配速）
- 訓練記錄表格（含數據欄位的截圖）

**單次 Gemini API call 架構：**

Gemini 原生多模態——圖片理解 + athlete_profile 注入 + 卡皮回應，
全部在一個 Gemini API call 裡完成。不需要兩個模型切換。

```
用戶傳圖片
    ↓
緩衝訊息：「收到了，稍等卡皮看看 🐾」（reply token，立刻發出）
    ↓
下載圖片 → 暫存
    ↓
Gemini：圖片 + athlete_profile + CAPYBARA_VOICE → 卡皮的個人化回應
    ↓
push_text 發出（reply token 已用完）
    ↓
對話歷史更新 + 暫存檔刪除
```

---

## 需要修改的檔案

### `tools/line_webhook.py`

新增 `ImageMessageContent` handler：

```python
from linebot.v3.webhooks import ImageMessageContent
from linebot.v3.messaging import MessagingApiBlob

@handler.add(MessageEvent, message=ImageMessageContent)
async def handle_image_message(event: MessageEvent):
    user_id = event.source.user_id

    # 1. 立刻發緩衝訊息（用掉 reply token）
    reply_text(event.reply_token, "收到了，稍等卡皮看看 🐾")

    tmp_path = None
    try:
        # 2. 從 LINE 下載圖片
        with ApiClient(config) as api_client:
            blob_api = MessagingApiBlob(api_client)
            image_content = blob_api.get_message_content(event.message.id)

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(image_content)
            tmp_path = f.name

        # 3. Gemini 單次 call：讀圖 + 個人化回應
        reply = analyze_training_image(tmp_path, user_id)

    finally:
        # 4. 確保刪除暫存檔（無論成功失敗）
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if reply is None:
        push_text(user_id,
            "卡皮看了一下，這張圖好像不是訓練數據？\n"
            "如果是配速圖或訓練記錄，可以試著重傳，"
            "或是直接跟卡皮說數字也行。🐾")
        return

    # 5. 更新對話歷史
    _append_history(user_id, "user", "[傳來訓練數據截圖]")
    _append_history(user_id, "assistant", reply)

    push_text(user_id, reply)
```

---

### `tools/image_reader.py`（新建）

```python
# tools/image_reader.py
"""
用 Gemini Vision 解讀訓練截圖，結合 athlete_profile 生成卡皮的個人化回應。
單次 Gemini API call：圖片理解 + 脈絡注入 + 回應生成一次完成。
"""
import base64
import os
import google.generativeai as genai

from tools.gcs_profile import load_profile
from tools.voice import CAPYBARA_VOICE


def _build_image_prompt(athlete_profile: str) -> str:
    return f"""你是卡皮教練，一個了解用戶的教練朋友。

{CAPYBARA_VOICE}

以下是這個用戶的訓練背景資料：
<athlete_profile>
{athlete_profile if athlete_profile else "尚無訓練背景資料。"}
</athlete_profile>

用戶剛傳來一張圖片，請你：

1. 先判斷這張圖片是不是訓練數據
   （配速圖、心率圖、訓練記錄表格都算）。
   如果不是，只回覆：NOT_TRAINING_DATA

2. 如果是訓練數據，用卡皮教練的方式回應：
   - 說你看到了什麼（一句話，具體說數字或趨勢）
   - 結合你對這個用戶的了解給出解讀
     （不只看數字，也考慮他的目標和你知道的狀況）
   - 如果有值得注意的地方，說一件最重要的
   - 視情況問一個問題，讓對話繼續

回應不超過 150 字。不要說「根據圖片顯示」這種機器語言。
記住你是卡皮教練，用第三人稱自稱。"""


def analyze_training_image(image_path: str, user_id: str) -> str | None:
    """
    讀取圖片，結合 athlete_profile，
    用 Gemini 單次 call 生成卡皮的個人化回應。

    回傳：回應文字，或 None（若圖片不是訓練數據或發生錯誤）
    """
    try:
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model_id = os.environ.get("GEMINI_MODEL_ID", "gemini-2.0-flash")
        model = genai.GenerativeModel(model_id)

        # 讀取圖片
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()

        suffix = image_path.lower().rsplit(".", 1)[-1]
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "gif": "image/gif",
                "webp": "image/webp"}.get(suffix, "image/jpeg")

        # 載入 athlete_profile
        athlete_profile = load_profile(user_id, "athlete_profile.md") or ""

        # 單次 Gemini call
        response = model.generate_content([
            {"mime_type": mime, "data": image_data},
            _build_image_prompt(athlete_profile),
        ])

        result = response.text.strip()

        if result == "NOT_TRAINING_DATA" or "NOT_TRAINING_DATA" in result:
            return None

        return result

    except Exception as e:
        print(f"[image_reader] Gemini 解析失敗（非致命）：{e}")
        return None
```

---

## 測試規格

```python
# tests/unit/test_image_reader.py

from unittest.mock import patch, MagicMock
from tools.image_reader import analyze_training_image, _build_image_prompt


def test_prompt_includes_athlete_profile():
    profile = "目標：完成台東 226\n每週訓練：4 天"
    prompt = _build_image_prompt(profile)
    assert "台東 226" in prompt
    assert "150 字" in prompt


def test_prompt_empty_profile_has_fallback():
    prompt = _build_image_prompt("")
    assert "尚無訓練背景資料" in prompt


def test_returns_none_for_non_training_image(tmp_path):
    fake_img = tmp_path / "photo.jpg"
    fake_img.write_bytes(b"fake image data")

    mock_response = MagicMock()
    mock_response.text = "NOT_TRAINING_DATA"

    with patch("tools.image_reader.genai") as mock_genai, \
         patch("tools.image_reader.load_profile", return_value=""):
        mock_genai.GenerativeModel.return_value.generate_content \
            .return_value = mock_response
        result = analyze_training_image(str(fake_img), "user1")

    assert result is None


def test_returns_reply_for_training_image(tmp_path):
    fake_img = tmp_path / "run.jpg"
    fake_img.write_bytes(b"fake image data")

    mock_response = MagicMock()
    mock_response.text = (
        "卡皮教練看到你這次跑了 10K，後半段配速掉了不少。"
        "你上週說睡眠不太好，恢復可能還不夠——"
        "今天的表現其實比卡皮預期的好。🐾"
    )

    with patch("tools.image_reader.genai") as mock_genai, \
         patch("tools.image_reader.load_profile",
               return_value="目標：台東 226"):
        mock_genai.GenerativeModel.return_value.generate_content \
            .return_value = mock_response
        result = analyze_training_image(str(fake_img), "user1")

    assert result is not None
    assert "卡皮教練" in result


def test_returns_none_on_gemini_failure(tmp_path):
    """Gemini 失敗時靜默回傳 None，不 crash"""
    fake_img = tmp_path / "run.jpg"
    fake_img.write_bytes(b"fake image data")

    with patch("tools.image_reader.genai") as mock_genai, \
         patch("tools.image_reader.load_profile", return_value=""):
        mock_genai.GenerativeModel.return_value.generate_content \
            .side_effect = Exception("API error")
        result = analyze_training_image(str(fake_img), "user1")

    assert result is None


# tests/integration/test_image_handler.py

def test_buffer_message_sent_immediately(mock_line, mock_gemini,
                                          mock_gcs, fake_image_event):
    """reply token 立刻用於緩衝訊息"""
    handle_image_message(fake_image_event(user_id="test"))
    assert "稍等卡皮看看" in mock_line.replies[0]
    assert "🐾" in mock_line.replies[0]


def test_non_training_image_sends_friendly_message(
        mock_line, mock_gemini, mock_gcs, fake_image_event):
    """非訓練圖片 → 友善說明，不 crash"""
    mock_gemini.set_response("NOT_TRAINING_DATA")
    handle_image_message(fake_image_event(user_id="test"))
    push = mock_line.pushes.get("test", [])
    assert len(push) == 1
    assert "不是訓練數據" in push[0]


def test_training_image_sends_personalized_reply(
        mock_line, mock_gemini, mock_gcs, fake_image_event):
    """訓練截圖 → 個人化回應包含用戶背景"""
    mock_gcs.seed("test/athlete_profile.md", "目標：完成台東 226")
    mock_gemini.set_response(
        "卡皮教練看到你這次 10K 後半掉速，"
        "結合你報名台東的目標，有氧基礎還需要加強。🐾"
    )
    handle_image_message(fake_image_event(user_id="test"))
    push = mock_line.pushes.get("test", [])
    assert "台東" in push[0]


def test_tmp_file_deleted_after_processing(
        mock_line, mock_gemini, mock_gcs, fake_image_event, tmp_path):
    """無論成功失敗，暫存圖片必須刪除"""
    mock_gemini.set_response("NOT_TRAINING_DATA")
    handle_image_message(fake_image_event(user_id="test"))
    # 暫存目錄裡不應有殘留 .jpg
    import glob
    leftovers = glob.glob("/tmp/*.jpg")
    assert len(leftovers) == 0


def test_conversation_history_updated(
        mock_line, mock_gemini, mock_gcs, fake_image_event):
    """成功解讀後，對話歷史要更新"""
    mock_gcs.seed("test/athlete_profile.md", "目標：減脂")
    mock_gemini.set_response("卡皮教練看了你的數據。🐾")
    handle_image_message(fake_image_event(user_id="test"))
    history = _conversation_history.get("test", [])
    assert any("[傳來訓練數據截圖]" in m["content"]
               for m in history if m["role"] == "user")
```

---

## 暫停條件

- 暫存圖片刪除邏輯失敗（安全問題，不能繞過）
- `tools/voice.py` 的 `CAPYBARA_VOICE` import 失敗
- 三輪修改後整合測試仍無法通過

---

## 不需要暫停，自主處理

- `_build_image_prompt()` 的措辭微調（不改結構）
- 新增更多圖片類型的測試案例
- `fake_image_event` helper 的實作細節

---

## 完成標準

- [ ] 所有新測試通過，無 regression
- [ ] 手動測試：傳一張 Garmin / Nike Run / Strava 配速截圖
  - 收到「收到了，稍等卡皮看看 🐾」
  - 3–5 秒後收到包含用戶背景脈絡的回應
- [ ] 手動測試：傳一張訓練記錄表格截圖，確認也能正確解讀
- [ ] 傳一張食物照，確認卡皮說看不懂
- [ ] 確認暫存目錄裡沒有殘留圖片

---

## 給 Claude Code 的一句話

Gemini 同時負責看圖和說話。  
`athlete_profile` 要在同一個 call 裡注入，  
這樣 Gemini 才能說出「你報名台東 226」這種只有卡皮知道的話。

---

## 需要修改的檔案

### `tools/line_webhook.py`

新增 `ImageMessageContent` handler：

```python
from linebot.v3.webhooks import ImageMessageContent
from linebot.v3.messaging import MessagingApiBlob

@handler.add(MessageEvent, message=ImageMessageContent)
async def handle_image_message(event: MessageEvent):
    user_id = event.source.user_id

    # 1. 立刻發緩衝訊息（用掉 reply token）
    reply_text(event.reply_token, "收到了，稍等卡皮看看 🐾")

    tmp_path = None
    try:
        # 2. 從 LINE 下載圖片
        with ApiClient(config) as api_client:
            blob_api = MessagingApiBlob(api_client)
            image_content = blob_api.get_message_content(event.message.id)

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(image_content)
            tmp_path = f.name

        # 3. Gemini 讀圖 → 結構化數據
        data = extract_training_data_from_image(tmp_path)

    finally:
        # 4. 確保刪除暫存檔
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if data is None:
        push_text(user_id,
            "卡皮看了一下，這張圖片好像不是訓練數據？"
            "如果是配速圖或訓練記錄，可以試著重傳，"
            "或是直接跟卡皮說數字也行。🐾")
        return

    # 5. Claude 個人化解讀（走現有的 coach_reply 流程）
    await handle_training_data_reply(user_id, data)
```

---

### `tools/image_reader.py`（新建）

```python
# tools/image_reader.py
"""
用 Gemini Vision 從訓練截圖中萃取結構化數據。
支援：配速圖、心率區間圖、訓練記錄表格。
不支援：非訓練相關圖片（回傳 None）。
"""
import json
import os
import base64
import google.generativeai as genai


EXTRACTION_PROMPT = """
你是一個運動數據萃取工具。
請從這張圖片中萃取訓練數據，以 JSON 格式回傳。

如果這張圖片不包含任何運動訓練數據，回傳：{"is_training_data": false}

如果包含訓練數據，回傳以下格式（只填有的欄位，沒有的填 null）：
{
  "is_training_data": true,
  "activity_type": "跑步 | 騎車 | 游泳 | 其他",
  "date": "YYYY-MM-DD 或 null",
  "duration_min": null,
  "distance_km": null,
  "avg_pace_min_per_km": null,
  "avg_heart_rate": null,
  "max_heart_rate": null,
  "heart_rate_zones": {
    "zone1_pct": null,
    "zone2_pct": null,
    "zone3_pct": null,
    "zone4_pct": null,
    "zone5_pct": null
  },
  "pace_trend": "穩定 | 後半掉速 | 前快後慢 | 負分跑法 | 無法判斷",
  "calories": null,
  "elevation_gain_m": null,
  "notes": "圖片中其他值得注意的資訊"
}

只輸出 JSON，不要輸出任何說明文字。
"""


def extract_training_data_from_image(image_path: str) -> dict | None:
    """
    讀取圖片，回傳訓練數據 dict。
    若圖片不是訓練數據，或解析失敗，回傳 None。
    """
    try:
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel(
            os.environ.get("GEMINI_MODEL_ID", "gemini-2.0-flash")
        )

        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()

        # 判斷 mime type
        suffix = image_path.lower().split(".")[-1]
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "gif": "image/gif",
                "webp": "image/webp"}.get(suffix, "image/jpeg")

        response = model.generate_content([
            {"mime_type": mime, "data": image_data},
            EXTRACTION_PROMPT,
        ])

        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        data = json.loads(raw)

        if not data.get("is_training_data"):
            return None

        return data

    except Exception as e:
        print(f"[image_reader] Gemini 解析失敗（非致命）：{e}")
        return None
```

---

### `tools/coach_reply.py` 新增函式

在現有的 `coach_reply.py` 加入 `build_image_analysis_prompt()`，
讓 Claude 在解讀數據時能結合 athlete_profile：

```python
def build_image_analysis_prompt(
    training_data: dict,
    athlete_profile: str,
    conversation_history: list[dict],
) -> str:
    """
    把 Gemini 萃取的訓練數據、用戶 profile、對話歷史
    組合成給 Claude 的 prompt，讓卡皮給出個人化解讀。
    """
    data_summary = _format_training_data(training_data)

    return f"""你是卡皮教練，正在解讀用戶剛傳來的訓練數據截圖。

以下是這個用戶的訓練背景資料：
<athlete_profile>
{athlete_profile}
</athlete_profile>

Gemini 從截圖中萃取的數據：
<training_data>
{data_summary}
</training_data>

請用卡皮教練的方式回應：
1. 先說你看到了什麼（一句話，具體）
2. 結合你對這個用戶的了解給出解讀
   （不只看數字，也考慮他最近說的生活狀況）
3. 如果有值得注意的地方，說一件最重要的就好
4. 視情況問一個問題，讓對話繼續

語氣遵守 CAPYBARA_VOICE 規則。
回應不超過 150 字。"""


def _format_training_data(data: dict) -> str:
    """把 dict 格式化成易讀的文字，給 Claude 當輸入。"""
    lines = []
    if data.get("activity_type"):
        lines.append(f"活動類型：{data['activity_type']}")
    if data.get("distance_km"):
        lines.append(f"距離：{data['distance_km']} km")
    if data.get("duration_min"):
        lines.append(f"時間：{data['duration_min']} 分鐘")
    if data.get("avg_pace_min_per_km"):
        lines.append(f"平均配速：{data['avg_pace_min_per_km']} 分/km")
    if data.get("avg_heart_rate"):
        lines.append(f"平均心率：{data['avg_heart_rate']} bpm")
    if data.get("pace_trend"):
        lines.append(f"配速趨勢：{data['pace_trend']}")
    zones = data.get("heart_rate_zones", {})
    zone_parts = []
    for z in ["zone1", "zone2", "zone3", "zone4", "zone5"]:
        pct = zones.get(f"{z}_pct")
        if pct is not None:
            zone_parts.append(f"Z{z[-1]}:{pct}%")
    if zone_parts:
        lines.append(f"心率區間：{' / '.join(zone_parts)}")
    if data.get("notes"):
        lines.append(f"備註：{data['notes']}")
    return "\n".join(lines) if lines else "無法解析具體數據"
```

---

### `tools/line_webhook.py` 補充 `handle_training_data_reply()`

```python
async def handle_training_data_reply(user_id: str, data: dict):
    """
    取得 athlete_profile + 對話歷史，
    用 Claude 生成卡皮的個人化解讀，
    透過 push_text 發出。
    """
    from tools.gcs_profile import load_profile
    from tools.coach_reply import build_image_analysis_prompt
    from tools.bedrock_claude_client import get_llm_client

    athlete_profile = load_profile(user_id, "athlete_profile.md") or ""
    history = _conversation_history.get(user_id, [])

    prompt = build_image_analysis_prompt(data, athlete_profile, history)

    client = get_llm_client(provider="claude")
    response = client.messages.create(
        max_tokens=300,
        system=prompt,
        messages=[{
            "role": "user",
            "content": "請解讀這份訓練數據。"
        }],
    )
    reply = response.content[0].text.strip()

    # 把這次互動加進對話歷史
    _append_history(user_id, "user", "[傳來訓練數據截圖]")
    _append_history(user_id, "assistant", reply)

    push_text(user_id, reply)
```

---

## 測試規格

```python
# tests/unit/test_image_reader.py

from mocks.gemini_mock import MockGeminiClient
from tools.image_reader import extract_training_data_from_image


def test_returns_none_for_non_training_image(mock_gemini, tmp_path):
    """非訓練數據圖片回傳 None"""
    mock_gemini.set_response('{"is_training_data": false}')
    fake_img = tmp_path / "photo.jpg"
    fake_img.write_bytes(b"fake")
    result = extract_training_data_from_image(str(fake_img))
    assert result is None


def test_extracts_running_data(mock_gemini, tmp_path):
    """跑步數據截圖正確萃取"""
    mock_gemini.set_response('''{
        "is_training_data": true,
        "activity_type": "跑步",
        "distance_km": 10.2,
        "avg_pace_min_per_km": 5.8,
        "avg_heart_rate": 152,
        "pace_trend": "後半掉速"
    }''')
    fake_img = tmp_path / "run.jpg"
    fake_img.write_bytes(b"fake")
    result = extract_training_data_from_image(str(fake_img))
    assert result is not None
    assert result["activity_type"] == "跑步"
    assert result["pace_trend"] == "後半掉速"


def test_returns_none_on_gemini_failure(mock_gemini, tmp_path):
    """Gemini 失敗時靜默回傳 None，不 crash"""
    mock_gemini.raise_on_call(Exception("API error"))
    fake_img = tmp_path / "run.jpg"
    fake_img.write_bytes(b"fake")
    result = extract_training_data_from_image(str(fake_img))
    assert result is None


def test_tmp_file_deleted_after_processing(mock_gemini, tmp_path):
    """處理完後暫存圖片必須刪除"""
    mock_gemini.set_response('{"is_training_data": false}')
    fake_img = tmp_path / "run.jpg"
    fake_img.write_bytes(b"fake")
    # 模擬 webhook handler 的暫存 + 刪除邏輯
    import os
    path = str(fake_img)
    try:
        extract_training_data_from_image(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)
    assert not os.path.exists(path)


# tests/unit/test_coach_reply_image.py

from tools.coach_reply import build_image_analysis_prompt, _format_training_data


def test_format_includes_pace_trend():
    data = {
        "is_training_data": True,
        "activity_type": "跑步",
        "distance_km": 10.0,
        "avg_pace_min_per_km": 5.5,
        "pace_trend": "後半掉速",
    }
    result = _format_training_data(data)
    assert "後半掉速" in result
    assert "10.0 km" in result


def test_prompt_includes_athlete_profile():
    data = {"is_training_data": True, "activity_type": "跑步",
            "distance_km": 5.0}
    profile = "目標：完成台東 226\n每週訓練：4 天"
    prompt = build_image_analysis_prompt(data, profile, [])
    assert "台東 226" in prompt
    assert "150 字" in prompt  # 字數限制在 prompt 裡


# tests/integration/test_image_handler.py

def test_non_training_image_sends_friendly_message(
        mock_line, mock_gemini, mock_gcs, fake_image_event):
    """非訓練圖片 → 卡皮說看不懂，不 crash"""
    mock_gemini.set_response('{"is_training_data": false}')
    handle_image_message(fake_image_event(user_id="test"))
    assert "不是訓練數據" in mock_line.pushes["test"][0]


def test_training_image_sends_personalized_reply(
        mock_line, mock_gemini, mock_claude, mock_gcs, fake_image_event):
    """訓練截圖 → 卡皮給出個人化解讀"""
    mock_gemini.set_response('''{
        "is_training_data": true,
        "activity_type": "跑步",
        "distance_km": 10.0,
        "pace_trend": "後半掉速"
    }''')
    mock_gcs.seed("test/athlete_profile.md", "目標：完成台東 226")
    mock_claude.set_response("你這次後半掉速明顯，可能是有氧基礎還需要加強。🐾")

    handle_image_message(fake_image_event(user_id="test"))

    # 先發緩衝訊息（reply）
    assert "稍等卡皮看看" in mock_line.replies[0]
    # 再發個人化回應（push）
    assert "台東" in mock_claude.calls[0]["system"] or \
           "掉速" in mock_line.pushes["test"][0]
```

---

## 暫停條件

- `tools/image_reader.py` 三輪修改後仍無法正確 mock Gemini Vision
- 修改 `tools/voice.py` 的自稱規則（設計決策，問人）
- 暫存圖片刪除邏輯失敗（安全問題，不能繞過）

---

## 不需要暫停，自主處理

- `_format_training_data()` 的輸出格式微調
- `EXTRACTION_PROMPT` 的措辭優化（不改結構）
- 新增更多測試案例（不同運動類型的截圖格式）
- `mock_gemini` 的 vision 介面設計

---

## 完成標準

- [ ] 所有新測試通過，無 regression
- [ ] 手動測試：傳一張 Garmin / Nike Run / Strava 截圖給卡皮
  - 確認收到「稍等卡皮看看 🐾」緩衝訊息
  - 確認 3–5 秒後收到卡皮的個人化解讀
  - 確認回應中有引用 athlete_profile 的內容
- [ ] 傳一張非訓練圖片（食物照、風景照），確認卡皮說看不懂
- [ ] 確認 Cloud Run 的暫存目錄裡沒有殘留的圖片檔

---

## 給 Claude Code 的一句話

Gemini 看圖，Claude 認識人。這個分工是這個功能的核心。
兩個模型的職責不要混在一起——Gemini 只輸出結構化數據，
Claude 才是那個知道「你報名台東 226」的人。
