# Architecture — Ultrawhale Dogfeed Pipeline v2.0

## Data Flow

```
LLM Server (llama.cpp / OpenAI-compatible)
        │
        ▼
┌──────────────────┐
│  generate.py     │  ← Quality scoring (scoring.py)
│  (core engine)   │  ← Difficulty sampling (difficulty.py)
└────────┬─────────┘  ← HF inference fallback (hf.py)
         │              ← LLM-judge curation (curation.py)
         ▼
┌──────────────────┐
│  Async Writer    │  Queue-based, non-blocking I/O
│  (JSONL output)  │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  orchestrator.py │  ← Parallel worker management
│  (coordinator)   │  ← Resource monitoring (resources.py)
└────────┬─────────┘  ← Dynamic autoscaling
         │              ← Kompress scheduling (kompress.py)
         ▼
┌──────────────────┐
│  upload.py       │  ← Non-destructive, idempotent
│  (HF dataset)    │  ← Skips active files, deduplicates
└──────────────────┘
```

## Component Roles

### `generate.py` — Core Generation Engine
The heart of the pipeline. Supports three modes:
- **Local**: Direct calls to OpenAI-compatible LLM server
- **Hybrid**: Local first, HF fallback on low quality
- **HF-only**: Pure HuggingFace Inference API

Each generation goes through quality scoring (coherence, length, diversity) and optional LLM-judge curation (5-point scale, threshold 4.0).

### `scoring.py` — Deterministic Quality Metrics
Pure functions for scoring Q&A pairs. Three dimensions:
- **Length** (35% weight): Question 8-200 tokens, answer 20-2000 tokens
- **Coherence** (35% weight): Punctuation presence, answer > question length
- **Diversity** (30% weight): MD5 deduplication

### `difficulty.py` — Active Learning Sampling
Distributes questions across difficulty tiers (40% easy, 40% medium, 20% hard). Each tier has specialized prompt templates. `ActiveLearningTracker` monitors success rates and suggests distribution adjustments.

### `curation.py` — LLM-as-Judge
Validates generated pairs using HF Inference API. Scores on 1-5 scale; rejects pairs below 4.0. Code verification is a placeholder (always passes) — future versions will add sandboxed execution.

### `hf.py` — HuggingFace Inference Client
Thin wrapper around HuggingFace Inference API with retry logic and timeout handling. Supports multiple model keys (llama70b, etc.).

### `orchestrator.py` — Pipeline Coordinator
Manages the parallel generation lifecycle:
- Launches workers with staggered starts
- Monitors via `resources.py`
- Dynamic autoscaling (2-8 workers based on memory/CPU)
- Kompress scheduler (background thread, every 5 min)
- Result aggregation by topic
- Graceful shutdown via SIGTERM/SIGINT

### `resources.py` — Resource Management
Monitors system memory and CPU via `psutil`. Enforces configurable thresholds. `ProcessManager` tracks worker processes with per-process memory limits.

### `kompress.py` — Post-Processing Compression
Compresses Q&A pairs through kompress-v8 model on HuggingFace. Reduces token count while preserving semantic content. Falls back to originals on compression failure.

### `upload.py` — Dataset Publishing
Non-destructive, idempotent upload to HuggingFace datasets. Rules:
- Never deletes or moves local files
- Skips files modified recently (< active_grace minutes)
- Skips files already present on HF
- Skips empty files

## Scaling Model

The pipeline scales by adding parallel workers, each generating independent Q&A pairs in different topic categories. The orchestrator's autoscaler adjusts worker count based on real-time resource usage:

| Condition | Action |
|-----------|--------|
| Memory < 40%, CPU < 60% | Scale UP (+1 worker) |
| Memory > 65%, CPU > 85% | Scale DOWN (-1 worker) |
| Range | 2-8 workers |

## Quality Pipeline

```
Raw Q&A pair
    │
    ▼
Phase 1: Quality Scoring (coherence · length · diversity)
    │  score ≥ 0.65 → pass
    ▼
Phase 2: HF Inference Fallback (if local score < 0.65)
    │  retry with Llama70B
    ▼
Phase 3: LLM-Judge Curation (1-5 scale)
    │  score ≥ 4.0 → pass
    ▼
Phase 4: Kompress-v8 Compression (optional)
    │
    ▼
Final dataset entry
```

## Configuration Surface

See `src/ultrawhale/config.py` for the full `Config` dataclass. All values load from environment variables with sensible defaults. Only `HF_TOKEN` is strictly required for upload and HF inference operations.

## Forward-Looking: v2.2 Rust Migration

The Python pipeline is stable and feature-complete for v2.0. A future v2.2 release is planned to migrate performance-critical paths (scoring, async I/O, worker orchestration) to Rust for lower latency and memory footprint. The Python package will remain the primary interface; Rust components will be optional native extensions.
