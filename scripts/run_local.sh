#!/usr/bin/env bash
# run_local.sh — start AIPadhaiApp dev server with a stable PID file.
# Usage: bash scripts/run_local.sh [port]
set -euo pipefail

PORT="${1:-8000}"
PID_FILE=".padhai_server.pid"
LOG_FILE="padhai_server.log"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$REPO_ROOT"

# Stop any existing instance
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[run_local] stopping previous server (PID $OLD_PID)…"
    kill "$OLD_PID" && sleep 1
  fi
  rm -f "$PID_FILE"
fi

# Also free the port if something else is on it
lsof -ti:"$PORT" | xargs -r kill -9 2>/dev/null || true
sleep 0.5

# Load .env if present (never committed; see .env.example)
if [ -f ".env" ]; then
  set -o allexport
  source .env
  set +o allexport
fi

if [ -z "${PADHAI_JWT_SECRET:-}" ]; then
  echo "[run_local] WARNING: PADHAI_JWT_SECRET not set — auth will fail."
  echo "            Generate one: python3 -c 'import secrets; print(secrets.token_urlsafe(48))'"
  echo "            Then add it to .env"
fi

echo "[run_local] starting on port $PORT (log → $LOG_FILE)"
echo "[run_local] git SHA: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

python -m uvicorn padhai.web:app \
  --host 0.0.0.0 --port "$PORT" \
  --log-level info \
  > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# Wait for it to bind
for i in $(seq 1 15); do
  sleep 0.5
  if curl -sf "http://localhost:$PORT/healthz" > /dev/null 2>&1; then
    echo "[run_local] server is up (PID $SERVER_PID)"
    curl -s "http://localhost:$PORT/healthz" | python3 -m json.tool 2>/dev/null || true
    exit 0
  fi
done

echo "[run_local] server did not start in time — check $LOG_FILE"
tail -20 "$LOG_FILE"
exit 1
