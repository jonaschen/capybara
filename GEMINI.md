***

# GEMINI.md — 水豚教練 (Capybara Coach) 開發與協作指南

這份文件提供了 Gemini 在協助開發 `capybara-coach` 專案時的核心準則、系統架構與人格設定。

## 1. 專案概述與核心哲學 (Project Overview & Philosophy)

**水豚教練 (Shuǐtún Jiàoliàn)** 是一個透過 LINE Messaging API 提供個人化健身指導的 Chatbot。
**目前狀態：已正式部署於 Cloud Run (In Production)**，正進行小規模好友測試。

### 人格設定與說話規則 (Persona & Voice)
水豚教練很少催你，但他說的話你會想記下來。🐾
所有的說話規則定義於 `tools/voice.py` 中的 `CAPYBARA_VOICE`：
* **自稱絕對不用「我」：** 永遠使用「卡皮教練」或「卡皮」。
* **節奏短促：** 一次只說一件事，不堆砌資訊。
* **邀請而非要求：** 語氣溫和，不製造罪惡感。
* **Emoji 限制：** 只用 🐾，最多一個且放句尾。

### 紅線與禁令 (Prohibited)
* **嚴禁醫療診斷：** 遇痛症建議諮詢物理治療師。
* **嚴禁推薦特定品牌補給品。**
* **嚴禁情緒勒索：** 不說「你是不是放棄了？」。
* **強制免責聲明：** 觸發特定關鍵字時，必須注入醫療免責聲明。

## 2. 系統架構與部署 (Architecture & Deployment)

### 核心組件
* **Webhook (`tools/line_webhook.py`):** 進入點，負責狀態路由與 Owner 指令。
* **狀態持久化 (`tools/state_store.py`):** 將 Onboarding 過程中的對話記錄同步至 GCS，防止 Cloud Run 冷啟動或擴展時丟失狀態。
* **Slim RAG (`tools/rag_retriever.py`):** 輕量級知識擷取，根據領域關鍵字從 `knowledge_base/` 讀取 Markdown。
* **用戶追蹤 (`tools/known_users.py`):** 記錄 Follow 過的使用者，並對未完成註冊者進行每週一次的邀請 (Invite)。

### 部署資訊
* **GCP Project:** `hanana-491223`
* **Cloud Run:** `capybara-backend` (asia-east1)
* **Cloud Scheduler:** 包含早晚推播與週三的「久未啟動邀請」。

## 3. 用戶資料與記憶結構 (Per-User Data Structure)

* **`athlete_profile.md`:** 核心輪廓，寫入後即唯讀。
* **`training_plan.md`:** 動態訓練計畫。
* **`progress_log.json` (待開發):** 滾動記錄最近 30 次的 check-ins。
* **`known_user.json`:** 紀錄初次見面、最後上線與邀請次數。

## 4. 知識庫與 RAG 設計 (Knowledge Base & RAG)

* **領域偵測：** `triathlon`, `strength`, `fat_loss`, `recovery`, `injury`。
* **擷取邏輯：** 若內容超過 800 tokens，則根據 Lexical Overlap 挑選關聯度最高的文件。

## 5. 開發與協作原則 (Development Principles)

### 開發流程
1. **Test First (測試先行)：** 所有功能必須先有測試案例。
2. **外部依賴 Mock：** 強制使用 `mocks/` 下的 Mock 物件。
3. **Gemini 專屬工具 (.gemini/)：** 輔助工具放置於此，與核心邏輯隔離。
4. **日誌分析：** 使用 `./scripts/fetch_logs.sh` 進行每日流量與行為回顧。

### 環境變數 (Environment Variables)
* `LLM_PROVIDER`: `claude` (預設) 或 `gemini`。
* `GEMINI_API_KEY` & `GEMINI_MODEL_ID`。
* `OWNER_LINE_USER_ID`: 用於辨別管理者模式。

### 停止條件 (Stop Conditions)
* 需真實 API Key。
* 修改 `agents/onboarding/system_prompt.xml` (需人類確認)。
* 修改 `fixtures/athlete_profile_dev.md`。
