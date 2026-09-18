"""Both sides of a replay equivalence must observe the same outside world.

The instrumentation proof asks one question: does `VOOL_SEMANTIC_REACH` change the answer? That is
only answerable if everything ELSE is held still. Free disk space is not still -- the product renders
it to one decimal GB, so 100 MB of movement flips the digit, and an ordinary full-suite run moves far
more than that while writing temp data.

Measured on 2026-08-14 with the flag HELD CONSTANT at OFF: the same probe returned 23.2 GB, then
22.9 GB after 250 MB of churn. Toggling the flag moved free space by 0.000 MB. Two independent
samples of a drifting fact, not an instrumentation difference.

The suite's same-configuration control could not catch it: it re-runs a differing turn twice under
OFF back-to-back, which detects JITTER but not DRIFT -- two adjacent samples agree while the earlier
OFF->ON gap straddled a real change.

These tests assert the general property, so they must hold for any volatile machine observation, not
for the one wording that exposed it.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile

import pytest

from tests.semantic_phase0._fixtures import (  # noqa: F401
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    pin_volatile_machine_observations,
    reseal_network_after_function_fixtures,
)


@contextlib.contextmanager
def _space_held(megabytes: int = 400):
    """Actually hold free space down while the measurement happens.

    The first version wrote and deleted inside one helper, so free space was back to baseline before
    anything was measured -- the churn moved nothing and every assertion below passed without the pin
    being exercised at all. The sabotage test caught it. The file has to stay on disk across the
    second reading or this proves nothing.
    """

    descriptor, path = tempfile.mkstemp(prefix="replay-churn-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(b"\0" * (megabytes * 1024 * 1024))
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def test_the_pinned_observation_survives_real_disk_movement() -> None:
    """The invariant: the world the replay sees does not move while the replay runs."""

    from core import machine_diagnostics

    first = machine_diagnostics.disk_usage()
    with _space_held():
        second = machine_diagnostics.disk_usage()

    assert first == second, "the replay observed two different worlds"


def test_the_pin_is_identical_across_both_instrumentation_settings() -> None:
    """OFF and ON must be handed the same reading -- that is the whole point."""

    from core import machine_diagnostics

    previous = os.environ.get("VOOL_SEMANTIC_REACH")
    try:
        os.environ["VOOL_SEMANTIC_REACH"] = "0"
        off = machine_diagnostics.disk_usage()
        with _space_held():
            os.environ["VOOL_SEMANTIC_REACH"] = "1"
            on = machine_diagnostics.disk_usage()
    finally:
        if previous is None:
            os.environ.pop("VOOL_SEMANTIC_REACH", None)
        else:
            os.environ["VOOL_SEMANTIC_REACH"] = previous

    assert off == on


def test_repeated_reads_stay_stable_over_many_samples() -> None:
    """Determinism across the whole replay, not just two adjacent samples."""

    from core import machine_diagnostics

    readings = []
    for size in (150, 300, 450, 600, 750, 900):
        with _space_held(size):
            readings.append(machine_diagnostics.disk_usage())

    assert all(r == readings[0] for r in readings), readings


def test_the_pin_still_reports_the_real_shape_of_the_machine() -> None:
    """Pinning must freeze the value, never fabricate or empty it.

    A pin that returned nothing would make the turn 'deterministic' by making it wrong.
    """

    from core import machine_diagnostics

    rows = machine_diagnostics.disk_usage()

    assert rows, "the pinned probe reported no drives at all"
    for row in rows:
        assert str(row.get("mount") or ""), row
        assert isinstance(row.get("free_gb"), (int, float)), row
        assert row["free_gb"] >= 0, row
    # The reading is frozen, but it is still a reading of THIS machine: the real root filesystem
    # has to be among the drives reported, and its size has to match what the OS says.
    mounts = {str(row["mount"]) for row in rows}
    assert "/" in mounts, mounts
    root = next(row for row in rows if str(row["mount"]) == "/")
    actual_total_gb = round(shutil.disk_usage("/").total / (1024**3), 1)
    assert abs(float(root["total_gb"]) - actual_total_gb) < 1.0, (root, actual_total_gb)


def test_a_caller_that_mutates_a_row_cannot_change_what_the_next_run_sees() -> None:
    """Independent copies: one turn must not be able to edit the next turn's world."""

    from core import machine_diagnostics

    first = machine_diagnostics.disk_usage()
    first[0]["free_gb"] = -12345
    second = machine_diagnostics.disk_usage()

    assert second[0]["free_gb"] != -12345


def test_the_pin_is_confined_to_this_replay_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production must not inherit a frozen world; only the equivalence proof holds it still.

    The fixture restores the real probe on teardown, so the attribute it replaces has to be the real
    function's own module attribute rather than a permanent rebinding somewhere deeper.
    """

    import inspect

    from tests.semantic_phase0 import _fixtures

    source = inspect.getsource(_fixtures.pin_volatile_machine_observations)

    assert "finally:" in source
    assert "machine_diagnostics.disk_usage = original" in source


def test_sabotage_resampling_one_side_reintroduces_the_two_world_failure() -> None:
    """Prove the pin is load-bearing: an unpinned probe drifts across the same churn."""

    from core import machine_diagnostics

    pinned = machine_diagnostics.disk_usage

    def _live(*args, **kwargs):
        usage = shutil.disk_usage("/")
        return [{"mount": "/", "free_gb": round(usage.free / (1024**3), 1)}]

    machine_diagnostics.disk_usage = _live
    try:
        before = machine_diagnostics.disk_usage()
        with _space_held(600):
            after = machine_diagnostics.disk_usage()
    finally:
        machine_diagnostics.disk_usage = pinned

    assert before != after, (
        "the churn did not move free space far enough to prove the pin matters; "
        f"before={before} after={after}"
    )
