"""Realization identity: what work each requirement becomes, decided BEFORE any argument exists.

The F4 defect stated as an ordering, because that is what it was:

    WRONG   argument sets -> realizations -> nodes
    RIGHT   requirements  -> realizations -> WorkSpec -> capability lookup -> binding -> nodes

Deriving realizations from argument sets means a requirement whose projector returned nothing has
zero realizations, therefore no node, therefore no outcome, therefore no line -- and the reader is
told nothing about a request the runtime definitely received. Every derived frame did exactly this:
"the ratio between them" has no ordinary subject slots, so it projected no arguments, so it had no
realization at all while the node that computed it ran and succeeded beside it.

So the realization is created from the REQUIREMENT, before anything is expanded:

* every executable requirement produces at least one `RequirementRealization`;
* a family with a declared fan-out role produces one per coordinated member, so a two-asset request
  whose second asset is unknown reports THAT asset by name rather than the whole frame;
* a family without one -- a conversion, a search, a derived computation -- produces exactly one.

Only then is a `WorkSpec` built and a capability looked up. `CAPABILITY_UNAVAILABLE` is a value the
lookup RETURNS; it is not what happens when a projector comes back empty, when an exception is
swallowed, when a binding is missed or when a planner forgot a family. Those are defects, and they
are typed as defects so they cannot be read as an answer about the world.

Three immutable objects carry it, and each enforces one mechanical invariant in its own
constructor rather than in whoever remembers to check:

* `RealizationLedger` -- realization ids are unique.
* `RealizationBinding` -- exactly one of `bound_node_ids` / `non_execution`, never both, never
  neither. That is I3, and as a constructor rule it cannot be violated by a code path that forgets.
* `BoundExecutionPlan` -- the ordered realization-id set is preserved exactly. Dedup may collapse
  two realizations onto one node; it may never collapse two realizations.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from core.conductor.node import NodeLifecycle, NodeOutcome
from core.conductor.registry import OperationSpec, execution_key, operation_spec
from core.conductor.requirements import (
    RequirementLedger,
    RequirementSlot,
    SlotResolution,
    UserRequirement,
)
from core.conductor.semantic_proof import frame_contract


class RealizationState(str, Enum):
    """Where one realization came to rest. Every value is assigned by production code."""

    #: Neither bound to a node nor given non-execution evidence. Never terminal; an accounting hole.
    UNBOUND = "unbound"
    #: A required slot resolved to nothing, so nothing was attempted.
    UNRESOLVED_SUBJECT = "unresolved_subject"
    #: A typed capability lookup returned UNAVAILABLE. The ONLY way to reach this state.
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    #: A permission or policy boundary refused it.
    POLICY_BLOCKED = "policy_blocked"
    #: Bound to a node that has not finished.
    PLANNED = "planned"
    SATISFIED = "satisfied"
    PARTIALLY_SATISFIED = "partially_satisfied"
    EXECUTION_FAILED = "execution_failed"
    #: A prerequisite did not reach SATISFIED, so this was not attempted.
    DEPENDENCY_BLOCKED = "dependency_blocked"
    #: NOT an outcome. The accounting itself is broken and the turn must not ship.
    INTEGRITY_FAILURE = "integrity_failure"


#: States a reader can be told about. INTEGRITY_FAILURE and UNBOUND are deliberately absent.
ACCOUNTED_STATES = frozenset(
    {
        RealizationState.SATISFIED,
        RealizationState.PARTIALLY_SATISFIED,
        RealizationState.EXECUTION_FAILED,
        RealizationState.UNRESOLVED_SUBJECT,
        RealizationState.CAPABILITY_UNAVAILABLE,
        RealizationState.POLICY_BLOCKED,
        RealizationState.DEPENDENCY_BLOCKED,
    }
)


class NonExecutionReason(str, Enum):
    """Why a realization legitimately has no node -- or, for the last one, illegitimately."""

    UNRESOLVED_SUBJECT = "unresolved_subject"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    POLICY_BLOCKED = "policy_blocked"
    #: Not legitimate. A defect that produced no work, carried as a defect so it cannot be read as
    #: an answer about the world. Reduces to INTEGRITY_FAILURE and the turn refuses to ship.
    INTEGRITY_DEFECT = "integrity_defect"


_NON_EXECUTION_STATES = {
    NonExecutionReason.UNRESOLVED_SUBJECT: RealizationState.UNRESOLVED_SUBJECT,
    NonExecutionReason.CAPABILITY_UNAVAILABLE: RealizationState.CAPABILITY_UNAVAILABLE,
    NonExecutionReason.POLICY_BLOCKED: RealizationState.POLICY_BLOCKED,
    NonExecutionReason.INTEGRITY_DEFECT: RealizationState.INTEGRITY_FAILURE,
}


@dataclass(frozen=True)
class NonExecutionEvidence:
    """A typed statement that no work will happen, and why. Never a bare absence."""

    reason: NonExecutionReason
    detail: str = ""

    @property
    def state(self) -> RealizationState:
        return _NON_EXECUTION_STATES[self.reason]

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason.value, "detail": self.detail}


# --- work specs and capability lookup ----------------------------------------------------------


@dataclass(frozen=True)
class WorkSpec:
    """The typed description of one realization's work, before any operation sees it.

    Role-keyed rather than argument-keyed on purpose: this is what the USER's frame established,
    and turning it into an operation's parameter names is the capability lookup's job. A WorkSpec
    exists for a family with no registered operation, which is the whole reason
    CAPABILITY_UNAVAILABLE can be a typed answer rather than an empty projection.
    """

    family: str
    #: role -> the surfaces this realization owns, in coordinated order.
    role_surfaces: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: role -> typed arguments a resolver attached to the slot, when it could.
    resolved: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Roles whose required slot resolved to nothing.
    unresolved_roles: tuple[str, ...] = ()
    #: Roles whose resolver RAISED. Kept apart from `unresolved_roles` all the way down, because
    #: they mean different things and only one of them is a statement about the subject.
    defect_roles: tuple[str, ...] = ()
    #: The frame span, for a family whose work has no arguments to key on.
    frame_span: tuple[int, int] = (-1, -1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "role_surfaces": {k: list(v) for k, v in self.role_surfaces.items()},
            "unresolved_roles": list(self.unresolved_roles),
        }


class CapabilityLookupState(str, Enum):
    """The four answers a capability lookup may give. There is no fifth, and no None."""

    AVAILABLE = "available"
    #: No registered operation serves this family. A real answer about the runtime.
    UNAVAILABLE = "unavailable"
    #: The subject could not be bound, so there is nothing to serve. Not a capability statement.
    UNRESOLVED_SUBJECT = "unresolved_subject"
    #: The lookup itself is broken -- an operation that raised, or one that serves the family and
    #: produced no arguments for a fully resolved frame. NEVER reported as UNAVAILABLE: "we cannot
    #: do that" and "our projector is broken" are different sentences and only one is true.
    DEFECT = "defect"


@dataclass(frozen=True)
class CapabilityLookupResult:
    state: CapabilityLookupState
    arguments: Mapping[str, Any] = field(default_factory=dict)
    execution_key: str = ""
    detail: str = ""

    @classmethod
    def available(cls, arguments: Mapping[str, Any], key: str) -> CapabilityLookupResult:
        return cls(CapabilityLookupState.AVAILABLE, dict(arguments), key)

    @classmethod
    def unavailable(cls, detail: str) -> CapabilityLookupResult:
        return cls(CapabilityLookupState.UNAVAILABLE, detail=detail)

    @classmethod
    def unresolved(cls, detail: str) -> CapabilityLookupResult:
        return cls(CapabilityLookupState.UNRESOLVED_SUBJECT, detail=detail)

    @classmethod
    def defect(cls, detail: str) -> CapabilityLookupResult:
        return cls(CapabilityLookupState.DEFECT, detail=detail)

    @property
    def non_execution(self) -> NonExecutionEvidence | None:
        if self.state is CapabilityLookupState.AVAILABLE:
            return None
        if self.state is CapabilityLookupState.UNAVAILABLE:
            return NonExecutionEvidence(NonExecutionReason.CAPABILITY_UNAVAILABLE, self.detail)
        if self.state is CapabilityLookupState.UNRESOLVED_SUBJECT:
            return NonExecutionEvidence(NonExecutionReason.UNRESOLVED_SUBJECT, self.detail)
        return NonExecutionEvidence(NonExecutionReason.INTEGRITY_DEFECT, self.detail)


def _arguments_for(spec: OperationSpec, work: WorkSpec) -> Mapping[str, Any]:
    """Assemble one realization's typed arguments from the roles its frame bound.

    A frame/slot ADAPTER, not a recognizer: every value here was proven as a role span and read out
    of the canonical text before anything called this. Per-family because the argument NAMES are
    the operation's, and the roles are the user's.
    """
    surfaces = {role: tuple(values) for role, values in work.role_surfaces.items()}

    def first(role: str) -> str:
        values = surfaces.get(role, ())
        return values[0] if values else ""

    if work.family == "fx_quote":
        # Role surfaces resolve through the currency contract, not `.upper()` alone: a proposed
        # frame can carry "US" or "usd", and taking the surface verbatim minted a SECOND identity
        # ("US/RUB") beside the planner node's ("USD/RUB") -- one request, two verdicts, both
        # printed (a served conversion AND an "invalid ISO currency code 'US'" failure line,
        # measured live 2026-08-28). One resolver, one identity, one node.
        from core.currency_intent import resolve_currency_pair

        base, quote = resolve_currency_pair(first("base_currency"), first("quote_currency"))
        amount = first("amount")
        arguments: dict[str, Any] = {
            "entity": f"{base}/{quote}",
            "base": base,
            "quote": quote,
            "request_kind": "conversion" if amount else "rate",
        }
        if amount:
            arguments["amount"] = amount.replace(",", "")
        return arguments
    if work.family == "place_search":
        return {
            "entity": f"{first('service')} near {first('location')}",
            "service": first("service"),
            "location": first("location"),
        }
    if work.family == "calculation":
        return {"entity": first("expression"), "expression_text": first("expression")}
    if work.family == "structured_field":
        return {"entity": first("field_name"), "field_value": first("field_value")}
    # Subject-per-node families carry the resolver's own typed arguments for the one subject this
    # realization owns. The resolver produced them; nothing here re-derives them from text.
    for values in work.resolved.values():
        if values:
            return dict(values)
    return {}


def lookup_capability(work: WorkSpec) -> CapabilityLookupResult:
    """The one place `CAPABILITY_UNAVAILABLE` can be decided, and the only thing that may decide it.

    EXHAUSTIVE, and now actually so. Every exit is a typed `CapabilityLookupResult`, including the
    exits nothing here writes: a registry that raises, a frame contract that raises, an adapter that
    raises. Those used to escape as exceptions, unwind past the realization stage, and be swallowed
    by the conductor's pre-claim handler -- so a software defect became a silent decline and the
    turn went to another lane with no record that anything had broken. A defect is a DEFECT: typed,
    reduced to INTEGRITY_FAILURE, and refused rather than hidden.

    Order matters and is the F4 rule: an unresolved subject is answered BEFORE a capability is
    consulted, because "nobody could tell me what Palladium is" and "this runtime cannot price
    things" are different facts and reporting the second for the first is a lie about the runtime.
    """
    try:
        return _lookup_capability(work)
    except Exception as exc:
        # The ONLY exit this handler has is DEFECT. It cannot reach UNAVAILABLE and it cannot reach
        # None, which is what makes "exhaustive" a property of the code rather than of the docstring.
        return CapabilityLookupResult.defect(
            f"{work.family} capability lookup raised {type(exc).__name__}: {exc}"
        )


def _lookup_capability(work: WorkSpec) -> CapabilityLookupResult:
    if work.defect_roles:
        return CapabilityLookupResult.defect(
            f"{work.family} resolver raised for {','.join(work.defect_roles)}"
        )
    if work.unresolved_roles:
        return CapabilityLookupResult.unresolved(",".join(work.unresolved_roles))
    spec = operation_spec(work.family)
    if spec is None:
        return CapabilityLookupResult.unavailable(f"no registered operation named {work.family!r}")
    contract = frame_contract(work.family)
    if contract is not None and not contract.roles:
        # A frame with no ordinary subject roles -- a derived computation. There are no arguments to
        # key on, so its identity is its own frame span. Returning UNAVAILABLE here is exactly the
        # defect F4 names: the work is real, the node exists, and only the projection is empty.
        start, end = work.frame_span
        return CapabilityLookupResult.available(
            {}, f"{work.family}:frame={start}:{end}"
        )
    arguments = _arguments_for(spec, work)
    if not arguments:
        # Registered, fully resolved, and the adapter produced nothing. That is a defect in this
        # module or in the contract, NOT a statement that the runtime lacks the capability.
        return CapabilityLookupResult.defect(
            f"{work.family} adapter produced no arguments for a fully resolved frame"
        )
    try:
        key = execution_key(spec, arguments)
    except Exception as exc:
        # The ONLY exit this handler has is DEFECT. It cannot reach UNAVAILABLE, which is what
        # "exceptions remain typed defects" means as code rather than as an intention: a broken key
        # producer can never be reported as "this runtime does not do that".
        return CapabilityLookupResult.defect(
            f"{work.family} execution key producer raised {type(exc).__name__}: {exc}"
        )
    return CapabilityLookupResult.available(arguments, key)


# --- realizations ------------------------------------------------------------------------------


@dataclass(frozen=True)
class RequirementRealization:
    """One atomic unit of accounting for a requirement, named before any work is described."""

    realization_id: str
    requirement_id: str
    family: str
    #: The frame span this came from, in the user's own words.
    source_surface: str
    owned_slot_ids: tuple[str, ...] = ()
    prerequisite_realization_ids: tuple[str, ...] = ()
    #: What a reader is shown. Built from the family's DISPLAY roles, never from slot order and
    #: never from a node's request text.
    display_subject: str = ""
    work: WorkSpec | None = None
    #: The identity of the work, produced by the OPERATION. Two realizations sharing one are the
    #: same work and may share a node; two that differ never may.
    execution_key: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    lookup: CapabilityLookupResult | None = None

    @property
    def frame_span(self) -> tuple[int, int]:
        return self.work.frame_span if self.work is not None else (-1, -1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "realization_id": self.realization_id,
            "requirement_id": self.requirement_id,
            "family": self.family,
            "display_subject": self.display_subject,
            "execution_key": self.execution_key,
            "owned_slot_ids": list(self.owned_slot_ids),
            "prerequisites": list(self.prerequisite_realization_ids),
            "lookup": self.lookup.state.value if self.lookup is not None else "",
        }


def display_subject(requirement: UserRequirement, slots: Sequence[RequirementSlot]) -> str:
    """The thing a reader recognises, owned by semantic ROLE rather than by slot order.

    "the first unresolved slot" produced sentences naming an amount when the currency pair was the
    subject -- "500 could not be identified" for a failed EUR to JPY conversion. The role order and
    the joining phrase both come from the family's frame contract, so there is one table rather
    than one per module.
    """
    contract = frame_contract(requirement.family)
    if contract is None or not contract.display_roles:
        return requirement.surface
    values = [
        slot.surface
        for role in contract.display_roles
        for slot in slots
        if slot.role == role and slot.surface
    ]
    return contract.display_join.join(values) if values else requirement.surface


@dataclass(frozen=True)
class RealizationLedger:
    """Every realization the ledger's requirements produced. Immutable, and ids are unique."""

    realizations: tuple[RequirementRealization, ...] = ()

    def __post_init__(self) -> None:
        ids = [r.realization_id for r in self.realizations]
        if len(ids) != len(set(ids)):
            raise ValueError(
                f"realization ids must be unique; got {len(ids)} with {len(set(ids))} distinct. "
                "Two realizations sharing an id are one accounting slot for two obligations."
            )

    def __len__(self) -> int:
        return len(self.realizations)

    def __iter__(self):
        return iter(self.realizations)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(r.realization_id for r in self.realizations)

    def by_id(self, realization_id: str) -> RequirementRealization | None:
        for realization in self.realizations:
            if realization.realization_id == realization_id:
                return realization
        return None

    def of_requirement(self, requirement_id: str) -> tuple[RequirementRealization, ...]:
        return tuple(r for r in self.realizations if r.requirement_id == requirement_id)


def plan_realizations(ledger: RequirementLedger) -> RealizationLedger:
    """One or more realizations for EVERY executable requirement, before arguments or nodes exist.

    This is the invariant the previous shape could not express: the count of realizations is a
    function of the requirement's own roles, so a projector that returns nothing, an operation
    nobody registered, and a derived frame with no subjects all still have somewhere for their
    outcome to be recorded.
    """
    realizations: list[RequirementRealization] = []
    by_requirement: dict[str, list[str]] = {}

    for requirement in ledger.executable:
        contract = frame_contract(requirement.family)
        fan_out = contract.fan_out_role if contract is not None else ""
        members = (
            sorted(requirement.of_role(fan_out), key=lambda s: s.member_ordinal)
            if fan_out
            else ()
        )
        others = tuple(s for s in requirement.slots if not fan_out or s.role != fan_out)
        # `members or ((),)` is the whole of "a requirement with zero ordinary subject slots still
        # gets a realization": no fan-out role, or a fan-out role with no members, yields exactly
        # one requirement-level realization rather than none.
        groups: tuple[tuple[RequirementSlot, ...], ...] = (
            tuple((member,) for member in members) if members else ((),)
        )
        for position, group in enumerate(groups):
            owned = (*group, *others)
            realization_id = f"{requirement.requirement_id}:realization:{position}"
            realizations.append(
                RequirementRealization(
                    realization_id=realization_id,
                    requirement_id=requirement.requirement_id,
                    family=requirement.family,
                    source_surface=requirement.surface,
                    owned_slot_ids=tuple(s.slot_id for s in owned),
                    display_subject=display_subject(requirement, owned),
                    work=_work_spec(requirement, owned),
                )
            )
            by_requirement.setdefault(requirement.requirement_id, []).append(realization_id)

    linked: list[RequirementRealization] = []
    for realization in realizations:
        requirement = ledger.by_id(realization.requirement_id)
        prerequisites = tuple(
            prerequisite_id
            for required in (requirement.requires if requirement else ())
            for prerequisite_id in by_requirement.get(required, ())
        )
        linked.append(replace(realization, prerequisite_realization_ids=prerequisites))
    return RealizationLedger(tuple(linked))


def _work_spec(
    requirement: UserRequirement, owned: Sequence[RequirementSlot]
) -> WorkSpec:
    role_surfaces: dict[str, tuple[str, ...]] = {}
    resolved: dict[str, Mapping[str, Any]] = {}
    unresolved: list[str] = []
    defects: list[str] = []
    for slot in owned:
        role_surfaces[slot.role] = (*role_surfaces.get(slot.role, ()), slot.surface)
        if slot.resolution is SlotResolution.RESOLVED and slot.arguments:
            resolved[slot.role] = dict(slot.arguments)
        elif slot.resolution is SlotResolution.RESOLVER_DEFECT:
            defects.append(slot.role)
        elif slot.required and slot.resolution is SlotResolution.UNRESOLVED_SUBJECT:
            unresolved.append(slot.role)
    return WorkSpec(
        family=requirement.family,
        role_surfaces=role_surfaces,
        resolved=resolved,
        unresolved_roles=tuple(unresolved),
        defect_roles=tuple(defects),
        frame_span=(requirement.start, requirement.end),
    )


def resolve_capabilities(ledger: RealizationLedger) -> RealizationLedger:
    """Attach one typed `CapabilityLookupResult` to every realization. Adds and removes none."""
    return RealizationLedger(
        tuple(
            replace(
                realization,
                lookup=(lookup := lookup_capability(realization.work or WorkSpec(realization.family))),
                arguments=dict(lookup.arguments),
                execution_key=lookup.execution_key,
            )
            for realization in ledger.realizations
        )
    )


# --- binding -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RealizationBinding:
    """What became of one realization: a node it runs on, or typed evidence that nothing will.

    Exactly one of the two, enforced here rather than by convention. A binding with neither is the
    accounting hole this whole architecture exists to close, and a binding with both is a
    realization claiming to be simultaneously served and refused.
    """

    realization_id: str
    bound_node_ids: tuple[str, ...] = ()
    non_execution: NonExecutionEvidence | None = None

    def __post_init__(self) -> None:
        if bool(self.bound_node_ids) == (self.non_execution is not None):
            raise ValueError(
                f"{self.realization_id!r} must have exactly one of bound_node_ids or "
                f"non_execution; got nodes={self.bound_node_ids!r} "
                f"evidence={self.non_execution!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "realization_id": self.realization_id,
            "bound_node_ids": list(self.bound_node_ids),
            "non_execution": (
                self.non_execution.to_dict() if self.non_execution is not None else None
            ),
        }


@dataclass(frozen=True)
class BoundExecutionPlan:
    """The realization ledger, plus exactly one binding for each of its realizations.

    The constructor is where I2 and I4 live. `bindings` must name the realization ids in the same
    order, with no additions and no omissions -- so a dedupe pass that collapsed two realizations
    into one, or a projection loop that quietly skipped one, cannot produce a plan at all.
    """

    realization_ledger: RealizationLedger = field(default_factory=RealizationLedger)
    bindings: tuple[RealizationBinding, ...] = ()

    def __post_init__(self) -> None:
        expected = list(self.realization_ledger.ids)
        actual = [b.realization_id for b in self.bindings]
        if actual != expected:
            raise ValueError(
                "bindings must preserve the ordered realization-id set exactly; "
                f"expected {expected!r}, got {actual!r}. Dedup may change node cardinality, "
                "never realization cardinality."
            )

    def binding_of(self, realization_id: str) -> RealizationBinding | None:
        for binding in self.bindings:
            if binding.realization_id == realization_id:
                return binding
        return None

    @property
    def node_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for binding in self.bindings:
            for node_id in binding.bound_node_ids:
                if node_id not in seen:
                    seen.append(node_id)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "realizations": [r.to_dict() for r in self.realization_ledger.realizations],
            "bindings": [b.to_dict() for b in self.bindings],
        }


def bind_existing_nodes(
    ledger: RealizationLedger, nodes: Sequence[Any]
) -> dict[str, tuple[str, ...]]:
    """realization_id -> node ids an EXISTING node already performs.

    Two typed channels, no text comparison in either:

    * `execution_key`, which the OPERATION produces for both sides, so a node the planner built and
      a realization projected from a requirement are the same work when the operation says so;
    * for a realization with no arguments to key on -- a derived computation -- an overlap between
      the node's clause span and the frame span, both measured against the one canonical text.

    Surface overlap and entity-only keys are what this replaces. They made "calculation" and
    "137 x 29" different work, and two genuinely different conversions the same work.
    """
    by_key: dict[str, list[str]] = {}
    for node in nodes:
        spec = operation_spec(node.operation)
        if spec is None:
            continue
        by_key.setdefault(execution_key(spec, node.arguments), []).append(node.node_id)

    bindings: dict[str, tuple[str, ...]] = {}
    for realization in ledger.realizations:
        lookup = realization.lookup
        if lookup is None or lookup.state is not CapabilityLookupState.AVAILABLE:
            continue
        matched = by_key.get(realization.execution_key, ())
        if matched:
            bindings[realization.realization_id] = (matched[0],)
            continue
        if realization.arguments:
            continue
        start, end = realization.frame_span
        if start < 0:
            continue
        span_matched = [
            node.node_id
            for node in nodes
            if node.operation == realization.family
            and node.clause_span[0] >= 0
            and node.clause_span[0] < end
            and start < node.clause_span[1]
        ]
        if span_matched:
            bindings[realization.realization_id] = (span_matched[0],)
    return bindings


# --- reduction ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RealizationOutcome:
    realization: RequirementRealization
    state: RealizationState
    node_id: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "realization_id": self.realization.realization_id,
            "display_subject": self.realization.display_subject,
            "state": self.state.value,
            "node_id": self.node_id,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RequirementOutcomeSet:
    """The single authority on what happened to every realization. Built once, read by everything."""

    outcomes: tuple[RealizationOutcome, ...] = ()

    def state_of(self, realization_id: str) -> RealizationState:
        for outcome in self.outcomes:
            if outcome.realization.realization_id == realization_id:
                return outcome.state
        return RealizationState.INTEGRITY_FAILURE

    @property
    def satisfied(self) -> tuple[RealizationOutcome, ...]:
        return tuple(o for o in self.outcomes if o.state is RealizationState.SATISFIED)

    @property
    def unaccounted(self) -> tuple[RealizationOutcome, ...]:
        return tuple(o for o in self.outcomes if o.state not in ACCOUNTED_STATES)

    @property
    def integrity_failures(self) -> tuple[RealizationOutcome, ...]:
        return tuple(o for o in self.outcomes if o.state is RealizationState.INTEGRITY_FAILURE)

    def failure_lines(self) -> tuple[str, ...]:
        """One reader-facing line per realization that did not reach SATISFIED.

        Named from the realization's own display subject, which came from the family's display
        ROLES. Never the first unresolved slot, never a node's request text, never an internal name.
        Partial results retain their operation's own rendered values and gap disclosure;
        a generic failure row for the same subject would suppress those valid values.
        """
        sentences = {
            RealizationState.UNRESOLVED_SUBJECT: (
                "could not be identified, so nothing was looked up for it"
            ),
            RealizationState.CAPABILITY_UNAVAILABLE: "is not something this runtime can look up",
            RealizationState.POLICY_BLOCKED: "was not permitted on this turn",
            RealizationState.EXECUTION_FAILED: "was attempted and did not come back",
            RealizationState.DEPENDENCY_BLOCKED: (
                "was not attempted because something it needs is missing"
            ),
        }
        return tuple(
            f"- {outcome.realization.display_subject} — {sentences[outcome.state]}"
            for outcome in self.outcomes
            if outcome.state in sentences
        )

    def to_dict(self) -> dict[str, Any]:
        return {"outcomes": [o.to_dict() for o in self.outcomes]}


def reduce_bound_plan(
    plan: BoundExecutionPlan,
    outcomes: Sequence[NodeOutcome],
    *,
    requirement_nodes: Mapping[str, Sequence[str]] | None = None,
) -> RequirementOutcomeSet:
    """Every realization's state, from its binding and the node outcomes. No other input.

    A node that SUCCEEDED reduces to SATISFIED for EVERY realization bound to it -- that is I5, and
    it is why two realizations sharing one deduplicated node are both accounted for rather than one
    of them silently disappearing with the duplicate node.

    ``requirement_nodes`` (requirement id -> node ids realizing it, from `ConductorPlan`) closes the
    gap the bindings alone cannot: a realization whose PLAN-TIME binding says non-execution or
    dependency-blocked may still have been SERVED by a node that ran for the same requirement
    through a different resolver. Measured live 2026-08-29: "get berlin temp then tell me if its
    above 20c" was answered with the temperature AND refused the same clause in the same breath,
    because the realization state was frozen at planning and the served node outcome was never
    consulted. A clause a node actually served can never be reported as refused.
    """
    by_node = {outcome.node.node_id: outcome for outcome in outcomes}
    succeeded_node_ids = {outcome.node.node_id for outcome in outcomes if outcome.fulfilled}
    requirement_of = {
        realization.realization_id: realization.requirement_id
        for realization in plan.realization_ledger.realizations
    }
    requirement_node_map = dict(requirement_nodes or {})

    def _requirement_served(realization_id: str) -> bool:
        """Whether THIS realization was served by a node that actually ran and succeeded.

        Two scopes, in order, and the difference between them is the fan-out law:

        * The realization's OWN bound nodes. Always authoritative: a binding is the operation's
          execution-key match, so a node bound here is work attributed to this subject.
        * The requirement's nodes -- but ONLY for a requirement that did not fan out. A
          single-realization requirement has exactly one subject, so a succeeded node for the
          requirement served that subject, whatever the plan-time binding said (the
          answer-then-refuse contradiction this rescued: berlin-then-threshold, a mislabelled
          family whose span a covering node served).

        A FAN-OUT requirement is one requirement over SEVERAL subjects, one realization each.
        There, requirement-level truth is exactly wrong: "the price of Gold and Palladium" is
        one requirement whose Gold node succeeding says nothing about Palladium, and rescuing
        the Palladium realization from the Gold node is how a partial turn reported itself
        fulfilled and a dependent computed over a prerequisite that never resolved.
        """
        binding = plan.binding_of(realization_id)
        own_node_ids = tuple(binding.bound_node_ids) if binding is not None else ()
        if own_node_ids and any(node_id in succeeded_node_ids for node_id in own_node_ids):
            return True
        requirement_id = requirement_of.get(realization_id)
        if requirement_id is None:
            return False
        if len(plan.realization_ledger.of_requirement(requirement_id)) > 1:
            return False
        return any(
            node_id in succeeded_node_ids
            for node_id in requirement_node_map.get(requirement_id, ())
        )

    states: dict[str, RealizationState] = {}
    details: dict[str, str] = {}
    node_of: dict[str, str] = {}

    for binding in plan.bindings:
        if _requirement_served(binding.realization_id):
            # A node ran for this requirement and succeeded. That fact outranks any plan-time
            # binding state: the clause was served, and reporting it as refused is the
            # answer-then-refuse contradiction the composer must never ship.
            states[binding.realization_id] = RealizationState.SATISFIED
            details[binding.realization_id] = "served by node execution"
            continue
        if binding.non_execution is not None:
            states[binding.realization_id] = binding.non_execution.state
            details[binding.realization_id] = binding.non_execution.detail
            continue
        node_id = binding.bound_node_ids[0]
        node_of[binding.realization_id] = node_id
        outcome = by_node.get(node_id)
        if outcome is None:
            # Bound to a node the execution report never accounted for. Not a failure of the work:
            # a failure of the reduction, and it must not ship as one of the reportable states.
            states[binding.realization_id] = RealizationState.PLANNED
            details[binding.realization_id] = f"no outcome for node {node_id}"
        elif outcome.fulfilled:
            states[binding.realization_id] = RealizationState.SATISFIED
        elif outcome.partially_fulfilled:
            states[binding.realization_id] = RealizationState.PARTIALLY_SATISFIED
        elif outcome.succeeded:
            states[binding.realization_id] = RealizationState.EXECUTION_FAILED
        elif outcome.state is NodeLifecycle.UNRESOLVED:
            states[binding.realization_id] = RealizationState.CAPABILITY_UNAVAILABLE
        elif outcome.state is NodeLifecycle.DEPENDENCY_FAILED:
            states[binding.realization_id] = RealizationState.DEPENDENCY_BLOCKED
        elif outcome.state is NodeLifecycle.FAILED:
            states[binding.realization_id] = RealizationState.EXECUTION_FAILED
        else:
            states[binding.realization_id] = RealizationState.PLANNED

    def _executed_with_partial_dependency(dependent: str, prerequisite: str) -> bool:
        if states.get(prerequisite) is not RealizationState.PARTIALLY_SATISFIED:
            return False
        producer_binding = plan.binding_of(prerequisite)
        consumer_binding = plan.binding_of(dependent)
        if producer_binding is None or consumer_binding is None:
            return False
        producers = {
            node_id for node_id in producer_binding.bound_node_ids
            if node_id in by_node and by_node[node_id].partially_fulfilled
        }
        return any(
            node_id in by_node and by_node[node_id].succeeded
            and producers.intersection(by_node[node_id].node.depends_on)
            for node_id in consumer_binding.bound_node_ids
        )

    # A partial producer can supply valid data through an executed edge. The consumer's
    # own result contract still has to succeed; the producer's unmet work stays partial.
    # Blocking travels the realization DAG until it settles. I9: a dependent cannot SATISFY unless
    # every prerequisite realization did. A prerequisite counts as satisfied when a node actually
    # served its requirement -- the same served-truth rule the bindings use above.
    changed = True
    while changed:
        changed = False
        for realization in plan.realization_ledger.realizations:
            current = states.get(realization.realization_id)
            # Both are settled. Re-entering with INTEGRITY_FAILURE downgraded it to
            # DEPENDENCY_BLOCKED on the next pass, which turned the one state that must never ship
            # into an ordinary reportable failure.
            if current in {
                RealizationState.DEPENDENCY_BLOCKED,
                RealizationState.INTEGRITY_FAILURE,
            }:
                continue
            for prerequisite in realization.prerequisite_realization_ids:
                prerequisite_served = (
                    states.get(prerequisite) is RealizationState.SATISFIED
                    or _requirement_served(prerequisite)
                    or _executed_with_partial_dependency(realization.realization_id, prerequisite)
                )
                if not prerequisite_served:
                    if current is RealizationState.SATISFIED:
                        # A dependent that ANSWERED while its prerequisite did not. Not a failure of
                        # the work -- a failure of the accounting, and the one thing that must never
                        # be presented to a reader as a result.
                        states[realization.realization_id] = RealizationState.INTEGRITY_FAILURE
                        details[realization.realization_id] = (
                            f"answered while prerequisite {prerequisite} did not"
                        )
                    else:
                        states[realization.realization_id] = RealizationState.DEPENDENCY_BLOCKED
                    changed = True
                    break

    return RequirementOutcomeSet(
        outcomes=tuple(
            RealizationOutcome(
                realization=realization,
                state=states.get(realization.realization_id, RealizationState.UNBOUND),
                node_id=node_of.get(realization.realization_id, ""),
                detail=details.get(realization.realization_id, ""),
            )
            for realization in plan.realization_ledger.realizations
        )
    )


__all__ = [
    "ACCOUNTED_STATES",
    "BoundExecutionPlan",
    "CapabilityLookupResult",
    "CapabilityLookupState",
    "NonExecutionEvidence",
    "NonExecutionReason",
    "RealizationBinding",
    "RealizationLedger",
    "RealizationOutcome",
    "RealizationState",
    "RequirementOutcomeSet",
    "RequirementRealization",
    "WorkSpec",
    "bind_existing_nodes",
    "display_subject",
    "lookup_capability",
    "plan_realizations",
    "reduce_bound_plan",
    "resolve_capabilities",
]
