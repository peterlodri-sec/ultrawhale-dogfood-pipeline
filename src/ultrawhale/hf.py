#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""HuggingFace Inference API wrapper for fallback generation (Phase 2).

Migrated from self-host-llm/hf_inference.py into the ultrawhale package.
"""

from ultrawhale.config import Config
from ultrawhale.logging import get_logger

logger = get_logger("hf")


class HFInferenceClient:
    """Wrapper for HF Inference API with model selection.

    Uses the project-wide Config for the HF token so there is a single
    source of truth for credentials.
    """

    MODELS: dict[str, str] = {
        "llama8b": "meta-llama/Meta-Llama-3-8B-Instruct",
        "mixtral": "mistralai/Mistral-7B-Instruct-v0.3",
        "hermes": "HuggingFaceH4/zephyr-7b-beta",
        "kompress": "PeetPedro/kompress-v8",
        "ralph": "RalphLabsAI/Ralph-1",
    }

    HF_API_URL: str = "https://api-inference.huggingface.co/models/{model_id}/v1/chat/completions"

    def __init__(self, api_token: str | None = None) -> None:
        """Initialize the HF Inference client.

        Args:
            api_token: Explicit HF token. Falls back to Config.hf_token if
                       not provided.

        Raises:
            ValueError: When no token is available.
        """
        self.token: str = ""
        if api_token:
            self.token = api_token
        else:
            cfg = Config()
            if not cfg.hf_token:
                raise ValueError(
                    "HF token is not available — set HF_TOKEN env var or provide an explicit api_token argument."
                )
            self.token = cfg.hf_token

        logger.info("HFInferenceClient initialised (token present)")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _chat(
        self,
        messages: list,
        model_key: str,
        max_tokens: int = 200,
    ) -> str | None:
        """Chat completion via HF direct inference API (no provider routing).

        Args:
            messages: OpenAI-style message list.
            model_key: Key into ``MODELS`` dict.
            max_tokens: Maximum tokens for the response.

        Returns:
            The assistant message content, or ``None`` on failure.
        """
        model_id: str = self.MODELS.get(model_key, self.MODELS["llama8b"])
        url: str = self.HF_API_URL.format(model_id=model_id)

        try:
            import requests as req
        except ImportError:
            logger.critical("requests library is not installed — run: pip install requests")
            return None

        try:
            resp = req.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
                timeout=25,
            )
            resp.raise_for_status()
            content = str(resp.json()["choices"][0]["message"]["content"].strip())
            logger.debug(
                "Chat response received (model=%s, tokens=%d)",
                model_key,
                max_tokens,
            )
            return content
        except req.exceptions.Timeout:
            logger.warning("Chat request timed out (model=%s, url=%s)", model_key, url)
            return None
        except req.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            logger.error(
                "Chat request failed (model=%s, status=%s): %s",
                model_key,
                status,
                exc,
            )
            return None
        except Exception as exc:
            logger.error("Unexpected error in _chat (model=%s): %s", model_key, exc)
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_question(
        self,
        topic: str,
        question_type: str = "conceptual",
        model_key: str = "llama8b",
    ) -> str | None:
        """Generate a question about *topic* via HF Inference API.

        Args:
            topic: The subject to ask about.
            question_type: One of ``"conceptual"``, ``"practical"``,
                           ``"theoretical"``.
            model_key: Key into ``MODELS``.

        Returns:
            The generated question text, or ``None`` on failure.
        """
        prompts: dict[str, str] = {
            "conceptual": (
                f"Generate a clear, fundamental question about {topic}. "
                "Focus on core concepts. Only output the question itself."
            ),
            "practical": (f"Generate a practical coding question related to {topic}. Only output the question itself."),
            "theoretical": (f"Generate a theoretical question about {topic}. Only output the question itself."),
        }
        prompt: str = prompts.get(question_type, prompts["conceptual"])
        logger.info(
            "Generating %s question about '%s' via %s",
            question_type,
            topic,
            model_key,
        )
        return self._chat([{"role": "user", "content": prompt}], model_key, max_tokens=100)

    def answer_question(
        self,
        question: str,
        model_key: str = "llama8b",
    ) -> str | None:
        """Generate an answer for a given question via HF Inference API.

        Args:
            question: The question text.
            model_key: Key into ``MODELS``.

        Returns:
            The generated answer text, or ``None`` on failure.
        """
        logger.info("Answering question via %s", model_key)
        return self._chat(
            [{"role": "user", "content": f"Answer concisely:\n{question}"}],
            model_key,
            max_tokens=400,
        )

    def generate_qa_pair(
        self,
        topic: str,
        question_type: str = "conceptual",
        model_key: str = "llama8b",
    ) -> tuple | None:
        """Generate a full Q&A pair via HF Inference API.

        Args:
            topic: The subject to ask about.
            question_type: One of ``"conceptual"``, ``"practical"``,
                           ``"theoretical"``.
            model_key: Key into ``MODELS``.

        Returns:
            ``(question, answer)`` tuple, or ``None`` if either step fails.
        """
        logger.info(
            "Generating full Q&A pair (topic=%s, type=%s, model=%s)",
            topic,
            question_type,
            model_key,
        )
        question = self.generate_question(topic, question_type, model_key)
        if not question:
            logger.warning("Question generation returned nothing — aborting Q&A pair")
            return None

        answer = self.answer_question(question, model_key)
        if not answer:
            logger.warning("Answer generation returned nothing — aborting Q&A pair")
            return None

        logger.info("Q&A pair generated successfully")
        return question, answer
