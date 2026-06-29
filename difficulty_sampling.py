#!/usr/bin/env python3
"""Phase 4: Difficulty-aware question sampling and active learning."""

from typing import Literal
import random

DIFFICULTY_PROMPTS = {
    "easy": {
        "conceptual": "Generate a simple, foundational question about {topic} for beginners. Focus on basic definitions and concepts. Keep it accessible.",
        "practical": "Generate a simple coding question about {topic} with clear, basic requirements. Suitable for learners.",
        "definition": "Generate a question asking to define a fundamental concept in {topic} in simple terms.",
    },
    "medium": {
        "conceptual": "Generate a moderate-difficulty question about {topic} that explores concepts and relationships. Suitable for intermediate learners.",
        "practical": "Generate a practical coding problem about {topic} with moderate complexity and clear requirements.",
        "comparison": "Generate a comparison question contrasting two related concepts in {topic}.",
    },
    "hard": {
        "theoretical": "Generate a challenging theoretical question about {topic} for advanced study or research.",
        "research": "Generate a research-level question about {topic} that explores cutting-edge topics or edge cases.",
        "synthesis": "Generate a synthesis question asking to combine knowledge from multiple areas of {topic}.",
    },
}

DIFFICULTY_DISTRIBUTION = {
    "easy": 0.40,
    "medium": 0.40,
    "hard": 0.20,
}


def select_difficulty(seed: int = None) -> Literal["easy", "medium", "hard"]:
    """Sample difficulty level based on distribution."""
    if seed is not None:
        random.seed(seed)

    rand = random.random()
    cumsum = 0
    for difficulty, prob in DIFFICULTY_DISTRIBUTION.items():
        cumsum += prob
        if rand <= cumsum:
            return difficulty
    return "hard"


def get_question_type_for_difficulty(difficulty: str, seed: int = None) -> str:
    """Select question type appropriate for difficulty level."""
    if seed is not None:
        random.seed(seed)

    types = list(DIFFICULTY_PROMPTS.get(difficulty, {}).keys())
    return random.choice(types) if types else "conceptual"


def get_prompt_for_difficulty(topic: str, difficulty: str, question_type: str = None) -> str:
    """Get prompt template for difficulty level."""
    if question_type is None:
        question_type = get_question_type_for_difficulty(difficulty)

    prompts = DIFFICULTY_PROMPTS.get(difficulty, {})
    prompt = prompts.get(question_type, prompts.get("conceptual", ""))
    return prompt.format(topic=topic)


class ActiveLearningTracker:
    """Track generation success/feedback by difficulty level."""

    def __init__(self, storage_file: str = ".ralph_active_learning.jsonl"):
        self.storage_file = storage_file
        self.stats = {"easy": {}, "medium": {}, "hard": {}}

    def log_generation(self, topic: str, difficulty: str, success: bool, score: float):
        """Log generation outcome."""
        if topic not in self.stats[difficulty]:
            self.stats[difficulty][topic] = {"success": 0, "total": 0, "avg_score": 0.0}

        stat = self.stats[difficulty][topic]
        stat["total"] += 1
        if success:
            stat["success"] += 1
        stat["avg_score"] = (stat["avg_score"] * (stat["total"] - 1) + score) / stat["total"]

    def get_success_rate(self, topic: str = None, difficulty: str = None) -> float:
        """Get success rate for topic/difficulty."""
        if topic and difficulty:
            stat = self.stats[difficulty].get(topic, {})
            total = stat.get("total", 0)
            return stat.get("success", 0) / total if total > 0 else 0.0
        return 0.0

    def suggest_difficulty_adjustment(self) -> dict:
        """Suggest distribution adjustments based on success rates."""
        avg_success = {}
        for difficulty in ["easy", "medium", "hard"]:
            rates = [s.get("success", 0) / s.get("total", 1) for s in self.stats[difficulty].values()]
            avg_success[difficulty] = sum(rates) / len(rates) if rates else 0.5

        # Adjust distribution: increase difficulty if easy has >80% success
        new_dist = dict(DIFFICULTY_DISTRIBUTION)
        if avg_success["easy"] > 0.80:
            new_dist["easy"] -= 0.05
            new_dist["medium"] += 0.05
        elif avg_success["easy"] < 0.50:
            new_dist["easy"] += 0.05
            new_dist["medium"] -= 0.05

        if avg_success["medium"] > 0.80:
            new_dist["medium"] -= 0.05
            new_dist["hard"] += 0.05
        elif avg_success["medium"] < 0.40:
            new_dist["medium"] += 0.05
            new_dist["hard"] -= 0.05

        return {
            "current": DIFFICULTY_DISTRIBUTION,
            "suggested": new_dist,
            "success_rates": avg_success,
        }

    def report(self) -> str:
        """Generate summary report."""
        lines = []
        lines.append("=== Active Learning Report ===")
        for difficulty in ["easy", "medium", "hard"]:
            lines.append(f"\n{difficulty.upper()}:")
            for topic, stat in self.stats[difficulty].items():
                rate = stat["success"] / stat["total"] if stat["total"] > 0 else 0
                lines.append(
                    f"  {topic}: {stat['success']}/{stat['total']} success ({rate:.1%}), "
                    f"avg_score={stat['avg_score']:.3f}"
                )
        return "\n".join(lines)


if __name__ == "__main__":
    # Test difficulty sampling
    print("Difficulty sampling test:")
    samples = [select_difficulty(seed=i) for i in range(100)]
    counts = {d: samples.count(d) for d in ["easy", "medium", "hard"]}
    for d, c in counts.items():
        print(f"  {d}: {c}% (target: {int(DIFFICULTY_DISTRIBUTION[d] * 100)}%)")

    print("\nQuestion types by difficulty:")
    for difficulty in ["easy", "medium", "hard"]:
        qtype = get_question_type_for_difficulty(difficulty, seed=42)
        prompt = get_prompt_for_difficulty("algorithms", difficulty, qtype)
        print(f"  {difficulty} → {qtype}")
        print(f"    {prompt[:60]}...")
