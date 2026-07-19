# SPDX-License-Identifier: MIT
"""Tailnet-only status server for Raspberry Pi deployments."""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ultrawhale.config import Config

RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def resolve_bind_host(run_command: RunCommand = subprocess.run) -> str:
    """Resolve the tailnet-only bind host.

    ULTRAWHALE_TAILNET_STATUS_HOST can override discovery. Otherwise, use the
    first IPv4 address reported by `tailscale ip -4`.
    """
    configured = os.getenv("ULTRAWHALE_TAILNET_STATUS_HOST")
    if configured:
        return _validate_tailnet_bind_host(configured.strip())

    result = run_command(
        ["tailscale", "ip", "-4"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        candidate = line.strip()
        if candidate and "." in candidate:
            return _validate_tailnet_bind_host(candidate)

    raise RuntimeError("No Tailscale IPv4 address found. Start tailscaled or set ULTRAWHALE_TAILNET_STATUS_HOST.")


def collect_status(lines: int = 80) -> str:
    """Collect a compact status report for tailnet monitoring."""
    cfg = Config()
    now = datetime.now(UTC).isoformat()
    parts = [
        f"ultrawhale status @ {now}",
        f"host: {socket.gethostname()}",
        f"llm: {cfg.llm_host} model={cfg.llm_model}",
        f"hf_repo: {cfg.hf_repo}",
        f"log_dir: {cfg.log_dir}",
        f"output_dir: {cfg.output_dir}",
        "",
        "systemd:",
        _command_output(["systemctl", "is-active", "ultrawhale.service"], fallback="unknown"),
        "",
        "latest output files:",
        _latest_files(cfg.output_dir),
        "",
        f"journalctl -u ultrawhale.service -n {lines}:",
        _command_output(
            ["journalctl", "-u", "ultrawhale.service", "-n", str(lines), "--no-pager"], fallback="unavailable"
        ),
    ]
    return "\n".join(parts) + "\n"


def serve(host: str, port: int, lines: int) -> None:
    """Serve status over HTTP on the given tailnet bind host."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {"/", "/status", "/logs"}:
                self.send_error(404)
                return

            body = collect_status(lines).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"tailnet status listening on http://{host}:{port}/status", flush=True)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve Ultrawhale status on a Tailscale-only address")
    parser.add_argument("--host", help="Bind host. Defaults to ULTRAWHALE_TAILNET_STATUS_HOST or tailscale ip -4")
    parser.add_argument("--port", type=int, default=int(os.getenv("ULTRAWHALE_TAILNET_STATUS_PORT", "8765")))
    parser.add_argument("--lines", type=int, default=int(os.getenv("ULTRAWHALE_TAILNET_STATUS_LINES", "80")))
    args = parser.parse_args(argv)

    host = args.host or resolve_bind_host()
    serve(host, args.port, args.lines)
    return 0


def _command_output(command: list[str], fallback: str) -> str:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return fallback

    output = (result.stdout or result.stderr).strip()
    return output or fallback


def _validate_tailnet_bind_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host

    if address.is_unspecified:
        raise ValueError("Tailnet status must bind to a tailnet-only address, not a wildcard address.")
    return host


def _latest_files(path: Path, limit: int = 8) -> str:
    if not path.exists():
        return "none"

    files = sorted(path.glob("dogfeed_*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return "none"

    return "\n".join(f"{file.name}\t{file.stat().st_size} bytes" for file in files[:limit])


if __name__ == "__main__":
    raise SystemExit(main())
