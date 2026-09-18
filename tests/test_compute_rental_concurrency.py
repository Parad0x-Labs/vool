"""
tests/test_compute_rental_concurrency.py
========================================
Regression tests for the ComputeRentalMarket concurrency guards.

Two races were possible before the per-market lock was added:

1.  release(): the check-then-set on ``session.active`` ("if not active: raise"
    then "active = False") was not atomic. Two threads could both observe
    ``active is True``, both flip it, both re-mark the listing available, and
    both emit a WorkProof — a double settlement.

2.  rent(): the availability check ("if not listing.available") and the claim
    ("listing.available = False") were not atomic. Two renters could both pass
    the check and double-book a single-tenant listing.

These tests force a deterministic interleave at the read inside each critical
section so that, without the lock, both callers proceed. With the lock the
second caller blocks until the first finishes the check-and-flip and then loses.

Pre-fix: both succeed (assertions fail). Post-fix: exactly one succeeds.

The interleave is driven by a ``threading.Barrier`` wired into the boolean
attribute the guard reads, so the test does not depend on the presence or the
internals of the lock itself.
"""
from __future__ import annotations

import contextlib
import threading
import uuid

from core.compute.rental_market import (
    ComputeListing,
    ComputeRentalMarket,
    RentalSession,
)


def _listing() -> ComputeListing:
    return ComputeListing(
        node_id=f"node-{uuid.uuid4().hex[:8]}",
        endpoint="http://localhost:7860",
        hardware={},
        tokens_per_second=50,
        price_per_1k_tokens=1.0,
        currency="NULL",  # no x402 path; keeps the test offline + fast
        min_rental_minutes=1,
        available=True,
    )


class _BarrierFlag:
    """A boolean-like flag whose first reads block on a barrier.

    ``read()`` samples the current value, then (until the barrier has tripped)
    waits on it. The pre-wait *snapshot* is returned, so two concurrent callers
    both observe the old value even though one writes False right after the
    barrier releases — exactly the window the lock must close. After the barrier
    trips once, reads/writes behave like a plain bool.

    The snapshot-before-wait ordering is essential: returning the live value
    after the wait would let the GIL serialize the second caller behind the
    first's write, hiding the race.
    """

    def __init__(self, value: bool, barrier: threading.Barrier):
        self._value = value
        self._barrier = barrier
        self._tripped = False
        self._guard = threading.Lock()

    def read(self) -> bool:
        snapshot = self._value  # sample BEFORE blocking
        with self._guard:
            do_wait = not self._tripped
        if do_wait:
            with contextlib.suppress(threading.BrokenBarrierError):
                self._barrier.wait(timeout=2.0)
            with self._guard:
                self._tripped = True
        return snapshot

    def write(self, value: bool) -> None:
        self._value = value

    @property
    def value(self) -> bool:
        return self._value


def _run_two(target, *args):
    """Run ``target(*args)`` on two threads; collect (result, exc) per thread."""
    results: list = [None, None]
    errors: list = [None, None]

    def worker(i: int) -> None:
        try:
            results[i] = target(*args)
        except Exception as exc:  # we assert on the type below
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    return results, errors


# ────────────────────────────────────────────────────────────────────────────
# release(): no double-settlement under concurrency
# ────────────────────────────────────────────────────────────────────────────

class TestConcurrentRelease:
    def test_two_concurrent_releases_only_one_succeeds(self):
        market = ComputeRentalMarket()
        listing = _listing()
        market._listings[listing.node_id] = listing
        session = market.rent(listing, duration_minutes=1)
        session.tokens_generated = 1000

        # Force both threads to read session.active before either writes it.
        barrier = threading.Barrier(2)
        active_box = _BarrierFlag(True, barrier)

        class _RacingSession:
            """Proxy that delays the first two reads of ``active`` on a barrier
            while delegating everything else to the real session."""

            def __init__(self, real: RentalSession):
                object.__setattr__(self, "_real", real)

            @property
            def active(self) -> bool:
                return active_box.read()

            @active.setter
            def active(self, value: bool) -> None:
                active_box.write(value)

            def __getattr__(self, name):
                return getattr(object.__getattribute__(self, "_real"), name)

        racing = _RacingSession(session)

        results, errors = _run_two(market.release, racing)

        proofs = [r for r in results if r is not None]
        already_closed = [
            e for e in errors
            if isinstance(e, ValueError) and "already closed" in str(e)
        ]

        # Exactly one release wins (one WorkProof), the other is rejected.
        assert len(proofs) == 1, (
            f"expected exactly one WorkProof, got {len(proofs)} "
            f"(double settlement); errors={errors}"
        )
        assert len(already_closed) == 1, (
            f"expected the losing release to raise 'already closed', "
            f"got results={results} errors={errors}"
        )
        assert active_box.value is False


# ────────────────────────────────────────────────────────────────────────────
# rent(): no double-booking of a single-tenant listing under concurrency
# ────────────────────────────────────────────────────────────────────────────

class TestConcurrentRent:
    def test_two_concurrent_rents_only_one_succeeds(self):
        market = ComputeRentalMarket()
        listing = _listing()
        market._listings[listing.node_id] = listing

        barrier = threading.Barrier(2)
        available_box = _BarrierFlag(True, barrier)

        class _RacingListing:
            """Proxy delaying the first two reads of ``available`` on a barrier
            while delegating everything else to the real listing."""

            def __init__(self, real: ComputeListing):
                object.__setattr__(self, "_real", real)

            @property
            def available(self) -> bool:
                return available_box.read()

            @available.setter
            def available(self, value: bool) -> None:
                available_box.write(value)

            def __getattr__(self, name):
                return getattr(object.__getattribute__(self, "_real"), name)

        racing = _RacingListing(listing)

        results, errors = _run_two(market.rent, racing, 1)

        sessions = [r for r in results if r is not None]
        not_available = [
            e for e in errors
            if isinstance(e, ValueError) and "not available" in str(e)
        ]

        # Exactly one rent wins; the single-tenant listing is not double-booked.
        assert len(sessions) == 1, (
            f"expected exactly one RentalSession, got {len(sessions)} "
            f"(double booking); errors={errors}"
        )
        assert len(not_available) == 1, (
            f"expected the losing rent to raise 'not available', "
            f"got results={results} errors={errors}"
        )
        assert available_box.value is False
