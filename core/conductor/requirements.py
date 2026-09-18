"""What the user REQUIRED, captured from a PROVEN semantic turn before anything resolves it.

Two architectural rules stack here, and the second was added because the first was not enough.

    REQUIREMENT EXISTENCE PRECEDES ENTITY RESOLUTION
    ROLE OWNERSHIP PRECEDES REQUIREMENT EXISTENCE

The first is why a resolver failing cannot delete a requirement: `Palladium` is captured as an
`asset_subject` holding the surface the user wrote, and whether Palladium can be priced is an
*answer about* that requirement rather than a precondition for it.

The second is newer and is what this module lost its regexes to. Existence still preceded
resolution, but ownership was decided downstream of four mechanisms that could not prove what they
were being asked: `core.turn_ir` classifying a fragment `UNKNOWN` and that being read as permission
to own it, an enumeration of coordinators deciding where a subject list continued, a `[^.;?!]+?`
tail deciding where one ended, and two regex vocabularies deciding polarity. Each fails toward
OWNING text. Together they produced a location called "open core/conductor/planner", a weather
requirement lifted out of a prohibition, and a subject list that ran to the end of whatever followed
the last "and".

All four are gone. Ownership now arrives as a validated `ProvenFrame` from
`core.conductor.semantic_proof`, whose surfaces this module does not compute -- it reads them off
the proof, which read them out of the one canonical text they were measured against. What no proof
path establishes is captured as nothing, and a message nobody could prove produces an empty ledger
that changes nothing downstream.

The three rules that keep the rest honest are unchanged:

* **Positive frames only.** A slot exists because a proof bound a semantic role to a span. There is
  no leftover-token rule and no unknown-noun rule.
* **Resolution is attachment.** `resolve` may bind a canonical identity to a slot or mark it
  `UNRESOLVED_SUBJECT`. It may not add a requirement, remove one, merge two, or change whether one
  is required.
* **The planner is not an authority for existence.** It maps clauses onto requirement ids it did
  not create. A planner that omits a requirement leaves the ledger's cardinality unchanged.

The requirement DAG is built here and is the ONLY semantic authority for requiredness. Node edges
are a projection of it, which is what makes `BTC` and `Bitcoin` one identity: the edge is to a
requirement id, and an alias is a surface inside a slot.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from core.conductor import frame_contracts as _frame_contracts
from core.conductor.frame_contracts import OBSERVATION_FAMILIES
from core.conductor.semantic_proof import (
    Abstention,
    Polarity,
    SemanticTurnProof,
    frame_contract,
    prove_turn,
)

#: Polarity is one concept with one spelling. This name is what the ledger's callers already say;
#: it is the semantic proof's own enum, not a second copy that could drift from it.
FramePolarity = Polarity

_ = _frame_contracts  # imported for its registration side effect; named so linters keep it


class SlotResolution(str, Enum):
    """Whether a slot's surface has been bound to something the runtime can act on."""

    #: Captured; nothing has tried to resolve it yet.
    PENDING = "pending"
    #: A resolver bound a canonical identity.
    RESOLVED = "resolved"
    #: A resolver looked and could not bind one. The slot, and its requirement, still exist.
    UNRESOLVED_SUBJECT = "unresolved_subject"
    #: The resolver itself raised. NOT the same as "could not be identified": one is a fact about
    #: the subject and the other is a fact about this runtime, and reporting the second as the first
    #: is how a crash in a realizer was shown to the reader as an unknown asset.
    RESOLVER_DEFECT = "resolver_defect"


class RequirementState(str, Enum):
    """A requirement's outcome. Every value is assigned by production code."""

    #: Projected into an execution node that has not finished.
    PLANNED = "planned"
    SATISFIED = "satisfied"
    #: A node ran and failed.
    EXECUTION_FAILED = "execution_failed"
    #: A required slot could not be resolved, so nothing was attempted.
    UNRESOLVED_SUBJECT = "unresolved_subject"
    #: Resolved, but a typed capability lookup said no registered capability serves it.
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    #: A policy or permission boundary refused it.
    POLICY_BLOCKED = "policy_blocked"
    #: A prerequisite did not reach SATISFIED, so this was never attempted.
    DEPENDENCY_BLOCKED = "dependency_blocked"


#: States a requirement may legitimately come to rest in, all of which a reader can be told.
TERMINAL_REQUIREMENT_STATES = frozenset(
    {
        RequirementState.SATISFIED,
        RequirementState.EXECUTION_FAILED,
        RequirementState.UNRESOLVED_SUBJECT,
        RequirementState.CAPABILITY_UNAVAILABLE,
        RequirementState.POLICY_BLOCKED,
        RequirementState.DEPENDENCY_BLOCKED,
    }
)


@dataclass(frozen=True)
class RequirementSlot:
    """One role-bound piece of a requirement, holding the user's own surface and offsets.

    No canonical entity at capture time, deliberately. The surface and the span are what the proof
    read out of the canonical text; a canonical identity is something a resolver may later attach,
    and a slot that demanded one before resolution could not represent `Palladium` at all.
    """

    slot_id: str
    role: str
    start: int
    end: int
    surface: str
    required: bool = True
    resolution: SlotResolution = SlotResolution.PENDING
    #: Which coordinated list this slot is a member of, and where in it. Asserted by the proof, so
    #: no separator between two members is ever consulted.
    coordination_group_id: str = ""
    member_ordinal: int = 0
    #: Attached by a resolver. Never read as identity for dependency purposes.
    canonical: str = ""
    #: Typed arguments a resolver produced for this slot, when it could.
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "role": self.role,
            "span": [self.start, self.end],
            "surface": self.surface,
            "required": self.required,
            "resolution": self.resolution.value,
            "canonical": self.canonical,
            "coordination_group_id": self.coordination_group_id,
            "member_ordinal": self.member_ordinal,
        }


@dataclass(frozen=True)
class UserRequirement:
    """One thing the user's message requires, independent of who will serve it."""

    requirement_id: str
    start: int
    end: int
    surface: str
    #: Which capability family this frame belongs to -- the operation name a projector will look
    #: for. A hint about HOW, never authority over WHETHER: an unregistered family still leaves the
    #: requirement in the ledger, coming to rest in CAPABILITY_UNAVAILABLE.
    family: str
    #: What kind of answer the user asked for, for the reader-facing sentence and the receipt.
    output_contract: str = ""
    slots: tuple[RequirementSlot, ...] = ()
    #: Requirement ids whose SATISFIED outcome this one needs. Identity is by id, so an alias in a
    #: slot surface can never split or duplicate a dependency.
    requires: tuple[str, ...] = ()
    #: Specific prerequisite SLOTS this frame named, when it named any. A requirement is one thing
    #: the user asked for and may cover several subjects -- "weather in Porto, Hobart and Oslo" is
    #: one requirement with three slots -- while a derived frame usually needs only some of them.
    #: Binding at slot granularity is what lets "how much warmer Porto is than Oslo" require two of
    #: those three without a third city's missing reading cancelling an answer the user can have.
    #: Empty means the whole requirement is required, which is the safe reading of an anaphor.
    input_bindings: tuple[str, ...] = ()
    #: Whether this frame ASSERTS its request, as PROVEN. Only AFFIRMED is executable; a NEGATED
    #: frame states work the user refused, and an UNRESOLVED one could not be established either
    #: way. Never defaulted from an absent value -- see `core.conductor.semantic_proof.Polarity`.
    polarity: Polarity = Polarity.AFFIRMED
    #: Which proof produced this: `formal_grammar` or `bounded_model`, and the frame id.
    provenance: str = ""

    @property
    def executable(self) -> bool:
        return self.polarity is Polarity.AFFIRMED

    @property
    def unresolved_required_slots(self) -> tuple[RequirementSlot, ...]:
        return tuple(
            slot
            for slot in self.slots
            if slot.required and slot.resolution is SlotResolution.UNRESOLVED_SUBJECT
        )

    @property
    def resolved_slots(self) -> tuple[RequirementSlot, ...]:
        return tuple(s for s in self.slots if s.resolution is SlotResolution.RESOLVED)

    def of_role(self, role: str) -> tuple[RequirementSlot, ...]:
        return tuple(s for s in self.slots if s.role == role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "span": [self.start, self.end],
            "surface": self.surface,
            "family": self.family,
            "output_contract": self.output_contract,
            "slots": [slot.to_dict() for slot in self.slots],
            "polarity": self.polarity.value,
            "requires": list(self.requires),
            "input_bindings": list(self.input_bindings),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class RequirementLedger:
    """The immutable record of what the user required. Never shrinks after capture.

    Every mutation returns a new ledger, so no downstream stage can quietly drop an entry.
    """

    request: str = ""
    requirements: tuple[UserRequirement, ...] = ()
    #: Spans no proven frame owns. Kept so unclaimed text stays VISIBLE without being attached to
    #: anything -- the alternative to representing it is letting it inherit into whatever frame
    #: happened to precede it, which is exactly how fabricated subjects were produced.
    unowned_spans: tuple[tuple[int, int], ...] = ()
    #: Regions a proof path considered and refused, with the rule that stopped each. An abstention
    #: is a decision the runtime made and must be able to show, not an absence.
    abstentions: tuple[Abstention, ...] = ()

    def __len__(self) -> int:
        return len(self.requirements)

    def by_id(self, requirement_id: str) -> UserRequirement | None:
        for requirement in self.requirements:
            if requirement.requirement_id == requirement_id:
                return requirement
        return None

    def of_family(self, family: str) -> tuple[UserRequirement, ...]:
        return tuple(r for r in self.requirements if r.family == family)

    @property
    def executable(self) -> tuple[UserRequirement, ...]:
        """Requirements an AFFIRMED frame established. The only ones anything may act on."""
        return tuple(r for r in self.requirements if r.executable)

    def unowned_text(self) -> tuple[str, ...]:
        return tuple(self.request[a:b].strip() for a, b in self.unowned_spans)

    def with_requirement(self, updated: UserRequirement) -> RequirementLedger:
        """Replace one entry. Refuses to add or remove, which is the ledger's whole guarantee."""
        if self.by_id(updated.requirement_id) is None:
            raise KeyError(
                f"{updated.requirement_id!r} is not in the ledger; resolution may attach to a "
                "requirement but never create one"
            )
        return replace(
            self,
            requirements=tuple(
                updated if r.requirement_id == updated.requirement_id else r
                for r in self.requirements
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "count": len(self.requirements),
            "requirements": [r.to_dict() for r in self.requirements],
            "unowned": list(self.unowned_text()),
            "abstentions": [a.to_dict() for a in self.abstentions],
        }


def bind_derived_inputs(ledger: RequirementLedger) -> RequirementLedger:
    """Narrow each derived requirement to the prerequisite SLOTS it actually names.

    Run AFTER resolution, so a slot is matched on its canonical identity as well as the surface the
    user wrote -- which is what makes a producer captured as "BTC" and a dependent written
    "Bitcoin" the same dependency. Alias resolution establishes that identity; spelling never
    decides whether the edge exists, because `requires` already holds every producer by id and this
    only chooses which of their slots matter.

    A derived frame naming none of them keeps all of them, which is the safe reading of an anaphor
    ("the difference between them"). Over-requiring is as wrong as under-requiring -- it refuses a
    Porto-vs-Oslo difference because an unrelated third city had no reading -- so the narrowing is
    worth having, but only where identity is positively established.
    """
    bound = ledger
    for requirement in ledger.requirements:
        if not requirement.requires:
            continue
        body = requirement.surface.casefold()
        slot_ids: list[str] = []
        for prerequisite_id in requirement.requires:
            prerequisite = ledger.by_id(prerequisite_id)
            if prerequisite is None:
                continue
            for slot in prerequisite.slots:
                names = {slot.surface.casefold(), slot.canonical.casefold()} - {""}
                if any(re.search(rf"\b{re.escape(name)}\b", body) for name in names):
                    slot_ids.append(slot.slot_id)
        if slot_ids:
            bound = bound.with_requirement(
                replace(requirement, input_bindings=tuple(slot_ids))
            )
    return bound


def arithmetic_core(text: str) -> str:
    """The arithmetic inside `text`, or `text` unchanged when it holds none.

    One definition, and it is the formal grammar's own: the expression a requirement captured and
    the clause a planner stored resolve to the same identity because both come through
    `formal_grammar_evidence`. A second regex here is how the two came to disagree before.
    """
    from core.semantic.canonical_text import CanonicalText

    body = str(text or "")
    if not body.strip():
        return body.strip()
    canonical = CanonicalText.of(body)
    from core.conductor.semantic_proof import formal_grammar_evidence

    for evidence in formal_grammar_evidence(canonical):
        if evidence.family == "calculation":
            return evidence.frame_scope.resolve(canonical).strip()
    return body.strip()


class _Counter:
    def __init__(self) -> None:
        self._n = 0

    def __iter__(self):
        return self

    def __next__(self) -> int:
        self._n += 1
        return self._n


def requirements_from_proof(proof: SemanticTurnProof) -> RequirementLedger:
    """One requirement per PROVEN frame, in the order the frames occur in the text.

    Nothing is computed from the text here beyond reading the proof: surfaces come from
    `ProvenRole.surface`, which the validator resolved out of the one canonical text the spans were
    measured against. This function has no regex, no separator, no vocabulary and no fallback --
    which is precisely why it cannot invent a subject.
    """
    counter = _Counter()
    captured: list[UserRequirement] = []
    for frame in proof.frames:
        contract = frame_contract(frame.family)
        if contract is None:
            # `validate_frame` already refuses an unknown family, so reaching here means the
            # contract was unregistered between validation and capture. Skip rather than guess.
            continue
        index = next(iter(counter))
        requirement_id = f"req:{index}:{frame.family}"
        slots = tuple(
            RequirementSlot(
                slot_id=f"{requirement_id}:slot:{position}",
                role=role.role_name,
                start=role.span.start,
                end=role.span.end,
                surface=role.surface,
                coordination_group_id=role.coordination_group_id,
                member_ordinal=role.member_ordinal,
            )
            for position, role in enumerate(frame.roles)
        )
        captured.append(
            UserRequirement(
                requirement_id=requirement_id,
                start=frame.scope.start,
                end=frame.scope.end,
                surface=frame.surface,
                family=frame.family,
                output_contract=contract.output_contract,
                slots=slots,
                polarity=frame.polarity,
                provenance=f"{frame.provenance.value}:{frame.frame_id}",
            )
        )

    # Derived edges. EVERY preceding executable observation requirement is a prerequisite, by id and
    # unconditionally -- no text comparison decides whether a dependency EXISTS. That was the F3
    # defect: a producer captured as "BTC" and a dependent written "ratio of Bitcoin to Gold" share
    # no spelling, so the BTC edge vanished and the ratio ran with one operand.
    #
    # Narrowing to specific SLOTS happens after resolution, in `bind_derived_inputs`, where
    # canonical identity exists. Narrowing can only ever reduce which slots of an already-required
    # requirement matter; it can never remove the requirement itself.
    linked: list[UserRequirement] = []
    for position, requirement in enumerate(captured):
        if requirement.family != "quantitative_reasoning":
            linked.append(requirement)
            continue
        producers = tuple(
            earlier.requirement_id
            for earlier in captured[:position]
            if earlier.family in OBSERVATION_FAMILIES and earlier.executable
        )
        linked.append(replace(requirement, requires=producers))
    return RequirementLedger(
        request=proof.canonical.text,
        requirements=tuple(linked),
        unowned_spans=tuple(proof.unclaimed_spans),
        abstentions=tuple(proof.abstentions),
    )


def capture_requirements(
    request: str,
    *,
    propose: Callable[[str, str], str] | None = None,
    proof: SemanticTurnProof | None = None,
) -> RequirementLedger:
    """Every requirement the turn PROVED, before any resolution.

    `propose` is the bounded semantic proposer -- the same injected model seam the planner already
    uses. Absent, only the closed formal grammars run, which is a smaller ledger and never a wrong
    one: an unproven role is captured as nothing and dispatches nothing.

    Consults no alias table, no gazetteer and no network on any path.
    """
    text = str(request or "")
    if not text.strip():
        return RequirementLedger()
    established = proof if proof is not None else prove_turn(text, propose=propose)
    return requirements_from_proof(established)


__all__ = [
    "OBSERVATION_FAMILIES",
    "TERMINAL_REQUIREMENT_STATES",
    "FramePolarity",
    "Polarity",
    "RequirementLedger",
    "RequirementSlot",
    "RequirementState",
    "SlotResolution",
    "UserRequirement",
    "arithmetic_core",
    "bind_derived_inputs",
    "capture_requirements",
    "requirements_from_proof",
]
