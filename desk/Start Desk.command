#!/bin/bash
# Double-click in Finder to open Tatsuki's Desk. Starts the server first if it is not running.
DESK="$(cd "$(dirname "$0")" && pwd)"
PORT="${DESK_PORT:-4747}"
if ! curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
  launchctl kickstart "gui/$(id -u)/com.tatsuki.desk.server" 2>/dev/null \
    || (cd "$DESK" && nohup python3 server.py >/dev/null 2>&1 &)
  for _ in 1 2 3 4 5 6 7 8 9 10; do curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1 && break; sleep 0.5; done
fi
open "http://localhost:$PORT"
