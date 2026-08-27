#!/bin/sh
set -eu

python /app/deploy/health.py &

if [ "${SOCIAL_PUBLISH_ENABLED:-0}" = "1" ]; then
  # 5s poll so "immediate" posts leave the queue quickly after approval.
  exec python /app/agents/social-media/scripts/worker.py --watch --interval 5
fi

exec python /app/agents/social-media/scripts/worker.py --watch --interval 30 --heartbeat-only
