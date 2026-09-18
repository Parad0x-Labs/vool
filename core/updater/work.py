"""Pause-new-work coordination, the destructive-work guard, and active-state receipts.

The install authority may not restart the app while work that can change or delete
things is still running, unless the operator explicitly resolves to proceed anyway.
"Explicit resolution" is a typed object naming the exact work ids being overridden —
never a bare force flag, so the override is auditable in the journal and the receipt.

The in-memory coordinator is the foundation contract; the daemon-side wiring (turn
lifecycle registering handles) is a documented integration seam, not a guess.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class WorkHandle:
    id: str
    description: str
    destructive: bool = False
    pause: Callable[[], None] | None = None


class PauseReason(Enum):
    OK = "ok"
    DESTRUCTIVE_WORK_ACTIVE = "destructive_work_active"


@dataclass(frozen=True)
class DestructiveWorkResolution:
    """The operator's explicit decision to restart despite listed destructive work."""

    operator_ack: str
    work_ids: tuple[str, ...]

    def covers(self, handles: list[WorkHandle]) -> bool:
        return all(handle.id in self.work_ids for handle in handles)


@dataclass
class PauseDecision:
    ok: bool
    reason: PauseReason
    destructive: list[WorkHandle] = field(default_factory=list)

    @property
    def plain_message(self) -> str:
        if self.reason is PauseReason.OK:
            return "New work is paused and it's safe to restart."
        names = "; ".join(f"{h.description or h.id}" for h in self.destructive) or "background work"
        return (
            "The app is busy with work that could change or delete things "
            f"({names}). It won't restart for the update until that finishes, "
            "unless you explicitly choose to continue anyway."
        )


class WorkCoordinator:
    """Registry of in-flight work. Handles register at start, complete at end."""

    def __init__(self) -> None:
        self._active: dict[str, WorkHandle] = {}

    def register(self, handle: WorkHandle) -> WorkHandle:
        self._active[handle.id] = handle
        return handle

    def complete(self, handle_id: str) -> None:
        self._active.pop(str(handle_id), None)

    def active(self) -> list[WorkHandle]:
        return list(self._active.values())

    def active_destructive(self) -> list[WorkHandle]:
        return [h for h in self._active.values() if h.destructive]


def destructive_block(
    coordinator: WorkCoordinator, *, resolution: DestructiveWorkResolution | None = None
) -> PauseDecision:
    """The refusal HALF of the pause gate, with no side effects: presses consult this
    synchronously (the API answers 409 honestly) while the flow still runs the full
    authoritative gate at the seam."""
    destructive = coordinator.active_destructive()
    if destructive and (resolution is None or not resolution.covers(destructive)):
        return PauseDecision(False, PauseReason.DESTRUCTIVE_WORK_ACTIVE, destructive)
    return PauseDecision(True, PauseReason.OK, destructive)


def prepare_to_pause(
    coordinator: WorkCoordinator, *, resolution: DestructiveWorkResolution | None = None
) -> PauseDecision:
    """Pause-all gate: refuses while destructive work runs unless explicitly resolved.
    Non-destructive work is paused (its hooks run) and the decision succeeds."""
    destructive = coordinator.active_destructive()
    if destructive and (resolution is None or not resolution.covers(destructive)):
        return PauseDecision(False, PauseReason.DESTRUCTIVE_WORK_ACTIVE, destructive)  # pragma: no cover - mirrored by destructive_block
    for handle in coordinator.active():
        if handle.pause is not None:
            try:
                handle.pause()
            except Exception:  # a pause hook failure still pauses nothing silently —
                # the work stays registered, so the receipt tells the truth.
                continue
    return PauseDecision(True, PauseReason.OK, destructive)


@dataclass
class ActiveStateReceipt:
    """What was happening when the update asked the app to restart. Written BEFORE
    shutdown, kept under user data, and named in the journal — restarts are auditable."""

    txid: str
    created_at: float
    active_work: list[dict]
    destructive_work: list[dict]

    def to_dict(self) -> dict:
        return {
            "txid": self.txid,
            "created_at": self.created_at,
            "active_work": list(self.active_work),
            "destructive_work": list(self.destructive_work),
        }

    @classmethod
    def capture(cls, txid: str, coordinator: WorkCoordinator, *, now: float | None = None) -> ActiveStateReceipt:
        active = coordinator.active()
        return cls(
            txid=str(txid),
            created_at=now if now is not None else time.time(),
            active_work=[{"id": h.id, "description": h.description, "destructive": h.destructive} for h in active],
            destructive_work=[
                {"id": h.id, "description": h.description} for h in active if h.destructive
            ],
        )


__all__ = [
    "ActiveStateReceipt",
    "DestructiveWorkResolution",
    "PauseDecision",
    "PauseReason",
    "WorkCoordinator",
    "WorkHandle",
    "destructive_block",
    "prepare_to_pause",
]
