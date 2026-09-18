"""Which gate got to decide this turn, and which one took the decision away from the model.

The question this answers is not "what did VOOL reply?" -- the ledger already has that -- but
**"did the model ever get the chance to read this message?"**. On the shipped runtime a turn passes
roughly sixty refusal points before any model sees it, and a keyword table three files deep can end
the turn without leaving a trace that names itself. `record_decision` in
`core.routing_decision_log` records the family that ANSWERED; it says nothing about the ones that
were consulted first, and nothing at all about turns no fast path claimed for a reason worth
knowing.

So REACH records the sequence: every gate consulted, in order, with what it did. Two derived facts
are what the architecture actually needs:

* **`preempted_by`** -- the first gate that CLAIMED the turn. Empty when nothing did.
* **`entered_model_lane`** / **`provider_call_attempted`** / **`tool_offer_made`** -- three facts,
  because they are three separate things. Execution arriving at the model lane is not a model
  having read the message: a hostile review found a turn whose receipt claimed the model lane while
  the turn left through a later early return with zero model calls. `provider_call_attempted` comes from the
  finished turn's own report (`record_turn_outcome`), never from a marker placed mid-flight.

**This module observes and never decides.** Nothing here returns a value any caller branches on,
every public function swallows its own exceptions, and the recorder is a `ContextVar` the runtime
never reads back. That is not politeness -- it is what makes "instrumentation ON and OFF produce
byte-identical responses" a property of the code rather than a hope. A measurement that can change
what it measures is not a measurement.

It also makes **zero model calls**, in this phase and by construction: there is no adapter import,
no ask-model callable, and no branch that could acquire one.

Off switch: `VOOL_SEMANTIC_REACH=0`. Default is on. When off, `begin_turn` installs nothing and
every `record` call is a no-op returning immediately -- the same code path a turn takes when no
recorder was ever started, which is the path that must stay identical.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

#: Canonical gate names. Strings rather than an Enum because a gate that is not in this list must
#: still be recordable -- an unnamed gate showing up in a receipt is exactly what you want to SEE,
#: not something to reject. These constants exist so the common ones spell consistently.
GATE_ATTEMPT_FOLLOWUP = "attempt_followup"
GATE_CONDUCTOR = "conductor"
#: The turn carried no request at all, so it was answered without consulting anything below. This
#: gate sits AHEAD of every other one in the front door -- earlier than `attempt_followup` -- which
#: is precisely why it needs naming: a claim recorded nowhere reads back as `preempted_by == ""`,
#: and the receipt then reports `routing_family = model_lane` for a turn that never went near it.
GATE_EMPTY_TURN = "empty_turn"
GATE_LIVE_DATA = "live_data_plan"
GATE_PLANNED_TURN = "planned_turn"
GATE_FRONTDOOR = "turn_frontdoor"
GATE_INTENT_ARBITER = "intent_arbiter"
GATE_TOOL_INTENT_GATE = "should_attempt_tool_intent"
GATE_MODEL_LANE = "model_lane"

#: What a gate did when it was consulted.
#:
#: `OUTCOME_REACHED` used to be the only positive outcome, and it was a lie waiting to happen: it
#: was recorded when execution ARRIVED at the model-routing code, and a hostile review found a
#: missing-tool turn where the receipt said `reached_model_lane` while the turn actually left through
#: a later early return with zero model calls. "Execution passed this line" and "the model read the
#: message" are different facts and now have different names.
OUTCOME_CLAIMED = "claimed"      # this gate answered the turn; nothing below it ran
OUTCOME_DECLINED = "declined"    # consulted, did not claim; the turn continued past it
OUTCOME_BLOCKED = "blocked"      # consulted, and it REMOVED an opportunity below it
OUTCOME_OFFERED = "offered"      # an opportunity was made available (the tool catalogue was offered)
OUTCOME_ENTERED = "entered"      # execution arrived here -- says nothing about what happened after
OUTCOME_CALL_ATTEMPTED = "provider_call_attempted"  # the runtime entered a provider adapter call
#: A row from `core.routing_decision_log`. Observation about observation: it records that a family
#: reported something, and it can NEVER set `preempted_by`. A telemetry row arriving late must not be
#: able to claim retroactively that it took the turn away from the model.
OUTCOME_TELEMETRY = "telemetry"

#: Retained so a stored receipt written by an earlier build still reads. Never recorded any more.
OUTCOME_REACHED = "reached"

_ENV_FLAG = "VOOL_SEMANTIC_REACH"

_RECORDER: ContextVar[ReachRecorder | None] = ContextVar("vool_semantic_reach", default=None)


def reach_enabled() -> bool:
    """Whether REACH observation is on. Anything other than an explicit "0"/"false"/"no" is on."""
    raw = str(os.environ.get(_ENV_FLAG, "") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class ReachEvent:
    """One gate's turn at deciding. Frozen: a recorded observation is not editable after the fact."""

    order: int
    gate: str
    outcome: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"order": self.order, "gate": self.gate, "outcome": self.outcome, "detail": self.detail}


@dataclass
class ReachRecorder:
    """The ordered record of one turn's gates.

    Lock-guarded because the runtime dispatches conductor nodes onto a thread pool, and a node that
    records a gate from a worker must not interleave a half-written list with the main thread. The
    lock protects the append only -- it is never held across anything that could block.
    """

    session_id: str = ""
    turn_id: str = ""
    events: list[ReachEvent] = field(default_factory=list)
    #: What the finished turn reported about itself. Empty until `record_turn_outcome` is called --
    #: and `observed: False` is the accurate reading for a turn that raised before reporting.
    outcome: dict[str, Any] = field(default_factory=lambda: {"observed": False})
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, gate: str, outcome: str, detail: str = "") -> None:
        clean_gate = str(gate or "").strip() or "unnamed_gate"
        clean_outcome = str(outcome or "").strip() or OUTCOME_DECLINED
        with self._lock:
            self.events.append(
                ReachEvent(
                    order=len(self.events),
                    gate=clean_gate,
                    outcome=clean_outcome,
                    detail=str(detail or "")[:200],
                )
            )

    @property
    def consulted(self) -> tuple[str, ...]:
        """Every gate that was asked, in order, including repeats.

        Repeats are kept rather than collapsed: `_maybe_handle_workspace_identity_request` runs at
        two different points in the front door, and a deduplicated list would hide which of the two
        actually claimed.
        """
        with self._lock:
            return tuple(event.gate for event in self.events)

    @property
    def preempted_by(self) -> str:
        """The first gate that claimed the turn, or "" when none did.

        "First" and not "last": once a gate claims, the runtime returns, so anything recorded after
        it is either a nested observation or a bug worth seeing in the ordered list.
        """
        with self._lock:
            for event in self.events:
                if event.outcome == OUTCOME_CLAIMED:
                    return event.gate
        return ""

    @property
    def blocked_by(self) -> tuple[str, ...]:
        """Gates that removed an opportunity below them without answering the turn themselves."""
        with self._lock:
            return tuple(event.gate for event in self.events if event.outcome == OUTCOME_BLOCKED)

    def saw(self, gate: str, outcome: str) -> bool:
        with self._lock:
            return any(event.gate == gate and event.outcome == outcome for event in self.events)

    @property
    def entered_model_lane(self) -> bool:
        """Whether execution ARRIVED at the model lane.

        Deliberately not called "reached": everything below this point can still leave through an
        early return without a model ever being consulted, and conflating the two is the defect this
        split exists to close. For whether a provider call was made, read `provider_call_attempted` -- which is still not
        "a model ran"; see its own note.
        """
        return self.saw(GATE_MODEL_LANE, OUTCOME_ENTERED)

    @property
    def tool_offer_made(self) -> bool:
        """Whether the tool catalogue was actually offered to the model on this turn."""
        return self.saw(GATE_TOOL_INTENT_GATE, OUTCOME_OFFERED)

    def note_provider_call_attempt(self, *, provider_id: str = "", model_name: str = "") -> None:
        """A provider call was ATTEMPTED. Recorded at the seam, not inferred later.

        The name is the narrowest thing the seam can prove, and it was measured rather than
        assumed. Four scenarios were driven through `MemoryFirstRouter._invoke_manifest`:

        | scenario | recorded |
        |---|---|
        | adapter method entered, raises before any provider I/O (a fixed capability fact, e.g. "does not support required tools") | yes |
        | provider call attempted, provider raises (transport) | yes |
        | provider returns a valid response | yes |
        | health probe fails, so the adapter method is never called | no |

        Row 1 is why this is not called "invoked". The adapter can refuse on a capability check
        before a single byte leaves the process, and the old `model_invoked` reported True for it --
        a receipt claiming a model ran when none did. What the seam actually witnesses is that the
        runtime resolved a provider and model, built the adapter, passed the health probe, and
        entered the adapter's task method.

        It does NOT prove the provider was reached, and does NOT prove a model produced anything.
        Proving actual execution would need a second observation on the adapter's return path --
        more production instrumentation than this phase is willing to add -- so the observation is
        scoped to what it can support instead of overclaiming. The turn's own `model_calls` is
        recorded beside it, and the two are compared rather than merged.
        """
        with self._lock:
            self.events.append(
                ReachEvent(
                    order=len(self.events),
                    gate=GATE_MODEL_LANE,
                    outcome=OUTCOME_CALL_ATTEMPTED,
                    detail=f"{provider_id}:{model_name}".strip(":")[:200],
                )
            )

    def record_turn_outcome(
        self, *, model_calls: int, route: str = "", route_reason: str = "", fast_path_hit: bool = False
    ) -> None:
        """What the finished turn itself reports. Never the source for the attempt record.

        Taken from the turn's own result rather than from a marker placed mid-flight, because a
        marker can only say where execution went, and the question is what actually happened. This
        is also what makes the receipt cross-checkable: `route` and `fast_path_hit` are computed by
        the turn pipeline with no knowledge of this recorder.
        """
        with self._lock:
            self.outcome = {
                "observed": True,
                "model_calls": max(0, int(model_calls or 0)),
                "route": str(route or ""),
                "route_reason": str(route_reason or ""),
                "fast_path_hit": bool(fast_path_hit),
            }
            # Deliberately does NOT synthesize an attempt event. `model_calls` is the turn's own
            # orchestration telemetry, and a hostile review showed it reading 0 while a provider call
            # had really been made. The attempt record comes from `note_provider_call_attempt` at the
            # seam; this stores the turn's self-report beside it so the two can be compared.

    @property
    def provider_call_attempted(self) -> bool:
        """Whether the runtime entered a provider adapter call, per the seam itself.

        Read `note_provider_call_attempt` for exactly what this witnesses and what it does not. It
        is NOT "a model ran": an adapter that refuses on a capability check before any I/O is
        counted here, because the runtime did make the call.
        """
        return self.saw(GATE_MODEL_LANE, OUTCOME_CALL_ATTEMPTED)

    @property
    def provider_calls_attempted(self) -> tuple[str, ...]:
        """Each provider:model the runtime attempted to call on this turn, in order."""
        with self._lock:
            return tuple(
                event.detail
                for event in self.events
                if event.gate == GATE_MODEL_LANE and event.outcome == OUTCOME_CALL_ATTEMPTED
            )

    @property
    def attempt_disagrees_with_self_report(self) -> bool:
        """The seam saw a provider call and the turn's own `model_calls` says none.

        Not an error -- the two count different things, and neither is the other's check -- but
        recorded so the receipt shows both and nobody has to guess which one was believed.
        """
        with self._lock:
            reported = int(self.outcome.get("model_calls") or 0)
        return self.provider_call_attempted and reported == 0

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            events = [event.to_dict() for event in self.events]
            outcome = dict(self.outcome)
        return {
            "consulted": [event["gate"] for event in events],
            "preempted_by": self.preempted_by,
            "blocked_by": list(self.blocked_by),
            # Three separate facts where there used to be one overloaded one.
            "entered_model_lane": self.entered_model_lane,
            "provider_call_attempted": self.provider_call_attempted,
            "provider_calls_attempted": list(self.provider_calls_attempted),
            "attempt_disagrees_with_self_report": self.attempt_disagrees_with_self_report,
            "tool_offer_made": self.tool_offer_made,
            "turn_outcome": outcome,
            "gate_count": len(events),
            "events": events,
        }


def current() -> ReachRecorder | None:
    """The recorder for the turn in flight, or None when nothing is observing."""
    try:
        return _RECORDER.get()
    except LookupError:  # pragma: no cover - ContextVar has a default, so this cannot fire
        return None


def record(gate: str, outcome: str, detail: str = "") -> None:
    """Record one gate's decision. Never raises, never returns anything to branch on.

    A no-op when observation is off or no turn is being observed, which is the same instruction
    sequence a turn runs today -- one `ContextVar.get` and a `None` test.
    """
    try:
        recorder = _RECORDER.get()
        if recorder is None:
            return
        recorder.record(gate, outcome, detail)
    except Exception:
        return  # observation must never break a turn


def claimed(gate: str, detail: str = "") -> None:
    record(gate, OUTCOME_CLAIMED, detail)


def declined(gate: str, detail: str = "") -> None:
    record(gate, OUTCOME_DECLINED, detail)


def blocked(gate: str, detail: str = "") -> None:
    record(gate, OUTCOME_BLOCKED, detail)


def note_provider_call_attempt(*, provider_id: str = "", model_name: str = "") -> None:
    """Record that a provider was reached. Never raises, never returns anything to branch on."""
    try:
        recorder = _RECORDER.get()
        if recorder is None:
            return
        recorder.note_provider_call_attempt(provider_id=provider_id, model_name=model_name)
    except Exception:
        return


def offered(gate: str, detail: str = "") -> None:
    record(gate, OUTCOME_OFFERED, detail)


def entered(gate: str, detail: str = "") -> None:
    record(gate, OUTCOME_ENTERED, detail)


@contextmanager
def observing_turn(*, session_id: str = "", turn_id: str = "") -> Iterator[ReachRecorder | None]:
    """Observe one turn. Yields the recorder, or None when observation is off.

    The `ContextVar` token is always reset in `finally`, so a turn that raises cannot leak its
    recorder into the next turn on the same thread -- which would silently attribute one message's
    gates to another and make every reading downstream wrong.
    """
    if not reach_enabled():
        yield None
        return
    recorder = ReachRecorder(session_id=str(session_id or ""), turn_id=str(turn_id or ""))
    token = _RECORDER.set(recorder)
    try:
        yield recorder
    finally:
        try:
            _RECORDER.reset(token)
        except Exception:
            _RECORDER.set(None)


__all__ = [
    "GATE_ATTEMPT_FOLLOWUP",
    "GATE_CONDUCTOR",
    "GATE_EMPTY_TURN",
    "GATE_FRONTDOOR",
    "GATE_INTENT_ARBITER",
    "GATE_LIVE_DATA",
    "GATE_MODEL_LANE",
    "GATE_PLANNED_TURN",
    "GATE_TOOL_INTENT_GATE",
    "OUTCOME_BLOCKED",
    "OUTCOME_CALL_ATTEMPTED",
    "OUTCOME_CLAIMED",
    "OUTCOME_DECLINED",
    "OUTCOME_ENTERED",
    "OUTCOME_OFFERED",
    "OUTCOME_REACHED",
    "OUTCOME_TELEMETRY",
    "ReachEvent",
    "ReachRecorder",
    "blocked",
    "claimed",
    "current",
    "declined",
    "entered",
    "note_provider_call_attempt",
    "observing_turn",
    "offered",
    "reach_enabled",
    "record",
]

# vool-kill-test-marker
