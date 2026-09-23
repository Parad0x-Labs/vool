"""An MCP server inherits nothing it was not offered.

`core/mcp_client.py` built its child environment as `{**os.environ, **self.env}` — the entire
environment, including every exported API key and token — and handed it to a subprocess the
operator configured but did not write. This machine holds live keypairs.

The fix is not new: `core/plugin_executor.build_child_env` already implements the allowlist for
plugins, and its module docstring names `core/mcp_client.py:120-122` as the defect it exists to
correct. An MCP server is the same trust class, so it now uses the same helper.

A server still receives every variable its own config sets (`env=`), and may opt into named host
variables (`env_allowlist=`). What it no longer gets is the ambient environment.

The capture helper here drove the REAL `start()` from the start, but it used to record
"whatever `Popen` call happened while patched, last write wins". `start()` waits out the
client's handshake timeout AFTER spawning, so the patch stayed up for that whole wait — 30s for
a client constructed without `timeout=`. Any OTHER thread's `Popen` in that window (a leaked
watchdog spawning with ambient inheritance passes no `env=`) replaced the captured environment
with `None` → `{}`, and the second capture of
`test_a_host_variable_reaches_the_server_only_when_named` answered `KeyError: 'SHARED_ENDPOINT'`
with a deterministic ~30.1s call duration — exactly CI runs 35849609728 / 35858135891
(shard 3, green in the baseline composition where no such spawner preceded it). The capture is
now keyed to the launch it exists to observe and the window is closed, so a concurrent spawn
can no longer impersonate the server launch or eat its environment.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import textwrap
import threading

import pytest

from core.mcp_client import MCPError, MCPStdioClient
from core.plugin_executor import kernel_confinement_available


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
    """Drive the REAL `start()` and capture the environment of THIS client's server launch.

    Asserting on `build_child_env` directly would be vacuous: it would keep passing while
    `start()` went back to `{**os.environ, ...}`. Verified by sabotage — the first version of
    this file did exactly that and did NOT fail when the leak was restored.

    A patched-in `Popen` is a process-global surface, and `start()` keeps running after the
    spawn (handshake wait), so the capture may not be "the last call I saw". A call is THE
    server launch only when its argv ends with this client's own `[command, *args]` and its
    environment carries `PLUGIN_SCRATCH` — the marker `build_child_env` sets on every MCP
    launch. Confinement probes (argv ending in `true`) run for real; any other concurrent
    spawn is recorded as a stray for diagnosis and is never mistaken for the launch.
    """
    launch_tail = [str(client.command), *[str(a) for a in client.args]]
    captured: dict[str, dict[str, str]] = {}
    stray: list[list[str]] = []
    real_popen = subprocess.Popen

    def _fake_popen(argv, **kwargs):
        argv_list = [str(a) for a in argv]
        # The child now starts under kernel confinement, which probes the backend first with a
        # no-op (`... -- true`). That probe is a real subprocess; only the SERVER spawn is the
        # call whose environment this helper exists to capture.
        if argv_list and argv_list[-1] == "true":
            return real_popen(argv, **kwargs)
        env = kwargs.get("env")
        if (
            "env" not in captured
            and argv_list[-len(launch_tail):] == launch_tail
            and isinstance(env, dict)
            and env.get("PLUGIN_SCRATCH")
        ):
            captured["env"] = dict(env)
        else:
            stray.append(argv_list)
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    # `start()` performs a handshake after spawning; we only need the spawn. Closing afterwards
    # keeps even a fake child from lingering on the client.
    try:
        with contextlib.suppress(Exception):
            client.start()
    finally:
        with contextlib.suppress(Exception):
            client.close()
    assert "env" in captured, f"start() never spawned this client's server (stray spawns: {stray})"
    return captured["env"]


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

    offered = _env_passed_to_popen(MCPStdioClient("echo", env={}, env_allowlist=("SHARED_ENDPOINT",), timeout=0.1), monkeypatch)
    assert offered["SHARED_ENDPOINT"] == "https://example.invalid"


def test_an_unnamed_host_variable_stays_absent_even_when_others_are_named(monkeypatch) -> None:
    monkeypatch.setenv("SHARED_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AMBIENT_SECRET_SYNTH", "must-stay-withheld")

    child = _env_passed_to_popen(
        MCPStdioClient("echo", env={}, env_allowlist=("SHARED_ENDPOINT",), timeout=0.1), monkeypatch
    )

    assert child["SHARED_ENDPOINT"] == "https://example.invalid"
    assert "AMBIENT_SECRET_SYNTH" not in child


def test_an_allowlisted_name_missing_from_the_host_is_simply_absent(monkeypatch) -> None:
    """A named-but-unset variable is not an error and not a phantom entry."""
    monkeypatch.delenv("VOOL_SYNTH_NOT_SET_ANYWHERE", raising=False)

    child = _env_passed_to_popen(
        MCPStdioClient("echo", env={}, env_allowlist=("VOOL_SYNTH_NOT_SET_ANYWHERE",), timeout=0.1), monkeypatch
    )

    assert "VOOL_SYNTH_NOT_SET_ANYWHERE" not in child


def test_a_configured_variable_outranks_the_same_named_host_variable(monkeypatch) -> None:
    """`env=` is the server's own config; the allowlist is a host grant. Config wins."""
    monkeypatch.setenv("SHARED_ENDPOINT", "https://host-grant.invalid")

    child = _env_passed_to_popen(
        MCPStdioClient(
            "echo",
            env={"SHARED_ENDPOINT": "https://configured.invalid"},
            env_allowlist=("SHARED_ENDPOINT",),
            timeout=0.1,
        ),
        monkeypatch,
    )

    assert child["SHARED_ENDPOINT"] == "https://configured.invalid"


def test_the_child_can_still_start_a_process(monkeypatch) -> None:
    """Withholding everything would be safe and useless; PATH must survive."""
    child = _env_passed_to_popen(MCPStdioClient("echo", timeout=0.1), monkeypatch)
    assert child.get("PATH"), "a child with no PATH cannot exec anything"


def test_the_client_defaults_to_an_empty_allowlist() -> None:
    """Default-deny: inheritance is opt-in, per server, by name."""
    assert MCPStdioClient("echo").env_allowlist == ()


def test_a_concurrent_spawn_cannot_impersonate_the_server_launch(monkeypatch) -> None:
    """The demonstrated CI failure mechanism, as a regression.

    Runs 35849609728 / 35858135891 (shard 3) failed with `KeyError: 'SHARED_ENDPOINT'` after a
    deterministic ~30.1s — the second client's DEFAULT handshake timeout. The capture used to
    stay patched for that whole wait and record the last `Popen` it saw, so a concurrent
    spawn with ambient inheritance (no `env=` kwarg) overwrote the server launch's
    environment with `{}`. Reproduced locally 2/2 by driving the real test under a synthetic
    watchdog spawning every 0.5s; this test keeps that watchdog running while asserting the
    capture still sees the launch's own environment.
    """
    monkeypatch.setenv("SHARED_ENDPOINT", "https://example.invalid")
    stop = threading.Event()
    thread = threading.Thread(target=_ambient_spawner, args=(stop,), daemon=True)
    thread.start()
    try:
        withheld = _env_passed_to_popen(MCPStdioClient("echo", env={}, timeout=0.1), monkeypatch)
        assert "SHARED_ENDPOINT" not in withheld

        offered = _env_passed_to_popen(
            MCPStdioClient("echo", env={}, env_allowlist=("SHARED_ENDPOINT",), timeout=0.1), monkeypatch
        )
    finally:
        stop.set()
        thread.join(timeout=5)

    assert offered["SHARED_ENDPOINT"] == "https://example.invalid"


def _ambient_spawner(stop: threading.Event) -> None:
    """A leaked-watchdog stand-in: spawns on a cadence with ambient inheritance.

    Under the helper's patch these never become real processes — the fake absorbs them — which
    is exactly the CI condition being pinned: they must not be captured either.
    """
    while not stop.is_set():
        with contextlib.suppress(Exception):
            subprocess.Popen(["true", "-n", "synthetic-watchdog"])  # deliberately not probe-shaped
        stop.wait(0.5)


def test_the_capture_still_detects_a_restored_ambient_leak(monkeypatch) -> None:
    """Sabotage: if `start()` ever hands the child the ambient environment again, the capture
    must SEE it. This is the teeth check for the capture repair — an identity-keyed capture
    that could no longer observe a leak would be a repair that proved nothing."""
    from core import plugin_executor

    monkeypatch.setenv("AMBIENT_SYNTH_SECRET", "leak-if-you-see-me")

    def _leaking_child_env(*, env_allowlist=(), secrets=None, scratch_dir=""):
        child = dict(os.environ)
        child.update({str(k): str(v) for k, v in (secrets or {}).items() if k and v})
        if scratch_dir:
            child["PLUGIN_SCRATCH"] = scratch_dir
        return child

    monkeypatch.setattr(plugin_executor, "build_child_env", _leaking_child_env)

    child = _env_passed_to_popen(MCPStdioClient("echo", env={}, timeout=0.1), monkeypatch)

    assert child["AMBIENT_SYNTH_SECRET"] == "leak-if-you-see-me", (
        "capture went blind: a restored ambient leak was not observed"
    )


def test_a_host_without_kernel_confinement_refuses_to_start_the_server(monkeypatch) -> None:
    """Confinement refusal must stay visible: an MCPError naming the refusal, no spawn."""
    from core import plugin_executor

    monkeypatch.setattr(plugin_executor, "_kernel_confinement_prefix", lambda *a, **k: None)

    client = MCPStdioClient("echo", timeout=0.1)
    with pytest.raises(MCPError, match="refusing to start unconfined"):
        client.start()
    assert client._proc is None


# ---------------------------------------------------------------------------
# The real spawn boundary
# ---------------------------------------------------------------------------

#: A harmless stand-in MCP server. It speaks just enough JSON-RPC over stdio for
#: `MCPStdioClient` to complete the handshake, and its single tool reports ONLY the
#: synthetic test variables named in REPORT_NAMES — never the environment at large.
_REPORTING_SERVER = textwrap.dedent(
    """
    import json, os, sys
    wanted = [name for name in os.environ.get("REPORT_NAMES", "").split(",") if name]
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("id") is None:
            continue  # notification
        method = msg.get("method")
        if method == "initialize":
            result = {"serverInfo": {"name": "env-probe"}, "capabilities": {}}
        elif method == "tools/list":
            result = {"tools": [{"name": "env_report", "description": "report synthetic values"}]}
        elif method == "tools/call":
            report = {name: os.environ.get(name) for name in wanted}
            result = {"content": [{"type": "text", "text": json.dumps(report)}], "isError": False}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """
)


@pytest.mark.skipif(
    not kernel_confinement_available(),
    reason="no kernel confinement backend on this host (sandbox-exec / bwrap): the real spawn "
    "boundary is only provable where confinement can actually confine",
)
class TestTheRealSpawnBoundary:
    """The captured-environment tests above prove what `start()` HANDS `Popen`. These drive a
    REAL confined child through the real MCP handshake and read back what it actually
    received — the full start → build_child_env → confinement → subprocess → child contract."""

    def test_the_server_receives_its_configured_and_allowlisted_variables_only(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("SHARED_HOST_VALUE", "https://synth.invalid")
        monkeypatch.setenv("AMBIENT_SECRET_SYNTH", "must-not-arrive")

        client = MCPStdioClient(
            sys.executable,
            ["-B", "-c", _REPORTING_SERVER],
            env={
                "SERVER_OWN_TOKEN": "synth-configured",
                "REPORT_NAMES": "SERVER_OWN_TOKEN,SHARED_HOST_VALUE,AMBIENT_SECRET_SYNTH",
            },
            env_allowlist=("SHARED_HOST_VALUE",),
            timeout=15.0,
            cwd=str(tmp_path),
        )
        with client:
            client.list_tools()
            report = json.loads(client.call_tool_text("env_report"))

        assert report["SERVER_OWN_TOKEN"] == "synth-configured"
        assert report["SHARED_HOST_VALUE"] == "https://synth.invalid"
        assert report["AMBIENT_SECRET_SYNTH"] is None, "an unnamed ambient variable reached the server"
        assert client.confinement.startswith("kernel:"), client.confinement

    def test_the_child_is_cleaned_up_on_close(self, tmp_path) -> None:
        client = MCPStdioClient(
            sys.executable,
            ["-B", "-c", _REPORTING_SERVER],
            env={"REPORT_NAMES": ""},
            timeout=15.0,
            cwd=str(tmp_path),
        )
        with client:
            client.list_tools()
            proc = client._proc
            assert proc is not None and proc.poll() is None
        assert proc.poll() is not None, "close() left the owned child running"
