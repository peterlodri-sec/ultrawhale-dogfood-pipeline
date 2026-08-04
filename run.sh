#!/usr/bin/env bash
# E2E dogfeed pipeline — runs until Ctrl+C or SIGTERM
# Starts: llm server → workers → upload loop
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && source .env && set +a

UPLOAD_INTERVAL=120
PIDS=()

log() { echo "[run] $(date '+%H:%M:%S') $*"; }

cleanup() {
    log "Shutting down..."
    for pid in "${PIDS[@]}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 2
    bash llm-server.sh stop 2>/dev/null || true
    pkill -f "ultrawhale generate" 2>/dev/null || true
    log "Done."
    exit 0
}
trap cleanup INT TERM

log "Starting LLM server..."
bash llm-server.sh start
log "LLM server ready."

# Read LLM config from llm-server.sh detection
LLM_HOST="http://localhost:8080"
LLM_MODEL="qwen3.6-27b"
if [ -f .llm-mode ] && [ "$(cat .llm-mode)" = "1" ]; then
    LLM_HOST="http://localhost:11434"
    LLM_MODEL="qwen2.5:3b"
    log "Using ollama: $LLM_HOST ($LLM_MODEL)"
fi
export LLM_HOST LLM_MODEL

# Workers via ultrawhale generate
log "Starting dogfeed worker 1 (CS)..."
nohup nice -n -20 uv run ultrawhale generate \
    --num 99999 --category cs \
    --host "$LLM_HOST" --model "$LLM_MODEL" \
    --skip-curation \
    --output "dogfeed_cs_worker1_$(date +%Y-%m-%d).jsonl" \
    > /tmp/worker1.log 2>&1 &
PIDS+=($!)

log "Starting dogfeed worker 2 (all topics)..."
nohup nice -n -20 uv run ultrawhale generate \
    --num 99999 --category all \
    --host "$LLM_HOST" --model "$LLM_MODEL" \
    --skip-curation \
    --output "dogfeed_general_worker2_$(date +%Y-%m-%d).jsonl" \
    > /tmp/worker2.log 2>&1 &
PIDS+=($!)

# Upload loop
log "Starting upload loop (every ${UPLOAD_INTERVAL}s)..."
upload_loop() {
    renice -n 19 -p $$ > /dev/null 2>&1
    while true; do
        sleep "$UPLOAD_INTERVAL"
        echo "[upload] $(date '+%H:%M:%S') uploading..."
        nice -n 19 uv run ultrawhale upload \
            --dir . --active-grace 5 2>&1 | tail -5 || true
        nice -n 19 uv run ultrawhale upload \
            --dir dogfeed_parallel --active-grace 5 2>&1 | tail -5 || true

        # ── Upload local recordings if detected ──
        if ls recordings/*.wav recordings/*.flac recordings/*.mp3 2>/dev/null | head -1 >/dev/null; then
            echo "[upload] $(date '+%H:%M:%S') recordings detected — uploading..."
            if [ -f recordings/README.md ]; then
                hf upload PeetPedro/overflow-sounds recordings/ . --repo-type dataset 2>&1 | tail -3 || true
            else
                hf upload PeetPedro/ultrawhale-dogfood recordings/ . --repo-type dataset 2>&1 | tail -3 || true
            fi
        fi
    done
}
upload_loop > /tmp/upload.log 2>&1 &
PIDS+=($!)

log "All systems go."
log "  Workers:  tail -f /tmp/worker1.log  /tmp/worker2.log"
log "  Upload:   tail -f /tmp/upload.log"
log "  LLM:      tail -f $SCRIPT_DIR/llama-server.log"
log "Press Ctrl+C to stop everything."

wait
