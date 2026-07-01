#!/usr/bin/env python3
"""
Dog Feeding Pipeline — Pi feeding + generic dataset loop.
Same bg upload pattern on any machine.
"""

import time
import logging
import json
import os
import subprocess
import threading
import glob
import random
from datetime import datetime
from typing import Dict, Any, List, Optional

# ── GPIO: real on Pi, mock elsewhere ──────────────────────────────────
try:
    import RPi.GPIO as GPIO
    IS_PI = True
except ImportError:
    class MockGPIO:
        BCM = "BCM"; OUT = "OUT"; HIGH = "HIGH"; LOW = "LOW"
        def setmode(self, _): pass
        def setup(self, _, __): pass
        def output(self, _, __): pass
        def cleanup(self): pass
    GPIO = MockGPIO()
    IS_PI = False

# ── HuggingFace ────────────────────────────────────────────────────────
try:
    from huggingface_hub import HfApi
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False
    print("huggingface_hub not installed — upload disabled")

# ── Paths ──────────────────────────────────────────────────────────────
DATA_ROOT = "/var/lib/dogfeeding" if IS_PI else os.path.join(os.getcwd(), ".dogfeeding")
CONFIG_PATH = "/boot/dog_feeding.conf" if IS_PI else os.path.join(DATA_ROOT, "dog_feeding.conf")
LOG_FILE = "/var/log/dog_feeding.log" if IS_PI else os.path.join(DATA_ROOT, "dog_feeding.log")
LOCAL_TELEMETRY_DIR = os.path.join(DATA_ROOT, "telemetry")
LOCAL_DATASET_DIR = os.path.join(DATA_ROOT, "datasets")


class DogFeedingPipeline:
    def __init__(self):
        self.config = self._load_config()
        self.logger = self._setup_logger()
        self.motor_pin = int(self.config.get("motor_pin", 18))
        self.servo_pin = int(self.config.get("servo_pin", 12))
        self.schedule_interval = int(self.config.get("schedule_interval_minutes", 30)) * 60

        # Dataset integrity verify (Pi only)
        if IS_PI and not self._verify_dataset():
            self.logger.error("Dataset verification failed. Aborting.")
            raise RuntimeError("Dataset integrity check failed")

        # HF init + bg upload scan
        self.hf_api: Optional[HfApi] = None
        if HF_AVAILABLE:
            self._init_hf()
            self._scan_pending_uploads()

        if IS_PI:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.motor_pin, GPIO.OUT)
            GPIO.setup(self.servo_pin, GPIO.OUT)

        self.logger.info(f"Pipeline initialized — mode: {'Pi' if IS_PI else 'generic'}")

    # ── HF / Upload ─────────────────────────────────────────────────────

    def _init_hf(self):
        token = os.environ.get("HF_TOKEN")
        if not token:
            self.logger.warning("HF_TOKEN not set — upload disabled")
            return
        try:
            self.hf_api = HfApi(token=token)
            self.logger.info("HuggingFace API ready")
        except Exception as e:
            self.logger.error(f"HuggingFace init failed: {e}")

    def _scan_pending_uploads(self):
        """Scan telemetry + dataset dirs. Spawn bg uploader if files found."""
        os.makedirs(LOCAL_TELEMETRY_DIR, exist_ok=True)
        os.makedirs(LOCAL_DATASET_DIR, exist_ok=True)

        pending: List[str] = []
        pending += glob.glob(os.path.join(LOCAL_TELEMETRY_DIR, "*.jsonl"))
        pending += glob.glob(os.path.join(LOCAL_DATASET_DIR, "*.jsonl"))
        pending += glob.glob(os.path.join(LOCAL_DATASET_DIR, "*.json"))

        if not pending:
            self.logger.info("No pending files to upload")
            return

        self.logger.info(f"Found {len(pending)} pending file(s) — spawning upload thread")
        t = threading.Thread(target=self._upload_worker, args=(pending,), daemon=True, name="hf-uploader")
        t.start()

    def _upload_worker(self, files: List[str]):
        """Bg thread: upload each file to HF, delete on success."""
        for fp in sorted(files):
            try:
                with open(fp) as f:
                    content = f.read()
                fname = os.path.basename(fp)

                # Route to telemetry/ or datasets/ in HF
                root = os.path.dirname(os.path.abspath(fp)).rstrip("/")
                ts = datetime.now().isoformat().replace(":", "-").replace(".", "-")
                if root == os.path.abspath(LOCAL_DATASET_DIR).rstrip("/"):
                    target = f"datasets/pi_{ts}_{fname}"
                else:
                    target = f"telemetry/pi_{ts}_{fname}"

                self.hf_api.upload_file(
                    path_or_fileobj=content.encode(),
                    path_in_repo=target,
                    repo_id="PeetPedro/ultrawhale-dogfood",
                    repo_type="dataset",
                )
                os.remove(fp)
                self.logger.info(f"Uploaded + removed: {fname} → {target}")
            except Exception as e:
                self.logger.error(f"Upload failed for {fp}: {e}")

    # ── Dataset verify (Pi only) ────────────────────────────────────────

    def _verify_dataset(self) -> bool:
        try:
            r = subprocess.run(["/boot/verify_dataset.sh"], capture_output=True, text=True)
            if r.returncode == 0:
                self.logger.info("Dataset integrity OK")
                return True
            self.logger.error(f"Dataset verify failed: {r.stderr}")
            return False
        except Exception as e:
            self.logger.error(f"Dataset verify error: {e}")
            return False

    # ── Config / Logging ─────────────────────────────────────────────────

    def _load_config(self) -> Dict[str, Any]:
        defaults = dict(device_id="dogfeeder-001", motor_pin=18, servo_pin=12,
                        schedule_interval_minutes=30, log_level="INFO")
        if not os.path.exists(CONFIG_PATH):
            self.logger.warning("Config not found, using defaults")
            return defaults
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            defaults.update(cfg)
            return defaults
        except Exception as e:
            self.logger.error(f"Config load error: {e}")
            return defaults

    def _setup_logger(self) -> logging.Logger:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        log = logging.getLogger("DogFeeding")
        log.setLevel(getattr(logging, self.config.get("log_level", "INFO")))
        h = logging.FileHandler(LOG_FILE)
        h.setFormatter(logging.Formatter("%(asctime)s — %(name)s — %(levelname)s — %(message)s"))
        log.addHandler(h)
        return log

    # ── Upload helpers ──────────────────────────────────────────────────

    def _upload_to_hf(self, event_data: dict):
        """Try HF upload; fallback to local file for bg retry."""
        if not self.hf_api:
            self._save_local("event", event_data, LOCAL_TELEMETRY_DIR)
            return
        try:
            dev = self.config.get("device_id", "dogfeeder-001")
            ts = datetime.now().isoformat().replace(":", "-").replace(".", "-")
            rec = {"timestamp": datetime.now().isoformat(),
                   "event_type": event_data.get("type", "unknown"),
                   "device_id": dev, "details": event_data}
            content = json.dumps(rec) + "\n"
            path = f"telemetry/pi_{dev}_{ts}.jsonl"
            self.hf_api.upload_file(
                path_or_fileobj=content.encode(),
                path_in_repo=path,
                repo_id="PeetPedro/ultrawhale-dogfood",
                repo_type="dataset",
            )
            self.logger.info(f"HF upload OK: {path}")
        except Exception as e:
            self.logger.warning(f"HF upload failed ({e}), saving locally")
            self._save_local("event", event_data, LOCAL_TELEMETRY_DIR)

    def _save_local(self, prefix: str, data: dict, directory: str):
        os.makedirs(directory, exist_ok=True)
        ts = datetime.now().isoformat().replace(":", "-").replace(".", "-")
        dev = self.config.get("device_id", "dogfeeder-001")
        fp = os.path.join(directory, f"{prefix}_{dev}_{ts}.jsonl")
        with open(fp, "w") as f:
            f.write(json.dumps({"timestamp": datetime.now().isoformat(),
                                "device_id": dev, **data}) + "\n")
        self.logger.info(f"Saved locally: {fp}")

    def save_dataset(self, records: List[Dict], name: Optional[str] = None):
        """Save dataset locally for bg upload on next start."""
        os.makedirs(LOCAL_DATASET_DIR, exist_ok=True)
        ts = datetime.now().isoformat().replace(":", "-").replace(".", "-")
        dev = self.config.get("device_id", "dogfeeder-001")
        fp = os.path.join(LOCAL_DATASET_DIR, f"{name or 'dataset'}_{dev}_{ts}.jsonl")
        with open(fp, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        self.logger.info(f"Dataset saved ({len(records)} records): {fp}")
        return fp

    # ── Feeding (Pi) / Generic loop (non-Pi) ────────────────────────────

    def feed_dog(self) -> bool:
        """One feeding cycle (real GPIO on Pi, mock elsewhere)."""
        ev = {"type": "feeding_cycle", "motor_pin": self.motor_pin, "servo_pin": self.servo_pin}
        try:
            if IS_PI:
                GPIO.output(self.motor_pin, GPIO.HIGH); time.sleep(2)
                GPIO.output(self.motor_pin, GPIO.LOW)
                GPIO.output(self.servo_pin, GPIO.HIGH); time.sleep(0.5)
                GPIO.output(self.servo_pin, GPIO.LOW)
            ev["status"] = "success"
            self.logger.info("Feed OK")
            self._upload_to_hf(ev)
            return True
        except Exception as e:
            ev["status"] = "failed"; ev["error"] = str(e)
            self.logger.error(f"Feed failed: {e}")
            self._upload_to_hf(ev)
            return False

    def _generate_dataset_batch(self) -> List[Dict]:
        """Stub: generate or download a dataset batch. Replace with real logic."""
        topics = ["physics", "math", "cs", "biology"]
        return [
            {"question": f"Explain {random.choice(topics)} concept #{i}",
             "answer": f"This is a generated answer for concept #{i}.",
             "difficulty": random.choice(["easy", "medium", "hard"])}
            for i in range(random.randint(3, 8))
        ]

    def run_scheduler(self):
        """Main loop. On Pi: feed + generate datasets. Off Pi: generate datasets."""
        self.logger.info(f"Mode: {'Pi (feed + dataset gen)' if IS_PI else 'generic (dataset gen only)'}")

        while True:
            try:
                # ── Feed (Pi only) ──
                if IS_PI:
                    self.feed_dog()

                # ── Generate dataset batch (both Pi and generic) ──
                data = self._generate_dataset_batch()
                if data:
                    self.save_dataset(data, "north")

                self.logger.info(f"Next cycle in {self.schedule_interval}s")
                time.sleep(self.schedule_interval)

            except KeyboardInterrupt:
                break
            except Exception as e:
                self.logger.error(f"Loop error: {e}")
                time.sleep(60)

    def cleanup(self):
        if IS_PI:
            GPIO.cleanup()


def main():
    try:
        p = DogFeedingPipeline()
        p.run_scheduler()
    except RuntimeError as e:
        print(f"Fatal: {e}"); exit(1)
    except Exception as e:
        print(f"Error: {e}"); exit(1)


if __name__ == "__main__":
    main()
