"""The council mutation runner is bounded, not babysat.

The matrix mutates production files in place and runs the suite against each mutant.
An unbounded `subprocess.run` means one mutant that wedges a test into an infinite wait
hangs the whole session with a MUTATED production file on disk; a timeout raised as an
exception skips the `finally` reporting path's failure accounting. The runner must bound
every invocation, isolate the child in its own process group so a kill takes the whole
tree, and report a timeout as a recorded failure with the byte-exact restore still
verified.
"""

from __future__ import annotations

import subprocess
from unittest import mock

import ops.council_mutations as matrix


def test_every_pytest_invocation_is_bounded_and_group_isolated() -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0)
    with mock.patch.object(matrix.subprocess, "run", return_value=completed) as run:
        ok, timed_out = matrix.run_pytest(["tests/test_council_scorecard.py"])
    assert (ok, timed_out) == (True, False)
    kwargs = run.call_args.kwargs
    assert kwargs.get("timeout") == matrix.PYTEST_TIMEOUT_SECONDS, (
        "every invocation carries the bound — a hang must be impossible"
    )
    assert kwargs.get("start_new_session") is True, (
        "the child owns its own process group so a timeout kill reaches its whole tree"
    )


def test_a_timeout_is_reported_not_raised() -> None:
    with mock.patch.object(
        matrix.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=1)
    ):
        ok, timed_out = matrix.run_pytest(["tests/test_council_scorecard.py"])
    assert (ok, timed_out) == (False, True), (
        "a timeout must come back as data — raising would skip the restore accounting"
    )


def test_the_bound_is_real_and_named() -> None:
    assert 60 <= matrix.PYTEST_TIMEOUT_SECONDS <= 3600, (
        "bounded means a concrete wall-clock number, generous but finite"
    )
