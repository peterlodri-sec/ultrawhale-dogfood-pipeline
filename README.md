# Ultrawhale Dogfeed Pipeline

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/logo.svg">
  <img alt="Ultrawhale Dogfeed Pipeline" src="./assets/logo.svg" width="160" align="right">
</picture>

[![CI](https://github.com/peterlodri-sec/ultrawhale-dogfood-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/peterlodri-sec/ultrawhale-dogfood-pipeline/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![HF Dataset](https://img.shields.io/badge/🤗%20dataset-PeetPedro/ultrawhale--dogfood-yellow)](https://huggingface.co/datasets/PeetPedro/ultrawhale-dogfood)

**Industrial-grade Q&A data synthesis pipeline for training LLMs.**

Generates high-quality, LLM-judge-validated Q&A pairs at scale — up to 7,200+ pairs/day on a single M3 MacBook. Built for self-hosters and open-source contributors who want to produce training data they can trust.

## Numbers

| Participants | Day | Week | Month | 6 Months | Year | 2 Years |
|---|---|---|---|---|---|---|
| 1 | 7.2K | 50.4K | 216K | 1.3M | 2.6M | 5.2M |
| 50 | 360K | 2.5M | 10.8M | 65M | 131M | 262M |
| 100 | 720K | 5M | 21.6M | 131M | 262M | 524M |
| 1,000 | 7.2M | 50.4M | 216M | 1.3B | 2.6B | 5.2B |

> *"The best way to predict the future is to generate it."*

## Live Stats — `PeetPedro/ultrawhale-dogfood`

![files](https://img.shields.io/badge/files-291-blue)
![range](https://img.shields.io/badge/range-Jun%2023%E2%80%9324%202026-lightgrey)
![pairs](https://img.shields.io/badge/pairs-%7E2.9K-brightgreen)
&nbsp; [![view](https://img.shields.io/badge/view_on_HF-yellow?logo=huggingface)](https://huggingface.co/datasets/PeetPedro/ultrawhale-dogfood)

#### random samples

[chain-of-thought reasoning](https://huggingface.co/datasets/PeetPedro/ultrawhale-dogfood/viewer/../resolve/main/dogfeed-loop-99-20260624-015219.jsonl)
· [what is a dogfeed in ML?](https://huggingface.co/datasets/PeetPedro/ultrawhale-dogfood/viewer/../resolve/main/dogfeed-loop-99-20260624-015219.jsonl)

## Quickstart

```bash
# Install (from source)
git clone https://github.com/peterlodri-sec/ultrawhale-dogfood-pipeline.git
cd ultrawhale-dogfood-pipeline
pip install .

# Generate 100 Q&A pairs (requires a running llama.cpp server)
ultrawhale generate --num 100 --category cs --host http://localhost:8080

# Check pipeline health
ultrawhale status

# Upload results to HuggingFace (requires HF_TOKEN)
ultrawhale upload
```

## Features

- **Parallel Generation** — Multi-worker architecture with dynamic autoscaling (2-8 workers)
- **Quality Gating** — Every pair scored on coherence, length, and diversity; LLM-judge validated
- **HF Inference Fallback** — Low-quality local outputs automatically fall back to HF-hosted Llama70B
- **Difficulty Sampling** — Active learning distributes questions across easy/medium/hard tiers
- **Structured Logging** — JSON or human-readable, with per-component tagging
- **Async I/O** — Queue-based writer ensures generation is never blocked by disk I/O
- **Resource Management** — Automatic CPU/memory monitoring prevents system crashes
- **Kompress-v8** — Post-processing compression for compact dataset storage

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    Orchestrator                       │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐              │
│  │Worker 1 │  │Worker 2 │  │Worker N │  ...2-8      │
│  │  (CS)   │  │(Physics)│  │(General)│              │
│  └────┬────┘  └────┬────┘  └────┬────┘              │
│       │            │            │                     │
│       ▼            ▼            ▼                     │
│  ┌─────────────────────────────────────┐             │
│  │       Quality Scoring + Curation    │             │
│  │  (coherence · length · diversity)   │             │
│  └──────────────┬──────────────────────┘             │
│                 │                                     │
│                 ▼                                     │
│  ┌─────────────────────────────────────┐             │
│  │       Async Writer (JSONL)          │             │
│  └──────────────┬──────────────────────┘             │
│                 │                                     │
│                 ▼                                     │
│  ┌─────────────────────────────────────┐             │
│  │    Kompress-v8 → HF Dataset Upload  │             │
│  └─────────────────────────────────────┘             │
└──────────────────────────────────────────────────────┘
```

## Configuration

All settings are configured via environment variables — no config files needed.

| Variable | Default | Description |
|----------|---------|-------------|
| `HF_TOKEN` | — | HuggingFace API token (required for upload + HF inference) |
| `LLM_HOST` | `http://localhost:8080` | LLM server URL |
| `LLM_MODEL` | `qwen3.6-27b` | Model name served by the server |
| `LLAMA_SERVER_BIN` | `/opt/homebrew/bin/llama-server` | llama.cpp binary path |
| `ULTRAWHALE_MAX_WORKERS` | `8` | Maximum parallel workers |
| `ULTRAWHALE_MIN_WORKERS` | `2` | Minimum parallel workers |
| `ULTRAWHALE_MIN_SCORE` | `0.65` | Minimum quality score threshold |
| `ULTRAWHALE_LOG_DIR` | `ralph_logs/` | Log output directory |
| `ULTRAWHALE_OUTPUT_DIR` | `dogfeed_parallel/` | Dataset output directory |

## Commands

```bash
ultrawhale generate   # Generate Q&A pairs
ultrawhale upload     # Upload dogfeed to HuggingFace
ultrawhale compress   # Post-process with kompress-v8
ultrawhale status     # Pipeline health check
```

## Development

```bash
git clone https://github.com/peterlodri-sec/ultrawhale-dogfood-pipeline.git
cd ultrawhale-dogfood-pipeline
uv sync --all-extras
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on setup, workflow, and PR process.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for deep architecture details.

## License

MIT — see [LICENSE](LICENSE).
