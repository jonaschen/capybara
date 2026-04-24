***

# GEMINI.md — 水豚教練 (Capybara Coach) 開發與協作指南

這份文件提供了 Gemini 在協助開發 `capybara-coach` 專案時的核心準則、系統架構與人格設定。

## 1. 專案概述與核心哲學 (Project Overview & Philosophy)

**水豚教練 (Shuǐtún Jiàoliàn)** 是一個透過 LINE Messaging API 提供個人化健身指導的 Chatbot。
它與 Hana 專案共用同一個 GCP 專案 (`hanana-491223`)，但擁有獨立的 LINE Channel、GCS Bucket (`capybara-profiles`) 與 Cloud Run 服務。

### 人格設定 (Persona)
水豚教練很少催你，但他說的話你會想記下來。他知道你偷懶了，但他不說；他知道你今天很拚，他也不誇。他只是告訴你明天要做什麼，然後讓你去睡覺。🐾

### 溝通準則 (Communication Guidelines)
* **說具體的事，不說感覺的事：** 例如 "今天跑 30 分鐘 Zone 2" 而非 "今天動一動"。
* **不放大，不縮小：** 沒練就是沒練，練了就是練了，不需要多餘的情緒。
* **專業詞彙即時解釋：** 第一次提到專業術語（如 Zone 2）需附帶簡單說明。
* **脈絡連貫：** 自然引用用戶歷史記錄，不每次重頭開始。
* **Emoji 限制：** 最多在句尾使用一個 🐾 emoji。

### 紅線與禁令 (Prohibited)
* **嚴禁醫療診斷：** "膝蓋外側痛可能的原因..." 需改為建議諮詢物理治療師。
* **嚴禁推薦特定品牌補給品。**
* **嚴禁情緒勒索或有毒正能量：** 不說 "加油！你一定可以的！"。
* **嚴禁絕對飲食規則：** 不給予如 "絕對不能吃碳水" 的指令。
* **強制免責聲明：** 討論傷痛或醫療話題時，必須自動注入免責聲明。

## 2. 系統架構與部署 (Architecture & Deployment)

專案採用 FastAPI + Cloud Run 的微服務架構。

### 核心組件
* **Webhook (`tools/line_webhook.py`):** 進入點，負責狀態路由。
* **核心邏輯 (`tools/coach_reply.py`):** 領域偵測、RAG 注入、LLM 呼叫。
* **狀態機 (State Machine):**
    * `IDLE`: 一般問答、課表查詢。
    * `ONBOARDING`: 對話式收集資料，生成 `athlete_profile.md`。
    * `COACHING`: 深度課表討論與調整模式。

### 部署資訊
* **GCP Project:** `hanana-491223`
* **Cloud Run:** `capybara-backend` (Region: `asia-east1`)
* **Cloud Scheduler:**
    * `capybara-push-morning` (07:00): 今日任務提示。
    * `capybara-push-evening` (21:00): 晚間鼓勵。

## 3. 用戶資料與記憶結構 (Per-User Data Structure)

所有學員狀態儲存於 GCS (`gs://capybara-profiles/{user_id}/`)。

* **`athlete_profile.md` (不可變更):**
    * 包含：目標、體能等級、可用時間、器材、舊傷、賽事目標。
    * **原則：** 寫入後即唯讀。修正資訊時以 append 方式記錄版本，不刪除舊資料。
* **`training_plan.md` (動態課表):** 目前的訓練計畫與調整記錄。
* **`progress_log.json`:** 滾動記錄最近 30 次的 check-ins。
* **`preferences.json`:** 單位、推播時間等偏好。

## 4. 知識庫與 RAG 設計 (Knowledge Base & RAG)

採用 **Slim RAG (輕量級目錄對應)** 模式。

### 領域偵測 (Domain Detection)
透過關鍵字將用戶意圖分類：
* `triathlon` (三鐵), `strength` (增肌), `fat_loss` (減脂), `nutrition` (營養), `recovery` (恢復), `injury` (傷痛)。

### RAG 擷取
依據領域偵測結果，從 `knowledge_base/` 對應目錄讀取 Markdown 內容作為 Context。

## 5. 開發與協作原則 (Development Principles)

### 開發流程
1. **Test First (測試先行):** 所有功能必須先有測試案例。
2. **外部依賴 Mock:** 開發時強制使用 `mocks/` 下的 Mock 物件（GCS, LINE, LLM）。
3. **自動修復限制:** 測試失敗最多嘗試修復 3 次。若失敗則寫入 `reports/blocked.md` 並停止，等待人類介入。
4. **回報機制:** 將需要人類決策的問題記錄於 `reports/human_checklist.md`。

### Gemini 專屬工具與目錄 (.gemini/)
* `.gemini/` 目錄專門存放由 Gemini 建立的輔助工具、腳本或專屬設定。
* 若在稽核、監控或開發過程中需要自訂工具（例如自訂爬蟲或檢測腳本），應將其放置於此目錄下，與專案核心邏輯（`tools/`）隔離。

### 環境變數 (Environment Variables)
* `LLM_PROVIDER`: `claude` (預設) 或 `gemini`。
* `GEMINI_API_KEY` & `GEMINI_MODEL_ID`: `gemini-2.0-flash`。
* `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET`, `OWNER_LINE_USER_ID`。
* `GCS_PROFILES_BUCKET`, `GOOGLE_CLOUD_PROJECT`。

### 停止條件 (Stop Conditions)
* 需真實 API Key 才能繼續時。
* 修改 `agents/onboarding/system_prompt.xml` 等核心設計文件時。
* 測試連續失敗 3 次。
