"""One node of a conductor plan, and what actually happened to it.

The split mirrors `core.agent_runtime.live_data_plan`: the node is frozen because it is the
*identity* of a piece of requested work, and the outcome is mutable because a retry starts a fresh
outcome against that same identity. Keeping them apart is what lets a receipt name the work rather
than the attempt.

Two deliberate departures from the live-data shapes, both of them defects that audit found there:

* `required_result_fields` is **enforced** here. In `live_data_plan` it is written, serialized, and
  compared against nothing -- documentation in a tuple. `NodeOutcome.succeeded` requires every
  declared field to be present in `result`, so a node that returns a shape its own contract does
  not satisfy fails instead of quietly counting.
* Every member of `NodeLifecycle` is reachable and assigned by the scheduler. `SubtaskLifecycle`
  ships nine members of which `APPROVED`, `RUNNING` and `SKIPPED` are never assigned anywhere; a
  lifecycle with unreachable states cannot be reasoned about.

Timing carries two clocks on purpose, captured at the same instant. `time.monotonic()` is what
overlap math runs on -- it cannot be moved by an NTP correction mid-turn -- and the ISO wall-clock
string is what a human reads in Activity. Recording only the ISO form, which is what reaches
SQLite for live-data subtasks today, leaves the overlap proof vulnerable to exactly the clock
adjustment monotonic was chosen to defeat.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.runtime_task_outcome import FulfillmentStatus


class NodeLifecycle(str, Enum):
    """Terminal and transitional states a node can hold. Every member is assigned by the scheduler."""

    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # The planner named this clause but no registered operation can serve it. This is the
    # fail-closed state: the request is carried into the answer as unserved rather than dropped.
    UNRESOLVED = "unresolved"
    # A dependency failed, so this node was never attempted. Distinct from FAILED because the node
    # itself is not broken and a retry of the dependency may make it runnable.
    DEPENDENCY_FAILED = "dependency_failed"


#: States from which no further work is attempted.
TERMINAL_STATES = frozenset(
    {
        NodeLifecycle.SUCCEEDED,
        NodeLifecycle.FAILED,
        NodeLifecycle.UNRESOLVED,
        NodeLifecycle.DEPENDENCY_FAILED,
    }
)


class NodeFailureCode(str, Enum):
    """Closed vocabulary for WHY a node did not produce an answer.

    R3 (AUD-20260829-003). `NodeOutcome.failure_reason` used to be the ONLY record of a failure,
    and it carried `f"{type(exc).__name__}: {exc}"` -- raw exception text, unbounded in content --
    straight toward the composed answer (`core.conductor.compose.reader_facing_reason` used to
    strip the class-name prefix and return whatever was left AS-IS, which is how a raw
    `ImportError` naming an absolute filesystem path reached the operator's screen, AUD-20260829-003
    evidence E001).

    A code is a finite, developer-reviewed vocabulary assigned AT THE FAILURE SITE
    (`core/conductor/scheduler.py`), never inferred from an exception's message text after the
    fact. `core.conductor.compose.reason_for_outcome` maps each member to safe, reader-facing
    prose; a code that is NONE or not a member of this enum renders a generic unavailable line
    plus `NodeOutcome.receipt_id` -- the fail-closed default. Adding a member here is a reviewed,
    explicit act; a new, unanticipated exception type's message leaking through is not, which is
    exactly the property this closes.
    """

    #: No failure -- SUCCEEDED, or the field was never set (legacy/direct-constructed outcomes;
    #: `compose.reason_for_outcome` falls back to scrubbing `failure_reason` as plain text for
    #: these, on the same fail-closed terms).
    NONE = ""
    #: `_run_one`: the operation's result was missing one or more `required_result_fields`. Safe
    #: detail (the field names) is developer-declared, not exception-derived; compose.py already
    #: appends it separately via `NodeOutcome.missing_result_fields`.
    RESULT_MISSING_FIELDS = "result_missing_fields"
    #: The operation returned a result but established none of its requested output.
    RESULT_UNFULFILLED = "result_unfulfilled"
    #: `_run_one`: the operation's own `render()` raised. Exception-derived -- generic phrase only.
    RENDER_FAILED = "render_failed"
    #: `_run_one`: the operation itself raised (includes "no registered operation named ...", a
    #: safe message, but caught by the same blanket handler as any other exception -- see the
    #: NODE_EXCEPTION docstring note in compose.py for why this is not split further). Exception-
    #: derived -- generic phrase only.
    NODE_EXCEPTION = "node_exception"
    #: The planner named this clause but no registered operation could serve it. Safe detail
    #: (`ConductorNode.unresolved_reason`) is developer/planner-authored prose, never exception text.
    UNRESOLVED = "unresolved"
    #: A declared dependency did not succeed, so this node was never attempted. Safe detail (the
    #: list of dependency node ids) is structural, never exception-derived.
    DEPENDENCY_FAILED = "dependency_failed"
    #: `run_conductor_plan`: a `Future` raised outside `_run_one`'s own handling (a pool-level
    #: fault). Exception-derived -- generic phrase only.
    SCHEDULER_FAULT = "scheduler_fault"
    #: `_run_one`: the OWNING turn's cancellation marker fired before this node started,
    #: so no work ran and no socket opened. Structural (the marker on the turn's own
    #: context), never exception-derived; assigned at node entry by the scheduler's
    #: cancellation check, not inferred from a raised error afterwards.
    CANCELLED = "cancelled"
    #: `_run_one`: the operation raised a TRANSPORT-family error reaching a live
    #: source (URLError/TimeoutError/ConnectionError) -- a network fact, not a runtime
    #: fault. Assigned at the failure site from the exception's TYPE only; the reason
    #: is the type-derived token, never exception text (a URLError message can embed
    #: the full request URL). Distinct from a policy refusal, which never runs a socket.
    TRANSPORT_FAILED = "transport_failed"
    #: The plan's wall-clock deadline expired before this node could run or finish.
    PLAN_DEADLINE_EXPIRED = "plan_deadline_expired"
    #: Sequential runner only (`run_conductor_plan_sequential`, test/sabotage-target path): the
    #: node was never reached before the loop ended.
    NOT_REACHED = "not_reached"


def now_pair() -> tuple[float, str]:
    """A monotonic instant and its wall-clock rendering, captured together.

    Together matters: two separate calls can straddle a clock adjustment, which would make the
    human-readable stamp disagree with the interval the overlap proof is computed from.
    """
    return time.monotonic(), datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class ConductorNode:
    """One self-contained piece of requested work, carved out of the user's message.

    `request_text` is the clause in the user's own words, and it is the only TEXT a domain adapter
    ever sees -- never the whole message. That is the structural fix for entity contamination: a
    weather adapter handed "get weather for Kaunas and Tallinn" cannot mistake "the provider retry
    implementation" for a city, because it is never shown it.

    An operation that reasons over the message's *facts* rather than its entities reads them from
    `NodeContext.shared_context` -- typed numbers and ambiguities, not a second copy of the text --
    and only when it declared `wants_shared_context`. No adapter gets the raw message back.
    """

    node_id: str
    operation: str
    request_text: str
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    required_result_fields: tuple[str, ...] = ()
    tool_intent: str = ""
    # Whether serving this node drives a model generation. The scheduler needs it before the node
    # runs: three concurrent local generations exhausted Ollama's read timeout on 2026-08-05, which
    # is why `turn_planner.run_plan` is pinned to one worker in production. Pure tool nodes do not
    # contend for that lane and can run wide.
    needs_generation: bool = False
    can_run_in_parallel: bool = True
    # Why this node exists in the shape it does -- "planner" for a served clause, or the reason the
    # registry could not serve it. Carried into the answer for UNRESOLVED nodes.
    unresolved_reason: str = ""
    # Which canonical obligation this node discharges, when it exists to discharge one. The link is
    # an id rather than a text match on purpose: an obligation is accounted for because a node
    # NAMES it, never because its words happen to appear somewhere in the answer. The clause text
    # of a node that dropped a subject still contains that subject, so a text test would report the
    # dropped obligation as covered -- which is the exact false negative the floor exists to catch.
    obligation_id: str = ""
    # The subset of `depends_on` this node cannot answer WITHOUT. Empty means "all of them", which
    # is what every node built before this field existed meant and still means.
    #
    # The two are different questions and conflating them breaks in both directions. Blocking on
    # every dependency refuses a Porto-vs-Oslo difference because an unrelated third city in the
    # same lookup had no reading. Blocking on none of them lets a Silver-per-Platinum ratio answer
    # from silver alone when nothing can price platinum -- and the model, having no symbol for the
    # operand it lacks, writes `silver_price / 1` and the step evaluates. A required prerequisite
    # is one the dependent's own clause NAMES; an anaphoric clause ("the difference between them")
    # names none, and then all of them are required.
    required_node_ids: tuple[str, ...] = ()
    # Where this node's clause sits in the canonical user text, as a half-open code-point range, or
    # `(-1, -1)` when it was not located. Offsets rather than a substring comparison: a realization
    # with no arguments to key on -- a derived computation -- binds to the node whose clause OVERLAPS
    # its frame, and overlap between two ranges over one text is a proven geometric relation. The
    # `_normalize(a).startswith(_normalize(b)[:24])` test this replaces matched two different
    # requests that opened with the same words.
    clause_span: tuple[int, int] = (-1, -1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "operation": self.operation,
            "request_text": self.request_text,
            "arguments": dict(self.arguments),
            "depends_on": list(self.depends_on),
            "required_result_fields": list(self.required_result_fields),
            "tool_intent": self.tool_intent,
            "needs_generation": self.needs_generation,
            "can_run_in_parallel": self.can_run_in_parallel,
            "unresolved_reason": self.unresolved_reason,
            "obligation_id": self.obligation_id,
            "required_node_ids": list(self.required_node_ids),
            "clause_span": list(self.clause_span),
        }


@dataclass
class NodeOutcome:
    """What happened to one node. Mutable so a retry can replace it against the same node."""

    node: ConductorNode
    state: NodeLifecycle = NodeLifecycle.PLANNED
    result: dict[str, Any] | None = None
    #: Short diagnostic summary for receipts/logs -- e.g. `f"{type(exc).__name__}: {exc}"[:200]`.
    #: NEVER read by the renderer (`core.conductor.compose.reason_for_outcome` reads `failure_code`
    #: first); kept for the receipt/event store and any caller that wants a developer-facing
    #: one-liner. See `failure_detail_full` for the untruncated text and `failure_traceback` for
    #: the full traceback -- both restore diagnostic detail this field's 200-char truncation loses.
    failure_reason: str = ""
    #: Closed-vocabulary reason code, assigned at the failure site. The renderer's fail-closed
    #: input; see `NodeFailureCode`.
    failure_code: NodeFailureCode = NodeFailureCode.NONE
    #: Opaque id correlating a served "could not be answered" row back to the full diagnostic
    #: record (`failure_detail_full` + `failure_traceback`, both in the receipt/event store, never
    #: in the rendered answer). Empty when the node succeeded.
    receipt_id: str = ""
    #: FULL (untruncated) exception text -- `f"{type(exc).__name__}: {exc}"` with no [:200] cut --
    #: set ONLY at the sites in scheduler.py that catch a real exception. Restores exactly the
    #: diagnostic detail the audit found missing: "no per-node traceback... the discriminating
    #: evidence was destroyed by the runtime itself" (AUD-20260829-003, R01). Never consulted by
    #: the renderer; belongs in the receipt/event store only.
    failure_detail_full: str = ""
    #: Full Python traceback text, same scope and same rule as `failure_detail_full`.
    failure_traceback: str = ""
    #: Rendered, user-facing text for this node. Produced by the operation's renderer, never by a
    #: model narrating what it thinks happened.
    rendered: str = ""
    queued_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None
    queued_at_iso: str = ""
    started_at_iso: str = ""
    completed_at_iso: str = ""
    #: Assessed by the operation from its executed result, never by the renderer.
    result_fulfillment: FulfillmentStatus | None = None

    @property
    def succeeded(self) -> bool:
        """SUCCEEDED *and* the result carries every field the node's contract declared.

        The second half is the part `live_data_plan` declares and never checks. A node whose
        adapter returned a partial shape has not done its job, and letting it count as success is
        how a missing field becomes a confident sentence downstream.
        """
        if self.state is not NodeLifecycle.SUCCEEDED or self.result is None:
            return False
        return all(str(name) in self.result for name in self.node.required_result_fields)

    @property
    def fulfillment_status(self) -> FulfillmentStatus | None:
        if self.state not in TERMINAL_STATES:
            return None
        if self.failure_code is NodeFailureCode.CANCELLED:
            return FulfillmentStatus.CANCELLED
        if self.state in {NodeLifecycle.UNRESOLVED, NodeLifecycle.DEPENDENCY_FAILED}:
            return FulfillmentStatus.BLOCKED
        if not self.succeeded:
            return FulfillmentStatus.FAILED
        return self.result_fulfillment or FulfillmentStatus.FULFILLED

    @property
    def fulfilled(self) -> bool:
        return self.fulfillment_status is FulfillmentStatus.FULFILLED

    @property
    def partially_fulfilled(self) -> bool:
        return self.fulfillment_status is FulfillmentStatus.PARTIALLY_FULFILLED

    @property
    def missing_result_fields(self) -> tuple[str, ...]:
        if self.result is None:
            return tuple(self.node.required_result_fields)
        return tuple(n for n in self.node.required_result_fields if str(n) not in self.result)

    @property
    def duration_s(self) -> float | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return max(0.0, self.completed_at - self.started_at)

    def overlaps(self, other: NodeOutcome) -> bool:
        """Half-open interval intersection over the monotonic clock.

        Half-open so a node that finishes at exactly the instant another starts is not counted as
        overlapping -- that is a handoff, not concurrency, and counting it would let a strictly
        sequential run claim overlap.
        """
        if None in (self.started_at, self.completed_at, other.started_at, other.completed_at):
            return False
        return self.started_at < other.completed_at and other.started_at < self.completed_at  # type: ignore[operator]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node.to_dict(),
            "state": self.state.value,
            "fulfillment_status": self.fulfillment_status.value if self.fulfillment_status else None,
            "result": self.result,
            "failure_reason": self.failure_reason,
            "failure_code": self.failure_code.value if self.failure_code else "",
            "receipt_id": self.receipt_id,
            "rendered": self.rendered,
            "queued_at_iso": self.queued_at_iso,
            "started_at_iso": self.started_at_iso,
            "completed_at_iso": self.completed_at_iso,
            "duration_s": self.duration_s,
        }


def node_receipt(outcome: NodeOutcome, *, plan_id: str) -> dict[str, Any]:
    """The runtime's own record of one node's execution.

    Every field is observed by the runtime at the dispatch seam. Nothing here is parsed out of
    model output, and there is deliberately no field a model could populate: the whole point is
    that "I ran these in parallel" is not evidence, and this record is.

    R3 (AUD-20260829-003): this receipt -- not the composed answer -- is where the FULL exception
    text and traceback belong. `failure_detail_full` and `failure_traceback` are the untruncated
    counterparts `failure_reason`'s 200-character cut discarded; a receipt id here is the same one
    a served "could not be answered" row carries, so a developer can go from what the user saw
    straight to what actually happened.
    """
    node = outcome.node
    return {
        "schema": "conductor_node_receipt_v1",
        "plan_id": plan_id,
        "node_id": node.node_id,
        "operation": node.operation,
        "tool_intent": node.tool_intent,
        "depends_on": list(node.depends_on),
        "state": outcome.state.value,
        "ok": outcome.succeeded,
        "fulfilled": outcome.fulfilled,
        "fulfillment_status": outcome.fulfillment_status.value if outcome.fulfillment_status else None,
        "failure_reason": outcome.failure_reason,
        "failure_code": outcome.failure_code.value if outcome.failure_code else "",
        "receipt_id": outcome.receipt_id,
        "failure_detail_full": outcome.failure_detail_full,
        "failure_traceback": outcome.failure_traceback,
        "missing_result_fields": list(outcome.missing_result_fields),
        "result_fields": sorted(outcome.result.keys()) if outcome.result else [],
        "queued_at_iso": outcome.queued_at_iso,
        "started_at_iso": outcome.started_at_iso,
        "completed_at_iso": outcome.completed_at_iso,
        "duration_s": outcome.duration_s,
    }


__all__ = [
    "TERMINAL_STATES",
    "ConductorNode",
    "NodeFailureCode",
    "NodeLifecycle",
    "NodeOutcome",
    "node_receipt",
    "now_pair",
]
