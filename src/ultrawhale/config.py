# SPDX-License-Identifier: MIT
"""Centralized configuration for the Ultrawhale pipeline.

All hardcoded paths, thresholds, and secrets are loaded from environment
variables with sensible defaults, so nothing needs to be patched for
deployment.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Pipeline configuration loaded from environment variables.

    Every value has a default; only HF_TOKEN is strictly required for
    upload and HF inference operations.
    """

    # --- LLM server ---
    llm_host: str = field(
        default_factory=lambda: os.getenv("LLM_HOST", os.getenv("MISTRALRS_HOST", "http://localhost:8080"))
    )
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", os.getenv("MISTRALRS_MODEL", "qwen3.6-27b")))
    llama_server_bin: str = field(
        default_factory=lambda: os.getenv("LLAMA_SERVER_BIN", "/opt/homebrew/bin/llama-server")
    )

    # --- Paths ---
    log_dir: Path = field(default_factory=lambda: Path(os.getenv("ULTRAWHALE_LOG_DIR", str(Path.cwd() / "ralph_logs"))))
    output_dir: Path = field(
        default_factory=lambda: Path(os.getenv("ULTRAWHALE_OUTPUT_DIR", str(Path.cwd() / "dogfeed_parallel")))
    )

    # --- HuggingFace ---
    hf_token: str | None = field(default_factory=lambda: _load_hf_token())
    hf_repo: str = field(default_factory=lambda: os.getenv("ULTRAWHALE_HF_REPO", "PeetPedro/ultrawhale-dogfood"))

    # --- Resource limits ---
    max_memory_percent: float = field(default_factory=lambda: float(os.getenv("ULTRAWHALE_MAX_MEMORY_PCT", "50")))
    max_cpu_percent: float = field(default_factory=lambda: float(os.getenv("ULTRAWHALE_MAX_CPU_PCT", "75")))
    max_workers: int = field(default_factory=lambda: int(os.getenv("ULTRAWHALE_MAX_WORKERS", "8")))
    min_workers: int = field(default_factory=lambda: int(os.getenv("ULTRAWHALE_MIN_WORKERS", "2")))

    # --- Quality ---
    min_quality_score: float = field(default_factory=lambda: float(os.getenv("ULTRAWHALE_MIN_SCORE", "0.65")))
    curation_threshold: float = field(default_factory=lambda: float(os.getenv("ULTRAWHALE_CURATION_THRESHOLD", "4.0")))

    # --- Timing ---
    round_timeout: int = field(default_factory=lambda: int(os.getenv("ULTRAWHALE_ROUND_TIMEOUT", "120")))
    retry_interval: int = field(default_factory=lambda: int(os.getenv("ULTRAWHALE_RETRY_INTERVAL", "30")))
    upload_interval: int = field(default_factory=lambda: int(os.getenv("ULTRAWHALE_UPLOAD_INTERVAL", "120")))
    upload_active_grace: int = field(default_factory=lambda: int(os.getenv("ULTRAWHALE_UPLOAD_GRACE", "5")))

    def validate(self) -> list[str]:
        """Validate configuration and return list of warnings.

        Returns:
            List of warning strings. Empty list means all clear.
        """
        warnings: list[str] = []

        if not self.hf_token:
            warnings.append(
                "HF_TOKEN not set — HF inference and upload will fail. Set via HF_TOKEN env var or .env file."
            )

        if _is_openrouter_host(self.llm_host) and not self.openai_api_key():
            warnings.append(
                "OPENROUTER_API_KEY not set — OpenRouter generation will fail. Set OPENROUTER_API_KEY or LLM_API_KEY."
            )

        if self.min_quality_score < 0 or self.min_quality_score > 1:
            warnings.append(f"ULTRAWHALE_MIN_SCORE={self.min_quality_score} out of range [0,1] — using 0.65")
            self.min_quality_score = 0.65

        if self.max_memory_percent <= 0 or self.max_memory_percent > 100:
            warnings.append(f"ULTRAWHALE_MAX_MEMORY_PCT={self.max_memory_percent} invalid — using 50")
            self.max_memory_percent = 50

        return warnings

    def mask_token(self) -> str:
        """Return a masked version of the HF token for safe logging."""
        if not self.hf_token:
            return "<unset>"
        if len(self.hf_token) <= 8:
            return "*" * len(self.hf_token)
        return self.hf_token[:4] + "…" + self.hf_token[-4:]

    def openai_base_url(self, llm_host: str | None = None) -> str:
        """Return the OpenAI-compatible base URL for an LLM host."""
        return _normalize_openai_base_url(llm_host or self.llm_host)

    def openai_api_key(self, llm_host: str | None = None) -> str:
        """Return the API key for the OpenAI-compatible LLM endpoint."""
        return _resolve_openai_api_key(llm_host or self.llm_host)

    def mask_llm_api_key(self, llm_host: str | None = None) -> str:
        """Return a masked API key for safe LLM endpoint logging."""
        return _mask_secret(self.openai_api_key(llm_host))


def _load_hf_token() -> str | None:
    """Load HF_TOKEN from environment or .env file."""
    token = os.getenv("HF_TOKEN")
    if token:
        return token.strip()

    # Fallback: try loading from .env in cwd
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        try:
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    if key.strip() == "HF_TOKEN":
                        return value.strip().strip('"').strip("'")
        except OSError:
            pass

    return None


def _normalize_openai_base_url(llm_host: str) -> str:
    """Normalize an endpoint root to the OpenAI-compatible /v1 base URL."""
    host = llm_host.rstrip("/")
    if host.endswith("/v1"):
        return host
    return f"{host}/v1"


def _resolve_openai_api_key(llm_host: str) -> str:
    """Resolve the API key for local or remote OpenAI-compatible endpoints."""
    explicit = os.getenv("LLM_API_KEY")
    if explicit:
        return explicit.strip()

    if _is_openrouter_host(llm_host):
        return os.getenv("OPENROUTER_API_KEY", "").strip()

    return os.getenv("OPENAI_API_KEY", "none").strip() or "none"


def _is_openrouter_host(llm_host: str) -> bool:
    return "openrouter.ai" in llm_host.lower()


def _mask_secret(secret: str | None) -> str:
    if not secret:
        return "<unset>"
    if secret == "none":
        return "none"
    if len(secret) <= 8:
        return "*" * len(secret)
    return secret[:4] + "…" + secret[-4:]
