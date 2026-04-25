#!/usr/bin/env bash
# scripts/fetch_logs.sh
#
# Pull recent Cloud Run logs for capybara-backend, optionally filtered by
# user_id or detected domain. Thin wrapper around `gcloud run services
# logs read`. Output is raw cleaned text — pipe to `less`, `grep`, etc.
#
# Examples:
#   ./scripts/fetch_logs.sh                           # last 24h, 200 lines
#   ./scripts/fetch_logs.sh --hours 1                 # last hour
#   ./scripts/fetch_logs.sh --user U1234abcd          # one user (substring match)
#   ./scripts/fetch_logs.sh --domain triathlon        # one domain
#   ./scripts/fetch_logs.sh --hours 6 --limit 500     # bigger window

set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-hanana-491223}"
REGION="asia-east1"
SERVICE="capybara-backend"

HOURS=24
LIMIT=200
USER_FILTER=""
DOMAIN_FILTER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hours)  HOURS="$2"; shift 2;;
    --limit)  LIMIT="$2"; shift 2;;
    --user)   USER_FILTER="$2"; shift 2;;
    --domain) DOMAIN_FILTER="$2"; shift 2;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1;;
  esac
done

# Compute RFC3339 lower bound (portable: prefer GNU date, fall back to BSD)
if date -u --version >/dev/null 2>&1; then
  SINCE=$(date -u -d "${HOURS} hours ago" +%Y-%m-%dT%H:%M:%SZ)
else
  SINCE=$(date -u -v "-${HOURS}H" +%Y-%m-%dT%H:%M:%SZ)
fi

LOG_FILTER="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SERVICE}\" AND timestamp>=\"${SINCE}\""

# gcloud logging read returns one entry per line in --format=json; we
# extract just the textPayload / jsonPayload.message for human-readable output.
gcloud logging read "${LOG_FILTER}" \
  --project="${PROJECT}" \
  --limit="${LIMIT}" \
  --order=desc \
  --format='value(timestamp,textPayload,jsonPayload.message)' \
  | { if [[ -n "${USER_FILTER}" ]]; then grep "${USER_FILTER}"; else cat; fi; } \
  | { if [[ -n "${DOMAIN_FILTER}" ]]; then grep -i "domain.*${DOMAIN_FILTER}"; else cat; fi; }
