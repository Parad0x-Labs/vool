"""Semantic admission for conductor operations.

Registration proves that an operation exists.  Argument expansion proves that a recognizer can
extract something from a string.  Neither proves that the operation's result can satisfy what the
user asked for.  This module owns that missing boundary.

The user's clause kind comes from :mod:`core.turn_ir`; conductor does not maintain a second clause
classifier.  An operation declares the effect it produces, the clause kinds that effect may serve,
and an optional domain predicate.  The planner may propose any registered name, but admission is a
runtime decision made before an operation sees arguments or acquires authority to execute.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from core.turn_ir import ClauseKind, TurnClause


class OperationEffect(str, Enum):
    """The externally meaningful result an operation can produce."""

    COMPUTED_VALUE = "computed_value"
    LIVE_OBSERVATION = "live_observation"
    WORKSPACE_EVIDENCE = "workspace_evidence"
    KNOWLEDGE_ANSWER = "knowledge_answer"
    DERIVED_ANALYSIS = "derived_analysis"
    MISSING_INFORMATION = "missing_information"
    SIDE_EFFECT = "side_effect"
    TRANSFORMED_CONTENT = "transformed_content"
    GENERATED_CONTENT = "generated_content"
    RECALLED_CONTENT = "recalled_content"
    ACTION_STATUS = "action_status"


#: The effect class of an operation whose observation leaves this machine through
#: the remote-fetch door. `OperationEffect` says WHAT an operation produces
#: ("live_observation") but not WHERE the observation comes from — and both
#: `machine_observation` (this host's own disk, battery, clock) and
#: `weather_lookup` (a remote provider) are LIVE_OBSERVATION. Receipt coverage
#: is a function of the transport, not the semantics, so it needs its own owning
#: field rather than a name list: the live-data lane's remote-operation receipt
#: set is DERIVED from this declaration (`core.live_data_retrieval_receipts`),
#: which is what keeps a newly landed remote tool from shipping un-receipted.
REMOTE_FETCH_EFFECT_CLASS = "remote_fetch"


# Effect compatibility is deliberately independent from operation names.  A newly registered
# operation may serve an existing clause kind only by declaring an effect that can satisfy it.
# ACT is the critical fail-closed row: evidence that text exists in a workspace is never a physical
# or digital side effect, even when the evidence operation itself executed successfully.
_EFFECTS_BY_KIND: dict[ClauseKind, frozenset[OperationEffect]] = {
    ClauseKind.KNOW: frozenset(
        {
            OperationEffect.COMPUTED_VALUE,
            OperationEffect.LIVE_OBSERVATION,
            OperationEffect.WORKSPACE_EVIDENCE,
            OperationEffect.KNOWLEDGE_ANSWER,
            OperationEffect.DERIVED_ANALYSIS,
            OperationEffect.MISSING_INFORMATION,
        }
    ),
    ClauseKind.COMPUTE: frozenset(
        {
            OperationEffect.COMPUTED_VALUE,
            OperationEffect.DERIVED_ANALYSIS,
            OperationEffect.MISSING_INFORMATION,
        }
    ),
    ClauseKind.OBSERVE: frozenset(
        {
            OperationEffect.LIVE_OBSERVATION,
            OperationEffect.WORKSPACE_EVIDENCE,
            OperationEffect.MISSING_INFORMATION,
            OperationEffect.ACTION_STATUS,
        }
    ),
    ClauseKind.ACT: frozenset({OperationEffect.SIDE_EFFECT, OperationEffect.ACTION_STATUS}),
    ClauseKind.TRANSFORM: frozenset({OperationEffect.TRANSFORMED_CONTENT}),
    ClauseKind.CREATE: frozenset(
        {OperationEffect.GENERATED_CONTENT, OperationEffect.SIDE_EFFECT}
    ),
    ClauseKind.RECALL: frozenset({OperationEffect.RECALLED_CONTENT}),
    ClauseKind.CLARIFY: frozenset({OperationEffect.MISSING_INFORMATION}),
    # UNKNOWN is not a wildcard.  An operation must explicitly opt into it, and its own recognizer
    # or domain predicate must still establish scope.  This preserves existing imperative forms
    # such as "get the weather" without turning an unclassified clause into general authority.
    ClauseKind.UNKNOWN: frozenset(OperationEffect),
}


@dataclass(frozen=True)
class OperationCapability:
    """What an operation can satisfy, separate from how it executes."""

    effect: OperationEffect
    domain: str
    accepted_kinds: frozenset[ClauseKind]
    accepts_clause: Callable[[TurnClause], bool] | None = None
    #: A derived operation may consume only results with these effects. Empty means it declares no
    #: dependency-effect constraint. Every supplied dependency must be in the declared set.
    accepted_dependency_effects: frozenset[OperationEffect] = frozenset()
    #: The TRANSPORT class of the work this operation performs to observe:
    #: `REMOTE_FETCH_EFFECT_CLASS` ("remote_fetch") when it reaches a remote host,
    #: empty when the operation has not declared one. Semantically distinct from
    #: `effect`, which names the RESULT kind (both a local disk read and a remote
    #: weather fetch are LIVE_OBSERVATION). Receipt and accounting coverage is
    #: derived from this declaration, never from operation-name lists — a name
    #: list is what let `water_temperature` ship with real remote work and zero
    #: receipts (measured at a2308a26).
    effect_class: str = ""


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    reason: str = ""


def decide_operation_compatibility(
    capability: OperationCapability | None,
    clause: TurnClause,
    *,
    dependency_effects: tuple[OperationEffect, ...] = (),
) -> CapabilityDecision:
    """Whether ``capability`` can satisfy ``clause``; failure is explicit and fail-closed."""

    if capability is None:
        return CapabilityDecision(False, "operation has no declared semantic capability")
    if clause.kind not in capability.accepted_kinds:
        return CapabilityDecision(
            False,
            f"{capability.domain} capability does not serve {clause.kind.value} requests",
        )
    if capability.effect not in _EFFECTS_BY_KIND.get(clause.kind, frozenset()):
        return CapabilityDecision(
            False,
            (
                f"{capability.effect.value} cannot satisfy a {clause.kind.value} request "
                f"in the {capability.domain} domain"
            ),
        )
    if capability.accepts_clause is not None:
        try:
            accepted = bool(capability.accepts_clause(clause))
        except Exception as exc:
            return CapabilityDecision(
                False,
                f"{capability.domain} capability scope check failed: {type(exc).__name__}: {exc}",
            )
        if not accepted:
            return CapabilityDecision(
                False,
                f"request is outside the {capability.domain} capability domain",
            )
    if capability.accepted_dependency_effects:
        if not dependency_effects:
            return CapabilityDecision(
                False,
                f"{capability.domain} capability requires compatible earlier results",
            )
        incompatible = tuple(
            effect
            for effect in dependency_effects
            if effect not in capability.accepted_dependency_effects
        )
        if incompatible:
            names = ", ".join(effect.value for effect in incompatible)
            return CapabilityDecision(
                False,
                f"{capability.domain} capability cannot consume dependency effects: {names}",
            )
    return CapabilityDecision(True)


__all__ = [
    "REMOTE_FETCH_EFFECT_CLASS",
    "CapabilityDecision",
    "OperationCapability",
    "OperationEffect",
    "decide_operation_compatibility",
]
