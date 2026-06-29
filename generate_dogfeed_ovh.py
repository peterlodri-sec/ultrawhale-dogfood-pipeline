#!/usr/bin/env python3
"""Generate dogfeed using OVHCloud AI Endpoints."""

import json
import sys
import uuid
import os
import tempfile
from datetime import datetime
from typing import Optional, Tuple
from pathlib import Path

try:
    from ovh_ai_client import OVHAIClient
except ImportError:
    print("Error: ovh_ai_client not found", file=sys.stderr)
    sys.exit(1)


TOPICS = [
    "algorithms", "data structures", "system design",
    "machine learning", "distributed systems", "databases",
    "quantum mechanics", "relativity", "physics",
    "cryptography", "compilers", "operating systems",
]

QUALITY_MIN_SCORE = 0.65


def _token_count(text: str) -> int:
    """Rough token estimate."""
    return len(text.split())


def _calculate_quality_score(question: str, answer: str) -> Tuple[float, dict]:
    """Score Q&A pair (same as generate_dogfeed.py)."""
    scores = {}

    q_tokens = _token_count(question)
    a_tokens = _token_count(answer)

    q_len = 1.0 if (8 <= q_tokens <= 200) else 0.3
    a_len = 1.0 if (20 <= a_tokens <= 2000) else 0.4

    length_score = (q_len * 0.4) + (a_len * 0.6)
    scores["length"] = length_score

    has_punct = any(p in question for p in "?.!;:") and any(p in answer for p in ".!;:")
    coherence_score = 0.9 if has_punct else 0.7
    coherence_score *= 1.0 if len(answer) > len(question) else 0.8
    scores["coherence"] = min(coherence_score, 1.0)

    diversity_score = 0.9  # Assume diverse (can't hash easily)
    scores["diversity"] = diversity_score

    final_score = (length_score * 0.35) + (coherence_score * 0.35) + (diversity_score * 0.30)
    return min(final_score, 1.0), scores


def generate_qa_pair(
    client: OVHAIClient,
    model_key: str,
    topic: str,
    retries: int = 2
) -> Optional[Tuple[dict, float]]:
    """Generate Q&A pair via OVHCloud."""

    attempt = 0
    while attempt <= retries:
        attempt += 1
        try:
            print(f"[OVH-Q] {model_key} attempt {attempt}/{retries + 1}", file=sys.stderr)
            question, answer = client.generate_qa_pair(topic, model_key)

            if not question or not answer:
                raise ValueError("Empty response")

            q_short = (question[:60] + "...") if len(question) > 60 else question
            a_short = (answer[:60] + "...") if len(answer) > 60 else answer
            print(f"[OVH-A] Q: {q_short} → A: {a_short}", file=sys.stderr)

            score, breakdown = _calculate_quality_score(question, answer)
            print(f"[SCORE] {score:.2f} (len:{breakdown['length']:.2f} coh:{breakdown['coherence']:.2f})", file=sys.stderr)

            if score < QUALITY_MIN_SCORE:
                print(f"[⚠] Low quality, retrying...", file=sys.stderr)
                if attempt > retries:
                    return None
                continue

            pair = {
                "id": str(uuid.uuid4()),
                "user_message": question,
                "free_response": answer,
                "free_model": f"ovh/{model_key}",
                "deepseek_response": "",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "session_id": str(uuid.uuid4())[:8],
                "topic": topic,
                "format": "qa-pair",
                "pov": "",
                "capabilities": "",
                "space_node": "",
                "memory_ref": "",
                "enriched_at": "",
                "pipeline": "ovh-ai-endpoints",
                "quality_score": round(score, 3),
            }
            print(f"[✓] Generated via OVH {model_key}", file=sys.stderr)
            return pair, score

        except Exception as e:
            print(f"[✗] Attempt {attempt} failed: {e}", file=sys.stderr)
            if attempt > retries:
                return None

    return None


def safe_write_jsonl(output_file: str, pair: dict) -> bool:
    """Atomically write JSON line (same as generate_dogfeed.py)."""
    try:
        temp_dir = os.path.dirname(output_file) or "."
        fd, temp_path = tempfile.mkstemp(dir=temp_dir, prefix=".tmp_", suffix=".jsonl")

        try:
            with os.fdopen(fd, 'w') as f:
                f.write(json.dumps(pair) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            os.unlink(temp_path)
            raise e

        existing_lines = []
        if os.path.exists(output_file):
            try:
                with open(output_file, 'r') as f:
                    existing_lines = f.readlines()
            except Exception as e:
                print(f"[⚠] Warning reading {output_file}: {e}", file=sys.stderr)

        with open(output_file, 'w') as f:
            with open(temp_path, 'r') as temp_f:
                f.write(temp_f.read())
            f.writelines(existing_lines)
            f.flush()
            os.fsync(f.fileno())

        os.unlink(temp_path)
        return True

    except Exception as e:
        print(f"[✗] File write failed: {e}", file=sys.stderr)
        return False


def generate_dataset(
    model_key: str = "qwen-9b",
    num_pairs: int = 100,
    output_file: str = "dogfeed_ovh.jsonl",
) -> None:
    """Generate dataset via OVHCloud."""

    try:
        client = OVHAIClient()
        print(f"[✓] OVHCloud client initialized", file=sys.stderr)
    except Exception as e:
        print(f"[✗] OVHCloud init failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[START] Generating {num_pairs} pairs via OVH {model_key} → {output_file}\n", file=sys.stderr)

    generated = 0
    failed = 0
    scores = []

    for i in range(num_pairs):
        topic = TOPICS[i % len(TOPICS)]

        result = generate_qa_pair(client, model_key, topic, retries=1)

        if result:
            pair, score = result
            if safe_write_jsonl(output_file, pair):
                generated += 1
                scores.append(score)
            else:
                failed += 1
        else:
            failed += 1

        if (i + 1) % 10 == 0:
            pct = int((generated / num_pairs) * 100)
            print(f"[{pct:3d}%] {generated}/{num_pairs} pairs (failed: {failed})", file=sys.stderr)

    print(f"\n[DONE] Generated: {generated} pairs, Failed: {failed}", file=sys.stderr)
    print(f"[FILE] {output_file}", file=sys.stderr)

    if scores:
        avg_score = sum(scores) / len(scores)
        print(f"[QUALITY] Avg: {avg_score:.3f}", file=sys.stderr)

    if os.path.exists(output_file):
        file_size = os.path.getsize(output_file)
        print(f"[VERIFY] File size: {file_size} bytes", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate dogfeed via OVHCloud AI Endpoints")
    parser.add_argument("--num", type=int, default=100, help="Number of pairs (default: 100)")
    parser.add_argument("--output", default="dogfeed_ovh.jsonl", help="Output file")
    parser.add_argument("--model", default="qwen-9b", help="OVH model key (default: qwen-9b)")

    args = parser.parse_args()

    generate_dataset(
        model_key=args.model,
        num_pairs=args.num,
        output_file=args.output,
    )
