"""Contract checks for the self-contained Windows bundle tooling (installer/bundle).

These pin the launcher/build/packaging contract that a hand-verified build depends on, so a
later edit can't silently break the installer (which CI can't compile).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

from core.runtime_install_profiles import default_ollama_models_path
from installer.bundle import bundle_supervisor
from installer.bundle.bundle_supervisor import BundleSupervisor

_BUNDLE = Path(__file__).resolve().parent.parent / "installer" / "bundle"


def test_bundle_build_provenance_is_checkout_derived_and_fail_closed() -> None:
    script = (_BUNDLE / "build_bundle.ps1").read_text(encoding="utf-8")

    assert "SourceCommit" not in script
    assert "SourceBranch" not in script
    assert '[string]$buildSource.source_kind -ne "git"' in script
    assert "[string]$buildSource.commit_full -notmatch '^[0-9a-f]{40}$'" in script
    assert "[bool]$buildSource.dirty_state" in script
    assert "Build from a native Git checkout" in script


def test_launcher_starts_server_windowless_and_delegates_open() -> None:
    cmd = (_BUNDLE / "vool-launch.cmd").read_text(encoding="utf-8")
    assert "vool_api_server.py" in cmd
    assert "--port 11435" in cmd
    assert "pythonw.exe" in cmd  # windowless server
    assert "ollama" in cmd.lower()  # starts the bundled Ollama
    assert "vool_window.py" in cmd  # opens the native WebView2 app window (not a browser tab)
    # models + data live OUTSIDE the install dir so uninstall/reinstall never deletes the LLMs/wallet
    assert "OLLAMA_MODELS=%VOOL_HOME%\\models" in cmd
    assert "OLLAMA_MODELS=%VOOL_ROOT%models" not in cmd  # not the (uninstall-deletable) install dir
    assert "bundle_supervisor.py" in cmd
    assert "bundle_manifest.json" in cmd
    assert '--root "%VOOL_ROOT:~0,-1%"' in cmd


def test_bundle_supervisor_is_stoppable_and_persists_diagnostics() -> None:
    supervisor = (_BUNDLE / "bundle_supervisor.py").read_text(encoding="utf-8")
    assert "--stop" in supervisor
    assert "bundle-supervisor.log" in supervisor
    assert "bundle-supervisor.json" in supervisor
    assert "retry_in" in supervisor
    assert "CREATE_NO_WINDOW" in supervisor


def test_bundle_manifest_controls_model_store_for_supervisor_and_runtime(tmp_path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "bundle_manifest.json").write_text(
        json.dumps(
            {
                "schema": "vool.bundle_manifest.v1",
                "selected_model": "qwen2.5:7b",
                "model_store": "%LOCALAPPDATA%\\\\VOOL\\\\models",
            }
        )
        + "\n",
        encoding="utf-8-sig",
    )
    home = tmp_path / "runtime"
    env = {
        "VOOL_HOME": str(home),
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "VOOL_BUNDLE_ROOT": str(root),
    }
    supervisor = BundleSupervisor(root, env=env)
    supervisor._prepare()

    assert Path(supervisor.env["OLLAMA_MODELS"]) == home / "models"
    assert default_ollama_models_path(env) == (home / "models").resolve()
    assert (home / "models").is_dir()


def test_bundle_supervisor_restarts_dead_child_with_backoff_and_logs() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "bundle"
        root.mkdir()
        (root / "bundle_manifest.json").write_text(
            json.dumps({"schema": "vool.bundle_manifest.v1", "selected_model": "qwen2.5:7b"}) + "\n",
            encoding="utf-8-sig",
        )
        home = Path(tmpdir) / "home"
        supervisor = BundleSupervisor(root, env={"VOOL_HOME": str(home), "LOCALAPPDATA": str(home)})
        supervisor._prepare()
        first = mock.Mock(pid=101)
        first.poll.return_value = None
        replacement = mock.Mock(pid=202)
        replacement.poll.return_value = None

        with mock.patch("installer.bundle.bundle_supervisor.subprocess.Popen", side_effect=[first, replacement]) as popen, mock.patch.object(
            supervisor, "_healthy", return_value=False
        ):
            supervisor._ensure_ollama()
            assert popen.call_count == 1
            first.poll.return_value = 17
            supervisor._reap_dead("ollama")
            assert supervisor.failures["ollama"] == 1
            assert supervisor.next_attempt["ollama"] > 0
            supervisor.next_attempt["ollama"] = 0
            supervisor._ensure_ollama()
            assert popen.call_count == 2
            supervisor._stop_child("ollama")

        log = (home / "logs" / "bundle-supervisor.log").read_text(encoding="utf-8")
        assert "exited returncode=17" in log
        assert "retry_in=" in log
        state = json.loads((home / "run" / "bundle-supervisor.json").read_text(encoding="utf-8"))
        assert state["restart_counts"]["ollama"] == 2
        assert state["last_exit"]["ollama"]["reason"] == "exited returncode=17"


def test_bundle_supervisor_stops_the_windows_process_tree(tmp_path, monkeypatch) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    home = tmp_path / "home"
    supervisor = BundleSupervisor(root, env={"VOOL_HOME": str(home), "LOCALAPPDATA": str(home)})
    supervisor._prepare()
    child = mock.Mock(pid=404)
    child.poll.return_value = None
    child.wait.return_value = 0
    supervisor.processes["ollama"] = child
    # The narrow platform seam, never the global os.name: pathlib dispatches on os.name at
    # Path() construction, so a global flip poisons every other Path in the process --
    # including pytest's own failure formatter (run 36063857499 shard 5).
    monkeypatch.setattr(bundle_supervisor, "_is_windows_platform", lambda: True)

    with mock.patch(
        "installer.bundle.bundle_supervisor.subprocess.run",
        return_value=mock.Mock(returncode=0),
    ) as run:
        supervisor._stop_child("ollama")

    assert run.call_args.args[0] == ["taskkill.exe", "/PID", "404", "/T", "/F"]
    assert run.call_args.kwargs["timeout"] == 8
    child.terminate.assert_not_called()
    child.wait.assert_called_once_with(timeout=5)


def test_bundle_supervisor_lease_prevents_duplicate_instances(tmp_path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    home = tmp_path / "home"
    env = {"VOOL_HOME": str(home), "LOCALAPPDATA": str(home)}
    first = BundleSupervisor(root, env=env)
    second = BundleSupervisor(root, env=env)

    assert first._acquire_lease() is True
    try:
        assert second._acquire_lease() is False
    finally:
        first._release_lease()

    try:
        assert second._acquire_lease() is True
    finally:
        second._release_lease()


def test_bundle_supervisor_startup_timeout_enters_bounded_backoff(tmp_path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "bundle_manifest.json").write_text(
        json.dumps({"schema": "vool.bundle_manifest.v1", "selected_model": "qwen2.5:7b"}) + "\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    supervisor = BundleSupervisor(root, env={"VOOL_HOME": str(home), "LOCALAPPDATA": str(home)})
    supervisor._prepare()
    stalled = mock.Mock(pid=303)
    stalled.poll.return_value = None
    supervisor.processes["api"] = stalled
    supervisor.started_at["api"] = 0

    with (
        mock.patch.object(supervisor, "_healthy", return_value=False),
        mock.patch.object(supervisor, "_stop_child") as stop_child,
    ):
        supervisor._ensure_api()

    stop_child.assert_called_once_with("api")
    assert supervisor.failures["api"] == 1
    assert supervisor.next_attempt["api"] > 0
    log = (home / "logs" / "bundle-supervisor.log").read_text(encoding="utf-8")
    assert "startup timeout" in log


def test_bundle_supervisor_clears_stale_api_processes_before_bundle_start(tmp_path, monkeypatch) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    home = tmp_path / "home"
    supervisor = BundleSupervisor(root, env={"VOOL_HOME": str(home)})
    supervisor._prepare()
    monkeypatch.setattr(bundle_supervisor, "_is_windows_platform", lambda: True)

    with mock.patch.object(supervisor, "_health_probe", return_value=False), mock.patch.object(
        supervisor, "_start"
    ) as start, mock.patch("installer.bundle.bundle_supervisor.subprocess.run") as run:
        supervisor._ensure_api()

    assert start.called
    command = run.call_args.args[0]
    assert command[0] == "powershell.exe"
    assert "*apps.vool_api_server*" in command[-1]
    assert "*vool_api_server.py*" in command[-1]
    assert "Stop-Process" in command[-1]


def test_bundle_supervisor_treats_http_error_as_unhealthy(tmp_path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    supervisor = BundleSupervisor(root, env={"VOOL_HOME": str(tmp_path / "home")})
    response = mock.MagicMock(status=404)
    response.__enter__.return_value = response
    response.__exit__.return_value = None

    with mock.patch("installer.bundle.bundle_supervisor.urllib.request.urlopen", return_value=response):
        assert supervisor._healthy("http://127.0.0.1:11435/healthz") is False


def test_bundle_supervisor_stop_entrypoint_writes_stoppable_marker(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home))
    monkeypatch.setattr("sys.argv", ["bundle_supervisor.py", "--stop"])

    assert bundle_supervisor.main() == 0
    assert (home / "run" / "stop").read_text(encoding="utf-8").strip() == "stop"


def test_bundle_supervisor_once_releases_lease_and_cleans_state(tmp_path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "bundle_manifest.json").write_text(
        json.dumps({"schema": "vool.bundle_manifest.v1", "selected_model": "qwen2.5:7b"}) + "\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    env = {"VOOL_HOME": str(home), "LOCALAPPDATA": str(home)}
    supervisor = BundleSupervisor(root, env=env)
    supervisor._ensure_ollama = mock.Mock()
    supervisor._ensure_api = mock.Mock()
    supervisor._ensure_window = mock.Mock()

    assert supervisor.run(once=True) == 0

    assert not supervisor.state_path.exists()
    assert json.loads(supervisor.model_status_path.read_text(encoding="utf-8"))["status"] == "stopped"
    log = (home / "logs" / "bundle-supervisor.log").read_text(encoding="utf-8")
    assert "supervisor started" in log
    assert "supervisor stopped" in log

    replacement = BundleSupervisor(root, env=env)
    try:
        assert replacement._acquire_lease() is True
    finally:
        replacement._release_lease()


def test_native_window_host_uses_webview_and_owned_exact_runtime() -> None:
    py = (_BUNDLE / "vool_window.py").read_text(encoding="utf-8")
    assert "import webview" in py  # native WebView2 window, not a browser
    assert "create_window" in py and "http://127.0.0.1:11435" in py  # canonical local API origin
    assert 'URL = f"{_API_ORIGIN}/chat"' in py
    assert "supervisor.ensure_ready()" in py  # exact health + identity before any window
    assert "supervisor.shutdown()" in py  # owned backend child ends with native host
    assert "vool-open.ps1" in py  # graceful Edge --app fallback when WebView2 is unavailable
    assert "_has_webview2" in py  # checks the runtime up front so the fallback is reachable when it is absent
    assert "_single_instance" in py  # repeated launches don't stack multiple windows


def test_edge_fallback_opener_is_chromeless_app_window() -> None:
    ps1 = (_BUNDLE / "vool-open.ps1").read_text(encoding="utf-8")
    assert "/chat" in ps1  # opens VOOL's own UI, not OpenClaw
    assert "--app=" in ps1  # chromeless app window (no tabs/address bar)
    assert "msedge.exe" in ps1  # rides Windows' built-in Edge/WebView2
    assert "--user-data-dir=" in ps1  # dedicated profile -> real app window even when Edge is already open (not a tab)
    assert "Start-Process $url" in ps1  # default-browser fallback


def test_build_bundle_copies_every_runtime_package_and_stays_lean() -> None:
    ps1 = (_BUNDLE / "build_bundle.ps1").read_text(encoding="utf-8")
    # Every top-level package the server imports must be copied (verified by running the bundle).
    for pkg in ("apps", "core", "adapters", "storage", "network", "relay", "retrieval", "sandbox", "tools", "ops", "installer", "config", "skills", "plugins"):
        assert f'"{pkg}"' in ps1, f"build_bundle.ps1 must copy the {pkg} package"
    assert not (_BUNDLE.parents[1] / "channels").exists(), "a restored runtime package must join the bundle inventory"
    # Lean deps only; the heavy ML stack must not be pip-installed into the bundle.
    for lean in ("pydantic", "cryptography", "uvicorn", "starlette", "solders"):
        assert lean in ps1
    assert "pip install" in ps1 and "torch" not in ps1  # training-only stack never enters the bundle
    assert "Embedded Python pip bootstrap failed." in ps1
    assert "Embedded Python setuptools/wheel bootstrap failed." in ps1
    assert "Embedded Python runtime dependency installation failed." in ps1
    assert "& $py -m pip check" in ps1
    assert "Embedded Python dependency check failed." in ps1
    # Native window: pywebview/pythonnet deps + the host script copied in.
    assert "pywebview" in ps1 and "pythonnet" in ps1
    assert "vool_window.py" in ps1
    assert "bundle_supervisor.py" in ps1
    assert "bundle_manifest.json" in ps1
    assert '"installer\\stamp_build_source.py"' in ps1
    assert 'Join-Path $appDir "config\\build-source.json"' in ps1
    for field in ("app_version", "source_commit", "source_dirty_state"):
        assert field in ps1
    for canonical_source in (
        "AGENT_HANDOVER.md",
        "README.md",
        "docs\\SYSTEM_SPINE.md",
        "docs\\STATUS.md",
        "docs\\PROOF_PATH.md",
    ):
        assert f'"{canonical_source}"' in ps1
    assert "Canonical grounding source missing" in ps1


def test_build_bundle_copies_the_ollama_runner_runtime() -> None:
    ps1 = (_BUNDLE / "build_bundle.ps1").read_text(encoding="utf-8")
    assert '"lib\\ollama"' in ps1
    assert "llama-server.exe" in ps1
    assert "Ollama runtime support directory not found" in ps1
    assert "Ollama runner missing from the installation" in ps1


def test_iss_packages_stage_and_installs_per_user() -> None:
    iss = (_BUNDLE / "vool.iss").read_text(encoding="utf-8")
    assert "OutputBaseFilename=VOOL-Setup" in iss
    assert 'Source: "{#Stage}\\app\\*"' in iss
    assert 'Source: "{#Stage}\\ollama\\*"' in iss
    assert 'DestDir: "{localappdata}\\VOOL\\models"' in iss
    assert "uninsneveruninstall" in iss
    assert 'DestDir: "{app}\\models"' not in iss
    assert 'Source: "{#Stage}\\python\\*"' in iss
    assert 'Source: "{#Stage}\\bundle_manifest.json"' in iss
    assert 'Source: "{#Stage}\\VOOL.cmd"' in iss
    assert "PrivilegesRequired=lowest" in iss  # no admin needed
    assert "vool.vbs" in iss  # shortcuts launch windowless via the VBS wrapper (no console flash)
    assert "{#Stage}" in iss  # packages the staging dir
    # uninstall must NOT delete the user's data/models dir (wallet, keys, LLMs survive)
    assert 'Type: filesandordirs; Name: "{localappdata}\\VOOL"' not in iss
    # installer stops the running server itself -> no "close Python" prompt on reinstall
    assert "StopNulla" in iss and "vool_api_server.py" in iss
    assert "CloseApplications=no" in iss


def test_vbs_runs_the_launcher_hidden() -> None:
    vbs = (_BUNDLE / "vool.vbs").read_text(encoding="utf-8")
    assert "VOOL.cmd" in vbs
    assert ", 0, False" in vbs  # WShell.Run window style 0 = hidden


# --- Close-to-Dock (macOS): red X minimizes VOOL instead of quitting (Spotify-style) ---

class _FakeClosingEvent:
    """Stands in for pywebview's synchronous 'closing' event: captures the += handler."""

    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


class _FakeWindow:
    def __init__(self) -> None:
        self.events = type("E", (), {"closing": _FakeClosingEvent()})()
        self.hidden = 0

    def hide(self) -> None:
        self.hidden += 1


def test_close_to_dock_is_default_on_and_macos_only(monkeypatch) -> None:
    # Default ON since c8f74d1: the red X hides the window to the Dock and the app stays live.
    # Correctness rests on hooking the WINDOW's windowShouldClose_ rather than pywebview's
    # events.closing -- the earlier hook conflated the red X with Cmd+Q and made the app unquittable,
    # which is why this used to ship off. VOOL_CLOSE_TO_DOCK=0 is the escape hatch.
    from installer.bundle import vool_window as nw

    monkeypatch.setattr(nw.sys, "platform", "darwin")
    monkeypatch.delenv("VOOL_CLOSE_TO_DOCK", raising=False)
    assert nw._close_to_dock_enabled() is True  # ON by default
    for falsey in ("0", "false", "no", "off"):
        monkeypatch.setenv("VOOL_CLOSE_TO_DOCK", falsey)
        assert nw._close_to_dock_enabled() is False, falsey
    monkeypatch.setenv("VOOL_CLOSE_TO_DOCK", "1")
    assert nw._close_to_dock_enabled() is True
    # Never on the other packaging lane -- Windows/Linux window UX is KAS's.
    for other in ("win32", "linux"):
        monkeypatch.setattr(nw.sys, "platform", other)
        assert nw._close_to_dock_enabled() is False, other


def test_dock_behavior_is_installed_after_start_when_enabled(monkeypatch) -> None:
    # The delegates must be installed on the main thread AFTER the run loop is up, so the wiring is a
    # post-start callback rather than something done at window-construction time.
    from installer.bundle import vool_window as nw

    monkeypatch.setattr(nw.sys, "platform", "darwin")
    monkeypatch.delenv("VOOL_CLOSE_TO_DOCK", raising=False)
    assert callable(nw._dock_post_start(_FakeWindow()))


def test_dock_behavior_not_installed_when_opted_out(monkeypatch) -> None:
    from installer.bundle import vool_window as nw

    monkeypatch.setattr(nw.sys, "platform", "darwin")
    monkeypatch.setenv("VOOL_CLOSE_TO_DOCK", "0")  # escape hatch -> red X quits as before
    assert nw._dock_post_start(_FakeWindow()) is None
    monkeypatch.setattr(nw.sys, "platform", "win32")
    monkeypatch.delenv("VOOL_CLOSE_TO_DOCK", raising=False)
    assert nw._dock_post_start(_FakeWindow()) is None


def test_native_window_host_hides_on_red_x_without_vetoing_cmd_q() -> None:
    py = (_BUNDLE / "vool_window.py").read_text(encoding="utf-8")
    assert "_install_dock_behavior" in py
    assert "windowShouldClose_" in py  # the red X -- a WINDOW-delegate selector
    assert "orderOut_" in py  # hide, not close (app + Dock icon stay alive)
    assert "applicationShouldHandleReopen_" in py  # Dock-icon click reopens the window
    assert 'sys.platform != "darwin"' in py  # macOS-only; Windows/Linux lane untouched
    assert "VOOL_CLOSE_TO_DOCK" in py  # env escape hatch back to close-quits
    # The regression guard: Cmd+Q must NOT be intercepted. applicationShouldTerminate_ is the
    # APP-delegate selector the old events.closing hook effectively vetoed; it is referenced in the
    # module docstring for that reason, so assert it is never DEFINED (which is what would veto Quit).
    assert "def applicationShouldTerminate_" not in py
    # Same for the old mechanism: events.closing fires for BOTH the red X and Cmd+Q, so it must never
    # be SUBSCRIBED to (it is named in the docstring explaining exactly this, hence the += idiom).
    assert "events.closing +=" not in py


# --- Native folder picker (js_api bridge): "+ Project" opens a real folder dialog in the VOOL window ---

class _FakeDialogWindow:
    def __init__(self, result) -> None:
        self._result = result

    def create_file_dialog(self, kind):
        return self._result


def _inject_fake_webview(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("webview")
    fake.FOLDER_DIALOG = "FOLDER_DIALOG"
    monkeypatch.setitem(sys.modules, "webview", fake)


def test_pick_folder_returns_the_chosen_path(monkeypatch) -> None:
    from installer.bundle import vool_window as nw

    _inject_fake_webview(monkeypatch)
    api = nw._WindowApi()
    api.set_window(_FakeDialogWindow(["/Users/me/code/acme"]))
    assert api.pick_folder() == {"ok": True, "path": "/Users/me/code/acme"}


def test_pick_folder_reports_cancel(monkeypatch) -> None:
    from installer.bundle import vool_window as nw

    _inject_fake_webview(monkeypatch)
    api = nw._WindowApi()
    api.set_window(_FakeDialogWindow(None))  # dialog dismissed
    assert api.pick_folder() == {"ok": False, "cancelled": True}


def test_pick_folder_without_a_window_is_soft() -> None:
    from installer.bundle import vool_window as nw

    assert nw._WindowApi().pick_folder() == {"ok": False, "error": "no_window"}


def test_native_window_exposes_the_folder_picker_bridge() -> None:
    py = (_BUNDLE / "vool_window.py").read_text(encoding="utf-8")
    assert "js_api=api" in py  # the bridge is passed to create_window
    assert "def pick_folder" in py and "FOLDER_DIALOG" in py  # native folder dialog
    assert "create_file_dialog" in py
