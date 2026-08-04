#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.llama-server.pid"
LOG_FILE="$SCRIPT_DIR/llama-server.log"
LLAMA_BIN="/opt/homebrew/bin/llama-server"
PORT=8080
HEALTH_URL="http://localhost:${PORT}/health"

# Ollama detection
OLLAMA_BIN="/opt/homebrew/bin/ollama"
PREFERRED_OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:3b}"
OLLAMA_PORT=11434
OLLAMA_MODE=0

is_running() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

ollama_available() {
    # Check if ollama binary exists and has the preferred model
    [ -x "$OLLAMA_BIN" ] || return 1
    "$OLLAMA_BIN" list 2>/dev/null | grep -q "$PREFERRED_OLLAMA_MODEL" || return 1
    return 0
}

ensure_ollama_running() {
    # Start ollama if not already running
    if curl -sf "http://localhost:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; then
        return 0
    fi
    echo "ollama not running — starting..."
    OLLAMA_FLASH_ATTENTION="1" OLLAMA_KV_CACHE_TYPE="q8_0" "$OLLAMA_BIN" serve > "$SCRIPT_DIR/ollama-server.log" 2>&1 &
    local opid=$!
    echo "$opid" > "$SCRIPT_DIR/.ollama-server.pid"
    # Wait for ollama to be ready
    for _ in $(seq 1 15); do
        if curl -sf "http://localhost:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; then
            echo "ollama ready (PID $opid)"
            return 0
        fi
        sleep 1
    done
    echo "ollama did not become ready — check ollama-server.log"
    return 1
}

cmd_start() {
    if is_running; then
        echo "✓ already running (PID $(cat "$PID_FILE"))"
        return 0
    fi

    # Try ollama first
    if ollama_available; then
        echo "ollama + $PREFERRED_OLLAMA_MODEL detected — using ollama"
        if ensure_ollama_running; then
            OLLAMA_MODE=1
            # Store mode so run.sh can read it
            echo "1" > "$SCRIPT_DIR/.llm-mode"
            echo "ollama" > "$SCRIPT_DIR/.llm-provider"
            # Export env vars for workers
            export LLM_HOST="http://localhost:${OLLAMA_PORT}/v1"
            export LLM_MODEL="$PREFERRED_OLLAMA_MODEL"
            echo "✓ ollama mode active at $LLM_HOST (model: $LLM_MODEL)"
            return 0
        fi
        echo "ollama start failed — falling back to llama-server"
    fi

    # Fallback: start llama-server (original behavior)
    echo "Starting llama-server — Qwen3.6-27B Q4_K_M..."
    echo "Logs: $LOG_FILE"
    echo "0" > "$SCRIPT_DIR/.llm-mode"
    echo "llama-server" > "$SCRIPT_DIR/.llm-provider"
    export LLM_HOST="http://localhost:${PORT}/v1"
    export LLM_MODEL="qwen3.6-27b"

    "$LLAMA_BIN" \
        -hf unsloth/Qwen3.6-27B-GGUF:Q4_K_M \
        --port "$PORT" \
        -c 8192 \
        -np 2 \
        -b 512 \
        -ub 512 \
        -ngl 999 \
        --flash-attn auto \
        --mlock \
        --no-mmap \
        --min-p 0.1 \
        --cache-type-k f16 \
        --cache-type-v f16 \
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
    # Stop ollama if we started it
    if [ -f "$SCRIPT_DIR/.ollama-server.pid" ]; then
        local opid=$(cat "$SCRIPT_DIR/.ollama-server.pid")
        if kill -0 "$opid" 2>/dev/null; then
            echo "Stopping ollama PID $opid..."
            kill "$opid" 2>/dev/null || true
            sleep 1
            kill -9 "$opid" 2>/dev/null || true
        fi
        rm -f "$SCRIPT_DIR/.ollama-server.pid"
        echo "ollama stopped"
    fi

    # Stop llama-server
    if ! is_running; then
        echo "✗ llama-server not running"
        rm -f "$PID_FILE"
        rm -f "$SCRIPT_DIR/.llm-mode" "$SCRIPT_DIR/.llm-provider"
        return 0
    fi
    PID=$(cat "$PID_FILE")
    echo "Stopping llama-server PID $PID..."
    kill "$PID"
    
    for _ in $(seq 1 10); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 1
    done
    
    if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID" && echo "Force-killed"
    fi
    rm -f "$PID_FILE"
    rm -f "$SCRIPT_DIR/.llm-mode" "$SCRIPT_DIR/.llm-provider"
    echo "✓ Stopped"
    return 0
}

cmd_status() {
    # Check ollama
    if [ -f "$SCRIPT_DIR/.llm-mode" ] && [ "$(cat "$SCRIPT_DIR/.llm-mode")" = "1" ]; then
        echo "✓ ollama mode"
        echo "  model: $PREFERRED_OLLAMA_MODEL"
        echo "  port: $OLLAMA_PORT"
        if [ -f "$SCRIPT_DIR/.ollama-server.pid" ]; then
            local opid=$(cat "$SCRIPT_DIR/.ollama-server.pid")
            if kill -0 "$opid" 2>/dev/null; then
                echo "  PID: $opid (running)"
            else
                echo "  PID: $opid (stale)"
            fi
        fi
        curl -sf "http://localhost:${OLLAMA_PORT}/api/tags" 2>/dev/null | python3 -c "import json,sys; [print(f'  model: {m[\"name\"]}') for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null || true
        return 0
    fi

    # Check llama-server
    if is_running; then
        PID=$(cat "$PID_FILE")
        echo "✓ llama-server running  PID=$PID  port=$PORT  model=qwen3.6-27b"
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
    if [ -f "$SCRIPT_DIR/.ollama-server.pid" ] && [ -s "$SCRIPT_DIR/ollama-server.log" ]; then
        tail -f "$SCRIPT_DIR/ollama-server.log"
    elif [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "No log file yet"
        return 1
    fi
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
