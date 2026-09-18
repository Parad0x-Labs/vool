"""One conversation advances one turn at a time.

Measured 2026-08-15 (MF-15): two /api/chat POSTs 300 ms apart on one session ran
concurrently; the fast turn's answer landed in the transcript BETWEEN the slow
turn's question and its answer, so read in order every pairing from that point
was wrong -- the operator's 50-question batch showed answers pairing off-by-one
("List B starts as [5, 6, 7]..." was answered with a greeting, and the greeting
turn's answer drifted onto the next question). The second turn also executed
without the first turn's exchange in its context, because history hydration ran
before the first turn had persisted anything.

The gate is a per-session FIFO ticket lock:

- turns of ONE session run in arrival order, one at a time;
- DISTINCT sessions never wait on each other;
- reentrant within a thread, so a gated turn that re-enters the runner for the
  same session cannot deadlock itself;
- the ticket is taken under the registry lock, so cap eviction can never race a
  thread that has been handed a gate but has not started waiting yet.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = ["hold_session_turn_gate"]


class _SessionGate:
    __slots__ = ("cond", "depth", "next_ticket", "now_serving", "owner")

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.next_ticket = 0
        self.now_serving = 0
        self.owner: int | None = None
        self.depth = 0

    def take_ticket(self) -> int | None:
        """Reserve a place in line; None means the calling thread already holds the gate."""
        me = threading.get_ident()
        with self.cond:
            if self.owner == me:
                self.depth += 1
                return None
            ticket = self.next_ticket
            self.next_ticket += 1
            return ticket

    def wait_for_turn(self, ticket: int) -> None:
        with self.cond:
            while self.now_serving != ticket:
                self.cond.wait()
            self.owner = threading.get_ident()
            self.depth = 1

    def release(self) -> None:
        with self.cond:
            self.depth -= 1
            if self.depth > 0:
                return
            self.owner = None
            self.now_serving += 1
            self.cond.notify_all()

    def busy(self) -> bool:
        with self.cond:
            return self.owner is not None or self.next_ticket != self.now_serving


_REGISTRY_LOCK = threading.Lock()
_GATES: OrderedDict[str, _SessionGate] = OrderedDict()
# Bounds bookkeeping, not concurrency: an idle session's gate is just a few ints,
# and a gate with any outstanding ticket is never evicted.
_MAX_TRACKED_SESSIONS = 4096


def _gate_and_ticket(session_key: str) -> tuple[_SessionGate, int | None]:
    with _REGISTRY_LOCK:
        gate = _GATES.get(session_key)
        if gate is None:
            gate = _SessionGate()
            _GATES[session_key] = gate
        else:
            _GATES.move_to_end(session_key)
        # Ticket taken while the registry lock is held: from this moment the gate
        # is visibly busy, so the eviction sweep below (and any future sweep)
        # cannot drop a gate someone is about to wait on.
        ticket = gate.take_ticket()
        while len(_GATES) > _MAX_TRACKED_SESSIONS:
            evicted = False
            for key in list(_GATES):
                if key != session_key and not _GATES[key].busy():
                    _GATES.pop(key)
                    evicted = True
                    break
            if not evicted:
                break
        return gate, ticket


@contextmanager
def hold_session_turn_gate(session_id: str | None) -> Iterator[None]:
    """Serialize the enclosed turn against every other turn of the same session.

    A falsy session id means the turn belongs to no conversation; it runs
    ungated, exactly as before this module existed.
    """
    key = str(session_id or "").strip()
    if not key:
        yield
        return
    gate, ticket = _gate_and_ticket(key)
    if ticket is None:
        # Reentrant hold by the same thread; take_ticket already bumped depth.
        try:
            yield
        finally:
            gate.release()
        return
    gate.wait_for_turn(ticket)
    try:
        yield
    finally:
        gate.release()
