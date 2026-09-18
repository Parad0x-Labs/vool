"""Repeated ordinary background work must reach a bounded resource steady state.

Measured on the hermetic gauntlet child (2026-08-14), which drives many turns through one
interpreter: threads climbed 1 -> 76 and descriptors 7 -> 391, exhausting a 256-descriptor limit
and taking the whole `tests/gauntlet/test_harness_control.py` group down as one infrastructure
fault. At peak the process held 73 connections to `vool_web0_v2.db` plus 73 WAL descriptors, 68 to
`vool_memory.db` plus 68 WAL, and 68 sockets.

The cause was not missing cleanup. `FactExtractor.trigger_async` spawned one unbounded thread per
invocation; each holds a socket for its `/api/chat` POST and a thread-local SQLite connection for
the memory write. The 30s request timeout bounds each thread's LIFETIME but not how many exist at
once, and turns arrive far faster than 30s whenever the endpoint is slow or absent -- 36 threads
were simultaneously blocked in `http.client._read_status`. Peak concurrency, not leaked state, is
what consumed the descriptors, which is why a thread-exit cleanup alone could never have fixed it.

These tests assert the INVARIANT -- resource use is bounded across repeated equivalent work -- not
the historical numbers. Nothing here depends on 76, 391, one prompt, or one gauntlet order.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from core import fact_extractor as fx


def _open_fds() -> int:
    return len(os.listdir("/dev/fd"))


def _extractor_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "vool-fact-extractor" and t.is_alive()]


class _HangingExtractor(fx.FactExtractor):
    """Stands in for a model endpoint that never answers -- the condition that caused the pile-up.

    Subclassing rather than monkeypatching the network keeps the real `trigger_async` bounding path
    under test; only the work inside the thread is replaced.
    """

    def __init__(self, release: threading.Event) -> None:
        self._release = release
        self.started = threading.Semaphore(0)
        self._close_memory_on_finish = False

    def _run_safely(self, messages: list[dict]) -> None:  # type: ignore[override]
        self.started.release()
        self._release.wait(timeout=30)


def _drain(release: threading.Event) -> None:
    release.set()
    deadline = time.monotonic() + 15
    while _extractor_threads() and time.monotonic() < deadline:
        time.sleep(0.02)


def test_repeated_triggers_never_exceed_the_concurrency_bound() -> None:
    """The invariant: N invocations do not create N live threads."""

    release = threading.Event()
    extractor = _HangingExtractor(release)
    try:
        peak = 0
        for _ in range(40):
            extractor.trigger_async([{"role": "user", "content": "hello"}])
            peak = max(peak, len(_extractor_threads()))
        assert peak <= fx._MAX_CONCURRENT_EXTRACTIONS, f"{peak} live extractor threads"
    finally:
        _drain(release)


def test_thread_count_does_not_grow_monotonically_across_repeated_work() -> None:
    """The original slope, asserted as absent: many rounds must not trend upward."""

    release = threading.Event()
    extractor = _HangingExtractor(release)
    try:
        counts = []
        for _ in range(6):
            for _ in range(15):
                extractor.trigger_async([{"role": "user", "content": "x"}])
            counts.append(len(_extractor_threads()))
        assert max(counts) <= fx._MAX_CONCURRENT_EXTRACTIONS, counts
        assert not all(counts[i] < counts[i + 1] for i in range(len(counts) - 1)), counts
    finally:
        _drain(release)


def test_descriptor_count_returns_to_baseline_after_the_work_drains() -> None:
    """Descriptors must come back, not merely stop growing."""

    release = threading.Event()
    extractor = _HangingExtractor(release)
    baseline = _open_fds()
    try:
        for _ in range(40):
            extractor.trigger_async([{"role": "user", "content": "y"}])
        peak = _open_fds()
    finally:
        _drain(release)
    settled = _open_fds()

    assert peak - baseline < 40, f"baseline={baseline} peak={peak}"
    assert settled - baseline <= 4, f"baseline={baseline} settled={settled}"


def test_a_saturated_bound_coalesces_instead_of_queueing() -> None:
    """Over the bound, work is skipped -- never queued into unbounded pending state."""

    release = threading.Event()
    extractor = _HangingExtractor(release)
    try:
        started = [extractor.trigger_async([{"role": "user", "content": "z"}]) for _ in range(20)]
        admitted = [t for t in started if t is not None]
        coalesced = [t for t in started if t is None]
        assert len(admitted) <= fx._MAX_CONCURRENT_EXTRACTIONS
        assert coalesced, "a saturated bound must coalesce, not admit everything"
    finally:
        _drain(release)


def test_a_released_slot_is_reusable_so_work_still_runs_later() -> None:
    """Bounding must not become a permanent stop: the next turn still extracts."""

    first_release = threading.Event()
    first = _HangingExtractor(first_release)
    admitted = [first.trigger_async([{"role": "user", "content": "a"}]) for _ in range(5)]
    assert any(t is not None for t in admitted)
    _drain(first_release)

    second_release = threading.Event()
    second = _HangingExtractor(second_release)
    try:
        again = second.trigger_async([{"role": "user", "content": "b"}])
        assert again is not None, "slots were not returned after the earlier work finished"
        assert second.started.acquire(timeout=10), "the admitted work never ran"
    finally:
        _drain(second_release)


def test_background_work_that_raises_still_returns_its_slot() -> None:
    """The failure path must release the bound, or one exception wedges extraction forever."""

    class _Exploding(fx.FactExtractor):
        def __init__(self) -> None:
            self.ran = threading.Semaphore(0)
            self._close_memory_on_finish = False

        def _run(self, messages: list[dict]):  # type: ignore[override]
            # Raised inside `_run` so production's own `_run_safely` handles it -- the real
            # failure path, not an exception escaping the thread.
            self.ran.release()
            raise RuntimeError("background failure")

    extractor = _Exploding()
    for _ in range(fx._MAX_CONCURRENT_EXTRACTIONS + 3):
        extractor.trigger_async([{"role": "user", "content": "boom"}])
        assert extractor.ran.acquire(timeout=10)
    deadline = time.monotonic() + 10
    while _extractor_threads() and time.monotonic() < deadline:
        time.sleep(0.02)

    assert not _extractor_threads()
    # Slots recovered: a fresh trigger is still admitted after repeated failures.
    release = threading.Event()
    survivor = _HangingExtractor(release)
    try:
        assert survivor.trigger_async([{"role": "user", "content": "after"}]) is not None
    finally:
        _drain(release)


def test_concurrent_callers_stay_within_the_bound() -> None:
    """Several turns firing at once must not multiply past the ceiling."""

    release = threading.Event()
    extractor = _HangingExtractor(release)
    peak = 0
    lock = threading.Lock()

    def burst() -> None:
        nonlocal peak
        for _ in range(10):
            extractor.trigger_async([{"role": "user", "content": "c"}])
            with lock:
                peak = max(peak, len(_extractor_threads()))

    try:
        callers = [threading.Thread(target=burst) for _ in range(6)]
        for t in callers:
            t.start()
        for t in callers:
            t.join(timeout=20)
        assert peak <= fx._MAX_CONCURRENT_EXTRACTIONS, f"peak={peak}"
    finally:
        _drain(release)


def test_a_completed_worker_leaves_no_database_descriptor_behind() -> None:
    """Behavioural, not source-inspection: descriptors must come back per completed worker.

    Each extraction opens a thread-local SQLite connection for the memory write. Nothing else can
    hold a thread-local, and the thread is about to vanish, so the worker is the only boundary that
    can return it. Without that hand-back every completed extraction strands the DB handle and its
    WAL descriptor for the life of the process -- invisible under a high limit, fatal under 256.
    """

    from storage.db import get_connection

    class _TouchesTheDatabase(fx.FactExtractor):
        def __init__(self) -> None:
            self.done = threading.Semaphore(0)
            self._close_memory_on_finish = False

        def _run(self, messages: list[dict]):  # type: ignore[override]
            connection = get_connection()
            connection.execute("SELECT 1").fetchone()
            self.done.release()
            return []

    extractor = _TouchesTheDatabase()
    # Warm the process once so first-touch schema/pragma work is not counted as growth.
    extractor.trigger_async([{"role": "user", "content": "warm"}])
    assert extractor.done.acquire(timeout=20)
    deadline = time.monotonic() + 20
    while _extractor_threads() and time.monotonic() < deadline:
        time.sleep(0.02)

    baseline = _open_fds()
    completed = 0
    for _ in range(24):
        if extractor.trigger_async([{"role": "user", "content": "db"}]) is None:
            continue
        completed += 1
        assert extractor.done.acquire(timeout=20)
    deadline = time.monotonic() + 20
    while _extractor_threads() and time.monotonic() < deadline:
        time.sleep(0.02)
    settled = _open_fds()

    assert completed >= 8, f"only {completed} workers ran; the measurement would prove nothing"
    # Each stranded connection costs a DB handle plus its WAL descriptor, so an unreleased
    # thread-local would put this far above the small allowance below.
    assert settled - baseline <= 4, (
        f"{completed} completed workers left {settled - baseline} descriptors behind "
        f"(baseline={baseline} settled={settled})"
    )


@pytest.mark.parametrize("rounds", (3, 9))
def test_the_invariant_holds_for_unseen_round_counts(rounds: int) -> None:
    """Anti-overfit: the bound is a property, not a tuned number for one workload shape."""

    release = threading.Event()
    extractor = _HangingExtractor(release)
    try:
        for _ in range(rounds):
            for _ in range(7):
                extractor.trigger_async([{"role": "user", "content": "r"}])
            assert len(_extractor_threads()) <= fx._MAX_CONCURRENT_EXTRACTIONS
    finally:
        _drain(release)
