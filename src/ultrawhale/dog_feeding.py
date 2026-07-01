#!/usr/bin/env python3
"""
Dog Feeding Pipeline — Pi feeding + generic dataset loop with background HF upload.

== Architecture Overview ==

Two modes, one codebase:
  • Pi mode   — RPi.GPIO available → feed motor+servo + generate datasets
  • Generic   — no Pi hardware    → generate datasets only (test/dev on Mac/Linux)

Lifecycle per cycle (configurable interval, default 30 min):
  1. Feed (Pi only)       → actuate GPIO motor+servo
  2. Generate dataset     → LLM via HF Inference API or OpenRouter fallback
  3. Save locally          → .dogfeeding/datasets/ or /var/lib/dogfeeding/datasets/
  4. Background uploader   → scans telemetry/ + datasets/ on startup, uploads to HF

== Data Flow ==

  Start
    │
    ├─ _load_config()          ← config file or env defaults
    ├─ _setup_logger()         ← file + console (stderr on generic mode)
    ├─ _verify_dataset()       ← Pi only: /boot/verify_dataset.sh (timeout 30s)
    ├─ _init_hf()              ← HF_TOKEN for upload + inference; OPENROUTER_API_KEY for fallback
    ├─ _scan_pending_uploads() ← spawn daemon thread for any backlog files
    │
    └─ run_scheduler() loop
         ├─ feed_dog()               ← GPIO actuation (Pi) / noop (generic)
         ├─ _generate_dataset_batch() ← LLM call with OpenRouter fallback
         ├─ save_dataset()            ← write .jsonl to datasets/ dir
         └─ sleep(interval)           ← configurable, default 30 min

  Upload thread (daemon):
    ┌─ _upload_worker()
    │  foreach pending file:
    │    ├─ read content
    │    ├─ upload to HuggingFace dataset (PeetPedro/ultrawhale-dogfood)
    │    │  • telemetry/  ← feeding events
    │    │  • datasets/   ← generated Q&A batches
    │    └─ delete local file on success
    └─ exit (restarts on next boot if files remain)

== Failure Modes ==

  • No network            → files stay in local dirs, uploaded on next boot
  • HF_TOKEN missing      → upload + inference disabled, OpenRouter tried
  • Both inference down   → empty batch, logged, cycle continues
  • GPIO error            → event uploaded as "failed", cycle continues
  • Config corrupt        → defaults loaded, logged, continues
  • Subprocess hang       → timeout at 30s, verify skipped

== Environment Variables ==

  HF_TOKEN              — HuggingFace token (upload + inference)
  OPENROUTER_API_KEY    — OpenRouter key (inference fallback)
"""

# ── Standard Library ──────────────────────────────────────────────────────────
import glob
import json
import logging
import os
import random
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

# ── Type hints (modern: use | instead of Optional, list instead of List) ─────
from typing import Any

# ==============================================================================
# HARDWARE ABSTRACTION
# ==============================================================================
# RPi.GPIO is only available on Raspberry Pi OS. On Mac/Linux we inject a no-op
# mock so the same code runs everywhere without ImportError.
# ==============================================================================

try:
    import RPi.GPIO as GPIO

    IS_PI: bool = True
except ImportError:
    # ── Mock GPIO for non-Pi environments ─────────────────────────────────
    # All methods are no-ops. The feed_dog() method checks IS_PI before
    # calling GPIO methods, so the mock is only a safety net.
    # ──────────────────────────────────────────────────────────────────────

    class _MockGPIO:
        BCM = "BCM"
        OUT = "OUT"
        HIGH = "HIGH"
        LOW = "LOW"

        @staticmethod
        def setmode(_mode: str) -> None:
            pass

        @staticmethod
        def setup(_pin: int, _mode: str) -> None:
            pass

        @staticmethod
        def output(_pin: int, _value: str) -> None:
            pass

        @staticmethod
        def cleanup() -> None:
            pass

    GPIO = _MockGPIO()
    IS_PI = False


# ==============================================================================
# HUGGINGFACE / OPENROUTER CLIENTS
# ==============================================================================
# We use huggingface_hub for both upload (HfApi) and inference (InferenceClient).
# OpenRouter is used via the openai-compatible SDK as a free fallback when HF
# Inference API is unavailable or rate-limited.
#
# Both imports are guarded so the script works even if the deps aren't installed.
# When neither is available, the pipeline degrades gracefully:
#   - feat: feeding still works (Pi) / dataset gen returns empty
#   - upload: saved locally for next boot
# ==============================================================================

try:
    from huggingface_hub import HfApi, InferenceClient

    HF_AVAILABLE: bool = True
except ImportError:
    HF_AVAILABLE = False

try:
    import openai

    OPENROUTER_AVAILABLE: bool = True
except ImportError:
    OPENROUTER_AVAILABLE = False


# ==============================================================================
# PATHS
# ==============================================================================
# On Pi:  /var/lib/dogfeeding/  (persistent across boots)
# On Mac: .dogfeeding/          (relative to cwd, easy to inspect)
#
# Why .dogfeeding and not a shared /tmp? Because the upload thread backs up
# files for retry — they must survive reboots and process restarts.
# ==============================================================================

_DATA_ROOT: str = "/var/lib/dogfeeding" if IS_PI else os.path.join(os.getcwd(), ".dogfeeding")

CONFIG_PATH: str = "/boot/dog_feeding.conf" if IS_PI else os.path.join(_DATA_ROOT, "dog_feeding.conf")

LOG_FILE: str = "/var/log/dog_feeding.log" if IS_PI else os.path.join(_DATA_ROOT, "dog_feeding.log")

LOCAL_TELEMETRY_DIR: str = os.path.join(_DATA_ROOT, "telemetry")
LOCAL_DATASET_DIR: str = os.path.join(_DATA_ROOT, "datasets")

# Maximum size of a pending file to upload (10 MB).
# Files larger than this are skipped and logged — they indicate something went
# wrong (runaway writer or corrupted output).
_MAX_UPLOAD_FILE_BYTES: int = 10 * 1024 * 1024


# ==============================================================================
# DEFAULTS
# ==============================================================================
# Fallback configuration when no config file exists. Every key here is also
# the canonical set — missing keys in user configs are filled from this dict.
# ==============================================================================

_DEFAULT_CONFIG: dict[str, Any] = {
    "device_id": "dogfeeder-001",
    "motor_pin": 18,
    "servo_pin": 12,
    "schedule_interval_minutes": 30,
    "log_level": "INFO",
    "hf_repo_id": "PeetPedro/ultrawhale-dogfood",
}


# ==============================================================================
# CLASS: DogFeedingPipeline
# ==============================================================================


class DogFeedingPipeline:
    """Orchestrates feeding + dataset generation + background upload.

    Usage:
        pipeline = DogFeedingPipeline()
        pipeline.run_scheduler()
        # Ctrl+C to stop
    """

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def __init__(self) -> None:
        # NOTE: order is deliberate. _load_config must come before _setup_logger
        # because the logger needs the configured log_level. We use print() for
        # any errors during config load since logger isn't ready yet.
        # ────────────────────────────────────────────────────────────────────────
        self.config: dict[str, Any] = self._load_config()
        self.logger: logging.Logger = self._setup_logger()

        # Extract configured values with safe fallbacks
        self.device_id: str = str(self.config.get("device_id", "dogfeeder-001"))
        raw_motor: Any = self.config.get("motor_pin", 18)
        self.motor_pin: int = int(raw_motor) if isinstance(raw_motor, (int, float, str)) else 18
        raw_servo: Any = self.config.get("servo_pin", 12)
        self.servo_pin: int = int(raw_servo) if isinstance(raw_servo, (int, float, str)) else 12
        raw_interval: Any = self.config.get("schedule_interval_minutes", 30)
        self.schedule_interval: int = (int(raw_interval) if isinstance(raw_interval, (int, float, str)) else 30) * 60
        self.hf_repo_id: str = str(self.config.get("hf_repo_id", "PeetPedro/ultrawhale-dogfood"))

        # ── Dataset integrity check (Pi only) ──────────────────────────────
        # The verify_dataset.sh script hashes the config content (excluding the
        # signature line) and compares against the embedded SHA256. If the SD
        # card has been tampered with, we refuse to start.
        # ────────────────────────────────────────────────────────────────────
        if IS_PI:
            if not self._verify_dataset():
                self.logger.critical("Dataset verification FAILED — halting")
                raise RuntimeError("Dataset integrity check failed — possible tampering")
            self.logger.info("Dataset integrity verified OK")

        # ── Clients (lazy inits) ───────────────────────────────────────────
        self.hf_api: HfApi | None = None
        self.hf_inference: InferenceClient | None = None
        self.openrouter: openai.OpenAI | None = None

        if HF_AVAILABLE:
            self._init_hf()
            # Scan for pending files from previous runs and spawn uploader
            self._scan_pending_uploads()

        # ── GPIO setup (Pi only) ───────────────────────────────────────────
        if IS_PI:
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(self.motor_pin, GPIO.OUT)
                GPIO.setup(self.servo_pin, GPIO.OUT)
                self.logger.info("GPIO initialized: motor=%s, servo=%s", self.motor_pin, self.servo_pin)
            except Exception as exc:
                self.logger.error("GPIO setup failed: %s", exc)
                raise

        # ── Signal handlers for clean shutdown ─────────────────────────────
        # Without these, Ctrl+C or SIGTERM leave GPIO pins in undefined state.
        # ────────────────────────────────────────────────────────────────────
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self.logger.info(
            "Pipeline ready — mode=%s interval=%ss device=%s",
            "Pi" if IS_PI else "generic",
            self.schedule_interval,
            self.device_id,
        )

    # ── Configuration ──────────────────────────────────────────────────────────

    def _load_config(self) -> dict[str, Any]:
        """Load user config from file, merging with _DEFAULT_CONFIG.

        DEVNOTE: We use print() here because the logger is not yet initialized.
        This is a deliberate ordering constraint — see __init__ comment.

        Returns:
            dict with all keys from _DEFAULT_CONFIG guaranteed present.
        """
        config = dict(_DEFAULT_CONFIG)  # copy — we mutate below

        if not os.path.exists(CONFIG_PATH):
            print(f"Config file not found at {CONFIG_PATH}, using defaults")
            return config

        try:
            raw: bytes = Path(CONFIG_PATH).read_bytes()
            # Guard against corrupt or empty config files
            if not raw.strip():
                print(f"Config file at {CONFIG_PATH} is empty, using defaults")
                return config

            user_cfg: dict[str, Any] = json.loads(raw.decode("utf-8"))
            if not isinstance(user_cfg, dict):
                print(f"Config file at {CONFIG_PATH} is not a JSON object, using defaults")
                return config

            config.update(user_cfg)
            return config

        except json.JSONDecodeError as exc:
            print(f"Config file at {CONFIG_PATH} is invalid JSON: {exc}. Using defaults.")
            return config
        except OSError as exc:
            print(f"Cannot read config file at {CONFIG_PATH}: {exc}. Using defaults.")
            return config

    # ── Logger ─────────────────────────────────────────────────────────────────

    def _setup_logger(self) -> logging.Logger:
        """Create a logger that writes to both a file and stderr.

        File handler:  rotates by restart (append mode), at LOG_FILE.
        Console handler: stderr, only in generic mode (Pi has syslog).

        The format includes timestamp, level, and message. The logger name
        is "DogFeeding" for easy filtering in log aggregation.
        """
        log_dir: str = os.path.dirname(LOG_FILE)
        os.makedirs(log_dir, exist_ok=True)

        logger_instance = logging.getLogger("DogFeeding")
        level_name: str = str(self.config.get("log_level", "INFO")).upper()
        logger_instance.setLevel(getattr(logging, level_name, logging.INFO))

        # Prevent duplicate handlers if _setup_logger is called multiple times
        if logger_instance.handlers:
            return logger_instance

        # -- File handler -------------------------------------------------------
        file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        logger_instance.addHandler(file_handler)

        # -- Console handler (generic mode only) --------------------------------
        # On Pi, logs go to the file above (or syslog via systemd). In generic
        # mode we also print to stderr so the user can see progress in the
        # terminal without tailing the file.
        # ────────────────────────────────────────────────────────────────────────
        if not IS_PI:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
            logger_instance.addHandler(console_handler)

        return logger_instance

    # ── HuggingFace / OpenRouter initialization ─────────────────────────────────

    def _init_hf(self) -> None:
        """Initialize HuggingFace API and Inference clients, plus OpenRouter.

        HF_TOKEN:
          Used for both HfApi (file upload) and InferenceClient (LLM calls).
          If absent, both upload and inference are disabled but the pipeline
          continues — datasets are saved locally for later upload.

        OPENROUTER_API_KEY:
          Used for openai-compatible inference fallback. If HF Inference API
          fails (rate limit, model down, etc.), we fall through to OpenRouter.
        """
        # ── HuggingFace ────────────────────────────────────────────────────────
        hf_token: str | None = os.environ.get("HF_TOKEN")
        if hf_token:
            try:
                self.hf_api = HfApi(token=hf_token)
                self.hf_inference = InferenceClient(token=hf_token)
                self.logger.info("HuggingFace HfApi + InferenceClient initialized")
            except Exception as exc:
                self.logger.error("HuggingFace init failed: %s", exc)
                self.hf_api = None
                self.hf_inference = None
        else:
            self.logger.warning("HF_TOKEN not set — upload + HF inference disabled")

        # ── OpenRouter fallback ────────────────────────────────────────────────
        or_key: str | None = os.environ.get("OPENROUTER_API_KEY")
        if or_key and OPENROUTER_AVAILABLE:
            try:
                self.openrouter = openai.OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=or_key,
                )
                self.logger.info("OpenRouter client ready")
            except Exception as exc:
                self.logger.error("OpenRouter init failed: %s", exc)
                self.openrouter = None
        else:
            self.logger.info("OpenRouter not configured (set OPENROUTER_API_KEY for inference fallback)")

    # ── Pending upload scanning ─────────────────────────────────────────────────

    def _scan_pending_uploads(self) -> None:
        """Scan local telemetry and dataset directories for pending files.

        If any are found, spawns a daemon thread to upload them to HuggingFace.
        The thread is daemon so it won't block shutdown.

        This is called once on startup. For repeated scanning, see the
        scheduler loop in run_scheduler().
        """
        os.makedirs(LOCAL_TELEMETRY_DIR, exist_ok=True)
        os.makedirs(LOCAL_DATASET_DIR, exist_ok=True)

        pending: list[str] = []
        pending.extend(glob.glob(os.path.join(LOCAL_TELEMETRY_DIR, "*.jsonl")))
        pending.extend(glob.glob(os.path.join(LOCAL_DATASET_DIR, "*.jsonl")))
        pending.extend(glob.glob(os.path.join(LOCAL_DATASET_DIR, "*.json")))

        if not pending:
            self.logger.info("No pending uploads found — both directories empty")
            return

        self.logger.info(
            "Found %d pending file(s) — spawning background upload thread",
            len(pending),
        )
        uploader = threading.Thread(
            target=self._upload_worker,
            args=(pending,),
            daemon=True,
            name="hf-uploader",
        )
        uploader.start()

    # ── Background upload worker ───────────────────────────────────────────────

    def _upload_worker(self, files: list[str]) -> None:
        """Daemon thread: upload each pending file to HuggingFace, delete on success.

        Strategy:
          1. Sort files for deterministic order
          2. Read file content
          3. Route to telemetry/ or datasets/ in HF repo based on source dir
          4. Upload with error handling
          5. Delete local file only on successful upload
          6. On failure, log and leave file for next boot

        Why not retry here?
          If upload fails (network, auth, HF down), retrying in a daemon thread
          is wasteful. The file will be picked up on the next start. For truly
          critical data, add a periodic scan in the main loop.

        File size guard:
          Files larger than _MAX_UPLOAD_FILE_BYTES (10 MB) are skipped with a
          warning. They indicate a runaway writer or corrupted output.
        """
        if not self.hf_api:
            self.logger.warning("Upload worker: hf_api not available — cannot upload")
            return

        for filepath in sorted(files):
            basename: str = os.path.basename(filepath)
            try:
                # ── Size guard ─────────────────────────────────────────────
                file_size: int = os.path.getsize(filepath)
                if file_size > _MAX_UPLOAD_FILE_BYTES:
                    self.logger.warning(
                        "Skipping oversized file %s (%d bytes > %d max)",
                        basename,
                        file_size,
                        _MAX_UPLOAD_FILE_BYTES,
                    )
                    continue

                # ── Read content ───────────────────────────────────────────
                content: str = Path(filepath).read_text(encoding="utf-8")
                if not content.strip():
                    self.logger.warning("Skipping empty file: %s", basename)
                    continue

                # ── Determine target path in HF repo ───────────────────────
                abs_dir: str = os.path.dirname(os.path.abspath(filepath)).rstrip("/")
                ts_safe: str = datetime.now(UTC).isoformat().replace(":", "-").replace(".", "-")

                if abs_dir == os.path.abspath(LOCAL_DATASET_DIR).rstrip("/"):
                    target: str = f"datasets/{self.device_id}_{ts_safe}_{basename}"
                else:
                    target = f"telemetry/{self.device_id}_{ts_safe}_{basename}"

                # ── Upload ─────────────────────────────────────────────────
                self.hf_api.upload_file(
                    path_or_fileobj=content.encode("utf-8"),
                    path_in_repo=target,
                    repo_id=self.hf_repo_id,
                    repo_type="dataset",
                )

                # ── Delete only after successful upload ─────────────────────
                os.remove(filepath)
                self.logger.info("Uploaded + removed: %s → %s", basename, target)

            except OSError as exc:
                self.logger.error("File error for %s: %s", basename, exc)
            except Exception as exc:
                self.logger.error("Upload failed for %s: %s", basename, exc)

        self.logger.info("Upload worker finished — %d files processed", len(files))

    # ── Dataset verification (Pi only) ─────────────────────────────────────────

    def _verify_dataset(self) -> bool:
        """Run the boot-partition verification script with a 30-second timeout.

        The script (verify_dataset.sh on the boot partition) recalculates the
        SHA256 of the config content (excluding the signature line) and compares
        it against the embedded dataset_signature field.

        This protects against:
          • SD card tampering between deployments
          • Corrupted filesystem writes
          • Accidental manual edits to /boot/dog_feeding.conf

        Returns:
            True if the script exits 0, False otherwise.
        """
        try:
            result: subprocess.CompletedProcess = subprocess.run(
                ["/boot/verify_dataset.sh"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                self.logger.info("Dataset integrity: PASS")
                return True

            self.logger.error(
                "Dataset integrity: FAIL (stderr=%s)",
                result.stderr.strip(),
            )
            return False

        except FileNotFoundError:
            self.logger.warning("verify_dataset.sh not found — skipping verification")
            return True  # don't halt on missing script (dev environment)
        except subprocess.TimeoutExpired:
            self.logger.error("Dataset verification timed out after 30s")
            return False
        except PermissionError:
            self.logger.error("verify_dataset.sh exists but is not executable")
            return False
        except Exception as exc:
            self.logger.error("Dataset verification error: %s", exc)
            return False

    # ── Event upload helpers ────────────────────────────────────────────────────

    def _upload_to_hf(self, event_data: dict[str, Any]) -> None:
        """Try to upload a feeding event to HuggingFace; fall back to local storage.

        The event is formatted as a JSONL record with a consistent schema:
          {timestamp, event_type, device_id, details}

        If the upload fails for any reason, we save the event locally in the
        telemetry directory. The startup scanner will pick it up on next boot.

        Args:
            event_data: Dict with at minimum a "type" key. Other keys are
                       nested under "details" in the output record.
        """
        if not self.hf_api:
            self._save_local_event(event_data)
            return

        dev: str = self.device_id
        now: datetime = datetime.now(UTC)
        ts_safe: str = now.isoformat().replace(":", "-").replace(".", "-")

        # Build a consistent record
        record: dict[str, Any] = {
            "timestamp": now.isoformat(),
            "event_type": event_data.get("type", "unknown"),
            "device_id": dev,
            "details": {k: v for k, v in event_data.items() if k != "type"},
        }
        payload: str = json.dumps(record, ensure_ascii=False) + "\n"
        remote_path: str = f"telemetry/{dev}_{ts_safe}.jsonl"

        try:
            self.hf_api.upload_file(
                path_or_fileobj=payload.encode("utf-8"),
                path_in_repo=remote_path,
                repo_id=self.hf_repo_id,
                repo_type="dataset",
            )
            self.logger.info("Event uploaded: %s", remote_path)

        except Exception as exc:
            self.logger.warning("Upload failed (%s) — saving locally", exc)
            self._save_local_event(event_data)

    def _save_local_event(self, event_data: dict[str, Any]) -> None:
        """Persist an event locally for later background upload.

        Schema is identical to _upload_to_hf so the upload worker can read
        these files without transformation.

        Args:
            event_data: Dict with minimum "type" key.
        """
        os.makedirs(LOCAL_TELEMETRY_DIR, exist_ok=True)

        dev: str = self.device_id
        now: datetime = datetime.now(UTC)
        ts_safe: str = now.isoformat().replace(":", "-").replace(".", "-")

        record: dict[str, Any] = {
            "timestamp": now.isoformat(),
            "event_type": event_data.get("type", "unknown"),
            "device_id": dev,
            "details": {k: v for k, v in event_data.items() if k != "type"},
        }

        filepath: str = os.path.join(
            LOCAL_TELEMETRY_DIR,
            f"event_{dev}_{ts_safe}.jsonl",
        )

        try:
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.logger.info("Event saved locally: %s", filepath)
        except OSError as exc:
            self.logger.error("Failed to save local event: %s", exc)

    # ── Dataset persistence ─────────────────────────────────────────────────────

    def save_dataset(
        self,
        records: list[dict[str, Any]],
        name: str | None = None,
    ) -> str | None:
        """Save a generated dataset batch to the local datasets directory.

        Each record is written as a separate JSONL line (one JSON object per
        line). This format is compatible with HuggingFace datasets and the
        background upload worker.

        Args:
            records: List of dicts representing Q&A pairs or other dataset items.
            name: Optional batch label (default: "dataset").

        Returns:
            Absolute path to the saved file, or None on failure.
        """
        if not records:
            self.logger.warning("save_dataset called with empty records — no-op")
            return None

        os.makedirs(LOCAL_DATASET_DIR, exist_ok=True)

        dev: str = self.device_id
        ts_safe: str = datetime.now(UTC).isoformat().replace(":", "-").replace(".", "-")
        label: str = name or "dataset"
        filepath: str = os.path.join(
            LOCAL_DATASET_DIR,
            f"{label}_{dev}_{ts_safe}.jsonl",
        )

        try:
            with open(filepath, "w", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.logger.info(
                "Dataset saved: %s (%d records, %d bytes)",
                filepath,
                len(records),
                os.path.getsize(filepath),
            )
            return filepath
        except OSError as exc:
            self.logger.error("Failed to save dataset: %s", exc)
            return None

    # ── Feeding (Pi only) ───────────────────────────────────────────────────────

    def feed_dog(self) -> bool:
        """Execute one feeding cycle.

        On Pi:
          1. Set motor pin HIGH for 2 seconds
          2. Set motor pin LOW
          3. Set servo pin HIGH for 0.5 seconds
          4. Set servo pin LOW

        On generic mode:
          This is a no-op (no GPIO hardware). The method still logs and
          reports success — useful for testing the event pipeline.

        In both cases, the event (success or failure) is uploaded to
        HuggingFace or saved locally.

        Returns:
            True if feeding succeeded, False on any error.
        """
        event: dict[str, Any] = {
            "type": "feeding_cycle",
            "motor_pin": self.motor_pin,
            "servo_pin": self.servo_pin,
        }

        try:
            if IS_PI:
                # ── Motor on ───────────────────────────────────────────────
                GPIO.output(self.motor_pin, GPIO.HIGH)
                time.sleep(2)
                GPIO.output(self.motor_pin, GPIO.LOW)

                # ── Servo actuation ────────────────────────────────────────
                # This is a simplified digital pulse. For precise servo control
                # (angle, position), use GPIO.PWM instead.
                GPIO.output(self.servo_pin, GPIO.HIGH)
                time.sleep(0.5)
                GPIO.output(self.servo_pin, GPIO.LOW)

                self.logger.info(
                    "Feeding cycle OK — motor=%d servo=%d",
                    self.motor_pin,
                    self.servo_pin,
                )
            else:
                self.logger.info("Feeding cycle OK (generic mode — no GPIO)")

            event["status"] = "success"
            self._upload_to_hf(event)
            return True

        except Exception as exc:
            self.logger.error("Feeding cycle FAILED: %s", exc)
            event["status"] = "failed"
            event["error"] = str(exc)
            self._upload_to_hf(event)
            return False

    # ── Dataset batch generation via LLM ────────────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> list[dict[str, Any]] | None:
        """Parse a JSON array from an LLM response, handling markdown fences.

        LLMs often wrap JSON in ```json ... ``` fences. This method strips
        those before parsing. If parsing fails, returns None (caller should
        log and fall through).

        Args:
            text: Raw LLM response text.

        Returns:
            Parsed list of dicts, or None if parsing failed.
        """
        cleaned: str = text.strip()

        # Strip leading ```json or ```  (any language tag)
        if cleaned.startswith("```"):
            first_newline: int = cleaned.find("\n")
            cleaned = cleaned[first_newline + 1 :] if first_newline != -1 and first_newline < 50 else cleaned[3:]
            cleaned = cleaned.strip()

        # Strip trailing ```
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

        # Try direct parse
        try:
            parsed: Any = json.loads(cleaned)
            if isinstance(parsed, list):
                return parsed
            # If it's a dict with a single key containing a list, unwrap it
            if isinstance(parsed, dict):
                for val in parsed.values():
                    if isinstance(val, list):
                        return val
        except json.JSONDecodeError:
            pass

        # Final attempt: find the first [ and last ] and try that substring
        start: int = cleaned.find("[")
        end: int = cleaned.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                extracted: Any = json.loads(cleaned[start : end + 1])
                if isinstance(extracted, list):
                    return extracted
            except json.JSONDecodeError:
                pass

        return None

    def _generate_dataset_batch(self) -> list[dict[str, Any]]:
        """Generate Q&A pairs by calling an LLM.

        Strategy:
          1. Pick a random topic from a curated list
          2. Select a random batch size (3–6 pairs)
          3. Construct a prompt asking for JSON output
          4. Try HuggingFace Inference API (free, uses HF_TOKEN)
          5. Fallback to OpenRouter (free tier, needs OPENROUTER_API_KEY)
          6. If both fail, return empty list and log

        The prompt explicitly requests JSON with no markdown wrapping, but
        _extract_json handles it anyway because many models ignore that.

        Returns:
            List of dicts with keys: question, answer, difficulty.
            Empty list if all backends failed.
        """
        topics: list[str] = [
            "physics",
            "math",
            "computer science",
            "biology",
            "chemistry",
            "history",
            "philosophy",
            "engineering",
            "linguistics",
            "economics",
            "astronomy",
            "neuroscience",
        ]
        topic: str = random.choice(topics)
        n_pairs: int = random.randint(3, 6)

        system_prompt: str = (
            "You are a precise data generation assistant. You always respond with valid JSON and nothing else."
        )
        user_prompt: str = (
            f"Generate {n_pairs} diverse question-answer pairs about {topic}. "
            "Return ONLY a valid JSON array, no markdown, no explanation. "
            'Each item must be: {{"question": "...", "answer": "...", '
            '"difficulty": "easy|medium|hard"}}. '
            "Questions should probe real understanding, not trivia. "
            "Answers should be 1-3 sentences, educational, and factually correct."
        )

        # ── First choice: HuggingFace Inference API ────────────────────────
        if self.hf_inference:
            try:
                self.logger.debug("Calling HF Inference: topic=%s n=%d", topic, n_pairs)
                hf_response = self.hf_inference.chat_completion(
                    model="Qwen/Qwen2.5-1.5B-Instruct",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=2048,
                    temperature=0.8,
                )
                content: str | None = hf_response.choices[0].message.content
                if content:
                    parsed: list[dict[str, Any]] | None = self._extract_json(content)
                    if parsed:
                        self.logger.info("HF Inference: %d pairs on '%s'", len(parsed), topic)
                        return parsed
                    self.logger.warning("HF Inference returned unparseable content: %.100s...", content)
                else:
                    self.logger.warning("HF Inference returned empty content")

            except Exception as exc:
                self.logger.warning("HF Inference failed: %s — trying OpenRouter", exc)
        else:
            self.logger.debug("HF Inference not available — skipping")

        # ── Fallback: OpenRouter ───────────────────────────────────────────
        if self.openrouter:
            try:
                self.logger.debug("Calling OpenRouter: topic=%s n=%d", topic, n_pairs)
                or_response = self.openrouter.chat.completions.create(
                    model="meta-llama/llama-3.2-3b-instruct:free",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=2048,
                    temperature=0.8,
                )
                content = or_response.choices[0].message.content
                if content:
                    parsed = self._extract_json(content)
                    if parsed:
                        self.logger.info("OpenRouter: %d pairs on '%s'", len(parsed), topic)
                        return parsed
                    self.logger.warning("OpenRouter returned unparseable content: %.100s...", content)
                else:
                    self.logger.warning("OpenRouter returned empty content")

            except Exception as exc:
                self.logger.warning("OpenRouter failed: %s", exc)
        else:
            self.logger.debug("OpenRouter not available — skipping")

        self.logger.warning("All inference backends failed — no dataset this cycle")
        return []

    # ── Main scheduler loop ─────────────────────────────────────────────────────

    def run_scheduler(self) -> None:
        """Main infinite loop.

        Each iteration:
          1. Feed dog (Pi only — motor + servo)
          2. Generate a dataset batch via LLM
          3. Save dataset locally (background uploader picks it up next start)
          4. Sleep for configured interval

        The loop catches KeyboardInterrupt for clean shutdown and all other
        exceptions with a 60-second backoff to avoid tight crash loops.

        Heartbeat:
          Every 10 iterations we log a heartbeat to confirm the loop is alive,
          even if no events have happened.
        """
        self.logger.info(
            "Scheduler started — interval=%ds mode=%s",
            self.schedule_interval,
            "Pi" if IS_PI else "generic",
        )

        iteration: int = 0

        while True:
            iteration += 1
            try:
                # ── Feed (Pi only) ─────────────────────────────────────────
                if IS_PI:
                    self.feed_dog()
                else:
                    self.logger.debug("Skipping feed (generic mode, iteration %d)", iteration)

                # ── Generate dataset batch ─────────────────────────────────
                batch: list[dict[str, Any]] = self._generate_dataset_batch()
                if batch:
                    self.save_dataset(batch, "north")
                else:
                    self.logger.info("No dataset generated this cycle")

                # ── Heartbeat every 10 iterations ──────────────────────────
                if iteration % 10 == 0:
                    self.logger.info("Heartbeat: %d iterations completed", iteration)

                # ── Sleep ──────────────────────────────────────────────────
                self.logger.debug("Cycle complete — sleeping %d seconds", self.schedule_interval)
                time.sleep(self.schedule_interval)

            except KeyboardInterrupt:
                self.logger.info("Scheduler stopped by user (Ctrl+C)")
                break
            except Exception as exc:
                self.logger.error("Scheduler error at iteration %d: %s", iteration, exc)
                time.sleep(60)  # avoid tight crash loop

        self.cleanup()

    # ── Signal handler ─────────────────────────────────────────────────────────

    def _handle_signal(self, signum: int, _frame: object) -> None:
        """Handle SIGINT/SIGTERM by raising KeyboardInterrupt for clean shutdown."""
        sig_name: str = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        self.logger.info("Received %s — shutting down", sig_name)
        raise KeyboardInterrupt()

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Release GPIO resources and flush logs."""
        if IS_PI:
            try:
                GPIO.cleanup()
                self.logger.info("GPIO cleaned up")
            except Exception as exc:
                self.logger.error("GPIO cleanup failed: %s", exc)

        # Ensure all log handlers flush
        for handler in self.logger.handlers[:]:
            handler.flush()
            handler.close()


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================


def main() -> None:
    """Application entry point.

    Creates the pipeline, runs the scheduler indefinitely, and handles
    top-level exceptions with clean messages and exit codes.
    """
    pipeline: DogFeedingPipeline | None = None
    try:
        pipeline = DogFeedingPipeline()
        pipeline.run_scheduler()
    except RuntimeError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nShutdown complete.", file=sys.stderr)
        sys.exit(0)
    except Exception as exc:
        print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
        if pipeline:
            pipeline.cleanup()
        sys.exit(1)


if __name__ == "__main__":
    main()
