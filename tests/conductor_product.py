"""Drive one conductor turn through the REAL product-truth cutover, for tests.

`compose_answer` used to return five signals a caller could read as a verdict -- `complete`,
`answered_count`, `unserved_count`, `absent_obligations`, `reported_obligation_count` -- and
production read them independently in four different places. That is the competing truth the F5
cutover removed, so `ComposedAnswer` now carries formatting and nothing else.

The questions those signals answered are still worth asking, and the tests that ask them are worth
keeping. They are answered HERE, from the one authority: every field below is derived from the
`ProductDecision` that `reduce_execution_report` produced for this exact execution, or counted off
the composed text. Nothing in this module decides anything, and nothing in production imports it --
which is the point. A test asserting `complete` is now asserting a property of the decision, not of
a second opinion that could drift from it.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.conductor.compose import ComposedAnswer, compose_answer
from core.conductor.product_decision import (
    ExecutionReport,
    ProductDecision,
    ProductDisposition,
    reduce_execution_report,
)


def decide(plan, outcomes: Sequence) -> ProductDecision:
    """The cutover, exactly as `_maybe_answer_conductor_turn` performs it."""
    return reduce_execution_report(
        ExecutionReport(
            bound_plan=plan.bound_plan,
            node_outcomes=tuple(outcomes),
            planned_node_ids=tuple(node.node_id for node in plan.nodes),
        )
    )


@dataclass(frozen=True)
class ComposedProduct(ComposedAnswer):
    """The composed answer, carrying the decision that governs it.

    A `ComposedAnswer` subclass rather than a wrapper, so a test that already asserts the composer
    returned a `ComposedAnswer` keeps asserting exactly that. The extra members are read-only views
    over the decision; none of them is a second opinion.
    """

    decision: ProductDecision = None  # type: ignore[assignment]

    @property
    def composed(self) -> ComposedAnswer:
        return self

    # -- verdict, derived from the ONE authority ------------------------------------------------
    @property
    def complete(self) -> bool:
        """Accounted for, and nothing answered outside its prerequisite closure.

        An honestly reported gap is complete in this sense; broken accounting never is.
        """
        return self.decision.disposition is not ProductDisposition.INTEGRITY_FAILURE

    @property
    def disposition(self) -> ProductDisposition:
        return self.decision.disposition

    @property
    def outcome_set(self):
        return self.decision.outcome_set

    @property
    def missing_node_ids(self) -> tuple[str, ...]:
        return tuple(
            code.split(":", 1)[1]
            for code in self.decision.integrity_codes
            if code.startswith("missing_node_outcome:")
        )

    @property
    def answered_count(self) -> int:
        return len(self.answered_node_ids)

    @property
    def unserved_count(self) -> int:
        body = self.text
        marker = "Could not be answered:"
        if marker not in body:
            return 0
        return sum(
            1 for line in body.split(marker, 1)[1].splitlines() if line.strip().startswith("- ")
        )

    @property
    def absent_obligations(self) -> tuple[str, ...]:
        return tuple(
            outcome.realization.requirement_id
            for outcome in self.decision.outcome_set.outcomes
            if outcome.state.value != "satisfied"
        )

    @property
    def reported_obligation_count(self) -> int:
        return len(self.decision.failure_lines)


def compose_product(plan, outcomes: Sequence) -> ComposedProduct:
    decision = decide(plan, outcomes)
    composed = compose_answer(plan, outcomes, decision)
    return ComposedProduct(
        text=composed.text,
        provenance=composed.provenance,
        answered_node_ids=composed.answered_node_ids,
        decision=decision,
    )


__all__ = ["ComposedProduct", "compose_product", "decide"]
