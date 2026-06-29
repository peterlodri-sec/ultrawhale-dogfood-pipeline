#!/usr/bin/env python3
"""HuggingFace Inference API wrapper for fallback generation (Phase 2)."""

import os
import sys
from typing import Optional

try:
    from huggingface_hub import InferenceClient
except ImportError:
    print("Error: huggingface_hub not installed. Run: pip install huggingface_hub", file=sys.stderr)
    sys.exit(1)


class HFInferenceClient:
    """Wrapper for HF Inference API with model selection."""

    MODELS = {
        "llama70b": "meta-llama/Meta-Llama-3-8B-Instruct",
        "mixtral": "mistralai/Mistral-7B-Instruct-v0.3",
        "hermes": "HuggingFaceH4/zephyr-7b-beta",
        "kompress": "PeetPedro/kompress-v8",
        "ralph": "RalphLabsAI/Ralph-1",
    }

    HF_API_URL = "https://api-inference.huggingface.co/models/{model_id}/v1/chat/completions"

    def __init__(self, api_token: Optional[str] = None):
        self.token = api_token or os.getenv("HF_TOKEN")
        if not self.token:
            raise ValueError("HF_TOKEN not set")
        try:
            import requests as req
            self._requests = req
        except ImportError:
            raise ImportError("requests not installed. Run: pip install requests")

    def _chat(self, messages: list, model_key: str, max_tokens: int = 200) -> Optional[str]:
        """Chat completion via HF direct inference API (no provider routing)."""
        model_id = self.MODELS.get(model_key, self.MODELS["llama70b"])
        url = self.HF_API_URL.format(model_id=model_id)
        try:
            resp = self._requests.post(
                url,
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                json={"messages": messages, "max_tokens": max_tokens, "temperature": 0.7},
                timeout=25,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"[HF] chat failed ({model_key}@{model_id}): {e}", file=sys.stderr)
            return None

    def generate_question(self, topic: str, question_type: str = "conceptual", model_key: str = "llama70b") -> Optional[str]:
        """Generate question via HF Inference API."""
        prompts = {
            "conceptual": f"Generate a clear, fundamental question about {topic}. Focus on core concepts. Only output the question itself.",
            "practical": f"Generate a practical coding question related to {topic}. Only output the question itself.",
            "theoretical": f"Generate a theoretical question about {topic}. Only output the question itself.",
        }
        prompt = prompts.get(question_type, prompts["conceptual"])
        return self._chat([{"role": "user", "content": prompt}], model_key, max_tokens=100)

    def answer_question(self, question: str, model_key: str = "llama70b") -> Optional[str]:
        """Generate answer via HF Inference API."""
        return self._chat(
            [{"role": "user", "content": f"Answer concisely:\n{question}"}],
            model_key,
            max_tokens=400,
        )

    def generate_qa_pair(self, topic: str, question_type: str = "conceptual", model_key: str = "llama70b") -> Optional[tuple]:
        """Generate full Q&A pair via HF Inference."""
        question = self.generate_question(topic, question_type, model_key)
        if not question:
            return None

        answer = self.answer_question(question, model_key)
        if not answer:
            return None

        return question, answer
