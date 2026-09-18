from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_probe_stack_shell_wrapper_points_at_provider_probe() -> None:
    script = (PROJECT_ROOT / "Probe_VOOL_Stack.sh").read_text(encoding="utf-8")

    assert "installer/provider_probe.py" in script
    assert ".venv/bin/python" in script


def test_probe_stack_bat_wrapper_points_at_provider_probe() -> None:
    script = (PROJECT_ROOT / "Probe_VOOL_Stack.bat").read_text(encoding="utf-8")

    assert "installer\\provider_probe.py" in script
    assert ".venv\\Scripts\\python.exe" in script


def test_provider_probe_runs_as_direct_script_entrypoint() -> None:
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "installer" / "provider_probe.py"), "--json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert '"recommended_stack_id"' in completed.stdout
