# Changelog

All notable changes to this project will be documented in this file.

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
