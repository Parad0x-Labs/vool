from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _setuptools_include_patterns() -> list[str]:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"\[tool\.setuptools\.packages\.find\]\s+include = \[(.*?)\]", pyproject, re.S)
    assert match is not None, "setuptools package discovery include list missing from pyproject.toml"
    return ast.literal_eval(f"[{match.group(1)}]")


def test_pyproject_package_discovery_lists_runtime_package_roots() -> None:
    include = set(_setuptools_include_patterns())
    model_registry = (REPO_ROOT / "core" / "model_registry.py").read_text(encoding="utf-8")
    tool_executor = (REPO_ROOT / "core" / "tool_intent_executor.py").read_text(encoding="utf-8")
    channel_actions = (REPO_ROOT / "core" / "channel_actions.py").read_text(encoding="utf-8")
    onboarding = (REPO_ROOT / "core" / "onboarding.py").read_text(encoding="utf-8")

    assert "adapters*" in include
    assert "tools*" in include
    assert "relay*" in include
    assert "installer*" in include
    assert (REPO_ROOT / "adapters" / "__init__.py").exists()
    assert (REPO_ROOT / "tools" / "__init__.py").exists()
    assert (REPO_ROOT / "relay" / "__init__.py").exists()
    assert (REPO_ROOT / "relay" / "bridge_workers" / "__init__.py").exists()
    assert (REPO_ROOT / "installer" / "__init__.py").exists()
    assert "from adapters." in model_registry
    assert "from tools.registry" in tool_executor
    assert "from relay." in channel_actions
    assert "from installer.register_openclaw_agent import register" not in onboarding
    assert "openclaw" not in onboarding.lower().replace("openclaw_registration", "")


def test_pyproject_runtime_extra_covers_installer_runtime_surface() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for marker in (
        "runtime = [",
        '"openai>=1.0"',
        '"anthropic>=0.18"',
        '"sentence-transformers>=2.2"',
        '"torch>=2.13.0"',
        '"transformers>=4.48"',
        '"playwright>=1.52,<2.0"',
        '"zstandard>=0.22.0"',
        '"xxhash>=3.4.0"',
    ):
        assert marker in pyproject


def test_pyproject_dev_extra_covers_build_and_test_tooling() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # Pinned, not floored. `[tool.ruff.lint] select` lists rule FAMILIES, so a floor like
    # ">=0.3" lets a new ruff enrol rules nobody opted into and turn main red with no code
    # change. The pin's VERSION lives only in pyproject -- the CI lint job and the
    # verification gate read it from there, so a bump lands in one place; the shape below
    # asserts it stays an exact pin.
    ruff_pin = re.search(r'"ruff==[0-9][0-9A-Za-z.\-]*"', pyproject)
    assert ruff_pin is not None, "ruff must stay exactly pinned in the dev extra, not floored"

    for marker in (
        "dev = [",
        '"build>=1.2"',
        '"pytest>=7.0"',
        ruff_pin.group(0),
        '"mypy>=1.8"',
        # tests/test_daemon_survives_concurrent_load.py imports httpx. It was never declared, so it
        # passed on machines that happened to have it and died in CI with ModuleNotFoundError on
        # every run. A test dependency that only exists on someone's laptop is not declared.
        '"httpx>=0.27"',
    ):
        assert marker in pyproject


def test_container_and_docs_share_api_healthz_contract() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    install_doc = (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    control_plane_doc = (REPO_ROOT / "docs" / "CONTROL_PLANE.md").read_text(encoding="utf-8")
    api_server = (REPO_ROOT / "apps" / "vool_api_server.py").read_text(encoding="utf-8")
    api_service = (REPO_ROOT / "core" / "web" / "api" / "service.py").read_text(encoding="utf-8")

    assert "http://localhost:11435/healthz" in dockerfile
    assert "http://127.0.0.1:11435/healthz" in install_doc
    assert "GET /healthz" in control_plane_doc
    assert "create_api_app" in api_server
    assert '"/healthz"' in api_service
    assert '"/v1/healthz"' in api_service


def test_installers_use_module_entrypoints_and_runtime_extra_without_pythonpath_hacks() -> None:
    sh_installer = (REPO_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    bat_installer = (REPO_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")

    assert 'pip install "${PROJECT_ROOT}[runtime,proof]"' in sh_installer
    assert 'pip install "%PROJECT_ROOT%[runtime,proof]"' in bat_installer
    assert "-m storage.migrations" in sh_installer
    assert "-m storage.migrations" in bat_installer
    assert "-m ops.ensure_public_hive_auth" in sh_installer
    assert "-m ops.ensure_public_hive_auth" in bat_installer
    assert "PYTHONPATH" not in sh_installer
    assert "PYTHONPATH" not in bat_installer
    assert "ops/ensure_public_hive_auth.py" not in sh_installer
    assert "ops\\ensure_public_hive_auth.py" not in bat_installer
    assert (REPO_ROOT / "ops" / "ensure_public_hive_auth.py").exists()


def test_install_doc_exposes_explicit_public_hive_auth_hydration_step() -> None:
    install_doc = (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")

    assert "python -m ops.ensure_public_hive_auth" in install_doc
    assert "VOOL_PUBLIC_HIVE_WATCH_HOST" in install_doc
    assert "VOOL_PUBLIC_HIVE_REMOTE_CONFIG" in install_doc


def test_do_ip_first_cluster_pack_is_shipped_with_direct_ip_runtime_defaults() -> None:
    cluster_root = REPO_ROOT / "config" / "meet_clusters" / "do_ip_first_4node"
    assert (cluster_root / "README.md").exists()
    assert (cluster_root / "cluster_manifest.json").exists()
    assert (cluster_root / "watch-edge-1.json").exists()

    agent_bootstrap = json.loads((cluster_root / "agent-bootstrap.sample.json").read_text(encoding="utf-8"))
    watch_edge = json.loads((cluster_root / "watch-edge-1.json").read_text(encoding="utf-8"))

    assert agent_bootstrap["meet_seed_urls"] == [
        "https://203.0.113.11:8766",
        "https://203.0.113.12:8766",
        "https://203.0.113.13:8766",
    ]
    assert agent_bootstrap["tls_insecure_skip_verify"] is True
    assert watch_edge["public_url"] == "https://203.0.113.14:8788"
    assert watch_edge["upstream_base_urls"] == [
        "https://203.0.113.11:8766",
        "https://203.0.113.12:8766",
        "https://203.0.113.13:8766",
    ]
    assert watch_edge["tls_insecure_skip_verify"] is True
    assert not str(watch_edge.get("auth_token") or "").strip()


def test_installers_derive_profile_truth_from_runtime_provider_snapshot() -> None:
    sh_installer = (REPO_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    bat_installer = (REPO_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")

    assert "from core.runtime_backbone import build_provider_registry_snapshot" in sh_installer
    assert "provider_capability_truth=snapshot.capability_truth" in sh_installer
    assert "from core.runtime_backbone import build_provider_registry_snapshot" in bat_installer
    assert "provider_capability_truth=snapshot.capability_truth" in bat_installer


def test_bootstrap_scripts_support_checksum_verification_and_docs_do_not_pipe_remote_scripts() -> None:
    sh_bootstrap = (REPO_ROOT / "installer" / "bootstrap_vool.sh").read_text(encoding="utf-8")
    ps_bootstrap = (REPO_ROOT / "installer" / "bootstrap_vool.ps1").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    install_doc = (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")

    assert "--sha256" in sh_bootstrap
    assert "VOOL_ARCHIVE_SHA256" in sh_bootstrap
    assert "sha256sum" in sh_bootstrap or "shasum" in sh_bootstrap
    assert "Archive checksum verified." in sh_bootstrap

    assert "ArchiveSha256" in ps_bootstrap
    assert "VOOL_ARCHIVE_SHA256" in ps_bootstrap
    assert "Get-FileHash -Algorithm SHA256" in ps_bootstrap
    assert "Archive checksum verified." in ps_bootstrap

    assert "| bash" not in readme
    assert "| iex" not in readme
    assert "| bash" not in install_doc
    assert "| iex" not in install_doc
    assert "curl -fsSLo bootstrap_vool.sh" in readme
    assert "Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1" in readme
    assert "curl -fsSLo bootstrap_vool.sh" in install_doc
    assert "Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1" in install_doc


def test_install_profile_selection_is_available_across_bootstrap_and_installer_surfaces() -> None:
    sh_installer = (REPO_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    bat_installer = (REPO_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")
    sh_bootstrap = (REPO_ROOT / "installer" / "bootstrap_vool.sh").read_text(encoding="utf-8")
    ps_bootstrap = (REPO_ROOT / "installer" / "bootstrap_vool.ps1").read_text(encoding="utf-8")
    ps_launcher = (REPO_ROOT / "Install_And_Run_VOOL.ps1").read_text(encoding="utf-8")
    ps_one_click = (REPO_ROOT / "installer" / "windows_one_click.ps1").read_text(encoding="utf-8")
    ps_package = (REPO_ROOT / "installer" / "build_windows_package.ps1").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    install_doc = (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")

    assert "--install-profile <profile>" in sh_installer
    assert "/INSTALLPROFILE=ID" in bat_installer
    assert "--install-profile <id>" in sh_bootstrap
    assert '-InstallProfile local-max' in install_doc
    assert '/INSTALLPROFILE=$InstallProfile' in ps_bootstrap
    assert "Install_And_Run_VOOL.ps1" in ps_bootstrap
    assert "-AutoYes" in ps_bootstrap
    assert "installer\\windows_one_click.ps1" in ps_launcher
    assert '$forward["SkipBenchmark"] = $true' in ps_launcher
    assert "System.Windows.Forms" in ps_one_click
    assert "Probe PC" in ps_one_click
    assert "Run live local model check after install" in ps_one_click
    assert "--benchmark --benchmark-timeout 240" in ps_one_click
    assert "$SkipBenchmark" in ps_one_click
    assert "$env:VOOL_INSTALL_PROFILE = $batchProfile" in ps_one_click
    assert "$env:VOOL_HEADLESS = \"1\"" in ps_one_click
    assert "$env:VOOL_HOME = $VoolHome" in ps_one_click
    assert "Set-AuthenticodeSignature" in ps_package
    assert "VOOL_WINDOWS_SIGNING_CERT_THUMBPRINT" in ps_package
    assert "Get-FileHash -Algorithm SHA256" in ps_package
    assert "schema = \"vool.windows_package.v1\"" in ps_package
    assert 'Get-GitLines @("ls-files")' in ps_package
    assert "Staged Windows package is missing Install_And_Run_VOOL.ps1" in ps_package
    assert "refusing to create an incomplete package" in ps_package
    assert "powershell -ExecutionPolicy Bypass -File .\\Install_And_Run_VOOL.ps1" in install_doc
    assert "installer\\build_windows_package.ps1" in install_doc
    assert "install-profile --set ollama-only" in sh_installer
    assert "install-profile --set ollama-max" in sh_installer
    assert "install-profile --set local-max" in readme
    assert "--install-profile local-only" in readme
    assert "install-profile --set ollama-only" in install_doc
    assert "install-profile --set ollama-max" in install_doc
    assert "ollama-only" in sh_bootstrap
    assert "ollama-max" in sh_bootstrap
    assert "detect_install_profile_display" in sh_installer
    assert "Recommended profile: ${recommended_install_profile_display}" in sh_installer
    assert "Install profile: ${install_profile_display}" in sh_installer
    assert "from core.install_recommendations import build_install_recommendation_truth" in bat_installer
    assert "from core.model_store_planner import DEFAULT_OPENCLAW_MEMORY_MODEL, build_model_store_drive_plan" in bat_installer
    assert "Recommended Ollama model store: %OLLAMA_MODELS_DIR%" in bat_installer
    assert "RECOMMENDED_BUNDLE_MODELS" in bat_installer
    assert "set \"MODELS_TO_PULL_LIST=%MODELS_TO_PULL:,= %\"" in bat_installer
    assert "for %%M in (%MODELS_TO_PULL_LIST%) do" in bat_installer
    # local_plus_llamacpp is a provider stack_id, not a valid --install-profile value; the
    # README must not advertise it as one (the real public profiles are auto-recommended /
    # local-only / local-max). A first-class llama.cpp install profile is separate future work.
    assert "local_plus_llamacpp" not in readme
    assert "first-class installer/runtime lane yet" not in readme


def test_windows_retired_openclaw_launcher_is_a_side_effect_free_stub() -> None:
    launcher = (REPO_ROOT / "OpenClaw_VOOL.bat").read_text(encoding="utf-8")
    runtime = (REPO_ROOT / "core" / "web" / "api" / "runtime.py").read_text(encoding="utf-8")
    start_launcher_native = (REPO_ROOT / "Start_VOOL.bat").read_text(encoding="utf-8")

    # The stub refuses honestly and points at native startup + the separate skills repo.
    assert "retired from VOOL" in launcher
    assert "Start_VOOL.bat" in launcher
    assert "Open_Chat.bat" in launcher
    assert "Talk_To_VOOL.bat" in launcher
    assert "Open_Web0.bat" in launcher
    assert "https://github.com/Parad0x-Labs/openclaw-skills" in launcher
    assert "exit /b 1" in launcher
    # No third-party discovery, registration, UI patching, or gateway startup remains.
    assert "where openclaw" not in launcher
    assert "register_openclaw_agent.py" not in launcher
    assert "inject_openclaw_web0_pill.py" not in launcher
    assert "patch_openclaw_session_retry.py" not in launcher
    assert "openclaw_locator" not in launcher
    assert "18789" not in launcher
    assert "schtasks" not in launcher
    # The native receipt-model resolution the retired launcher used to carry still holds
    # on the native start path (receipt model honored unless explicitly overridden).
    assert 'if not "!RECEIPT_MODEL!"=="" if not "%VOOL_ALLOW_MODEL_ENV_OVERRIDE%"=="1" set "VOOL_OLLAMA_MODEL=!RECEIPT_MODEL!"' in start_launcher_native
    assert 'if "%VOOL_OLLAMA_MODEL%"=="" if not "!RECEIPT_MODEL!"=="" set "VOOL_OLLAMA_MODEL=!RECEIPT_MODEL!"' in start_launcher_native
    assert 'set "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS=1"' in start_launcher_native
    assert 'for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"' in start_launcher_native
    background_cmd = (REPO_ROOT / "vool_background.cmd").read_text(encoding="utf-8")
    assert "goto run" in background_cmd
    assert "http://127.0.0.1:11435/healthz" in background_cmd
    assert 'for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"' in background_cmd
    assert '--cwd "%SCRIPT_ROOT%"' in background_cmd
    assert "installer\\start_windows_detached.py" in background_cmd
    assert "VOOL API detached start requested" in background_cmd
    assert "vool_api_child.log" in background_cmd
    assert "vool_api_child.err.log" in background_cmd
    assert "call \"%SCRIPT_DIR%Start_VOOL.bat\"" not in background_cmd
    assert "BeginConnect('127.0.0.1', 11435" in background_cmd
    assert 'start "VOOL API" /MIN' not in background_cmd
    background_vbs = (REPO_ROOT / "vool_background.vbs").read_text(encoding="utf-8")
    assert "\\vool_background.cmd" in background_vbs
    assert "\\Start_VOOL.bat" not in background_vbs
    start_launcher = (REPO_ROOT / "Start_VOOL.bat").read_text(encoding="utf-8")
    assert 'set "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS=1"' in start_launcher
    install_bat = (REPO_ROOT / "installer" / "install_vool.bat").read_text(encoding="utf-8")
    assert 'setx VOOL_REGISTER_INSTALLED_OLLAMA_MODELS "1"' in install_bat
    assert "VOOL_ENABLE_WINDOWS_COMPUTE_MODE" in runtime
    assert "Adaptive compute mode daemon disabled" in runtime
    assert "VOOL_ENABLE_WINDOWS_MESH_DAEMON" in runtime
    assert "Mesh daemon disabled" in runtime
    assert "VOOL_OPENCLAW_GATEWAY_PORT" not in launcher


def test_windows_stub_launcher_executes_as_refusal_from_path_with_spaces(tmp_path: Path) -> None:
    """EXECUTED on a Windows host: the retired OpenClaw launcher must refuse (exit 1,
    honest notice) with zero side effects on an unrelated third-party config, including
    when invoked through cmd.exe from a project path that contains spaces.

    On non-Windows platforms cmd.exe cannot execute a .bat, so this case is a plain
    PENDING-PLATFORM skip -- never a local pass. DELIVERY runs it through the Windows
    fresh-host gauntlet (Test_VOOL_Windows_Gauntlet.cmd), whose focused regression
    selection includes this file.
    """
    import os
    import shutil
    import subprocess
    import sys

    if sys.platform != "win32":
        import pytest

        pytest.skip("cmd.exe batch execution requires a Windows host (Windows fresh-host gauntlet)")

    run_dir = tmp_path / "Vool Space Project"
    run_dir.mkdir()
    shutil.copyfile(REPO_ROOT / "OpenClaw_VOOL.bat", run_dir / "OpenClaw_VOOL.bat")

    fake_home = tmp_path / "isolated-home"
    openclaw_dir = fake_home / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    unrelated_config = openclaw_dir / "openclaw.json"
    unrelated_before = '{"model": "unrelated", "reserveTokensFloor": 99000}'
    unrelated_config.write_text(unrelated_before, encoding="utf-8")

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(run_dir / "OpenClaw_VOOL.bat")],
        capture_output=True,
        text=True,
        env={**os.environ, "USERPROFILE": str(fake_home)},
        timeout=60,
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 1, combined
    assert "retired from VOOL" in combined
    assert unrelated_config.read_text(encoding="utf-8") == unrelated_before, (
        "the refusal stub must leave unrelated third-party config byte-for-byte intact"
    )


def test_open_chat_bat_opens_through_the_powershell_boundary() -> None:
    """The chat launcher's whole external-command surface is one contract: powershell.

    The browser open rides the same powershell boundary the health checks already use, so
    the executed Windows cases below can isolate and record every external effect. On a
    real machine Start-Process with a URL opens the default browser exactly like the old
    `start ""` did.
    """
    launcher = (REPO_ROOT / "Open_Chat.bat").read_text(encoding="utf-8")
    assert 'powershell -NoProfile -Command "Start-Process \'%CHAT_URL%\'"' in launcher
    assert 'start "" "%CHAT_URL%"' not in launcher
    # The health/startup contract is unchanged.
    assert "schtasks /query /tn \"VOOL_Daemon\"" in launcher
    assert "vool_background.vbs" in launcher
    assert "http://127.0.0.1:11435/healthz" in launcher


_WINDOWS_DOUBLE_PY = r'''
import json, re, sys
from pathlib import Path

kind = sys.argv[1]
args = " ".join(sys.argv[2:])
base = Path(sys.argv[3])
entry = {"kind": kind, "args": args}
code = 0
if kind == "powershell":
    if "Invoke-WebRequest" in args:
        mode = (base / "health-mode.txt").read_text(encoding="utf-8").strip()
        if mode.startswith("flaky:"):
            need = int(mode.split(":", 1)[1])
            counter_file = base / "health-counter.txt"
            calls = int(counter_file.read_text(encoding="utf-8") or 0) + 1 if counter_file.exists() else 1
            counter_file.write_text(str(calls), encoding="utf-8")
            code = 0 if calls > need else 1
        else:
            code = 0 if mode == "healthy" else 1
        entry["health"] = bool(code == 0)
    elif "Start-Sleep" in args:
        entry["sleep"] = True
    elif "Start-Process" in args:
        match = re.search(r"Start-Process '([^']+)'", args)
        entry["opened_url"] = match.group(1) if match else ""
    else:
        code = 2
elif kind == "schtasks":
    mode = (base / "schtasks-mode.txt").read_text(encoding="utf-8").strip()
    code = 0 if mode == "ok" else 1
    entry["subcommand"] = "/run" if "/run" in args else ("/query" if "/query" in args else "?")
else:
    code = 2
with open(base / ("calls-" + kind + ".jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps(entry) + "\n")
raise SystemExit(code)
'''


def _open_chat_bat_fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    """Isolated cmd.exe execution rig for the REAL Open_Chat.bat: a project directory with
    spaces, an isolated USERPROFILE with a planted unrelated config, and recorded
    powershell/schtasks doubles (.cmd shims resolved ahead of the real executables through
    PATH). No real scheduled task, powershell, script host or browser is ever invoked."""
    import os
    import shutil
    import sys

    run_dir = tmp_path / "Vool Space Project"
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "Open_Chat.bat", run_dir / "Open_Chat.bat")

    doubles = tmp_path / "doubles"
    doubles.mkdir(parents=True, exist_ok=True)
    (doubles / "health-mode.txt").write_text("healthy", encoding="utf-8")
    (doubles / "schtasks-mode.txt").write_text("ok", encoding="utf-8")
    double_py = doubles / "cmd_double.py"
    double_py.write_text(_WINDOWS_DOUBLE_PY, encoding="utf-8")
    for name in ("powershell", "schtasks"):
        shim = doubles / (name + ".cmd")
        shim.write_text(
            f'@"{sys.executable}" "{double_py}" {name} "{doubles}" %*\n',
            encoding="utf-8",
        )

    fake_home = tmp_path / "isolated-home"
    openclaw_dir = fake_home / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    unrelated_config = openclaw_dir / "openclaw.json"
    unrelated_config.write_text('{"model": "unrelated", "reserveTokensFloor": 99000}', encoding="utf-8")

    env = {
        **os.environ,
        "PATH": str(doubles) + os.pathsep + os.environ.get("PATH", ""),
        "USERPROFILE": str(fake_home),
    }
    return run_dir, doubles, unrelated_config, env


def _run_open_chat_bat(run_dir: Path, env: dict, timeout: int = 120):
    import subprocess

    return subprocess.run(
        ["cmd.exe", "/d", "/c", str(run_dir / "Open_Chat.bat")],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _win32_or_skip() -> None:
    import sys

    if sys.platform != "win32":
        import pytest

        pytest.skip("cmd.exe batch execution requires a Windows host (Windows fresh-host gauntlet)")


def _calls(doubles: Path, kind: str) -> list[dict]:
    import json

    path = doubles / f"calls-{kind}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_open_chat_bat_executed_healthy_opens_chat_without_starting_anything(tmp_path: Path) -> None:
    """EXECUTED on Windows: an already-healthy runtime opens the chat page through the
    powershell boundary with zero schtasks/startup activity, from a path with spaces,
    leaving unrelated third-party config byte-for-byte intact."""
    _win32_or_skip()
    run_dir, doubles, config, env = _open_chat_bat_fixture(tmp_path)
    (doubles / "health-mode.txt").write_text("healthy", encoding="utf-8")

    result = _run_open_chat_bat(run_dir, env)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "Chat opened at http://127.0.0.1:11435/chat" in combined
    ps = _calls(doubles, "powershell")
    assert ps and ps[0].get("health") is True, "the healthy arm must check health first"
    assert any(entry.get("opened_url") == "http://127.0.0.1:11435/chat" for entry in ps)
    assert _calls(doubles, "schtasks") == [], "a healthy runtime must not be restarted"
    assert config.read_text(encoding="utf-8") == '{"model": "unrelated", "reserveTokensFloor": 99000}'


def test_open_chat_bat_executed_missing_installation_refuses_honestly(tmp_path: Path) -> None:
    """EXECUTED on Windows: health down, no scheduled task, no background launcher in the
    project directory -> the honest not-installed refusal, exit 1, nothing opened."""
    _win32_or_skip()
    run_dir, doubles, config, env = _open_chat_bat_fixture(tmp_path)
    (doubles / "health-mode.txt").write_text("unhealthy", encoding="utf-8")
    (doubles / "schtasks-mode.txt").write_text("fail", encoding="utf-8")

    result = _run_open_chat_bat(run_dir, env)
    combined = result.stdout + result.stderr
    assert result.returncode == 1, combined
    assert "ERROR: VOOL is not installed yet. Run installer\\install_vool.bat first." in combined
    tasks = _calls(doubles, "schtasks")
    assert [e.get("subcommand") for e in tasks] == ["/query"], "a missing task must not be run"
    assert not any("opened_url" in e for e in _calls(doubles, "powershell"))
    assert config.read_text(encoding="utf-8") == '{"model": "unrelated", "reserveTokensFloor": 99000}'


def test_open_chat_bat_executed_startup_success_opens_after_becoming_healthy(tmp_path: Path) -> None:
    """EXECUTED on Windows: health down, the scheduled task runs, health becomes healthy
    during the poll -> chat opened, exit 0."""
    _win32_or_skip()
    run_dir, doubles, config, env = _open_chat_bat_fixture(tmp_path)
    (doubles / "health-mode.txt").write_text("flaky:2", encoding="utf-8")
    (doubles / "schtasks-mode.txt").write_text("ok", encoding="utf-8")

    result = _run_open_chat_bat(run_dir, env)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "Starting VOOL..." in combined
    assert "Chat opened at http://127.0.0.1:11435/chat" in combined
    assert [e.get("subcommand") for e in _calls(doubles, "schtasks")] == ["/query", "/run"]
    health_sequence = [e["health"] for e in _calls(doubles, "powershell") if "health" in e]
    assert health_sequence[:1] == [False], "startup begins from an unhealthy port"
    assert health_sequence[-1] is True
    assert config.read_text(encoding="utf-8") == '{"model": "unrelated", "reserveTokensFloor": 99000}'


def test_open_chat_bat_executed_startup_failure_reports_bounded_error(tmp_path: Path) -> None:
    """EXECUTED on Windows: the task runs but health never becomes healthy -> the bounded
    poll exhausts (120 iterations against the instant doubles), the honest error, exit 1,
    and nothing is opened."""
    _win32_or_skip()
    run_dir, doubles, config, env = _open_chat_bat_fixture(tmp_path)
    (doubles / "health-mode.txt").write_text("unhealthy", encoding="utf-8")
    (doubles / "schtasks-mode.txt").write_text("ok", encoding="utf-8")

    # The bounded poll spawns 240 double processes (sleep + health per iteration); give the
    # slowest gauntlet host headroom without touching the launcher's own bounds.
    result = _run_open_chat_bat(run_dir, env, timeout=300)
    combined = result.stdout + result.stderr
    assert result.returncode == 1, combined
    assert "ERROR: VOOL API did not become healthy on http://127.0.0.1:11435/healthz." in combined
    ps = _calls(doubles, "powershell")
    health_calls = [e for e in ps if "health" in e]
    assert len(health_calls) == 1 + 120, "the initial check plus the full bounded poll"
    sleeps = [e for e in ps if e.get("sleep")]
    assert len(sleeps) == 120
    assert not any("opened_url" in e for e in ps)
    assert config.read_text(encoding="utf-8") == '{"model": "unrelated", "reserveTokensFloor": 99000}'
