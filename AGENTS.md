# AGENTS.md — Ultrawhale Dogfeed Pipeline

## What This Is

Python 3.12+ CLI tool (`ultrawhale`) that generates LLM-judge-validated Q&A pairs at scale. Generates training data via local llama.cpp server, scores quality, uploads to HuggingFace. Optional Raspberry Pi dog-feeding mode.

## Setup

```bash
uv sync --all-extras        # install all deps (dev + merge extras)
```

Requires: Python 3.12+, uv. No config files — everything is env vars.

## Verification Commands (CI Order)

```bash
uv run ruff check src/ tests/                    # lint
uv run ruff format --check src/ tests/           # format check
uv run mypy src/ --ignore-missing-imports        # type check
uv run pytest -m "not requires_hf_token"         # tests (skip HF-dependent)
uv run pytest                                     # all tests (needs HF_TOKEN)
uv build                                          # package build
```

CI runs these in order. Run lint → format → typecheck → test before pushing.

## Project Structure

```
src/ultrawhale/       # package (hatchling wheel)
cli.py                # CLI entry: ultrawhale generate|upload|compress|status
config.py             # env var loading (no config files)
generate.py           # core generation engine
scoring.py            # quality scoring (coherence, length, diversity)
orchestrator.py       # parallel worker coordinator (2-8 workers)
dog_feeding.py        # Pi vs generic mode auto-detection, GPIO + HF upload
hf.py                 # HuggingFace Inference API client
upload.py             # HF dataset upload
kompress.py           # post-processing compression
difficulty.py         # difficulty-aware sampling
curation.py           # LLM-judge curation
logging.py            # structured logging setup
resources.py          # CPU/memory resource monitoring
tests/                # pytest, markers: slow, integration, requires_hf_token
```

## Key Conventions

- **Formatter/Linter**: ruff, line-length 120, target py312
- **Ruff rules**: E, F, I, N, W, UP, B, SIM, C4
- **Type checker**: mypy (non-strict, `--ignore-missing-imports` in CI)
- **Pre-commit**: ruff fix, ruff-format, isort, trailing-whitespace, detect-secrets
- **Commits**: Conventional Commits format (`feat(scoring): ...`)
- **Output dirs** (gitignored): `dogfeed_parallel/`, `ralph_logs/`, `hf_upload_staging/`
- **Dogfeed JSONL files** in root are gitignored — don't commit them
- **CI excludes** `_cold-archive` from ruff and mypy

## Runtime

- **LLM server**: `llm-server.sh` manages llama.cpp on port 8080 (Qwen3.6-27B Q4_K_M)
- **Full pipeline**: `run.sh` starts server → workers → upload loop (Ctrl+C stops all)
- **Task runner**: `task run` (uses Taskfile.yml), `task stop`, `task status`
- **Docker**: `docker compose up` (llama-server + orchestrator)

## Required Env Vars

| Var | Default | Purpose |
|-----|---------|---------|
| `HF_TOKEN` | — | HuggingFace upload + inference fallback |
| `LLM_HOST` | `http://localhost:8080` | LLM server URL (also checks `MISTRALRS_HOST`) |
| `LLM_MODEL` | `qwen3.6-27b` | Model name (also checks `MISTRALRS_MODEL`) |
| `LLAMA_SERVER_BIN` | `/opt/homebrew/bin/llama-server` | llama.cpp binary path |
| `ULTRAWHALE_MAX_WORKERS` | `8` | Max parallel workers |
| `ULTRAWHALE_MIN_WORKERS` | `2` | Min parallel workers |
| `ULTRAWHALE_MIN_SCORE` | `0.65` | Min quality score threshold |
| `ULTRAWHALE_LOG_DIR` | `ralph_logs/` | Log output directory |
| `ULTRAWHALE_OUTPUT_DIR` | `dogfeed_parallel/` | Dataset output directory |
| `ULTRAWHALE_HF_REPO` | `PeetPedro/ultrawhale-dogfood` | HF dataset repo |
| `OPENROUTER_API_KEY` | — | Optional fallback inference |

## Gotchas

- `run.sh` sources `.env` automatically; manual `ultrawhale` invocations don't, but `config.py` has its own `.env` fallback for `HF_TOKEN` only
- Workers run as `nohup` background processes — use `task stop` or `pkill` to clean up
- `mypy` in CI uses `--ignore-missing-imports` — don't add per-file ignores for missing imports
- `ruff format --check` (CI) fails if files aren't formatted — run `ruff format` before pushing
- `dogfeed_*.jsonl` and `dogfeed_parallel/` are gitignored — never commit
- Coverage omits `__main__.py`, `if __name__ == "__main__"`, `raise NotImplementedError`, and `def __repr__`
- **GPG signing**: `git commit` hangs if GPG agent not unlocked. Use `--no-gpg-sign` or `export GPG_TTY=$(tty)`

## Related

- **`audio-edge/`**: vaked-audio edge streaming engine (separate repo: [peterlodri-sec/vaked-audio-edge](https://github.com/peterlodri-sec/vaked-audio-edge)). TypeScript Worker + CF Container for Opus streaming + YouTube import. Web-only — no CLI or local apps.
