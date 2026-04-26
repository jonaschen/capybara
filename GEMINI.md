***

# GEMINI.md — 水豚教練 (Capybara Coach) 開發與協作指南

這份文件提供了 Gemini 在協助開發 `capybara-coach` 專案時的核心準則、系統架構與人格設定。

## 1. 專案概述與核心哲學 (Project Overview & Philosophy)

**水豚教練 (Shuǐtún Jiàoliàn)** 是一個透過 LINE Messaging API 提供個人化健身指導的 Chatbot。
**目前狀態：Phase B-lite 已上線**。具體包含對話記憶與圖片解讀能力。

### 人格設定與說話規則 (Persona & Voice)
水豚教練的核心定位是：**「了解你的教練朋友。朋友先，教練後。」** 🐾
所有的說話規則定義於 `tools/voice.py`：
* **自稱絕對不用「我」：** 永遠使用「卡皮教練」或「卡皮」。
* **狀態優先規則：** 先問狀態（睡眠、壓力、生活），再談訓練。
* **數據要有脈絡：** HRV 或配速只是參考，用戶的生活脈絡才是課表調整的關鍵。
* **不製造罪惡感：** 沒練不是失敗，是資訊。不追究、不檢討。
* **Emoji 限制：** 只用 🐾，最多一個且放句尾。

### 紅線與禁令 (Prohibited)
* **嚴禁醫療診斷：** 遇痛症建議諮詢物理治療師。
* **嚴禁推薦品牌補給品。**
* **嚴禁情緒勒索：** 不說「你是不是放棄了？」。
* **不把數據當唯一判據：** 卡皮不盲從數據。

## 2. 系統架構與部署 (Architecture & Deployment)

### 核心組件
* **Webhook (`tools/line_webhook.py`):** 進入點，負責狀態路由、圖片處理與 Owner 指令。
* **圖片解讀 (`tools/image_reply.py`):** 多模態 (Multimodal) 分析，解讀運動 App 截圖並與用戶輪廓結合。
* **對話持久化 (`tools/chat_store.py`):** 儲存最近 10 輪 IDLE 模式對話，確保 Cloud Run 重啟後記憶不消失。
* **狀態持久化 (`tools/state_store.py`):** 儲存 Onboarding 過程中的對話記錄。
* **Slim RAG (`tools/rag_retriever.py`):** 輕量級知識擷取。

### 部署資訊
* **GCP Project:** `hanana-491223`
* **Cloud Run:** `capybara-backend`
* **Cloud Scheduler:** 早晚推播、週三邀請 (Stalled Users)。

## 3. 用戶資料與記憶結構 (Per-User Data Structure)

* **`athlete_profile.md`:** 核心輪廓（唯讀）。
* **`training_plan.md`:** 動態訓練計畫。
* **`chat_history.json`:** 最近 10 輪對話（含圖片摘要）。
* **`known_user.json`:** 用戶活躍度與邀請紀錄。
* **`progress_log.json` (待開發):** 記錄 check-ins 以供 Phase 5 自動觸發調整。

## 4. 知識庫與 RAG 設計 (Knowledge Base & RAG)

* **領域：** `triathlon`, `strength`, `fat_loss`, `recovery`, `injury`。
* **擷取邏輯：** 根據 Lexical Overlap 挑選文件，注入 `<knowledge_base>` 區塊。

## 5. 開發與協作原則 (Development Principles)

### 開發流程
1. **Test First (測試先行)：** 所有功能必須先有測試案例。
2. **多模態測試：** 涉及圖片的功能需驗證 LLM 是否正確產生 `<summary>` 標籤。
3. **Gemini 專屬工具 (.gemini/)：** 輔助工具放置於此。
4. **日誌分析：** 使用 `./scripts/fetch_logs.sh` 回顧 TURN 與 IMAGE 事件。

### 環境變數 (Environment Variables)
* `LLM_PROVIDER`: `claude` | `gemini`。
* `GEMINI_API_KEY` & `GEMINI_MODEL_ID`。
* `ANTHROPIC_API_KEY`。

### 停止條件 (Stop Conditions)
* 需真實 API Key。
* 修改 `agents/onboarding/system_prompt.xml`。
* 修改核心 Voice 規則。
