"""The write receipt of one turn's saved memory.

Measured on the v0.8 line (job 22 review, the memory and context diagnosis of 2026-11-16): whether a fact was saved
was decided on the person's wording, whether the reply claimed a save was decided on the reply's wording, and nothing
joined the two. "I'll keep that safe" was published with no row, and a reply beside a refused passcode said "Locked
in".

A receipt is what the one writer (`core.memory.entries.add_memory_fact`) did during a turn's keep step: the facts it
wrote, and the reasons a write was refused. The memory door (`core.agent_runtime.memory_runtime`) opens it around the
memory lane and the keep step and stamps it on the turn; the claim gate (`core.action_claim_gate`) reads it. A turn
with no receipt is a turn the keep step never saw, and is judged as before.
"""
from __future__ import annotations

import contextvars
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

#: The turn-context key the receipt rides under. Reserved (core.request_trust): a caller-written receipt would let a
#: client vouch for a save that never happened.
CONTEXT_KEY = "_memory_keep_receipt"

REFUSED_SECRET = "secret"
REFUSED_PRIVATE_CHAT = "private_chat"
REFUSED_AGENT_TURN = "agent_turn"
REFUSED_FORGOTTEN_HERE = "forgotten_here"


@dataclass
class KeepReceipt:
    """What the keep step wrote and refused in one turn."""

    saved: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    #: The rows were written from what the person said in passing, on a turn another author answers.
    in_passing: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"saved": list(self.saved), "refused": list(dict.fromkeys(self.refused)), "in_passing": self.in_passing}


_ACTIVE: contextvars.ContextVar[KeepReceipt | None] = contextvars.ContextVar("vool_memory_keep_receipt", default=None)
_BY_TURN: OrderedDict[str, dict[str, Any]] = OrderedDict()
_BY_TURN_LOCK = threading.Lock()
_BY_TURN_LIMIT = 256


@contextmanager
def collecting() -> Iterator[KeepReceipt]:
    """Collect this thread's memory writes and refusals until the block ends."""
    receipt = KeepReceipt()
    token = _ACTIVE.set(receipt)
    try:
        yield receipt
    finally:
        _ACTIVE.reset(token)


def note_saved(fact: str) -> None:
    """Called by the writer after a row was recorded."""
    receipt = _ACTIVE.get()
    if receipt is not None:
        receipt.saved.append(" ".join(str(fact or "").split()))


def note_refused(reason: str) -> None:
    """Called where a write was asked for and refused."""
    receipt = _ACTIVE.get()
    if receipt is not None and reason:
        receipt.refused.append(str(reason))


def _turn_id(source_context: Any) -> str:
    if not isinstance(source_context, dict):
        return ""
    try:
        from core.turn_contract import TURN_REQUEST_KEY

        return str(getattr(source_context.get(TURN_REQUEST_KEY), "turn_id", "") or "")
    except Exception:
        return ""


def stamp(source_context: Any, receipt: KeepReceipt) -> None:
    """Put the turn's receipt where finalization reads it: on the turn context, and by turn id for a path that
    finalizes from a copy of the context taken before the keep step ran."""
    record = receipt.as_dict()
    # What this step wrote and refused is also filed as the turn's action receipts, checked against the store.
    from core.action_receipts import note_memory_keep

    note_memory_keep(source_context, list(record.get("saved") or []), list(record.get("refused") or []))
    turn_id = _turn_id(source_context)
    with _BY_TURN_LOCK:
        # One turn is stamped more than once (the keep step at the turn's start, the memory door later, and each
        # part of a message that is answered in parts): the receipt is everything they wrote and refused.
        earlier = source_context.get(CONTEXT_KEY) if isinstance(source_context, dict) else None
        if not isinstance(earlier, dict):
            earlier = _BY_TURN.get(turn_id) if turn_id else None
        if isinstance(earlier, dict):
            record = {
                "saved": list(dict.fromkeys([*earlier.get("saved", []), *record["saved"]])),
                "refused": list(dict.fromkeys([*earlier.get("refused", []), *record["refused"]])),
                "in_passing": bool(earlier.get("in_passing") or record["in_passing"]),
            }
    if isinstance(source_context, dict):
        source_context[CONTEXT_KEY] = record
    if turn_id:
        with _BY_TURN_LOCK:
            _BY_TURN[turn_id] = record
            _BY_TURN.move_to_end(turn_id)
            while len(_BY_TURN) > _BY_TURN_LIMIT:
                _BY_TURN.popitem(last=False)


def _recorded(source_context: Any, turn_id: str = "") -> dict[str, Any] | None:
    if isinstance(source_context, dict):
        record = source_context.get(CONTEXT_KEY)
        if isinstance(record, dict):
            return record
    with _BY_TURN_LOCK:
        for key in (str(turn_id or ""), _turn_id(source_context)):
            if key and key in _BY_TURN:
                return _BY_TURN[key]
    return None


def receipt_for(source_context: Any, turn_id: str = "") -> dict[str, Any] | None:
    """The turn's receipt as a reply may use it, or None when the keep step did not see this turn.

    The saved facts it names are memory content on its way into a reply (the line `Saved to memory: "..."`), so each
    passes the one serving gate (`core.memory.revocation.authorize_payload`) like any other row that is read. A fact
    the gate withholds is not returned; `withheld` says how many were, so a reader can tell "nothing was written"
    from "something was written that may not be shown" and neither claims nor denies the save."""
    record = _recorded(source_context, turn_id)
    if record is None:
        return None
    saved = [str(fact) for fact in record.get("saved") or []]
    if not saved:
        return record
    from core.memory.revocation import authorize_payload

    context = source_context if isinstance(source_context, dict) else {}
    chat = str(context.get("session_id") or context.get("chat_id") or "").strip() or None
    served = [fact for fact in saved if authorize_payload(fact, chat, context.get("runtime_home"))]
    if len(served) == len(saved):
        return record
    return {**record, "saved": served, "withheld": len(saved) - len(served)}


__all__ = [
    "CONTEXT_KEY",
    "REFUSED_AGENT_TURN",
    "REFUSED_FORGOTTEN_HERE",
    "REFUSED_PRIVATE_CHAT",
    "REFUSED_SECRET",
    "KeepReceipt",
    "collecting",
    "note_refused",
    "note_saved",
    "receipt_for",
    "stamp",
]
