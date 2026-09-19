#!/usr/bin/env bash
# ntp-discipline.sh — the 8b-is macOS clock witness.
# Queries the council of clocks (read-only, no admin) and prints the
# constellation's time truth: the offset against the reference stratum.
# The multidog telemetry rows this value, so the ledger can witness time.
#
#   ./ntp-discipline.sh              → +0.021924 +/- 0.015779 time.apple.com
#   ./ntp-discipline.sh --json       → {"server":"time.apple.com","offset_ms":21.9,"jitter_ms":15.8}
set -uo pipefail

SERVER="${NTP_SERVER:-time.apple.com}"

if [ "${1:-}" = "--json" ]; then
  raw="$(sntp "$SERVER" 2>/dev/null | head -1)"
  # sntp prints:  +0.021924 +/- 0.015779  time.apple.com  17.253.20.45
  offset="$(echo "$raw" | awk '{print $1}')"
  jitter="$(echo "$raw" | awk '{print $3}')"
  ms() { awk -v x="$1" 'BEGIN{printf "%d", x*1000}'; }
  off_ms="$(ms "${offset#+}")"
  jit_ms="$(ms "$jitter")"
  python3 -c "import json,sys; print(json.dumps({'server': '$SERVER', 'offset_ms': '${off_ms}', 'jitter_ms': '${jit_ms}'}))" 2>/dev/null \
    || echo "{\"server\":\"$SERVER\",\"offset_ms\":\"$off_ms\",\"jitter_ms\":\"$jit_ms\"}"
  exit 0
fi

sntp "$SERVER" 2>&1 | head -1