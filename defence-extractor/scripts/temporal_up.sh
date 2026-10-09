#!/usr/bin/env bash
# Start (idempotently) the Temporal dev server and a pipeline worker on this machine (user space, localhost only).
#   UI: http://localhost:8233   (from your laptop: ssh -L 8233:localhost:8233 algotest)
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
mkdir -p var
if ! ss -ltn | grep -q ':7233 '; then
  setsid nohup temporal server start-dev --ip 127.0.0.1 --port 7233 --ui-port 8233 \
    --db-filename var/temporal.db --log-level error > var/temporal.log 2>&1 < /dev/null &
  for _ in $(seq 1 30); do ss -ltn | grep -q ':7233 ' && break; sleep 1; done
fi
echo "temporal: $(ss -ltn | grep -c -E ':(7233|8233) ') ports listening"
if [ "${1:-}" = "--worker" ]; then
  if [ -f var/worker.pid ] && kill -0 "$(cat var/worker.pid)" 2>/dev/null; then
    echo "worker already running (pid $(cat var/worker.pid))"
  else
    source .venv/bin/activate
    setsid nohup dx temporal-worker --max-activities "${2:-24}" > var/worker.log 2>&1 < /dev/null &
    echo $! > var/worker.pid
    echo "worker started (pid $(cat var/worker.pid))"
  fi
fi
