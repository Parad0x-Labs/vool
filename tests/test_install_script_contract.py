from __future__ import annotations

import re
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
    assert 'if port_listening "127.0.0.1" "\\${VOOL_OPENCLAW_API_PORT}"; then' in script
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
    assert 'api_pid="\\$(spawn_detached /tmp/vool_api_server.log "\\${PROJECT_ROOT}/Start_VOOL.sh")"' in script
    assert 'wait_for_http_ready "\\${VOOL_OPENCLAW_API_URL}/healthz" 30 "\\${api_pid}" 3' in script
    assert 'spawn_detached /tmp/vool_openclaw.log ollama launch openclaw --yes --model "\\${MODEL_TAG}"' in script
    assert 'launch openclaw --yes --config --model "${model_tag}"' in script
    assert 'openclaw gateway run --force' in script
    assert '${HOME}/.openclaw-default' in script
    assert 'Skipping Ollama OpenClaw auto-config for isolated home' in script
    assert 'say "Verifying live launch through the shell launcher..."' in script
    assert 'local launchd_runtime_ready=0' in script
    assert 'local launchd_runtime_consecutive=0' in script
    assert 'for _ in $(seq 1 240); do' in script
    assert 'curl -sf --max-time 2 "http://127.0.0.1:11435/v1/models" >/dev/null 2>&1' in script
    assert 'if [[ "${launchd_runtime_consecutive}" -ge 5 ]]; then' in script
    assert 'say "Launchd runtime verified at http://127.0.0.1:11435 (stable health + /v1/models)"' in script
    assert 'say "ERROR: launchd installed VOOL, but the API did not stay healthy long enough to verify /v1/models within 240 seconds."' in script
    assert 'exec "${PROJECT_ROOT}/OpenClaw_VOOL.sh"' in script
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


def test_windows_installer_uses_headless_safe_openclaw_bootstrap() -> None:
    install_bat_script = (PROJECT_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")

    # The Ollama CLI's OpenClaw bootstrap subcommand cannot run headless here (it demands
    # an interactive terminal for model selection even with --yes and a model flag set),
    # and without its config-only flag it launches an attached interactive TUI that would
    # hang a headless install. The installer must not fall back to that subcommand.
    assert '"%OLLAMA_EXE%" launch openclaw' not in install_bat_script
    assert "npm install -g openclaw" in install_bat_script
    assert "where npm" in install_bat_script


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
    assert "http://127.0.0.1:11435/healthz" in openclaw_launcher
    assert "installer\\start_windows_detached.py" in openclaw_launcher
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
    assert "vool_background.vbs" in openclaw_launcher
    assert "%SystemRoot%\\System32\\wscript.exe" in openclaw_launcher
    assert 'schtasks /create /tn "VOOL_Daemon" /tr "\\"%SystemRoot%\\System32\\wscript.exe\\" \\"%VBS_PATH%\\""' in install_bat_script
    assert 'start "VOOL API" /MIN' not in openclaw_launcher
    assert "BeginConnect('127.0.0.1', 11435" in background_cmd
    assert "Could not start VOOL API" in openclaw_launcher
    assert "vool_api.err.log" in openclaw_launcher
    assert "where openclaw.cmd" in openclaw_launcher
    assert "where openclaw.exe" in openclaw_launcher
    assert "VOOL_ALLOW_MODEL_ENV_OVERRIDE" in openclaw_launcher
    assert 'from core.openclaw_locator import load_gateway_token; print(load_gateway_token())' in openclaw_launcher
    assert "%USERPROFILE%\\.local\\bin\\openclaw.cmd" in openclaw_launcher
    assert "gateway run --force --port 18789" in openclaw_launcher
    assert "goto ensure_gateway" in openclaw_launcher
    assert ":ensure_gateway" in openclaw_launcher
    assert "timeout /t" not in openclaw_launcher
    assert "for /L %%i in (1,1,120)" in openclaw_launcher
    assert "for /L %%j in (1,1,90)" in openclaw_launcher
    assert "-Tail 80" in openclaw_launcher
    assert 'type "%TEMP%\\vool_api.err.log"' not in openclaw_launcher
    assert 'Start-Sleep -Seconds 1' in openclaw_launcher
    assert "Test-NetConnection -ComputerName 127.0.0.1 -Port 18789" in openclaw_launcher
    assert "vool_gateway.err.log" in openclaw_launcher
    assert "OpenClaw gateway did not become reachable on 127.0.0.1:18789" in openclaw_launcher
    assert "OpenClaw CLI not found on PATH. Installing OpenClaw..." in install_bat_script
    assert "OPENCLAW_MEMORY_MODEL=nomic-embed-text" in install_bat_script


def test_background_processes_run_without_visible_console_windows() -> None:
    start_launcher = (PROJECT_ROOT / "Start_VOOL.bat").read_text(encoding="utf-8")
    openclaw_launcher = (PROJECT_ROOT / "OpenClaw_VOOL.bat").read_text(encoding="utf-8")
    install_bat_script = (PROJECT_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")

    # API server: pythonw.exe (no console) + log redirect, so no window in the taskbar.
    assert 'set "PYTHONW_EXE=%SCRIPT_ROOT%\\.venv\\Scripts\\pythonw.exe"' in start_launcher
    assert '"%PYTHONW_EXE%" -m apps.vool_api_server >> "%TEMP%\\vool_api_server.log" 2>&1' in start_launcher

    # OpenClaw gateway: hidden .vbs task launcher via the env var openclaw honors, set both in the
    # live launcher session and persisted by the installer so the daemon task is registered hidden.
    assert 'set "OPENCLAW_WINDOWS_TASK_HIDDEN_LAUNCHER=1"' in openclaw_launcher
    assert 'setx OPENCLAW_WINDOWS_TASK_HIDDEN_LAUNCHER "1"' in install_bat_script


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
