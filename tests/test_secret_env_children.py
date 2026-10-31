"""No child VOOL starts gets a key in its starting environment or argv.

On macOS any same-user program can read another process's starting environment (KERN_PROCARGS2)
and the macOS sandbox cannot stop it, so the guarantee checked here is about what VOOL puts in a
starting environment, never about what a sandboxed reader can see.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import secret_env
from core.secret_env import child_env, handoff_spawn_kwargs, is_secret_env_name, secret_argv_findings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer" / "bundle"))


def _fake() -> str:
    return "VOOLTESTKEY-" + secrets.token_hex(8)


@pytest.fixture
def keys(monkeypatch):
    values = {"OPENAI_API_KEY": _fake(), "ANTHROPIC_API_KEY": _fake(), "GITHUB_TOKEN": _fake(),
              "VOOL_KEY_PASSPHRASE": _fake()}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def _assert_clean(env, argv, values):
    blob = json.dumps({"env": dict(env or {}), "argv": [str(a) for a in argv or []]})
    for name, value in values.items():
        assert value not in blob, f"{name} value reached the child"
    assert not [n for n in (env or {}) if is_secret_env_name(n)]
    assert secret_argv_findings(argv) == []


def test_names_that_hold_keys_are_recognized_and_settings_are_not():
    for name in ("OPENAI_API_KEY", "VOOL_OPENROUTER_API_KEY", "GITHUB_TOKEN", "HF_TOKEN", "VOOL_KEY_PASSPHRASE",
                 "DB_PASSWORD", "VOOL_MEET_AUTH_TOKEN", "AWS_SECRET_ACCESS_KEY", "VOOL_RELEASE_GPG_KEY"):
        assert is_secret_env_name(name), name
    for name in ("PATH", "HOME", "VOOL_HOME", "VOOL_KEY_STORAGE_MODE", "TOKENIZERS_PARALLELISM", "VOOL_MAX_TOKENS",
                 "VOOL_CLOUD_USD_PER_TOKEN", "VOOL_WATCH_TOKEN_SYMBOL", "VOOL_NATIVE_AUTHORIZATION_DEADLINE",
                 "OPENAI_BASE_URL", "SSH_AUTH_SOCK", secret_env.HANDOFF_FD_ENV):
        assert not is_secret_env_name(name), name


def test_child_env_drops_keys_and_keeps_settings(keys):
    env = child_env(VOOL_HOME="/tmp/x")
    _assert_clean(env, [], keys)
    assert env["PATH"] == os.environ["PATH"] and env["VOOL_HOME"] == "/tmp/x"


def test_native_supervisor_sends_keys_over_a_pipe_not_the_environment(keys, tmp_path, monkeypatch):
    from native_runtime_supervisor import NativeRuntimeError, NativeRuntimeSupervisor

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    seen = {}

    class Exited:
        pid = 0
        returncode = 1

        def poll(self):
            return 1

        def wait(self, timeout=None):
            return 1

    def popen(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        fd = kwargs["pass_fds"][0]
        assert kwargs["env"][secret_env.HANDOFF_FD_ENV] == str(fd)
        seen["received"] = secret_env.receive_handoff({secret_env.HANDOFF_FD_ENV: str(os.dup(fd))})
        return Exited()

    sup = NativeRuntimeSupervisor(ROOT, [sys.executable, "-m", "apps.vool_api_server"], expected_sha="a" * 40,
                                  health_url=f"http://127.0.0.1:{port}/healthz", startup_timeout=2, popen=popen)
    with pytest.raises(NativeRuntimeError):
        sup.ensure_ready()
    _assert_clean(seen["kwargs"]["env"], seen["argv"], keys)
    # the daemon still gets every key (compared without printing a value)
    assert [n for n, v in keys.items() if seen["received"].get(n) != v] == []
    with pytest.raises(OSError):  # the parent's copy of the pipe end is closed after the spawn
        os.fstat(seen["kwargs"]["pass_fds"][0])


def test_local_service_autostart_child_gets_no_key(keys):
    from core import local_service_autostart as autostart

    seen = {}

    def spawn(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        raise OSError("not started in this test")

    svc = autostart.LocalService(name="t-" + secrets.token_hex(3), reachable=lambda: False,
                                 argv=["/usr/bin/true"], env={"COMFY_PORT": "1"}, ready_timeout=0.1)
    ok, _ = autostart.ensure_service(svc, sleep=lambda s: None, spawn=spawn)
    assert not ok
    _assert_clean(seen["env"], seen["argv"], keys)
    assert seen["env"]["COMFY_PORT"] == "1"


def test_wholesale_environment_launchers_hand_children_no_key(keys, tmp_path):
    from core import runtime_dependency_preflight
    from core.liquefy_client import LiquefyClientV1

    _assert_clean(LiquefyClientV1()._env, [], keys)
    env = runtime_dependency_preflight._child_environment(str(tmp_path))
    assert env["VOOL_KEY_PASSPHRASE"] == "import-conformance-preflight"  # fixed scratch value, not the owner's
    _assert_clean({k: v for k, v in env.items() if k != "VOOL_KEY_PASSPHRASE"}, [], keys)


@pytest.mark.skipif(sys.platform != "darwin", reason="the allowlist key rule is the macOS route")
def test_plugin_allowlist_does_not_copy_owner_keys_on_mac(keys, monkeypatch):
    from core.plugin_executor import build_child_env

    monkeypatch.setenv("SAMPLE_SETTING", "on")
    env = build_child_env(env_allowlist=("GITHUB_TOKEN", "OPENAI_API_KEY", "SAMPLE_SETTING"))
    _assert_clean(env, [], keys)
    assert env["SAMPLE_SETTING"] == "on"


@pytest.mark.skipif(sys.platform != "darwin", reason="Mac rule")
def test_keyed_mcp_server_outside_the_isolated_runtime_is_refused_on_mac(tmp_path, monkeypatch):
    from core import mcp_client
    from core.mcp_client import MCPError, MCPStdioClient

    from core import plugin_executor

    monkeypatch.setattr(plugin_executor, "confined_argv", lambda argv, **kw: (list(argv), "kernel:sandbox-exec"))
    started = []
    monkeypatch.setattr(mcp_client.subprocess, "Popen", lambda *a, **k: started.append(k) or pytest.fail("started"))
    client = MCPStdioClient(sys.executable, ["-c", "pass"], cwd=str(tmp_path), name="keyed",
                            env={"SERVICE_KEY": _fake()})
    with pytest.raises(MCPError):
        client.start()
    assert started == []


def test_start_script_keeps_saved_keys_unexported(tmp_path):
    text = (ROOT / "installer" / "install_vool.sh").read_text()
    block = re.search(r'(  \. "\\\$\{PROVIDER_ENV_FILE\}"\n  # Keys from this file.*?done < <\(.*?\)\n)', text, re.S)
    assert block, "Start_VOOL.sh provider-env block not found"
    snippet = block.group(1).replace("\\$", "$").replace("\\\\", "\\")
    key = _fake()
    provider = tmp_path / "provider-env.sh"
    provider.write_text(f"export OPENAI_API_KEY={key}\nexport OPENAI_BASE_URL=http://127.0.0.1:9/v1\n")
    script = f'PROVIDER_ENV_FILE="{provider}"\n{snippet}env\n'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20,
                         env={"PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    assert key not in out.stdout and "OPENAI_BASE_URL=http://127.0.0.1:9/v1" in out.stdout


def test_daemon_reads_saved_keys_from_its_file_into_this_process_only(tmp_path, monkeypatch):
    key = _fake()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "provider-env.sh").write_text(f"export OPENAI_API_KEY={key}\nexport OPENAI_MODEL=m\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        assert secret_env.load_provider_env_secrets(str(tmp_path)) == ["OPENAI_API_KEY"]
        assert os.environ["OPENAI_API_KEY"] == key
        out = subprocess.run(["/usr/bin/env"], capture_output=True, text=True, timeout=10)  # inherits the C environment
        assert key not in out.stdout
    finally:
        os.environ.pop("OPENAI_API_KEY", None)


def test_handoff_fails_closed_on_a_bad_pipe():
    with pytest.raises(secret_env.SecretHandoffError):
        secret_env.receive_handoff({secret_env.HANDOFF_FD_ENV: "not-a-number"})
    r, w = os.pipe(); os.write(w, b"{not json"); os.close(w)
    with pytest.raises(secret_env.SecretHandoffError):
        secret_env.receive_handoff({secret_env.HANDOFF_FD_ENV: str(r)})


# --- darwin: read the real started child's starting environment ------------------------------

def _procargs(pid: int) -> bytes | None:
    libc = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, pid)
    size = ctypes.c_size_t(1 << 20)
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    return buf.raw[: size.value]


def _ps_env(pid: int) -> str:
    return subprocess.run(["ps", "-E", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout


def _wait_for(path: Path, proc, limit=60.0) -> str:
    end = time.time() + limit
    while time.time() < end:
        if path.exists() and path.read_text().strip():
            return path.read_text()
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    return path.read_text() if path.exists() else ""


@pytest.mark.skipif(sys.platform != "darwin", reason="reads KERN_PROCARGS2 / ps -E of a started child on macOS")
def test_mac_supervisor_child_and_self_restart_start_without_keys(tmp_path):
    """Both routes the daemon uses: a launcher handoff, and keys arriving in the environment."""
    values = {"OPENAI_API_KEY": _fake(), "GITHUB_TOKEN": _fake()}
    child = (
        "import os,sys,time\n"
        f"sys.path.insert(0,{str(ROOT)!r})\n"
        "from core.secret_env import shed_starting_secrets\n"
        "shed_starting_secrets()\n"
        "open(sys.argv[1],'w').write(' '.join(sorted(k for k in ('OPENAI_API_KEY','GITHUB_TOKEN') if os.environ.get(k))))\n"
        "time.sleep(30)\n"
    )
    script = tmp_path / "child.py"
    script.write_text(child)
    base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    for route in ("handoff", "environment"):
        marker = tmp_path / f"{route}.txt"
        if route == "handoff":
            env, kwargs = handoff_spawn_kwargs({**base, **values})
        else:
            env, kwargs = {**base, **values}, {}
        proc = subprocess.Popen([sys.executable, str(script), str(marker)], env=env, start_new_session=True, **kwargs)
        secret_env.close_handoff(kwargs)
        try:
            seen = _wait_for(marker, proc)
            assert seen == "GITHUB_TOKEN OPENAI_API_KEY", (route, seen)  # the keys still arrived
            raw = _procargs(proc.pid)
            assert raw is not None
            ps = _ps_env(proc.pid)
            for name, value in values.items():
                assert value.encode() not in raw, (route, name)
                assert value not in ps and f"{name}=" not in ps, (route, name)
        finally:
            proc.terminate()
            proc.wait(10)


@pytest.mark.skipif(sys.platform != "darwin", reason="reads the started daemon's starting environment on macOS")
def test_mac_daemon_dev_start_holds_no_key_in_its_starting_environment(tmp_path):
    values = {"OPENAI_API_KEY": _fake(), "ANTHROPIC_API_KEY": _fake()}
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    env = {k: v for k, v in os.environ.items() if not is_secret_env_name(k) and k != "PYTEST_CURRENT_TEST"}
    env.update(values, PYTHONPATH=str(ROOT), VOOL_HOME=str(tmp_path / "home"), VOOL_KEY_STORAGE_MODE="file",
               VOOL_LOCAL_MODELS_ENABLED="0")
    log = (tmp_path / "daemon.log").open("wb")
    proc = subprocess.Popen([sys.executable, "-m", "apps.vool_api_server", "--port", str(port)], cwd=str(ROOT),
                            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        end = time.time() + 120
        while time.time() < end and proc.poll() is None:
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close()
                break
            except OSError:
                time.sleep(0.5)
        assert proc.poll() is None, (tmp_path / "daemon.log").read_text()[-800:]
        pids = [proc.pid] + [int(p) for p in subprocess.run(["pgrep", "-P", str(proc.pid)], capture_output=True,
                                                            text=True).stdout.split()]
        for pid in pids:
            raw = _procargs(pid) or b""
            ps = _ps_env(pid)
            for name, value in values.items():
                assert value.encode() not in raw and value not in ps, (pid, name)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, 15)
            try:
                proc.wait(20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, 9)
                proc.wait(5)
        log.close()
