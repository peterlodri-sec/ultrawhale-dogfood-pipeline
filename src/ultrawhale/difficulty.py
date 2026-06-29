# SPDX-License-Identifier: MIT
"""Difficulty-aware question sampling and active learning.

Migrated from self-host-llm/difficulty_sampling.py into the ultrawhale
package structure with hardening applied:
  - Instance-level RNG (no more ``random.seed(seed)`` side effects).
  - ``Literal`` removed; plain ``str`` annotations (Python 3.12+).
  - All public API preserved: ``select_difficulty``,
    ``get_question_type_for_difficulty``,
    ``get_prompt_for_difficulty``, ``ActiveLearningTracker``.
"""

from __future__ import annotations

import random

from ultrawhale.logging import get_logger

logger = get_logger("difficulty")

# ---------------------------------------------------------------------------
# Prompt template tables
# ---------------------------------------------------------------------------

DIFFICULTY_PROMPTS: dict[str, dict[str, str]] = {
    "easy": {
        "conceptual": (
            "Generate a simple, foundational question about {topic} for beginners. "
            "Focus on basic definitions and concepts. Keep it accessible."
        ),
        "practical": (
            "Generate a simple coding question about {topic} with clear, basic requirements. Suitable for learners."
        ),
        "definition": "Generate a question asking to define a fundamental concept in {topic} in simple terms.",
    },
    "medium": {
        "conceptual": (
            "Generate a moderate-difficulty question about {topic} that explores "
            "concepts and relationships. Suitable for intermediate learners."
        ),
        "practical": (
            "Generate a practical coding problem about {topic} with moderate complexity and clear requirements."
        ),
        "comparison": "Generate a comparison question contrasting two related concepts in {topic}.",
    },
    "hard": {
        "theoretical": "Generate a challenging theoretical question about {topic} for advanced study or research.",
        "research": "Generate a research-level question about {topic} that explores cutting-edge topics or edge cases.",
        "synthesis": "Generate a synthesis question asking to combine knowledge from multiple areas of {topic}.",
    },
}

DIFFICULTY_DISTRIBUTION: dict[str, float] = {
    "easy": 0.40,
    "medium": 0.40,
    "hard": 0.20,
}

# ---------------------------------------------------------------------------
# Module-level convenience functions  (backed by global RNG for simplicity)
# ---------------------------------------------------------------------------

_rng = random.Random()


def select_difficulty(seed: int | None = None) -> str:
    """Sample difficulty level based on distribution.

    Parameters
    ----------
    seed:
        Optional seed for reproducible sampling.  Unlike the original
        implementation this does **not** call ``random.seed()`` globally;
        it seeds an internal ``Random`` instance instead.

    Returns
    -------
    One of ``"easy"``, ``"medium"``, ``"hard"``.
    """
    rng = random.Random(seed) if seed is not None else _rng
    rand = rng.random()
    cumsum = 0.0
    for difficulty, prob in DIFFICULTY_DISTRIBUTION.items():
        cumsum += prob
        if rand <= cumsum:
            return difficulty
    return "hard"


def get_question_type_for_difficulty(difficulty: str, seed: int | None = None) -> str:
    """Select a question type appropriate for *difficulty*.

    Parameters
    ----------
    difficulty:
        One of ``"easy"``, ``"medium"``, ``"hard"``.
    seed:
        Optional seed (see ``select_difficulty``).

    Returns
    -------
    A question-type key (e.g. ``"conceptual"``, ``"practical"``, etc.).
    """
    rng = random.Random(seed) if seed is not None else _rng
    types = list(DIFFICULTY_PROMPTS.get(difficulty, {}).keys())
    return rng.choice(types) if types else "conceptual"


def get_prompt_for_difficulty(
    topic: str,
    difficulty: str,
    question_type: str | None = None,
) -> str:
    """Return the prompt template for *difficulty* / *question_type*.

    Parameters
    ----------
    topic:
        Subject matter inserted into the template.
    difficulty:
        One of ``"easy"``, ``"medium"``, ``"hard"``.
    question_type:
        Optional over-ride; inferred via
        ``get_question_type_for_difficulty`` when ``None``.

    Returns
    -------
    Formatted prompt string.
    """
    if question_type is None:
        question_type = get_question_type_for_difficulty(difficulty)

    prompts = DIFFICULTY_PROMPTS.get(difficulty, {})
    prompt = prompts.get(question_type) or prompts.get("conceptual", "")
    return prompt.format(topic=topic)


# ---------------------------------------------------------------------------
# Active-learning tracker
# ---------------------------------------------------------------------------


class ActiveLearningTracker:
    """Track generation success/feedback by difficulty level.

    Parameters
    ----------
    storage_file:
        Path to a JSONL file for persistence (reserved for future use).
    """

    def __init__(self, storage_file: str = ".ralph_active_learning.jsonl") -> None:
        self.storage_file = storage_file
        self.stats: dict[str, dict[str, dict[str, float | int]]] = {
            "easy": {},
            "medium": {},
            "hard": {},
        }
        logger.info("ActiveLearningTracker initialised (storage=%s)", storage_file)

    # -- logging ----------------------------------------------------------

    def log_generation(
        self,
        topic: str,
        difficulty: str,
        success: bool,
        score: float,
    ) -> None:
        """Record one generation outcome.

        Parameters
        ----------
        topic:
            The topic string.
        difficulty:
            Difficulty level.
        success:
            Whether the generation was successful.
        score:
            Quality / relevance score (0.0 – 1.0).
        """
        if difficulty not in self.stats:
            logger.warning("Unknown difficulty '%s', skipping log", difficulty)
            return

        stat = self.stats[difficulty].setdefault(
            topic,
            {"success": 0, "total": 0, "avg_score": 0.0},
        )
        stat["total"] += 1  # type: ignore[operator]
        if success:
            stat["success"] += 1  # type: ignore[operator]

        prev_total = stat["total"] - 1  # type: ignore[operator]
        avg = stat["avg_score"]
        stat["avg_score"] = (avg * prev_total + score) / stat["total"]  # type: ignore[index,operator]

        logger.debug(
            "Logged generation: difficulty=%s topic=%s success=%s score=%.3f",
            difficulty,
            topic,
            success,
            score,
        )

    # -- query ------------------------------------------------------------

    def get_success_rate(self, topic: str | None = None, difficulty: str | None = None) -> float:
        """Return the observed success rate for *topic* at *difficulty*.

        When both arguments are ``None`` (default) the overall rate is
        returned.  When only *difficulty* is given the rate is averaged
        across all topics at that difficulty.
        """
        if topic and difficulty:
            stat = self.stats.get(difficulty, {}).get(topic, {})
            total = stat.get("total", 0)
            return stat.get("success", 0) / total if total else 0.0

        if difficulty:
            entries = list(self.stats.get(difficulty, {}).values())
        else:
            entries = [v for d in self.stats.values() for v in d.values()]

        successes = sum(e.get("success", 0) for e in entries)
        totals = sum(e.get("total", 0) for e in entries)
        return successes / totals if totals else 0.0

    # -- distribution adjustment -----------------------------------------

    def suggest_difficulty_adjustment(self) -> dict:
        """Suggest distribution adjustments based on observed success rates.

        Returns
        -------
        A dict with keys ``"current"``, ``"suggested"``, and
        ``"success_rates"``.
        """
        avg_success: dict[str, float] = {}
        for difficulty in ("easy", "medium", "hard"):
            rates = [s.get("success", 0) / s.get("total", 1) for s in self.stats[difficulty].values()]
            avg_success[difficulty] = sum(rates) / len(rates) if rates else 0.5

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

    # -- report -----------------------------------------------------------

    def report(self) -> str:
        """Generate a human-readable summary report."""
        lines = ["=== Active Learning Report ==="]
        for difficulty in ("easy", "medium", "hard"):
            lines.append(f"\n{difficulty.upper()}:")
            for topic, stat in self.stats[difficulty].items():
                total: int = stat["total"]  # type: ignore[assignment]
                success: int = stat["success"]  # type: ignore[assignment]
                rate = success / total if total else 0.0
                lines.append(f"  {topic}: {success}/{total} success ({rate:.1%}), avg_score={stat['avg_score']:.3f}")
        return "\n".join(lines)


# =========================================================================
# CLI smoke test  (safe for import — only runs when invoked directly)
# =========================================================================

if __name__ == "__main__":
    import sys

    logger.info("Running difficulty module smoke test")

    # --- difficulty sampling ---
    print("Difficulty sampling test:")
    samples = [select_difficulty(seed=i) for i in range(100)]
    counts = {d: samples.count(d) for d in ("easy", "medium", "hard")}
    for d, c in counts.items():
        target_pct = int(DIFFICULTY_DISTRIBUTION[d] * 100)
        print(f"  {d}: {c}% (target: {target_pct}%)")

    # --- question types ---
    print("\nQuestion types by difficulty:")
    for difficulty in ("easy", "medium", "hard"):
        qtype = get_question_type_for_difficulty(difficulty, seed=42)
        prompt = get_prompt_for_difficulty("algorithms", difficulty, qtype)
        print(f"  {difficulty} → {qtype}")
        print(f"    {prompt[:60]}...")

    # --- active learning tracker ---
    print("\nActive learning tracker smoke test:")
    tracker = ActiveLearningTracker()
    tracker.log_generation("sorting", "easy", success=True, score=0.9)
    tracker.log_generation("sorting", "easy", success=False, score=0.3)
    tracker.log_generation("searching", "medium", success=True, score=0.85)
    print(tracker.report())

    sys.exit(0)
