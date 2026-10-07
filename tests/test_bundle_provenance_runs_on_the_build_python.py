"""The bundle's dependency provenance audit runs on the interpreter the macOS build uses.

`build_macos_app.sh` runs `ops/bundle_dependency_provenance.py` with the system python3 (3.9 on current
macOS). Release dry run, 2026-10-07: `zip(..., strict=False)` raised "zip() takes no keyword arguments"
there, so every self-contained build stopped at the provenance step.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SYSTEM_PY = Path("/usr/bin/python3")

pytestmark = pytest.mark.skipif(not SYSTEM_PY.exists(), reason="no system python3 (not a macOS build host)")

PROBE = (
    "import json, sys; sys.dont_write_bytecode = True; sys.path.insert(0, '.');"
    "import ops.bundle_dependency_provenance as m; print(json.dumps(m.lock_artifacts(), sort_keys=True))"
)


def test_the_lock_audit_runs_under_the_system_python_and_matches_this_interpreter():
    built = subprocess.run([str(SYSTEM_PY), "-c", PROBE], cwd=REPO, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert built.returncode == 0, built.stderr[-2000:]
    here = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert here.returncode == 0, here.stderr[-2000:]
    assert json.loads(built.stdout) == json.loads(here.stdout)
    assert json.loads(built.stdout), "uv.lock yielded no packages"
