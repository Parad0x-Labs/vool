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
    assert (
        "say \"ERROR: launchd installed VOOL, but the API did not stay verifiably healthy "
        "(VOOL /healthz identity + /v1/models) within 240 seconds.\"" in script
    )
    # The poll verifies the SERVED runtime's identity, not just curl exit status.
    assert "supervised_health_is_vool" in script
    assert 'exec "${PROJECT_ROOT}/Start_VOOL.sh"' in script
    assert 'pull_models "${ollama_exe}" "${install_profile}" "${model_tag}"' in script
    assert 'pull_models "${ollama_exe}" "${install_profile}" "${model_tag}" "${runtime_home}"' in script
    # Native memory-embedding provisioning (decoupled from the retired OpenClaw gate):
    # pull_models consults the core authority for the embedding lane, not a mode flag.
    assert 'detect_memory_embedding_models() {' in script
    assert '< <(detect_memory_embedding_models "${runtime_home}")' in script
    assert '"${runtime_home}" "${openclaw_enabled}"' not in script


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
    assert '"Start_VOOL.bat" "Talk_To_VOOL.bat" "Open_Chat.bat" "Open_Web0.bat" "OpenClaw_VOOL.bat" "Stop_VOOL.bat" "vool_background.vbs" "vool_background.cmd"' in install_bat_script
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
    assert "Open_Chat.bat" in openclaw_launcher
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


def _sh(value: str | Path) -> str:
    """One interpolation rule for every host path embedded in generated shell: convert for
    Git-Bash (tests/platform_helpers.bash_path) and shell-quote, so paths with spaces and
    Windows-native trees survive verbatim."""
    import shlex

    from tests.platform_helpers import bash_path

    return shlex.quote(bash_path(str(value)))


def _run_installer_main(
    tmp_path: Path,
    *,
    real_python: str | None,
    agent_name: str,
    runtime_home: Path,
    auto_start: bool = False,
    launch_agent_path: str = "",
    keep_functions: tuple[str, ...] = (),
    sleep_fast: bool = False,
    override_functions: str = "",
) -> subprocess.CompletedProcess[str]:
    import os
    import subprocess

    tmp_path.mkdir(parents=True, exist_ok=True)
    installer_src = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    cut = installer_src.rfind('\nparse_args "$@"')
    assert cut != -1
    # Boundary doubles are FUNCTIONS, never PATH shadowing: a Git-Bash login shell may
    # rewrite PATH, and Windows uses a different separator, but a defined function always
    # wins over any binary the resolver would find. keep_functions drops a stub so the
    # REAL installer body runs (the owning setup boundary) with only its external
    # service-manager commands doubled via override_functions.
    stubs = _MAIN_STUBS
    for name in keep_functions:
        needle = f"{name}() {{ :; }}\n"
        assert needle in stubs, f"cannot keep non-stubbed or multi-line function: {name}"
        stubs = stubs.replace(needle, "")
    functions_path = tmp_path / "installer_functions.sh"
    functions_path.write_text(installer_src[:cut] + "\n" + stubs, encoding="utf-8")

    extra_functions = ""
    if sleep_fast:
        # Test-only time double for the bounded verification poll: the production loop and
        # its 240-iteration limit stay byte-for-byte intact.
        extra_functions += "sleep() { :; }\n"

    harness_path = tmp_path / "run_main.sh"
    harness_path.write_text(
        _MAIN_HARNESS
        + "\n".join(
            [
                'PROJECT_ROOT="${HARNESS_PROJECT_ROOT}"',
                'SCRIPT_DIR="${HARNESS_SCRIPT_DIR}"',
                'VENV_DIR="${PROJECT_ROOT}/.venv"',
                "AUTO_YES=1",
                f"AUTO_START={1 if auto_start else 0}",
                'RUNTIME_HOME_OVERRIDE="${HARNESS_RUNTIME_HOME}"',
                'AGENT_NAME_OVERRIDE="${HARNESS_AGENT_NAME}"',
                'VOOL_HOME="${HARNESS_RUNTIME_HOME}"',
                f"LAUNCH_AGENT_PATH={_sh(launch_agent_path)}",
                'DESKTOP_SHORTCUT_PATH=""',
                override_functions,
                extra_functions,
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


class _VerifyFixtureServer:
    """A real loopback HTTP server on the canonical 127.0.0.1:11435 for the --start
    verification boundary: real curl, real sockets, real main flow. Modes:

    - ``vool``: the served /healthz payload contract (ok=true + runtime.app_version) and a
      /v1/models 200 — the intended healthy runtime.
    - ``foreign200``: HTTP 200 with a NON-VOOL body at both paths — an unrelated service
      squatting on the port.
    - ``intermittent``: alternates 200/503 on the polled paths so the consecutive-success
      counter must reset and verification must ultimately fail.

    Every request is logged so poll patterns and bootstrap-before-poll ordering are provable.
    ``/bootstrap-marker`` is the ordering probe the service-manager double reports through.
    """

    VOOL_HEALTH = {
        "ok": True,
        "agent": "FixtureAgent",
        "daemon": False,
        "runtime": {"app_version": "0.6.0", "protocol_version": 1},
    }

    def __init__(self, tmp_path: Path, mode: str) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        tmp_path.mkdir(parents=True, exist_ok=True)
        self.log_path = tmp_path / "fixture-requests.jsonl"
        self.mode = mode
        state: dict[str, int] = {"calls": 0}
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a: object) -> None:
                return

            def _send(self, payload: dict, status: int = 200) -> None:
                import json

                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                import json

                with rig._lock:
                    with open(rig.log_path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"path": self.path}) + "\n")
                    if rig.mode == "intermittent":
                        state["calls"] += 1
                        healthy = state["calls"] % 2 == 1
                    else:
                        healthy = True
                if self.path.startswith("/bootstrap-marker"):
                    return self._send({"ok": True})
                if rig.mode == "vool":
                    if self.path.startswith("/healthz"):
                        return self._send(dict(_VerifyFixtureServer.VOOL_HEALTH))
                    if self.path.startswith("/v1/models"):
                        return self._send({"object": "list", "data": []})
                    return self._send({})
                if not healthy:
                    return self._send({"error": "unavailable"}, status=503)
                if self.path.startswith("/healthz"):
                    return self._send({"status": "ok", "service": "unrelated-demo-app"})
                return self._send({"models": []})

        self._lock = threading.Lock()
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", 11435), _Handler)
        except OSError:
            import pytest

            pytest.skip("canonical port 11435 is busy in this environment")
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def paths(self) -> list[str]:
        import json

        if not self.log_path.exists():
            return []
        return [json.loads(line)["path"] for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _supervisor_boundary_doubles(tmp_path: Path) -> str:
    """Function doubles for the OWNING supervisor setup boundary: the REAL
    install_macos_launch_agent runs, but launchctl is recorded (and reports through the
    fixture's /bootstrap-marker so bootstrap-before-verification ordering is provable in one
    linear request log). No real service manager, scheduled task or owner state is touched."""
    record = tmp_path / "launchctl-calls.txt"
    return (
        "launchctl() {\n"
        f"  printf '%s\\n' \"$*\" >> {_sh(record)}\n"
        "  curl -sf --max-time 2 http://127.0.0.1:11435/bootstrap-marker >/dev/null 2>&1 || true\n"
        "  return 0\n"
        "}\n"
    )


def _canary_direct_launcher(tmp_path: Path) -> Path:
    """A canary Start_VOOL.sh in the harness's project root: if main() ever took the
    direct-start branch it would exec this and leave the marker behind (and exit 42)."""
    marker = tmp_path / "direct-start-ran.txt"
    start_script = tmp_path / "run1" / "project" / "Start_VOOL.sh"
    start_script.parent.mkdir(parents=True, exist_ok=True)
    start_script.write_text(f"#!/usr/bin/env bash\ntouch {_sh(marker)}\nexit 42\n", encoding="utf-8")
    start_script.chmod(0o755)
    return marker


def test_installer_start_verify_accepts_real_vool_health_and_rejects_foreign_services(
    tmp_path: Path,
) -> None:
    """--start with an installed supervisor VERIFIES the served runtime, against REAL network
    fixtures on the canonical port — not a body-discarding curl stub.

    Arms: the intended healthy runtime (verified, exit 0, no second bind, supervisor
    bootstrapped BEFORE the first health poll); an unrelated HTTP-200 service (refused with
    the honest error — the old status-only poll falsely declared it verified); intermittent
    health (consecutive-success reset, bounded failure exit 1) with the production loop and
    limits intact and only the test-side sleep doubled.
    """
    import sys

    # ---- arm 1: intended healthy runtime -------------------------------------------
    fixture = _VerifyFixtureServer(tmp_path / "fx1", "vool")
    try:
        marker = _canary_direct_launcher(tmp_path)
        runtime_home = tmp_path / "runtime1"
        result = _run_installer_main(
            tmp_path / "run1",
            real_python=sys.executable,
            agent_name="StartVerify",
            runtime_home=runtime_home,
            auto_start=True,
            launch_agent_path="",  # the OWNing setup boundary sets this (or not) below
            keep_functions=("install_macos_launch_agent",),
            override_functions=_supervisor_boundary_doubles(tmp_path / "run1")
            + "write_launcher() { :; }\n",
        )
        combined = result.stdout + result.stderr
        assert "command not found" not in combined, combined[-2000:]
        assert result.returncode == 0, combined[-2000:]
        assert "Launchd runtime verified" in result.stdout
        assert not marker.exists(), "the verify branch must not exec the direct launcher"
        paths = fixture.paths()
        assert "/bootstrap-marker" in paths, "the supervisor setup boundary must run"
        assert "/healthz" in paths and "/v1/models" in paths
        assert paths.index("/bootstrap-marker") < paths.index("/healthz"), (
            "the supervisor must be bootstrapped before --start verifies health"
        )
    finally:
        fixture.stop()

    # ---- arm 2: unrelated HTTP-200 service on the port ------------------------------
    fixture = _VerifyFixtureServer(tmp_path / "fx2", "foreign200")
    try:
        result = _run_installer_main(
            tmp_path / "run2",
            real_python=sys.executable,
            agent_name="StartForeign",
            runtime_home=tmp_path / "runtime2",
            auto_start=True,
            launch_agent_path="",
            keep_functions=("install_macos_launch_agent",),
            sleep_fast=True,
            override_functions=_supervisor_boundary_doubles(tmp_path / "run2")
            + "write_launcher() { :; }\n",
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 1, combined[-2000:]
        assert "Launchd runtime verified" not in result.stdout
        assert "did not stay verifiably healthy" in result.stdout
        assert fixture.paths().count("/healthz") >= 1
    finally:
        fixture.stop()

    # ---- arm 3: intermittent health — consecutive reset, bounded failure ------------
    fixture = _VerifyFixtureServer(tmp_path / "fx3", "intermittent")
    try:
        result = _run_installer_main(
            tmp_path / "run3",
            real_python=sys.executable,
            agent_name="StartFlaky",
            runtime_home=tmp_path / "runtime3",
            auto_start=True,
            launch_agent_path="",
            keep_functions=("install_macos_launch_agent",),
            sleep_fast=True,
            override_functions=_supervisor_boundary_doubles(tmp_path / "run3")
            + "write_launcher() { :; }\n",
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 1, combined[-2000:]
        assert "did not stay verifiably healthy" in result.stdout
        # Alternating 200/503 never reaches 5 consecutive successes: the counter reset.
        assert fixture.paths().count("/healthz") >= 5
    finally:
        fixture.stop()


def test_supervised_health_parser_refuses_non_object_payloads_cleanly() -> None:
    """The bounded parser cases for the --start identity check, executed directly.

    The poll's payload contract is ok=true plus a non-empty runtime.app_version. Valid
    source-style and packaged-style payloads pass; every invalid input -- ok=false,
    missing or empty version, non-VOOL objects, NON-OBJECT JSON bodies (lists, strings,
    numbers, booleans, null) and undecodable bytes -- must refuse with exit 1 and NO
    traceback: the parse outcome is a decision, not an exception. The non-object cases
    pin the repair for the old unguarded ``payload.get("ok")`` that crashed instead of
    refusing.
    """
    import re as _re
    import subprocess
    import sys

    script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    match = _re.search(r"supervised_health_is_vool\(\) \{.*?-c '(.*?)'\n\}", script, _re.S)
    assert match is not None, "the supervised_health_is_vool python payload check is missing"
    parser = match.group(1)

    def check(body: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", parser],
            input=body,
            capture_output=True,
            text=True,
            timeout=60,
        )

    accepting = [
        '{"ok": true, "agent": "a", "runtime": {"app_version": "0.6.0", "protocol_version": 1}}',
        '{"ok": true, "runtime": {"app_version": "0.6.0-dev+packaged"}}',
    ]
    for body in accepting:
        result = check(body)
        assert result.returncode == 0, (body, result.stderr)
        assert "Traceback" not in result.stderr

    refusing = [
        '{"ok": false, "runtime": {"app_version": "0.6.0"}}',
        '{"runtime": {"app_version": "0.6.0"}}',
        '{"ok": true, "runtime": {}}',
        '{"ok": true, "runtime": {"app_version": "   "}}',
        '{"ok": true, "runtime": {"app_version": ""}}',
        '{"ok": true, "runtime": "not-a-dict"}',
        '{"ok": true, "app_version": "0.6.0"}',
        '{"status": "ok", "service": "unrelated-demo-app"}',
        '["ok", {"app_version": "0.6.0"}]',
        '"healthy"',
        "42",
        "true",
        "null",
        "not json at all",
    ]
    for body in refusing:
        result = check(body)
        assert result.returncode == 1, (body, result.stdout, result.stderr)
        assert "Traceback" not in result.stderr, (body, result.stderr)


def test_installer_start_verify_reuse_and_home_identity_boundary(tmp_path: Path) -> None:
    """What supervised reuse proves — and what it deliberately does not.

    A healthy VOOL runtime already serving the canonical port is REUSED by --start (service
    identity verified through the served health payload; exit 0, no second bind). WHICH
    home owns that runtime is a different question, answered by the API's own per-home
    pidfile record (data/vool_api.pid — the authority doctor/stop read): the serving home
    carries a live pid; the newly installed home does not. --start verifies a VOOL-served
    runtime, not this home's runtime, and that boundary is what this test records.
    """
    import os
    import sys

    fixture = _VerifyFixtureServer(tmp_path / "fx", "vool")
    try:
        serving_home = tmp_path / "serving-home"
        (serving_home / "data").mkdir(parents=True)
        (serving_home / "data" / "vool_api.pid").write_text(str(os.getpid()), encoding="utf-8")
        installed_home = tmp_path / "installed-home"

        result = _run_installer_main(
            tmp_path / "run1",
            real_python=sys.executable,
            agent_name="StartReuse",
            runtime_home=installed_home,
            auto_start=True,
            launch_agent_path="",
            keep_functions=("install_macos_launch_agent",),
            sleep_fast=True,
            override_functions=_supervisor_boundary_doubles(tmp_path / "run1")
            + "write_launcher() { :; }\n",
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined[-2000:]
        assert "Launchd runtime verified" in result.stdout

        # The per-home pidfile contract distinguishes the serving runtime's home.
        serving_pid = int((serving_home / "data" / "vool_api.pid").read_text(encoding="utf-8").strip())
        assert serving_pid == os.getpid()  # a live process — exactly what doctor/stop check
        assert not (installed_home / "data" / "vool_api.pid").exists(), (
            "the newly installed home owns no running API; --start verified the served runtime's"
            " identity, and the per-home pidfile is the recorded boundary between homes"
        )
    finally:
        fixture.stop()


def test_installer_without_start_bootstraps_supervisor_but_does_not_verify(tmp_path: Path) -> None:
    """The supported no-start distinction, through the owning setup boundary.

    AUTO_START=0 does NOT mean "no service": on macOS the installer still installs and
    bootstraps the RunAtLoad/KeepAlive supervisor during the install steps (launchd may start
    the runtime at load). What --start adds is VERIFICATION (and the direct-launch fallback).
    With the REAL install_macos_launch_agent running under recorded launchctl doubles: the
    supervisor setup is invoked, the plist (Darwin) or XDG autostart entry (elsewhere) is
    written with the supervisor environment, and NO health verification poll runs.
    """
    import platform
    import sys

    fixture = _VerifyFixtureServer(tmp_path / "fx", "vool")
    try:
        runtime_home = tmp_path / "runtime1"
        # The REAL owning setup boundary runs. On Darwin that is install_macos_launch_agent
        # (launchctl doubled); elsewhere it chains into the real Linux keepalive installer,
        # whose systemctl probe is doubled to "no user bus" so it lands on the XDG autostart
        # fallback hermetically — no real service manager is contacted on any platform.
        keep = ("install_macos_launch_agent",)
        if platform.system() != "Darwin":
            keep = (*keep, "install_linux_keepalive_service", "install_linux_xdg_autostart")
        result = _run_installer_main(
            tmp_path / "run1",
            real_python=sys.executable,
            agent_name="NoStart",
            runtime_home=runtime_home,
            auto_start=False,
            launch_agent_path="",
            keep_functions=keep,
            override_functions=_supervisor_boundary_doubles(tmp_path / "run1")
            + "systemctl() { return 1; }\n"
            + "write_launcher() { :; }\n",
        )
        combined = result.stdout + result.stderr
        assert "command not found" not in combined, combined[-2000:]
        assert "HARNESS_MAIN_RC=0" in result.stdout, combined[-2000:]
        assert "Launching VOOL now" not in result.stdout, "no --start means no verification pass"

        launchctl_record = tmp_path / "run1" / "launchctl-calls.txt"
        isolated_home = tmp_path / "run1" / "isolated-home"
        if platform.system() == "Darwin":
            assert launchctl_record.exists(), "install must bootstrap the supervisor even without --start"
            plist = isolated_home / "Library" / "LaunchAgents" / "ai.vool.runtime.plist"
            assert plist.is_file(), "the launch agent plist must be written by the owning boundary"
            body = plist.read_text(encoding="utf-8")
            assert "<key>RunAtLoad</key>" in body and "<key>KeepAlive</key>" in body
            assert "<key>VOOL_LAUNCHD_SUPERVISOR</key>" in body
            assert str(runtime_home) in body or str(tmp_path / "runtime1") in body
        else:
            # No systemd user bus in the harness environment: the owning boundary falls back
            # to the XDG autostart entry with the same supervisor environment.
            autostart = isolated_home / ".config" / "autostart" / "vool-runtime.desktop"
            assert autostart.is_file(), "the supervisor autostart entry must be written"
            body = autostart.read_text(encoding="utf-8")
            assert "VOOL_LAUNCHD_SUPERVISOR=1" in body

        # No verification ran: the fixture saw no health poll (only the doubles' markers,
        # which the launchctl double only emits when it is invoked — and only on Darwin).
        paths = fixture.paths()
        assert "/healthz" not in paths, "without --start the installer must not poll health"
    finally:
        fixture.stop()


def test_installer_start_flag_without_supervisor_runs_direct_launcher(tmp_path: Path) -> None:
    """Without a supervisor (no launch agent installed), --start execs Start_VOOL.sh.

    That direct path is where a busy canonical port surfaces the API server's own
    bind-failure exit (3, reproduced live against a healthy running instance): install
    succeeded, the start reports the bind failure without disturbing the running server.
    """
    import sys

    # The harness derives PROJECT_ROOT as <run-dir>/project; the canary launcher lives there.
    project_root = tmp_path / "run1" / "project"
    marker = tmp_path / "direct-start-ran.txt"
    start_script = project_root / "Start_VOOL.sh"
    start_script.parent.mkdir(parents=True, exist_ok=True)
    start_script.write_text(
        f'#!/usr/bin/env bash\ntouch "{marker}"\nexit 42\n',
        encoding="utf-8",
    )
    start_script.chmod(0o755)

    runtime_home = tmp_path / "runtime"
    result = _run_installer_main(
        tmp_path / "run1",
        real_python=sys.executable,
        agent_name="StartDirect",
        runtime_home=runtime_home,
        auto_start=True,
        launch_agent_path="",
        override_functions="write_launcher() { :; }",
    )

    # main() exec's the launcher, so the harness's own rc line is gone; the launcher's
    # distinctive exit code and marker are the proof the direct branch ran.
    assert result.returncode == 42, (result.stdout + result.stderr)[-2000:]
    assert marker.exists()


def test_pull_models_skips_cleanly_when_ollama_unavailable(tmp_path: Path) -> None:
    """Missing/offline model backend at install time: the pull step skips with a warning
    instead of failing the install or silently pretending models were provisioned.

    The non-execution proof is a REAL boundary spy: a recording fake Ollama exists on disk
    for both arms. In the control arm (available backend) the same spy provably records the
    model operations, so its silence in the unavailable arm is evidence, not an accident of
    a path nothing would ever write.
    """
    import subprocess
    import sys

    from tests.platform_helpers import bash_script_args

    installer_script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    prefix, marker_line, _ = installer_script.partition('\nparse_args "$@"\n')
    assert marker_line

    # The spy: a real executable that records every model operation it is asked for.
    fake_ollama = tmp_path / "ollama-spy"
    record = tmp_path / "recorded.txt"
    fake_ollama.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s %s\\n" "$1" "${{2:-}}" >> {_sh(record)}\n'
        'if [[ "$1" == "list" ]]; then exit 0; fi\n'  # nothing installed: every model pulls
        "exit 0\n",
        encoding="utf-8",
    )
    fake_ollama.chmod(0o755)

    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text(
        f"#!/usr/bin/env bash\nexec {_sh(sys.executable)} \"$@\"\n",
        encoding="utf-8",
    )
    (venv_bin / "python").chmod(0o755)

    def run_pull(ollama_exe: str, run_dir: Path) -> subprocess.CompletedProcess[str]:
        runtime_home = run_dir / "runtime"
        runtime_home.mkdir(parents=True, exist_ok=True)
        harness = run_dir / "run_pull.sh"
        harness.write_text(
            prefix
            + "\n"
            + "\n".join(
                [
                    f"PROJECT_ROOT={_sh(PROJECT_ROOT)}",
                    f"SCRIPT_DIR={_sh(PROJECT_ROOT / 'installer')}",
                    f"VENV_DIR={_sh(tmp_path / '.venv')}",
                    f"pull_models {_sh(ollama_exe)} 'local-only' 'qwen3:8b' {_sh(runtime_home)}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.run(
            bash_script_args(harness),
            capture_output=True,
            text=True,
            cwd=tmp_path,
            timeout=120,
        )

    # Control arm — available backend: the spy must catch the attempted model operations.
    control = run_pull(str(fake_ollama), tmp_path / "control")
    assert control.returncode == 0, control.stdout + control.stderr
    recorded = [line for line in record.read_text(encoding="utf-8").splitlines() if line.startswith("pull ")]
    assert recorded, "control arm: the spy must prove it records model operations"
    assert any(model.startswith("qwen") for model in (r.split(" ", 1)[1] for r in recorded))

    # Unavailable arm — empty backend executable: honest skip, and the spy stays silent.
    record.unlink()
    unavailable = run_pull("", tmp_path / "unavailable")
    assert unavailable.returncode == 0, unavailable.stdout + unavailable.stderr
    assert "Model pull skipped because Ollama is unavailable" in unavailable.stdout
    assert "Downloading" not in unavailable.stdout
    assert not record.exists(), (
        "no model operation may run without a backend — the spy (proven active in the"
        " control arm) recorded nothing"
    )


def test_pull_models_provisions_native_embedding_lane_with_recording_ollama(tmp_path: Path) -> None:
    """Executed pull_models proof against a recording fake Ollama (no real pulls).

    Fresh native installs must provision the semantic-memory embedding model through the
    core local-model policy authority — the pull that used to hide behind the retired
    OpenClaw gate. The fake Ollama records `list` and `pull` invocations so the requested
    model set is asserted without downloading anything.
    """
    import subprocess

    from tests.platform_helpers import bash_script_args

    fake_ollama = tmp_path / "ollama"
    record = tmp_path / "recorded.txt"
    fake_ollama.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s %s\\n" "$1" "${2:-}" >> ' + f'"{record}"\n'
        'if [[ "$1" == "list" ]]; then exit 0; fi\n'  # nothing installed: every model pulls
        "exit 0\n",
        encoding="utf-8",
    )
    fake_ollama.chmod(0o755)

    installer_script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    prefix, marker, _ = installer_script.partition('\nparse_args "$@"\n')
    assert marker

    # The embedding shim resolves the core authority through the venv python, so give the
    # harness a wrapper that runs the real interpreter.
    import sys

    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text(
        f"#!/usr/bin/env bash\nexec {_sh(sys.executable)} \"$@\"\n",
        encoding="utf-8",
    )
    (venv_bin / "python").chmod(0o755)

    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    harness = tmp_path / "run_pull.sh"
    harness.write_text(
        prefix
        + "\n"
        + "\n".join(
            [
                f"PROJECT_ROOT={_sh(PROJECT_ROOT)}",
                f"SCRIPT_DIR={_sh(PROJECT_ROOT / 'installer')}",
                f"VENV_DIR={_sh(tmp_path / '.venv')}",
                f"pull_models {_sh(fake_ollama)} 'local-only' 'qwen3:8b' {_sh(runtime_home)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        bash_script_args(harness),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    requested = [line.split(" ", 1)[1].strip() for line in record.read_text(encoding="utf-8").splitlines() if line.startswith("pull ")]
    assert "nomic-embed-text" in requested, f"native embedding lane not provisioned: {requested}"
    assert any(model.startswith("qwen") for model in requested), f"chat models missing from pull: {requested}"
