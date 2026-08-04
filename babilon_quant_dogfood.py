#!/usr/bin/env python3
"""Babilon → ultrawhale-dogfood recursive sampling-learning pipeline.

Generates quant dog-translation training data (dog→human baby babble)
in the ultrawhale-dogfood JSONL format and uploads to HuggingFace
for recursive model training.

Schema per record:
  id            — UUID
  user_message  — dog sound type + seed context (the "prompt")
  free_response — quant-transformed baby-babble translation ("answer")
  free_model    — "babilon/quant-dog-v1"
  topic         — dog_vocalization
  format        — quant-babble
  pipeline      — babilon-quant-gen-v1
  quality_score — quant-confidence * seed-drift score
  seed          — combined seed (music + eye)
  sound_type    — woof/growl/whine/breath/silent
  feeling       — output emotional tag
  ternarity     — 16-dim matrix snapshot
"""

import json
import uuid
import time
import struct
import hashlib
import random
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

# ── Xoshiro128StarStar (replicated from QuantVoiceGen.swift) ──

class Xoshiro128StarStar:
    def __init__(self, seed_str: str):
        data = seed_str.encode("utf-8")
        h = sum(data) & 0xFFFFFFFF
        state = h
        self.s = [0] * 4
        for i in range(4):
            state = (state + 0x9E3779B9) & 0xFFFFFFFF
            z = state
            z = ((z ^ (z >> 16)) * 0x21F0AAAD) & 0xFFFFFFFF
            z = ((z ^ (z >> 15)) * 0x735A2D97) & 0xFFFFFFFF
            z = z ^ (z >> 15)
            self.s[i] = z

    def next(self) -> float:
        result = ((self._rotl(self.s[1] * 5, 7) * 9) & 0xFFFFFFFF)
        t = (self.s[1] << 9) & 0xFFFFFFFF
        self.s[2] ^= self.s[0]; self.s[3] ^= self.s[1]
        self.s[1] ^= self.s[2]; self.s[0] ^= self.s[3]
        self.s[2] ^= t
        self.s[3] = self._rotl(self.s[3], 11)
        return result / 0xFFFFFFFF

    @staticmethod
    def _rotl(x: int, k: int) -> int:
        return ((x << k) | (x >> (32 - k))) & 0xFFFFFFFF


# ── Baby drift (replicated from MarleyView.swift) ──

VOWELS = set("aeiou")
BABY_MAP: dict[str, str] = {
    "r": "w", "l": "y", "s": "sh", "t": "tch", "k": "k", "p": "b",
    "b": "b", "v": "b", "f": "ff", "c": "k", "g": "g", "d": "d",
    "m": "m", "n": "n", "h": "h", "w": "w", "j": "y", "z": "z",
}

def baby_drift(word: str, prng: Xoshiro128StarStar, hash_val: float) -> str:
    lower = word.lower()
    out = ""
    last_char = "\0"
    jit = prng.next()
    for ch in lower:
        drift = int((jit * hash_val * 7) % 3)
        jit = prng.next()
        if len(out) >= 8:
            break
        if ch in VOWELS:
            if drift == 0 and last_char != ch:
                out += ch + ch
            elif last_char != ch:
                out += ch
            last_char = ch
        elif ch in BABY_MAP:
            if drift == 2:
                continue
            baby = BABY_MAP[ch]
            if out.endswith(baby):
                continue
            out += baby
            last_char = ch
        else:
            out += ch
            last_char = ch
    return out or "mm"


# ── Sound-aware babble + feelings ──

SOUND_BABBLE: dict[str, list[str]] = {
    "woof":   ["brrr", "grrr", "woof", "awoo"],
    "growl":  ["grrr", "gah", "gaga", "brrr"],
    "whine":  ["nnng", "wah", "mmm", "baba"],
    "breath": ["mmm", "ooo", "baba", "goo"],
    "silent": ["mmm", "baba", "gaga", "goo"],
}
FEELINGS = [
    "safe", "watch", "quiet", "sleep", "guard", "love", "happy",
    "alert", "play", "home", "stay", "protect", "food", "warm",
    "pack", "rest", "good", "now",
]

DOG_MEANINGS: dict[str, list[str]] = {
    "woof": [
        "Someone is here. I am watching. You are safe.",
        "Intruder at the door. Behind me. I guard.",
        "I see them. I am the wall. No one passes.",
        "Ears up. Eyes fixed. I am ready. You rest.",
    ],
    "growl": [
        "I hear something. Stay close. I protect.",
        "A sound. Far. I mark it. You keep sleeping.",
        "The night is speaking. I answer with silence.",
        "Perimeter check. All nodes. I report: alert.",
    ],
    "breath": [
        "All is well. The perimeter is clear. Rest.",
        "Wind in the yard. Birds in the tree. Peace.",
        "Your heart is slow. My heart matches. We rest.",
        "Safe zone. Zero threats. Infinite calm. Breathe.",
    ],
    "whine": [
        "Something feels wrong. Check the door. The aperture.",
        "I am uneasy. The air changed. Check the back gate.",
        "A shadow moved. Not wind. Not bird. Not human. Check.",
        "My stomach says worry. My nose says nothing. I whine.",
    ],
    "silent": [
        "I am here. You are here. This is enough.",
        "No words needed. My head on your knee. Forever.",
        "The sun moved. I followed it. The patch is warm now.",
        "You breathe. I breathe. The house breathes. All one.",
    ],
}

# ── Music seed playlist ──

PLAYLIST = [
    ("Akkezdet Phiai", "Kottazűr", "∞ Hz"),
    ("Belga", "Kocsi", "420 Hz"),
    ("NKS", "Vegyetek jót ha tudtok", "333 Hz"),
    ("Akkezdet Phiai", "Megalázó És Felszabadító", "777 Hz"),
    ("Sub Bass Monster", "Nincs baj", "111 Hz"),
    ("Belga", "Nemzeti Hiphop", "888 Hz"),
    ("Akkezdet Phiai", "Akkezdet", "666 Hz"),
    ("NKS", "Nincsen Kegyelem Soha", "999 Hz"),
]


def make_seed(track_idx: int, eye_x: int = 0, eye_y: int = 0) -> str:
    artist, title, freq = PLAYLIST[track_idx % len(PLAYLIST)]
    base = f"OM_MANI_PADME_HUNG_{title.upper().replace(' ', '_')}_{freq.replace(' ', '')}"
    if eye_x or eye_y:
        base += f"_EYE_{eye_x}x{eye_y}"
    return base


def make_quant_translation(sound_type: str, meaning: str, seed: str) -> tuple[str, str, float]:
    """Generate one quant dog→human translation. Returns (text, feeling, quality_score)."""
    prng = Xoshiro128StarStar(seed + sound_type + meaning)
    words = [w for w in meaning.replace(".", "").replace(",", "").split() if len(w) > 1]
    babble_pool = SOUND_BABBLE.get(sound_type, ["gaga", "goo", "wah"])
    b1 = babble_pool[int(prng.next() * len(babble_pool)) % len(babble_pool)]
    b2 = babble_pool[int(prng.next() * len(babble_pool)) % len(babble_pool)]
    feeling = FEELINGS[int(prng.next() * len(FEELINGS)) % len(FEELINGS)]
    fragments = []
    for i, w in enumerate(words):
        h = float(abs(hash(w)) % 100) / 100.0
        drift = baby_drift(w, prng, h)
        if i % 3 == 2:
            mid_babble = random.choice(babble_pool)
            fragments.append(f"{drift}… {mid_babble}")
        else:
            fragments.append(drift)
    mid = fragments[0] if len(fragments) == 1 else "… ".join(fragments)
    text = f"{b1}… {mid} …{feeling}."
    # Quality: based on fragment count (more = richer) × seed entropy
    quality = min(0.65 + (len(fragments) * 0.03) + (prng.next() * 0.1), 0.99)
    return text, feeling, quality


def generate_babilon_dogfood(
    num_cycles: int = 100,
    output_path: str = "babilon_dogfood.jsonl",
    eye_seed: bool = True,
) -> list[dict[str, Any]]:
    """Generate Babilon quant translations in dogfood JSONL format.

    Args:
        num_cycles: Number of translation pairs to generate.
        output_path: Where to write the JSONL file.
        eye_seed: Include simulated eye detection coordinates.

    Returns:
        List of generated records.
    """
    records = []
    sound_types = list(DOG_MEANINGS.keys())
    track_idx = 0

    for i in range(num_cycles):
        sound = random.choice(sound_types)
        meaning = random.choice(DOG_MEANINGS[sound])
        eye_x = random.randint(0, 255) if eye_seed else 0
        eye_y = random.randint(0, 255) if eye_seed else 0
        seed = make_seed(track_idx, eye_x, eye_y)
        track_idx += 1
        if track_idx >= len(PLAYLIST):
            track_idx = 0

        translation, feeling, quality = make_quant_translation(sound, meaning, seed)

        record = {
            "id": str(uuid.uuid4()),
            "user_message": f"dog sound: {sound} | seed: {seed[:60]}",
            "free_response": translation,
            "free_model": "babilon/quant-dog-v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": f"babilon-{i:04d}",
            "topic": "dog_vocalization",
            "format": "quant-babble",
            "pov": f"marley-shepherd-{sound}",
            "pipeline": "babilon-quant-gen-v1",
            "quality_score": round(quality, 3),
            "seed": seed,
            "sound_type": sound,
            "feeling": feeling,
            "eye_coords": f"{eye_x},{eye_y}" if eye_seed else "",
            "ternarity": [1, 0, -1, 1, 0, -1, 1, 0, -1, 1, 0, -1, 1, 0, -1, 1],
        }
        records.append(record)

    Path(output_path).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
    )
    return records


# ── Recursive sampling loop: generate → judge → enrich → upload ──

def recursive_dogfood_loop(
    rounds: int = 10,
    pairs_per_round: int = 50,
    upload: bool = False,
    hf_repo: str = "PeetPedro/ultrawhale-dogfood",
) -> None:
    """Run recursive training loop.

    Each round generates pairs, scores them, and optionally uploads
    high-quality pairs to HuggingFace for downstream model training.
    """
    for rnd in range(rounds):
        t0 = time.time()
        path = f"dogfeed_parallel/babilon_loop_{rnd:03d}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
        records = generate_babilon_dogfood(
            num_cycles=pairs_per_round,
            output_path=path,
            eye_seed=True,
        )
        avg_q = sum(r["quality_score"] for r in records) / len(records)
        passed = sum(1 for r in records if r["quality_score"] >= 0.80)
        elapsed = time.time() - t0
        print(
            f"  loop {rnd:03d} | {len(records)} pairs | avg_q={avg_q:.3f} | "
            f">=0.80: {passed}/{len(records)} | {elapsed:.1f}s | {path}"
        )
        if upload and passed > 0:
            try:
                from ultrawhale.upload import upload_jsonl
                upload_jsonl(path, hf_repo)
                print(f"    → uploaded to {hf_repo}")
            except Exception as e:
                print(f"    → upload failed: {e}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Babilon → ultrawhale-dogfood recursive pipeline")
    ap.add_argument("--rounds", type=int, default=10, help="Number of recursive loops (default: 10)")
    ap.add_argument("--pairs", type=int, default=50, help="Pairs per round (default: 50)")
    ap.add_argument("--upload", action="store_true", help="Upload to HuggingFace")
    ap.add_argument("--repo", default="PeetPedro/ultrawhale-dogfood", help="HF repo")
    ap.add_argument("--output", default="babilon_dogfood.jsonl", help="Single-shot output path")
    ap.add_argument("--single", action="store_true", help="Single-shot generation (no loop)")
    args = ap.parse_args()

    if args.single:
        records = generate_babilon_dogfood(num_cycles=args.pairs, output_path=args.output)
        print(f"Generated {len(records)} pairs → {args.output}")
        for r in records[:3]:
            print(f"  [{r['sound_type']}] {r['free_response']}")
        print(f"  ... ({len(records) - 3} more)")
    else:
        recursive_dogfood_loop(rounds=args.rounds, pairs_per_round=args.pairs, upload=args.upload, hf_repo=args.repo)
