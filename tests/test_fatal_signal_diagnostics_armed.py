"""The API server's fatal-signal diagnostics arm is VERIFIED, never assumed.

``_arm_fatal_signal_diagnostics`` exists so a native SIGSEGV-class fault dumps every
thread's Python stack instead of vanishing; a silent arming failure would drop that
diagnostic without a trace -- the exact failure mode the function exists to remove. These
controls pin the verification contract in both directions: a real arming reports True with
``faulthandler.is_enabled()`` actually true, and a refused or ineffective arming reports
False instead of raising or claiming success. The entrypoint check keeps main() wired to
the verifying arm rather than a bare ``faulthandler.enable``.
"""
from __future__ import annotations

import faulthandler
import inspect
import os
import subprocess
import sys
from pathlib import Path

from apps import vool_api_server

REPO = Path(__file__).resolve().parents[1]

# pytest arms faulthandler for its own session, so an in-process check could not tell a real
# arming from one that was already there (and re-arming would redirect pytest's own crash
# output). The positive control runs in a fresh interpreter that starts with it disarmed.
_FRESH_ARMING = """
import faulthandler
before = faulthandler.is_enabled()
from apps import vool_api_server
after_import = faulthandler.is_enabled()
armed = vool_api_server._arm_fatal_signal_diagnostics()
print("ARMING", before, after_import, armed, faulthandler.is_enabled())
"""


def test_arming_reports_true_only_when_actually_enabled():
    env = {key: value for key, value in os.environ.items() if key != "PYTHONFAULTHANDLER"}
    result = subprocess.run(
        [sys.executable, "-c", _FRESH_ARMING], cwd=REPO, env=env,
        capture_output=True, text=True, timeout=180, check=False,
    )
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.startswith("ARMING ")]
    assert lines, result.stdout
    # Disarmed before the call (so the call is what armed it), True reported, actually enabled.
    assert lines[-1].split()[1:] == ["False", "False", "True", "True"], lines[-1]


def test_a_refused_arming_is_reported_false_and_never_raises(monkeypatch):
    def refused(*_args, **_kwargs):
        raise RuntimeError("handler installation refused")

    monkeypatch.setattr(faulthandler, "enable", refused)
    assert vool_api_server._arm_fatal_signal_diagnostics() is False


def test_an_ineffective_arming_is_reported_false(monkeypatch):
    monkeypatch.setattr(faulthandler, "enable", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    assert vool_api_server._arm_fatal_signal_diagnostics() is False


def test_main_wires_the_verified_arm_before_serving():
    source = inspect.getsource(vool_api_server.main)
    arm_call = source.index("_arm_fatal_signal_diagnostics()")
    assert arm_call < source.index("preflight(")
    assert "faulthandler.enable" not in source
