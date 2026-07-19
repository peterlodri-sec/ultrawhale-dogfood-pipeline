# SPDX-License-Identifier: MIT
"""Tests for the tailnet-only status monitor."""

import subprocess

import pytest

from ultrawhale.tailnet_status import resolve_bind_host


def test_resolve_bind_host_uses_env_override(monkeypatch):
    monkeypatch.setenv("ULTRAWHALE_TAILNET_STATUS_HOST", "100.64.0.42")

    assert resolve_bind_host() == "100.64.0.42"


def test_resolve_bind_host_uses_first_tailscale_ipv4(monkeypatch):
    monkeypatch.delenv("ULTRAWHALE_TAILNET_STATUS_HOST", raising=False)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="100.64.0.42\nfd7a:115c:a1e0::1\n", stderr="")

    assert resolve_bind_host(run_command=fake_run) == "100.64.0.42"


def test_resolve_bind_host_rejects_wildcard_override(monkeypatch):
    monkeypatch.setenv("ULTRAWHALE_TAILNET_STATUS_HOST", "0.0.0.0")

    with pytest.raises(ValueError, match="tailnet-only"):
        resolve_bind_host()
