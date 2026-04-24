# Deploy 水豚教練 to Cloud Run

End-to-end first deploy. Shipped scripts handle the repeatable parts; this doc covers the one-time setup that scripts can't automate.

---

## 0. Pre-reqs (one-time, manual)

### LINE Messaging API channel
- https://developers.line.biz → Provider → **Create a Messaging API channel**
- Settings tab → **Channel secret** (copy)
- Messaging API tab → **Channel access token (long-lived)** → Issue (copy)
- Messaging API tab → **Webhook URL** → leave empty for now (filled in step 3)
- Messaging API tab → **Use webhook**: ON
- Messaging API tab → **Auto-reply messages**: OFF, **Greeting messages**: OFF

Get your own LINE user ID for owner mode: send any message to the bot once it's live, then check Cloud Run logs for `source.user_id` — paste it back into `OWNER_LINE_USER_ID`.

### gcloud
```bash
gcloud auth login
gcloud config set project hanana-491223
gcloud auth application-default login   # for local dev hitting GCS

# Enable APIs (one-time per project)
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  storage.googleapis.com
```

---

## 1. Fill in `.env`

```bash
cp .env.example .env
# edit .env — values needed:
#   LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN     (from LINE console)
#   ANTHROPIC_API_KEY                                  (from Anthropic console)
#   DAILY_PUSH_SECRET                                  (openssl rand -hex 32)
#   OWNER_LINE_USER_ID                                 (your own LINE ID — see step 0)
#
# defaults are fine for: LLM_PROVIDER, USE_BEDROCK, GCS_PROFILES_BUCKET,
# GOOGLE_CLOUD_PROJECT, PORT
```

`.env` is gitignored. Never commit it.

---

## 2. First deploy

```bash
./scripts/deploy.sh
```

The script:
1. Loads `.env`, validates required vars are present
2. Creates Artifact Registry repo `capybara` if missing
3. Creates GCS bucket `capybara-profiles` if missing
4. Builds the container via Cloud Build (no local Docker needed)
5. Deploys to Cloud Run service `capybara-backend` in `asia-east1`
6. Prints the service URL + LINE webhook URL + health URL

Re-run the same script to redeploy after code changes. Use `--no-build` to redeploy the current image (e.g. only env vars changed).

---

## 3. Wire up LINE webhook

Take the webhook URL printed by `deploy.sh` (it ends in `/callback`):

- LINE Developers console → your channel → Messaging API → **Webhook URL** → paste
- Click **Verify** — should return 200
- **Use webhook**: ON

Send the bot a message from your phone. You should get a coach reply.

---

## 4. Daily push (Cloud Scheduler)

```bash
./scripts/setup_scheduler.sh
```

Creates two jobs in `asia-east1` (Asia/Taipei TZ):
- `capybara-push-morning` — daily 07:00 → POST `/trigger/daily_push {"push_type":"morning"}`
- `capybara-push-evening` — daily 21:00 → POST `/trigger/daily_push {"push_type":"evening"}`

Smoke-test now without waiting for 7am:
```bash
gcloud scheduler jobs run capybara-push-morning --location=asia-east1
```

Check Cloud Run logs to confirm the request landed and `pushed:N` came back.

---

## 5. Test the full flow

As yourself (owner):
- Send anything → owner mode replies via `coach_reply` (debug footer `[🏊 ...]`)
- `/help` → command list
- `/onboard` → forces onboarding mode → next message starts the interview
- Complete the interview → `/plan` shows your generated plan
- `/adjust 膝蓋有點不舒服` → plan revised, 調整記錄 gains an entry

As a non-owner (a friend's LINE):
- Their first message triggers onboarding automatically (no `/onboard` needed)

---

## Cost guardrails

- Cloud Run: `min-instances=0`, `max-instances=2`, `512Mi` mem. Idle = $0. Each cold start ~1–2s.
- Cloud Scheduler: 3 jobs free/month per project; we use 2.
- GCS: trivial — Markdown files only, no image/video.
- Anthropic API: 1 onboarding ≈ 8–12 calls × ~600 tokens; 1 coach turn ≈ 1 call × ~800 tokens; 1 daily push ≈ 1 call × ~600 tokens × users.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| LINE Webhook Verify returns 400 | wrong `LINE_CHANNEL_SECRET` | recheck console value, redeploy |
| Bot doesn't reply but logs show 200 | `LINE_CHANNEL_ACCESS_TOKEN` wrong | reissue + redeploy |
| `/plan` says "找不到目前計畫" | profile + plan never generated | run `/onboard`, complete interview |
| Owner mode not detected | `OWNER_LINE_USER_ID` mismatch | grep Cloud Run logs for actual user_id |
| `/trigger/daily_push` returns 401 | scheduler bearer mismatch | re-run `setup_scheduler.sh` after rotating secret |
| Cloud Build fails on import | dep missing in `tools/requirements.txt` | add it, redeploy |

For deeper issues check Cloud Run logs:
```bash
gcloud run services logs read capybara-backend --region=asia-east1 --limit=50
```
