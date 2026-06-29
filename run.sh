#!/usr/bin/env bash
# E2E dogfeed pipeline — runs until Ctrl+C or SIGTERM
# Starts: llm server → dogfeed workers → ralph loop → upload loop
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env if present (HF_TOKEN etc)
[ -f .env ] && set -a && source .env && set +a

UPLOAD_INTERVAL=120   # seconds between uploads
PYTHON=python3
PIDS=()

log() { echo "[run] $(date '+%H:%M:%S') $*"; }

cleanup() {
    log "Shutting down..."
    # kill all tracked background pids
    for pid in "${PIDS[@]}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    # Give them a moment to shut down gracefully
    sleep 2
    # stop llm server via script
    bash llm-server.sh stop 2>/dev/null || true
    # kill any lingering workers
    pkill -f generate_dogfeed.py 2>/dev/null || true
    pkill -f ralph_parallel.py   2>/dev/null || true
    log "Done."
    exit 0
}
trap cleanup INT TERM

# ── 1. Start LLM server ───────────────────────────────────────────────────────
log "Starting LLM server..."
bash llm-server.sh start
log "LLM server ready."

# ── 2. Start dogfeed workers ──────────────────────────────────────────────────
log "Starting dogfeed worker 1 (CS, qwen3.6-27b)..."
nohup nice -n -20 $PYTHON -u generate_dogfeed.py \
    --model qwen3.6-27b \
    --host http://localhost:8080 \
    --num 99999 \
    --category cs \
    --output "dogfeed_cs_worker1_$(date +%Y-%m-%d).jsonl" \
    > /tmp/worker1.log 2>&1 &
PIDS+=($!)
log "Worker 1 PID: ${PIDS[-1]}"

log "Starting dogfeed worker 2 (all topics, qwen3.6-27b)..."
nohup nice -n -20 $PYTHON -u generate_dogfeed.py \
    --model qwen3.6-27b \
    --host http://localhost:8080 \
    --num 99999 \
    --category all \
    --output "dogfeed_general_worker2_$(date +%Y-%m-%d).jsonl" \
    > /tmp/worker2.log 2>&1 &
PIDS+=($!)
log "Worker 2 PID: ${PIDS[-1]}"

# ── 3. Start ralph parallel loop ──────────────────────────────────────────────
log "Starting ralph parallel loop..."
nohup nice -n -20 $PYTHON -u ralph_parallel.py \
    --mode fast --rounds 99999 --workers 5 --pairs-per-worker 5 \
    > /tmp/ralph.log 2>&1 &
PIDS+=($!)
log "Ralph PID: ${PIDS[-1]}"

# ── 4. Upload loop (every 2 min) ──────────────────────────────────────────────
log "Starting upload loop (every ${UPLOAD_INTERVAL}s)..."
upload_loop() {
    # Run in subshell with renice to ensure zero interference
    renice -n 19 -p $$ > /dev/null 2>&1
    while true; do
        sleep "$UPLOAD_INTERVAL"
        echo "[upload] $(date '+%H:%M:%S') uploading..."
        # Use a nice value to ensure low I/O and CPU priority
        nice -n 19 $PYTHON upload_local_dogfeed.py \
            --dir . \
            --active-grace 5 2>&1 | tail -5 || true
        nice -n 19 $PYTHON upload_local_dogfeed.py \
            --dir dogfeed_parallel \
            --active-grace 5 2>&1 | tail -5 || true
    done
}
upload_loop > /tmp/upload.log 2>&1 &
PIDS+=($!)
log "Upload loop PID: ${PIDS[-1]}"

# ── 5. Status summary ─────────────────────────────────────────────────────────
log "All systems go."
log "  Workers:  tail -f /tmp/worker1.log  /tmp/worker2.log"
log "  Ralph:    tail -f /tmp/ralph.log"
log "  Upload:   tail -f /tmp/upload.log"
log "  LLM:      tail -f $SCRIPT_DIR/llama-server.log"
log "Press Ctrl+C to stop everything."

# ── 6. Wait forever ───────────────────────────────────────────────────────────
wait
