#!/usr/bin/env python3
"""Merge dogfeed with MemoryAgentBench + DialogSum datasets."""

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

try:
    from datasets import load_dataset
except ImportError:
    print("Error: datasets not installed. Run: pip install datasets", file=sys.stderr)
    sys.exit(1)


def load_memory_agent_bench() -> Iterator[dict]:
    """Load MemoryAgentBench dataset."""
    print("[LOAD] MemoryAgentBench...", file=sys.stderr)
    try:
        dataset = load_dataset("ai-hyz/MemoryAgentBench", split="train")
        for sample in dataset:
            yield {
                "id": str(uuid.uuid4()),
                "user_message": sample.get("question", ""),
                "free_response": sample.get("answer", ""),
                "free_model": "MemoryAgentBench",
                "deepseek_response": "",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "session_id": str(uuid.uuid4())[:8],
                "topic": sample.get("category", "memory-agent"),
                "format": "qa-pair",
                "pov": "",
                "capabilities": "",
                "space_node": "",
                "memory_ref": "",
                "enriched_at": "",
                "pipeline": "import-memory-agent-bench",
                "source": "MemoryAgentBench",
            }
    except Exception as e:
        print(f"[ERROR] Loading MemoryAgentBench: {e}", file=sys.stderr)


def load_dialogsum() -> Iterator[dict]:
    """Load DialogSum dataset."""
    print("[LOAD] DialogSum...", file=sys.stderr)
    try:
        dataset = load_dataset("knkarthick/dialogsum", split="train")
        for sample in dataset:
            # DialogSum: convert dialogue → Q&A format
            dialogue = sample.get("dialogue", "")
            summary = sample.get("summary", "")

            yield {
                "id": str(uuid.uuid4()),
                "user_message": f"Summarize this dialogue:\n{dialogue}",
                "free_response": summary,
                "free_model": "DialogSum",
                "deepseek_response": "",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "session_id": str(uuid.uuid4())[:8],
                "topic": "dialogue-summarization",
                "format": "qa-pair",
                "pov": "",
                "capabilities": "",
                "space_node": "",
                "memory_ref": "",
                "enriched_at": "",
                "pipeline": "import-dialogsum",
                "source": "DialogSum",
            }
    except Exception as e:
        print(f"[ERROR] Loading DialogSum: {e}", file=sys.stderr)


def load_traditional_chinese_aya() -> Iterator[dict]:
    """Load Traditional Chinese Aya collection (live compression language)."""
    print("[LOAD] Traditional Chinese Aya...", file=sys.stderr)
    try:
        dataset = load_dataset("Heng666/Traditional_Chinese-aya_collection", split="train")
        for sample in dataset:
            # Aya format: instruction-based Q&A
            instruction = sample.get("instruction", "")
            output = sample.get("output", "")
            language = sample.get("language", "Traditional Chinese")

            yield {
                "id": str(uuid.uuid4()),
                "user_message": instruction,
                "free_response": output,
                "free_model": "Aya-Traditional-Chinese",
                "deepseek_response": "",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "session_id": str(uuid.uuid4())[:8],
                "topic": "traditional-chinese",
                "format": "qa-pair",
                "pov": "",
                "capabilities": "",
                "space_node": "",
                "memory_ref": "",
                "enriched_at": "",
                "pipeline": "import-traditional-chinese-aya",
                "source": "Traditional_Chinese-Aya",
                "language": language,
            }
    except Exception as e:
        print(f"[ERROR] Loading Traditional Chinese Aya: {e}", file=sys.stderr)


def load_philosophy_culture_translations() -> Iterator[dict]:
    """Load Philosophy-Culture-Translations (reflecting on philosophy/culture)."""
    print("[LOAD] Philosophy-Culture-Translations...", file=sys.stderr)
    try:
        dataset = load_dataset("AI-Culture-Commons/philosophy-culture-translations-html-csv", split="train")
        for sample in dataset:
            # Extract content from HTML/CSV
            title = sample.get("title", "")
            content = sample.get("content", "") or sample.get("text", "")
            source = sample.get("source", "Philosophy-Culture")

            # Format as reflective Q&A
            yield {
                "id": str(uuid.uuid4()),
                "user_message": f"Reflect on: {title}",
                "free_response": content,
                "free_model": "Philosophy-Culture-Commons",
                "deepseek_response": "",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "session_id": str(uuid.uuid4())[:8],
                "topic": "philosophy-culture",
                "format": "qa-pair",
                "pov": "",
                "capabilities": "",
                "space_node": "",
                "memory_ref": "",
                "enriched_at": "",
                "pipeline": "import-philosophy-culture-translations",
                "source": source,
            }
    except Exception as e:
        print(f"[ERROR] Loading Philosophy-Culture-Translations: {e}", file=sys.stderr)


def load_local_dogfeed(pattern: str = "dogfeed_*.jsonl") -> Iterator[dict]:
    """Load local dogfeed files."""
    print(f"[LOAD] Local dogfeed ({pattern})...", file=sys.stderr)
    for fpath in Path(".").glob(pattern):
        if "kompressed" in fpath.name or "aggregated" not in fpath.name:
            continue
        try:
            with open(fpath) as f:
                for line in f:
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            print(f"[⚠] Error reading {fpath}: {e}", file=sys.stderr)


def merge_datasets(output_file: str = "ultrawhale_merged.jsonl"):
    """Merge all datasets."""
    print(f"\n[MERGE] Combining datasets → {output_file}", file=sys.stderr)

    total = 0
    sources = {"dogfeed": 0, "memory_agent": 0, "dialogsum": 0, "traditional_chinese_aya": 0, "philosophy_culture": 0}

    with open(output_file, "w") as outf:
        # Load dogfeed
        try:
            for pair in load_local_dogfeed():
                outf.write(json.dumps(pair) + "\n")
                total += 1
                sources["dogfeed"] += 1
        except Exception as e:
            print(f"[⚠] Dogfeed loading error: {e}", file=sys.stderr)

        # Load MemoryAgentBench
        try:
            for pair in load_memory_agent_bench():
                outf.write(json.dumps(pair) + "\n")
                total += 1
                sources["memory_agent"] += 1
        except Exception as e:
            print(f"[⚠] MemoryAgentBench error: {e}", file=sys.stderr)

        # Load DialogSum
        try:
            for pair in load_dialogsum():
                outf.write(json.dumps(pair) + "\n")
                total += 1
                sources["dialogsum"] += 1
        except Exception as e:
            print(f"[⚠] DialogSum error: {e}", file=sys.stderr)

        # Load Traditional Chinese Aya
        try:
            for pair in load_traditional_chinese_aya():
                outf.write(json.dumps(pair) + "\n")
                total += 1
                sources["traditional_chinese_aya"] += 1
        except Exception as e:
            print(f"[⚠] Traditional Chinese Aya error: {e}", file=sys.stderr)

        # Load Philosophy-Culture-Translations
        try:
            for pair in load_philosophy_culture_translations():
                outf.write(json.dumps(pair) + "\n")
                total += 1
                sources["philosophy_culture"] += 1
        except Exception as e:
            print(f"[⚠] Philosophy-Culture error: {e}", file=sys.stderr)

    print(f"\n[✓] Merged: {total} pairs total", file=sys.stderr)
    for source, count in sources.items():
        print(f"    {source}: {count}", file=sys.stderr)

    return total


def upload_to_hf(file_path: str, repo_id: str = "PeetPedro/ultrawhale-dogfood", api_token: Optional[str] = None):
    """Upload merged dataset to HuggingFace."""
    import os
    from huggingface_hub import HfApi

    token = api_token or os.getenv("HF_TOKEN")
    if not token:
        print("[ERROR] HF_TOKEN not set", file=sys.stderr)
        return False

    print(f"\n[UPLOAD] Uploading to {repo_id}...", file=sys.stderr)
    try:
        api = HfApi(token=token)
        api.upload_file(
            path_or_fileobj=file_path,
            path_in_repo=Path(file_path).name,
            repo_id=repo_id,
            repo_type="dataset",
        )
        print(f"[✓] Upload complete: https://huggingface.co/datasets/{repo_id}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"[ERROR] Upload failed: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Merge dogfeed with external datasets")
    parser.add_argument("--output", default="ultrawhale_merged.jsonl", help="Output JSONL file")
    parser.add_argument("--upload", action="store_true", help="Upload to HuggingFace after merge")
    parser.add_argument("--repo", default="PeetPedro/ultrawhale-dogfood", help="HF repo ID")

    args = parser.parse_args()

    # Merge datasets
    total = merge_datasets(args.output)

    # Upload if requested
    if args.upload:
        upload_to_hf(args.output, args.repo)

    print(f"\n[DONE] {total} pairs in {args.output}")
