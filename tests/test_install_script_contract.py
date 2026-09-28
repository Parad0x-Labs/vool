from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_install_script_autodetects_supported_python() -> None:
    script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")

    assert "resolve_python_bin()" in script
    assert "uv python find" in script
    assert "python3.11" in script


def test_install_script_rebuilds_unsupported_venv() -> None:
    script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")

    assert "Existing virtual environment uses unsupported Python. Rebuilding..." in script
    assert 'rm -rf "${VENV_DIR}"' in script


def test_install_script_hardens_openclaw_launcher_bootstrap() -> None:
    script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")

    assert "--install-profile <profile>" in script
    assert "ollama-only" in script
    assert "ollama-max" in script
    assert 'validate_selected_install_profile() {' in script
    assert 'ensure_profile_remote_credentials() {' in script
    assert 'Enter Kimi / Moonshot API key' in script
    assert 'Enter Tether API key' in script
    assert 'Enter OpenAI-compatible remote API key' in script
    assert '"${SCRIPT_DIR}/validate_install_profile.py"' in script
    assert 'persist_install_profile_record() {' in script
    assert 'persist_provider_env_file() {' in script
    assert 'PROVIDER_ENV_FILE="\\${VOOL_HOME}/config/provider-env.sh"' in script
    assert "from core.install_recommendations import build_install_recommendation_truth" in script
    assert "print(build_install_recommendation_truth().primary_local_model)" in script
    assert '") 2>/dev/null || echo "qwen3:8b"' in script
    assert 'PRIMARY_LOCAL_MODEL=qwen3:8b' in script
    assert "from core.install_recommendations import install_recommendation_machine_summary" in script
    assert "print(json.dumps(install_recommendation_machine_summary(), ensure_ascii=False))" in script
    assert '\'{"selected_tier":"capacity-C","ollama_model":"qwen3:8b","recommended_bundle_models":["qwen3:8b","deepseek-r1:8b"]}\'' in script
    assert 'VOOL_REMOTE_API_KEY VOOL_REMOTE_BASE_URL VOOL_REMOTE_MODEL VOOL_CLOUD_API_KEY' in script
    assert 'detect_install_profile_display() {' in script
    assert script.count('cd "${PROJECT_ROOT}" && "${VENV_DIR}/bin/python" -c "') >= 3
    assert 'cd "${PROJECT_ROOT}" && VOOL_HOME="${runtime_home}" VOOL_INSTALL_PROFILE="${requested_profile}" "${VENV_DIR}/bin/python" -c "' in script
    assert 'Profile: ${install_profile_display}' in script
    assert 'Recommended profile: ${recommended_install_profile_display}' in script
    assert 'Install profile: ${install_profile_display}' in script
    assert 'wait_for_http_ready() {' in script
    assert 'port_listening() {' in script
    assert 'spawn_detached() {' in script
    assert 'curl -sf --max-time 2 "\\${url}" >/dev/null 2>&1' in script
    assert 'cd "${PROJECT_ROOT}"' in script
    assert 'export VOOL_HOME="\\${VOOL_HOME:-${runtime_home}}"' in script
    # The starter deliberately does NOT default VOOL_WORKSPACE_ROOT: pinning it made
    # every packaged run resolve the unbound chat workspace to the hidden internal
    # directory, silently overriding the Desktop default (2026-09-18). An operator who
    # wants the pin exports it explicitly before starting.
    assert "export VOOL_WORKSPACE_ROOT=" not in script
    assert "VOOL_WORKSPACE_ROOT is deliberately NOT defaulted" in script
    assert 'export VOOL_OPENCLAW_API_PORT="\\${VOOL_OPENCLAW_API_PORT:-11435}"' in script
    assert 'export VOOL_OPENCLAW_API_URL="\\${VOOL_OPENCLAW_API_URL:-http://127.0.0.1:\\${VOOL_OPENCLAW_API_PORT}}"' in script
    assert 'export PATH="${SCRIPT_DIR}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"' in script
    assert 'VENV_RESOLVER="${PROJECT_ROOT}/scripts/ensure_workspace_runtime.sh"' in script
    assert 'VENV_PY="$(bash "${VENV_RESOLVER}")"' in script
    assert 'export VOOL_OPENCLAW_API_URL="\\${VOOL_OPENCLAW_API_URL:-http://127.0.0.1:\\${VOOL_OPENCLAW_API_PORT}}"' in script
    assert 'if [[ "\\${VOOL_LAUNCHD_SUPERVISOR:-0}" == "1" ]]; then' in script
    assert 'API_LOG_PATH="\\${VOOL_API_LOG_PATH:-\\${VOOL_HOME}/logs/api-supervised.log}"' in script
    assert 'terminate_pid() {' in script
    assert 'api_pid="\\$(spawn_detached "\\${API_LOG_PATH}" "\\${VENV_PY}" -m apps.vool_api_server --port "\\${VOOL_OPENCLAW_API_PORT}")"' in script
    assert 'wait_for_http_ready "\\${VOOL_OPENCLAW_API_URL}/healthz" 240 "\\${api_pid}" 5' in script
    assert 'start_new_session=True' in script
    # OpenClaw retirement: no gateway startup, no third-party config discovery, no registration.
    assert 'ollama launch openclaw' not in script
    assert 'openclaw gateway run' not in script
    assert 'discover_openclaw_paths' not in script
    assert 'register_openclaw_agent.py' not in script
    assert 'write_retired_openclaw_stub() {' in script
    assert 'say "Verifying live launch through the shell launcher..."' in script
    assert 'local launchd_runtime_ready=0' in script
    assert 'local launchd_runtime_consecutive=0' in script
    assert 'for _ in $(seq 1 240); do' in script
    assert 'curl -sf --max-time 2 "http://127.0.0.1:11435/v1/models" >/dev/null 2>&1' in script
    assert 'if [[ "${launchd_runtime_consecutive}" -ge 5 ]]; then' in script
    assert 'say "Launchd runtime verified at http://127.0.0.1:11435 (stable health + /v1/models)"' in script
    assert 'say "ERROR: launchd installed VOOL, but the API did not stay healthy long enough to verify /v1/models within 240 seconds."' in script
    assert 'exec "${PROJECT_ROOT}/Start_VOOL.sh"' in script
    assert 'pull_models "${ollama_exe}" "${install_profile}" "${model_tag}"' in script
    assert 'pull_models "${ollama_exe}" "${install_profile}" "${model_tag}" "${runtime_home}" "${openclaw_enabled}"' in script
    assert 'required_model="nomic-embed-text"' in script


def test_install_script_launch_agent_enables_supervised_runtime() -> None:
    script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")

    assert '<key>VOOL_LAUNCHD_SUPERVISOR</key>' in script
    assert '<string>1</string>' in script
    assert '<key>VOOL_API_LOG_PATH</key>' in script
    assert '<string>${log_dir}/api-supervised.log</string>' in script


def test_install_wrappers_forward_install_profile_and_extra_args() -> None:
    install_and_run = (PROJECT_ROOT / "Install_And_Run_VOOL.sh").read_text(encoding="utf-8")
    install_and_run_bat = (PROJECT_ROOT / "Install_And_Run_VOOL.bat").read_text(encoding="utf-8")
    install_bat = (PROJECT_ROOT / "Install_VOOL.bat").read_text(encoding="utf-8")
    install_bat_script = (PROJECT_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")

    assert '--start "$@"' in install_and_run
    assert "%*" in install_and_run_bat
    assert "%*" in install_bat
    assert "requested_profile=r'%VOOL_INSTALL_PROFILE%'" in install_bat_script
    assert '"%SCRIPT_DIR%validate_install_profile.py"' in install_bat_script


def test_windows_launchers_avoid_nested_quote_for_loop_around_python_exe() -> None:
    # `for /f "..." %%A in ('"%PYTHON_EXE%" -c "..." 2^>nul') do ...` silently produces no
    # output (and the receipt-derived variable silently falls back to a hardcoded default)
    # when %PYTHON_EXE% itself contains a space, which happens for any install path with a
    # space in it (e.g. a folder named "My Vool" or "Local Vool") -- a very real,
    # previously-undetected cause of VOOL reporting the wrong model at runtime despite the
    # installer having selected the right one. Every Windows launcher that reads
    # install_receipt.json at every startup must route through a temp file instead of an
    # inline for/f command clause.
    launcher_names = (
        "Start_VOOL.bat",
        "OpenClaw_VOOL.bat",
        "Talk_To_VOOL.bat",
    )
    for name in launcher_names:
        script = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert re.search(r"for /f[^\n]*\('\"%PYTHON_EXE%\"", script) is None, name

    install_bat_script = (PROJECT_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")
    assert re.search(r"for /f[^\n]*\('\"%VENV_DIR%\\Scripts\\python\.exe\"", install_bat_script) is None


def test_windows_installer_never_installs_or_boots_openclaw_software() -> None:
    install_bat_script = (PROJECT_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")

    # OpenClaw retirement: the installer must not install, boot, or invoke third-party
    # OpenClaw software through any path (Ollama's bootstrap subcommand or npm).
    assert '"%OLLAMA_EXE%" launch openclaw' not in install_bat_script
    assert "npm install -g openclaw" not in install_bat_script
    assert "where openclaw" not in install_bat_script
    assert "OpenClaw registration retired" in install_bat_script


def test_windows_launchers_use_module_entrypoint_for_api_server() -> None:
    install_bat_script = (PROJECT_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")
    start_launcher = (PROJECT_ROOT / "Start_VOOL.bat").read_text(encoding="utf-8")
    openclaw_launcher = (PROJECT_ROOT / "OpenClaw_VOOL.bat").read_text(encoding="utf-8")
    background_cmd = (PROJECT_ROOT / "vool_background.cmd").read_text(encoding="utf-8")

    assert 'for %%I in ("%PROJECT_ROOT%\\..\\.vool_runtime") do set "VOOL_HOME_DEFAULT=%%~fI"' in install_bat_script
    assert "Step 7/14: Verifying launchers" in install_bat_script
    assert "persist_windows_runtime_config.py" in install_bat_script
    assert '"Start_VOOL.bat" "Talk_To_VOOL.bat" "OpenClaw_VOOL.bat" "Stop_VOOL.bat" "vool_background.vbs" "vool_background.cmd"' in install_bat_script
    assert "Missing Windows launcher" in install_bat_script
    assert 'set "VBS_PATH=%PROJECT_ROOT%\\vool_background.vbs"' in install_bat_script
    assert 'set "BACKGROUND_CMD_PATH=%PROJECT_ROOT%\\vool_background.cmd"' in install_bat_script
    assert 'set "SCRIPT_DIR=%PROJECT_ROOT%' not in install_bat_script
    # The API server runs windowless via pythonw.exe with output redirected to a log, so no console
    # window appears in the taskbar (the watchdog launches it detached, so python.exe would pop one).
    assert "-m apps.vool_api_server" in start_launcher
    assert '"%PYTHONW_EXE%" -m apps.vool_api_server' in start_launcher
    assert "vool_api_server.log" in start_launcher
    # OpenClaw_VOOL.bat is a side-effect-free retirement stub.
    assert "retired from VOOL" in openclaw_launcher
    assert "Start_VOOL.bat" in openclaw_launcher
    assert "Open_Web0.bat" in openclaw_launcher
    assert "https://github.com/Parad0x-Labs/openclaw-skills" in openclaw_launcher
    assert "exit /b 1" in openclaw_launcher
    assert "Start_VOOL.bat" in background_cmd
    assert "vool_background.cmd" in install_bat_script
    assert "goto run" in background_cmd
    assert "http://127.0.0.1:11435/healthz" in background_cmd
    assert 'for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"' in background_cmd
    assert '--cwd "%SCRIPT_ROOT%"' in background_cmd
    assert "VOOL API detached start requested" in background_cmd
    assert "vool_api_child.log" in background_cmd
    assert "vool_api_child.err.log" in background_cmd
    assert "call \"%SCRIPT_DIR%Start_VOOL.bat\"" not in background_cmd
    assert 'schtasks /create /tn "VOOL_Daemon" /tr "\\"%SystemRoot%\\System32\\wscript.exe\\" \\"%VBS_PATH%\\""' in install_bat_script
    assert "BeginConnect('127.0.0.1', 11435" in background_cmd
    assert "for /L %%i in (1,1,120)" not in openclaw_launcher
    assert "Test-NetConnection" not in openclaw_launcher
    assert "18789" not in openclaw_launcher
    assert "register_openclaw_agent.py" not in openclaw_launcher
    assert "inject_openclaw_web0_pill.py" not in openclaw_launcher
    assert "patch_openclaw_session_retry.py" not in openclaw_launcher


def test_background_processes_run_without_visible_console_windows() -> None:
    start_launcher = (PROJECT_ROOT / "Start_VOOL.bat").read_text(encoding="utf-8")
    openclaw_launcher = (PROJECT_ROOT / "OpenClaw_VOOL.bat").read_text(encoding="utf-8")
    install_bat_script = (PROJECT_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")

    # API server: pythonw.exe (no console) + log redirect, so no window in the taskbar.
    assert 'set "PYTHONW_EXE=%SCRIPT_ROOT%\\.venv\\Scripts\\pythonw.exe"' in start_launcher
    assert '"%PYTHONW_EXE%" -m apps.vool_api_server >> "%TEMP%\\vool_api_server.log" 2>&1' in start_launcher

    # OpenClaw retirement: no gateway is started, so its hidden-task env var is not set anywhere.
    assert "OPENCLAW_WINDOWS_TASK_HIDDEN_LAUNCHER" not in openclaw_launcher
    assert "OPENCLAW_WINDOWS_TASK_HIDDEN_LAUNCHER" not in install_bat_script


def test_install_script_surfaces_machine_probe_command() -> None:
    script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")

    assert 'Probe:   ${PROJECT_ROOT}/Probe_VOOL_Stack.sh' in script


def test_public_hive_auth_helper_is_tracked() -> None:
    helper = PROJECT_ROOT / "ops" / "ensure_public_hive_auth.py"

    assert helper.exists()
    content = helper.read_text(encoding="utf-8")
    assert 'default=""' in content
    assert "from core.public_hive_bridge import ensure_public_hive_auth" in content


def test_workspace_runtime_bootstrap_helper_is_tracked() -> None:
    helper = PROJECT_ROOT / "scripts" / "ensure_workspace_runtime.sh"

    assert helper.exists()
    content = helper.read_text(encoding="utf-8")
    assert "runtime_python_ready()" in content
    assert "ensure_pip()" in content
    assert '"${VENV_DIR}/bin/python" -m ensurepip --upgrade' in content
    assert 'required = ("starlette", "uvicorn")' in content
    assert 'pip install -e "${PROJECT_ROOT}[runtime,proof]"' in content


def test_install_script_runs_public_hive_auth_helper_from_project_root() -> None:
    script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")

    assert 'result_json="$(cd "${PROJECT_ROOT}" && VOOL_HOME="${runtime_home}" \\' in script
    assert '"${VENV_DIR}/bin/python" -m ops.ensure_public_hive_auth \\' in script
    assert "hydrated_from_local_cluster" in script


def test_windows_installer_bootstraps_python_when_missing() -> None:
    # A one-click installer cannot assume Python is pre-installed: it must set Python up
    # itself so the single documented command works on a bare Windows host. install_vool.bat
    # must delegate to ensure_python.ps1 and use the concrete resolved interpreter path (a
    # freshly-installed Python is not on the current cmd session's PATH), not a bare
    # `python` / `py -3`, and it must no longer hard-exit when Python is absent.
    install_bat = (PROJECT_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")

    assert (PROJECT_ROOT / "installer" / "ensure_python.ps1").exists()
    assert "ensure_python.ps1" in install_bat
    assert '-OutFile "%PYFOUND_FILE%"' in install_bat
    assert 'set PYTHON_CMD="!PYTHON_EXE!"' in install_bat
    assert "Python was not found. Install Python 3.10+ and retry." not in install_bat


def test_ensure_python_helper_uses_winget_then_pythonorg_fallback() -> None:
    helper = (PROJECT_ROOT / "installer" / "ensure_python.ps1").read_text(encoding="utf-8")

    # winget first (best-effort), official python.org silent installer as the fallback, both
    # per-user (no admin), with a version check and Microsoft Store execution-alias-stub rejection.
    assert "Python.Python.3.12" in helper
    assert "python.org/ftp/python" in helper
    assert "InstallAllUsers=0" in helper
    assert "PrependPath=1" in helper
    assert "WindowsApps" in helper
    assert "sys.version_info" in helper
    # winget must be best-effort and non-blocking: non-interactive + a hard timeout so the
    # Python EXE's UAC self-elevation can't hang a headless run before the python.org fallback.
    assert "--disable-interactivity" in helper
    assert "WaitForExit(180000)" in helper
    # The python.org download must force modern TLS or it fails on older un-patched hosts.
    assert "Tls12" in helper
    # Get-Command -All so a real Python behind the Store stub on PATH isn't missed.
    assert "Get-Command $name -All" in helper
    # The resolved path is handed back to the .bat via -OutFile in the OEM codepage (so cmd's
    # for/f reads it intact even with a non-ASCII username), not stdout.
    assert "$OutFile" in helper
    assert "Set-Content -LiteralPath $OutFile -Value $found -Encoding Oem" in helper


# ------------------------------------------------------------------------------------------
# Executed main-flow regression: the REAL installer control flow must complete with only
# external side-effect boundaries stubbed. Introduced by the OpenClaw retirement, which
# deleted seed_agent_identity() while main still called it: bash exited 127
# ("seed_agent_identity: command not found") only mid-install, which source-only
# assertions cannot catch. These tests run real main() to completion.
# ------------------------------------------------------------------------------------------

_MAIN_STUBS = """ensure_python() { :; }
bootstrap_python_toolchain() { :; }
create_or_update_venv() {
  mkdir -p "${VENV_DIR}/bin"
  if [[ -n "${HARNESS_REAL_PYTHON:-}" ]]; then
    printf '#!/usr/bin/env bash\nexec "%s" "$@"\n' "${HARNESS_REAL_PYTHON}" > "${VENV_DIR}/bin/python"
    chmod +x "${VENV_DIR}/bin/python"
  fi
}
install_dependencies() { :; }
initialize_runtime() { :; }
bootstrap_public_hive_auth() { :; }
install_playwright_runtime() { :; }
bootstrap_xsearch() { :; }
ensure_profile_remote_credentials() { :; }
provision_optional_llamacpp_lane() { :; }
install_macos_launch_agent() { :; }
install_linux_xdg_autostart() { :; }
install_linux_keepalive_service() { :; }
ensure_ollama_api_key() { :; }
ensure_ollama_installed() { printf 'ollama-stub'; }
start_ollama_server() { :; }
heal_ollama_gpu_library() { :; }
pull_models() { :; }
configure_liquefy() { :; }
write_install_receipt() { :; }
run_install_doctor() { :; }
create_desktop_shortcut() { :; }
detect_required_ollama_models() { :; }
validate_selected_install_profile() { :; }
"""

_MAIN_HARNESS = "#!/usr/bin/env bash\nset -euo pipefail\nsource \"${HARNESS_FUNCTIONS}\"\n"


def _run_installer_main(
    tmp_path: Path,
    *,
    real_python: str | None,
    agent_name: str,
    runtime_home: Path,
) -> subprocess.CompletedProcess[str]:
    import os
    import subprocess

    tmp_path.mkdir(parents=True, exist_ok=True)
    installer_src = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    cut = installer_src.rfind('\nparse_args "$@"')
    assert cut != -1
    functions_path = tmp_path / "installer_functions.sh"
    functions_path.write_text(installer_src[:cut] + "\n" + _MAIN_STUBS, encoding="utf-8")

    harness_path = tmp_path / "run_main.sh"
    harness_path.write_text(
        _MAIN_HARNESS
        + "\n".join(
            [
                'PROJECT_ROOT="${HARNESS_PROJECT_ROOT}"',
                'SCRIPT_DIR="${HARNESS_SCRIPT_DIR}"',
                'VENV_DIR="${PROJECT_ROOT}/.venv"',
                "AUTO_YES=1",
                "AUTO_START=0",
                'RUNTIME_HOME_OVERRIDE="${HARNESS_RUNTIME_HOME}"',
                'AGENT_NAME_OVERRIDE="${HARNESS_AGENT_NAME}"',
                'VOOL_HOME="${HARNESS_RUNTIME_HOME}"',
                'LAUNCH_AGENT_PATH=""',
                'DESKTOP_SHORTCUT_PATH=""',
                "main",
                'rc=$?',
                'printf "\\nHARNESS_MAIN_RC=%s\\n" "${rc}"',
                'exit "${rc}"',
            ]
        ),
        encoding="utf-8",
    )
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    home = tmp_path / "isolated-home"
    home.mkdir(parents=True, exist_ok=True)

    from tests.platform_helpers import bash_script_args

    user_site = ""
    if real_python:
        import site

        candidate = site.getusersitepackages()
        if Path(candidate).is_dir():
            user_site = str(candidate)
    env = {
        **os.environ,
        "HOME": str(home),
        "PYTHONPATH": user_site,
        "HARNESS_FUNCTIONS": str(functions_path),
        "HARNESS_PROJECT_ROOT": str(project_root),
        "HARNESS_SCRIPT_DIR": str(PROJECT_ROOT / "installer"),
        "HARNESS_RUNTIME_HOME": str(runtime_home),
        "HARNESS_AGENT_NAME": agent_name,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if real_python:
        env["HARNESS_REAL_PYTHON"] = real_python
    return subprocess.run(
        bash_script_args(harness_path),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=300,
    )


def test_installer_main_flow_completes_and_seeds_native_identity(tmp_path: Path) -> None:
    import json
    import sys

    runtime_home = tmp_path / "runtime"
    result = _run_installer_main(
        tmp_path / "run1",
        real_python=sys.executable,
        agent_name="InstallFlowName",
        runtime_home=runtime_home,
    )

    combined = result.stdout + result.stderr
    assert "command not found" not in combined, combined[-2000:]
    assert "HARNESS_MAIN_RC=0" in result.stdout, combined[-2000:]
    identity_path = runtime_home / "data" / "owner_identity.json"
    assert identity_path.is_file(), "main flow must persist the owner identity"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    assert identity["agent_name"] == "InstallFlowName"


def test_installer_main_flow_preserves_existing_identity(tmp_path: Path) -> None:
    import json
    import sys

    runtime_home = tmp_path / "runtime"
    first = _run_installer_main(
        tmp_path / "run1",
        real_python=sys.executable,
        agent_name="OriginalName",
        runtime_home=runtime_home,
    )
    assert "HARNESS_MAIN_RC=0" in first.stdout

    second = _run_installer_main(
        tmp_path / "run2",
        real_python=sys.executable,
        agent_name="ShouldNotWin",
        runtime_home=runtime_home,
    )
    combined = second.stdout + second.stderr
    assert "command not found" not in combined, combined[-2000:]
    assert "HARNESS_MAIN_RC=0" in second.stdout, combined[-2000:]
    identity = json.loads((runtime_home / "data" / "owner_identity.json").read_text(encoding="utf-8"))
    assert identity["agent_name"] == "OriginalName", "a re-install must not rename the owner's agent"


def test_installer_main_flow_falls_back_to_requested_name_when_runtime_python_is_unavailable(
    tmp_path: Path,
) -> None:
    # The documented failure boundary: before the venv exists, seed_agent_identity cannot
    # invoke seed_identity.py; the install must still complete using the requested name.
    runtime_home = tmp_path / "runtime"
    result = _run_installer_main(
        tmp_path / "run1",
        real_python=None,
        agent_name="FallbackName",
        runtime_home=runtime_home,
    )

    combined = result.stdout + result.stderr
    assert "command not found" not in combined, combined[-2000:]
    assert "HARNESS_MAIN_RC=0" in result.stdout, combined[-2000:]
    assert "Visible agent name: FallbackName" in result.stdout
    assert not (runtime_home / "data" / "owner_identity.json").exists()
