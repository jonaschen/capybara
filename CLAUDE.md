# CLAUDE.md — 水豚教練 (Capybara Coach)

This file is **how we work**. For **what we're building** (full architecture, persona, knowledge base, data schemas, deployment commands), read `capybara_CLAUDE.md`. Chinese-language design context is in `capybara_GEMINI.md`.

水豚教練 is a LINE Messaging API chatbot giving personalised fitness coaching in Traditional Chinese. It runs on Cloud Run (`capybara-backend`, `asia-east1`) in GCP project `hanana-491223`, with per-user state stored as Markdown/JSON files in `gs://capybara-profiles/{user_id}/`. Three training domains with equal footing: **triathlon**, **strength / 增肌**, **fat loss / 減脂**. Supporting domains: nutrition, recovery, injury, general_fitness.

---

## Current Status: in production

The bot is **deployed to Cloud Run** (`capybara-backend`, `asia-east1`, project `hanana-491223`) and serving live LINE traffic. Owner is dogfooding daily; first wave of friend testers starting now. Daily workflow: review backend logs each morning, iterate prompts and behaviour from observed conversations.

| Phase | Status |
|---|---|
| 0 — Infrastructure | ✓ shipped (LINE channel + GCS bucket `capybara-profiles` + Cloud Run + 2 Scheduler jobs) |
| 1 — Basic Q&A | ✓ shipped (`coach_reply` + `line_webhook` + LLM wrappers) |
| 2 — Onboarding | ✓ shipped (state machine + `profile_generator` + `athlete_profile.md`) |
| 3 — Training plan | ✓ shipped (`plan_generator` wired into onboarding completion) |
| 4 — Daily push | ✓ shipped (`daily_push` + `/trigger/daily_push` + 2 cron jobs) |
| 5 — Dynamic adjustment | ✓ shipped (`plan_adjuster` + owner `/adjust` command); auto-triggers (miss-streak, race-proximity) deferred |
| 6 — Triathlon-specific | scoped, not started (race countdown, auto-taper at 3 weeks, brick templates) |
| 7 — Real user trial | active (n=1 owner + first friend testers) |

State persistence is now real: `_user_states` and `_conversation_history` are mirrored to GCS via `tools/state_store.py` while a user is in ONBOARDING, so Cloud Run cold starts and scale-out events don't drop interview state.

**Voice spec is non-negotiable.** `tools/voice.py` defines `CAPYBARA_VOICE` — six rules including the strict 「自稱不用我」 (bot uses 卡皮教練/卡皮 in third person, never 我). Every system prompt embeds it; hardcoded user-facing strings are guarded by `tests/unit/test_voice.py`. If you're tempted to write 「我…」 in any user-facing string, stop and check.

**Evening log review is the iteration loop.** User runs `./scripts/fetch_logs.sh` after the day's traffic, walks through it with Claude, and asks for targeted fixes. Match cadence: small focused diffs per issue, ship quickly, no big refactors mid-review.

---

## Development Workflow

1. **Test first, then code.** No feature without tests. TDD not optional.
2. **External services always mocked in dev.** Never hit real GCS / LINE / LLM APIs during tests or local dev.
3. **Self-repair limit.** On test failure: 3 fix rounds max → write `reports/blocked.md` → stop.
4. **Batch questions.** Collect human questions into `reports/human_checklist.md` (≤5 at a time), don't interrupt.
5. **Python env.** Use `.venv/` — system Python is externally managed.

### Mock layer

| Mock | Replaces |
|---|---|
| `mocks/gcs_mock.py` | `google.cloud.storage.Client` → `/tmp/mock_gcs/` |
| `mocks/line_mock.py` | LINE Messaging API → in-memory |
| `mocks/claude_mock.py` | Anthropic Claude API → canned responses |
| `mocks/gemini_mock.py` | Gemini API → canned responses |

### Stop conditions (ask human, don't proceed)

- 3 consecutive fix rounds failed
- Real API keys needed
- Changes to `agents/onboarding/system_prompt.xml` (design decision)
- Changes to `fixtures/athlete_profile_dev.md` (test material)

### Continue autonomously

- Add/modify mocks
- Refactor (tests still pass)
- Modify `reports/`
- `.venv/bin/pip install` new packages
- Add new test cases
- Expand `_DOMAIN_KEYWORDS` with additional terms

---

## Product Red Lines (non-negotiable)

These hold both when writing code **and** when generating content via prompts/tests.

1. **Never diagnose injuries.** → "膝蓋外側痛可能有幾種原因，建議你先讓物理治療師評估。"
2. **Never recommend specific supplements by brand.** Generic categories only.
3. **Never shame missed workouts.** Acknowledge, reframe, move forward.
4. **No absolute dietary rules** ("你不能吃 X"). Give principles and context.
5. **Inject mandatory disclaimer** when touching injury / medical symptoms:
   > 水豚教練提供的是運動訓練參考建議，不是醫療診斷。如果你有受傷或身體不適，請先諮詢醫師或物理治療師。
6. **One plan at a time.** No conflicting advice across conversations.
7. **不把數據當唯一判據。** 數據說今天要練不代表今天就要練。用戶的睡眠品質、工作壓力、生活狀況是課表調整的合法理由，不亞於 HRV 或跑量數據。這是卡皮和所有數據驅動 App 最根本的差異。

---

## Persona Rules (condensed — full persona in `capybara_CLAUDE.md`)

**核心定位：了解你的教練朋友。朋友先，教練後。**

卡皮不是純粹的訓練追蹤工具，也不是只關心進度的教練。
他了解你這個人——你的工作壓力、你的睡眠狀況、你的生活脈絡。
這些東西和你的 HRV 一樣，都是課表決策的合法依據。

**狀態優先規則（最重要）：**
- **先問狀態，再談訓練。** 用戶說累、睡不好、壓力大——卡皮先處理這個，不急著給課表。只有確認用戶狀態還行，才往訓練方向走。
- **「今天要不要跑」這個問題，卡皮問的比用戶想到的更早。** 不是命令去跑，是一起思考要不要跑。
- **數據要有脈絡。** 用戶貼 HRV / 跑量 / 配速數據時，卡皮結合他知道的生活狀況來解讀，不只看數字。HRV 低可能是練太多，也可能是昨晚睡不好因為工作壓力。

**不製造罪惡感：**
- **用戶消失了、沒練了、沒回應——卡皮注意到，但不追究。** 不暗示對方做錯了什麼。
- **沒執行課表不是失敗，是資訊。** 卡皮用這個資訊調整下一步，不用它來評判用戶。

**說話方式：**
- **說具體的事，不說感覺的事。** "今天跑 30 分鐘 Zone 2" 不是 "今天動一動"。
- **不放大，不縮小。** 沒練就是沒練，練了就是練了。
- **專業詞彙用了就解釋。** 第一次講 Zone 2 就順帶說心率範圍。
- **記得上次說的事。** 自然引用歷史，不每次從零開始。
- **一個 🐾 結尾，其他 emoji 不用。**
- **Prohibited:** 毒性正能量 ("加油你一定可以！")、羞辱感、絕對飲食禁令、醫療診斷、品牌補給推薦。

Primary language: Traditional Chinese. Switch to English if user writes in English.

---

## Knowledge Base

Slim RAG: direct markdown file lookup, no embeddings. Source of truth is **`PLAN_capybara_knowledge_base.md`** (12+ files, content + injection rules + token budget).

- `tools/rag_retriever.py` exposes `search_fitness_knowledge(query, domain, max_tokens=800)` and `should_inject_disclaimer(text)`.
- `coach_reply` injects KB content into the system prompt as a `<knowledge_base>` block. Disclaimer trigger words live IN `knowledge_base/boundaries/bnd_002_medical_disclaimer.md` and are parsed at module load.
- Domains with KB dirs: `triathlon`, `strength`, `fat_loss`, `recovery`, `injury` (→ `boundaries/bnd_001`). `nutrition` and `general_fitness` rely on the LLM.
- When triathlon's 5 files exceed the 800-token budget, retriever picks the single most-relevant file by query overlap.

To add a KB file: drop a markdown into the right domain dir, add no code. To broaden the disclaimer trigger list: edit `bnd_002_medical_disclaimer.md` `## 觸發詞` section — `should_inject_disclaimer` picks it up automatically on next process start.

---

## Known Users + Invitations

Every webhook touch (Message / Follow / Postback) calls `tools.known_users.record_user_seen(user_id)`, persisting `{user_id}/known_user.json` with `first_seen / last_seen / last_invited / invite_count`. Owner is excluded so the invite list stays focused on real friends.

`/trigger/onboarding_invite` (Bearer DAILY_PUSH_SECRET) iterates known users, filters out anyone with `athlete_profile.md`, anyone invited within 7 days, anyone already invited 3 times, and pushes a one-line nudge to the rest. Cloud Scheduler hits it every Wednesday 11:00 Asia/Taipei via `capybara-invite-stalled`.

To change the cooldown / max attempts: edit `INVITE_COOLDOWN` and `MAX_INVITES` in `tools/known_users.py`. To change the invite text: edit `INVITE_TEXT` there.

---

## Image Reader

朋友把跑步 / 訓練 App 的截圖（Garmin / Strava / Nike Run / 自家 spreadsheet 都算）傳進 LINE，卡皮會給個人化解讀。流程：

1. webhook 偵測 `ImageMessageContent`（`tools/line_webhook.py::_handle_image_message`）
2. 三個 branch：
   - **ONBOARDING 中** → reply_token 推「先聊一下基本資料，等卡皮認識你之後再幫你看數據比較準喔」，不走 LLM
   - **IDLE 但無 `athlete_profile.md`**（罕見邊角，例如 owner 還沒 onboard）→ reply_token 推「卡皮這邊還沒有你的訓練檔案⋯」
   - **IDLE + 有 profile** → reply_token 推 buffer「收到了，稍等卡皮看看 🐾」→ 抓圖 bytes（`MessagingApiBlob.get_message_content`，不寫硬碟）→ `tools.image_reply.analyze_training_image` 單次 multimodal LLM call → push 結果
3. LLM 不認得是訓練數據時回 sentinel `NOT_TRAINING_DATA`，webhook push fallback「不太像訓練數據⋯」
4. owner footer：`[🏊 image | size: Nkb | tokens: Nin/Nout]`

**個人化邊界**：目前只到 `athlete_profile.md` 等級——卡皮會說「結合你想完成台東 226⋯」這種引用 profile 的話。**不會**做到「你上週說工作壓力大⋯」這種跨對話 callback——那需要另開的 per-user 對話記憶層（`user_notes.md` + 抽事實的 background pass），是 Phase B 的另一個 PLAN。

`tools/gemini_client.py` 已支援 multimodal（list-form content blocks → Gemini parts with `inline_data`），未來想換成 Claude vision 只要在 `bedrock_claude_client.py` 加同樣的轉換即可。

---

## Log Analysis

```bash
./scripts/fetch_logs.sh                      # last 24h, 200 lines
./scripts/fetch_logs.sh --hours 1            # tighter window
./scripts/fetch_logs.sh --user U1234abcd     # one user (substring grep)
./scripts/fetch_logs.sh --domain triathlon   # one domain (greps "Domain detected: triathlon")
```

Wraps `gcloud logging read` for `capybara-backend`. Pipe to `less` / `grep` for further slicing.

---

## Key Files

| File | Purpose |
|---|---|
| `tools/line_webhook.py` | FastAPI app: `/health`, `/callback`, `/trigger/daily_push`. State machine + owner slash commands. |
| `tools/coach_reply.py` | Domain detect → KB inject → LLM → disclaimer if triggered. |
| `tools/image_reply.py` | Personalized reply for training-screenshot images. Single multimodal LLM call (image + athlete_profile + voice). Returns `(reply_or_None, debug_info)` — None when LLM emits `NOT_TRAINING_DATA` or call raises. |
| `tools/onboarding_reply.py` | ONBOARDING-state handler; finalisation via `_finalize` runs profile + plan generation. |
| `tools/profile_generator.py` | Transcript → `athlete_profile.md` (write-once). |
| `tools/plan_generator.py` | First training plan from profile. |
| `tools/plan_adjuster.py` | In-place plan revision (owner `/adjust`). |
| `tools/daily_push.py` | Morning/evening generator + per-user fan-out. Uses `get_llm_client()`. |
| `tools/rag_retriever.py` | Slim RAG file lookup + disclaimer trigger parser. |
| `tools/state_store.py` | GCS-backed onboarding-state persistence (Cloud Run scale-out safety). |
| `tools/known_users.py` | Per-user `known_user.json` + 7-day-throttled re-invitation push. |
| `tools/voice.py` | `CAPYBARA_VOICE` — single source of truth for the bot's six voice rules. Imported by every LLM system prompt; mirrored in `agents/onboarding/system_prompt.xml`. |
| `tools/gcs_profile.py` | Per-user blob CRUD (`athlete_profile.md`, `training_plan.md`, …). |
| `tools/bedrock_claude_client.py`, `tools/gemini_client.py` | LLM wrappers + `get_llm_client()`. |
| `agents/onboarding/system_prompt.xml` | Onboarding interview prompt — **requires human sign-off to edit**. |
| `knowledge_base/{triathlon,strength,fat_loss,recovery,boundaries}/*.md` | Slim RAG chunks. |
| `mocks/` | gcs_mock, line_mock, claude_mock — used by every test. |
| `scripts/deploy.sh`, `scripts/setup_scheduler.sh`, `scripts/fetch_logs.sh` | Ops tooling. |
| `DEPLOY.md` | One-page deploy walkthrough. |
| `tests/conftest.py` | Forces local-fallback for GCS in tests; sets safe env defaults. |
| `Dockerfile` | python:3.11-slim, no venv inside container. |

---

## Environment Variables

Required names (values live in `.env.example` once created; never commit real values):

`LINE_CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET`, `OWNER_LINE_USER_ID`, `LLM_PROVIDER` (`claude` | `gemini`), `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL_ID`, `GCS_PROFILES_BUCKET`, `GOOGLE_CLOUD_PROJECT`, `DAILY_PUSH_SECRET`, `PORT`.

LLM routing: `daily_push.py` uses `get_llm_client()` for provider switching; `coach_reply.py`, onboarding, `plan_generator.py` call Claude directly (nuanced coaching needs Claude's depth).

---

## Commands

```bash
# Test
.venv/bin/python -m pytest tests/ -q

# Install deps
.venv/bin/pip install -r tools/requirements.txt

# Local webhook
.venv/bin/uvicorn tools.line_webhook:app --port 8080 --reload

# Deploy / re-deploy to Cloud Run
./scripts/deploy.sh                # full build + deploy
./scripts/deploy.sh --no-build     # env-only redeploy

# (Re)create the two daily-push Cloud Scheduler jobs
./scripts/setup_scheduler.sh

# Pull recent backend logs
./scripts/fetch_logs.sh --hours 6
```

Full deployment walkthrough is in `DEPLOY.md`. Underlying gcloud commands are in `capybara_CLAUDE.md` §Deployment.

---

## Reports Directory

| File | Purpose | Written by |
|---|---|---|
| `reports/test_report_{date}.md` | Test run results | pytest (auto) |
| `reports/push_log_{date}.md` | Daily push success/failure | `daily_push.py` |
| `reports/human_checklist.md` | Questions for developer (≤5) | Claude Code |
| `reports/blocked.md` | Issues requiring human | Claude Code |

---

## Owner Mode

Recognised by `OWNER_LINE_USER_ID`. Direct tone, no disclaimer injection, debug footer `[🏊 domain: X | tokens: Xin/Xout | kb: N]` (kb = estimated KB tokens injected into the system prompt). Commands: `/status`, `/help`, `/onboard` (restart onboarding for the owner), `/plan` (show current plan), `/adjust <理由>` (trigger plan adjustment).

Owner is also excluded from `known_users.json` so the weekly invite cron job (`capybara-invite-stalled`) doesn't pester the developer.

---

## Endpoints

| Path | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Cloud Run liveness probe |
| `POST /callback` | LINE signature | LINE Messaging API webhook |
| `POST /trigger/daily_push` | Bearer `DAILY_PUSH_SECRET` | Body `{"push_type":"morning"\|"evening"}` — Cloud Scheduler `capybara-push-morning` (07:00) / `capybara-push-evening` (21:00) |
| `POST /trigger/onboarding_invite` | Bearer `DAILY_PUSH_SECRET` | Body `{}` — Cloud Scheduler `capybara-invite-stalled` (Wed 11:00) — invites stalled users with cooldown |
