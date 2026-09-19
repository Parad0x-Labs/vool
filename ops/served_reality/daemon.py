"""Daemon lifecycle for the served-reality bench.

Launches the STAGED app (never a working tree) as a subprocess with an
isolated VOOL_HOME, waits for /healthz to prove both liveness and the exact
served commit, and guarantees teardown of the whole process tree.

Truth rules:

* stdout/stderr go to files (never an undrained pipe that could block the
  daemon or lose evidence).
* healthz must report the EXACT staged commit and ``dirty: false``; a
  mismatch is a harness refusal, not a green boot.
* exit status of the daemon is recorded honestly; a daemon that died mid-run
  is a fact the case results carry (as VOOL-class failure of the run
  surface), never a silent skip.
* teardown terminates the full tree (SIGTERM -> bounded wait -> SIGKILL),
  because the product spawns children (tools, probes) that must not survive
  the bench.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ops.served_reality.classify import BenchError
from ops.served_reality.wire import WireClient, WireLog, free_port


def _child_pids(parent_pid: int) -> list[int]:
    """Direct+indirect children of pid via ps (portable, no psutil)."""
    out: list[int] = []
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid,ppid"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return out
        rows: dict[int, list[int]] = {}
        for line in proc.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    pid, ppid = int(parts[0]), int(parts[1])
                except ValueError:
                    continue
                rows.setdefault(ppid, []).append(pid)
        frontier = list(rows.get(parent_pid, []))
        while frontier:
            pid = frontier.pop()
            if pid in out:
                continue
            out.append(pid)
            frontier.extend(rows.get(pid, []))
    except Exception:
        pass
    return out


def _process_start_token(pid: int) -> str:
    """A process-identity token (start time) so a recycled PID is never
    mistaken for the process we meant to signal.

    macOS recycles PIDs aggressively under spawn churn: a replacement daemon
    can inherit the OLD daemon's PID while the old teardown's bounded wait is
    still running. Signaling "the pid" then would kill the NEW boot —
    measured live as `daemon died during boot (exit -15/-9)`.
    """
    try:
        proc = subprocess.run(
            ["ps", "-o", "lstart=,", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.stdout.strip()
    except Exception:
        return ""


def _signal_if_same_process(pid: int, token: str, sig: int) -> bool:
    """Signal pid only if it is still the same process we captured."""
    if _process_start_token(pid) != token or not token:
        return False
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False


def terminate_tree(pid: int, *, term_wait_s: float = 12.0) -> None:
    """SIGTERM the tree bottom-up, wait bounded, then SIGKILL the rest.

    Every signal is identity-checked against the process start token, so a
    recycled PID (a freshly booted replacement daemon) can never be killed
    by a stale teardown.
    """
    if pid <= 1:
        raise BenchError(f"refusing to terminate tree of pid {pid}")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    children = _child_pids(pid)
    targets: dict[int, str] = {pid: _process_start_token(pid)}
    for child in children:
        targets[child] = _process_start_token(child)
    for child in sorted(children, reverse=True):
        _signal_if_same_process(child, targets[child], signal.SIGTERM)
    _signal_if_same_process(pid, targets[pid], signal.SIGTERM)
    deadline = time.monotonic() + term_wait_s
    while time.monotonic() < deadline:
        alive = 0
        for target, token in targets.items():
            if _process_start_token(target) == token and token:
                try:
                    os.kill(target, 0)
                    alive += 1
                except ProcessLookupError:
                    pass
        if alive == 0:
            return
        time.sleep(0.2)
    for target, token in targets.items():
        _signal_if_same_process(target, token, signal.SIGKILL)


@dataclass
class DaemonHandle:
    pid: int
    port: int
    process: subprocess.Popen
    home: Path
    app_dir: Path
    stdout_path: Path
    stderr_path: Path
    launch_env: dict[str, str] = field(default_factory=dict)
    staged_sha: str = ""
    healthz_runtime: dict[str, Any] = field(default_factory=dict)
    _terminated: bool = False

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def terminate(self) -> int | None:
        """Idempotent full-tree teardown. Returns the daemon's exit code."""
        if self._terminated:
            return self.process.returncode
        self._terminated = True
        if self.process.poll() is None:
            terminate_tree(self.process.pid)
        try:
            return self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return self.process.returncode


def build_daemon_env(
    *,
    home: Path,
    workspace: Path,
    stub_port: int | None,
    stub_model: str,
    bundle_manifest: Path | None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """The deterministic, network-contained daemon environment.

    All model-lane-shaped traffic is pinned to the bench stub: the vLLM
    OpenAI-compatible lane, the Ollama-protocol seams (intent arbiter,
    resource governor, ps probe, OLLAMA_HOST consumers), so nothing can
    silently reach a real provider or the operator's own Ollama on 11434.
    """
    env = dict(os.environ)
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("TETHER_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("VOOL_REMOTE_API_KEY", None)
    env.update(
        {
            "VOOL_HOME": str(home),
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_SKIP_PROVIDER_PREWARM": "1",
            # The isolated home is the ONLY credential authority for a bench
            # daemon: without this, the product reads the operator's real
            # login-keychain OpenRouter key through credential_store and
            # auto-registers a PAID lane (measured live on 2026-09-02) —
            # breaking both determinism and the no-paid-lane contract.
            "VOOL_CREDENTIAL_STORE": "vault",
            # The mesh daemon binds a machine-fixed UDP port (49152) and its
            # duplicate/ownership logic will hard-kill a second instance:
            # measured live as bench daemons dying with SIGKILL ~4s after
            # boot whenever the operator's own daemon is running. A bench
            # daemon is a solo served surface — no swarm mesh.
            "VOOL_DISABLE_MESH_DAEMON": "1",
            # No Public Hive chatter from a bench daemon either.
            "VOOL_PUBLIC_HIVE_ENABLED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    if stub_port is not None:
        stub_base = f"http://127.0.0.1:{stub_port}"
        env.update(
            {
                "VLLM_BASE_URL": f"{stub_base}/v1",
                "VLLM_MODEL": stub_model,
                "VOOL_OLLAMA_CHAT_URL": f"{stub_base}/api/chat",
                "VOOL_OLLAMA_URL": stub_base,
                "VOOL_OLLAMA_PS_URL": f"{stub_base}/api/ps",
                "VOOL_RAW_OLLAMA_API_URL": stub_base,
                "VOOL_LOADED_OLLAMA_MODELS": stub_model,
                "OLLAMA_HOST": stub_base,
                # Network containment by default: every external fetch is a
                # deterministic recorded 502 from the stub's CONNECT gate;
                # loopback (the stub lanes) never transits the proxy. The
                # bench can therefore never spend money or mutate anything
                # over the network unless --live explicitly opts in.
                "HTTP_PROXY": stub_base,
                "HTTPS_PROXY": stub_base,
                "ALL_PROXY": stub_base,
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "no_proxy": "127.0.0.1,localhost,::1",
            }
        )
    if bundle_manifest is not None:
        env["VOOL_BUNDLE_MANIFEST"] = str(bundle_manifest)
    if extra_env:
        env.update(extra_env)
    return env


def launch_daemon(
    *,
    app_dir: Path,
    home: Path,
    workspace: Path,
    python_bin: str,
    run_dir: Path,
    wire_log: WireLog,
    stub_port: int | None = None,
    stub_model: str = "served-reality-stub",
    bundle_manifest: Path | None = None,
    expected_sha: str | None = None,
    extra_env: dict[str, str] | None = None,
    boot_timeout_s: float = 120.0,
    port: int | None = None,
) -> DaemonHandle:
    """Launch the staged app and gate on an exact-sha healthz."""
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    daemon_port = port or free_port()
    env = build_daemon_env(
        home=home,
        workspace=workspace,
        stub_port=stub_port,
        stub_model=stub_model,
        bundle_manifest=bundle_manifest,
        extra_env=extra_env,
    )
    stdout_path = run_dir / "daemon.stdout.log"
    stderr_path = run_dir / "daemon.stderr.log"
    # Append mode: every restart's console evidence survives in one file —
    # a truncating log would destroy exactly the crash we need to see.
    with stdout_path.open("ab") as out, stderr_path.open("ab") as err:
        process = subprocess.Popen(
            [python_bin, "-m", "apps.vool_api_server", "--port", str(daemon_port), "--bind", "127.0.0.1"],
            cwd=str(app_dir),
            env=env,
            stdout=out,
            stderr=err,
            start_new_session=False,
        )
    handle = DaemonHandle(
        pid=process.pid,
        port=daemon_port,
        process=process,
        home=home,
        app_dir=app_dir,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        launch_env={k: v for k, v in env.items() if "KEY" not in k.upper() or k.endswith("_MODEL")},
        staged_sha=expected_sha or "",
    )
    client = WireClient(f"http://127.0.0.1:{daemon_port}", wire_log, default_timeout_s=5.0)
    deadline = time.monotonic() + boot_timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr_tail = _tail(stderr_path, 2000)
            stdout_tail = _tail(stdout_path, 2000)
            handle.terminate()
            raise BenchError(
                f"daemon died during boot (exit {process.returncode})",
                detail=f"stderr tail: {stderr_tail}\nstdout tail: {stdout_tail}",
            )
        try:
            exchange = client.healthz(timeout_s=3.0)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            exchange = None
        if exchange is not None and exchange.status == 200:
            payload = exchange.response_json()
            runtime = dict((payload or {}).get("runtime") or {})
            handle.healthz_runtime = runtime
            commit = str(runtime.get("commit_full") or runtime.get("commit") or "")
            dirty = runtime.get("dirty")
            if expected_sha and not commit.startswith(expected_sha):
                handle.terminate()
                raise BenchError(
                    "daemon reports a different commit than the staged SHA",
                    detail=f"healthz commit={commit!r} expected~{expected_sha!r}",
                )
            if dirty is True:
                handle.terminate()
                raise BenchError("daemon reports dirty=true; staged app must be exact", detail=str(runtime))
            return handle
        time.sleep(0.5)
    handle.terminate()
    raise BenchError(
        "daemon did not answer /healthz within the boot timeout",
        detail=f"last error: {last_error}; stderr tail: {_tail(stderr_path, 2000)}",
    )


def _tail(path: Path, limit: int) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "<unreadable>"
    return data[-limit:]


def read_pending_approvals(home: Path) -> list[dict[str, Any]]:
    """Read the product's pending-approvals file in the isolated home.

    The store is a dict keyed by approval token; normalize every shape found
    in the wild into rows carrying ``approval_id`` (the token) + ``status``.

    (Read-only view of what the product itself persisted; the APPROVE/DENY
    action always goes through the product's own API, never by editing this
    file.)
    """
    path = home / "data" / "pending_approvals.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict) and "approvals" not in payload and "pending" not in payload:
        for token, item in payload.items():
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault("approval_id", token)
                rows.append(row)
    elif isinstance(payload, dict):
        items = payload.get("approvals") or payload.get("pending") or []
        rows = [dict(item) for item in items if isinstance(item, dict)]
    elif isinstance(payload, list):
        rows = [dict(item) for item in payload if isinstance(item, dict)]
    return rows
