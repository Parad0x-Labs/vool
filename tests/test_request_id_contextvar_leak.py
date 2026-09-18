"""The request-id ContextVar leak — CLOSED on the unified candidate, and held closed.

WHAT LEAKED
-----------
`tests/foundation/test_r5_pipeline_order.py` drove an HTTP-shaped turn and left the
runtime's current-request ContextVar set to `req:http:r5-empty-sr`. Every later test in
the SAME process that opened a turn through `tests/test_turn_attempt_chain._Harness`
then died inside `_r3_open_turn_execution` -> `open_execution`, bound to that stale
request.

MEASURED, base vs candidate, same command, same venv::

    PYTHONPATH="$PWD" <venv>/bin/python -m pytest -q -p no:randomly \
        tests/foundation/test_r5_pipeline_order.py tests/test_demand_ownership_r1e.py

    a2308a26 (untouched base worktree) -> 15 failed, 3 passed
    unified candidate                  -> 18 passed

WHO CLOSED IT
-------------
The request-context isolation lane (`e42d68cf`), integrated here: request/turn identity
owns its own lifecycle — a scoped bind with per-test isolation — so the var is released
when the turn that bound it ends, instead of outliving it.

WHY THIS FILE STILL EXISTS
--------------------------
It arrived on the mixed-demand lane as an `xfail(strict=True)` detector whose reason
said "when this XPASSes the leak is fixed - delete this test". Integrating the two lanes
made it XPASS, which is the detector doing exactly its job. It is kept rather than
deleted, and inverted: the same two-file ordering now has to PASS. A deleted detector
proves nothing about tomorrow, and this ordering is precisely the one that regressed
before — the leak is invisible to any run of either file on its own.
"""
from __future__ import annotations

import subprocess
import sys

#: The leaking file, and one victim. The victim's tests pass on their own; run after
#: the leaker in the SAME process they die in `open_execution` on a stale request id.
_LEAKER = "tests/foundation/test_r5_pipeline_order.py"
_VICTIM = (
    "tests/test_demand_ownership_r1e.py::"
    "test_mixed_explanation_and_weather_loses_neither_demand"
)


def _run(*targets: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", *targets],
        capture_output=True,
        text=True,
    )


def test_the_victim_passes_on_its_own():
    """The control. Without this the detector below could go red for any reason."""
    result = _run(_VICTIM)
    assert result.returncode == 0, result.stdout[-2000:]


def test_a_turn_that_binds_a_request_context_releases_it_for_the_next_test():
    """The repaired behaviour: the same victim, run after the leaker, in one process.

    This assertion is the whole point of the file. At a2308a26 it fails; here it
    passes because the request context is released with the turn that bound it.
    """
    result = _run(_LEAKER, _VICTIM)
    assert result.returncode == 0, (
        "the victim failed only because it ran after the leaker - a request context "
        "was left bound:\n" + result.stdout[-2000:]
    )


def test_the_whole_victim_file_survives_the_leaker():
    """The full poisoned-order battery, not just one victim.

    The single-victim test above can be satisfied by one lucky case. This runs the
    entire file that the leak used to take down: 15 of its 18 tests failed at
    a2308a26 under exactly this ordering. Nothing may fail here.
    """
    result = _run(_LEAKER, "tests/test_demand_ownership_r1e.py")
    assert result.returncode == 0, (
        "the poisoned-order battery regressed - a request context outlived its "
        "turn:\n" + result.stdout[-4000:]
    )
