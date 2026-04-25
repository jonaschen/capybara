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

## Knowledge Base

Slim RAG: direct markdown file lookup, no embeddings. Source of truth is **`PLAN_capybara_knowledge_base.md`** (12+ files, content + injection rules + token budget).

- `tools/rag_retriever.py` exposes `search_fitness_knowledge(query, domain, max_tokens=800)` and `should_inject_disclaimer(text)`.
- `coach_reply` injects KB content into the system prompt as a `<knowledge_base>` block. Disclaimer trigger words live IN `knowledge_base/boundaries/bnd_002_medical_disclaimer.md` and are parsed at module load.
- Domains with KB dirs: `triathlon`, `strength`, `fat_loss`, `recovery`, `injury` (→ `boundaries/bnd_001`). `nutrition` and `general_fitness` rely on the LLM.
- When triathlon's 5 files exceed the 800-token budget, retriever picks the single most-relevant file by query overlap.

To add a KB file: drop a markdown into the right domain dir, add no code. To broaden the disclaimer trigger list: edit `bnd_002_medical_disclaimer.md` `## 觸發詞` section — `should_inject_disclaimer` picks it up automatically on next process start.

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
| `tools/onboarding_reply.py` | ONBOARDING-state handler; finalisation via `_finalize` runs profile + plan generation. |
| `tools/profile_generator.py` | Transcript → `athlete_profile.md` (write-once). |
| `tools/plan_generator.py` | First training plan from profile. |
| `tools/plan_adjuster.py` | In-place plan revision (owner `/adjust`). |
| `tools/daily_push.py` | Morning/evening generator + per-user fan-out. Uses `get_llm_client()`. |
| `tools/rag_retriever.py` | Slim RAG file lookup + disclaimer trigger parser. |
| `tools/state_store.py` | GCS-backed onboarding-state persistence (Cloud Run scale-out safety). |
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
