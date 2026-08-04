#!/bin/bash
# "Opens" the Net Worth page on the always-running local Flask app.
# Loading GET /My_Networth_html server-side refreshes stale prices (>24h)
# and records today's snapshot into data/networth_history.json.
# Triggered daily by the launchd agent com.darl.networth-refresh.

LOG="$HOME/Library/Logs/networth-refresh.log"
mkdir -p "$(dirname "$LOG")"

code=$(curl -s -S -m 60 \
  "http://127.0.0.1:5001/My_Networth_html" \
  -o /dev/null -w "%{http_code}" 2>>"$LOG")

echo "[$(date '+%Y-%m-%d %H:%M:%S')] open page -> HTTP ${code:-000}" >> "$LOG"
