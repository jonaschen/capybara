# CLAUDE.md — 水豚教練 (Capybara Coach)

This file provides guidance to Claude Code when working with the `capybara-coach` repository.

---

## Project Overview

**水豚教練** is a LINE Messaging API chatbot providing personalized fitness coaching.  
It is a **separate project from Hana** — different LINE channel, different GCS bucket, different Cloud Run service — but shares the same GCP project (`hanana-491223`) and the same technology stack.

**Persona:** 水豚教練很少催你，但他說的話你會想記下來。他知道你偷懶了，但他不說；他知道你今天很拚，他也不誇。他只是告訴你明天要做什麼，然後讓你去睡覺。

**Primary language:** Traditional Chinese. Switches to English if user writes in English.

**Target users:** Mandarin-speaking adults who want structured fitness guidance without the intimidation of a hardcore gym culture. Primary focus: triathlon athletes (beginner to intermediate), muscle gain / fat loss, general fitness foundation.

---

## Current State

**Phase 0 — Setup** (start here)
- [ ] LINE channel created, `CHANNEL_ACCESS_TOKEN` and `CHANNEL_SECRET` obtained
- [ ] GCS bucket `capybara-profiles` created in `hanana-491223`
- [ ] Cloud Run service `capybara-backend` deployed (separate from `hanana-backend`)
- [ ] Cloud Scheduler jobs: `capybara-push-morning` (07:00) + `capybara-push-evening` (21:00)

See `ROADMAP.md` for full phase-level status.

---

## Architecture

```
LINE message → Cloud Run (tools/line_webhook.py, FastAPI + uvicorn)
    → state check (IDLE / ONBOARDING / COACHING)
    → IDLE: domain detection → LLM (+ RAG if domain-specific) → LINE reply
    → ONBOARDING: collect fitness profile → generate athlete_profile.md
    → COACHING: plan queries, progress check-ins, dynamic adjustments
```

**Daily push (Cloud Scheduler):**
```
07:00 → morning_push: today's training focus + one actionable tip
21:00 → evening_push: one line of encouragement, no demands
```

---

## Key Files

| File | Purpose |
|---|---|
| `tools/line_webhook.py` | FastAPI webhook — coach's entry point. State routing (IDLE/ONBOARDING/COACHING). |
| `tools/coach_reply.py` | Core reply logic — domain detection, RAG injection, LLM call, LINE push |
| `tools/daily_push.py` | Morning tip + evening encouragement. Uses `get_llm_client()` for provider switching. |
| `tools/profile_generator.py` | Structured extraction from onboarding conversation → `athlete_profile.md` |
| `tools/plan_generator.py` | Generates and dynamically adjusts training plans → `training_plan.md` |
| `tools/gemini_client.py` | Gemini API wrapper (reuse from Hana repo if available) |
| `tools/bedrock_claude_client.py` | Claude API wrapper (reuse from Hana repo if available) |
| `tools/requirements.txt` | Runtime dependencies |
| `Dockerfile` | Slim image, target <300 MB, cold start <5s |
| `.env.example` | All required environment variables |
| `agents/onboarding/system_prompt.xml` | Onboarding interview — collect athlete profile |
| `knowledge_base/` | Domain-specific fitness knowledge chunks |

---

## GCS Per-User Data Structure

Bucket: `gs://capybara-profiles/`

```
{user_id}/
├── athlete_profile.md        ← Onboarding writes once. READ-ONLY after creation.
│                                Contains: goal, current fitness level, schedule,
│                                injury history, equipment access, race targets
├── training_plan.md          ← Current training plan. Dynamic — updated by coach.
├── progress_log.json         ← Rolling log of check-ins (last 30 entries)
├── preferences.json          ← Mutable: preferred units (kg/lb), notification time, rest days
└── onboarding_transcript.md  ← Raw onboarding conversation log
known_users.json              ← Full LINE user IDs (auto-populated on every message)
```

**Immutability rule:** `athlete_profile.md` is written once during onboarding and never overwritten. Plan adjustments go into `training_plan.md`. Profile corrections (injury, schedule change) create a new versioned entry appended to the profile, never deleting old data.

---

## Persona & Tone

**水豚教練 (Shuǐtún Jiàoliàn)**

水豚教練很少催你，但他說的話你會想記下來。  
他知道你偷懶了，但他不說；他知道你今天很拚，他也不誇。  
他只是告訴你明天要做什麼，然後讓你去睡覺。

具體說話方式：
- **說具體的事，不說感覺的事。** "今天跑 30 分鐘 Zone 2" 不是 "今天動一動"。
- **不放大，不縮小。** 沒練就是沒練，練了就是練了，都不需要多說什麼。
- **專業詞彙用了就解釋。** 第一次說 Zone 2 就順帶說心率範圍，不讓人感到被排除在外。
- **記得上次說的事。** 自然引用用戶的歷史，不每次從零開始。
- **一個 🐾 emoji 上限，放在句尾。** 不用其他 emoji。

**Prohibited language:**
- Toxic positivity ("加油！你一定可以的！")
- Shame or guilt about missed workouts
- Absolute dietary rules ("你絕對不能吃 XXX")
- Medical diagnoses or injury diagnoses
- Supplement recommendations by brand name

**Mandatory disclaimer** (auto-injected when discussing injury or medical topics):
> 水豚教練提供的是運動訓練參考建議，不是醫療診斷。如果你有受傷或身體不適，請先諮詢醫師或物理治療師。

---

## Domain Detection

Keyword-based matching. Falls back to `general_fitness` when no keywords match.

```python
_DOMAIN_KEYWORDS = {
    "triathlon": [
        "三鐵", "鐵人", "游泳", "騎車", "跑步", "T1", "T2", "轉換區",
        "鐵人三項", "51.5", "113", "226", "奧運距離", "半程鐵人", "全程鐵人",
        "brick", "open water", "transition",
    ],
    "strength": [
        "增肌", "重訓", "肌力", "深蹲", "硬舉", "臥推", "槓鈴", "啞鈴",
        "1RM", "組數", "次數", "RPE", "漸進超負荷",
    ],
    "fat_loss": [
        "減脂", "減重", "體脂", "熱量赤字", "TDEE", "間歇性斷食",
        "有氧", "體重", "體態",
    ],
    "nutrition": [
        "飲食", "蛋白質", "碳水", "脂肪", "補給", "能量棒", "電解質",
        "賽前飲食", "恢復飲食", "巨量營養素",
    ],
    "recovery": [
        "恢復", "休息日", "睡眠", "肌肉痠痛", "延遲性痠痛", "DOMS",
        "泡沫滾筒", "伸展", "按摩",
    ],
    "injury": [
        "受傷", "痛", "膝蓋", "腳踝", "肩膀", "下背", "拉傷", "扭傷",
        "復健", "物理治療",
    ],
    "general_fitness": [],  # fallback
}
```

---

## Knowledge Base

**Architecture:** Direct file lookup (no vector search, no embedding model).  
Same approach as Hana's Slim RAG — modern LLMs handle general fitness knowledge via built-in training data. Only domain-specific or Taiwan-context content is stored as chunks.

```
knowledge_base/
├── triathlon/
│   ├── tri_001_beginner_roadmap.md      ← 初鐵完賽 16 週計畫架構
│   ├── tri_002_zone_training.md         ← 心率區間訓練說明
│   ├── tri_003_brick_workout.md         ← 磚塊訓練設計
│   ├── tri_004_taiwan_races.md          ← 台灣主要三鐵賽事日曆與特性
│   └── tri_005_nutrition_race_day.md    ← 比賽日補給策略
├── strength/
│   ├── str_001_progressive_overload.md  ← 漸進超負荷原則
│   ├── str_002_beginner_program.md      ← 新手三日課表框架
│   └── str_003_deload_week.md           ← 減量週設計
├── fat_loss/
│   ├── fat_001_tdee_calculation.md      ← TDEE 計算與赤字設定
│   └── fat_002_body_recomp.md           ← 增肌減脂同步的條件
└── recovery/
    └── rec_001_sleep_and_adaptation.md  ← 睡眠與超補償
```

**Loading:** `search_fitness_knowledge(query, domain)` returns matching chunks by domain via direct lookup.

---

## Onboarding Flow

Triggered when a new user sends their first message (no `athlete_profile.md` in GCS).

**Goal:** Collect enough to generate a meaningful first training plan. Keep it conversational — 8–10 exchanges maximum, not a form.

**Required fields for `athlete_profile.md`:**

```
goal: [增肌 / 減脂 / 完成三鐵 / 提升體能 / 其他]
current_level: [完全新手 / 有基礎但不規律 / 規律訓練 X 個月/年]
available_days: [每週幾天可以訓練]
session_duration: [每次大約幾分鐘]
equipment: [健身房 / 家裡有器材 / 無器材 / 有自行車]
injury_history: [無 / 有（描述）]
race_target: [無 / 有（賽事名稱、日期）]
weight_height: [體重 kg、身高 cm — optional, for TDEE if fat loss goal]
```

**Onboarding system prompt:** `agents/onboarding/system_prompt.xml`

On `[PROFILE_COMPLETE]` marker or user says "結束" → call `profile_generator.py` → write `athlete_profile.md` to GCS → generate initial `training_plan.md` → send plan summary to user.

---

## Training Plan Format (`training_plan.md`)

```markdown
# 訓練計畫 — {goal}
Generated: {date}
Valid until: {date + 4 weeks}
Last adjusted: {date}

## 本週重點
{one sentence focus for this week}

## 週課表
| 星期 | 訓練內容 | 時長 | 強度 |
|---|---|---|---|
| 一 | 跑步 Zone 2 | 40 分 | 低 |
| 二 | 重訓 上肢 | 45 分 | 中 |
| 三 | 休息 / 伸展 | 20 分 | 極低 |
...

## 調整記錄
- {date}: 因用戶反映膝蓋不適，移除深蹲，改為腿推機
- {date}: 用戶完成第一場 5K，增加長跑距離 10%
```

**Dynamic adjustment triggers:**
- User reports injury or pain → immediately revise plan, log adjustment
- User misses 3+ consecutive workouts → simplify plan, don't guilt
- User reports plan too easy/hard → adjust intensity within same structure
- Race date within 3 weeks → auto-switch to taper phase

---

## Daily Push Content

**Morning (07:00):**
```
{今天的訓練任務，一句話}
💡 {一個具體的執行提示}
```
Example:
```
今天：Zone 2 跑步 35 分鐘，心率維持在 130–145 bpm。
💡 前 5 分鐘慢走熱身，讓身體告訴你今天的狀態。
```

**Evening (21:00):**
```
{一句話，不要求任何行動，純粹陪伴}
```
Example:
```
今天不管練了什麼，或者什麼都沒練，都沒關係。明天還在。🐾
```

---

## State Machine

| State | Trigger | Description |
|---|---|---|
| `IDLE` | Default | General fitness Q&A, plan queries, progress check-ins |
| `ONBOARDING` | New user (no `athlete_profile.md`) | Collect fitness profile |
| `COACHING` | User explicitly asks for plan review / adjustment | Deep plan discussion mode |

**Owner mode:** Recognized by `OWNER_LINE_USER_ID`. Direct tone, no disclaimer, debug footer `[🏊 domain: X | tokens: Xin/Xout]`. Commands: `/status`, `/help`, `/onboard` (restart onboarding), `/plan` (show current plan), `/adjust` (trigger plan adjustment).

---

## LLM Provider

- `LLM_PROVIDER=claude` (default) — Anthropic API
- `LLM_PROVIDER=gemini` — Gemini API (requires `GEMINI_API_KEY`)
- `GEMINI_MODEL_ID` env var controls Gemini model
- `daily_push.py` uses `get_llm_client()` for provider switching
- `coach_reply.py` and onboarding use Claude directly (nuanced coaching requires Claude's depth)
- `plan_generator.py` uses Claude (structured plan generation)

---

## Token Optimization

- Conversation history: last 12 messages (6 turns). Oldest dropped when exceeded.
- `training_plan.md` is injected into system prompt only in `COACHING` state or when user asks about their plan.
- Token budget warning: log `WARNING` when input tokens exceed 5,000.
- `athlete_profile.md` always injected (short, high-value context).

---

## Non-Negotiable Rules

1. **Never diagnose injuries.** "膝蓋外側痛可能有幾種原因，建議你先讓物理治療師評估。"
2. **Never prescribe medication or specific supplements by brand.**
3. **Never shame missed workouts.** Acknowledge, reframe, move forward.
4. **Never give absolute dietary rules** ("你不能吃碳水"). Give principles and context instead.
5. **Mandatory disclaimer** when discussing injury or medical symptoms (auto-injected).
6. **One plan at a time.** Don't give conflicting advice across conversations.

---

## Development Workflow

### Core Principles (same as Hana)

1. **Test first, then code.** No feature without tests.
2. **External services always mocked.** `mocks/gcs_mock.py`, `mocks/line_mock.py`, `mocks/claude_mock.py`
3. **Self-repair on failure.** 3 fix rounds → write to `reports/blocked.md` → stop.
4. **Batch human questions** into `reports/human_checklist.md`.

**Python environment:** Use `.venv/`. System Python is externally managed.  
Run tests: `.venv/bin/python -m pytest tests/ -q`

### Mock Layer

| Mock | Replaces |
|---|---|
| `mocks/gcs_mock.py` | `google.cloud.storage.Client` → `/tmp/mock_gcs/` |
| `mocks/line_mock.py` | LINE Messaging API → in-memory |
| `mocks/claude_mock.py` | Anthropic Claude API → canned responses |
| `mocks/gemini_mock.py` | Gemini API → canned responses |

### Stop Conditions

- 3 consecutive fix rounds failed
- Real API keys needed
- Changes to `agents/onboarding/system_prompt.xml` (design decision — ask human)
- Changes to `fixtures/athlete_profile_dev.md` (test material — ask human)

### Continue Autonomously

- Add/modify mocks
- Refactor (tests still pass)
- Modify `reports/`
- Install Python packages (`.venv/bin/pip install`)
- Add new test cases
- Expand `_DOMAIN_KEYWORDS` with additional terms

---

## Environment Variables

```bash
# LINE
LINE_CHANNEL_ACCESS_TOKEN=...
LINE_CHANNEL_SECRET=...
OWNER_LINE_USER_ID=...         # Your LINE user ID

# LLM
LLM_PROVIDER=claude            # claude | gemini
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
GEMINI_MODEL_ID=gemini-2.0-flash

# GCS
GCS_PROFILES_BUCKET=capybara-profiles
GOOGLE_CLOUD_PROJECT=hanana-491223

# Push auth
DAILY_PUSH_SECRET=...          # Bearer token for /trigger/daily_push

# Deployment
PORT=8080
```

---

## Deployment

- **GCP project:** `hanana-491223` (same as Hana)
- **Cloud Run service:** `capybara-backend` (separate from `hanana-backend`)
- **Region:** `asia-east1`
- **Container registry:** `asia-east1-docker.pkg.dev/hanana-491223/capybara/capybara-coach:latest`
- **Redeploy:**
  ```bash
  gcloud run deploy capybara-backend \
    --image asia-east1-docker.pkg.dev/hanana-491223/capybara/capybara-coach:latest \
    --region asia-east1 \
    --project hanana-491223
  ```

**Cloud Scheduler jobs:**
```bash
# Morning push 07:00
gcloud scheduler jobs create http capybara-push-morning \
  --location=asia-east1 \
  --schedule="0 7 * * *" \
  --time-zone="Asia/Taipei" \
  --uri="https://{CLOUD_RUN_URL}/trigger/daily_push" \
  --http-method=POST \
  --headers="Authorization=Bearer ${DAILY_PUSH_SECRET},Content-Type=application/json" \
  --message-body='{"push_type":"morning"}' \
  --project=hanana-491223

# Evening push 21:00
gcloud scheduler jobs create http capybara-push-evening \
  --location=asia-east1 \
  --schedule="0 21 * * *" \
  --time-zone="Asia/Taipei" \
  --uri="https://{CLOUD_RUN_URL}/trigger/daily_push" \
  --http-method=POST \
  --headers="Authorization=Bearer ${DAILY_PUSH_SECRET},Content-Type=application/json" \
  --message-body='{"push_type":"evening"}' \
  --project=hanana-491223
```

---

## Reports Directory

| File | Purpose | Written by |
|---|---|---|
| `reports/test_report_{date}.md` | Test run results | Auto (pytest) |
| `reports/push_log_{date}.md` | Daily push success/failure | `daily_push.py` |
| `reports/human_checklist.md` | Questions for developer (≤5) | Claude Code |
| `reports/blocked.md` | Issues requiring human | Claude Code |

---

## Phase Roadmap (high-level)

| Phase | Goal | Key deliverable |
|---|---|---|
| 0 | Infrastructure | LINE channel, GCS bucket, Cloud Run, Scheduler |
| 1 | Basic Q&A | `coach_reply.py` with domain detection + LLM |
| 2 | Onboarding | `athlete_profile.md` generation from conversation |
| 3 | Training plan | `plan_generator.py` + `training_plan.md` |
| 4 | Daily push | Morning tip + evening encouragement |
| 5 | Dynamic adjustment | Plan updates based on check-in feedback |
| 6 | Triathlon-specific | Race countdown, taper phase, brick workouts |
| 7 | Real user trial | n=2 athletes, 4-week pilot |
