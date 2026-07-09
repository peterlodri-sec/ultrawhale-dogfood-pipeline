# Changelog

All notable changes to this project will be documented in this file.

## [2.1.0] - 2026-07-09

### Added
- **Pi Dog Feeding Burner** — `pi_burner.sh` downloads Pi OS Lite 64-bit, signs dataset, dual-burns SD card, writes headless config (WiFi/BT disabled, Ethernet only, GPIO motor/servo).
- **Dog Feeding Pipeline** (`dog_feeding.py`) — auto-detects Pi vs generic mode, GPIO motor+servo on schedule (Pi) or mock (generic), real Q&A gen via HF Inference + OpenRouter fallback, background upload thread for telemetry + datasets.
- **Dogfeed quality phases 1–4** — quality scoring, HF Inference hybrid backend, parallel ralph orchestrator, difficulty-aware active learning sampling.
- **HF dataset upload** — 383 dogfeed JSONL files across Jun 22–29 now live on `PeetPedro/ultrawhale-dogfood`.

### Fixed
- macOS SD card detection: filter disk images via `diskutil info`; SD reader no longer treated as internal disk.
- `dd` write: unmount disk, use `/dev/rdisk` for raw write, filter disk images from target list.
- Disk image detection grep: `Virtual: No` was false positive on macOS; switched to correct field.
- `second_burn`: unmount disk again before pass 2 (macOS auto-remounts after first write).
- mypy: rename `response` vars to avoid type clash between HF and OpenRouter return types.
- CI: inline workflow (shared workflow had `startup_failure` on first run).

### Changed
- Pi burner integrated into main README theme sections with E2E safety NOTICE.

## [2.0.0] - 2026-06-29

### Added
- **Package structure**: pip-installable `ultrawhale` package with `pyproject.toml`.
- **CLI**: `ultrawhale` command with `generate`, `upload`, `compress`, `status` subcommands.
- **Structured logging**: JSON and human-readable modes with per-component tagging.
- **Centralized configuration**: `Config` dataclass loading from env vars.
- **Exponential backoff**: Retry logic with jitter in generation and HF inference.
- **Graceful shutdown**: SIGTERM/SIGINT handlers in orchestrator.
- **Health check**: `ultrawhale status` reports config, connectivity, warnings.
- **Test suite**: 35 tests covering scoring, difficulty sampling, and curation.
- **CI/CD**: GitHub Actions workflow (lint, type-check, test, build) + release pipeline.
- **Documentation**: Overhauled README, architecture doc, contributing guide.
- **Deployment**: Dockerfile, docker-compose, pre-commit hooks.
- **License**: MIT with SPDX headers on all source files.

### Changed
- **Module migration**: All 12 scripts moved to `src/ultrawhale/` with proper packaging.
- **Logging**: All `print()` calls replaced with structured logger calls.
- **Config**: Hardcoded paths, thresholds, and secrets extracted to env vars.
- **Error handling**: Bare `except: pass` removed; broad exceptions narrowed with warnings.
- **Random state**: `difficulty.py` uses instance-level RNG (no global seed side effects).
- **Quality scoring**: Extracted to pure `scoring.py` module with testable functions.
- **Upload**: Merged upload scripts into idempotent `upload.py` with retry.

### Fixed
- Token isolation: `HF_TOKEN` masked in logs, never echoed.
- Subprocess safety: Quoted variable expansion, SIGTERM cleanup of child processes.
- Missing shebangs: All scripts now have `#!/usr/bin/env python3`.
- Empty `package.json`: Removed (no Node dependency).

## [1.0.0] - 2026-06-29
### Added
- Async writer thread in `generate_dogfeed.py` for high-throughput I/O.
- Quantized KV Cache (`q8_0`) in `llm-server.sh`.
- Pretty log output via `pretty_logs.sh`.
- Changelog and versioning structure.

### Changed
- Optimized `llm-server.sh` flags for SOTA throughput.
- Tuned `ResourceManager` limits for safer parallel execution.
- Increased `ROUND_TIMEOUT` for more robust task completion.
