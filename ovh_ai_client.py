#!/usr/bin/env python3
"""OVHCloud AI Endpoints integration."""

import os
import sys
from typing import Optional

try:
    import requests
except ImportError:
    print("Error: requests not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


class OVHAIClient:
    """Client for OVHCloud AI Endpoints."""

    MODELS = {
        "qwen-embedding": "Qwen3-Embedding-8B",
        "qwen-9b": "Qwen3.5-9B",
        "qwen-397b": "Qwen3.5-397B-A17B",
        "gpt-oss-120b": "gpt-oss-120b",
        "llama-70b": "Meta-Llama-3_3-70B-Instruct",
    }

    def __init__(self, endpoint_url: Optional[str] = None, token_file: str = ".ovh_ai_token"):
        """Initialize with endpoint and token."""
        self.endpoint_url = (endpoint_url or os.getenv("OVH_AI_ENDPOINT_URL") or
                           "https://oai.endpoints.kepler.ai.cloud.ovh.net")

        # Read token from file
        if os.path.exists(token_file):
            with open(token_file) as f:
                self.token = f.read().strip()
        else:
            self.token = os.getenv("OVH_AI_ENDPOINTS_ACCESS_TOKEN")

        if not self.token:
            raise ValueError("OVH_AI_ENDPOINTS_ACCESS_TOKEN not set or token file not found")

    def generate_text(self, prompt: str, model: str, max_tokens: int = 200, temperature: float = 0.7) -> Optional[str]:
        """Generate text via OVHCloud AI Endpoints."""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            response = requests.post(
                f"{self.endpoint_url}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()
            if "choices" in data and len(data["choices"]) > 0:
                return data["choices"][0].get("message", {}).get("content")
            return None

        except Exception as e:
            print(f"[OVH] Generation error: {e}", file=sys.stderr)
            return None

    def generate_qa_pair(self, topic: str, model: str, question_type: str = "conceptual") -> Optional[tuple]:
        """Generate Q&A pair via OVHCloud."""
        q_prompt = f"Generate a {question_type} question about {topic}. Only output the question."
        question = self.generate_text(q_prompt, model, max_tokens=100, temperature=0.7)

        if not question:
            return None

        a_prompt = f"Answer this question concisely:\n{question}"
        answer = self.generate_text(a_prompt, model, max_tokens=200, temperature=0.7)

        if not answer:
            return None

        return question, answer


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OVHCloud AI Endpoints client")
    parser.add_argument("--endpoint", required=True, help="OVHCloud AI endpoint URL")
    parser.add_argument("--model", required=True, help="Model slug/ID")
    parser.add_argument("--test", action="store_true", help="Test connection")

    args = parser.parse_args()

    try:
        client = OVHAIClient(endpoint_url=args.endpoint)
        print(f"✓ OVHCloud client initialized", file=sys.stderr)

        if args.test:
            result = client.generate_text("Say hello", args.model, max_tokens=20)
            print(f"Test response: {result}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
