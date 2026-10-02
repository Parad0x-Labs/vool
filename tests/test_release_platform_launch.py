"""Exercise the emitted platform launch boundaries without installing a runtime."""
from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def function(name: str) -> str:
    source = (ROOT / "installer/install_vool.sh").read_text()
    match = re.search(rf"^{name}\(\) \{{.*?^\}}", source, re.M | re.S)
    assert match
    return match.group()


def linux_shortcuts(tmp_path: Path, *, desktop: bool = False) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    if desktop:
        (home / "Desktop").mkdir()
    project = tmp_path / 'research 100% $desk "quoted"'
    (project / "installer/assets").mkdir(parents=True)
    for icon in ("vool.png", "vool.icns", "vool_stop.png"):
        (project / "installer/assets" / icon).touch()
    for script in ("Open_Chat.sh", "Stop_VOOL.sh"):
        (project / script).write_text('#!/bin/bash\nprintf done > "$VOOL_TEST_MARKER"\n')
    functions = [function("create_desktop_shortcut")]
    source = (ROOT / "installer/install_vool.sh").read_text()
    if "desktop_entry_value() {" in source:
        functions.insert(0, function("desktop_entry_value"))
        functions.insert(1, function("desktop_entry_arg"))
    env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / "data"), "PROJECT_ROOT": str(project)}
    code = '\n'.join(functions) + '\nuname() { echo Linux; }\nxdg-user-dir() { echo "$HOME/Desktop"; }\nsay() { :; }\ncreate_desktop_shortcut\n'
    result = subprocess.run(["bash", "-c", code], env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return home, project


def test_linux_installs_app_menu_without_a_desktop_folder(tmp_path: Path) -> None:
    home, _ = linux_shortcuts(tmp_path)
    assert (home / "data/applications/VOOL.desktop").is_file()
    assert (home / "data/applications/Stop_VOOL.desktop").is_file()
    assert not (home / "Desktop").exists()


def test_linux_menu_and_desktop_share_safe_launch_arguments(tmp_path: Path) -> None:
    home, project = linux_shortcuts(tmp_path, desktop=True)
    menu = home / "data/applications/VOOL.desktop"
    desktop = home / "Desktop/VOOL.desktop"
    assert menu.read_bytes() == desktop.read_bytes()
    exec_line = next(line for line in menu.read_text().splitlines() if line.startswith("Exec="))
    assert "100%%" in exec_line
    assert '\\\\$desk' in exec_line
    assert '\\\\"quoted\\\\"' in exec_line
    # On a Linux runner, use the desktop implementation itself rather than a home-grown parser.
    if shutil.which("gio") and os.uname().sysname == "Linux":
        marker = tmp_path / "opened"
        done = subprocess.run(["gio", "launch", str(menu)], env={**os.environ, "VOOL_TEST_MARKER": str(marker)},
                              capture_output=True, text=True, timeout=15)
        assert done.returncode == 0, done.stderr
        import time
        for _ in range(50):
            if marker.exists():
                break
            time.sleep(0.1)
        assert marker.read_text() == "done", str(project)


@pytest.mark.skipif(not Path("/usr/libexec/PlistBuddy").exists(), reason="macOS plist reader required")
@pytest.mark.parametrize("host,target,version,minimum,accepted", [
    ("arm64", "arm64", "14.0", "14.0", True),
    ("x86_64", "x86_64", "15.0", "14.0", True),
    ("x86_64", "arm64", "15.0", "14.0", False),
    ("arm64", "x86_64", "15.0", "14.0", False),
    ("arm64", "arm64", "13.6.9", "14.0", False),
    ("arm64", "arm64", "14.0", "14.1", False),
    ("arm64", "arm64", "14.0.1", "14.0.2", False),
    ("arm64", "arm64", "14.0", "unknown", False),
])
def test_mac_rejects_incompatible_build_before_python(tmp_path: Path, host: str, target: str,
                                                     version: str, minimum: str, accepted: bool) -> None:
    source = (ROOT / "installer/bundle/build_macos_app.sh").read_text()
    body = re.search(r"<<'LAUNCHER'\n(.*?)\nLAUNCHER", source, re.S).group(1)
    # Execute the actual emitted prefix; nothing after PY= may start without this guard passing.
    prefix = body.split('PY="${RES}/python/bin/python3"')[0]
    app = tmp_path / "VOOL.app/Contents"
    (app / "MacOS").mkdir(parents=True)
    (app / "Resources").mkdir()
    (app / "Info.plist").write_bytes(plistlib.dumps({"LSArchitecturePriority": [target],
                                                    "LSMinimumSystemVersion": minimum}))
    launcher = app / "MacOS/VOOL"
    launcher.write_text(prefix + '\nprintf passed > "$VOOL_TEST_MARKER"\n')
    shims = tmp_path / "bin"
    shims.mkdir()
    for name, output in (("uname", host), ("sw_vers", version), ("osascript", "alert shown")):
        shim = shims / name
        shim.write_text(f"#!/bin/bash\necho '{output}'\n")
        shim.chmod(0o755)
    marker = tmp_path / "python-reached"
    env = {**os.environ, "HOME": str(tmp_path / "home"), "PATH": f"{shims}:/usr/bin:/bin",
           "VOOL_TEST_MARKER": str(marker)}
    done = subprocess.run(["bash", str(launcher)], env=env, capture_output=True, text=True, timeout=15)
    assert marker.exists() is accepted
    assert (done.returncode == 0) is accepted
    if not accepted:
        log = tmp_path / "home/Library/Application Support/VOOL/app.log"
        assert "VOOL cannot start" in log.read_text()


def test_public_bootstrap_uses_the_public_repository() -> None:
    shell = (ROOT / "installer/bootstrap_vool.sh").read_text()
    windows = (ROOT / "installer/bootstrap_vool.ps1").read_text()
    assert 'REPO="${VOOL_GITHUB_REPO:-vool}"' in shell
    assert '$RepoName = "vool"' in windows


_POST_START_DRIVER = """
    import importlib.util, sys, types

    # Keep the Settings binding on its deterministic non-Cocoa fallback so the control does
    # not depend on whether the host happens to have PyObjCTools installed.
    sys.modules["PyObjCTools"] = None  # 'from PyObjCTools import ...' now raises ImportError
    spec = importlib.util.spec_from_file_location("nw", {window!r})
    nw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nw)

    seen = []
    nw._install_settings_shortcut = lambda *a, **k: seen.append("settings")

    callback = None
    if {with_callback}:
        callback = lambda: seen.append("dock")
    nw._run_post_start(callback)
    print("POST-START", seen)
"""


@pytest.mark.parametrize("with_callback,expected", [(False, ["settings"]), (True, ["dock", "settings"])])
def test_post_start_contract_covers_the_absent_dock_callback(tmp_path: Path, with_callback: bool,
                                                             expected: list) -> None:
    """The post-start hook must survive the absent dock callback and still do its own work.

    ``_dock_post_start`` returns None on every non-macOS platform by contract; calling it
    unconditionally raised ``TypeError: 'NoneType' object is not callable`` on every Linux
    launch (run 36982838739, job 110761047078) and aborted the Settings-shortcut install
    with it. The absent-callback case and the legitimate-callback case are both driven
    through the real window-host module, off the real control flow.
    """
    home = tmp_path / "home"
    home.mkdir()
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(_POST_START_DRIVER.format(
        window=str(ROOT / "installer/bundle/vool_window.py"), with_callback=with_callback)))
    done = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(home)})
    assert done.returncode == 0, done.stderr
    assert f"POST-START {expected}" in done.stdout, done.stdout


@pytest.mark.skipif(sys.platform == "linux", reason="requires a non-Linux host")
def test_linux_acceptance_refuses_simulated_platform_evidence(tmp_path: Path) -> None:
    profile, evidence = tmp_path / "profile", tmp_path / "native.json"
    done = subprocess.run([sys.executable, str(ROOT / "ops/linux_desktop_smoke.py"),
                           "--profile", str(profile), "--output", str(evidence),
                           "--expected-commit", "a" * 40], capture_output=True, text=True, timeout=10)
    assert done.returncode == 2
    assert "requires Linux" in done.stderr
    assert not profile.exists()
    assert not evidence.exists()
