"""Exercise the real process boundary: hangs stay red; completed verdicts survive."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

WATCHDOG = Path(__file__).resolve().parents[1] / "ops" / "pytest_watchdog.py"


def _run(tmp_path, source, *, timeout=2, conftest=""):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_sample.py").write_text(source)
    (suite / "conftest.py").write_text(conftest)
    output = tmp_path / "watchdog"
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "VOOL_CI_PROGRESS_PID"):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, str(WATCHDOG), "--phase-timeout", str(timeout),
         "--output", str(output), "--", sys.executable, "-m", "pytest",
         "-q", "-p", "no:cacheprovider", "--confcutdir", str(suite), str(suite)],
        cwd=suite, env=env, capture_output=True, text=True, timeout=15,
    )
    assert (output / "result.json").exists(), result.stdout + result.stderr
    return result, json.loads((output / "result.json").read_text()), output


@pytest.mark.parametrize("phase", ["collection", "setup", "call", "teardown", "shutdown"])
def test_stuck_phase_exits_red_with_location_and_stack(tmp_path, phase):
    source = "import time\ndef test_ok(): pass\n"
    conftest = "import time, pytest\n"
    if phase == "collection":
        source += "time.sleep(30)\n"
    elif phase == "call":
        source = "import time\ndef test_stuck(): time.sleep(30)\n"
    elif phase in {"setup", "teardown"}:
        body = "time.sleep(30); yield" if phase == "setup" else "yield; time.sleep(30)"
        conftest += "@pytest.fixture(autouse=True)\ndef block(): " + body + "\n"
    else:
        source += "import atexit\natexit.register(time.sleep, 30)\n"
    result, evidence, output = _run(tmp_path, source, conftest=conftest)
    assert result.returncode == 124, result.stdout + result.stderr
    assert evidence["timed_out"] and not evidence["complete"]
    assert phase in evidence["last_progress"]["phase"]
    assert evidence["wall_seconds"] < 10
    assert "test_sample.py" in (output / "stacks.log").read_text() or phase != "call"
    assert "CI STALL:" in result.stdout
    if phase in {"setup", "call", "teardown"}:
        assert "test_sample.py::test_" in evidence["last_progress"]["node"]


def test_progressing_suite_can_exceed_phase_budget_and_preserves_order(tmp_path):
    source = "import time\n" + "\n".join(
        f"def test_{n}():\n    time.sleep(0.7)\n    open('order.txt', 'a').write('{n}\\n')"
        for n in range(4)
    )
    result, evidence, _ = _run(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert evidence["complete"] and not evidence["timed_out"]
    assert evidence["wall_seconds"] > 2
    assert "4 passed" in result.stdout
    assert (tmp_path / "suite" / "order.txt").read_text().splitlines() == ["0", "1", "2", "3"]


def test_assertion_failure_stays_red(tmp_path):
    result, evidence, _ = _run(tmp_path, "def test_bad(): assert False, 'real failure'\n")
    assert result.returncode == 1
    assert evidence["exitstatus"] == 1
    assert not evidence["timed_out"]
    assert "real failure" in result.stdout


def test_nested_pytest_cannot_reset_outer_deadline(tmp_path):
    source = """import subprocess, sys

def test_outer():
    code = "import pytest; pytest.main(['--version'])"
    while True:
        subprocess.run([sys.executable, '-c', code], check=True)
"""
    result, evidence, _ = _run(tmp_path, source)
    assert result.returncode == 124, result.stdout + result.stderr
    assert evidence["last_progress"]["node"].endswith("::test_outer")
    assert evidence["wall_seconds"] < 10


def test_cancellation_reaps_the_owned_child_and_records_incomplete(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_wait.py").write_text("import time\ndef test_wait(): time.sleep(30)\n")
    output = tmp_path / "cancel"
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_PLUGINS", None)
    command = [sys.executable, str(WATCHDOG), "--phase-timeout", "20", "--output",
               str(output), "--", sys.executable, "-m", "pytest", "-q", "-p",
               "no:cacheprovider", "--confcutdir", str(suite), str(suite)]
    process = subprocess.Popen(command, cwd=suite, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 8
        progress = {}
        while time.monotonic() < deadline:
            try:
                progress = json.loads((output / "progress.json").read_text())
            except FileNotFoundError:
                pass
            if progress.get("phase") == "call":
                break
            time.sleep(0.05)
        assert progress.get("phase") == "call"
        process.terminate()
        text, _ = process.communicate(timeout=5)
        assert process.returncode == 130, text
        evidence = json.loads((output / "result.json").read_text())
        assert evidence["interrupted"] and not evidence["complete"]
        with pytest.raises(ProcessLookupError):
            os.kill(progress["pid"], 0)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
