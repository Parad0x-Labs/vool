"""Fair-start health verdicts for a process this updater just relaunched.

The verdict for a relaunched version is an observation of the relaunched process,
never clock arithmetic measured from launch time: a slow machine must not have its
good update rolled back (or its rollback restored) before the relaunched version
actually got to run. Both bounds below stay finite so a genuinely dead runtime
cannot hang the caller; they document the whole allowance.

Shared by the manual-rollback flow (core/updater/rollback.py) and the forward
update flow (core/updater/transaction.py).
"""
from __future__ import annotations

import time

STOP_BOUND_SECONDS = 90.0            # stop grace for a process a flow must remove from the app seat
RELAUNCH_START_BOUND_SECONDS = 90.0  # start allowance before a never-observed process is failed


def await_relaunch_health(
    probe,
    helper,
    *,
    health_timeout: float,
    expected_version: str | None,
    start_bound: float | None = None,
):
    """The health verdict for a version this flow just relaunched: an observation of
    the relaunched process, never elapsed time since launch. Not-healthy stands only
    once the process was seen to exit (it ran and failed without serving), or was seen
    up and then stayed silent for the health window measured from that moment, or the
    documented start bound passed while it was never seen up. A helper that cannot
    observe its launch (a supervisor owns the restart, or an injected test helper)
    keeps the plain bounded probe."""
    if start_bound is None:
        start_bound = RELAUNCH_START_BOUND_SECONDS
    observe = getattr(helper, "observe_relaunch", None)
    if observe is None:
        return probe(timeout=health_timeout, expected_version=expected_version)
    started = time.monotonic()
    up_since = None
    while True:
        health = probe(timeout=1.0, expected_version=expected_version)
        if health.ok:
            return health
        state = observe()
        if state == "unknown":
            # This launch is not observable (supervisor-owned); fall back to the
            # plain bounded probe so the wait stays the documented health window.
            return probe(timeout=health_timeout, expected_version=expected_version)
        now = time.monotonic()
        if state == "exited":
            return health
        if state == "up":
            up_since = now if up_since is None else up_since
            if now - up_since >= health_timeout:
                return health
        elif now - started >= start_bound:
            return health
