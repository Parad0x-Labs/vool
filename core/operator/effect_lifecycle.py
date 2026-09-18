"""One lifecycle law for the externally visible effects the operator lane performs.

Two destinations use it, each keeping its own durable store:

* provider calendar operations -- the operator approval store (``core.operator.approvals``);
* native Apple Notes deliveries -- the workspace delivery journal (``core.operator.notes``).

This module owns only what they share: the evidence ladder an operation climbs, the resting
state each rung maps to, and the OS-backed owner lock that proves a worker is alive. It makes
no provider call and holds no policy; approval stays with the approval door and adapters stay
translators (docs/KAS_BOUNDARY.md).

Evidence ladder -- monotonic; a later write never drops a rung:

    unsent       nothing crossed the effect boundary, or the provider definitively refused
                 the write itself (positive evidence of non-application)
    dispatching  the write was about to cross, or crossed; its outcome is not established
    accepted     the provider acknowledged the write; a concrete provider identity may be known
    verified     a read-back of that identity matched the approved content
    recorded     the receipt is durably persisted (terminal)

The only way DOWN the ladder is a definitive refusal of the write itself. A missing reply, a
refused or failed read, an undecodable body, or a receipt that could not be written never
moves an operation down: those prove nothing about the effect.

Ownership: an operation in flight belongs to exactly one live worker, proven by an exclusive
OS lock (``core.cross_process_lock.PublicationLock``: flock on POSIX, msvcrt.locking on
Windows) held for the whole attempt. The kernel releases it when the owner exits for any
reason, so a lock that can be taken is proof no live worker holds the operation -- never a
timeout, a heartbeat age or a bare PID. Durable writes are also fenced by an owner token and a
fence number that only grows, so a worker that lost ownership cannot alter the winner's state.
Lock files are never deleted: unlinking one while another process has it open would let two
workers lock two different inodes for the same operation.
"""
from __future__ import annotations

import hashlib
import os
import time

from core.cross_process_lock import LockUnavailable, PublicationLock

PHASE_UNSENT = "unsent"
PHASE_DISPATCHING = "dispatching"
PHASE_ACCEPTED = "accepted"
PHASE_VERIFIED = "verified"
PHASE_RECORDED = "recorded"
PHASES = (PHASE_UNSENT, PHASE_DISPATCHING, PHASE_ACCEPTED, PHASE_VERIFIED, PHASE_RECORDED)
_RANK = {name: index for index, name in enumerate(PHASES)}

#: Approval-store states an attempt may come to rest in without a receipt.
RESUMABLE_STATUSES = ("pending_approval", "outcome_unproven", "effect_unrecorded")


def phase_rank(phase: str | None) -> int:
    return _RANK.get(str(phase or PHASE_UNSENT), 0)


def higher_phase(current: str | None, proposed: str | None) -> str:
    """The later of two rungs: evidence is only ever added."""
    current_name = str(current or PHASE_UNSENT)
    proposed_name = str(proposed or PHASE_UNSENT)
    return current_name if phase_rank(current_name) >= phase_rank(proposed_name) else proposed_name


def resting_status(phase: str | None, *, entry_status: str) -> str:
    """Where an operation rests when its attempt ends WITHOUT a new receipt.

    Decided by durable evidence, never by the code path that happened to exit: a verified
    effect is verify-only (``effect_unrecorded``); anything that crossed the boundary is
    ``outcome_unproven``; an attempt that never crossed returns to the state it entered from.
    """
    rank = phase_rank(phase)
    if rank >= _RANK[PHASE_RECORDED]:
        return "executed"
    if rank >= _RANK[PHASE_VERIFIED]:
        return "effect_unrecorded"
    if rank >= _RANK[PHASE_DISPATCHING]:
        return "outcome_unproven"
    return entry_status if entry_status in RESUMABLE_STATUSES else "pending_approval"


def owner_lock_path(lock_root: str | os.PathLike[str], operation_id: str) -> str:
    """The one lock file for one operation under a store's lock root."""
    digest = hashlib.sha256(str(operation_id).encode("utf-8")).hexdigest()[:40]
    return os.path.join(os.fspath(lock_root), f"{digest}.lock")


class OwnerLock:
    """Exclusive, non-blocking, OS-backed ownership of one operation (or one journal)."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = os.fspath(path)
        self._lock = PublicationLock(self.path)
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def try_acquire(self) -> bool:
        """True when this worker now owns the lock. False when a live worker holds it, or the
        lock cannot be taken at all -- both mean ownership cannot be proven here."""
        if self._held:
            return True
        try:
            self._lock.__enter__()
        except LockUnavailable:
            return False
        self._held = True
        return True

    def acquire_within(self, seconds: float, *, step_seconds: float = 0.01) -> bool:
        """Bounded wait for a SHORT critical section (a small journal read/write).

        WALL-CLOCK bound, not a liveness rule: it exists so a wedged holder cannot hang the
        caller, which then fails closed with nothing dispatched. Never use it to decide that
        an operation's owner is dead -- ``try_acquire`` on the owner lock is that proof.
        """
        deadline = time.monotonic() + max(0.0, float(seconds))
        while True:
            if self.try_acquire():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(step_seconds)

    def release(self) -> None:
        if self._held:
            self._held = False
            self._lock.__exit__(None, None, None)


def owner_provenance() -> dict[str, int]:
    """Who took an operation, for the record only. Liveness is the lock, never this pid."""
    return {"pid": os.getpid()}


__all__ = [
    "PHASES",
    "PHASE_ACCEPTED",
    "PHASE_DISPATCHING",
    "PHASE_RECORDED",
    "PHASE_UNSENT",
    "PHASE_VERIFIED",
    "RESUMABLE_STATUSES",
    "OwnerLock",
    "higher_phase",
    "owner_lock_path",
    "owner_provenance",
    "phase_rank",
    "resting_status",
]
