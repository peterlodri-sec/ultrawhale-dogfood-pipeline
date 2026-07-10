# SPDX-License-Identifier: MIT
"""Ultrawhale CLI — entry point for all pipeline commands."""

import argparse
import sys
from pathlib import Path

from ultrawhale import __version__
from ultrawhale.logging import get_logger, setup_logging


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate Q&A pairs."""
    from ultrawhale.generate import generate_dataset

    setup_logging(component="generate")
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
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    """Upload dogfeed files to HuggingFace."""
    from ultrawhale.upload import upload_dogfeed

    setup_logging(component="upload")
    upload_dogfeed(
        directory=args.dir,
        active_grace_minutes=args.active_grace,
        pattern=args.pattern,
        dry_run=args.dry_run,
    )
    return 0


def cmd_compress(args: argparse.Namespace) -> int:
    """Compress dogfeed files with kompress-v8."""
    from ultrawhale.kompress import compress_jsonl_file

    setup_logging(component="kompress")
    output = args.output or args.input.replace(".jsonl", "_kompressed.jsonl")
    count = compress_jsonl_file(args.input, output, args.token)
    return 0 if count >= 0 else 1


def cmd_status(args: argparse.Namespace) -> int:
    """Health check — report pipeline status."""
    from ultrawhale.config import Config

    setup_logging(component="status")

    cfg = Config()
    logger = get_logger("status")

    warnings = cfg.validate()
    if warnings:
        for w in warnings:
            logger.warning(w)
    else:
        logger.info("Configuration valid.")

    logger.info(f"LLM server: {cfg.llm_host} (model: {cfg.llm_model})")
    logger.info(f"HF token: {cfg.mask_token()}")
    logger.info(f"HF repo: {cfg.hf_repo}")
    logger.info(f"Log dir: {cfg.log_dir}")
    logger.info(f"Output dir: {cfg.output_dir}")
    logger.info(f"Workers: {cfg.min_workers}-{cfg.max_workers}")
    logger.info(f"Quality threshold: {cfg.min_quality_score}")

    # Check LLM server reachability
    try:
        import openai

        client = openai.OpenAI(base_url=f"{cfg.llm_host}/v1", api_key="none")
        models = client.models.list()
        logger.info(f"LLM server reachable — {len(models.data) if hasattr(models, 'data') else '?'} models")
    except Exception as e:
        logger.warning(f"LLM server not reachable: {e}")

    return 0


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="ultrawhale",
        description="Ultrawhale Dogfeed Pipeline — industrial-grade Q&A data synthesis",
    )
    parser.add_argument("--version", action="version", version=f"ultrawhale {__version__}")
    parser.add_argument("--json-log", action="store_true", help="Emit JSON-structured logs")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- generate ---
    gen_parser = subparsers.add_parser("generate", help="Generate Q&A pairs")
    gen_parser.add_argument("--model", default="qwen3.6-27b", help="Model name")
    gen_parser.add_argument("--num", type=int, default=100, help="Number of Q&A pairs")
    gen_parser.add_argument("--output", default="dogfeed.jsonl", help="Output JSONL file")
    gen_parser.add_argument("--host", default="http://localhost:8080", help="LLM server URL")
    gen_parser.add_argument(
        "--category",
        default="all",
        choices=[
            "all",
            "cs",
            "physics",
            "hybrid",
            "diverse",
            "diverse2",
            "philosophy",
            "socrates",
            "modphil",
            "space",
            "history",
        ],
    )
    gen_parser.add_argument("--hybrid", action="store_true", help="Use HF Inference as fallback")
    gen_parser.add_argument("--difficulty", action="store_true", help="Enable difficulty-aware sampling")
    gen_parser.add_argument("--hf-only", action="store_true", help="Skip local LLM, use HF only")
    gen_parser.add_argument("--skip-curation", action="store_true", help="Skip LLM-judge curation")
    gen_parser.set_defaults(func=cmd_generate)

    # --- upload ---
    up_parser = subparsers.add_parser("upload", help="Upload dogfeed to HuggingFace")
    up_parser.add_argument("--dir", type=Path, default=Path("."), help="Directory with dogfeed files")
    up_parser.add_argument("--dry-run", action="store_true")
    up_parser.add_argument("--active-grace", type=int, default=30)
    up_parser.add_argument("--pattern", default="dogfeed_*.jsonl")
    up_parser.set_defaults(func=cmd_upload)

    # --- compress ---
    comp_parser = subparsers.add_parser("compress", help="Compress with kompress-v8")
    comp_parser.add_argument("input", help="Input JSONL file")
    comp_parser.add_argument("--output", help="Output JSONL file")
    comp_parser.add_argument("--token", help="HF_TOKEN override")
    comp_parser.set_defaults(func=cmd_compress)

    # --- status ---
    status_parser = subparsers.add_parser("status", help="Health check")
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    setup_logging(json_mode=args.json_log)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
