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


def _run(tmp_path, source, *, timeout=2, conftest="", budgets=None, extra_watchdog_args=()):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_sample.py").write_text(source)
    (suite / "conftest.py").write_text(conftest)
    output = tmp_path / "watchdog"
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "VOOL_CI_PROGRESS_PID"):
        env.pop(name, None)
    command = [sys.executable, str(WATCHDOG), "--phase-timeout", str(timeout),
               "--output", str(output)]
    if budgets is not None:
        budgets_path = tmp_path / "budgets.json"
        budgets_path.write_text(json.dumps(budgets))
        command += ["--node-budgets", str(budgets_path)]
    command += list(extra_watchdog_args)
    command += ["--", sys.executable, "-m", "pytest",
                "-q", "-p", "no:cacheprovider", "--confcutdir", str(suite), str(suite)]
    result = subprocess.run(command, cwd=suite, env=env, capture_output=True, text=True, timeout=30)
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


def test_shared_fake_clock_suite_completes_and_cannot_falsify_a_stall(tmp_path):
    """A test that simulates a business clock by patching the shared stdlib
    ``time`` module (the usepod price-wait pattern) must complete normally, and
    its fake timestamps must never drive the stall decision. On main CI
    (artifact 10902318921) the teardown progress carried the fake ``at``
    1000.2 while the child's real clock read 2967.6 and it had already advanced
    to the next file: the supervisor declared a 600s "teardown stall" it had
    never observed. Here the fake clock lags the real one by 10s (> the 2s
    budget) with the same geometry, and the fixture's real 0.6s teardown hold
    makes the contaminated entry deterministically observable by the 0.1s
    polls -- success may not depend on catching a microsecond window."""
    source = """import time
import pytest

@pytest.fixture
def fake_business_clock():
    ticks = [time.monotonic() - 10.0]
    real = time.monotonic
    time.monotonic = lambda: ticks[0]
    yield ticks
    time.sleep(0.6)
    time.monotonic = real

def test_price_wait_shape(fake_business_clock):
    ticks = fake_business_clock
    ticks[0] += 0.2

def test_followup_normal():
    open('advanced.txt', 'w').write('reached')
"""
    result, evidence, _ = _run(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert evidence["complete"] and not evidence["timed_out"]
    assert "2 passed" in result.stdout
    assert (tmp_path / "suite" / "advanced.txt").read_text() == "reached"
    # The supervisor's own stall measurement stayed honest for the whole run:
    # both tests plus the 0.6s teardown hold are far inside the 2s budget.
    assert evidence["stalled_seconds"] < 2


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


# --- measured per-file phase budgets ----------------------------------------------------------


def test_a_measured_node_may_spend_its_budget_in_one_phase(tmp_path):
    """The shape of the corpus replay module: one module-scoped fixture legitimately holds the
    file's WHOLE measured duration inside a single opaque setup phase. Without its budget the
    base bound would kill known-slow work as a stall (measured: full run 36063857499 shard 0
    killed a 2647.95s-measured file at the flat 600s phase bound)."""
    conftest = (
        "import time, pytest\n"
        "@pytest.fixture(scope='module', autouse=True)\n"
        "def corpus(): time.sleep(1.6)\n"
    )
    result, evidence, _ = _run(
        tmp_path, "def test_ok(): pass\n", timeout=0.8, conftest=conftest,
        budgets={"weights": {"test_sample.py": 2.0}, "provenance": {"run": 35873216239}},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert evidence["complete"] and not evidence["timed_out"]


def test_a_measured_node_is_still_bounded_at_measured_times_margin(tmp_path):
    # 2.0s measured * 1.5 margin = 3s bound; the stuck call dies there, not at the base.
    result, evidence, _ = _run(
        tmp_path, "import time\ndef test_stuck(): time.sleep(30)\n", timeout=0.8,
        budgets={"test_sample.py": 2.0},
    )
    assert result.returncode == 124, result.stdout + result.stderr
    assert evidence["timed_out"] and evidence["wall_seconds"] < 6
    assert "exceeded 3s" in result.stdout


def test_an_unmeasured_node_keeps_the_base_bound_even_with_budgets_present(tmp_path):
    result, evidence, _ = _run(
        tmp_path, "import time\ndef test_stuck(): time.sleep(30)\n", timeout=0.8,
        budgets={"some/other_file.py": 60.0},
    )
    assert result.returncode == 124, result.stdout + result.stderr
    assert evidence["timed_out"] and evidence["wall_seconds"] < 3
    assert "exceeded 0.8s" in result.stdout


def test_a_missing_budgets_file_degrades_to_the_base_bound(tmp_path):
    result, evidence, _ = _run(
        tmp_path, "import time\ndef test_stuck(): time.sleep(30)\n", timeout=0.8,
        extra_watchdog_args=["--node-budgets", str(tmp_path / "absent.json")],
    )
    assert result.returncode == 124, result.stdout + result.stderr
    assert evidence["timed_out"] and evidence["wall_seconds"] < 3


def test_budget_loading_and_effective_bounds(tmp_path):
    import json as _json

    from ops.pytest_watchdog import _effective_phase_timeout, _load_node_budgets

    # The committed snapshot's envelope ({"weights": {...}, provenance, ...}) parses; entries
    # without a positive numeric duration are dropped, and broken/absent files degrade to no
    # budgets at all.
    payload = {"weights": {"a/test_x.py": 3.0, "b/test_y.py": "not-a-number", "c/z.py": 0}, "z": 1}
    weights = tmp_path / "weights.json"
    weights.write_text(_json.dumps(payload))
    assert _load_node_budgets(weights) == {"a/test_x.py": 3.0}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert _load_node_budgets(broken) == {}
    assert _load_node_budgets(tmp_path / "absent.json") == {}

    budgets = {"a/test_x.py": 3.0, "b/big.py": 800.0}
    # A budget never LOWERS the base bound; it only raises it (ceil(measured*margin)).
    assert _effective_phase_timeout(600, budgets, 1.5, "a/test_x.py::test_t") == 600
    assert _effective_phase_timeout(2, budgets, 1.5, "a/test_x.py::test_t") == 5
    assert _effective_phase_timeout(600, budgets, 1.5, "b/big.py::test_t") == 1200
    assert _effective_phase_timeout(600, budgets, 1.5, "other.py::test_t") == 600
    assert _effective_phase_timeout(600, {}, 1.5, "a/test_x.py::test_t") == 600
    assert _effective_phase_timeout(600, budgets, 1.5, "") == 600
