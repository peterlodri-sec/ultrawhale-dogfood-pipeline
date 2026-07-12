# Ultrawhale Dogfeed Pipeline

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/logo.svg">
  <img alt="Ultrawhale Dogfeed Pipeline" src="./assets/logo.svg" width="160" align="right">
</picture>

[![CI](https://github.com/peterlodri-sec/ultrawhale-dogfood-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/peterlodri-sec/ultrawhale-dogfood-pipeline/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12+-00d4ff.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-00e660.svg)](LICENSE)
[![HF Dataset](https://img.shields.io/badge/%20dataset-PeetPedro/ultrawhale--dogfood-ffb020)](https://huggingface.co/datasets/PeetPedro/ultrawhale-dogfood)
[![Q&A Pairs](https://img.shields.io/badge/Q%26A%20pairs-%7E24K-brightgreen)](https://huggingface.co/datasets/PeetPedro/ultrawhale-dogfood)

`qa-synthesis` `llm-judge` `pi-feeder` `ultrawhale` `dogfeed` `self-host`

**Industrial-grade Q&A data synthesis pipeline for training LLMs — now with Pi-powered dog feeding.**

Generates high-quality, LLM-judge-validated Q&A pairs at scale — up to 7,200+ pairs/day on a single M3 MacBook. Built for self-hosters and open-source contributors who want to produce training data they can trust. Optionally burns to Raspberry Pi 3B+ for automated motor/servo feeding with real-time HuggingFace dataset upload.

## Numbers

| Participants | Day | Week | Month | 6 Months | Year | 2 Years |
|---|---|---|---|---|---|---|
| 1 | 7.2K | 50.4K | 216K | 1.3M | 2.6M | 5.2M |
| 50 | 360K | 2.5M | 10.8M | 65M | 131M | 262M |
| 100 | 720K | 5M | 21.6M | 131M | 262M | 524M |
| 1,000 | 7.2M | 50.4M | 216M | 1.3B | 2.6B | 5.2B |

> *"The best way to predict the future is to generate it."*
>
> **why?** I cannot explain it honestly, I don't know, this just feels cool to **me**,  
> **anon + global + fully honest public datasetgen+dataset? please copy me ^^**  
>
> **SUPER-ULTRA-DEVELOPMENT-MODE** — this is the notice, call for action etc.  
> I NEVER SAID ANYWHERE THAT THIS IS PERFECT. It's not. Help make it one.

## Live Stats — `PeetPedro/ultrawhale-dogfood`

![files](https://img.shields.io/badge/files-407-blue)
![range](https://img.shields.io/badge/range-Jun%2022%E2%80%93Jul%2012%202026-lightgrey)
![pairs](https://img.shields.io/badge/pairs-%7E24K-brightgreen)
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

# --- Pi Dog Feeding ---
# Burn SD card (insert card first)
task start

# Or step by step:
task pi:check     # Check dependencies
task pi:burn      # Download Pi OS, sign dataset, burn SD card
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
- **Pi Dog Feeding** — Burn Pi OS with signed dataset, GPIO motor/servo control, background HF upload on every cycle
- **Dual-Mode** — Same code runs on Pi (GPIO feeding + dataset gen) or generic machine (dataset gen only)

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
│                                                       │
│  ┌─────────────────────────────────────┐             │
│  │  Pi Feeder Node (optional)          │             │
│  │  GPIO motor/servo → telemetry/      │             │
│  │  LLM gen → datasets/                │             │
│  │  bg upload thread → HF              │             │
│  └─────────────────────────────────────┘             │
└──────────────────────────────────────────────────────┘
```

## Configuration

All settings are configured via environment variables — no config files needed.

| Variable | Default | Description |
|----------|---------|-------------|
| `HF_TOKEN` | — | HuggingFace API token (required for upload + HF inference) |
| `OPENROUTER_API_KEY` | — | OpenRouter key (free inference fallback if HF Inference API down) |
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

task start            # E2E: insert SD → burn Pi OS → sign dataset → configure
task pi:check         # Check Pi burner dependencies
task pi:burn          # Download Pi OS, sign, dual-burn SD card
```

## Development

```bash
git clone https://github.com/peterlodri-sec/ultrawhale-dogfood-pipeline.git
cd ultrawhale-dogfood-pipeline
uv sync --all-extras
uv run pytest

# Test dog feeding pipeline in generic mode (no Pi needed)
HF_TOKEN=your_token python3 src/ultrawhale/dog_feeding.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on setup, workflow, and PR process.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for deep architecture details.

## License

MIT — see [LICENSE](LICENSE).

---

## Pi Dog Feeding Burner

> ** NOTICE — E2E: Once you start a Pi with it, there is no SSH, no user access, no stopping it from outside.**
> The Pi boots, verifies the dataset signature, and runs autonomously — feeding on schedule and uploading to HuggingFace.
> To stop: disconnect power, remove SD card, run `/boot/erase_sd.sh` on another machine to wipe it.

End-to-end dog feeding solution for Raspberry Pi 3B+. Burns a signed Pi OS image, configures GPIO motor/servo control, and uploads events + generated datasets to HuggingFace.

### Components
1. **Pi Burner Script** (`pi_burner.sh`)
   - Downloads latest Pi OS Lite 64-bit
   - Signs dataset config with SHA256
   - Dual-burn SD card for reliability
   - Writes config.txt (WiFi/BT disabled, Ethernet only)
   - Adds verify_dataset.sh + erase_sd.sh + SETUP.txt

2. **Dog Feeding Pipeline** (`src/ultrawhale/dog_feeding.py`)
   - Auto-detects Pi vs generic mode
   - GPIO motor+servo feeding on schedule (Pi) or mock (generic)
   - Real Q&A generation via HF Inference API or OpenRouter fallback
   - Background upload thread for telemetry + datasets
   - Dataset signature verification on boot

### Security
- Dataset signed with SHA256, verified on every boot
- No SSH, no user accounts
- Secure erase script (`/boot/erase_sd.sh`)
- Ethernet-only (WiFi/BT disabled via dtoverlay)
- Config tampering → feed pipeline halts
