#!/usr/bin/env bash
# scripts/setup_scheduler.sh
#
# Create or update the two Cloud Scheduler jobs that hit /trigger/daily_push.
# Idempotent: re-running updates the existing jobs in place.
#
#   ./scripts/setup_scheduler.sh
#
# Reads SERVICE URL from `gcloud run services describe`, so deploy first.
# Reads DAILY_PUSH_SECRET from .env.

set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-hanana-491223}"
REGION="asia-east1"
SERVICE="capybara-backend"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${DAILY_PUSH_SECRET:-}" ]]; then
  echo "DAILY_PUSH_SECRET missing from .env" >&2
  exit 1
fi

URL=$(gcloud run services describe "${SERVICE}" \
        --region="${REGION}" --project="${PROJECT}" \
        --format='value(status.url)' 2>/dev/null || true)
if [[ -z "${URL}" ]]; then
  echo "Cloud Run service ${SERVICE} not found in ${REGION}. Deploy first." >&2
  exit 1
fi
ENDPOINT="${URL}/trigger/daily_push"

upsert_job() {
  local name="$1"
  local cron="$2"
  local body="$3"
  # Common args (no header flag — that name differs between create/update).
  local common_args=(
    --location="${REGION}"
    --schedule="${cron}"
    --time-zone="Asia/Taipei"
    --uri="${ENDPOINT}"
    --http-method=POST
    --message-body="${body}"
    --project="${PROJECT}"
  )
  local headers="Authorization=Bearer ${DAILY_PUSH_SECRET},Content-Type=application/json"
  if gcloud scheduler jobs describe "${name}" --location="${REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
    echo "→ Updating ${name}"
    gcloud scheduler jobs update http "${name}" "${common_args[@]}" --update-headers="${headers}"
  else
    echo "→ Creating ${name}"
    gcloud scheduler jobs create http "${name}" "${common_args[@]}" --headers="${headers}"
  fi
}

upsert_job "capybara-push-morning" "0 7 * * *"  '{"push_type":"morning"}'
upsert_job "capybara-push-evening" "0 21 * * *" '{"push_type":"evening"}'

# Weekly re-invitation for users who followed but never onboarded.
INVITE_ENDPOINT="${URL}/trigger/onboarding_invite"

upsert_invite_job() {
  local name="$1"
  local cron="$2"
  local common_args=(
    --location="${REGION}"
    --schedule="${cron}"
    --time-zone="Asia/Taipei"
    --uri="${INVITE_ENDPOINT}"
    --http-method=POST
    --message-body='{}'
    --project="${PROJECT}"
  )
  local headers="Authorization=Bearer ${DAILY_PUSH_SECRET},Content-Type=application/json"
  if gcloud scheduler jobs describe "${name}" --location="${REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
    echo "→ Updating ${name}"
    gcloud scheduler jobs update http "${name}" "${common_args[@]}" --update-headers="${headers}"
  else
    echo "→ Creating ${name}"
    gcloud scheduler jobs create http "${name}" "${common_args[@]}" --headers="${headers}"
  fi
}

upsert_invite_job "capybara-invite-stalled" "0 11 * * 3"  # Wed 11:00

echo
echo "✓ Scheduler jobs ready."
echo "  Daily push endpoint  : ${ENDPOINT}"
echo "  Invite endpoint      : ${INVITE_ENDPOINT}"
echo "  Morning              : 07:00 Asia/Taipei (daily)"
echo "  Evening              : 21:00 Asia/Taipei (daily)"
echo "  Stalled-user invites : 11:00 Asia/Taipei (Wed only)"
echo
echo "Trigger manually for a smoke test:"
echo "  gcloud scheduler jobs run capybara-push-morning   --location=${REGION}"
echo "  gcloud scheduler jobs run capybara-invite-stalled --location=${REGION}"
