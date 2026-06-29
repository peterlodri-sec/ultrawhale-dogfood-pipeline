#!/usr/bin/env python3
"""Parallel ralph loop orchestrator (Phase 3)."""

import os
import subprocess
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

try:
    from resource_manager import ResourceManager, ProcessManager
except ImportError:
    print("[⚠] resource_manager not available, skipping resource limits", file=sys.stderr)
    ResourceManager = None
    ProcessManager = None

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "ralph_logs"
OUTPUT_DIR = SCRIPT_DIR / "dogfeed_parallel"

PARALLEL_WORKERS = 5
PAIRS_PER_WORKER = 5   # HF inference ~10s/pair; 5 fits in 60s timeout
RETRY_INTERVAL = 30
ROUND_TIMEOUT = 120  # Max 120s per round
MISTRALRS_MODEL = "qwen3.6-27b"  # model name as served by mistralrs-server
MISTRALRS_HOST = "http://localhost:8080"
WORKERS_CONFIG = [
    {"category": "cs", "topic_suffix": "cs", "model": MISTRALRS_MODEL},
    {"category": "physics", "topic_suffix": "physics", "model": MISTRALRS_MODEL},
    {"category": "all", "topic_suffix": "general", "model": MISTRALRS_MODEL},
    {"category": "all", "topic_suffix": "math", "model": MISTRALRS_MODEL},
    {"category": "all", "topic_suffix": "philosophy", "model": MISTRALRS_MODEL},
]


def setup_dirs():
    """Create log/output directories."""
    LOG_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)


def launch_worker(worker_id: int, config: dict) -> subprocess.Popen:
    """Launch a single generation worker."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_file = OUTPUT_DIR / f"dogfeed_{config['topic_suffix']}_{timestamp}.jsonl"
    log_file = LOG_DIR / f"worker_{worker_id}_{config['topic_suffix']}.log"

    model = config.get("model", MISTRALRS_MODEL)
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "generate_dogfeed.py"),
        "--num", str(PAIRS_PER_WORKER),
        "--category", config["category"],
        "--model", model,
        "--host", MISTRALRS_HOST,
        "--output", str(output_file),
    ]
    print(f"[WORKER-{worker_id}] Launching {config['topic_suffix']} ({model}) → {output_file.name}")

    env = os.environ.copy()
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
    status = {}
    for i, proc in enumerate(processes):
        if proc.poll() is None:
            status[i] = "running"
        elif proc.returncode == 0:
            status[i] = "completed"
        else:
            status[i] = f"failed (code {proc.returncode})"
    return status


def run_kompress_scheduler():
    """Background thread: compress files every 5 minutes."""
    try:
        from kompress_postprocess import compress_jsonl_file
    except ImportError:
        print("[⚠] kompress_postprocess not available, skipping compression", file=sys.stderr)
        return

    while True:
        time.sleep(300)  # 5 minutes

        try:
            # Find most recent uncompressed file
            raw_files = list(OUTPUT_DIR.glob("dogfeed_*.jsonl"))
            if not raw_files:
                continue

            latest = max(raw_files, key=lambda p: p.stat().st_mtime)
            if latest.name.endswith("_kompressed.jsonl"):
                continue

            print(f"[KOMPRESS] Processing {latest.name}...", file=sys.stderr)
            output_file = latest.parent / latest.name.replace(".jsonl", "_kompressed.jsonl")
            compress_jsonl_file(str(latest), str(output_file))

        except Exception as e:
            print(f"[⚠] Kompress scheduler error: {e}", file=sys.stderr)


def aggregate_results():
    """Merge parallel outputs into single HF-ready JSONL files."""
    import json

    topic_data = {}

    for file in OUTPUT_DIR.glob("dogfeed_*.jsonl"):
        topic = file.name.split("_")[1]  # Extract topic from filename
        if topic not in topic_data:
            topic_data[topic] = []

        with open(file) as f:
            for line in f:
                if line.strip():
                    try:
                        topic_data[topic].append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    # Write aggregated files
    for topic, data in topic_data.items():
        agg_file = SCRIPT_DIR / f"dogfeed_{topic}_aggregated.jsonl"
        with open(agg_file, "w") as f:
            for record in data:
                f.write(json.dumps(record) + "\n")
        print(f"[AGG] {topic}: {len(data)} samples → {agg_file.name}")


def run_parallel_loop(duration_hours: float = 24):
    """Run parallel ralph loop for specified duration."""
    setup_dirs()
    start_time = time.time()
    duration_secs = duration_hours * 3600
    iteration = 0

    print(f"[RALPH-PARALLEL] Starting {PARALLEL_WORKERS} workers for {duration_hours}h")
    print(f"[RALPH-PARALLEL] Each iteration: {PARALLEL_WORKERS} workers × {PAIRS_PER_WORKER} pairs")

    while time.time() - start_time < duration_secs:
        iteration += 1
        elapsed_hours = (time.time() - start_time) / 3600
        print(f"\n[ITERATION-{iteration}] Elapsed: {elapsed_hours:.1f}h")

        # Launch workers
        processes = []
        for i, config in enumerate(WORKERS_CONFIG[:PARALLEL_WORKERS]):
            proc = launch_worker(i, config)
            processes.append(proc)
            time.sleep(2)  # Stagger starts

        # Monitor completion
        print(f"[MONITOR] Waiting for {len(processes)} workers...")
        while any(p.poll() is None for p in processes):
            status = monitor_workers(processes)
            completed = sum(1 for s in status.values() if s == "completed")
            print(f"  [{completed}/{len(processes)}] completed", end="\r")
            time.sleep(10)

        status = monitor_workers(processes)
        print(f"\n[RESULT] {status}")

        # Aggregate results
        try:
            aggregate_results()
        except Exception as e:
            print(f"[⚠] Aggregation failed: {e}")

        elapsed_secs = time.time() - start_time
        remaining_secs = duration_secs - elapsed_secs
        if remaining_secs > 0:
            print(f"[SLEEP] {RETRY_INTERVAL}s until next iteration (remaining: {remaining_secs / 3600:.1f}h)")
            time.sleep(RETRY_INTERVAL)

    # Cleanup
    if pm:
        pm.cleanup_all()
        print(f"[CLEANUP] All workers terminated", file=sys.stderr)

    print(f"\n[DONE] Parallel ralph loop completed after {elapsed_hours:.1f}h")
    print(f"[OUTPUT] Check {OUTPUT_DIR} for raw files and {SCRIPT_DIR} for aggregated")


def run_fast_loop(rounds_per_cycle: int = 2):
    """Run 2-3 rounds every 10 min indefinitely (kompress-v8 optimized)."""
    setup_dirs()
    cycle = 0
    CYCLE_INTERVAL = 600  # 10 minutes in seconds

    print(f"[RALPH-FAST] Starting fast loop: {rounds_per_cycle} rounds × 10min")

    # Initialize resource manager (if available)
    rm = None
    pm = None
    if ResourceManager:
        rm = ResourceManager(max_memory_percent=50, max_cpu_percent=75)
        pm = ProcessManager(rm)
        print(f"[RESOURCE] Manager enabled (memory: <40%, CPU: <60%)", file=sys.stderr)

    # Start kompress scheduler in background thread
    kompress_thread = threading.Thread(target=run_kompress_scheduler, daemon=True)
    kompress_thread.start()
    print(f"[KOMPRESS] Scheduler started (every 5min)", file=sys.stderr)

    MIN_WORKERS = 2
    MAX_WORKERS = 8
    PARALLEL_WORKERS = 3

    while True:
        cycle += 1
        cycle_start = time.time()
        print(f"\n[CYCLE-{cycle}] Starting at {datetime.now().strftime('%H:%M:%S')} (Workers: {PARALLEL_WORKERS})")

        # Dynamic Scaling
        if rm:
            status = rm.get_status()
            if status['memory_percent'] < 40 and status['cpu_percent'] < 60:
                if PARALLEL_WORKERS < MAX_WORKERS:
                    PARALLEL_WORKERS += 1
                    print(f"[AUTOSCALER] Scaling UP: {PARALLEL_WORKERS} workers", file=sys.stderr)
            elif status['memory_percent'] > 65 or status['cpu_percent'] > 85:
                if PARALLEL_WORKERS > MIN_WORKERS:
                    PARALLEL_WORKERS -= 1
                    print(f"[AUTOSCALER] Scaling DOWN: {PARALLEL_WORKERS} workers", file=sys.stderr)

        for round_num in range(1, rounds_per_cycle + 1):
            round_start = time.time()
            print(f"  [ROUND-{round_num}/{rounds_per_cycle}]")

            # Check resources before launching
            if rm and not rm.wait_for_resources(max_wait=60):
                print(f"[⚠] Resources unavailable, skipping round", file=sys.stderr)
                continue

            # Launch workers
            processes = []
            for i, config in enumerate(WORKERS_CONFIG[:PARALLEL_WORKERS]):
                # Stagger launches to avoid resource spike
                if rm and not rm.is_system_healthy():
                    time.sleep(3)  # Back off if system under load
                proc = launch_worker(i, config)
                processes.append(proc)
                time.sleep(0.5)

            # Wait for completion with 60s timeout
            round_start_time = time.time()
            while any(p.poll() is None for p in processes):
                if time.time() - round_start_time > ROUND_TIMEOUT:
                    print(f"[TIMEOUT] Round exceeded {ROUND_TIMEOUT}s, killing stragglers", file=sys.stderr)
                    for p in processes:
                        if p.poll() is None:
                            try:
                                p.terminate()
                                p.wait(timeout=2)
                            except:
                                p.kill()
                    break
                if rm:
                    status = rm.get_status()
                    if status.get("memory_percent", 0) > 50:
                        print(f"[MONITOR] Memory: {status['memory_percent']:.1f}%", file=sys.stderr)
                time.sleep(2)

            # Cleanup
            if pm:
                stats = pm.get_process_stats()
                print(f"[STATS] Workers: {stats['alive']} alive, {stats['dead']} dead, {stats['memory_mb']:.0f}MB", file=sys.stderr)

            round_elapsed = time.time() - round_start
            print(f"    ✓ Round completed in {round_elapsed:.0f}s")

        # Aggregate results
        try:
            aggregate_results()
        except Exception as e:
            print(f"[⚠] Aggregation failed: {e}")

        cycle_elapsed = time.time() - cycle_start
        remaining = CYCLE_INTERVAL - cycle_elapsed
        if remaining > 0:
            print(f"[SLEEP] {remaining:.0f}s until next cycle")
            time.sleep(remaining)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parallel ralph loop orchestrator")
    parser.add_argument("--mode", choices=["standard", "fast"], default="standard", help="Loop mode: standard (24h) or fast (10min cycles)")
    parser.add_argument("--duration", type=float, default=24, help="Duration in hours (default: 24, ignored in fast mode)")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers (default: 4 with kompress)")
    parser.add_argument("--pairs-per-worker", type=int, default=100, help="Pairs per iteration per worker")
    parser.add_argument("--rounds", type=int, default=2, help="Rounds per 10min cycle in fast mode (default: 2)")
    args = parser.parse_args()

    PARALLEL_WORKERS = min(args.workers, len(WORKERS_CONFIG))
    PAIRS_PER_WORKER = args.pairs_per_worker

    try:
        if args.mode == "fast":
            run_fast_loop(args.rounds)
        else:
            run_parallel_loop(args.duration)
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Ralph loop stopped by user")
        sys.exit(0)
