#!/usr/bin/env bash
# scripts/deploy.sh
#
# One-shot Cloud Run deploy for 水豚教練.
#
#   ./scripts/deploy.sh           # build + deploy
#   ./scripts/deploy.sh --no-build # redeploy current image without rebuilding
#
# Pre-reqs (one-time, see DEPLOY.md):
#   - gcloud auth login && gcloud config set project hanana-491223
#   - .env populated with real values (LINE_*, ANTHROPIC_API_KEY, DAILY_PUSH_SECRET, ...)
#   - Artifact Registry repo "capybara" exists in asia-east1 (this script creates it
#     if missing)
#   - GCS bucket "capybara-profiles" exists in hanana-491223 (this script creates it
#     if missing)

set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-hanana-491223}"
REGION="asia-east1"
SERVICE="capybara-backend"
REPO="capybara"
IMAGE_NAME="capybara-coach"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE_NAME}:latest"
BUCKET="${GCS_PROFILES_BUCKET:-capybara-profiles}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

# Load .env if present (without exporting noise)
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Required env vars — fail fast if any missing
required=(
  LINE_CHANNEL_SECRET
  LINE_CHANNEL_ACCESS_TOKEN
  ANTHROPIC_API_KEY
  DAILY_PUSH_SECRET
)
missing=()
for v in "${required[@]}"; do
  if [[ -z "${!v:-}" ]]; then
    missing+=("$v")
  fi
done
if (( ${#missing[@]} > 0 )); then
  echo "Missing env vars in .env: ${missing[*]}" >&2
  exit 1
fi

# Auth + project
if ! gcloud auth print-access-token >/dev/null 2>&1; then
  echo "Not authenticated. Run: gcloud auth login" >&2
  exit 1
fi
gcloud config set project "${PROJECT}" >/dev/null

# Artifact Registry repo (idempotent)
if ! gcloud artifacts repositories describe "${REPO}" --location="${REGION}" >/dev/null 2>&1; then
  echo "→ Creating Artifact Registry repo ${REPO} in ${REGION}"
  gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="水豚教練 container images"
fi

# GCS bucket (idempotent)
if ! gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1; then
  echo "→ Creating GCS bucket gs://${BUCKET}"
  gcloud storage buckets create "gs://${BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access
fi

# Build (skippable)
if [[ "${1:-}" != "--no-build" ]]; then
  echo "→ Submitting Cloud Build for ${IMAGE}"
  gcloud builds submit --tag "${IMAGE}" --region="${REGION}"
else
  echo "→ Skipping build (--no-build)"
fi

# Deploy. Pass env vars via --set-env-vars; secrets stay in your local .env.
# OWNER_LINE_USER_ID, GEMINI_*, USE_BEDROCK, etc. are optional — set them in .env
# and they get forwarded if present.
ENV_VARS="LINE_CHANNEL_SECRET=${LINE_CHANNEL_SECRET}"
ENV_VARS+=",LINE_CHANNEL_ACCESS_TOKEN=${LINE_CHANNEL_ACCESS_TOKEN}"
ENV_VARS+=",ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"
ENV_VARS+=",DAILY_PUSH_SECRET=${DAILY_PUSH_SECRET}"
ENV_VARS+=",GCS_PROFILES_BUCKET=${BUCKET}"
ENV_VARS+=",GOOGLE_CLOUD_PROJECT=${PROJECT}"
ENV_VARS+=",LLM_PROVIDER=${LLM_PROVIDER:-claude}"
ENV_VARS+=",USE_BEDROCK=${USE_BEDROCK:-false}"
[[ -n "${OWNER_LINE_USER_ID:-}" ]] && ENV_VARS+=",OWNER_LINE_USER_ID=${OWNER_LINE_USER_ID}"
[[ -n "${GEMINI_API_KEY:-}" ]] && ENV_VARS+=",GEMINI_API_KEY=${GEMINI_API_KEY}"
[[ -n "${GEMINI_MODEL_ID:-}" ]] && ENV_VARS+=",GEMINI_MODEL_ID=${GEMINI_MODEL_ID}"

echo "→ Deploying ${SERVICE} to Cloud Run (${REGION})"
gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080 \
  --memory=512Mi \
  --cpu=1 \
  --min-instances=0 \
  --max-instances=2 \
  --timeout=300 \
  --set-env-vars="${ENV_VARS}"

URL=$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')
echo
echo "✓ Deployed."
echo "  Service URL : ${URL}"
echo "  LINE webhook: ${URL}/callback"
echo "  Health check: ${URL}/health"
echo
echo "Next:"
echo "  1) Paste the LINE webhook URL above into LINE Developers console"
echo "     → your channel → Messaging API → Webhook URL → Verify"
echo "  2) Run scripts/setup_scheduler.sh to enable the daily push jobs"
