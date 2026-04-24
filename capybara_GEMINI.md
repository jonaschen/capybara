
***

# GEMINI.md — 水豚教練 (Capybara Coach) 開發與協作指南

這份文件提供了 Gemini 在協助開發 `capybara-coach` 專案時的核心準則、系統架構與人格設定。

## 1. 專案概述與核心哲學 (Project Overview & Philosophy)

**水豚教練**是一個透過 LINE Messaging API 提供個人化健身指導的 Chatbot。
它與 Hana 專案共用同一個 GCP 專案 (`hanana-491223`) 與底層邏輯，但擁有獨立的 LINE Channel、GCS Bucket (`capybara-profiles`) 與 Cloud Run 服務。

**核心哲學：穩如泰山、精準科學、溫暖接住。**
* **目標受眾：** 需要結構化運動指引的成年人。訓練目標以**增肌減脂、全面性的健康平衡**為主，而非單純追求極致的配速或競賽成績；同時也涵蓋初中階三鐵愛好者的週期訓練。
* **溝通準則：** 說具體的事，不說感覺的事。不放大（不誇張讚美），不縮小（沒練就是沒練）。專業詞彙用了就解釋。最多在句尾使用一個 🐾 emoji。
* **紅線 (Prohibited)：** 絕不進行醫療診斷、絕不推薦特定品牌補給品、絕不情緒勒索或給予有毒的正能量（Toxic positivity）。遇到傷痛一律加上免責聲明並建議尋求物理治療。

## 2. 系統架構與部署 (Architecture & Deployment)

專案採用輕量化、高效率的微服務架構，部署於 Google Cloud Platform：

* **基礎設施：** * 後端服務部署於 GCP Cloud Run (`asia-east1` 區域)，服務名稱為 `capybara-backend`。
    * 排程推播由 Cloud Scheduler 觸發 (`07:00` 早晨提示、`21:00` 晚間陪伴)。
* **狀態路由 (State Machine)：**
    * `IDLE`: 日常問答、課表查詢。
    * `ONBOARDING`: 新手對話式資料收集（產出 Profile）。
    * `COACHING`: 深入的課表討論與動態調整。
* **容器化原則：** 保持 Docker Image 極度輕量化。延續過去將 Image 從 4.5GB 壓縮至 249MB 的最佳實踐，確保 Cloud Run 的冷啟動時間小於 5 秒。

## 3. 用戶資料與記憶結構 (Per-User Data Structure)

不使用複雜的關聯式資料庫，所有學員狀態皆以檔案形式結構化儲存於 GCS (`gs://capybara-profiles/{user_id}/`)，確保狀態分離與版本控制：

* **`athlete_profile.md` (學員輪廓 - 不可變更)：**
    * Onboarding 階段結束後生成，包含目標、當前體能、可用時間、器材與舊傷。
    * **原則：** 寫入後即 Read-only。若有體能或目標改變，以 append 方式新增版本紀錄，不覆寫原始資料。
* **`training_plan.md` (動態課表)：**
    * 目前的訓練計畫，由教練依據進度動態調整（例如：因 DOMS 或睡眠不足自動降階）。
* **`progress_log.json` (訓練日誌)：**
    * 滾動記錄最近 30 次的打卡、回饋與情緒狀態（例如：RPE 感受、疲勞度）。
* **`preferences.json` (偏好設定)：**
    * 包含單位偏好、推播時間與固定休息日。

## 4. 知識庫設計 (Knowledge Base & RAG)

捨棄沉重的 Vector Search，採用**輕量級目錄對應 (Slim RAG)**。現代 LLM 已具備充足的基礎健身知識，我們只將領域特定 (Domain-specific) 或在地化資訊切塊儲存：

* **領域偵測 (Domain Detection)：** 透過關鍵字陣列將用戶對話分類為 `triathlon`（三鐵）、`strength`（增肌重訓）、`fat_loss`（減脂）、`recovery`（恢復）、`injury`（傷痛）或 `general_fitness`。
* **檔案結構擷取：** 依據判定的 Domain，直接讀取 `knowledge_base/` 下對應的 Markdown 檔案（例如 `tri_002_zone_training.md` 或 `str_001_progressive_overload.md`）作為 Context 注入。

## 5. 給 Gemini 的開發與協作原則

在協助開發與重構 `capybara-coach` 時，需嚴格遵守以下開發流程：

1.  **Test First (測試先行)：** 所有新功能必須先寫測試。使用 `.venv` 環境執行 `pytest`。
2.  **外部依賴全面 Mock：** 開發階段絕不呼叫真實的 GCS、LINE 或 LLM API。依賴 `mocks/gcs_mock.py`、`line_mock.py` 與 `gemini_mock.py` 進行邏輯驗證。
3.  **自動修復限制：** 遇到測試失敗，最多嘗試修復 3 次。若仍未通過，必須將問題輸出至 `reports/blocked.md` 並停止執行，等待人類介入。
4.  **無痛對接 AI 代理邏輯：** 在開發 System Prompt 與 Tools 時，預留清晰的介面，以便未來可輕易整合 MCP (Model Context Protocol) 標準，讓水豚教練能操作更複雜的運算工具（如高階 TDEE 或週期化課表生成器）。
