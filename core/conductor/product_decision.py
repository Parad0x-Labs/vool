"""The irreversible product-truth cutover. One reducer, one decision, and nobody reclassifies.

Before this module the same turn was classified four times by four things that could disagree:
`CompletionVerdict.ship_allowed` in the projection, `CanonicalObligations.coverage` in the
obligation floor, `ComposedAnswer.complete` in the composer, and an `answered_count >=
unserved_count` ratio at the dispatch seam -- with a fifth opinion downstream, where a non-empty
response string became `FulfillmentStatus.FULFILLED` in the persisted receipt. Five authorities over
one fact is not redundancy. It is five chances for the record to disagree with what happened, and it
did: a plan reporting `conductor_no_nodes_served` persisted beside a delivered answer, and an
unaccounted requirement rode a confident reply all the way to the reader.

So the shape is now a cutover with a side:

    ExecutionReport  ->  reduce_execution_report  ->  ProductDecision
                                                      |
                        composer FORMATS -------------+
                        dispatch TRANSPORTS ----------+
                        persistence PERSISTS ---------+

Everything on the right of that arrow is downstream of the truth, not a second source of it. The
`ProductDecision` carries the outcome set, the disposition, the claim, and the EXACT
`RuntimeTaskOutcome` the persistence layer must write -- exact, because a status derived downstream
from prose is a status that can contradict the execution it describes.

**The claim is monotonic.** `ConductorClaimGate` is entered once, at the moment the decision exists,
and from then on there is no path back to `NOT_CLAIMED` and no path to `None`. A post-claim
exception becomes `CLAIMED_INTEGRITY_FAILURE` and a refusal the reader can see. The blanket
`except Exception: return None` this replaces did the opposite: a `TypeError` from a keyword
argument sent every conductor turn silently to the ordinary lane for as long as it took somebody to
notice the conductor had stopped existing.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.conductor.node import NodeOutcome
from core.conductor.realization import (
    ACCOUNTED_STATES,
    BoundExecutionPlan,
    RealizationState,
    RequirementOutcomeSet,
    reduce_bound_plan,
)
from core.runtime_task_outcome import FulfillmentStatus, RuntimeTaskOutcome


class ProductDisposition(str, Enum):
    """What the product may do with this turn. One value, derived from the outcome set."""

    FULFILLED = "fulfilled"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FAILED = "failed"
    BLOCKED = "blocked"
    #: Accounting is broken. Fail closed; do NOT fall through to another lane.
    INTEGRITY_FAILURE = "integrity_failure"
    #: Nothing was captured and nothing was planned, so this layer has no opinion.
    NOTHING_CAPTURED = "nothing_captured"


class ConductorClaim(str, Enum):
    """Whether the conductor owns this turn's answer, and in what condition.

    NOT_CLAIMED is the only value that permits another lane to answer. Every other value is a
    commitment: the conductor has told the truth about this turn and that truth is what ships.
    """

    NOT_CLAIMED = "not_claimed"
    CLAIMED_SUCCESS = "claimed_success"
    CLAIMED_PARTIAL = "claimed_partial"
    CLAIMED_FAILURE = "claimed_failure"
    CLAIMED_INTEGRITY_FAILURE = "claimed_integrity_failure"


_CLAIM_OF_DISPOSITION = {
    ProductDisposition.FULFILLED: ConductorClaim.CLAIMED_SUCCESS,
    ProductDisposition.PARTIALLY_FULFILLED: ConductorClaim.CLAIMED_PARTIAL,
    ProductDisposition.FAILED: ConductorClaim.CLAIMED_FAILURE,
    ProductDisposition.BLOCKED: ConductorClaim.CLAIMED_FAILURE,
    ProductDisposition.INTEGRITY_FAILURE: ConductorClaim.CLAIMED_INTEGRITY_FAILURE,
    ProductDisposition.NOTHING_CAPTURED: ConductorClaim.NOT_CLAIMED,
}

_FULFILMENT_OF_DISPOSITION = {
    ProductDisposition.FULFILLED: FulfillmentStatus.FULFILLED,
    ProductDisposition.PARTIALLY_FULFILLED: FulfillmentStatus.PARTIALLY_FULFILLED,
    ProductDisposition.FAILED: FulfillmentStatus.FAILED,
    ProductDisposition.BLOCKED: FulfillmentStatus.BLOCKED,
    ProductDisposition.INTEGRITY_FAILURE: FulfillmentStatus.FAILED,
}

#: The failure stage every conductor-owned outcome is stamped with, so a receipt says which layer
#: decided rather than leaving the reader to infer it from a code.
FAILURE_STAGE = "conductor_product_decision"
INTEGRITY_FAILURE_CODE = "conductor_integrity:accounting_broken"


@dataclass(frozen=True)
class ExecutionReport:
    """Everything execution produced, as one immutable object handed to the reducer.

    Nodes are carried alongside the bound plan because a plan may hold nodes no realization binds --
    a clause the planner served for which nothing was captured. Those still have to be accounted
    for, and accounting for them HERE rather than in a second heuristic at the dispatch seam is what
    stops the node counts from becoming a rival authority.
    """

    bound_plan: BoundExecutionPlan = field(default_factory=BoundExecutionPlan)
    node_outcomes: tuple[NodeOutcome, ...] = ()
    planned_node_ids: tuple[str, ...] = ()
    #: requirement id -> node ids realizing it, verbatim from `ConductorPlan.requirement_nodes`.
    #: Lets the reduction ask "did a node actually SERVE this requirement" instead of trusting the
    #: plan-time binding state — the answer-then-refuse contradiction (a clause served by a node
    #: yet listed under "Could not be answered") measured live 2026-08-29 (Q007, Q010).
    requirement_nodes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def missing_node_ids(self) -> tuple[str, ...]:
        accounted = {outcome.node.node_id for outcome in self.node_outcomes}
        return tuple(node_id for node_id in self.planned_node_ids if node_id not in accounted)


@dataclass(frozen=True)
class ProductDecision:
    """The one authority. Immutable, built once, and never revised by anything downstream."""

    outcome_set: RequirementOutcomeSet
    disposition: ProductDisposition
    claim: ConductorClaim
    #: The EXACT outcome persistence must write, or None when the conductor claimed nothing and is
    #: therefore not the authority for this turn. None is not "unknown": it is "another lane owns
    #: this", and the alternative -- stamping FULFILLED on a turn this layer did not answer -- is a
    #: claim about work that did not happen here.
    runtime_task_outcome: RuntimeTaskOutcome | None
    failure_lines: tuple[str, ...] = ()
    integrity_codes: tuple[str, ...] = ()
    #: Node ids that succeeded and bind to no realization. The composer renders these; they are not
    #: a second opinion about fulfilment.
    unbound_satisfied_node_ids: tuple[str, ...] = ()
    unbound_failed_node_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # I7, as a constructor rule. A decision that is not FULFILLED cannot be built holding a
        # FULFILLED runtime outcome, so there is no path that persists one -- not a forgetful
        # branch, not a downstream default, not a caller that built the outcome itself.
        if (
            self.runtime_task_outcome is not None
            and self.runtime_task_outcome.fulfillment_status is FulfillmentStatus.FULFILLED
            and self.disposition is not ProductDisposition.FULFILLED
        ):
            raise ValueError(
                f"disposition {self.disposition.value!r} cannot persist as "
                "RuntimeTaskOutcome.FULFILLED"
            )
        if (self.runtime_task_outcome is None) != (
            self.claim is ConductorClaim.NOT_CLAIMED
        ):
            raise ValueError(
                "a claimed decision must carry an explicit RuntimeTaskOutcome, and an unclaimed "
                "one must carry none"
            )
        if (self.claim is ConductorClaim.NOT_CLAIMED) != (
            self.disposition is ProductDisposition.NOTHING_CAPTURED
        ):
            raise ValueError(
                f"claim {self.claim.value!r} does not follow from disposition "
                f"{self.disposition.value!r}"
            )

    @property
    def claimed(self) -> bool:
        return self.claim is not ConductorClaim.NOT_CLAIMED

    @property
    def shippable(self) -> bool:
        """Whether this turn may be put in front of a reader as an ANSWER.

        An honestly reported gap is shippable. Broken accounting is not, and it does not fall
        through either -- see `integrity_response`.
        """
        return self.claimed and self.disposition is not ProductDisposition.INTEGRITY_FAILURE

    def to_dict(self) -> dict[str, Any]:
        """Serializable for EVERY disposition, including the one that carries no outcome.

        This used to dereference `runtime_task_outcome` unconditionally, so a legitimate
        NOTHING_CAPTURED decline raised `AttributeError` on the way to the dispatch seam. The
        conductor's pre-claim handler then caught it and returned None -- the right answer, reached
        by an exception, which meant the clean decline branch below it had never once executed.
        """
        return {
            "disposition": self.disposition.value,
            "claim": self.claim.value,
            "runtime_task_outcome": (
                self.runtime_task_outcome.to_dict()
                if self.runtime_task_outcome is not None
                else None
            ),
            "failure_lines": list(self.failure_lines),
            "integrity_codes": list(self.integrity_codes),
            "outcome_set": self.outcome_set.to_dict(),
        }


def _runtime_outcome(
    disposition: ProductDisposition, codes: Sequence[str]
) -> RuntimeTaskOutcome:
    """The EXACT outcome to persist. Derived from the disposition, never from the response text."""
    status = _FULFILMENT_OF_DISPOSITION[disposition]
    return RuntimeTaskOutcome(
        fulfillment_status=status,
        failure_stage="" if status is FulfillmentStatus.FULFILLED else FAILURE_STAGE,
        failure_codes=tuple(codes),
        # An integrity failure is not retryable: the same broken accounting would be rebuilt.
        retryable=status
        in {FulfillmentStatus.PARTIALLY_FULFILLED, FulfillmentStatus.FAILED}
        and INTEGRITY_FAILURE_CODE not in codes,
    )


def reduce_execution_report(report: ExecutionReport) -> ProductDecision:
    """`ExecutionReport -> ProductDecision`. The cutover, and the only place it happens.

    Everything a shipping decision needs is computed here from typed state: realization outcomes,
    the nodes no realization binds, and the nodes the report never accounted for. Nothing reads a
    response string, a confidence float, or a count of apology lines.
    """
    outcome_set = reduce_bound_plan(
        report.bound_plan,
        report.node_outcomes,
        requirement_nodes=report.requirement_nodes,
    )

    bound_nodes = set(report.bound_plan.node_ids)
    unbound_satisfied: list[str] = []
    unbound_failed: list[str] = []
    for outcome in report.node_outcomes:
        node_id = outcome.node.node_id
        if node_id in bound_nodes:
            continue
        (unbound_satisfied if outcome.fulfilled else unbound_failed).append(node_id)

    codes: list[str] = []
    integrity: list[str] = []
    for outcome in outcome_set.outcomes:
        if outcome.state is RealizationState.INTEGRITY_FAILURE:
            integrity.append(
                f"integrity:{outcome.realization.realization_id}:{outcome.detail or 'accounting'}"
            )
        elif outcome.state not in ACCOUNTED_STATES:
            integrity.append(
                f"unaccounted:{outcome.realization.realization_id}:{outcome.state.value}"
            )
        elif outcome.state is not RealizationState.SATISFIED:
            codes.append(f"{outcome.state.value}:{outcome.realization.realization_id}")
    for node_id in report.missing_node_ids:
        # A node the plan contains and the report never accounted for is a missing REDUCTION. It is
        # exactly as disqualifying as an unaccounted realization and for the same reason.
        integrity.append(f"missing_node_outcome:{node_id}")
    codes.extend(f"unfulfilled_node:{node_id}" for node_id in unbound_failed)

    satisfied = len(outcome_set.satisfied) + len(unbound_satisfied)
    total = len(outcome_set.outcomes) + len(unbound_satisfied) + len(unbound_failed)

    if integrity:
        disposition = ProductDisposition.INTEGRITY_FAILURE
        integrity.insert(0, INTEGRITY_FAILURE_CODE)
    elif total == 0:
        disposition = ProductDisposition.NOTHING_CAPTURED
    elif satisfied == total:
        disposition = ProductDisposition.FULFILLED
    elif satisfied or any(outcome.partially_fulfilled for outcome in report.node_outcomes):
        disposition = ProductDisposition.PARTIALLY_FULFILLED
    elif not unbound_failed and all(
        o.state
        in {
            RealizationState.DEPENDENCY_BLOCKED,
            RealizationState.POLICY_BLOCKED,
            RealizationState.CAPABILITY_UNAVAILABLE,
            RealizationState.UNRESOLVED_SUBJECT,
        }
        for o in outcome_set.outcomes
    ):
        disposition = ProductDisposition.BLOCKED
    else:
        disposition = ProductDisposition.FAILED

    all_codes = tuple(integrity) + tuple(codes)
    return ProductDecision(
        outcome_set=outcome_set,
        disposition=disposition,
        claim=_CLAIM_OF_DISPOSITION[disposition],
        runtime_task_outcome=(
            None
            if disposition is ProductDisposition.NOTHING_CAPTURED
            else _runtime_outcome(disposition, all_codes)
        ),
        failure_lines=outcome_set.failure_lines(),
        integrity_codes=tuple(integrity),
        unbound_satisfied_node_ids=tuple(unbound_satisfied),
        unbound_failed_node_ids=tuple(unbound_failed),
    )


class ClaimViolationError(RuntimeError):
    """A path tried to un-claim a turn the conductor had already committed to.

    Its own type so this can never be caught by a handler written for provider faults, and so a
    test can assert the specific violation rather than "something raised".
    """


class ConductorClaimGate:
    """Monotonic ownership of one turn. Entered once; there is no exit.

    Deliberately an object with state rather than a return value, because the property being
    enforced is about the WHOLE remainder of the turn: once entered, every exit path -- normal
    return, composition fault, dispatch fault, an exception from three frames down -- must produce a
    claimed result. A boolean local would be re-read by whoever remembered to; this refuses.
    """

    __slots__ = ("_claim", "_decision")

    def __init__(self) -> None:
        self._claim = ConductorClaim.NOT_CLAIMED
        self._decision: ProductDecision | None = None

    @property
    def claim(self) -> ConductorClaim:
        return self._claim

    @property
    def claimed(self) -> bool:
        return self._claim is not ConductorClaim.NOT_CLAIMED

    @property
    def decision(self) -> ProductDecision | None:
        return self._decision

    def enter(self, decision: ProductDecision) -> ProductDecision:
        """Commit to `decision`. Refuses NOT_CLAIMED, and refuses a second entry."""
        if self.claimed:
            raise ClaimViolationError(
                f"already claimed as {self._claim.value!r}; a claim is monotonic"
            )
        if not decision.claimed:
            raise ClaimViolationError("NOT_CLAIMED may not be entered; there is nothing to commit to")
        self._claim = decision.claim
        self._decision = decision
        return decision

    def integrity_failure(self, cause: BaseException | str) -> ProductDecision:
        """Convert a POST-CLAIM fault into a claimed integrity failure. Never returns None.

        This is the whole exception domain rule in one method: after the cutover, a composition or
        dispatch defect is a fact about this turn's accounting, and the reader is told so. Handing
        the turn to another lane at this point would let a narrower path answer a message whose
        bookkeeping the runtime already knows is wrong, and the wrongness would never surface.
        """
        if not self.claimed:
            raise ClaimViolationError(
                "integrity_failure is a POST-claim conversion; nothing has been claimed"
            )
        detail = (
            f"{type(cause).__name__}: {cause}" if isinstance(cause, BaseException) else str(cause)
        )
        previous = self._decision
        codes = (INTEGRITY_FAILURE_CODE, f"post_claim:{detail}")
        decision = ProductDecision(
            outcome_set=previous.outcome_set if previous is not None else RequirementOutcomeSet(),
            disposition=ProductDisposition.INTEGRITY_FAILURE,
            claim=ConductorClaim.CLAIMED_INTEGRITY_FAILURE,
            runtime_task_outcome=_runtime_outcome(ProductDisposition.INTEGRITY_FAILURE, codes),
            failure_lines=previous.failure_lines if previous is not None else (),
            integrity_codes=codes,
        )
        self._claim = ConductorClaim.CLAIMED_INTEGRITY_FAILURE
        self._decision = decision
        return decision


#: What a reader is told when the runtime cannot account for its own turn. Deterministic, and it
#: never pretends the request was served.
INTEGRITY_FAILURE_RESPONSE = (
    "I could not account for every part of that request, so I am not going to give you an answer "
    "that looks complete. Nothing here is safe to rely on yet."
)


def emergency_decision_payload(decision: Any) -> dict[str, Any]:
    """A serialized claimed decision, even when the decision's own serializer is broken.

    The last-resort post-claim exit assembles its result from a dict literal precisely so nothing
    left in it can fail -- and then called `decision.to_dict()` inside that literal, which is a
    remaining call that can fail. Measured: raising there escaped the dispatch as a second
    exception, so the one path written to guarantee a claimed turn was itself able to lose the
    claim. A comment asserting a path cannot fail is not the same as a path that cannot fail; this
    is the same lesson `lookup_capability` learned about being exhaustive in code.

    CARRIES, never decides. The disposition and outcome here are read off the decision the claim
    gate already made; when even that cannot be read, the constants are the ones that gate
    guarantees for a post-claim fault -- INTEGRITY_FAILURE and FAILED -- so this is a transcription
    of an existing verdict and not a sixth authority forming its own.
    """
    try:
        payload = decision.to_dict()
        if isinstance(payload, dict):
            return payload
    except BaseException:  # a broken serializer must not lose the claim
        pass

    def _value(attribute: str, fallback: str) -> str:
        try:
            held = getattr(decision, attribute, None)
            return str(getattr(held, "value", held) or fallback)
        except BaseException:
            return fallback

    try:
        codes = [str(code) for code in getattr(decision, "integrity_codes", ()) or ()]
    except BaseException:
        codes = []
    return {
        "disposition": _value("disposition", ProductDisposition.INTEGRITY_FAILURE.value),
        "claim": _value("claim", ConductorClaim.CLAIMED_INTEGRITY_FAILURE.value),
        "runtime_task_outcome": {
            "fulfillment_status": FulfillmentStatus.FAILED.value,
            "failure_stage": FAILURE_STAGE,
            "failure_codes": codes or [INTEGRITY_FAILURE_CODE],
            "retryable": False,
        },
        "failure_lines": [],
        "integrity_codes": codes or [INTEGRITY_FAILURE_CODE],
        "outcome_set": {"outcomes": []},
    }


def total_failure_response(decision: ProductDecision) -> str:
    """The truthful reply for a turn where nothing the user asked for was served.

    Named lines rather than a generic apology: the user asked for specific things and each one has
    a state. Falling through to another lane here would answer a different question and silently
    drop the rest, which is what the deleted `answered_count < unserved_count` branch did.
    """
    lines = list(decision.failure_lines)
    if not lines:
        return (
            "I could not serve any part of that request, and I am not going to answer a different "
            "question instead."
        )
    return "Could not be answered:\n" + "\n".join(lines)


__all__ = [
    "FAILURE_STAGE",
    "INTEGRITY_FAILURE_CODE",
    "INTEGRITY_FAILURE_RESPONSE",
    "ClaimViolationError",
    "ConductorClaim",
    "ConductorClaimGate",
    "ExecutionReport",
    "ProductDecision",
    "ProductDisposition",
    "emergency_decision_payload",
    "reduce_execution_report",
    "total_failure_response",
]
