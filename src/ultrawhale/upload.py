# SPDX-License-Identifier: MIT
"""Upload local dogfeed JSONL files to HuggingFace — non-destructive, read-only.

Rules:
  - Never deletes or moves local files
  - Skips the most recently modified file (likely still being written)
  - Skips files already present on HF (checks by filename)
  - Skips empty files
  - Uploads one at a time with progress
"""

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from ultrawhale.config import Config
from ultrawhale.logging import get_logger

logger = get_logger("upload")

try:
    from huggingface_hub import CommitOperationAdd, HfApi
except ImportError:
    logger.error("huggingface_hub not installed. Run: pip install huggingface_hub")
    sys.exit(1)


def upload_dogfeed(
    directory: Path,
    hf_repo: str | None = None,
    hf_token: str | None = None,
    pattern: str = "dogfeed_*.jsonl",
    active_grace_minutes: int = 30,
    dry_run: bool = False,
) -> int:
    """Upload eligible dogfeed files to HuggingFace.

    Args:
        directory: Directory containing dogfeed JSONL files.
        hf_repo: HF repo ID (defaults to Config.hf_repo).
        hf_token: HF API token (defaults to Config.hf_token).
        pattern: Glob pattern for local files.
        active_grace_minutes: Skip files modified within this many minutes.
        dry_run: If True, show what would be uploaded without pushing.

    Returns:
        Number of files uploaded.
    """
    cfg = Config()
    repo = hf_repo or cfg.hf_repo
    token = hf_token or cfg.hf_token

    if not dry_run and not token:
        logger.error("HF_TOKEN not set — use --dry-run or set HF_TOKEN")
        sys.exit(1)

    target_dir = directory.expanduser().resolve()
    if not target_dir.is_dir():
        logger.error(f"Directory not found: {target_dir}")
        sys.exit(1)

    # --- Gather local files ---
    local_files = sorted(target_dir.glob(pattern))
    now = time.time()
    grace_secs = active_grace_minutes * 60

    eligible: list[Path] = []
    skipped_active: list[tuple[str, int]] = []
    skipped_empty: list[str] = []

    for f in local_files:
        if f.stat().st_size == 0:
            skipped_empty.append(f.name)
            continue
        age_secs = now - f.stat().st_mtime
        if age_secs < grace_secs:
            skipped_active.append((f.name, int(age_secs // 60)))
        else:
            eligible.append(f)

    logger.info(f"Directory: {target_dir}")
    logger.info(f"Local files found: {len(local_files)}")
    if skipped_empty:
        logger.info(f"  skip (empty): {', '.join(skipped_empty)}")
    for name, age_min in skipped_active:
        logger.info(f"  skip (active, {age_min}min old): {name}")
    logger.info(f"Eligible for upload: {len(eligible)}")

    if not eligible:
        logger.info("Nothing to upload.")
        return 0

    # --- Check HF for existing files ---
    logger.info("Checking HF for already-uploaded files...")
    api = HfApi()
    try:
        hf_files = set(api.list_repo_files(repo, repo_type="dataset", token=token))
    except Exception as e:
        logger.error(f"Could not list HF files: {e}")
        sys.exit(1)

    to_upload = [f for f in eligible if f.name not in hf_files]
    already = [f for f in eligible if f.name in hf_files]

    if already:
        logger.info(f"Already on HF: {len(already)} files — skipping")
    logger.info(f"To upload: {len(to_upload)} files")

    if not to_upload:
        logger.info("All eligible files already on HF.")
        return 0

    # --- Upload ---
    uploaded = 0
    for i, f in enumerate(to_upload, 1):
        size_kb = f.stat().st_size // 1024
        logger.info(f"[{i:03d}/{len(to_upload)}] {f.name} ({size_kb} KB)")

        if dry_run:
            logger.info("  [dry-run]")
            continue

        try:
            content = f.read_bytes()
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            api.create_commit(
                repo_id=repo,
                repo_type="dataset",
                operations=[
                    CommitOperationAdd(
                        path_in_repo=f.name,
                        path_or_fileobj=content,
                    )
                ],
                commit_message=f"upload: {f.name} (ultrawhale pipeline) [{ts}]",
                token=token,
            )
            uploaded += 1
            logger.info(f"  ✓ Uploaded {f.name}")
        except Exception as e:
            logger.error(f"  Upload failed: {e}")

    mode = "dry-run" if dry_run else "done"
    logger.info(f"{mode}: {uploaded} files uploaded, {len(to_upload)} processed.")
    return uploaded


def upload_jsonl(
    file_path: str | Path,
    hf_repo: str | None = None,
    hf_token: str | None = None,
) -> bool:
    """Upload a single JSONL file to a HuggingFace dataset repository."""
    cfg = Config()
    repo = hf_repo or cfg.hf_repo
    token = hf_token or cfg.hf_token
    if not token:
        logger.error("HF_TOKEN not set — cannot upload JSONL file")
        return False

    p = Path(file_path)
    if not p.exists() or p.stat().st_size == 0:
        return False

    api = HfApi()
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    api.create_commit(
        repo_id=repo,
        repo_type="dataset",
        operations=[
            CommitOperationAdd(
                path_in_repo=p.name,
                path_or_fileobj=p.read_bytes(),
            )
        ],
        commit_message=f"upload: {p.name} (ultrawhale pipeline) [{ts}]",
        token=token,
    )
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Upload dogfeed to HuggingFace")
    parser.add_argument("--dir", type=Path, default=Path("."), help="Directory with dogfeed files")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be uploaded")
    parser.add_argument("--active-grace", type=int, default=30, help="Skip files modified within N minutes")
    parser.add_argument("--pattern", default="dogfeed_*.jsonl", help="Glob pattern")
    args = parser.parse_args()

    upload_dogfeed(args.dir, active_grace_minutes=args.active_grace, pattern=args.pattern, dry_run=args.dry_run)
