from __future__ import annotations

import subprocess
from pathlib import Path

from tests.platform_helpers import bash_script_args

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _render_openclaw_launcher(tmp_path: Path) -> str:
    installer_script = (PROJECT_ROOT / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    prefix, marker, _ = installer_script.partition('\nparse_args "$@"\n')

    assert marker

    harness = tmp_path / "render_openclaw_launcher.sh"
    output = tmp_path / "OpenClaw_VOOL.sh"
    harness.write_text(
        prefix + '\nwrite_openclaw_launcher "$1" "$2" "$3" "$4"\n',
        encoding="utf-8",
    )
    subprocess.run(
        bash_script_args(
            harness,
            str(output),
            "/tmp/vool-runtime-home",
            "qwen2.5:14b",
            "/tmp/.openclaw-default",
        ),
        check=True,
        cwd=PROJECT_ROOT,
    )
    return output.read_text(encoding="utf-8")


def test_openclaw_launcher_respects_runtime_home_override(tmp_path: Path) -> None:
    script = _render_openclaw_launcher(tmp_path)

    assert 'export VOOL_HOME="${VOOL_HOME:-/tmp/vool-runtime-home}"' in script


def test_openclaw_launcher_maps_profile_home_to_state_dir(tmp_path: Path) -> None:
    script = _render_openclaw_launcher(tmp_path)

    assert 'export OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-/tmp/.openclaw-default}"' in script


def test_openclaw_launcher_uses_noninteractive_ollama_fallback(tmp_path: Path) -> None:
    script = _render_openclaw_launcher(tmp_path)

    assert 'ollama launch openclaw --yes --model "${MODEL_TAG}"' in script


def test_openclaw_launcher_exports_project_root_and_health_waits(tmp_path: Path) -> None:
    script = _render_openclaw_launcher(tmp_path)

    assert 'cd "${PROJECT_ROOT}"' in script
    assert 'VENV_RESOLVER="${PROJECT_ROOT}/scripts/ensure_workspace_runtime.sh"' in script
    assert 'VENV_PY="$(bash "${VENV_RESOLVER}")"' in script
    assert 'spawn_detached() {' in script
    assert 'start_new_session=True' in script
    assert 'api_pid="$(spawn_detached /tmp/vool_api_server.log "${PROJECT_ROOT}/Start_VOOL.sh")"' in script
    assert 'wait_for_http_ready() {' in script
    assert 'export VOOL_OPENCLAW_API_URL="${VOOL_OPENCLAW_API_URL:-http://127.0.0.1:${VOOL_OPENCLAW_API_PORT}}"' in script
    assert 'wait_for_http_ready "${VOOL_OPENCLAW_API_URL}/healthz" 30 "${api_pid}" 3' in script


def test_workspace_openclaw_launcher_does_not_expand_empty_profile_array_under_nounset(tmp_path: Path) -> None:
    script = _render_openclaw_launcher(tmp_path)

    assert 'PROFILE_ARGS' not in script
    assert 'spawn_detached /tmp/vool_openclaw.log openclaw gateway run --force' in script
