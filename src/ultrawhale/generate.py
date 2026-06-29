# SPDX-License-Identifier: MIT
"""Generate Q&A dogfeed data — the core generation engine.

Supports local LLM (OpenAI-compatible API), HF Inference fallback,
difficulty-aware sampling, and curated quality gating.
"""

import json
import os
import queue
import random
import sys
import threading
import time
import uuid
from datetime import UTC, datetime

from ultrawhale.config import Config
from ultrawhale.logging import get_logger
from ultrawhale.scoring import (
    QUALITY_THRESHOLDS,
    calculate_quality_score,
)

logger = get_logger("generate")

# --- Optional dependency detection ---
try:
    import openai
except ImportError:
    logger.error("openai not installed. Run: pip install openai")
    sys.exit(1)

HF_AVAILABLE = False
try:
    from ultrawhale.hf import HFInferenceClient

    HF_AVAILABLE = True
except ImportError:
    logger.debug("HF inference not available — hybrid/HF-only modes disabled")

DIFFICULTY_AVAILABLE = False
try:
    from ultrawhale.difficulty import (
        ActiveLearningTracker,
        get_question_type_for_difficulty,
        select_difficulty,
    )

    DIFFICULTY_AVAILABLE = True
except ImportError:
    logger.debug("Difficulty sampling not available — use --difficulty to enable")

# --- Topic definitions ---
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

QUESTION_PROMPTS = {
    "conceptual": (
        "Generate a clear, fundamental question about {topic} suitable for CS "
        "students learning the basics. Focus on core concepts."
    ),
    "practical": "Generate a practical coding question related to {topic} with clear requirements and expected output.",
    "theoretical": "Generate a theoretical question about {topic} for advanced study or research.",
    "comparison": "Generate a comparison question contrasting two related concepts or approaches in {topic}.",
    "definition": "Generate a question asking to define and explain a fundamental concept in {topic}.",
    "example": "Generate a question asking for a real-world example or use case of {topic}.",
}


def _truncate(text: str, max_len: int = 60) -> str:
    """Truncate text for logging."""
    return (text[:max_len] + "...") if len(text) > max_len else text


def _backoff_delay(attempt: int, base: float = 1.0, max_delay: float = 16.0) -> float:
    """Exponential backoff with jitter."""
    delay: float = min(base * (2**attempt), max_delay)
    return delay * (0.5 + random.random())


def generate_qa_pair(
    client,
    model: str,
    topic: str,
    question_type: str = "conceptual",
    retries: int = 2,
) -> tuple[dict, float] | None:
    """Generate single Q&A pair with retry logic and quality scoring.

    Args:
        client: OpenAI-compatible client.
        model: Model name.
        topic: Topic to generate about.
        question_type: Type of question (conceptual, practical, etc.).
        retries: Maximum retry attempts.

    Returns:
        Tuple of (pair_dict, score) or None on failure.
    """
    prompt_template = QUESTION_PROMPTS.get(question_type, QUESTION_PROMPTS["conceptual"])
    prompt = prompt_template.format(topic=topic)

    for attempt in range(retries + 1):
        try:
            logger.info(f"Q-gen attempt {attempt + 1}/{retries + 1}")
            q_response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
            )
            question = q_response.choices[0].message.content.strip()
            if not question:
                raise ValueError("Empty question response")
            logger.debug(f"Q: {_truncate(question)}")

            a_prompt = f"Answer this question concisely:\n{question}"
            a_response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": a_prompt}],
                stream=False,
            )
            answer = a_response.choices[0].message.content.strip()
            if not answer:
                raise ValueError("Empty answer response")
            logger.debug(f"A: {_truncate(answer)}")

            score, score_breakdown = calculate_quality_score(question, answer, topic)
            logger.info(
                f"Score: {score:.2f} "
                f"(len:{score_breakdown['length']:.2f} "
                f"coh:{score_breakdown['coherence']:.2f} "
                f"div:{score_breakdown['diversity']:.2f})"
            )

            if score < QUALITY_THRESHOLDS["min_score"]:
                logger.warning(f"Low quality (score {score:.2f} < {QUALITY_THRESHOLDS['min_score']}), retrying...")
                if attempt < retries:
                    time.sleep(_backoff_delay(attempt))
                continue

            pair = {
                "id": str(uuid.uuid4()),
                "user_message": question,
                "free_response": answer,
                "free_model": f"llama.cpp/{model}",
                "deepseek_response": "",
                "timestamp": datetime.now(UTC).isoformat(),
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
            logger.info(f"Saved pair (quality: {score:.2f})")
            return pair, score

        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {type(e).__name__}: {e}")
            if attempt < retries:
                time.sleep(_backoff_delay(attempt))
                continue
            logger.error(f"Max retries exhausted for {topic}")
            return None

    return None


def generate_qa_pair_hybrid(
    client,
    model: str,
    topic: str,
    question_type: str = "conceptual",
    use_hf_fallback: bool = False,
    hf_client=None,
) -> tuple[dict, float] | None:
    """Generate Q&A pair with HF Inference fallback on low quality.

    Tries local LLM first; if score is below threshold, falls back to HF.
    """
    result = generate_qa_pair(client, model, topic, question_type, retries=1)

    if result:
        pair, score = result
        if score >= QUALITY_THRESHOLDS["min_score"]:
            return result
        logger.info(f"Local score {score:.2f} < threshold, trying HF fallback...")

    if use_hf_fallback and hf_client and HF_AVAILABLE:
        try:
            logger.info("Generating via HF Inference (llama70b)...")
            qa = hf_client.generate_qa_pair(topic, question_type, "llama8b")
            if qa:
                question, answer = qa
                score, _breakdown = calculate_quality_score(question, answer, topic)
                logger.info(f"HF score: {score:.2f}")

                if score >= QUALITY_THRESHOLDS["min_score"]:
                    pair = {
                        "id": str(uuid.uuid4()),
                        "user_message": question,
                        "free_response": answer,
                        "free_model": "hf-inference/llama-70b",
                        "deepseek_response": "",
                        "timestamp": datetime.now(UTC).isoformat(),
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
            logger.error(f"HF fallback failed: {e}")

    return None


def generate_qa_pair_hf_only(
    hf_client,
    topic: str,
    question_type: str = "conceptual",
    model_key: str = "llama8b",
) -> tuple[dict, float] | None:
    """Generate Q&A pair via HF Inference only (no local LLM)."""
    try:
        qa = hf_client.generate_qa_pair(topic, question_type, model_key)
        if not qa:
            return None
        question, answer = qa
        score, breakdown = calculate_quality_score(question, answer, topic)
        logger.info(f"HF-only score: {score:.2f} (len:{breakdown['length']:.2f} coh:{breakdown['coherence']:.2f})")
        if score < QUALITY_THRESHOLDS["min_score"]:
            return None
        pair = {
            "id": str(uuid.uuid4()),
            "user_message": question,
            "free_response": answer,
            "free_model": f"hf-inference/{model_key}",
            "deepseek_response": "",
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": str(uuid.uuid4())[:8],
            "topic": topic,
            "format": "qa-pair",
            "pov": "",
            "capabilities": "",
            "space_node": "",
            "memory_ref": "",
            "enriched_at": "",
            "pipeline": "hf-only-phase2",
            "quality_score": round(score, 3),
        }
        return pair, score
    except Exception as e:
        logger.error(f"HF-only failed: {e}")
        return None


def writer_thread(output_file: str, q: queue.Queue) -> None:
    """Dedicated thread to write results to disk."""
    with open(output_file, "a") as f:
        while True:
            pair = q.get()
            if pair is None:  # Shutdown signal
                break
            f.write(json.dumps(pair) + "\n")
            f.flush()
            q.task_done()


def generate_dataset(
    model: str = "qwen3.6-27b",
    num_pairs: int = 100,
    output_file: str = "dogfeed.jsonl",
    llm_host: str = "http://localhost:8080",
    topic_category: str = "all",
    hybrid_mode: bool = False,
    difficulty_sampling: bool = False,
    hf_only: bool = False,
    skip_curation: bool = False,
) -> None:
    """Generate dataset and write atomically to JSONL.

    Args:
        model: Model name served by the LLM server.
        num_pairs: Number of Q&A pairs to generate.
        output_file: Output JSONL file path.
        llm_host: LLM server URL.
        topic_category: Topic category (all, cs, physics, hybrid).
        hybrid_mode: Enable HF fallback for low-quality local generations.
        difficulty_sampling: Enable difficulty-aware sampling.
        hf_only: Use HF Inference API only (skip local LLM).
        skip_curation: Skip LLM-judge curation (faster, lower quality filter).
    """
    cfg = Config()

    # --- HF client setup ---
    hf_client = None
    if hf_only or hybrid_mode:
        if HF_AVAILABLE:
            try:
                hf_client = HFInferenceClient(api_token=cfg.hf_token)
                mode = "hf-only" if hf_only else "fallback"
                logger.info(f"HF Inference API available ({mode} mode)")
            except Exception as e:
                logger.warning(f"HF Inference not available: {e}")
                if hf_only:
                    sys.exit(1)
                hybrid_mode = False
        elif hf_only:
            logger.error("--hf-only requires huggingface_hub. Run: pip install huggingface_hub")
            sys.exit(1)

    # --- Local LLM client ---
    client = None
    if not hf_only:
        client = openai.OpenAI(base_url=f"{llm_host}/v1", api_key="none")

    # --- Difficulty tracking ---
    al_tracker = None
    if difficulty_sampling and DIFFICULTY_AVAILABLE:
        try:
            al_tracker = ActiveLearningTracker()
            logger.info("Active learning enabled (difficulty sampling)")
        except Exception as e:
            logger.warning(f"Difficulty sampling not available: {e}")
            difficulty_sampling = False

    # --- Verify server ---
    if not hf_only and client is not None:
        try:
            client.models.list()
            logger.info(f"LLM server ready (model: {model})")
        except Exception as e:
            logger.error(f"LLM server not reachable at {llm_host}: {e}")
            sys.exit(1)

    # --- Select topics ---
    if topic_category not in TOPIC_CATEGORIES:
        logger.error(f"Unknown category '{topic_category}'. Choose: {', '.join(TOPIC_CATEGORIES.keys())}")
        sys.exit(1)
    topics = TOPIC_CATEGORIES[topic_category]
    logger.info(f"Using {len(topics)} {topic_category} topics")

    logger.info(f"Generating {num_pairs} Q&A pairs → {output_file}")

    # --- Writer thread ---
    q: queue.Queue = queue.Queue()
    t = threading.Thread(target=writer_thread, args=(output_file, q), daemon=True)
    t.start()

    generated = 0
    failed = 0
    scores: list[float] = []

    # --- Curation engine (optional) ---
    curator = None
    if not skip_curation and cfg.hf_token:
        try:
            from ultrawhale.curation import CurationEngine

            curator = CurationEngine(cfg.hf_token)
            logger.info("Curation engine enabled (LLM-judge scoring)")
        except Exception as e:
            logger.warning(f"Curation not available: {e}")

    for i in range(num_pairs):
        topic = topics[i % len(topics)]

        if difficulty_sampling and DIFFICULTY_AVAILABLE:
            difficulty = select_difficulty(seed=i)
            q_type = get_question_type_for_difficulty(difficulty, seed=i)
            logger.debug(f"Difficulty: {difficulty} - {q_type}")
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
            result = generate_qa_pair(client, model, topic, q_type, retries=2)

        if result:
            pair, score = result
            if curator:
                curated_pair = curator.curate(pair)
                if curated_pair:
                    q.put(curated_pair)
                    generated += 1
                    scores.append(curated_pair.get("curated_score", 0))
                else:
                    failed += 1
            else:
                q.put(pair)
                generated += 1
                scores.append(score)

            if al_tracker and difficulty:
                al_tracker.log_generation(topic, difficulty, True, score)
        else:
            failed += 1
            if al_tracker and difficulty:
                al_tracker.log_generation(topic, difficulty, False, 0.0)

        # Progress every 10 pairs
        if (i + 1) % 10 == 0:
            pct = int((generated / num_pairs) * 100) if generated else 0
            logger.info(f"[{pct:3d}%] {generated}/{num_pairs} pairs (failed: {failed})")

    q.put(None)
    t.join()

    logger.info(f"Done — Generated: {generated} pairs, Failed: {failed}")
    logger.info(f"Output: {output_file}")

    if scores:
        avg_score = sum(scores) / len(scores)
        logger.info(f"Quality — Avg: {avg_score:.3f}, Min: {min(scores):.3f}, Max: {max(scores):.3f}")

    if al_tracker:
        logger.info(f"\n{al_tracker.report()}")
        suggestion = al_tracker.suggest_difficulty_adjustment()
        logger.info(f"AL suggestion: {suggestion}")

    if os.path.exists(output_file):
        file_size = os.path.getsize(output_file)
        logger.info(f"File size: {file_size} bytes")
    else:
        logger.error("Output file not created!")


# --- CLI entry point ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Q&A dogfeed data")
    parser.add_argument("--model", default="qwen3.6-27b", help="Model name")
    parser.add_argument("--num", type=int, default=100, help="Number of Q&A pairs")
    parser.add_argument("--output", default="dogfeed.jsonl", help="Output JSONL file")
    parser.add_argument("--host", default="http://localhost:8080", help="LLM server URL")
    parser.add_argument(
        "--category",
        default="all",
        choices=list(TOPIC_CATEGORIES.keys()),
        help=f"Topic category: {', '.join(TOPIC_CATEGORIES.keys())}",
    )
    parser.add_argument("--hybrid", action="store_true", help="Use HF Inference as fallback")
    parser.add_argument("--difficulty", action="store_true", help="Enable difficulty-aware sampling")
    parser.add_argument("--hf-only", action="store_true", dest="hf_only", help="Skip local LLM, use HF Inference only")
    parser.add_argument("--skip-curation", action="store_true", help="Skip LLM-judge curation")

    args = parser.parse_args()

    generate_dataset(
        model=args.model,
        num_pairs=args.num,
        output_file=args.output,
        llm_host=args.host,
        topic_category=args.category,
        hybrid_mode=args.hybrid,
        difficulty_sampling=args.difficulty,
        hf_only=args.hf_only,
        skip_curation=args.skip_curation,
    )
