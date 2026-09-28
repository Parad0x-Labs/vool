"""Executed retirement contract for the generated OpenClaw launcher.

These tests render the REAL installer function (write_retired_openclaw_stub from
installer/install_vool.sh) into an isolated fixture and execute the generated
stub with bash: it must refuse with the retirement notice, exit nonzero, and
have zero side effects -- including from a project path that contains spaces and
next to an unrelated existing OpenClaw config that must stay byte-for-byte
intact. The native Start launcher must carry no gateway dependency.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.platform_helpers import bash_script_args

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_installer_function(tmp_path: Path, call_line: str) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    installer_script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    prefix, marker, _ = installer_script.partition('\nparse_args "$@"\n')

    assert marker

    harness = tmp_path / "run_installer_function.sh"
    harness.write_text(prefix + "\n" + call_line + "\n", encoding="utf-8")
    subprocess.run(bash_script_args(harness), check=True, cwd=PROJECT_ROOT)


def _render_retired_stub(tmp_path: Path, target: Path) -> Path:
    _run_installer_function(tmp_path, f'write_retired_openclaw_stub "{target}"')
    return target


def test_generated_stub_refuses_nonzero_without_side_effects(tmp_path: Path) -> None:
    project = tmp_path / "Vool Space Project"  # path with spaces, like a real Desktop checkout
    project.mkdir()
    stub = _render_retired_stub(tmp_path / "render", project / "OpenClaw_VOOL.sh")

    assert stub.exists()
    before = sorted(str(p.relative_to(project)) for p in project.rglob("*"))
    result = subprocess.run(
        bash_script_args(stub),
        capture_output=True,
        text=True,
        cwd=project,
    )
    after = sorted(str(p.relative_to(project)) for p in project.rglob("*"))

    assert result.returncode == 1, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "retired from VOOL" in combined
    assert "Start_VOOL.sh" in combined
    assert "Talk_To_VOOL.sh" in combined
    assert "Open_Web0.sh" in combined
    assert "https://github.com/Parad0x-Labs/openclaw-skills" in combined
    # No gateway, no registration, no third-party mutation anywhere in the stub bytes.
    body = stub.read_text(encoding="utf-8")
    for forbidden in (
        "openclaw gateway",
        "register_openclaw_agent",
        "inject_openclaw_web0_pill",
        "patch_openclaw_session_retry",
        "load_gateway_token",
        "OPENCLAW_HOME",
        "18789",
    ):
        assert forbidden not in body
    assert after == before, "the retirement stub must not create or modify any file"


def test_stub_leaves_unrelated_openclaw_config_untouched(tmp_path: Path) -> None:
    # An unrelated, VALID OpenClaw install lives next to the retired launcher.
    openclaw_home = tmp_path / ".openclaw"
    openclaw_home.mkdir()
    config = openclaw_home / "openclaw.json"
    original = '{"gateway":{"auth":{"token":"keep-me"}},"agents":{"list":[]}}'
    config.write_text(original, encoding="utf-8")

    stub = _render_retired_stub(tmp_path / "render", tmp_path / "OpenClaw_VOOL.sh")
    env_extra = {"OPENCLAW_HOME": str(openclaw_home)}
    import os

    result = subprocess.run(
        bash_script_args(stub),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, **env_extra},
    )

    assert result.returncode == 1
    assert config.read_text(encoding="utf-8") == original
    assert sorted(p.name for p in openclaw_home.iterdir()) == ["openclaw.json"]


def test_native_start_launcher_has_no_gateway_dependency(tmp_path: Path) -> None:
    """Render the REAL write_launcher output and prove the native start path never
    waits on, starts, or reads the OpenClaw gateway."""
    project = tmp_path / "project"
    project.mkdir()
    target = project / "Start_VOOL.sh"
    _run_installer_function(
        tmp_path / "render",
        f'write_launcher "{target}" "/tmp/vool-runtime-home" "local-only"',
    )
    script = target.read_text(encoding="utf-8")

    assert 'export VOOL_HOME="${VOOL_HOME:-/tmp/vool-runtime-home}"' in script
    assert 'exec "${VENV_PY}" -m apps.vool_api_server --port "${VOOL_OPENCLAW_API_PORT}"' in script
    assert "18789" not in script
    assert "openclaw gateway" not in script
    assert "OPENCLAW_HOME" not in script
    assert "OPENCLAW_STATE_DIR" not in script
    assert "register_openclaw_agent" not in script
    assert "VOOL_OPENCLAW_GATEWAY_PORT" not in script


def test_installer_source_carries_no_openclaw_setup_calls() -> None:
    script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")

    for retired_call in (
        "register_openclaw(",
        "configure_openclaw_with_ollama(",
        "apply_openclaw_ui_patches(",
        "setup_openclaw_bridge(",
        "resolve_openclaw_agent_dir(",
    ):
        assert retired_call not in script
