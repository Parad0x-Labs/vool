"""The council's ownership of the ONE server-global model pin, enforced at the server.

A council swaps the machine's composer pin per cloud seat (``core/council/dispatch.py``)
and restores the operator's in a ``finally``. For the length of a run the pin therefore
names a seat's model, not the operator's — and any ``/api/chat`` turn that resolves its
model in that window is answered by whichever seat happened to be pinned at that instant.

C3 closed the originating chat's composer in the browser. That is a promise the browser
cannot keep: a second tab, a different chat, a client that never loaded the page, or a
bare ``curl`` all reach the same door. This module is where the promise is actually kept.

**Shape.** One process-global owner, guarded by one condition variable, holding:

* ``run_id`` and ``state`` — safe to serve, and served by ``/api/council/lock`` so every
  composer reflects the same server truth instead of its own DOM;
* a **capability** minted here, handed only to the council's own seat dispatcher, and
  presented on the ``X-Vool-Council-Dispatch`` request header. It is never written to the
  run state, never appended to the ledger, never served by any read surface, and is
  accepted only from loopback. Nothing derivable from public material — the run id, a seat
  session id — is accepted in its place, because both are things a caller can learn.

**The race is closed, not narrowed.** Admission and acquisition serialize on the same
lock. An ordinary turn is admitted only while nothing owns the pin and no council is
trying to take it; ``acquire`` marks itself pending (so later turns are refused), then
WAITS for already-admitted turns to finish before it pins. A turn is therefore either
admitted strictly before ownership, or refused strictly before it selects a model. If the
in-flight turns will not drain inside the bound, ``acquire`` REFUSES — the council does
not pin over a running turn and then hope.

**Restart is by construction.** Ownership lives in this process and nowhere else. A run
state file that says ``round_open`` describes a run whose thread died with the last
daemon; it cannot lock a chat, because there is nothing on disk for this module to read.
``reset_on_startup`` states that explicitly at the serving app's boot.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

#: How long ``acquire`` waits for already-admitted ordinary turns to finish before it
#: gives up. Bounded on purpose: an unbounded wait would let one wedged turn make
#: "convene" hang forever, and pinning anyway would be the exact defect this closes.
_DRAIN_TIMEOUT_SECONDS = 20.0

#: There is NO age at which a live admission is forgiven. An earlier version dropped any
#: admission older than 45 minutes, reasoning that no real turn runs that long. VOOL turns
#: demonstrably do — a long agent task streams for hours — and the effect was that a turn
#: still writing was deleted from the count and a council pinned straight over it. The
#: fence's whole job, defeated by a clock.
#:
#: An admission ends when the turn ends: an explicit (idempotent) release, the streaming
#: generator's finalization (normal exhaustion, client disconnect, or collection), a
#: cancellation that closes that stream, or the process restarting. Nothing else. If they
#: do not drain inside the bounded wait, `acquire` REFUSES — uncertainty is a refusal, not
#: a licence to pin.

#: What the caller is told to wait for: the states at which the pin comes back. Named
#: states, not a number of seconds — how long a council takes is not knowable, and a
#: fabricated retry-after would be a guess. `needs_attention` is on the list because a
#: council paused for the operator releases the pin too: it is not an ending, but it IS a
#: moment the composer returns. `failed` stays for runs on disk that predate C4.
TERMINAL_STATES = (
    "converged", "failed", "no_convergence", "stopped", "crashed", "needs_attention",
)

REFUSAL_ERROR = "council_model_pin_active"

_CONDITION = threading.Condition(threading.RLock())


@dataclass(frozen=True)
class _PinOwner:
    run_id: str
    state: str
    since: float
    capability: str


_owner: _PinOwner | None = None
#: The run currently trying to take the pin. New ordinary turns are refused from this
#: instant, which is what makes the drain below terminate.
_pending_run: str = ""
_admissions: dict[str, float] = {}


class CouncilPinBusyError(RuntimeError):
    """``acquire`` could not take the pin. Carries the typed reason for the 409."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass
class Admission:
    """One admitted (or refused) ordinary chat turn.

    ``release`` is idempotent and safe from any thread: the streaming lane hands it to a
    generator's ``finally``, which may run on collection rather than on a clean close.
    """

    refusal: dict[str, Any] | None = None
    token: str = ""
    seat: bool = False
    _released: bool = field(default=False, repr=False)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if not self.token:
            return
        with _CONDITION:
            _admissions.pop(self.token, None)
            _CONDITION.notify_all()


def _live_admissions_locked() -> int:
    """How many ordinary turns are still running. Counted, never aged out."""
    return len(_admissions)


def _oldest_admission_age_locked() -> float:
    if not _admissions:
        return 0.0
    return max(0.0, time.monotonic() - min(_admissions.values()))


def inflight_admissions() -> int:
    """Ordinary turns currently admitted. A turn is in flight until IT says otherwise."""
    with _CONDITION:
        return _live_admissions_locked()


def _refusal_locked() -> dict[str, Any]:
    """The typed 409 body. Everything in it is already public; the capability is not."""
    if _owner is not None:
        run_id, state, since = _owner.run_id, _owner.state, _owner.since
    else:
        run_id, state, since = _pending_run, "convening", time.time()
    return {
        "error": REFUSAL_ERROR,
        "run_id": run_id,
        "state": state,
        "since": since,
        "retry_after_state": list(TERMINAL_STATES),
        "detail": (
            f"a council ({run_id}) currently owns this machine's model pin, so an ordinary "
            "turn sent now could be answered by one of its seat models. The chat reopens by "
            "itself when the run reaches a terminal state — or stop the council from the "
            "card in the chat it was convened from."
        ),
    }


def _admit() -> Admission:
    """The one admission core. Both doors onto the global pin come through here, so a
    chat turn and a model write are the same kind of fact to `acquire`'s drain."""
    presented_owner = _owner is not None or bool(_pending_run)
    if presented_owner:
        return Admission(refusal=_refusal_locked())
    token = uuid.uuid4().hex
    _admissions[token] = time.monotonic()
    return Admission(token=token)


def is_dispatch_capability(capability: str, *, owner_local: bool = False) -> bool:
    """True only for the live run's own capability, presented from loopback.

    Constant-time compared. A capability that leaked off the machine is not a key to the
    front door: the bypass is loopback-only, like every other council surface.
    """
    presented = str(capability or "")
    if not presented or not owner_local:
        return False
    with _CONDITION:
        return _owner is not None and hmac.compare_digest(presented, _owner.capability)


def admit_chat_turn(*, capability: str = "", owner_local: bool = False) -> Admission:
    """Admit or refuse ONE ordinary ``/api/chat`` turn.

    A valid capability presented from loopback is the council's own seat dispatch and
    passes without being counted — it is the pin's own user, not a racer for it.
    """
    if is_dispatch_capability(capability, owner_local=owner_local):
        return Admission(seat=True)
    with _CONDITION:
        return _admit()


def admit_model_write(*, capability: str = "", owner_local: bool = False) -> Admission:
    """Admit or refuse ONE ordinary write to the global composer pin.

    The pin a council holds and the pin ``POST /api/cloud/model`` sets are the same
    global state, so a model write is a user of the pin exactly as a turn is. It takes a
    real admission rather than a point-in-time check: held across the mutation, it makes
    `acquire` wait for the write to land instead of pinning through it.

    The council's own two writes — the per-seat pin and the final restoration — carry the
    run's capability and pass without being counted, for the same reason a seat turn does.
    """
    if is_dispatch_capability(capability, owner_local=owner_local):
        return Admission(seat=True)
    with _CONDITION:
        return _admit()


def refuse_pin_write_reason(capability: str = "", *, owner_local: bool = False) -> str:
    """A sentence for a NON-HTTP writer refused by the fence, or "" when it may write.

    `set_cloud_model` is the one mutation authority every surface converges on — the chat
    command and the natural-language switch intent reach it without passing through the
    HTTP endpoint at all. Guarding there means a writer added later is fenced by
    construction rather than by remembering to ask.
    """
    if is_dispatch_capability(capability, owner_local=owner_local):
        return ""
    with _CONDITION:
        if _owner is None and not _pending_run:
            return ""
        run_id = _owner.run_id if _owner is not None else _pending_run
    return (
        f"A council ({run_id}) currently owns this machine's model pin, so the model was "
        "left unchanged. The pin is handed back when the run reaches a terminal state."
    )


def pin_write_refusal(capability: str = "", *, owner_local: bool = False) -> dict[str, Any] | None:
    """The typed 409 body for a refused pin write, or None when the write may proceed.

    Answered BEFORE any classification or pricing work at the endpoint: a refusal that
    first classifies has already done the work it refused, and told the caller about the
    pricing state of an id it is not going to let them pin.
    """
    if is_dispatch_capability(capability, owner_local=owner_local):
        return None
    with _CONDITION:
        if _owner is None and not _pending_run:
            return None
        return _refusal_locked()


def acquire(run_id: str, *, state: str = "convened") -> str:
    """Take the pin for ``run_id`` and return the seat-dispatch capability.

    Raises :class:`CouncilPinBusyError` rather than pinning when another council holds it, or
    when an ordinary turn already admitted will not finish inside the drain bound.
    """
    global _owner, _pending_run
    clean = str(run_id or "").strip()
    if not clean:
        raise CouncilPinBusyError("invalid_run_id", "a pin owner needs a run id")
    with _CONDITION:
        if _owner is not None:
            raise CouncilPinBusyError(
                "council_model_pin_active",
                f"council {_owner.run_id} already owns the model pin",
            )
        if _pending_run:
            raise CouncilPinBusyError(
                "council_model_pin_active",
                f"council {_pending_run} is already taking the model pin",
            )
        _pending_run = clean
        try:
            deadline = time.monotonic() + float(_DRAIN_TIMEOUT_SECONDS)
            while _live_admissions_locked() > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    held = _live_admissions_locked()
                    oldest = _oldest_admission_age_locked()
                    raise CouncilPinBusyError(
                        "chat_turn_in_flight",
                        f"{held} chat turn(s) are still running (the oldest for "
                        f"{oldest:.0f}s) and the council will not take the model pin out "
                        "from under them — let them finish or stop them, then convene",
                    )
                _CONDITION.wait(remaining)
            capability = secrets.token_urlsafe(32)
            _owner = _PinOwner(clean, str(state or "convened"), time.time(), capability)
            return capability
        finally:
            _pending_run = ""
            _CONDITION.notify_all()


def note_state(run_id: str, state: str) -> None:
    """Keep the served lock state honest as the run moves. Never changes ownership."""
    global _owner
    with _CONDITION:
        if _owner is not None and _owner.run_id == str(run_id or "").strip():
            _owner = _PinOwner(_owner.run_id, str(state or _owner.state),
                               _owner.since, _owner.capability)


def release(run_id: str) -> bool:
    """Hand the pin back. True when this call is what released it."""
    global _owner
    with _CONDITION:
        if _owner is None or _owner.run_id != str(run_id or "").strip():
            return False
        _owner = None
        _CONDITION.notify_all()
        return True


def snapshot() -> dict[str, Any] | None:
    """What OWNS the pin right now, or None. Never carries the capability.

    A council merely *taking* the pin is not owning it: during the drain wait an already
    admitted turn is still the pin's rightful user, and telling it otherwise would be a
    claim about a state that has not happened.
    """
    with _CONDITION:
        if _owner is None:
            return None
        return {
            "run_id": _owner.run_id,
            "state": _owner.state,
            "since": _owner.since,
            "seconds": max(0.0, time.time() - _owner.since),
        }


def lock_payload() -> dict[str, Any]:
    """The body of ``/api/council/lock`` — what every composer reads instead of its DOM."""
    current = snapshot()
    if current is None:
        return {"ok": True, "locked": False, "run_id": "", "state": "", "reason": ""}
    return {
        "ok": True,
        "locked": True,
        "run_id": current["run_id"],
        "state": current["state"],
        "since": current["since"],
        "seconds": current["seconds"],
        "reason": (
            "Council in session — it owns this machine's model pin until the run ends, so "
            "a message sent now could answer on a seat's model. Every chat is paused, not "
            "just the one the council was convened from. The composer returns by itself "
            "when the run reaches a terminal state."
        ),
    }


def dispatch_capability_for_tests() -> str:
    """The live capability, for tests that must present a VALID one.

    In-process only. No HTTP surface calls this, and none may: the whole point of the
    capability is that it exists nowhere a caller can read it.
    """
    with _CONDITION:
        return _owner.capability if _owner is not None else ""


def reset_on_startup() -> None:
    """Drop every ownership claim. Called at the serving app's boot.

    Ownership is a fact about a live thread in THIS process. Nothing here is read from
    disk, so a persisted "round_open" run cannot lock a chat after a restart — this
    states that rather than leaving it to be inferred from the absence of a loader.
    """
    global _owner, _pending_run
    with _CONDITION:
        _owner = None
        _pending_run = ""
        _admissions.clear()
        _CONDITION.notify_all()
