"""The machine-lane home isolation is real, and it survives failure and cancellation.

``tests/effect_budget/test_door_command_machine.py`` drives the machine write
lane, which computes its allowed roots from ``Path.home()``. Before this proof
existed, that test wrote ``cp2-machine-note.txt`` into the operator's REAL
``~/Desktop`` and never removed it: ``VOOL_HOME`` (which the root conftest
sets) does not move ``Path.home()``.

Two laws are proved here, both by running a REAL nested pytest session in a
subprocess — not by reasoning about fixture semantics:

    ISOLATION SURVIVES A FAILING TEST. A test that writes through the machine
    lane and then fails leaves nothing in the operator's home. The write went to
    the synthetic home; HOME is restored in teardown, which pytest runs on
    failure exactly as on success.

    ISOLATION SURVIVES CANCELLATION. The same holds when the test body raises
    ``KeyboardInterrupt`` — the interrupt unwinds through the fixture
    generators' ``finally``, so neither the redirect nor the guard is skipped.

And the guard itself is proved to BITE: a nested test that writes into the
watched directory fails with the guard's own message. Without that leg the
first two laws would be satisfied by a guard that never looks.

Nothing here writes into the operator's real home. The nested sessions point
``VOOL_TEST_OPERATOR_HOME_OVERRIDE`` at a throwaway directory, so the "real
home" the guard watches is itself synthetic.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_NESTED_CONFTEST = '''
import sys
sys.path.insert(0, {root!r})

from tests.effect_budget.conftest import (  # noqa: F401
    _real_home_residue_guard,
    operator_home,
    synthetic_machine_home,
)
'''

_NESTED_TESTS = '''
import os
from pathlib import Path


def test_writes_through_the_lane_then_fails(synthetic_machine_home):
    target = Path(os.path.expanduser("~/Desktop")) / "isolation-probe-fail.txt"
    target.write_text("v1", encoding="utf-8")
    assert target.exists()
    raise AssertionError("deliberate failure AFTER a machine-lane write")


def test_writes_through_the_lane_then_is_cancelled(synthetic_machine_home):
    target = Path(os.path.expanduser("~/Desktop")) / "isolation-probe-cancel.txt"
    target.write_text("v1", encoding="utf-8")
    assert target.exists()
    raise KeyboardInterrupt("deliberate cancellation AFTER a machine-lane write")
'''

_NESTED_LEAK = '''
from pathlib import Path

from tests.effect_budget.conftest import operator_home


def test_a_write_into_the_watched_home_is_caught():
    """No synthetic_machine_home: this writes where the guard is watching."""
    (operator_home() / "Desktop" / "deliberate-leak.txt").write_text("x", encoding="utf-8")
'''


def _run_nested(tmp_path: Path, body: str, watched_home: Path, *, args: tuple[str, ...] = ()):
    """Run a real pytest session over ``body`` and return the completed process."""
    nested = tmp_path / "nested"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "conftest.py").write_text(
        _NESTED_CONFTEST.format(root=str(PROJECT_ROOT)), encoding="utf-8"
    )
    (nested / "test_nested_probe.py").write_text(body, encoding="utf-8")

    env = dict(os.environ)
    env["VOOL_TEST_OPERATOR_HOME_OVERRIDE"] = str(watched_home)
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(nested),
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            "--no-header",
            "-q",
            "--confcutdir",
            str(nested),
            *args,
        ],
        cwd=str(nested),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.fixture()
def watched_home(tmp_path: Path) -> Path:
    """A stand-in for the operator's real home, so nothing real is at risk."""
    home = tmp_path / "watched-operator-home"
    for name in ("Desktop", "Downloads", "Documents"):
        (home / name).mkdir(parents=True, exist_ok=True)
    return home


def test_isolation_survives_failure_and_cancellation(tmp_path: Path, watched_home: Path) -> None:
    before = {
        name: sorted(p.name for p in (watched_home / name).iterdir())
        for name in ("Desktop", "Downloads", "Documents")
    }

    result = _run_nested(tmp_path, _NESTED_TESTS, watched_home)

    combined = result.stdout + result.stderr
    # Both nested tests must have RUN and ended badly on their OWN terms. Assert
    # each marker separately: checking only one would let this proof pass while
    # the other leg never executed.
    assert "deliberate failure AFTER a machine-lane write" in combined, combined[-4000:]
    assert "deliberate cancellation AFTER a machine-lane write" in combined, combined[-4000:]
    assert "KeyboardInterrupt" in combined, combined[-4000:]
    assert result.returncode != 0, combined[-4000:]
    # The residue guard must NOT be the thing that fired: the writes landed in
    # the synthetic home, which is the whole point.
    assert "wrote into the operator's REAL home" not in combined, combined[-4000:]

    after = {
        name: sorted(p.name for p in (watched_home / name).iterdir())
        for name in ("Desktop", "Downloads", "Documents")
    }
    assert after == before, (
        "a failing and a cancelled machine-lane test left residue in the watched home: "
        f"{before} -> {after}"
    )
    # And the probe files exist nowhere at all outside their tmp homes.
    assert not (watched_home / "Desktop" / "isolation-probe-fail.txt").exists()
    assert not (watched_home / "Desktop" / "isolation-probe-cancel.txt").exists()


def test_the_residue_guard_bites_when_a_test_writes_into_the_watched_home(
    tmp_path: Path, watched_home: Path
) -> None:
    """The guard is load-bearing: remove it and the leak above goes unreported."""
    result = _run_nested(tmp_path, _NESTED_LEAK, watched_home)

    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined[-4000:]
    assert "wrote into the operator's REAL home" in combined, combined[-4000:]
    assert "deliberate-leak.txt" in combined, combined[-4000:]
