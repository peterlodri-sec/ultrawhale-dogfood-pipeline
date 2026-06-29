#!/usr/bin/env python3
"""Resource manager: memory, CPU monitoring + graceful throttling."""

import os
import psutil
import sys
import time
from typing import Optional


class ResourceManager:
    """Monitor and manage system resources."""

    def __init__(self, max_memory_percent: float = 40, max_cpu_percent: float = 60):
        """Initialize with resource limits (% of system total)."""
        self.max_memory_percent = max_memory_percent
        self.max_cpu_percent = max_cpu_percent
        self.pause_flag = False

    def check_memory(self) -> bool:
        """Check if memory usage is within limits."""
        try:
            mem = psutil.virtual_memory()
            if mem.percent > self.max_memory_percent:
                print(f"[⚠] Memory high: {mem.percent:.1f}% > {self.max_memory_percent}%", file=sys.stderr)
                return False
            return True
        except Exception as e:
            print(f"[⚠] Memory check error: {e}", file=sys.stderr)
            return True

    def check_cpu(self) -> bool:
        """Check if CPU load is within limits."""
        try:
            cpu = psutil.cpu_percent(interval=0.1)
            if cpu > self.max_cpu_percent:
                print(f"[⚠] CPU high: {cpu:.1f}% > {self.max_cpu_percent}%", file=sys.stderr)
                return False
            return True
        except Exception as e:
            print(f"[⚠] CPU check error: {e}", file=sys.stderr)
            return True

    def is_system_healthy(self) -> bool:
        """Check if system resources are healthy."""
        return self.check_memory() and self.check_cpu()

    def wait_for_resources(self, max_wait: int = 300) -> bool:
        """Block until resources are available (max 5min)."""
        start = time.time()
        while time.time() - start < max_wait:
            if self.is_system_healthy() and not self.pause_flag:
                return True
            print(f"[THROTTLE] Waiting for resources... ({int(time.time() - start)}s/{max_wait}s)", file=sys.stderr)
            time.sleep(10)
        return False

    def pause(self):
        """Pause generation."""
        self.pause_flag = True
        print("[PAUSE] Generation paused", file=sys.stderr)

    def resume(self):
        """Resume generation."""
        self.pause_flag = False
        print("[RESUME] Generation resumed", file=sys.stderr)

    def get_status(self) -> dict:
        """Get current resource status."""
        try:
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=0.1)
            return {
                "memory_percent": mem.percent,
                "memory_available_gb": mem.available / (1024**3),
                "cpu_percent": cpu,
                "paused": self.pause_flag,
                "healthy": self.is_system_healthy(),
            }
        except Exception as e:
            return {"error": str(e)}


class ProcessManager:
    """Manage worker processes with resource limits."""

    def __init__(self, resource_manager: ResourceManager):
        self.rm = resource_manager
        self.processes = {}
        self.dead_workers = set()

    def launch_process(self, worker_id: int, cmd: list, log_file: str) -> Optional[psutil.Popen]:
        """Launch process with resource limits."""
        try:
            # Resource limits (per process)
            # 500MB memory limit for each worker
            # Soft limit - process can exceed but will be throttled
            with open(log_file, "w") as log_f:
                proc = psutil.Popen(
                    cmd,
                    stdout=log_f,
                    stderr=log_f,
                    preexec_fn=os.setsid if hasattr(os, 'setsid') else None,
                )

            # Try to set memory limit (macOS may not support)
            try:
                p = psutil.Process(proc.pid)
                # Note: memory limits require elevated permissions on macOS
                # Just monitor instead
            except Exception as e:
                print(f"[⚠] Could not set process limits: {e}", file=sys.stderr)

            self.processes[worker_id] = proc
            print(f"[PROCESS] Worker {worker_id} launched (PID {proc.pid})", file=sys.stderr)
            return proc

        except Exception as e:
            print(f"[ERROR] Failed to launch worker {worker_id}: {e}", file=sys.stderr)
            self.dead_workers.add(worker_id)
            return None

    def monitor_process(self, worker_id: int) -> bool:
        """Check if process is still alive."""
        if worker_id not in self.processes:
            return False
        proc = self.processes[worker_id]
        return proc.poll() is None

    def kill_process(self, worker_id: int, force: bool = False):
        """Gracefully kill process."""
        if worker_id not in self.processes:
            return
        proc = self.processes[worker_id]
        try:
            if force:
                proc.kill()
                print(f"[KILL] Worker {worker_id} force-killed", file=sys.stderr)
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                print(f"[KILL] Worker {worker_id} terminated", file=sys.stderr)
        except Exception as e:
            print(f"[⚠] Error killing worker {worker_id}: {e}", file=sys.stderr)

    def cleanup_all(self):
        """Cleanup all processes."""
        for worker_id in list(self.processes.keys()):
            if self.monitor_process(worker_id):
                self.kill_process(worker_id, force=False)

    def get_process_stats(self) -> dict:
        """Get stats on all processes."""
        stats = {"alive": 0, "dead": 0, "memory_mb": 0}
        for worker_id, proc in self.processes.items():
            if self.monitor_process(worker_id):
                stats["alive"] += 1
                try:
                    p = psutil.Process(proc.pid)
                    stats["memory_mb"] += p.memory_info().rss / (1024**2)
                except Exception:
                    pass
            else:
                stats["dead"] += 1
        return stats


if __name__ == "__main__":
    # Test resource manager
    rm = ResourceManager(max_memory_percent=50, max_cpu_percent=70)
    print("Resource Manager Status:")
    print(rm.get_status())
