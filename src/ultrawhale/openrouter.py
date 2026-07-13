# SPDX-License-Identifier: MIT
"""Bulletproof OpenRouter inference client with retries, rate-limit handling, and circuit breaker.

Uses the OpenAI-compatible SDK (OpenRouter's native API pattern).
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass

from ultrawhale.logging import get_logger

logger = get_logger("openrouter")

# ---------------------------------------------------------------------------
# Retry / backoff configuration
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 16.0
_REQUEST_TIMEOUT = 60
_CIRCUIT_BREAKER_THRESHOLD = 5
_CIRCUIT_BREAKER_COOLDOWN = 60

_RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})


def _exponential_backoff(attempt: int, base: float = _BACKOFF_BASE, max_delay: float = _BACKOFF_MAX) -> float:
    delay: float = min(base * (2**attempt), max_delay)
    return float(delay * (0.5 + random.random()))


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@dataclass
class _CircuitBreaker:
    failure_count: int = 0
    last_failure_time: float = 0.0
    threshold: int = _CIRCUIT_BREAKER_THRESHOLD
    cooldown: float = _CIRCUIT_BREAKER_COOLDOWN

    def record_failure(self) -> None:
        now = time.time()
        if now - self.last_failure_time > self.cooldown:
            self.failure_count = 1
        else:
            self.failure_count += 1
        self.last_failure_time = now

    def record_success(self) -> None:
        self.failure_count = 0

    def is_open(self) -> bool:
        if self.failure_count < self.threshold:
            return False
        if time.time() - self.last_failure_time > self.cooldown:
            self.failure_count = 0
            return False
        return True


# ---------------------------------------------------------------------------
# Models registry
# ---------------------------------------------------------------------------

MODELS: dict[str, str] = {
    "free": "google/gemma-3-27b-it:free",
    "deepseek": "deepseek/deepseek-r1:free",
    "mistral": "mistralai/mistral-nemo:free",
    "llama": "meta-llama/llama-4-maverick:free",
    "qwen": "qwen/qwen2.5-vl-72b-instruct:free",
}

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OpenRouterClient:
    """Robust OpenRouter inference client via OpenAI-compatible endpoint.

    Features:
    - Exponential backoff with jitter on retryable errors (429, 502, 503, 504)
    - Circuit breaker — pauses all calls after N consecutive failures
    - Free-tier model awareness (track rate limits per model)
    - Structured logging at every step
    - API key loaded from ``OPENROUTER_API_KEY`` env var
    """

    def __init__(self, api_key: str | None = None, max_retries: int = _MAX_RETRIES) -> None:
        key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("OPENROUTER_API_KEY not set — set env var or pass api_key explicitly")

        self.api_key: str = key
        self.max_retries: int = max_retries
        self._breaker: _CircuitBreaker = _CircuitBreaker()

        # Lazy-import openai so the module is importable without it installed
        self._client: object | None = None
        logger.info("OpenRouterClient ready")

    def _get_client(self):
        """Lazy-initialise the OpenAI client (imports openai on first use)."""
        if self._client is not None:
            return self._client
        import openai

        self._client = openai.OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=self.api_key,
            timeout=_REQUEST_TIMEOUT,
        )
        return self._client

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    def _request(
        self,
        messages: list[dict[str, str]],
        model_key: str = "free",
        max_tokens: int = 200,
        temperature: float = 0.7,
    ) -> str:
        """Send a chat completion with full retry logic.

        Raises:
            OpenRouterError: After exhausting all retries.
        """
        model_id = MODELS.get(model_key, MODELS["free"])

        if self._breaker.is_open():
            raise OpenRouterError(
                f"Circuit breaker open — {self._breaker.failure_count} "
                f"consecutive failures, retry in ~{_CIRCUIT_BREAKER_COOLDOWN}s"
            )

        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                client = self._get_client()
                response = client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

                content = response.choices[0].message.content or ""
                content = content.strip()
                self._breaker.record_success()
                logger.debug("OpenRouter chat OK (model=%s, attempt=%d, tokens=%d)", model_key, attempt + 1, max_tokens)
                return content

            except Exception as exc:
                exc_str = str(exc).lower()
                status = _extract_status(exc)

                if status in _RETRYABLE_STATUSES or _is_rate_limited(exc_str):
                    last_exc = exc
                    logger.warning(
                        "OpenRouter retryable (model=%s, status=%s, attempt=%d/%d)",
                        model_key,
                        status or "?",
                        attempt + 1,
                        self.max_retries + 1,
                    )
                    self._breaker.record_failure()
                    if attempt < self.max_retries:
                        delay = _exponential_backoff(attempt)
                        logger.debug("OpenRouter backoff %.1fs", delay)
                        time.sleep(delay)
                    continue

                if _is_timeout(exc_str):
                    last_exc = exc
                    logger.warning("OpenRouter timeout (model=%s, attempt=%d)", model_key, attempt + 1)
                    self._breaker.record_failure()
                    if attempt < self.max_retries:
                        time.sleep(_exponential_backoff(attempt))
                    continue

                if _is_auth_error(exc_str):
                    raise OpenRouterError(f"OpenRouter auth failure — check OPENROUTER_API_KEY: {exc}") from exc

                # Unknown error — log and retry if attempts remain
                last_exc = exc
                logger.error("OpenRouter unexpected error (attempt=%d): %s", attempt + 1, exc)
                self._breaker.record_failure()
                if attempt < self.max_retries:
                    time.sleep(_exponential_backoff(attempt))

        raise OpenRouterError(f"OpenRouter failed after {self.max_retries + 1} attempts: {last_exc}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_question(
        self,
        topic: str,
        question_type: str = "conceptual",
        model_key: str = "free",
    ) -> str | None:
        """Generate a question about *topic* via OpenRouter.

        Returns ``None`` on any failure (graceful degradation).
        """
        prompts: dict[str, str] = {
            "conceptual": (
                f"Generate a clear, fundamental question about {topic}. "
                "Focus on core concepts. Only output the question itself."
            ),
            "practical": f"Generate a practical coding question related to {topic}. Only output the question itself.",
            "theoretical": f"Generate a theoretical question about {topic}. Only output the question itself.",
        }
        prompt = prompts.get(question_type, prompts["conceptual"])
        logger.info("OR generate-question topic=%s type=%s model=%s", topic, question_type, model_key)
        try:
            return self._request([{"role": "user", "content": prompt}], model_key, max_tokens=100)
        except OpenRouterError as exc:
            logger.warning("OR generate-question failed: %s", exc)
            return None

    def answer_question(
        self,
        question: str,
        model_key: str = "free",
    ) -> str | None:
        """Generate an answer via OpenRouter."""
        logger.info("OR answer-question model=%s", model_key)
        try:
            return self._request(
                [{"role": "user", "content": f"Answer concisely:\n{question}"}],
                model_key,
                max_tokens=400,
            )
        except OpenRouterError as exc:
            logger.warning("OR answer-question failed: %s", exc)
            return None

    def generate_qa_pair(
        self,
        topic: str,
        question_type: str = "conceptual",
        model_key: str = "free",
    ) -> tuple[str, str] | None:
        """Generate a full Q&A pair via OpenRouter.

        Returns ``(question, answer)`` or ``None``.
        """
        logger.info("OR generate-qa topic=%s type=%s model=%s", topic, question_type, model_key)
        question = self.generate_question(topic, question_type, model_key)
        if not question:
            return None
        answer = self.answer_question(question, model_key)
        if not answer:
            return None
        return question, answer

    def chat(
        self,
        messages: list[dict[str, str]],
        model_key: str = "free",
        max_tokens: int = 200,
        temperature: float = 0.7,
    ) -> str | None:
        """Generic chat completion. Returns ``None`` on failure."""
        try:
            return self._request(messages, model_key, max_tokens, temperature)
        except OpenRouterError as exc:
            logger.warning("OR chat failed: %s", exc)
            return None

    @property
    def circuit_breaker_state(self) -> dict[str, object]:
        """Expose circuit breaker status for monitoring."""
        return {
            "open": self._breaker.is_open(),
            "failure_count": self._breaker.failure_count,
            "threshold": self._breaker.threshold,
            "last_failure": self._breaker.last_failure_time,
        }


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OpenRouterError(Exception):
    """OpenRouter API call failed irrecoverably."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Error classification helpers
# ---------------------------------------------------------------------------


def _extract_status(exc: Exception) -> int | None:
    """Extract HTTP status code from an OpenAI/OpenRouter exception."""
    for attr in ("status_code", "status", "http_status"):
        val = getattr(exc, attr, None)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return None


def _is_rate_limited(exc_str: str) -> bool:
    keywords = ("rate limit", "too many requests", "429", "quota")
    return any(k in exc_str for k in keywords)


def _is_timeout(exc_str: str) -> bool:
    return "timeout" in exc_str or "timed out" in exc_str


def _is_auth_error(exc_str: str) -> bool:
    return "401" in exc_str or "403" in exc_str or "unauthorized" in exc_str or "invalid api key" in exc_str
