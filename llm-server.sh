#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.llama-server.pid"
LOG_FILE="$SCRIPT_DIR/llama-server.log"
LLAMA_BIN="/opt/homebrew/bin/llama-server"
PORT=8080
HEALTH_URL="http://localhost:${PORT}/health"

is_running() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

cmd_start() {
    if is_running; then
        echo "✓ already running (PID $(cat "$PID_FILE"))"
        return 0
    fi

    echo "Starting llama-server — Qwen3.6-27B Q4_K_M..."
    echo "Logs: $LOG_FILE"

    "$LLAMA_BIN" \
        -hf unsloth/Qwen3.6-27B-GGUF:Q4_K_M \
        --port "$PORT" \
        -c 8192 \
        -np 2 \
        -b 512 \
        -ub 512 \
        -ngl 999 \
        --kv-unified \
        --cache-idle-slots \
        --flash-attn auto \
        --mlock \
        --no-mmap \
        --min-p 0.1 \
        --cache-type-k q8_0 \
        --cache-type-v q8_0 \
        --reasoning off \
        >> "$LOG_FILE" 2>&1 &

    echo $! > "$PID_FILE"
    echo "PID $! — waiting for ready..."

    for _ in $(seq 1 90); do
        if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
            echo "✓ Ready at http://localhost:${PORT}/v1"
            return 0
        fi
        sleep 2
    done

    echo "✗ Server did not become healthy within 180s — check logs"
    return 1
}

cmd_stop() {
    if ! is_running; then
        echo "✗ not running"
        rm -f "$PID_FILE"
        return 0
    fi
    PID=$(cat "$PID_FILE")
    echo "Stopping PID $PID..."
    kill "$PID"
    
    # wait up to 10s for clean exit
    for _ in $(seq 1 10); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 1
    done
    
    # force if still alive
    if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID" && echo "Force-killed"
    fi
    rm -f "$PID_FILE"
    echo "✓ Stopped"
    return 0
}

cmd_status() {
    if is_running; then
        PID=$(cat "$PID_FILE")
        echo "✓ running  PID=$PID  port=$PORT"
        echo ""
        curl -sf "$HEALTH_URL" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (health endpoint not yet responding)"
    else
        echo "✗ not running"
        [ -f "$PID_FILE" ] && echo "  (stale PID file removed)" && rm -f "$PID_FILE"
        return 1
    fi
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_logs() {
    if [ ! -f "$LOG_FILE" ]; then
        echo "No log file yet: $LOG_FILE"
        return 1
    fi
    tail -f "$LOG_FILE"
}

# Main Execution Control
case "${1:-help}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_restart ;;
    logs)    cmd_logs ;;
    *)
        echo "Usage: $0 {start|stop|status|restart|logs}"
        exit 1
        ;;
esac
