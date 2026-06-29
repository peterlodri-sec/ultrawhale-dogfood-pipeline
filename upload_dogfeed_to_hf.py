#!/usr/bin/env python3
"""Upload local dogfeed JSONL files to HuggingFace dataset."""

import os
import json
import glob
from pathlib import Path
from huggingface_hub import HfApi, create_repo
from datetime import datetime

REPO_ID = "PeetPedro/ultrawhale-dogfood"
REPO_TYPE = "dataset"
LOCAL_DIR = Path(__file__).parent
OUTPUT_DIR = LOCAL_DIR / "hf_upload_staging"

def prepare_files():
    """Consolidate dogfeed files into upload format."""
    dogfeed_files = sorted(glob.glob(str(LOCAL_DIR / "dogfeed*.jsonl")))
    print(f"Found {len(dogfeed_files)} dogfeed files")

    output_dir = OUTPUT_DIR
    output_dir.mkdir(exist_ok=True)

    # Merge all dogfeeds by topic
    merged = {}
    total_samples = 0

    for fpath in dogfeed_files:
        topic = "general"
        if "physics" in fpath:
            topic = "physics"
        elif "cs" in fpath:
            topic = "cs"

        if topic not in merged:
            merged[topic] = []

        with open(fpath) as f:
            for line in f:
                if line.strip():
                    try:
                        merged[topic].append(json.loads(line))
                        total_samples += 1
                    except json.JSONDecodeError:
                        pass

    # Write merged files
    for topic, data in merged.items():
        out_file = output_dir / f"dogfeed_{topic}.jsonl"
        with open(out_file, 'w') as f:
            for record in data:
                f.write(json.dumps(record) + '\n')
        print(f"  {topic}: {len(data)} samples → {out_file.name}")

    print(f"\nTotal: {total_samples} samples across {len(merged)} topics")
    return output_dir

def upload_to_hf(api_token: str):
    """Upload staging directory to HuggingFace."""
    api = HfApi(token=api_token)

    print(f"\nUploading to {REPO_ID}...")
    try:
        create_repo(REPO_ID, repo_type=REPO_TYPE, exist_ok=True, private=False)
        print(f"  ✓ Repo exists or created")
    except Exception as e:
        print(f"  Warning: {e}")

    # Upload all files from staging dir
    for fpath in OUTPUT_DIR.glob("*.jsonl"):
        print(f"  Uploading {fpath.name}...", end=" ")
        try:
            api.upload_file(
                path_or_fileobj=str(fpath),
                path_in_repo=fpath.name,
                repo_id=REPO_ID,
                repo_type=REPO_TYPE,
            )
            print("✓")
        except Exception as e:
            print(f"✗ {e}")

    print(f"\n✓ Upload complete: https://huggingface.co/datasets/{REPO_ID}")

if __name__ == "__main__":
    import sys

    token = os.getenv("HF_TOKEN")
    if not token:
        print("Error: HF_TOKEN not set")
        print("Set with: export HF_TOKEN=your_token_here")
        sys.exit(1)

    print("=== Preparing dogfeed files ===")
    prepare_files()

    print("\n=== Uploading to HuggingFace ===")
    upload_to_hf(token)
