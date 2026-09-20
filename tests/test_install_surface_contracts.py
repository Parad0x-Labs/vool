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
    assert "from installer.register_openclaw_agent import register" in onboarding


def test_pyproject_runtime_extra_covers_installer_runtime_surface() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for marker in (
        "runtime = [",
        '"openai>=1.0"',
        '"anthropic>=0.18"',
        '"sentence-transformers>=2.2"',
        '"torch>=2.5"',
        '"transformers>=4.48"',
        '"playwright>=1.52,<2.0"',
        '"zstandard>=0.22.0"',
        '"xxhash>=3.4.0"',
    ):
        assert marker in pyproject


def test_pyproject_dev_extra_covers_build_and_test_tooling() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for marker in (
        "dev = [",
        '"build>=1.2"',
        '"pytest>=7.0"',
        # Pinned, not floored. `[tool.ruff.lint] select` lists rule FAMILIES, so a floor like
        # ">=0.3" lets a new ruff enrol rules nobody opted into and turn main red with no code
        # change. This exact string is also what the CI lint job installs -- the two must agree,
        # so bump them together.
        '"ruff==0.16.7"',
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


def test_windows_openclaw_launcher_uses_receipt_model_unless_explicitly_overridden() -> None:
    launcher = (REPO_ROOT / "OpenClaw_VOOL.bat").read_text(encoding="utf-8")
    runtime = (REPO_ROOT / "core" / "web" / "api" / "runtime.py").read_text(encoding="utf-8")

    assert 'if not "%VOOL_ALLOW_MODEL_ENV_OVERRIDE%"=="1" set "VOOL_OLLAMA_MODEL=%MODEL_TAG%"' in launcher
    assert 'if "%VOOL_OLLAMA_MODEL%"=="" set "VOOL_OLLAMA_MODEL=%MODEL_TAG%"' in launcher
    assert 'if not "%VOOL_OLLAMA_MODEL%"=="" set "MODEL_TAG=%VOOL_OLLAMA_MODEL%"' in launcher
    assert "where openclaw.cmd" in launcher
    assert "where openclaw.exe" in launcher
    assert 'set "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS=1"' in launcher
    assert 'for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"' in launcher
    assert 'register_openclaw_agent.py" "%SCRIPT_ROOT%" "%VOOL_HOME%" "%MODEL_TAG%" "%DISPLAY_NAME%"' in launcher
    assert "http://127.0.0.1:11435/healthz" in launcher
    assert "installer\\start_windows_detached.py" in launcher
    assert "vool_background.vbs" in launcher
    assert 'schtasks /run /tn "VOOL_Daemon"' in launcher
    assert "%SystemRoot%\\System32\\wscript.exe" in launcher
    assert "%SCRIPT_ROOT%\\vool_background.vbs" in launcher
    assert 'start "VOOL API" /MIN' not in launcher
    assert '--cwd "%SCRIPT_ROOT%"' in launcher
    assert "goto ensure_gateway" in launcher
    assert ":ensure_gateway" in launcher
    assert "timeout /t" not in launcher
    assert "for /L %%i in (1,1,120)" in launcher
    assert "for /L %%j in (1,1,90)" in launcher
    assert "-Tail 80" in launcher
    assert 'type "%TEMP%\\vool_api.err.log"' not in launcher
    assert "Start-Sleep -Seconds 1" in launcher
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
    assert "Test-NetConnection -ComputerName 127.0.0.1 -Port 18789" in launcher
    assert "%USERPROFILE%\\.local\\bin\\openclaw.cmd" in launcher
    assert 'from core.openclaw_locator import load_gateway_token; print(load_gateway_token())' in launcher
    assert "vool_api.err.log" in launcher
    assert "vool_api_child.err.log" in launcher
    assert "vool_gateway.err.log" in launcher
