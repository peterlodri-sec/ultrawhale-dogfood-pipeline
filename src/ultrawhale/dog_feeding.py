#!/usr/bin/env python3
"""
Dog Feeding Pipeline for Raspberry Pi
Controls motor and servo for scheduled dog feeding.
"""

import time
import logging
import json
import os
import subprocess
from datetime import datetime
from typing import Dict, Any

# Try to import RPi.GPIO, fallback to mock if not available
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    # Mock GPIO for testing
    class MockGPIO:
        BCM = "BCM"
        OUT = "OUT"
        HIGH = "HIGH"
        LOW = "LOW"
        
        def setmode(self, mode):
            pass
            
        def setup(self, pin, mode):
            pass
            
        def output(self, pin, value):
            pass
            
        def cleanup(self):
            pass
    
    GPIO = MockGPIO()
    GPIO_AVAILABLE = False

# HuggingFace imports
try:
    from huggingface_hub import HfApi, HfFileSystem
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False
    print("HuggingFace client not available")

# Configuration
CONFIG_PATH = "/boot/dog_feeding.conf"
LOG_FILE = "/var/log/dog_feeding.log"

class DogFeedingPipeline:
    def __init__(self):
        self.config = self._load_config()
        self.logger = self._setup_logger()
        self.motor_pin = int(self.config.get("motor_pin", 18))
        self.servo_pin = int(self.config.get("servo_pin", 12))
        self.schedule_interval = int(self.config.get("schedule_interval_minutes", 30)) * 60
        
        # Verify dataset integrity
        if not self._verify_dataset():
            self.logger.error("Dataset verification failed. Aborting.")
            raise RuntimeError("Dataset integrity check failed")
        
        # Initialize HF if available
        self.hf_api = None
        if HF_AVAILABLE:
            self._init_hf()
        
        if GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.motor_pin, GPIO.OUT)
            GPIO.setup(self.servo_pin, GPIO.OUT)
            
    def _init_hf(self):
        """Initialize HuggingFace API client."""
        try:
            # Try to get token from environment
            hf_token = os.environ.get("HF_TOKEN")
            if not hf_token:
                self.logger.warning("HF_TOKEN not found in environment")
                return
                
            self.hf_api = HfApi(token=hf_token)
            self.logger.info("HuggingFace API initialized")
        except Exception as e:
            self.logger.error(f"HuggingFace initialization failed: {e}")
    
    def _verify_dataset(self) -> bool:
        """Verify dataset integrity using embedded signature."""
        self.logger.info("Verifying dataset integrity...")
        
        try:
            # Run verification script
            result = subprocess.run(
                ["/boot/verify_dataset.sh"],
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode == 0:
                self.logger.info("Dataset verified successfully")
                return True
            else:
                self.logger.error(f"Dataset verification failed: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Dataset verification error: {e}")
            return False
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file."""
        default_config = {
            "device_id": "dogfeeder-001",
            "motor_pin": 18,
            "servo_pin": 12,
            "schedule_interval_minutes": 30,
            "log_level": "INFO"
        }
        
        if not os.path.exists(CONFIG_PATH):
            self.logger.warning(f"Config file {CONFIG_PATH} not found, using defaults")
            return default_config
            
        try:
            with open(CONFIG_PATH, 'r') as f:
                config = json.load(f)
                # Merge with defaults
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                return config
        except Exception as e:
            self.logger.error(f"Failed to load config: {e}")
            return default_config
    
    def _setup_logger(self) -> logging.Logger:
        """Setup logger."""
        logger = logging.getLogger("DogFeeding")
        logger.setLevel(getattr(logging, self.config.get("log_level", "INFO")))
        
        # Create logs directory if it doesn't exist
        os.makedirs("/var/log", exist_ok=True)
        
        handler = logging.FileHandler(LOG_FILE)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        return logger
    
    def _upload_to_hf(self, event_data: dict):
        """Upload event data to HuggingFace dataset."""
        if not HF_AVAILABLE or not self.hf_api:
            self.logger.debug("HuggingFace not available, skipping upload")
            return

        try:
            device_id = self.config.get("device_id", "dogfeeder-001")
            timestamp = datetime.now().isoformat()
            safe_ts = timestamp.replace(":", "-").replace(".", "-")

            # Build JSONL record
            record = {
                "timestamp": timestamp,
                "event_type": event_data.get("type", "unknown"),
                "device_id": device_id,
                "details": event_data
            }

            # Upload to HF dataset's telemetry/ directory
            repo_id = "PeetPedro/ultrawhale-dogfood"
            path = f"telemetry/pi_{device_id}_{safe_ts}.jsonl"
            content = json.dumps(record) + "\n"

            self.hf_api.upload_file(
                path_or_fileobj=content.encode(),
                path_in_repo=path,
                repo_id=repo_id,
                repo_type="dataset"
            )

            self.logger.info(f"HuggingFace upload successful: {path}")

        except Exception as e:
            self.logger.error(f"HuggingFace upload failed: {e}")
    
    def feed_dog(self) -> bool:
        """Execute one feeding cycle."""
        self.logger.info("Starting feeding cycle")
        
        # Prepare event data
        event_data = {
            "type": "feeding_cycle",
            "motor_pin": self.motor_pin,
            "servo_pin": self.servo_pin
        }
        
        try:
            # Turn on motor for 2 seconds
            if GPIO_AVAILABLE:
                GPIO.output(self.motor_pin, GPIO.HIGH)
                time.sleep(2)
                GPIO.output(self.motor_pin, GPIO.LOW)
                
                # Move servo to position
                # This is a simplified servo control
                # In reality, you'd use PWM for precise control
                GPIO.output(self.servo_pin, GPIO.HIGH)
                time.sleep(0.5)
                GPIO.output(self.servo_pin, GPIO.LOW)
            
            self.logger.info("Feeding cycle completed successfully")
            event_data["status"] = "success"
            
            # Upload to HuggingFace
            self._upload_to_hf(event_data)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Feeding cycle failed: {e}")
            event_data["status"] = "failed"
            event_data["error"] = str(e)
            
            # Upload error event
            self._upload_to_hf(event_data)
            return False
    
    def run_scheduler(self):
        """Run the feeding scheduler."""
        self.logger.info("Starting feeding scheduler")
        
        while True:
            try:
                self.logger.info(f"Waiting {self.schedule_interval} seconds for next feeding")
                time.sleep(self.schedule_interval)
                self.feed_dog()
            except KeyboardInterrupt:
                self.logger.info("Scheduler stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Scheduler error: {e}")
                time.sleep(60)  # Wait 1 minute before retry
    
    def cleanup(self):
        """Cleanup GPIO resources."""
        if GPIO_AVAILABLE:
            GPIO.cleanup()

def main():
    """Main entry point."""
    try:
        pipeline = DogFeedingPipeline()
        pipeline.run_scheduler()
    except RuntimeError as e:
        print(f"Fatal error: {e}")
        exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        exit(1)

if __name__ == "__main__":
    main()