"""Upgrade compatibility across the NULLA -> VOOL rename (installed-base contracts).

A user upgrading from a pre-rename build (the macOS .app that writes
``~/Library/Application Support/NULLA``) must land in their EXISTING profile, not a fresh
empty one; the two app generations must keep excluding each other's windows; and an
external SIGTERM must still tear the owned runtime down while the native run loop parks
the main thread. All three contracts were broken by the rename or by the Cocoa run loop
(measured 2026-09-19 against the packaged 0.6.0-beta app):

- the generated self-contained launcher hardcoded the new support dir, stranding a legacy
  profile and creating a duplicate empty home beside it;
- the single-instance guard moved to the new state dir / mutex name, so an old NULLA.app
  window and a new VOOL.app window could run at once and fight over the runtime port;
- the Python-level SIGTERM handler never ran while the main thread sat inside
  ``webview.start()``'s native run loop, so ``kill -TERM <host>`` was ignored outright.

The tests drive the REAL generated launcher text and the REAL window-host module — behavior,
never prose greps.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO / "installer" / "bundle" / "build_macos_app.sh"
WINDOW_SCRIPT = REPO / "installer" / "bundle" / "vool_window.py"

BASE_PATH = "/usr/bin:/bin"
LAUNCHER_HEREDOC_RE = re.compile(
    r"cat >\"\$\{APP\}/Contents/MacOS/VOOL\" <<'LAUNCHER'\n(.*?)\nLAUNCHER\n", re.S
)


def _self_contained_launcher_text() -> str:
    """The generated self-contained launcher, verbatim from the build script."""
    match = LAUNCHER_HEREDOC_RE.search(BUILD_SCRIPT.read_text(encoding="utf-8"))
    assert match, "self-contained launcher heredoc not found in build_macos_app.sh"
    return match.group(1)


def _stub_bundle_root(tmp_path: Path) -> Path:
    """A minimal Resources layout the launcher can source without a real bundle."""
    res = tmp_path / "stub-bundle" / "Contents" / "Resources"
    (res / "python/bin").mkdir(parents=True)
    (res / "app/installer/bundle").mkdir(parents=True)
    py = res / "python/bin/python3"
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    (res / "app/installer/bundle/launcher_ollama.sh").write_text(
        "vool_ensure_bundled_ollama() { :; }\n"
    )
    (tmp_path / "stub-bundle" / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict><key>NULLASourceSHA</key><string>deadbeef</string>'
        "</dict></plist>\n"
    )
    return tmp_path / "stub-bundle" / "Contents" / "Resources"


def _run_launcher(launcher: str, home: Path, res: Path, env: dict[str, str] | None = None) -> str:
    """Run the launcher verbatim with the final window-host exec replaced by a marker."""
    body = launcher.replace(
        'exec "${PY}" "${RES}/app/installer/bundle/vool_window.py"',
        'echo "LAUNCHER-DONE VOOL_HOME=${VOOL_HOME}"',
    )
    assert body != launcher, "the launcher's exec into the window host was not found"
    full_env = {"PATH": BASE_PATH, "HOME": str(home), "RES": str(res), **(env or {})}
    done = subprocess.run(["bash", "-c", body], env=full_env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, f"launcher failed: {done.stdout}\n{done.stderr}"
    log = home / "Library/Application Support/VOOL/app.log"
    return (log.read_text() if log.exists() else "") + "\n[stdout] " + done.stdout


def _legacy_runtime(home: Path) -> Path:
    runtime = home / "Library/Application Support/NULLA/runtime"
    (runtime / "data").mkdir(parents=True)
    (runtime / "data/nulla_web0_v2.db").write_bytes(b"legacy-profile")
    return runtime


def _canonical_runtime(home: Path) -> Path:
    runtime = home / "Library/Application Support/VOOL/runtime"
    runtime.mkdir(parents=True)
    return runtime


# ------------------------------------------------------------------------------------------
# The generated launcher must discover a pre-rename profile instead of duplicating it
# ------------------------------------------------------------------------------------------

def test_launcher_reuses_a_legacy_nulla_runtime_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy = _legacy_runtime(home)
    log = _run_launcher(_self_contained_launcher_text(), home, _stub_bundle_root(tmp_path))
    assert f"VOOL_HOME={legacy}" in log, f"legacy profile was not reused: {log}"
    assert "reusing the pre-rename runtime home" in log


def test_launcher_fresh_machine_uses_canonical_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    log = _run_launcher(_self_contained_launcher_text(), home, _stub_bundle_root(tmp_path))
    assert f"VOOL_HOME={home}/Library/Application Support/VOOL/runtime" in log
    assert "reusing the pre-rename runtime home" not in log


def test_launcher_both_profiles_exist_picks_canonical_and_names_the_conflict(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    canonical = _canonical_runtime(home)
    legacy = _legacy_runtime(home)
    log = _run_launcher(_self_contained_launcher_text(), home, _stub_bundle_root(tmp_path))
    assert f"VOOL_HOME={canonical}" in log, "selection must be deterministic, not first-found"
    assert str(legacy) in log and "not used" in log, "the conflict must be visible, never silent"


def test_launcher_respects_an_explicit_vool_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    explicit = tmp_path / "explicit-home"
    _legacy_runtime(home)  # an explicit home wins even when a legacy profile exists
    log = _run_launcher(
        _self_contained_launcher_text(), home, _stub_bundle_root(tmp_path),
        env={"VOOL_HOME": str(explicit)},
    )
    assert f"VOOL_HOME={explicit}" in log


# ------------------------------------------------------------------------------------------
# The single-instance guard must exclude the pre-rename app generation too
# ------------------------------------------------------------------------------------------

_LEGACY_LOCK_DRIVER = """
    import fcntl, importlib.util, os, sys
    spec = importlib.util.spec_from_file_location("nw", {window!r})
    nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
    legacy = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "NULLA")
    os.makedirs(legacy, exist_ok=True)
    fd = os.open(os.path.join(legacy, "window.lock"), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # what the old NULLA.app holds
    print("ALONE=%s" % nw._single_instance())
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock lane")
def test_single_instance_refuses_while_a_legacy_nulla_window_is_open(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(_LEGACY_LOCK_DRIVER.format(window=str(WINDOW_SCRIPT))))
    done = subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True, text=True, timeout=60,
        env={"PATH": BASE_PATH, "HOME": str(home)},
    )
    assert done.returncode == 0, done.stderr
    assert "ALONE=False" in done.stdout, (
        "a VOOL window must not open beside a still-running pre-rename window"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock lane")
def test_single_instance_holds_both_generation_locks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent("""
        import importlib.util, os, sys
        spec = importlib.util.spec_from_file_location("nw", {window!r})
        nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
        assert nw._single_instance(), "first instance must be allowed"
        for name in ("VOOL", "NULLA"):
            path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", name, "window.lock")
            print("LOCK-EXISTS", name, os.path.exists(path))
        print("SECOND=%s" % nw._single_instance())
    """).format(window=str(WINDOW_SCRIPT)))
    done = subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True, text=True, timeout=60,
        env={"PATH": BASE_PATH, "HOME": str(home)},
    )
    assert done.returncode == 0, done.stderr
    assert "LOCK-EXISTS VOOL True" in done.stdout
    assert "LOCK-EXISTS NULLA True" in done.stdout, (
        "the legacy lock must be held too, or an old NULLA.app started later stacks a window"
    )
    assert "SECOND=False" in done.stdout


def test_windows_mutex_names_cover_both_generations() -> None:
    """Source contract for the Win32 lane (cannot execute Win32 here): both the canonical
    and the pre-rename mutex names must be claimed, or an old window stacks on Windows."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("nw", WINDOW_SCRIPT)
    nw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nw)
    assert "Local\\VOOL_WINDOW_SINGLETON" in nw._WIN_MUTEX_NAMES
    assert "Local\\NULLA_WINDOW_SINGLETON" in nw._WIN_MUTEX_NAMES


# ------------------------------------------------------------------------------------------
# SIGTERM must tear the owned runtime down while the native run loop parks the main thread
# ------------------------------------------------------------------------------------------

_SIGNAL_WATCHER_DRIVER = """
    import importlib.util, os, signal, sys, threading, time
    spec = importlib.util.spec_from_file_location("nw", {window!r})
    nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)

    class FakeSupervisor:
        def shutdown(self):
            nw._log("FAKE-SUPERVISOR-SHUTDOWN-CALLED")

    nw._install_termination_handlers(FakeSupervisor(), {{"pid": None}})
    # The watcher-thread mechanism is what runs in production while the main thread is
    # parked inside webview.start()'s native run loop; a real signal here exercises the
    # same C-trampoline -> wakeup-fd -> watcher chain.
    os.kill(os.getpid(), signal.SIGTERM)
    for _ in range(100):
        time.sleep(0.1)  # bytecode or not, the watcher must exit the process
    print("SURVIVED")  # teardown never ran
"""


@pytest.mark.skipif(os.name != "posix", reason="POSIX wakeup-fd lane")
def test_sigterm_teardown_runs_via_the_wakeup_watcher_without_main_thread_scheduling(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(_SIGNAL_WATCHER_DRIVER.format(window=str(WINDOW_SCRIPT))))
    done = subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True, text=True, timeout=60,
        env={"PATH": BASE_PATH, "HOME": str(home)},
    )
    assert "SURVIVED" not in done.stdout, "SIGTERM was ignored again (watcher never ran)"
    assert done.returncode == 0, f"expected the watcher's clean exit, got {done.returncode}"
    log = home / "Library/Application Support/VOOL/open.log"
    text = log.read_text() if log.exists() else ""
    assert "FAKE-SUPERVISOR-SHUTDOWN-CALLED" in text, "the owned runtime was not released"
    assert "termination signal received" in text
