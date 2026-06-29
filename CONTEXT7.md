# CONTEXT7: Ultrawhale Dogfeed Pipeline

## Project Overview
High-throughput generation pipeline for training LLMs using local Qwen3.6-27B.

## Technical Stack
- **Engine:** `llama.cpp` (server mode)
- **Language:** Python 3.11+
- **Runner:** `Taskfile.yml`
- **Inference:** Qwen3.6-27B-GGUF:Q4_K_M
- **Parallelization:** `ralph_parallel.py` (Worker-based round-robin)
- **Pipeline:** Generation -> Atomic Async Write -> Compression (Kompress-v8) -> HF Upload

## Core Settings (SOTA-Optimized v1.0.0)
- **Context Window:** 8192 tokens
- **KV Cache:** `q8_0` (Quantized)
- **Reasoning:** Disabled (`--reasoning off`)
- **Parallel Slots:** 2
- **Autoscaling:** Dynamic (2-8 workers)

## Directory Structure
- `/`: Taskfile, runner scripts
- `dogfeed_parallel/`: Raw generation output
- `ralph_logs/`: Worker log files
- `*.jsonl`: Aggregate datasets

## Agent Guidelines
1. **Never kill `task run`** unless explicitly requested.
2. **Log Monitoring:** Use `task logs:pretty` for structured, colorized visualization.
3. **Resource Safety:** Adhere to `ResourceManager` limits (50% Mem, 75% CPU).
4. **Data Integrity:** All writes to datasets must remain atomic.
