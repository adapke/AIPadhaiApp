#!/usr/bin/env bash
# stop_local.sh — stop the dev server started by run_local.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$REPO_ROOT/.padhai_server.pid"
PORT="${1:-8000}"

if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE")
  if kill -0 "$PID" 2>/dev/null; then
    echo "[stop_local] stopping server PID $PID"
    kill "$PID"
    sleep 1
  else
    echo "[stop_local] PID $PID not running"
  fi
  rm -f "$PID_FILE"
else
  echo "[stop_local] no PID file found; trying port $PORT"
fi

# Belt-and-suspenders: free the port
lsof -ti:"$PORT" | xargs -r kill -9 2>/dev/null || true
echo "[stop_local] done"
