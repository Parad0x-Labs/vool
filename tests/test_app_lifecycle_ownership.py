"""Single-lifetime process ownership for the native app (window host <-> runtime daemon).

The 2026-09-02 defect pair: externally killing the daemon left the native window host alive
forever (an empty window holding the single-instance lock), and nothing ever called the
supervisor's ``assert_alive`` — the watchdog contract existed only as dead code. Conversely, the
window host installed no SIGTERM/SIGINT handlers, so an external SIGTERM killed the host through
its default disposition without running its ``finally`` — orphaning the owned daemon and its
listeners, plus a stale ``vool_api.pid``.

Every test here drives the REAL ``vool_window.py`` / supervisor / server modules in subprocess
drivers with fake webview/supervisor stand-ins — produced behavior, never prose greps:

- owned-daemon death closes the window and exits the host deterministically (exit 3);
- SIGTERM to the host tears the owned runtime down before the process dies;
- teardown sweeps the pidfile of the owned child (SIGKILLed daemons cannot clean up);
- a stale boot pidfile naming a provably dead pid is swept before a new runtime starts;
- supervisor teardown kills the whole owned process group (grandchildren included);
- a daemon spawned with the app-ownership marker exits when its parent window host dies.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WINDOW_SCRIPT = REPO / "installer" / "bundle" / "vool_window.py"
SUPERVISOR_SCRIPT = REPO / "installer" / "bundle" / "native_runtime_supervisor.py"
SERVER_SCRIPT = REPO / "apps" / "vool_api_server.py"

BASE_PATH = "/usr/bin:/bin"


def _driver(tmp_path: Path, name: str, body: str, env: dict[str, str] | None = None,
            timeout: float = 90.0) -> subprocess.CompletedProcess:
    """Run a driver script with the repo importable, an isolated HOME, and no user state."""
    home = tmp_path / ("home-" + name)
    home.mkdir(exist_ok=True)
    driver = tmp_path / f"driver_{name}.py"
    driver.write_text(textwrap.dedent(body))
    full_env = {
        "PATH": BASE_PATH,
        "HOME": str(home),
        "VOOL_HOME": str(tmp_path / ("vool-home-" + name)),
        **(env or {}),
    }
    return subprocess.run([sys.executable, str(driver)], capture_output=True, text=True,
                          env=full_env, timeout=timeout)


def _open_log(home_name: str, tmp_path: Path) -> str:
    log_file = tmp_path / home_name / "Library" / "Application Support" / "VOOL" / "open.log"
    return log_file.read_text() if log_file.exists() else ""


# ------------------------------------------------------------------------------------------
# The window host must die deterministically when its owned daemon dies
# ------------------------------------------------------------------------------------------

_RUNTIME_DEATH_DRIVER = """
    import importlib.util, os, sys, threading, time, types
    spec = importlib.util.spec_from_file_location("nw", {window!r})
    nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
    import installer.bundle.native_runtime_supervisor as nrs

    class FakeWindow:
        def __init__(self):
            self.destroyed = threading.Event()
        def destroy(self):
            self.destroyed.set()

    class FakeSupervisor:
        owns_runtime = True
        def __init__(self):
            self.alive_checks = 0
            self.shutdowns = 0
        def ensure_ready(self):
            return {{"commit_full": "a" * 40, "pid": 4242}}
        def assert_alive(self):
            self.alive_checks += 1
            raise nrs.NativeRuntimeError("owned runtime child exited (returncode=-9)")
        def shutdown(self):
            self.shutdowns += 1

    sup = FakeSupervisor()
    nrs.NativeRuntimeSupervisor.from_environment = classmethod(lambda cls, **kw: sup)

    created = {{}}
    fake = types.ModuleType("webview")
    import installer.bundle.native_frame_authority as frame_authority
    frame_ready = []
    frame_authority.install_macos_frame_authority = lambda: frame_ready.append(True)
    def create_window(*a, **k):
        assert sys.platform != 'darwin' or frame_ready == [True]
        w = FakeWindow(); created["w"] = w; return w
    def start(fn=None):
        deadline = time.time() + 15
        while not created.get("w").destroyed.is_set():
            if time.time() > deadline:
                print("START-TIMEOUT"); return
            time.sleep(0.02)
    fake.create_window = create_window
    fake.start = start
    sys.modules["webview"] = fake

    rc = nw.main()
    print("RC=%d" % rc)
    print("DESTROYED=%s" % created["w"].destroyed.is_set())
    print("SHUTDOWN=%d" % sup.shutdowns)
"""


def test_owned_daemon_death_closes_the_window_and_exits_the_host(tmp_path: Path) -> None:
    env = {"VOOL_RUNTIME_WATCHDOG_POLL": "0.05"}
    done = _driver(tmp_path, "runtime-death", _RUNTIME_DEATH_DRIVER.format(window=str(WINDOW_SCRIPT)), env)
    log = _open_log("home-runtime-death", tmp_path)
    assert "START-TIMEOUT" not in done.stdout, "window host ignored its daemon's death for 15s"
    assert "RC=3" in done.stdout, f"runtime loss must exit 3, got: {done.stdout}"
    assert "DESTROYED=True" in done.stdout, "the watchdog must close the window"
    assert "SHUTDOWN=1" in done.stdout, "teardown must release the (dead) child"
    assert "runtime lost" in log


def test_window_host_without_watchdog_contract_still_opens_and_closes(tmp_path: Path) -> None:
    """Back-compat: a supervisor stand-in without assert_alive (existing fixtures) must not break
    the normal open -> close path; the host exits 0 when the window loop simply ends."""
    body = _RUNTIME_DEATH_DRIVER.format(window=str(WINDOW_SCRIPT)).replace(
        "        def assert_alive(self):\n            self.alive_checks += 1\n"
        '            raise nrs.NativeRuntimeError("owned runtime child exited (returncode=-9)")\n',
        "",
    ).replace('        while not created.get("w").destroyed.is_set():\n', "        if True:\n")
    done = _driver(tmp_path, "no-watchdog-contract", body, {"VOOL_RUNTIME_WATCHDOG_POLL": "0.05"})
    assert "RC=0" in done.stdout, done.stdout + done.stderr
    assert "SHUTDOWN=1" in done.stdout
    assert "START-TIMEOUT" not in done.stdout


# ------------------------------------------------------------------------------------------
# External SIGTERM to the window host must tear the owned runtime down first
# ------------------------------------------------------------------------------------------

_SIGTERM_DRIVER = """
    import importlib.util, os, signal, sys, threading, time, types
    spec = importlib.util.spec_from_file_location("nw", {window!r})
    nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
    import installer.bundle.native_runtime_supervisor as nrs

    class FakeWindow:
        def destroy(self): pass

    class FakeSupervisor:
        owns_runtime = True
        def ensure_ready(self):
            return {{"commit_full": "a" * 40, "pid": 4242}}
        def shutdown(self):
            nw._log("FAKE-SUPERVISOR-SHUTDOWN-CALLED")

    sup = FakeSupervisor()
    nrs.NativeRuntimeSupervisor.from_environment = classmethod(lambda cls, **kw: sup)

    fake = types.ModuleType("webview")
    import installer.bundle.native_frame_authority as frame_authority
    frame_ready = []
    frame_authority.install_macos_frame_authority = lambda: frame_ready.append(True)
    def create_window(*a, **k):
        assert sys.platform != 'darwin' or frame_ready == [True]
        print("WINDOW-CREATED", flush=True)
        return FakeWindow()
    fake.create_window = create_window
    def start(fn=None):
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(0.02)
        print("START-TIMEOUT")
    fake.start = start
    sys.modules["webview"] = fake

    threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    rc = nw.main()
    print("RC=%d" % rc)
"""


def test_external_sigterm_tears_down_the_owned_runtime_before_exit(tmp_path: Path) -> None:
    done = _driver(tmp_path, "sigterm", _SIGTERM_DRIVER.format(window=str(WINDOW_SCRIPT)))
    log = _open_log("home-sigterm", tmp_path)
    assert "START-TIMEOUT" not in done.stdout, "SIGTERM left the window host running"
    assert done.returncode == 0, f"expected a clean exit after teardown, got {done.returncode}"
    assert "FAKE-SUPERVISOR-SHUTDOWN-CALLED" in log, "SIGTERM must release the owned child"
    assert "SIGTERM" in log
    assert "WINDOW-CREATED" in done.stdout


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS bridge installation gate')
def test_failed_native_frame_installation_prevents_window_creation_and_releases_runtime(tmp_path):
    body = _RUNTIME_DEATH_DRIVER.format(window=str(WINDOW_SCRIPT)).replace(
        'frame_authority.install_macos_frame_authority = lambda: frame_ready.append(True)',
        'def refused():\n        raise RuntimeError("frame authority unavailable")\n'
        '    frame_authority.install_macos_frame_authority = refused',
    ).replace('print("DESTROYED=%s" % created["w"].destroyed.is_set())',
              'print("CREATED=%d" % len(created))')
    done = _driver(tmp_path, 'frame-refused', body)
    assert done.returncode == 0, done.stderr
    assert 'RC=1' in done.stdout
    assert 'CREATED=0' in done.stdout
    assert 'SHUTDOWN=1' in done.stdout


# ------------------------------------------------------------------------------------------
# PID-file hygiene: teardown sweeps the owned child's pidfile; boot sweeps a provably dead one
# ------------------------------------------------------------------------------------------

def _write_pidfile(tmp_path: Path, name: str, pid: int) -> Path:
    pid_dir = tmp_path / ("vool-home-" + name) / "data"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / "vool_api.pid"
    pid_file.write_text(f"{pid}\n", encoding="utf-8")
    return pid_file


def _dead_pid() -> int:
    proc = subprocess.Popen(["/bin/sleep", "0"])
    proc.wait()
    assert proc.poll() is not None
    return proc.pid


def test_teardown_sweeps_the_owned_childs_pidfile(tmp_path: Path) -> None:
    dead = _dead_pid()
    pid_file = _write_pidfile(tmp_path, "sweep-owned", dead)
    body = f"""
        import importlib.util, os, sys
        spec = importlib.util.spec_from_file_location("nw", {str(WINDOW_SCRIPT)!r})
        nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
        nw._sweep_owned_runtime_pidfile({dead})
        print("SWEPT=%s" % (not os.path.exists({str(pid_file)!r})))
    """
    done = _driver(tmp_path, "sweep-owned", body)
    assert "SWEPT=True" in done.stdout, done.stdout + done.stderr


def test_teardown_never_touches_a_foreign_pidfile(tmp_path: Path) -> None:
    alive = subprocess.Popen(["/bin/sleep", "20"])
    try:
        pid_file = _write_pidfile(tmp_path, "sweep-foreign", alive.pid)
        body = f"""
            import importlib.util, os, sys
            spec = importlib.util.spec_from_file_location("nw", {str(WINDOW_SCRIPT)!r})
            nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
            nw._sweep_owned_runtime_pidfile({alive.pid + 1})
            print("KEPT=%s" % os.path.exists({str(pid_file)!r}))
        """
        done = _driver(tmp_path, "sweep-foreign", body)
        assert "KEPT=True" in done.stdout, "a live foreign pidfile must survive teardown"
    finally:
        alive.kill()
        alive.wait()


def test_boot_sweeps_a_stale_pidfile_naming_a_dead_pid(tmp_path: Path) -> None:
    dead = _dead_pid()
    pid_file = _write_pidfile(tmp_path, "sweep-stale", dead)
    body = f"""
        import importlib.util, os, sys
        spec = importlib.util.spec_from_file_location("nw", {str(WINDOW_SCRIPT)!r})
        nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
        nw._sweep_stale_runtime_pidfile()
        print("SWEPT=%s" % (not os.path.exists({str(pid_file)!r})))
    """
    done = _driver(tmp_path, "sweep-stale", body)
    assert "SWEPT=True" in done.stdout, done.stdout + done.stderr


def test_boot_keeps_a_live_occupants_pidfile(tmp_path: Path) -> None:
    alive = subprocess.Popen(["/bin/sleep", "20"])
    try:
        pid_file = _write_pidfile(tmp_path, "keep-live", alive.pid)
        body = f"""
            import importlib.util, os, sys
            spec = importlib.util.spec_from_file_location("nw", {str(WINDOW_SCRIPT)!r})
            nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
            nw._sweep_stale_runtime_pidfile()
            print("KEPT=%s" % os.path.exists({str(pid_file)!r}))
        """
        done = _driver(tmp_path, "keep-live", body)
        assert "KEPT=True" in done.stdout, done.stdout + done.stderr
    finally:
        alive.kill()
        alive.wait()


def test_sigterm_before_ownership_capture_still_sweeps_the_dead_childs_pidfile(
    tmp_path: Path,
) -> None:
    """Live-matrix finding (2026-09-02): a SIGTERM that lands while ensure_ready() is still
    validating kills the daemon through the handler — but the owned-pid capture has not run yet,
    so the owned-pidfile sweep is a no-op and a SIGKILLed-mid-graceful daemon leaves
    vool_api.pid behind. The signal path must fall back to the stale sweep (provably dead pid)."""
    dead = _dead_pid()
    pid_file = _write_pidfile(tmp_path, "sigterm-early", dead)
    body = f"""
        import importlib.util, os, sys
        spec = importlib.util.spec_from_file_location("nw", {str(WINDOW_SCRIPT)!r})
        nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)

        class FakeSupervisor:
            owns_runtime = True
            def ensure_ready(self):
                raise RuntimeError("SIGTERM must land before this returns")
            def shutdown(self):
                pass

        sup = FakeSupervisor()
        nw._install_termination_handlers(sup, {{"pid": None}})
        os.kill(os.getpid(), 15)
    """
    done = _driver(tmp_path, "sigterm-early", body, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    assert not pid_file.exists(), (
        "a SIGTERM before ownership capture must still sweep the dead child's pidfile"
    )


# ------------------------------------------------------------------------------------------
# Supervisor teardown kills the whole owned process group (grandchildren included)
# ------------------------------------------------------------------------------------------

def test_supervisor_teardown_kills_grandchildren_of_the_owned_runtime(tmp_path: Path) -> None:
    """Child-death propagation: the owned runtime's own children must not survive teardown."""
    sys.path.insert(0, str(REPO))
    marker = tmp_path / "grandchild.pid"
    command = ["bash", "-c", f'/bin/sleep 60 & echo $! > {marker}; wait']
    from installer.bundle.native_runtime_supervisor import NativeRuntimeSupervisor

    supervisor = NativeRuntimeSupervisor(
        tmp_path, command, expected_sha="a" * 40,
        health_url="http://127.0.0.1:1/healthz",  # nothing listens; readiness path unused here
        startup_timeout=1.0,
    )
    supervisor.process = supervisor._popen(
        command, cwd=str(tmp_path), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    deadline = time.time() + 5
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.02)
    grandchild = int(marker.read_text().strip())
    try:
        os.kill(grandchild, 0)
    except OSError:
        pytest.fail("grandchild never started")
    supervisor.shutdown()
    time.sleep(0.3)
    try:
        os.kill(grandchild, 0)
    except OSError:
        pass
    else:
        pytest.fail("grandchild survived the owned teardown")


# ------------------------------------------------------------------------------------------
# The daemon exits when its parent window host dies (orphan defense; macOS has no PDEATHSIG)
# ------------------------------------------------------------------------------------------

_PARENT_WATCH_DRIVER = """
    import importlib.util, os, sys, time
    sys.path.insert(0, {repo!r})
    {marker_line}
    spec = importlib.util.spec_from_file_location("srv", {server!r})
    srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)

    class FakeServer:
        should_exit = False

    server = FakeServer()
    srv._start_parent_death_watch(server)
    deadline = time.time() + 10
    while time.time() < deadline:
        if server.should_exit:
            print("SHOULD-EXIT=1"); break
        time.sleep(0.05)
    else:
        print("SHOULD-EXIT=0")
"""


def test_daemon_sets_should_exit_when_its_parent_window_host_dies(tmp_path: Path) -> None:
    parent = subprocess.Popen(["/bin/sleep", "30"])
    time.sleep(0.2)
    parent.kill()
    parent.wait()
    body = _PARENT_WATCH_DRIVER.format(
        repo=str(REPO), server=str(SERVER_SCRIPT),
        marker_line=f'os.environ["VOOL_OWNED_BY_WINDOW_PID"] = str({parent.pid})',
    )
    done = _driver(tmp_path, "parent-death", body)
    assert "SHOULD-EXIT=1" in done.stdout, done.stdout + done.stderr


def test_daemon_without_ownership_marker_never_arms_the_parent_watch(tmp_path: Path) -> None:
    body = _PARENT_WATCH_DRIVER.format(
        repo=str(REPO), server=str(SERVER_SCRIPT),
        marker_line="os.environ.pop('VOOL_OWNED_BY_WINDOW_PID', None)",
    )
    done = _driver(tmp_path, "parent-watch-unarmed", body, timeout=30)
    assert "SHOULD-EXIT=0" in done.stdout, (
        "an unmarked daemon must not watch any parent (updater relaunches, manual runs)"
    )
