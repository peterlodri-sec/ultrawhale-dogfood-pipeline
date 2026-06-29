#!/usr/bin/env python3
"""
Generate Q&A dogfeed data using local mistral.rs (OpenAI-compatible API).
Bulletproof: atomic file writes, explicit timeouts, retry logic.
Output format matches ultrawhale-dogfeed dataset schema.
Phase 1: Quality scoring (coherence, length, diversity filters).
"""

import json
import sys
import uuid
import os
import tempfile
import hashlib
import queue
import threading
from datetime import datetime
from typing import Optional, Tuple

try:
    import openai
except ImportError:
    print("Error: openai not installed. Run: pip install openai", file=sys.stderr)
    sys.exit(1)

HF_AVAILABLE = False
try:
    from hf_inference import HFInferenceClient
    HF_AVAILABLE = True
except ImportError:
    pass

DIFFICULTY_AVAILABLE = False
try:
    from difficulty_sampling import (
        select_difficulty,
        get_prompt_for_difficulty,
        get_question_type_for_difficulty,
        ActiveLearningTracker,
    )
    DIFFICULTY_AVAILABLE = True
except ImportError:
    pass


TOPICS_ALL = [
    "coding fundamentals",
    "algorithms",
    "data structures",
    "system design",
    "software architecture",
    "distributed systems",
    "machine learning",
    "deep learning",
    "computer science theory",
    "complexity theory",
    "cryptography",
    "compiler design",
    "operating systems",
    "databases",
    "networking",
    "SOTA research papers",
    "thesis research",
    "academic theories",
]

TOPICS_CS_THEORY = [
    "algorithms",
    "data structures",
    "computer science theory",
    "complexity theory",
    "cryptography",
    "compiler design",
    "operating systems",
    "automata theory",
    "formal languages",
    "computability theory",
    "SOTA research papers",
    "thesis research in CS",
]

TOPICS_PHYSICS = [
    "quantum mechanics",
    "relativity theory",
    "quantum field theory",
    "statistical mechanics",
    "particle physics",
    "string theory",
    "cosmology",
    "astrophysics",
    "condensed matter physics",
    "quantum computing theory",
    "SOTA physics research",
    "theoretical physics thesis",
]

TOPIC_CATEGORIES = {
    "all": TOPICS_ALL,
    "cs": TOPICS_CS_THEORY,
    "physics": TOPICS_PHYSICS,
    "hybrid": TOPICS_ALL,
}

# More foundational questions
QUESTION_PROMPTS = {
    "conceptual": "Generate a clear, fundamental question about {topic} suitable for CS students learning the basics. Focus on core concepts.",
    "practical": "Generate a practical coding question related to {topic} with clear requirements and expected output.",
    "theoretical": "Generate a theoretical question about {topic} for advanced study or research.",
    "comparison": "Generate a comparison question contrasting two related concepts or approaches in {topic}.",
    "definition": "Generate a question asking to define and explain a fundamental concept in {topic}.",
    "example": "Generate a question asking for a real-world example or use case of {topic}.",
}

QUALITY_THRESHOLDS = {
    "min_question_tokens": 8,
    "max_question_tokens": 200,
    "min_answer_tokens": 20,
    "max_answer_tokens": 2000,
    "min_score": 0.65,
}

seen_hashes = set()


def _truncate(text: str, max_len: int = 60) -> str:
    """Truncate text for logging."""
    return (text[:max_len] + "...") if len(text) > max_len else text


def _token_count(text: str) -> int:
    """Rough token estimate: split on whitespace."""
    return len(text.split())


def _calculate_quality_score(question: str, answer: str, topic: str) -> Tuple[float, dict]:
    """Score Q&A pair on coherence, length, diversity (0-1)."""
    scores = {}

    q_tokens = _token_count(question)
    a_tokens = _token_count(answer)

    q_len = 1.0 if (QUALITY_THRESHOLDS["min_question_tokens"] <= q_tokens <= QUALITY_THRESHOLDS["max_question_tokens"]) else 0.3
    a_len = 1.0 if (QUALITY_THRESHOLDS["min_answer_tokens"] <= a_tokens <= QUALITY_THRESHOLDS["max_answer_tokens"]) else 0.4

    length_score = (q_len * 0.4) + (a_len * 0.6)
    scores["length"] = length_score

    has_punctuation = any(p in question for p in "?.!;:") and any(p in answer for p in ".!;:")
    coherence_score = 0.9 if has_punctuation else 0.7
    coherence_score *= 1.0 if len(answer) > len(question) else 0.8
    scores["coherence"] = min(coherence_score, 1.0)

    q_hash = hashlib.md5((question + answer).encode()).hexdigest()
    is_novel = q_hash not in seen_hashes
    diversity_score = 1.0 if is_novel else 0.1
    scores["diversity"] = diversity_score

    final_score = (length_score * 0.35) + (coherence_score * 0.35) + (diversity_score * 0.30)

    if is_novel:
        seen_hashes.add(q_hash)

    return min(final_score, 1.0), scores


def generate_qa_pair(
    client,
    model: str,
    topic: str,
    question_type: str = "conceptual",
    retries: int = 2
) -> Optional[Tuple[dict, float]]:
    """Generate single Q&A pair with retry logic and quality scoring."""

    prompt_template = QUESTION_PROMPTS.get(question_type, QUESTION_PROMPTS["conceptual"])
    prompt = prompt_template.format(topic=topic)

    attempt = 0
    while attempt <= retries:
        attempt += 1
        try:
            print(f"[Q-gen] attempt {attempt}/{retries + 1}", file=sys.stderr)
            q_response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
            )
            question = q_response.choices[0].message.content.strip()
            if not question:
                raise ValueError("Empty question response")
            q_short = _truncate(question)
            print(f"[Q:{q_short}] - sent", file=sys.stderr)

            a_prompt = f"Answer this question concisely:\n{question}"
            a_response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": a_prompt}],
                stream=False,
            )
            answer = a_response.choices[0].message.content.strip()
            if not answer:
                raise ValueError("Empty answer response")
            a_short = _truncate(answer)
            print(f"[A:Q({q_short}) got! {a_short}]", file=sys.stderr)

            score, score_breakdown = _calculate_quality_score(question, answer, topic)
            score_str = f"{score:.2f}"
            print(f"[SCORE] {score_str} (len:{score_breakdown['length']:.2f} coh:{score_breakdown['coherence']:.2f} div:{score_breakdown['diversity']:.2f})", file=sys.stderr)

            if score < QUALITY_THRESHOLDS["min_score"]:
                print(f"[⚠] Low quality (score {score_str} < {QUALITY_THRESHOLDS['min_score']}), retrying...", file=sys.stderr)
                if attempt > retries:
                    return None
                continue

            pair = {
                "id": str(uuid.uuid4()),
                "user_message": question,
                "free_response": answer,
                "free_model": f"mistralrs/{model}",
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
                "pipeline": "qwen-local-gen-phase1-quality",
                "quality_score": round(score, 3),
            }
            print(f"[✓] saved to output (quality: {score_str})", file=sys.stderr)
            return pair, score

        except Exception as e:
            print(f"[✗] Attempt {attempt} failed: {type(e).__name__}: {e}", file=sys.stderr)
            if attempt > retries:
                print(f"[✗] Max retries exhausted for {topic}", file=sys.stderr)
                return None
            print(f"[→] Retrying...", file=sys.stderr)

    return None


def generate_qa_pair_hybrid(
    client,
    model: str,
    topic: str,
    question_type: str = "conceptual",
    use_hf_fallback: bool = False,
    hf_client: Optional[object] = None,
) -> Optional[Tuple[dict, float]]:
    """Generate Q&A pair: try local mistral.rs first, fallback to HF if low quality."""

    result = generate_qa_pair(client, model, topic, question_type, retries=1)

    if result:
        pair, score = result
        if score >= QUALITY_THRESHOLDS["min_score"]:
            return result
        print(f"[HYBRID] Local score {score:.2f} < threshold, trying HF...", file=sys.stderr)

    if use_hf_fallback and hf_client and HF_AVAILABLE:
        try:
            print(f"[HYBRID] Generating via HF Inference (llama70b)...", file=sys.stderr)
            qa = hf_client.generate_qa_pair(topic, question_type, "llama70b")
            if qa:
                question, answer = qa
                score, breakdown = _calculate_quality_score(question, answer, topic)
                print(f"[HYBRID] HF score: {score:.2f}", file=sys.stderr)

                if score >= QUALITY_THRESHOLDS["min_score"]:
                    pair = {
                        "id": str(uuid.uuid4()),
                        "user_message": question,
                        "free_response": answer,
                        "free_model": "hf-inference/llama-70b",
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
                        "pipeline": "hybrid-phase2-hf-fallback",
                        "quality_score": round(score, 3),
                    }
                    return pair, score
        except Exception as e:
            print(f"[HYBRID] HF fallback failed: {e}", file=sys.stderr)

    return None


def writer_thread(output_file: str, q: queue.Queue):
    """Dedicated thread to write results to disk."""
    with open(output_file, 'a') as f:
        while True:
            pair = q.get()
            if pair is None:  # Shutdown signal
                break
            f.write(json.dumps(pair) + "\n")
            f.flush()
            q.task_done()


def generate_qa_pair_hf_only(
    hf_client,
    topic: str,
    question_type: str = "conceptual",
    model_key: str = "llama70b",
) -> Optional[Tuple[dict, float]]:
    """Generate Q&A pair via HF Inference only (no Ollama)."""
    try:
        qa = hf_client.generate_qa_pair(topic, question_type, model_key)
        if not qa:
            return None
        question, answer = qa
        score, breakdown = _calculate_quality_score(question, answer, topic)
        print(f"[HF-ONLY] score: {score:.2f} (len:{breakdown['length']:.2f} coh:{breakdown['coherence']:.2f})", file=sys.stderr)
        if score < QUALITY_THRESHOLDS["min_score"]:
            return None
        pair = {
            "id": str(uuid.uuid4()),
            "user_message": question,
            "free_response": answer,
            "free_model": f"hf-inference/{model_key}",
            "deepseek_response": "",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "session_id": str(uuid.uuid4())[:8],
            "topic": topic,
            "format": "qa-pair",
            "pov": "", "capabilities": "", "space_node": "",
            "memory_ref": "", "enriched_at": "",
            "pipeline": "hf-only-phase2",
            "quality_score": round(score, 3),
        }
        return pair, score
    except Exception as e:
        print(f"[HF-ONLY] failed: {e}", file=sys.stderr)
        return None


def generate_dataset(
    model: str = "qwen3.6-27b",
    num_pairs: int = 100,
    output_file: str = "dogfeed.jsonl",
    mistralrs_host: str = "http://localhost:8080",
    topic_category: str = "all",
    hybrid_mode: bool = False,
    difficulty_sampling: bool = False,
    hf_only: bool = False,
) -> None:
    """Generate dataset and write atomically to JSONL."""

    hf_client = None
    al_tracker = None

    if hf_only or hybrid_mode:
        if HF_AVAILABLE:
            try:
                hf_client = HFInferenceClient()
                print(f"[INFO] HF Inference API available {'(hf-only mode)' if hf_only else '(fallback mode)'}", file=sys.stderr)
            except Exception as e:
                print(f"[⚠] HF Inference not available: {e}", file=sys.stderr)
                if hf_only:
                    sys.exit(1)
                hybrid_mode = False
        elif hf_only:
            print(f"[✗] --hf-only requires huggingface_hub. Run: pip install huggingface_hub", file=sys.stderr)
            sys.exit(1)

    client = None
    if not hf_only:
        client = openai.OpenAI(base_url=f"{mistralrs_host}/v1", api_key="none")

    if difficulty_sampling and DIFFICULTY_AVAILABLE:
        try:
            al_tracker = ActiveLearningTracker()
            print(f"[INFO] Active learning enabled (difficulty sampling)", file=sys.stderr)
        except Exception as e:
            print(f"[⚠] Difficulty sampling not available: {e}", file=sys.stderr)
            difficulty_sampling = False

    if not hf_only:
        # Verify mistral.rs server is reachable
        try:
            client.models.list()
            print(f"\n[✓] mistral.rs server ready (model: {model})", file=sys.stderr)
        except Exception as e:
            print(f"[✗] mistral.rs not reachable at {mistralrs_host}: {e}", file=sys.stderr)
            sys.exit(1)

    # Select topics by category
    if topic_category not in TOPIC_CATEGORIES:
        print(f"[✗] Unknown category '{topic_category}'. Choose: {', '.join(TOPIC_CATEGORIES.keys())}", file=sys.stderr)
        sys.exit(1)
    topics = TOPIC_CATEGORIES[topic_category]
    print(f"[INFO] Using {len(topics)} {topic_category} topics", file=sys.stderr)

    print(f"[START] Generating {num_pairs} Q&A pairs → {output_file}\n", file=sys.stderr)

    q = queue.Queue()
    t = threading.Thread(target=writer_thread, args=(output_file, q), daemon=True)
    t.start()

    generated = 0
    failed = 0
    scores = []

    for i in range(num_pairs):
        topic = topics[i % len(topics)]

        if difficulty_sampling and DIFFICULTY_AVAILABLE:
            difficulty = select_difficulty(seed=i)
            q_type = get_question_type_for_difficulty(difficulty, seed=i)
            print(f"[DIFFICULTY] {difficulty} - {q_type}", file=sys.stderr)
        else:
            q_type = list(QUESTION_PROMPTS.keys())[i % len(QUESTION_PROMPTS)]
            difficulty = None

        if hf_only:
            result = generate_qa_pair_hf_only(hf_client, topic, q_type)
        elif hybrid_mode:
            result = generate_qa_pair_hybrid(
                client,
                model,
                topic,
                q_type,
                use_hf_fallback=True,
                hf_client=hf_client,
            )
        else:
            result = generate_qa_pair(
                client,
                model,
                topic,
                q_type,
                retries=2
            )

        if result:
            pair, score = result
            q.put(pair)
            generated += 1
            scores.append(score)
            if al_tracker and difficulty:
                al_tracker.log_generation(topic, difficulty, True, score)
        else:
            failed += 1
            if al_tracker and difficulty:
                al_tracker.log_generation(topic, difficulty, False, 0.0)

        # Progress bar every 10 pairs
        if (i + 1) % 10 == 0:
            pct = int((generated / num_pairs) * 100)
            print(f"[{pct:3d}%] {generated}/{num_pairs} pairs (failed: {failed})", file=sys.stderr)

    q.put(None)
    t.join()

    print(f"\n[DONE] Generated: {generated} pairs, Failed: {failed}", file=sys.stderr)
    print(f"[FILE] {output_file}", file=sys.stderr)

    if scores:
        avg_score = sum(scores) / len(scores)
        min_score = min(scores)
        max_score = max(scores)
        print(f"[QUALITY] Avg: {avg_score:.3f}, Min: {min_score:.3f}, Max: {max_score:.3f}", file=sys.stderr)

    if al_tracker:
        print(f"\n{al_tracker.report()}", file=sys.stderr)
        suggestion = al_tracker.suggest_difficulty_adjustment()
        print(f"[AL-SUGGESTION] {suggestion}", file=sys.stderr)

    if os.path.exists(output_file):
        file_size = os.path.getsize(output_file)
        print(f"[VERIFY] File size: {file_size} bytes", file=sys.stderr)
    else:
        print(f"[ERROR] Output file not created!", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Q&A dogfeed data via mistral.rs (OpenAI-compatible)")
    parser.add_argument("--model", default="qwen3.6-27b", help="Model name (must match what mistral.rs serves)")
    parser.add_argument("--num", type=int, default=100, help="Number of Q&A pairs (default: 100)")
    parser.add_argument("--output", default="dogfeed.jsonl", help="Output JSONL file")
    parser.add_argument("--host", default="http://localhost:8080", help="mistral.rs server URL")
    parser.add_argument(
        "--category",
        default="all",
        choices=list(TOPIC_CATEGORIES.keys()),
        help=f"Topic category: {', '.join(TOPIC_CATEGORIES.keys())}"
    )
    parser.add_argument(
        "--hybrid",
        action="store_true",
        help="Use HF Inference API as fallback (requires HF_TOKEN)"
    )
    parser.add_argument(
        "--difficulty",
        action="store_true",
        help="Enable difficulty-aware sampling (easy/medium/hard distribution)"
    )
    parser.add_argument(
        "--hf-only",
        action="store_true",
        dest="hf_only",
        help="Skip mistral.rs entirely, use HF Inference API only (requires HF_TOKEN)"
    )

    args = parser.parse_args()

    generate_dataset(
        model=args.model,
        num_pairs=args.num,
        output_file=args.output,
        mistralrs_host=args.host,
        topic_category=args.category,
        hybrid_mode=args.hybrid,
        difficulty_sampling=args.difficulty,
        hf_only=args.hf_only,
    )
