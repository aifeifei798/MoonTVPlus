#!/bin/bash
# Start the headless Chrome the CDP harnesses attach to. They CANNOT start one
# themselves — they only attach — so run this first, in the background, and keep
# it alive for the whole probe session.
#
# Usage: scripts/chrome_start.sh [port] [profile-dir]
#        CDP_PORT=<port> scripts/cdp_eval.cjs <auth> <path> <expr>
set -u

PORT="${1:-9334}"
PROFILE="${2:-/tmp/opencode/chrome-profile}"
LOG="/tmp/opencode/chrome-${PORT}.log"

pkill -f "remote-debugging-port=${PORT}" 2>/dev/null
sleep 1

mkdir -p "$(dirname "$PROFILE")"
nohup google-chrome \
  --headless=new \
  --remote-debugging-port="$PORT" \
  --remote-debugging-host=127.0.0.1 \
  --user-data-dir="$PROFILE" \
  --window-size=1280,800 \
  --hide-spellcheck \
  about:blank >"$LOG" 2>&1 &

for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:${PORT}/json/list" >/dev/null 2>&1; then
    echo "CDP ready on ${PORT} (log: ${LOG}) — export CDP_PORT=${PORT} for the harnesses"
    exit 0
  fi
  sleep 0.25
done
echo "CDP never answered on ${PORT}; see ${LOG}"
exit 1
