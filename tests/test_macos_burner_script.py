# SPDX-License-Identifier: MIT
"""Safety contract tests for the macOS SD-card burner script."""

import subprocess
from pathlib import Path

SCRIPT = Path("scripts/burn-sd-macos.sh")


def test_macos_burner_script_exists_is_executable_and_parses():
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111

    result = subprocess.run(["bash", "-n", str(SCRIPT)], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_macos_burner_requires_safe_confirmed_raw_disk_flow():
    source = SCRIPT.read_text()

    required_fragments = [
        "diskutil list external physical",
        "Removable Media:",
        "diskutil unmountDisk",
        "/dev/r${SD_DEVICE#/dev/}",
        "sudo dd",
        "conv=sync",
        "sync",
        "diskutil eject",
        "--dry-run",
        "CONFIRM",
        "BURN ${SD_DEVICE}",
        "Refusing to write",
    ]

    for fragment in required_fragments:
        assert fragment in source


def test_macos_burner_can_fetch_latest_rpi_lite_and_apply_pi_tweaks():
    source = SCRIPT.read_text()

    required_fragments = [
        "--rpi-lite-latest",
        "os_list_imagingutility_v4.json",
        "Raspberry Pi OS Lite (64-bit)",
        "extract_sha256",
        "--pi-ethernet-tweaks",
        "dtoverlay=disable-wifi",
        "dtoverlay=disable-bt",
        "touch \"$BOOT_MOUNT/ssh\"",
        "user-data",
        "network-config",
        "match:",
        "name: eth*",
    ]

    for fragment in required_fragments:
        assert fragment in source
