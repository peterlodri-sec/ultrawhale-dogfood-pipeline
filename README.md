# Ultrawhale Dogfeed Pipeline (v1.0.0)

High-throughput Q&A generation pipeline for training LLMs.

## Features
- **Parallel Generation:** Multi-worker architecture using `ralph_parallel.py`.
- **Async I/O:** Queue-based writer ensures generation is never blocked by disk I/O.
- **Resource Management:** Automatic CPU/Memory monitoring to prevent system crashes.
- **SOTA Inference:** Optimized `llama.cpp` server with quantized KV caching.
- **Dynamic Autoscaling:** Automatic worker scaling (2-8) based on real-time resource load.

## Getting Started
1. Set `HF_TOKEN` in `.env`.
2. Start the system:
   ```bash
   task run
   ```
3. Monitor logs with structure/color:
   ```bash
   task logs:pretty
   ```

## Tuning
- **Parallelism:** Adjust `workers` and `pairs-per-worker` in `ralph_parallel.py`.
- **Server:** See `llm-server.sh` for context window and cache settings.

## Autoscaling
The pipeline dynamically scales workers based on resource usage. 
- **Scale Up:** Memory < 40%, CPU < 60%.
- **Scale Down:** Memory > 65%, CPU > 85%.
- **Limits:** 2-8 workers.
