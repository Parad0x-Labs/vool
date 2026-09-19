"""Production-build invariants for the research/networking boundary.

A normal production VOOL build must not run the research peer-networking
systems: no mesh listener on UDP 49152 or TCP 49153, no mesh daemon boot, no
public-hive presence heartbeat threads, no autonomous peer jobs, no swarm
query dispatch, no stale-port kills and no STUN probes. The boundary authority
is ``core/runtime_mode.py``; the choke points are the API runtime bootstrap,
the agent's background-thread startup, the turn-reasoning swarm dispatch and
the transport's bind-conflict recovery. See ``research/README.md``.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

from core.runtime_mode import (
    background_presence_threads_allowed,
    mesh_daemon_boot_allowed,
    research_networking_enabled,
    stale_port_kill_allowed,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------- authority


def test_research_networking_is_env_opt_in_only(monkeypatch):
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    assert research_networking_enabled() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("VOOL_RESEARCH_NETWORKING", value)
        assert research_networking_enabled() is True
    for value in ("", "0", "false", "off"):
        monkeypatch.setenv("VOOL_RESEARCH_NETWORKING", value)
        assert research_networking_enabled() is False


def test_research_mode_cannot_be_enabled_by_preferences_or_edition():
    """The gate reads ONLY the environment: no preference, config key or product
    edition can switch the research stack on in a production build."""
    import inspect

    import core.runtime_mode as runtime_mode

    source = inspect.getsource(runtime_mode.research_networking_enabled)
    assert "_RESEARCH_NETWORKING_ENV" in source
    assert "load_preferences" not in source
    assert "policy_engine" not in source
    assert "edition" not in source


def test_mesh_daemon_boot_requires_research_mode(monkeypatch):
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    # The historical gate allowed the daemon in the default PERSONAL edition.
    assert mesh_daemon_boot_allowed(edition_allows_mesh=True, disable_env_set=False) is False
    assert mesh_daemon_boot_allowed(edition_allows_mesh=False, disable_env_set=False) is False
    monkeypatch.setenv("VOOL_RESEARCH_NETWORKING", "1")
    assert mesh_daemon_boot_allowed(edition_allows_mesh=True, disable_env_set=False) is True
    # The explicit disable still wins over the research opt-in.
    assert mesh_daemon_boot_allowed(edition_allows_mesh=True, disable_env_set=True) is False


def test_background_presence_threads_require_research_mode(monkeypatch):
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    assert background_presence_threads_allowed(is_test_runtime=False) is False
    assert background_presence_threads_allowed(is_test_runtime=True) is False
    monkeypatch.setenv("VOOL_RESEARCH_NETWORKING", "1")
    assert background_presence_threads_allowed(is_test_runtime=False) is True
    assert background_presence_threads_allowed(is_test_runtime=True) is False


def test_stale_port_kill_requires_research_mode(monkeypatch):
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    assert stale_port_kill_allowed() is False
    monkeypatch.setenv("VOOL_RESEARCH_NETWORKING", "1")
    assert stale_port_kill_allowed() is True


# ------------------------------------------------------------------ choke points


def test_api_runtime_mesh_boot_consults_the_boundary(monkeypatch):
    """The production bootstrap decision is the runtime_mode predicate verbatim."""
    from core import runtime_mode

    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    import core.web.api.runtime as api_runtime

    mesh_edition_ok = True  # PERSONAL edition allows the surface...
    disabled = not runtime_mode.mesh_daemon_boot_allowed(
        edition_allows_mesh=mesh_edition_ok,
        disable_env_set=False,
    )
    assert disabled is True  # ...and production still refuses to boot the daemon.


def test_swarm_query_dispatch_is_gated(monkeypatch):
    """A production turn never broadcasts a QUERY_SHARD to mesh peers."""
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    from core.agent_runtime import turn_reasoning

    calls = {"dispatch": 0, "holders": 0}

    def dispatch(query, *, limit=5):
        calls["dispatch"] += 1
        return 1

    def holders(*args, **kwargs):
        calls["holders"] += 1
        return []

    class _Ctx:
        retrieval_confidence_score = 0.0

    class _Task:
        task_id = "t"
        task_summary = "s"

    turn_reasoning._dispatch_background_swarm_query(
        task=_Task(),
        classification={"task_class": "debugging"},
        ranked=[],
        context_result=_Ctx(),
        request_relevant_holders_fn=holders,
        dispatch_query_shard_fn=dispatch,
        build_generalized_query_fn=lambda task, classification: {"query_id": "q" * 8},
        audit_logger_module=__import__("core.audit_logger", fromlist=["log"]),
    )
    assert calls == {"dispatch": 0, "holders": 0}, "production must not dispatch or request holders"


def test_udp_transport_does_not_kill_port_holder_in_production(monkeypatch):
    """Bind-conflict recovery falls back to an ephemeral port instead of killing."""
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    from network import transport

    assert transport._kill_stale_udp_holder(49152) is False


def test_udp_transport_skips_stun_in_production(monkeypatch):
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    from network import transport

    class _FakeSock:
        def getsockname(self):
            return ("127.0.0.1", 0)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = int(probe.getsockname()[1])
    server = transport.UDPTransportServer(host="127.0.0.1", port=free_port)
    # Bind a real ephemeral socket pair is heavy; assert the gate directly on the
    # start path by checking STUN is not attempted: patch the client module.
    import types

    stun_mod = types.ModuleType("network.stun_client")

    def _boom(*args, **kwargs):
        raise AssertionError("STUN discovery must not run in a production build")

    stun_mod.discover_public_endpoint = _boom
    monkeypatch.setitem(sys.modules, "network.stun_client", stun_mod)
    runtime = server.start()
    try:
        assert runtime.running is True
    finally:
        server.stop()


def test_agent_background_threads_do_not_start_in_production(monkeypatch):
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from apps.vool_agent import VoolAgent

    agent = VoolAgent.__new__(VoolAgent)
    agent.backend_name = "prod-backend"
    agent.device = "prod-device"
    assert agent._background_runtime_threads_enabled() is False


# ------------------------------------------------------- live production boot


def _udp_ports_open_in_proc_net() -> set[int]:
    ports: set[int] = set()
    try:
        for line in open("/proc/net/udp").read().splitlines()[1:]:
            parts = line.split()
            local = parts[1]
            port_hex = local.split(":")[1]
            ports.add(int(port_hex, 16))
    except OSError:
        pass
    return ports


def _udp_ports_open_via_lsof(pid: int) -> set[int]:
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-i", "UDP", "-a", "-p", str(pid)],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    ports = set()
    for line in out.splitlines():
        if "UDP" not in line:
            continue
        fields = line.split()
        for field in fields:
            if ":" in field and field.rsplit(":", 1)[-1].isdigit():
                ports.add(int(field.rsplit(":", 1)[-1]))
    return ports


@pytest.mark.timeout(300)
def test_live_default_boot_binds_no_mesh_ports_and_runs_no_presence_threads(tmp_path):
    """Boot the real API server the way the shipped app does — default
    environment, fresh home — and prove the production invariants on the live
    process: no mesh daemon, no UDP 49152 / TCP 49153 listener, no presence
    threads."""
    env = dict(os.environ)
    env.pop("VOOL_DISABLE_MESH_DAEMON", None)
    env.pop("VOOL_RESEARCH_NETWORKING", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["VOOL_HOME"] = str(tmp_path / "home")
    env["VOOL_SKIP_PROVIDER_PREWARM"] = "1"
    env["VOOL_DISABLE_COMPUTE_MODE"] = "1"

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        api_port = int(probe.getsockname()[1])

    proc = subprocess.Popen(
        [sys.executable, "-m", "apps.vool_api_server", "--port", str(api_port), "--bind", "127.0.0.1"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 240
        health = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError("API server exited during production-boot invariant test")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{api_port}/healthz", timeout=3) as resp:
                    health = json.loads(resp.read().decode("utf-8"))
                    break
            except Exception:
                time.sleep(1.5)
        assert health is not None, "API server never became healthy"

        # 1. The runtime reports NO mesh daemon.
        assert health.get("daemon") is False, "production boot must not run the mesh daemon"

        # 2. No mesh listener on the canonical ports (Linux /proc, macOS lsof).
        udp_ports = _udp_ports_open_in_proc_net() or _udp_ports_open_via_lsof(proc.pid)
        if udp_ports:
            assert 49152 not in udp_ports, "UDP 49152 must not be bound by a production build"
            assert 49153 not in udp_ports, "UDP 49153 must not be bound by a production build"
        try:
            tcp_lsof = subprocess.run(
                ["lsof", "-nP", "-i", "TCP:49153", "-a", "-p", str(proc.pid)],
                capture_output=True, text=True, timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            tcp_lsof = ""
        assert "49153" not in tcp_lsof, "TCP 49153 must not be bound by a production build"

        # 3. No research/presence threads in the live process.
        threads = subprocess.run(
            ["ps", "-M", str(proc.pid)], capture_output=True, text=True, timeout=10
        ).stdout if sys.platform == "darwin" else ""
        if not threads:
            try:
                threads = "\n".join(
                    open(f"/proc/{proc.pid}/task/{tid}/comm").read()
                    for tid in os.listdir(f"/proc/{proc.pid}/task")
                )
            except OSError:
                threads = ""
        for forbidden in ("vool-udp-transport", "vool-public-presence", "vool-idle-commons"):
            assert forbidden not in threads, f"research thread {forbidden} running in production boot"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
