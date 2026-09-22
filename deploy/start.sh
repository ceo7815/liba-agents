#!/bin/sh
set -eu

export PYTHONPATH=/app

python /app/deploy/health.py &

echo "liba-agents start: SOCIAL_PUBLISH_ENABLED=${SOCIAL_PUBLISH_ENABLED:-0} SOCIAL_DRY_RUN=${SOCIAL_DRY_RUN:-1}"
echo "liba-agents start: LIBA_OS_BASE_URL_set=$([ -n "${LIBA_OS_BASE_URL:-}" ] && echo yes || echo no) LIBA_OS_API_KEY_set=$([ -n "${LIBA_OS_API_KEY:-}" ] && echo yes || echo no)"
echo "liba-agents start: META_PAGE_ID_set=$([ -n "${META_PAGE_ID:-}" ] && echo yes || echo no) META_TOKEN_set=$([ -n "${META_PAGE_ACCESS_TOKEN:-}" ] && echo yes || echo no)"
echo "liba-agents start: CALL_QA_VOICENTER_ENABLED=${CALL_QA_VOICENTER_ENABLED:-0} VOICENTER_API_CODE_set=$([ -n "${VOICENTER_API_CODE:-}" ] && echo yes || echo no)"

# Voicenter → call-qa (Sofia). Report chips + watch first; history must not block them.
if [ "${CALL_QA_VOICENTER_ENABLED:-0}" = "1" ]; then
  python /app/agents/call-qa/scripts/report_status.py || true
  (
    while true; do
      python /app/agents/call-qa/scripts/pull_voicenter.py --watch --interval 15 || true
      echo "call-qa voicenter worker exited; restarting in 5s"
      sleep 5
    done
  ) &
  (
    python /app/agents/call-qa/scripts/pull_voicenter_history.py --days 90 --chunk 1 || true
    python /app/agents/call-qa/scripts/pull_voicenter.py --once || true
    python /app/agents/call-qa/scripts/report_status.py || true
  ) &
  (
    sleep 8
    python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/status?probe=1', timeout=90).read()" || true
  ) &
fi

# Keep the publisher alive even if a single crash happens (health.py alone is not enough).
while true; do
  if [ "${SOCIAL_PUBLISH_ENABLED:-0}" = "1" ]; then
    # 5s poll so immediate posts leave the queue quickly after approval.
    python /app/agents/social-media/scripts/worker.py --watch --interval 5 || true
  else
    python /app/agents/social-media/scripts/worker.py --watch --interval 30 --heartbeat-only || true
  fi
  echo "social-media worker exited; restarting in 5s"
  sleep 5
done
