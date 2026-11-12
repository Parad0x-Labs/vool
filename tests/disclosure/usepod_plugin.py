"""The UsePod lane for the served disclosure drives, switched on the v0.8 way.

UsePod left VOOL's core in 0.8: its cloud routes answer only when the first-party UsePod plugin is
installed and on (core.plugin_providers.require_legacy_x402). These drives give the daemon its own
plugin store inside the test's folder and switch that plugin on through the real plugin manager,
so nothing reaches the session's shared plugin store.
"""
from __future__ import annotations

import subprocess
import sys

from tests import _reader_served_rig as rig


def plugin_paths(tmp_path):
    return {"VOOL_PLUGIN_LIFECYCLE_PATH": str(tmp_path / "plugins" / "plugin-lifecycle.json"),
            "VOOL_PLUGINS_DIR": str(tmp_path / "plugins" / "installed")}


def switch_on(daemon):
    result = subprocess.run(
        [sys.executable, "-B", "-c", "from tests.usepod._legacy_profile import enable_reference; enable_reference()"],
        env=daemon.env(), cwd=rig.REPO_ROOT, capture_output=True, timeout=120, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
