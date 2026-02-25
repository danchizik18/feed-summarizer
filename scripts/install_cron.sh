#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 7 * * *}"
CRON_MARKER="# feed_summary_daily_digest"
CRON_JOB="${CRON_SCHEDULE} cd ${ROOT_DIR} && ${ROOT_DIR}/cron/run_daily.sh"

tmp_current="$(mktemp)"
tmp_filtered="$(mktemp)"
tmp_final="$(mktemp)"

cleanup() {
  rm -f "$tmp_current" "$tmp_filtered" "$tmp_final"
}
trap cleanup EXIT

crontab -l > "$tmp_current" 2>/dev/null || true

# Remove previous entries for this project before appending the latest schedule.
grep -vF "${ROOT_DIR}/cron/run_daily.sh" "$tmp_current" | grep -vF "$CRON_MARKER" > "$tmp_filtered" || true

cat "$tmp_filtered" > "$tmp_final"
{
  echo "$CRON_MARKER"
  echo "$CRON_JOB"
} >> "$tmp_final"

crontab "$tmp_final"
echo "Installed cron job:"
echo "$CRON_JOB"
