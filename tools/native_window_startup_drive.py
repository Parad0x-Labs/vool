"""Open a packaged VOOL.app's ACTUAL native window against a private HOME and a loopback provider.

Phase 1 (prepare, first run only): the bundle's own interpreter runs the packaged daemon once in
the private HOME, the scripted loopback provider is registered and certified through the
production doors (``tests._reader_served_rig``), and the runtime store is seeded with existing
history (``tests.test_chat_startup_served._seed_history``). Phase 2 (window): the bundle's real
launcher, ``Contents/MacOS/VOOL``, is started with ``HOME`` pointed at the private directory, so
the app's own supervisor starts the packaged daemon on the canonical port and opens the WKWebView.
Every local model endpoint points at the loopback stub, Ollama model registration is off and key
storage is file-backed: no local model is launched and no Keychain item is touched. The stub
stays up for as long as the window runs; the operator drives the window by hand (or with a UI
automation tool) and reads the timings from the daemon's request log named in ``state.json``.

Usage (from the worktree, with the repo on PYTHONPATH):
    python tools/native_window_startup_drive.py --app dist/<name>/VOOL.app --home <private-home> \\
        [--launches 2] [--no-prepare]

A second launch re-uses the prepared HOME, which is the "relaunch with existing history" case.
The canonical port must be free: the app's supervisor replaces any runtime it identifies there,
and this drive must never stop an operator's own runtime.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

CANONICAL_API = "http://127.0.0.1:11435"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _healthz(url: str, timeout: float = 1.5) -> dict | None:
    try:
        with urlopen(f"{url}/healthz", timeout=timeout) as response:
            return json.load(response)
    except Exception:
        return None


def _normal_launch_env(home: Path, extra: dict[str, str]) -> dict[str, str]:
    """The environment for a NORMAL-use launch of the bundle inside a private HOME.

    Nothing here points a model endpoint anywhere: the runtime discovers providers the way it does
    for any operator, and credentials come only from what the operator types into Settings. The
    two kill-switches keep this Mac's rules: no local model may load, and installed Ollama models
    are not registered. Key storage is file-backed under a passphrase generated once per private
    HOME and kept only there (0600), so nothing reaches the Keychain and relaunches reuse it.
    """
    import secrets

    passphrase_file = home / "key-passphrase"
    if not passphrase_file.exists():
        passphrase_file.write_text(secrets.token_urlsafe(32))
        passphrase_file.chmod(0o600)
    env = dict(os.environ)
    env.update(extra)
    env.update({
        "VOOL_DISABLE_MESH_DAEMON": "1",
        "VOOL_SKIP_PROVIDER_PREWARM": "1",
        "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
        "VOOL_LOCAL_MODELS_ENABLED": "0",
        "VOOL_KEY_STORAGE_MODE": "file",
        "VOOL_KEY_PASSPHRASE": passphrase_file.read_text().strip(),
        "VOOL_CREDENTIAL_STORE": "vault",
    })
    for key in ("VOOL_HOME", "PYTHONPATH", "OLLAMA_HOST", "VOOL_OLLAMA_URL", "VOOL_OLLAMA_CHAT_URL",
                "VOOL_OLLAMA_PS_URL", "VOOL_OLLAMA_TAGS_URL", "VOOL_INSTALL_PROFILE"):
        env.pop(key, None)
    return env


def _parent_pid(pid: int) -> int | None:
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out) if out else None
    except Exception:
        return None


def _listener_pid(port: int) -> int | None:
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=10).stdout.split()
        return int(out[0]) if out else None
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--launches", type=int, default=2)
    parser.add_argument("--no-prepare", action="store_true")
    parser.add_argument("--stub-default", default="Hello! How can I help you today?")
    parser.add_argument("--provider", choices=("stub", "none"), default="stub",
                        help="stub registers and certifies the scripted loopback provider (protocol drives); "
                             "none launches the bundle for NORMAL use: no scripted provider, no Ollama "
                             "redirection, local models disabled, file-backed key storage under a passphrase "
                             "kept only inside the private HOME, so credentials entered through Settings "
                             "stay in that profile and never reach the Keychain")
    parser.add_argument("--launch-mode", choices=("exec", "open"), default="exec",
                        help="exec runs Contents/MacOS/VOOL directly; open launches the bundle through "
                             "LaunchServices (`open -n -W --env ...`) so the window carries the bundle "
                             "identity that UI automation tools address")
    args = parser.parse_args()

    app = args.app.resolve()
    app_root = app / "Contents" / "Resources" / "app"
    embedded = app / "Contents" / "Resources" / "python" / "bin" / "python3"
    launcher = app / "Contents" / "MacOS" / "VOOL"
    for required in (app_root, embedded, launcher):
        if not required.exists():
            raise SystemExit(f"missing bundle member: {required}")

    from tests import _reader_served_rig as rig
    from tests.test_chat_startup_served import _seed_history

    rig.REPO_ROOT = app_root
    sys.executable = str(embedded)

    home = args.home.resolve()
    support = home / "Library" / "Application Support" / "VOOL"
    vool_home = support / "runtime"
    tmp = home / "tmp"
    for d in (vool_home, tmp, home / "Documents"):
        d.mkdir(parents=True, exist_ok=True)
    state_path = home / "state.json"
    stop_file = home / "STOP"
    extra = {
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "VOOL_INSTALL_PROFILE": "hybrid-fallback",
        # The window's watchdog polls /healthz; a deliberate SIGSTOP of the daemon (the
        # timeout/Stop scenarios) must not be read as a lost runtime.
        "VOOL_RUNTIME_WATCHDOG_POLL": "600",
        "VOOL_NATIVE_STARTUP_TIMEOUT": "240",
    }
    state: dict = {"app": str(app), "home": str(home), "vool_home": str(vool_home), "launch_mode": args.launch_mode,
                   "request_log": str(vool_home / "data" / "logs" / "vool_api.log"),
                   "window_log_dir": str(support), "launches": [], "phase": "starting"}

    def write_state() -> None:
        state_path.write_text(json.dumps(state, indent=1))

    if _healthz(CANONICAL_API) is not None:
        raise SystemExit("the canonical API port answers /healthz already; refusing to let the app's "
                         "supervisor replace a runtime this drive does not own")

    provider_context = (
        contextlib.nullcontext(None) if args.provider == "none"
        else rig.CapturingProvider(default=args.stub_default)
    )
    with provider_context as provider:
        daemon = None
        if provider is not None:
            state["stub_url"] = provider.base_url
            daemon = rig.ServedDaemon(vool_home, provider=provider, env_extra=extra)
        if provider is not None and not args.no_prepare:
            state["phase"] = "prepare"
            write_state()
            t0 = time.monotonic()
            daemon.start(timeout=240)
            try:
                _seed_history(daemon.home)
                certification = daemon.certify(timeout=180)
                state["certification"] = {"state": certification.get("state"),
                                          "seconds": round(time.monotonic() - t0, 3)}
                if certification.get("state") != "verified":
                    state["phase"] = "certification_failed"
                    write_state()
                    print(json.dumps(certification)[:800], flush=True)
                    return 2
            finally:
                daemon.stop()
        if daemon is not None:
            launch_env = daemon.env()
            for key in ("VOOL_HOME", "PYTHONPATH"):
                launch_env.pop(key, None)     # the launcher exports both from the bundle itself
        else:
            launch_env = _normal_launch_env(home, extra)
            state["provider"] = "none (normal use)"
        previous_daemon: int | None = None
        for index in range(max(1, args.launches)):
            if stop_file.exists():
                break
            # A quitting window tears its daemon down AFTER the launcher exits; polling /healthz
            # before that pid is gone attributes the old daemon's answer to the new launch.
            deadline = time.monotonic() + 60
            while previous_daemon and time.monotonic() < deadline:
                try:
                    os.kill(previous_daemon, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.2)
            record = {"index": index, "launcher_started_at": _now()}
            state["launches"].append(record)
            state["phase"] = f"launch-{index}"
            write_state()
            started = time.monotonic()
            if args.launch_mode == "open":
                # LaunchServices strips the environment; hand over exactly the isolation and
                # loopback variables, and let `open -W` block for the app's whole lifetime.
                command = ["open", "-n", "-W", "-a", str(app)]
                for key in sorted(launch_env):
                    if key in ("HOME", "TMPDIR") or key.startswith(("VOOL_", "OLLAMA_")):
                        command += ["--env", f"{key}={launch_env[key]}"]
            else:
                command = [str(launcher)]
            process = subprocess.Popen(command, env=launch_env, cwd=str(home),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
            record["launcher_pid"] = process.pid
            ready = None
            while time.monotonic() - started < 300:
                if process.poll() is not None:
                    break
                ready = _healthz(CANONICAL_API)
                if ready is not None:
                    break
                time.sleep(0.25)
            if ready is not None:
                record["healthz_ready_at"] = _now()
                record["healthz_ready_after_s"] = round(time.monotonic() - started, 3)
                record["runtime"] = {k: (ready.get("runtime") or {}).get(k) for k in ("commit_full", "build_id", "pid", "dirty")}
                record["daemon_pid"] = _listener_pid(11435)
                record["window_pid"] = _parent_pid(record["daemon_pid"]) if record["daemon_pid"] else None
            write_state()
            print(json.dumps({"launch": index, "ready": ready is not None, "launcher_pid": process.pid,
                              "daemon_pid": record.get("daemon_pid")}), flush=True)
            while process.poll() is None:
                if stop_file.exists():
                    # The window's own SIGTERM handler tears the owned runtime down; ask it first.
                    if record.get("window_pid"):
                        with contextlib.suppress(Exception):
                            os.kill(int(record["window_pid"]), signal.SIGTERM)
                    with contextlib.suppress(Exception):
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    with contextlib.suppress(Exception):
                        process.wait(timeout=20)
                    break
                time.sleep(1.0)
            previous_daemon = record.get("daemon_pid")
            record["launcher_exit_code"] = process.returncode
            record["launcher_exited_at"] = _now()
            record["provider_calls_so_far"] = len(provider.calls) if provider is not None else None
            write_state()
            print(json.dumps({"launch": index, "exit": process.returncode,
                              "provider_calls_so_far": (len(provider.calls) if provider is not None else None)}), flush=True)
        state["phase"] = "done"
        state["provider_calls"] = len(provider.calls) if provider is not None else None
        write_state()
    return 0


if __name__ == "__main__":
    sys.exit(main())
