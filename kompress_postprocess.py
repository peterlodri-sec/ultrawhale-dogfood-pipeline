#!/usr/bin/env python3
"""Post-processing: compress generated Q&A pairs with kompress-v8."""

import json
import sys
from typing import Optional

try:
    from huggingface_hub import InferenceClient
except ImportError:
    print("Error: huggingface_hub not installed", file=sys.stderr)
    sys.exit(1)


class KompressClient:
    """Wrapper for kompress-v8 (context pruner/compressor)."""

    MODEL = "PeetPedro/kompress-v8"

    def __init__(self, api_token: Optional[str] = None):
        import os
        self.token = api_token or os.getenv("HF_TOKEN")
        if not self.token:
            raise ValueError("HF_TOKEN not set")
        self.client = InferenceClient(api_key=self.token)

    def compress_text(self, text: str, max_tokens: int = 200) -> Optional[str]:
        """Compress text using kompress-v8."""
        try:
            prompt = f"Compress this concisely (max {max_tokens} tokens):\n{text}"
            response = self.client.text_generation(
                prompt,
                model=self.MODEL,
                max_new_tokens=max_tokens,
                temperature=0.3,  # Lower temp for deterministic compression
            )
            return response.strip() if response else None
        except Exception as e:
            print(f"[Kompress] Compression failed: {e}", file=sys.stderr)
            return None

    def compress_qa_pair(self, question: str, answer: str) -> dict:
        """Compress Q&A pair, keep original if compression fails."""
        q_compressed = self.compress_text(question, max_tokens=80)
        a_compressed = self.compress_text(answer, max_tokens=150)

        return {
            "question": q_compressed or question,
            "answer": a_compressed or answer,
            "compressed": bool(q_compressed and a_compressed),
        }


def compress_jsonl_file(input_file: str, output_file: str, api_token: Optional[str] = None):
    """Compress all Q&A pairs in JSONL file."""
    try:
        kompressor = KompressClient(api_token)
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 0

    compressed_count = 0
    total_count = 0

    try:
        with open(input_file) as inf, open(output_file, "w") as outf:
            for line in inf:
                if not line.strip():
                    continue

                total_count += 1
                try:
                    pair = json.loads(line)

                    # Compress Q&A
                    compressed = kompressor.compress_qa_pair(
                        pair.get("user_message", ""),
                        pair.get("free_response", ""),
                    )

                    if compressed["compressed"]:
                        pair["user_message"] = compressed["question"]
                        pair["free_response"] = compressed["answer"]
                        pair["kompressed_at"] = True
                        compressed_count += 1

                    outf.write(json.dumps(pair) + "\n")

                    if total_count % 10 == 0:
                        print(f"[Kompress] {total_count} processed, {compressed_count} compressed...", file=sys.stderr)

                except json.JSONDecodeError:
                    print(f"[⚠] Skipped invalid JSON at line {total_count}", file=sys.stderr)

    except Exception as e:
        print(f"[ERROR] File processing failed: {e}", file=sys.stderr)
        return compressed_count

    print(f"[✓] Compression complete: {compressed_count}/{total_count} pairs compressed", file=sys.stderr)
    return compressed_count


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Compress Q&A pairs with kompress-v8")
    parser.add_argument("input", help="Input JSONL file")
    parser.add_argument("--output", help="Output JSONL file (default: input_kompressed.jsonl)")
    parser.add_argument("--token", help="HF_TOKEN (default: env HF_TOKEN)")

    args = parser.parse_args()
    output = args.output or args.input.replace(".jsonl", "_kompressed.jsonl")

    count = compress_jsonl_file(args.input, output, args.token)
    sys.exit(0 if count >= 0 else 1)
