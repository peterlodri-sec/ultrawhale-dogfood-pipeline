# SPDX-License-Identifier: MIT
"""Parallel orchestrator — coordinates workers, resources, compression, and aggregation.

The orchestrator manages the full pipeline lifecycle: launching generation
workers, monitoring resource usage with autoscaling, scheduling kompress
post-processing, and aggregating results into HF-ready datasets.
"""

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from ultrawhale.config import Config
from ultrawhale.logging import get_logger

logger = get_logger("orchestrator")

# --- Optional dependencies ---
try:
    from ultrawhale.resources import ProcessManager, ResourceManager
except ImportError:
    logger.debug("resource_manager not available — resource limits disabled")
    ResourceManager = None  # type: ignore[misc,assignment]
    ProcessManager = None  # type: ignore[misc,assignment]

KOMPRESS_AVAILABLE = False
try:
    from ultrawhale.kompress import compress_jsonl_file

    KOMPRESS_AVAILABLE = True
except ImportError:
    logger.debug("kompress not available — compression disabled")

# --- Configuration ---
cfg = Config()
SCRIPT_DIR = Path(__file__).parent.parent.parent  # repo root
LOG_DIR = cfg.log_dir
OUTPUT_DIR = cfg.output_dir
LLM_MODEL = cfg.llm_model
LLM_HOST = cfg.llm_host

PARALLEL_WORKERS = 5  # matches len(WORKERS_CONFIG)
PAIRS_PER_WORKER = 5
RETRY_INTERVAL = cfg.retry_interval
ROUND_TIMEOUT = cfg.round_timeout

WORKERS_CONFIG = [
    {"category": "cs", "topic_suffix": "cs", "model": LLM_MODEL},
    {"category": "physics", "topic_suffix": "physics", "model": LLM_MODEL},
    {"category": "all", "topic_suffix": "general", "model": LLM_MODEL},
    {"category": "all", "topic_suffix": "math", "model": LLM_MODEL},
    {"category": "all", "topic_suffix": "philosophy", "model": LLM_MODEL},
]

# --- Shutdown state ---
_shutdown_requested = False


def _handle_shutdown(signum: int, frame) -> None:
    """Signal handler for graceful shutdown."""
    global _shutdown_requested
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    _shutdown_requested = True


# Signal handlers registered in __main__ block to avoid hijacking importers


def setup_dirs() -> None:
    """Create log and output directories."""
    LOG_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)


def launch_worker(worker_id: int, config: dict) -> subprocess.Popen:
    """Launch a single generation worker subprocess."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_file = OUTPUT_DIR / f"dogfeed_{config['topic_suffix']}_{timestamp}.jsonl"
    log_file = LOG_DIR / f"worker_{worker_id}_{config['topic_suffix']}.log"

    model = config.get("model", LLM_MODEL)
    cmd = [
        sys.executable,
        "-m",
        "ultrawhale.generate",
        "--num",
        str(PAIRS_PER_WORKER),
        "--category",
        config["category"],
        "--model",
        model,
        "--host",
        LLM_HOST,
        "--output",
        str(output_file),
    ]
    logger.info(f"Worker-{worker_id} launching {config['topic_suffix']} ({model}) → {output_file.name}")

    env = os.environ.copy()
    if "HF_TOKEN" not in env and cfg.hf_token:
        env["HF_TOKEN"] = cfg.hf_token

    with open(log_file, "w") as log_f:
        return subprocess.Popen(
            cmd,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            cwd=SCRIPT_DIR,
            env=env,
        )


def monitor_workers(processes: list[subprocess.Popen]) -> dict:
    """Monitor worker processes and return status."""
    status: dict[int, str] = {}
    for i, proc in enumerate(processes):
        if proc.poll() is None:
            status[i] = "running"
        elif proc.returncode == 0:
            status[i] = "completed"
        else:
            status[i] = f"failed (code {proc.returncode})"
    return status


def run_kompress_scheduler() -> None:
    """Background thread: compress files every 5 minutes."""
    if not KOMPRESS_AVAILABLE:
        logger.debug("Kompress scheduler not started — module unavailable")
        return

    while not _shutdown_requested:
        time.sleep(300)

        try:
            raw_files = list(OUTPUT_DIR.glob("dogfeed_*.jsonl"))
            if not raw_files:
                continue

            latest = max(raw_files, key=lambda p: p.stat().st_mtime)
            if latest.name.endswith("_kompressed.jsonl"):
                continue

            logger.info(f"Kompress processing {latest.name}...")
            output_file = latest.parent / latest.name.replace(".jsonl", "_kompressed.jsonl")
            compress_jsonl_file(str(latest), str(output_file))

        except Exception as e:
            logger.warning(f"Kompress scheduler error: {e}")


def aggregate_results() -> None:
    """Merge parallel outputs into single HF-ready JSONL files."""
    import json

    topic_data: dict[str, list[dict]] = {}

    for file in OUTPUT_DIR.glob("dogfeed_*.jsonl"):
        topic = file.name.split("_")[1]
        if topic not in topic_data:
            topic_data[topic] = []

        with open(file) as f:
            for line in f:
                if line.strip():
                    with contextlib.suppress(json.JSONDecodeError):
                        topic_data[topic].append(json.loads(line))

    for topic, data in topic_data.items():
        agg_file = SCRIPT_DIR / f"dogfeed_{topic}_aggregated.jsonl"
        with open(agg_file, "w") as f:
            for record in data:
                f.write(json.dumps(record) + "\n")
        logger.info(f"Aggregated {topic}: {len(data)} samples → {agg_file.name}")


def run_fast_loop(rounds_per_cycle: int = 2) -> None:
    """Run fast generation loop with autoscaling (indefinite)."""
    setup_dirs()

    cycle = 0
    cycle_interval = 600  # 10 minutes
    min_workers = cfg.min_workers
    max_workers = cfg.max_workers
    current_workers = 3

    logger.info(f"Fast loop starting: {rounds_per_cycle} rounds × 10min, workers: {min_workers}-{max_workers}")

    # --- Resource manager ---
    rm: ResourceManager | None = None
    pm: ProcessManager | None = None
    if ResourceManager is not None:
        rm = ResourceManager(
            max_memory_percent=cfg.max_memory_percent,
            max_cpu_percent=cfg.max_cpu_percent,
        )
        pm = ProcessManager(rm)
        logger.info(f"Resource manager enabled (mem<{cfg.max_memory_percent}%, cpu<{cfg.max_cpu_percent}%)")

    # --- Kompress scheduler ---
    kompress_thread = threading.Thread(target=run_kompress_scheduler, daemon=True)
    kompress_thread.start()
    logger.info("Kompress scheduler started (every 5min)")

    while not _shutdown_requested:
        cycle += 1
        cycle_start = time.time()
        logger.info(f"Cycle {cycle} starting (workers: {current_workers})")

        # --- Autoscaling ---
        if rm:
            status = rm.get_status()
            mem = status.get("memory_percent", 0)
            cpu = status.get("cpu_percent", 0)
            if mem < 40 and cpu < 60 and current_workers < max_workers:
                current_workers += 1
                logger.info(f"Autoscaler UP: {current_workers} workers")
            elif (mem > 65 or cpu > 85) and current_workers > min_workers:
                current_workers -= 1
                logger.info(f"Autoscaler DOWN: {current_workers} workers")

        for round_num in range(1, rounds_per_cycle + 1):
            if _shutdown_requested:
                break

            round_start = time.time()
            logger.info(f"Round {round_num}/{rounds_per_cycle}")

            if rm and not rm.wait_for_resources(max_wait=60):
                logger.warning("Resources unavailable, skipping round")
                continue

            # --- Launch workers ---
            processes: list[subprocess.Popen] = []
            for i, config in enumerate(WORKERS_CONFIG[:current_workers]):
                if rm and not rm.is_system_healthy():
                    time.sleep(3)
                proc = launch_worker(i, config)
                processes.append(proc)
                time.sleep(0.5)

            # --- Wait with timeout ---
            round_start_time = time.time()
            while any(p.poll() is None for p in processes):
                if _shutdown_requested:
                    for p in processes:
                        if p.poll() is None:
                            p.terminate()
                    break
                if time.time() - round_start_time > ROUND_TIMEOUT:
                    logger.warning(f"Round timeout ({ROUND_TIMEOUT}s), killing stragglers")
                    for p in processes:
                        if p.poll() is None:
                            try:
                                p.terminate()
                                p.wait(timeout=2)
                            except Exception:
                                p.kill()
                    break
                time.sleep(2)

            # --- Stats ---
            if pm:
                stats = pm.get_process_stats()
                logger.info(f"Stats: {stats.get('alive', 0)} alive, {stats.get('dead', 0)} dead")

            round_elapsed = time.time() - round_start
            logger.info(f"Round completed in {round_elapsed:.0f}s")

        # --- Aggregate ---
        try:
            aggregate_results()
        except Exception as e:
            logger.warning(f"Aggregation failed: {e}")

        if _shutdown_requested:
            break

        elapsed = time.time() - cycle_start
        sleep_time = max(0, cycle_interval - elapsed)
        logger.info(f"Cycle {cycle} done ({elapsed:.0f}s). Sleeping {sleep_time:.0f}s...")
        time.sleep(sleep_time)

    # --- Cleanup ---
    if pm:
        pm.cleanup_all()
        logger.info("All workers terminated")

    logger.info("Orchestrator shutdown complete.")


def run_parallel_loop(duration_hours: float = 24) -> None:
    """Run parallel ralph loop for a fixed duration."""
    setup_dirs()
    start_time = time.time()
    duration_secs = duration_hours * 3600
    iteration = 0

    logger.info(f"Starting {PARALLEL_WORKERS} workers for {duration_hours}h")
    logger.info(f"Each iteration: {PARALLEL_WORKERS} workers × {PAIRS_PER_WORKER} pairs")

    while time.time() - start_time < duration_secs and not _shutdown_requested:
        iteration += 1
        elapsed_hours = (time.time() - start_time) / 3600
        logger.info(f"Iteration {iteration} — elapsed: {elapsed_hours:.1f}h")

        processes = []
        for i, config in enumerate(WORKERS_CONFIG[:PARALLEL_WORKERS]):
            proc = launch_worker(i, config)
            processes.append(proc)
            time.sleep(2)

        logger.info(f"Waiting for {len(processes)} workers...")
        while any(p.poll() is None for p in processes):
            if _shutdown_requested:
                for p in processes:
                    if p.poll() is None:
                        p.terminate()
                break
            status = monitor_workers(processes)
            time.sleep(10)

        status = monitor_workers(processes)
        logger.info(f"Results: {status}")

        try:
            aggregate_results()
        except Exception as e:
            logger.warning(f"Aggregation failed: {e}")

        remaining = duration_secs - (time.time() - start_time)
        if remaining > 0 and not _shutdown_requested:
            logger.info(f"Sleeping {RETRY_INTERVAL}s (remaining: {remaining / 3600:.1f}h)")
            time.sleep(min(RETRY_INTERVAL, remaining))

    logger.info(f"Parallel loop complete after {(time.time() - start_time) / 3600:.1f}h")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ultrawhale parallel orchestrator")
    parser.add_argument(
        "--mode",
        choices=["fast", "parallel"],
        default="fast",
        help="Loop mode: fast (indefinite cycles) or parallel (fixed duration)",
    )
    parser.add_argument("--rounds", type=int, default=2, help="Rounds per cycle (fast mode)")
    parser.add_argument("--duration", type=float, default=24, help="Duration in hours (parallel mode)")
    parser.add_argument("--workers", type=int, default=5, help="Number of workers")
    parser.add_argument("--pairs-per-worker", type=int, default=5, help="Pairs per worker")
    args = parser.parse_args()

    PAIRS_PER_WORKER = args.pairs_per_worker
    PARALLEL_WORKERS = args.workers

    from ultrawhale.logging import setup_logging

    setup_logging(component="orchestrator")

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    if args.mode == "fast":
        run_fast_loop(args.rounds)
    else:
        run_parallel_loop(args.duration)
