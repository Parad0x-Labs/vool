"""An MCP server inherits nothing it was not offered.

`core/mcp_client.py` built its child environment as `{**os.environ, **self.env}` — the entire
environment, including every exported API key and token — and handed it to a subprocess the
operator configured but did not write. This machine holds live keypairs.

The fix is not new: `core/plugin_executor.build_child_env` already implements the allowlist for
plugins, and its module docstring names `core/mcp_client.py:120-122` as the defect it exists to
correct. An MCP server is the same trust class, so it now uses the same helper.

A server still receives every variable its own config sets (`env=`), and may opt into named host
variables (`env_allowlist=`). What it no longer gets is the ambient environment.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
from typing import Any

from core.mcp_client import MCPStdioClient


class _FakeProc:
    """Enough of Popen for `start()` to proceed to the point we care about."""

    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("")
        self.returncode = None

    def poll(self):
        return None

    def kill(self):
        return None


def _env_passed_to_popen(client: MCPStdioClient, monkeypatch) -> dict[str, str]:
    """Drive the REAL `start()` and capture the environment it hands the subprocess.

    Asserting on `build_child_env` directly would be vacuous: it would keep passing while
    `start()` went back to `{**os.environ, ...}`. Verified by sabotage — the first version of
    this file did exactly that and did NOT fail when the leak was restored.
    """
    captured: dict[str, Any] = {}
    real_popen = subprocess.Popen

    def _fake_popen(argv, **kwargs):
        # The child now starts under kernel confinement, which probes the backend first with a
        # no-op (`... -- true`). That probe is a real subprocess; only the SERVER spawn is the
        # call whose environment this helper exists to capture.
        if list(argv) and str(list(argv)[-1]) == "true":
            return real_popen(argv, **kwargs)
        captured["env"] = kwargs.get("env")
        captured["argv"] = list(argv)
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    # `start()` performs a handshake after spawning; we only need the spawn.
    with contextlib.suppress(Exception):
        client.start()
    assert "env" in captured, "start() never spawned a subprocess"
    return dict(captured["env"] or {})


def test_an_ambient_secret_is_never_handed_to_a_server(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-live-should-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "should-not-leak-either")

    child = _env_passed_to_popen(MCPStdioClient("echo", env={"SERVER_OWN": "1"}, timeout=0.1), monkeypatch)

    assert "OPENROUTER_API_KEY" not in child
    assert "AWS_SECRET_ACCESS_KEY" not in child
    assert not any(key.endswith("_API_KEY") for key in child), sorted(child)


def test_a_server_still_receives_its_own_configured_variables(monkeypatch) -> None:
    """The fix must not break servers that legitimately need configuration."""
    child = _env_passed_to_popen(MCPStdioClient("echo", env={"MY_SERVER_TOKEN": "abc", "MODE": "fast"}, timeout=0.1), monkeypatch)

    assert child["MY_SERVER_TOKEN"] == "abc"
    assert child["MODE"] == "fast"


def test_a_host_variable_reaches_the_server_only_when_named(monkeypatch) -> None:
    monkeypatch.setenv("SHARED_ENDPOINT", "https://example.invalid")

    withheld = _env_passed_to_popen(MCPStdioClient("echo", env={}, timeout=0.1), monkeypatch)
    assert "SHARED_ENDPOINT" not in withheld

    offered = _env_passed_to_popen(MCPStdioClient("echo", env={}, env_allowlist=("SHARED_ENDPOINT",)), monkeypatch)
    assert offered["SHARED_ENDPOINT"] == "https://example.invalid"


def test_the_child_can_still_start_a_process(monkeypatch) -> None:
    """Withholding everything would be safe and useless; PATH must survive."""
    child = _env_passed_to_popen(MCPStdioClient("echo", timeout=0.1), monkeypatch)
    assert child.get("PATH"), "a child with no PATH cannot exec anything"


def test_the_client_defaults_to_an_empty_allowlist() -> None:
    """Default-deny: inheritance is opt-in, per server, by name."""
    assert MCPStdioClient("echo").env_allowlist == ()
