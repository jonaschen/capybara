# CLAUDE.md — 水豚教練 (Capybara Coach)

This file is **how we work**. For **what we're building** (full architecture, persona, knowledge base, data schemas, deployment commands), read `capybara_CLAUDE.md`. Chinese-language design context is in `capybara_GEMINI.md`.

水豚教練 is a LINE Messaging API chatbot giving personalised fitness coaching in Traditional Chinese. It runs on Cloud Run (`capybara-backend`, `asia-east1`) in GCP project `hanana-491223`, with per-user state stored as Markdown/JSON files in `gs://capybara-profiles/{user_id}/`. Three training domains with equal footing: **triathlon**, **strength / 增肌**, **fat loss / 減脂**. Supporting domains: nutrition, recovery, injury, general_fitness.

---

## Current Phase: 0 — Infrastructure

- [ ] LINE channel created, `CHANNEL_ACCESS_TOKEN` + `CHANNEL_SECRET` obtained
- [ ] GCS bucket `capybara-profiles` created in `hanana-491223`
- [ ] Cloud Run `capybara-backend` deployed (separate from `hanana-backend`)
- [ ] Cloud Scheduler jobs: `capybara-push-morning` (07:00) + `capybara-push-evening` (21:00)

Phase 0 is mostly out-of-repo cloud provisioning handled by the human. First code session is Phase 1.

---

## Phase Roadmap

| Phase | Goal | Key deliverable | Exit criterion |
|---|---|---|---|
| 0 | Infrastructure | LINE + GCS + Cloud Run + Scheduler | All 4 checkboxes green; webhook reachable |
| 1 | Basic Q&A | `tools/line_webhook.py` + `tools/coach_reply.py` — domain detect → LLM → LINE reply | Owner sends "增肌怎麼開始" → coherent reply locally |
| 2 | Onboarding | `agents/onboarding/system_prompt.xml` + `tools/profile_generator.py` → `athlete_profile.md` | 8–10 turn onboarding produces profile in mock GCS |
| 3 | Training plan | `tools/plan_generator.py` → `training_plan.md` | First plan matches spec §Training Plan Format |
| 4 | Daily push | `tools/daily_push.py` 07:00 / 21:00, `/trigger/daily_push` endpoint | 3 consecutive successful Scheduler runs on staging |
| 5 | Dynamic adjustment | Plan updates on injury / missed-workouts / feedback / race-proximity | Adjustment appears in `training_plan.md` §調整記錄 |
| 6 | Triathlon-specific | Race countdown, taper phase (auto at 3 weeks), brick workouts | Test user 3 weeks from race gets taper plan |
| 7 | Real user trial | n=2 athletes, 4-week pilot | Both complete 4 weeks; `reports/pilot_feedback.md` populated |

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

---

## Persona Rules (condensed — full persona in `capybara_CLAUDE.md`)

- **說具體的事，不說感覺的事。** "今天跑 30 分鐘 Zone 2" 不是 "今天動一動"。
- **不放大，不縮小。** 沒練就是沒練，練了就是練了。
- **專業詞彙用了就解釋。** 第一次講 Zone 2 就順帶說心率範圍。
- **記得上次說的事。** 自然引用歷史，不每次從零開始。
- **一個 🐾 結尾，其他 emoji 不用。**
- **Prohibited:** 毒性正能量 ("加油你一定可以！")、羞辱感、絕對飲食禁令、醫療診斷、品牌補給推薦。

Primary language: Traditional Chinese. Switch to English if user writes in English.

---

## Key Files (target paths — most do not exist yet)

| File | Purpose |
|---|---|
| `tools/line_webhook.py` | FastAPI webhook, state routing IDLE / ONBOARDING / COACHING |
| `tools/coach_reply.py` | Domain detect → RAG inject → LLM → LINE push |
| `tools/profile_generator.py` | Onboarding conversation → `athlete_profile.md` (write-once) |
| `tools/plan_generator.py` | Generates and dynamically adjusts `training_plan.md` |
| `tools/daily_push.py` | Morning + evening scheduled push. Uses `get_llm_client()`. |
| `tools/bedrock_claude_client.py` | Anthropic Claude wrapper |
| `tools/gemini_client.py` | Gemini wrapper |
| `agents/onboarding/system_prompt.xml` | Onboarding interview prompt — **requires human sign-off to edit** |
| `knowledge_base/{triathlon,strength,fat_loss,recovery}/*.md` | Slim RAG chunks — direct file lookup, no vector search |
| `fixtures/athlete_profile_dev.md` | Test fixture — **requires human sign-off to edit** |
| `mocks/` | All external-API mocks (see table above) |
| `reports/` | Test reports, push logs, human checklist, blocked issues |
| `.env.example` | All required env vars (not yet created) |
| `Dockerfile` | Slim image, <300 MB target, cold start <5s |

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

# Local webhook (after Phase 1 lands)
.venv/bin/uvicorn tools.line_webhook:app --port 8080 --reload
```

Full deployment commands (gcloud run deploy, gcloud scheduler jobs create) are in `capybara_CLAUDE.md` §Deployment — do not paraphrase them here, copy exactly when shipping.

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

Recognised by `OWNER_LINE_USER_ID`. Direct tone, no disclaimer injection, debug footer `[🏊 domain: X | tokens: Xin/Xout]`. Commands: `/status`, `/help`, `/onboard` (restart), `/plan` (show current), `/adjust` (trigger adjustment).
