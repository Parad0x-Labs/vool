"""What the turn PROVED about its own semantic roles, before anything may act on one.

The thing this replaces, and why it had to go: role ownership used to be decided by asking
`core.turn_ir` to classify a fragment and treating `ClauseKind.UNKNOWN` as permission to own it. That
reads "no recognizer here had an opinion" as "this is a subject", which is the same shape as the
residual-tail rule it was itself brought in to replace -- an absence of evidence promoted to
evidence. Beside it sat an enumeration of coordinators deciding where a subject list continued, a
`[^.;?!]+?` tail deciding where one ended, and two regex vocabularies deciding polarity. Four
mechanisms, none of which can prove the thing it was being asked, and each of which fails toward
OWNING text rather than toward leaving it alone.

The replacement inverts the burden. Nothing is a role filler until something PROVES it is, and a
proof is a typed object that a deterministic validator either accepts whole or refuses whole:

    CanonicalText
      -> SemanticFrameEvidence      (proposed: by a formal grammar, or by a bounded model)
      -> validate_frame             (deterministic; the runtime, never the proposer)
      -> ProvenFrame                (surfaces resolved BY THE RUNTIME from its own text)
      -> requirements               (core.conductor.requirements)

Two proof paths, and the split is about what can actually be proven rather than about which is
cheaper:

* **FORMAL_GRAMMAR** -- closed syntax that establishes its own roles with no world knowledge:
  arithmetic, ISO-4217 conversion, explicit `key: value` fields. These abstain the moment the text
  leaves the declared syntax. They never reach for a noun.
* **BOUNDED_MODEL** -- everything open-ended. A model proposes typed evidence and nothing else: it
  never writes an argument, never names an operation's parameters, and never contributes a surface.
  It points at the user's own text. `validate_frame` then proves the pointing is sound, and a
  proposal that fails any rule abstains rather than being partially salvaged.

**No proof means no provider call.** ABSTAINED, UNCLAIMED and non-AFFIRMED material yields zero
slots and therefore zero provider arguments -- not "an argument the runtime is careful with", zero.
The failure direction is silence, which under-reports; the direction this replaced fabricated.

**There is no mandatory cloud dependency here.** The proposer is injected. Absent -- a local-only
lane with no proposer wired, a test, a turn whose model call failed -- the bounded path abstains and
the formal path still proves what it can. Abstention is a safe answer that costs a capability; it is
never a wrong answer that spends one.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.semantic.canonical_text import CanonicalText, EntitySpan, SpanBindingError


class Polarity(str, Enum):
    """Whether a frame ASSERTS its request. Frame-local, and never defaulted.

    A frame with no proven polarity is UNRESOLVED, not AFFIRMED. That asymmetry is the whole point:
    defaulting an unproven polarity to "yes" means every proposal that forgets the field buys a
    provider call, and the one thing a semantic proof must never do is convert an omission into an
    action.
    """

    AFFIRMED = "affirmed"
    NEGATED = "negated"
    UNRESOLVED = "unresolved"


class EvidenceStatus(str, Enum):
    """Whether the proposer is standing behind this frame at all."""

    PROVEN = "proven"
    #: The proposer looked and declined. Carried rather than dropped so a receipt can show that the
    #: runtime considered this region and chose not to own it.
    ABSTAINED = "abstained"


class ProofProvenance(str, Enum):
    FORMAL_GRAMMAR = "formal_grammar"
    BOUNDED_MODEL = "bounded_model"


class AbstentionReason(str, Enum):
    """Why a proposed frame was refused. Every refusal names one; none of them is a guess.

    These are the validator's entire vocabulary, and each is a rule a proposal can fail. Kept as an
    enum so a new rule is a visible diff and a test can assert the complete set.
    """

    PROPOSER_ABSTAINED = "proposer_abstained"
    UNKNOWN_FAMILY = "unknown_family"
    SPAN_NOT_BOUND = "span_not_bound"
    EMPTY_SPAN = "empty_span"
    SPAN_OUTSIDE_SCOPE = "span_outside_scope"
    UNSUPPORTED_ROLE = "unsupported_role"
    MISSING_REQUIRED_ROLE = "missing_required_role"
    COORDINATION_NOT_EXPLICIT = "coordination_not_explicit"
    POLARITY_NOT_PROVEN = "polarity_not_proven"
    INVENTED_SURFACE = "invented_surface"
    PREDICATE_AS_ROLE = "predicate_as_role"
    ROLE_NOT_COORDINATED = "role_not_coordinated"
    MALFORMED_PROPOSAL = "malformed_proposal"
    #: Two fillers of one frame intersect. They cannot both be distinct members, and which one the
    #: frame meant is not established -- so neither is owned.
    ROLE_SPANS_OVERLAP = "role_spans_overlap"
    #: Two proven frames claim overlapping scope. Two readings of one region is not two requests.
    CONFLICTING_FRAMES = "conflicting_frames"


# --- frame contracts -------------------------------------------------------------------------
#
# The semantic vocabulary of a family: which roles it HAS. Deliberately separate from
# `core.conductor.registry`, which owns whether a family can be EXECUTED. A family may be
# semantically well-formed and have no operation behind it -- that is `fx_quote` today -- and the
# two answers must not be able to overwrite one another. Collapsing them is how "no operation is
# registered" came to look like "the user did not ask for this".


@dataclass(frozen=True)
class FrameContract:
    """What a family's frame is made of. One source, read by capture, projection and rendering."""

    family: str
    #: Every role name this family may bind. A proposal naming anything else is refused.
    roles: tuple[str, ...]
    #: Roles without which the frame is not established at all.
    required_roles: tuple[str, ...] = ()
    #: The single role that may carry several coordinated members, and along which realizations fan
    #: out. Empty means the frame is one indivisible piece of work whatever its roles.
    fan_out_role: str = ""
    #: Roles, in order, that name the thing a READER recognises. "EUR to JPY", never the amount.
    display_roles: tuple[str, ...] = ()
    #: How several display roles read as one phrase.
    display_join: str = " "
    output_contract: str = ""
    #: Whether a CLOSED FORMAL GRAMMAR may assert this family's polarity on its own.
    #:
    #: False by default, and the default is the safe one. A closed grammar reads its own syntax and
    #: nothing around it, so it cannot see a prohibition written in prose -- "never convert EUR to
    #: JPY" is, to the FX grammar, an ISO pair with a directional operator between them. Where the
    #: work is OUTWARD-FACING that mistake spends a network call on something the user forbade, so
    #: those families get UNRESOLVED from the formal path and become executable only when a bounded
    #: semantic proof affirms them.
    #:
    #: True only where the work reaches no network and no provider, and its worst case is a local
    #: arithmetic evaluation nobody asked for. That distinction is the family's to declare, not the
    #: grammar's to infer.
    formal_proof_may_affirm: bool = False

    def formal_polarity(self, proposed: Polarity) -> Polarity:
        """The polarity a FORMAL frame of this family may actually claim.

        Downgrades to UNRESOLVED unless the family declares that closed syntax is sufficient
        authority. UNRESOLVED is non-executable and stays visible on the ledger, which is the
        difference between "we will not act on this" and "the user did not ask for it".
        """
        if proposed is Polarity.AFFIRMED and not self.formal_proof_may_affirm:
            return Polarity.UNRESOLVED
        return proposed

    @property
    def resolves_per_role(self) -> bool:
        """Whether a resolver binds each subject separately, or the whole frame binds at once."""
        return bool(self.fan_out_role)


_FRAME_CONTRACTS: dict[str, FrameContract] = {}


def register_frame_contract(contract: FrameContract, *, replace: bool = False) -> None:
    name = str(contract.family or "").strip()
    if not name:
        raise ValueError("a frame contract must name a family")
    if name in _FRAME_CONTRACTS and not replace:
        raise ValueError(f"frame contract {name!r} is already registered")
    if contract.fan_out_role and contract.fan_out_role not in contract.roles:
        raise ValueError(
            f"{name!r} declares fan_out_role {contract.fan_out_role!r} which is not one of its roles"
        )
    for role in (*contract.required_roles, *contract.display_roles):
        if role not in contract.roles:
            raise ValueError(f"{name!r} declares role {role!r} which is not one of its roles")
    _FRAME_CONTRACTS[name] = contract


def frame_contract(family: str) -> FrameContract | None:
    return _FRAME_CONTRACTS.get(str(family or "").strip())


def known_frame_contracts() -> tuple[FrameContract, ...]:
    return tuple(_FRAME_CONTRACTS[name] for name in sorted(_FRAME_CONTRACTS))


def unregister_frame_contract(family: str) -> None:
    """Exists for tests and for the mutation that proves the contract registry is load-bearing."""
    _FRAME_CONTRACTS.pop(str(family or "").strip(), None)


# --- evidence --------------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticRoleEvidence:
    """One role filler, proposed. Coordination membership is EXPLICIT, never inferred.

    `coordination_group_id` and `member_ordinal` exist so that "Oslo and Tromso" and "Oslo btw
    Tromso" and "Oslo/Tromso" are the same two members. Which separator sits between them is not
    consulted anywhere in this module: membership is asserted by the proposer and proven by the
    validator, so no enumeration of coordinators can be wrong about a separator it never met.
    """

    role_name: str
    value_span: EntitySpan
    coordination_group_id: str
    member_ordinal: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role_name,
            "span": [self.value_span.start, self.value_span.end],
            "group": self.coordination_group_id,
            "ordinal": self.member_ordinal,
        }


@dataclass(frozen=True)
class SemanticFrameEvidence:
    """One proposed frame. Nothing here is trusted; `validate_frame` decides what survives."""

    frame_id: str
    frame_scope: EntitySpan
    predicate_span: EntitySpan
    family: str
    polarity: Polarity
    roles: tuple[SemanticRoleEvidence, ...] = ()
    status: EvidenceStatus = EvidenceStatus.PROVEN
    provenance: ProofProvenance = ProofProvenance.BOUNDED_MODEL

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "family": self.family,
            "scope": [self.frame_scope.start, self.frame_scope.end],
            "predicate": [self.predicate_span.start, self.predicate_span.end],
            "polarity": self.polarity.value,
            "status": self.status.value,
            "provenance": self.provenance.value,
            "roles": [role.to_dict() for role in self.roles],
        }


@dataclass(frozen=True)
class ProvenRole:
    """A role the runtime has proven, carrying the surface IT read out of its own text."""

    role_name: str
    span: EntitySpan
    surface: str
    coordination_group_id: str
    member_ordinal: int


@dataclass(frozen=True)
class ProvenFrame:
    """A frame that survived every rule. The only thing capture is allowed to read."""

    frame_id: str
    family: str
    polarity: Polarity
    provenance: ProofProvenance
    scope: EntitySpan
    predicate: EntitySpan
    surface: str
    roles: tuple[ProvenRole, ...] = ()

    @property
    def executable(self) -> bool:
        return self.polarity is Polarity.AFFIRMED

    def of_role(self, role_name: str) -> tuple[ProvenRole, ...]:
        return tuple(r for r in self.roles if r.role_name == role_name)


@dataclass(frozen=True)
class Abstention:
    """A region the runtime considered and did not own, with the rule that stopped it."""

    frame_id: str
    family: str
    reason: AbstentionReason
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "family": self.family,
            "reason": self.reason.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SemanticTurnProof:
    """Everything the turn proved about itself, and everything it declined to."""

    canonical: CanonicalText
    frames: tuple[ProvenFrame, ...] = ()
    abstentions: tuple[Abstention, ...] = ()
    #: Regions no proven frame owns. Visible, and attached to nothing.
    unclaimed_spans: tuple[tuple[int, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical.to_dict(),
            "frames": [
                {
                    "frame_id": f.frame_id,
                    "family": f.family,
                    "polarity": f.polarity.value,
                    "provenance": f.provenance.value,
                    "surface": f.surface,
                    "roles": [
                        {"role": r.role_name, "surface": r.surface, "ordinal": r.member_ordinal}
                        for r in f.roles
                    ],
                }
                for f in self.frames
            ],
            "abstentions": [a.to_dict() for a in self.abstentions],
        }


# --- validation ------------------------------------------------------------------------------


def _resolve(span: EntitySpan, canonical: CanonicalText) -> str | None:
    """The substring, or None when the span does not belong to exactly this text."""
    try:
        return span.resolve(canonical)
    except SpanBindingError:
        return None


def validate_frame(
    evidence: SemanticFrameEvidence, canonical: CanonicalText
) -> ProvenFrame | Abstention:
    """Prove `evidence` against `canonical`, or refuse it with a named reason.

    Whole or nothing. There is no partial acceptance and no repair: a frame with one bad role is a
    proposal the runtime could not verify, and salvaging the good half of an unverifiable proposal
    is how a proposer's mistake becomes a provider call.

    Every surface on the returned `ProvenFrame` is read out of `canonical` HERE. Nothing the
    proposer wrote reaches a requirement -- which is what makes "no invented surface" structural
    rather than a check that could be forgotten.
    """
    frame_id = str(evidence.frame_id or "")
    family = str(evidence.family or "")

    if evidence.status is not EvidenceStatus.PROVEN:
        return Abstention(frame_id, family, AbstentionReason.PROPOSER_ABSTAINED)

    contract = frame_contract(family)
    if contract is None:
        return Abstention(frame_id, family, AbstentionReason.UNKNOWN_FAMILY, family)

    # Polarity is proven or the frame is not executable. There is no branch here that supplies a
    # value: an evidence object whose polarity is not a `Polarity` member never becomes a frame.
    if not isinstance(evidence.polarity, Polarity):
        return Abstention(
            frame_id, family, AbstentionReason.POLARITY_NOT_PROVEN, repr(evidence.polarity)
        )
    polarity = evidence.polarity
    if evidence.provenance is ProofProvenance.FORMAL_GRAMMAR:
        # A closed grammar's affirmation is only as good as what it can see, and it cannot see the
        # sentence around itself. The family says whether that is enough.
        polarity = contract.formal_polarity(polarity)

    scope_text = _resolve(evidence.frame_scope, canonical)
    if scope_text is None:
        return Abstention(frame_id, family, AbstentionReason.SPAN_NOT_BOUND, "frame_scope")
    if not scope_text.strip():
        return Abstention(frame_id, family, AbstentionReason.EMPTY_SPAN, "frame_scope")

    predicate_text = _resolve(evidence.predicate_span, canonical)
    if predicate_text is None:
        return Abstention(frame_id, family, AbstentionReason.SPAN_NOT_BOUND, "predicate")
    if not predicate_text.strip():
        return Abstention(frame_id, family, AbstentionReason.EMPTY_SPAN, "predicate")
    if not _within(evidence.predicate_span, evidence.frame_scope):
        return Abstention(frame_id, family, AbstentionReason.SPAN_OUTSIDE_SCOPE, "predicate")

    proven: list[ProvenRole] = []
    groups: dict[tuple[str, str], list[int]] = {}
    for role in evidence.roles:
        role_name = str(role.role_name or "")
        if role_name not in contract.roles:
            return Abstention(frame_id, family, AbstentionReason.UNSUPPORTED_ROLE, role_name)
        surface = _resolve(role.value_span, canonical)
        if surface is None:
            return Abstention(frame_id, family, AbstentionReason.SPAN_NOT_BOUND, role_name)
        if not surface.strip():
            return Abstention(frame_id, family, AbstentionReason.EMPTY_SPAN, role_name)
        if not _within(role.value_span, evidence.frame_scope):
            return Abstention(frame_id, family, AbstentionReason.SPAN_OUTSIDE_SCOPE, role_name)
        if (role.value_span.start, role.value_span.end) == (
            evidence.predicate_span.start,
            evidence.predicate_span.end,
        ):
            # A frame whose role filler is exactly its own predicate has established nothing. This
            # is the one thing the deleted verb list was actually right about, expressed without a
            # vocabulary: the predicate is whatever the proposer pointed at as the predicate.
            return Abstention(frame_id, family, AbstentionReason.PREDICATE_AS_ROLE, role_name)
        # A label is optional readability. When present it is a CLAIM about the text, and a claim
        # that does not match the text is a proposer inventing a surface -- refused, never repaired.
        label = str(role.value_span.label or "")
        if label and canonical.normalize(label) != surface:
            return Abstention(
                frame_id, family, AbstentionReason.INVENTED_SURFACE, f"{role_name}:{label}"
            )
        group = str(role.coordination_group_id or "")
        if not group:
            return Abstention(
                frame_id, family, AbstentionReason.COORDINATION_NOT_EXPLICIT, role_name
            )
        ordinal = int(role.member_ordinal)
        if ordinal < 0:
            return Abstention(
                frame_id, family, AbstentionReason.COORDINATION_NOT_EXPLICIT, role_name
            )
        bucket = groups.setdefault((role_name, group), [])
        if ordinal in bucket:
            return Abstention(
                frame_id,
                family,
                AbstentionReason.COORDINATION_NOT_EXPLICIT,
                f"{role_name}:duplicate ordinal {ordinal}",
            )
        bucket.append(ordinal)
        proven.append(
            ProvenRole(
                role_name=role_name,
                span=role.value_span,
                surface=surface,
                coordination_group_id=group,
                member_ordinal=ordinal,
            )
        )

    # No two fillers of one frame may overlap. A span that CONTAINS another member's span is the
    # swallow case -- "Oslo and Tromso" proposed as one location beside "Oslo" as another -- and it
    # is caught here as geometry, without any notion of what a coordinator is. Two fillers that
    # intersect at all are not two distinct fillers, and choosing which one the frame meant is a
    # guess the runtime is not entitled to make.
    for index, first in enumerate(proven):
        for second in proven[index + 1 :]:
            if first.span.overlaps(second.span):
                return Abstention(
                    frame_id,
                    family,
                    AbstentionReason.ROLE_SPANS_OVERLAP,
                    f"{first.role_name}[{first.span.start},{first.span.end}) overlaps "
                    f"{second.role_name}[{second.span.start},{second.span.end})",
                )

    for (role_name, group), ordinals in groups.items():
        if sorted(ordinals) != list(range(len(ordinals))):
            # Members number 0..n-1 or the list has a hole, and a hole means a member the proposer
            # believed in and did not describe. Guessing which one is missing is not available.
            return Abstention(
                frame_id,
                family,
                AbstentionReason.COORDINATION_NOT_EXPLICIT,
                f"{role_name}:{group}:{sorted(ordinals)}",
            )
        if len(ordinals) > 1 and role_name != contract.fan_out_role:
            # Only the declared fan-out role may hold several members. A second `base_currency` is
            # not a longer list, it is a frame the contract does not describe.
            return Abstention(
                frame_id, family, AbstentionReason.ROLE_NOT_COORDINATED, role_name
            )

    present = {role.role_name for role in proven}
    missing = [role for role in contract.required_roles if role not in present]
    if missing:
        return Abstention(
            frame_id, family, AbstentionReason.MISSING_REQUIRED_ROLE, ",".join(missing)
        )

    proven.sort(key=lambda r: (contract.roles.index(r.role_name), r.member_ordinal))
    return ProvenFrame(
        frame_id=frame_id,
        family=family,
        polarity=polarity,
        provenance=evidence.provenance,
        scope=evidence.frame_scope,
        predicate=evidence.predicate_span,
        surface=scope_text.strip(),
        roles=tuple(proven),
    )


def _within(inner: EntitySpan, outer: EntitySpan) -> bool:
    return outer.start <= inner.start and inner.end <= outer.end


def validate_proof(
    canonical: CanonicalText, evidence: Sequence[SemanticFrameEvidence]
) -> SemanticTurnProof:
    """Every proposal validated independently, and what is left over recorded as unclaimed."""
    frames: list[ProvenFrame] = []
    abstentions: list[Abstention] = []
    for item in evidence:
        outcome = validate_frame(item, canonical)
        if isinstance(outcome, ProvenFrame):
            frames.append(outcome)
        else:
            abstentions.append(outcome)
    frames.sort(key=lambda f: (f.scope.start, f.frame_id))
    surviving, conflicts = _arbitrate(frames)
    return SemanticTurnProof(
        canonical=canonical,
        frames=tuple(surviving),
        abstentions=(*abstentions, *conflicts),
        unclaimed_spans=_unclaimed(canonical, surviving),
    )


def _arbitrate(
    frames: Sequence[ProvenFrame],
) -> tuple[list[ProvenFrame], list[Abstention]]:
    """Resolve frames whose SCOPES overlap. Two readings of one region is not two requests.

    Exactly one overlap is legitimate, and it is the one this architecture creates on purpose: a
    closed formal grammar and a bounded model both describing the same span. There the bounded
    reading WINS whenever it is not a plain affirmation, because it is the only one of the two that
    can see the prose around the syntax:

        "never convert EUR to JPY"

    is, to the formal grammar, an ISO pair with a directional operator between them -- correct as
    far as it can see, and a live currency call as far as the user is concerned. A formal AFFIRMED
    frame may therefore never replace an overlapping semantic NEGATED or UNRESOLVED one. It may
    replace an overlapping semantic AFFIRMED one, where the two agree and the deterministic reading
    is the more precise.

    Every other overlap is a genuine conflict and BOTH frames abstain. Choosing between two
    same-provenance readings of one region would be the runtime guessing which request the user
    made, which is the thing the whole proof boundary exists to stop.
    """
    dropped: dict[str, Abstention] = {}
    for index, first in enumerate(frames):
        for second in frames[index + 1 :]:
            if not first.scope.overlaps(second.scope):
                continue
            loser = _formal_yields_to(first, second)
            if loser is not None:
                dropped.setdefault(
                    loser.frame_id,
                    Abstention(
                        loser.frame_id,
                        loser.family,
                        AbstentionReason.CONFLICTING_FRAMES,
                        "formal and bounded readings of one region; the more authoritative kept",
                    ),
                )
                continue
            detail = f"{first.frame_id} and {second.frame_id} claim overlapping scope"
            for frame in (first, second):
                dropped.setdefault(
                    frame.frame_id,
                    Abstention(
                        frame.frame_id, frame.family, AbstentionReason.CONFLICTING_FRAMES, detail
                    ),
                )
    return (
        [frame for frame in frames if frame.frame_id not in dropped],
        list(dropped.values()),
    )


def _formal_yields_to(first: ProvenFrame, second: ProvenFrame) -> ProvenFrame | None:
    """The loser when a formal and a bounded reading overlap, or None when neither pairing applies.

    Three orderings, and each exists because getting it wrong loses something specific:

    * **bounded NOT AFFIRMED beats everything.** The bounded reader is the only one that can see
      "never" in the prose around the syntax, and its refusal must survive. Losing this authorizes
      a live conversion the user forbade.
    * **formal AFFIRMED beats bounded AFFIRMED.** They agree, and an expression parsed by a grammar
      is more precise than the same expression pointed at by a model. Losing this quietly demotes
      every closed-syntax proof the model happened to also mention.
    * **bounded AFFIRMED beats formal UNRESOLVED.** A family whose contract forbids closed syntax
      from affirming it -- `fx_quote` -- produces a formal frame that is deliberately not
      executable. Keeping THAT over a semantic affirmation would mean the deferral the contract asks
      for ("abstain and defer to bounded semantic proof") deferred into a dead end, and no
      conversion could ever run.

    Stated once rather than three times: the survivor is whichever frame has the stronger claim to
    decide this region, and a non-affirmation always outranks an affirmation.
    """
    formal, bounded = (first, second)
    if formal.provenance is not ProofProvenance.FORMAL_GRAMMAR or (
        bounded.provenance is not ProofProvenance.BOUNDED_MODEL
    ):
        formal, bounded = (second, first)
    if formal.provenance is not ProofProvenance.FORMAL_GRAMMAR or (
        bounded.provenance is not ProofProvenance.BOUNDED_MODEL
    ):
        return None
    if bounded.polarity is not Polarity.AFFIRMED:
        return formal
    if formal.polarity is Polarity.AFFIRMED:
        return bounded
    return formal


def _unclaimed(
    canonical: CanonicalText, frames: Sequence[ProvenFrame]
) -> tuple[tuple[int, int], ...]:
    """Regions of the text no proven frame covers, merged and trimmed of whitespace."""
    covered = sorted((f.scope.start, f.scope.end) for f in frames)
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for start, end in covered:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < canonical.length:
        gaps.append((cursor, canonical.length))
    trimmed: list[tuple[int, int]] = []
    for start, end in gaps:
        chunk = canonical.text[start:end]
        lead = len(chunk) - len(chunk.lstrip())
        tail = len(chunk) - len(chunk.rstrip())
        if start + lead < end - tail:
            trimmed.append((start + lead, end - tail))
    return tuple(trimmed)


# --- proof path 1: strict formal deterministic grammar ----------------------------------------
#
# Every recognizer below matches CLOSED SYNTAX and abstains outside it. None consults a noun, an
# alias table, a gazetteer or a coin index, and none has an opinion about where a natural-language
# list ends -- because none of them ever sees one. What they cannot prove they do not propose.

#: An arithmetic operator, written as a symbol or as the one-word English name of that symbol. The
#: names are part of the CLOSED SYNTAX of written arithmetic, not a vocabulary of request verbs:
#: each maps to exactly one operator and to nothing else.
_ARITHMETIC_OPERATOR = r"(?:[x×*/÷+]|(?<![A-Za-z])-(?![A-Za-z])|times|divided\s+by|plus|minus)"
_ARITHMETIC_RE = re.compile(
    rf"(?P<lhs>\d[\d,.]*)\s*(?P<operator>{_ARITHMETIC_OPERATOR})\s*(?P<rhs>\d[\d,.]*)",
    re.IGNORECASE,
)

#: ISO-4217 is a formal standard, and MEMBERSHIP of it is what makes this closed syntax rather than
#: "three upper-case letters with a preposition between them". That looser reading is what shipped,
#: and it authorized network work on file formats:
#:
#:     "convert this CSV to XML for me"   -> fx_quote CSV/XML
#:     "convert JPG to PNG"               -> fx_quote JPG/PNG
#:
#: Both are well-formed under a shape test and neither is a currency. The membership check moved
#: here, to `core.currency_intent.ISO_4217` -- the repository's own authoritative currency-domain
#: contract, not a list written out beside it.
#:
#: This narrows unknown-slot survival for FX on purpose. "XYZ to ABC" is no longer a formal frame,
#: because a grammar that cannot tell a currency from a file extension has not proven a conversion.
#: An unknown genuine currency is exactly the case the BOUNDED path exists to own.
#: Case-insensitive on purpose, with the homograph cost paid below rather than by refusing the
#: lower case outright. "convert 100 usd to rub" is exactly as formal as its upper-case twin, and
#: the upper-case-only reading silently unproved it -- the fx clause then died on the clause-kind
#: gate ("does not serve transform requests") because no proven frame existed to outrank the verb
#: reading. Lower-case admission is delegated to `fx_conversion_intent`, the currency contract's
#: own evidence-bearing recognizer, so "move 3 try to top of the list" (try/top are ISO homographs)
#: still proves nothing.
_CONVERSION_RE = re.compile(
    r"(?:(?P<amount>\d[\d,.]*)\s*)?"
    r"\b(?P<base>[A-Za-z]{3})\b\s*(?P<operator>to|into|->|→)\s*\b(?P<quote>[A-Za-z]{3})\b"
)


def _is_iso_currency(code: str) -> bool:
    """Membership of ISO 4217, from the repository's own currency contract."""
    from core.currency_intent import ISO_4217

    return str(code or "").upper() in ISO_4217


def _lowercase_pair_proven_by_currency_contract(text: str, base: str, quote: str) -> bool:
    """Whether the currency contract's own recognizer reads this exact pair out of `text`.

    Fail-toward-abstain: an unavailable or raising recognizer leaves the region to the bounded
    path, exactly as an unmatched grammar would.
    """
    try:
        from core.currency_intent import fx_conversion_intent, fx_rate_lookup_intent

        request = fx_conversion_intent(text) or fx_rate_lookup_intent(text)
    except Exception:
        return False
    if request is None:
        return False
    if hasattr(request, "source"):
        pair = {request.source.code, request.target.code}
    else:
        pair = {request.base.code, request.quote.code}
    return pair == {str(base).upper(), str(quote).upper()}

#: `key: value` and `key = value`, alone on one line. Explicit structure the user typed, not prose.
#:
#: Two tightenings, each found by a test that the looser form failed:
#:
#: * The key holds NO SPACES. It used to allow up to 40 characters of them, which made every prose
#:   sentence containing a colon an "explicit structured field" -- "Give me the weather in Oslo:
#:   Tromso" was claimed here, and the bounded proposal covering the same region was then dropped as
#:   a duplicate, so a two-member list became a field and lost a subject. A field name is a token.
#: * The value may not open `//`. That is the RFC-3986 `scheme://` production, so `https://x` was
#:   read as the field `https` with the value `//x`. Excluding it is a syntactic exclusion of a
#:   different closed syntax, not a vocabulary: no URL host, scheme or extension is named anywhere.
_STRUCTURED_FIELD_RE = re.compile(
    r"^[ \t]*(?P<key>[A-Za-z][A-Za-z0-9_.-]{0,40})[ \t]*(?P<operator>[:=])[ \t]*"
    r"(?!//)(?P<value>\S[^\n]*?)[ \t]*$",
    re.MULTILINE,
)


def formal_grammar_evidence(canonical: CanonicalText) -> tuple[SemanticFrameEvidence, ...]:
    """Frames the closed grammars prove. Empty is the normal answer for ordinary prose.

    Polarity is AFFIRMED for what these match, and the boundary is worth stating rather than
    hiding: a closed grammar reads its own syntax and nothing around it, so it cannot see a
    prohibition expressed in prose. Prose polarity belongs to the bounded path, which is where the
    deleted `never|without|avoid` vocabulary went -- not into a shorter list here. What this costs
    is bounded and local: the families with closed syntax are arithmetic, conversion and explicit
    fields, none of which reaches a network.
    """
    text = canonical.text
    evidence: list[SemanticFrameEvidence] = []
    claimed: list[tuple[int, int]] = []
    counter = 0

    for match in _CONVERSION_RE.finditer(text):
        if not (
            _is_iso_currency(match.group("base")) and _is_iso_currency(match.group("quote"))
        ):
            # Not a conversion this grammar can prove. ABSTAIN and leave the region to the bounded
            # path, which is what "outside the declared syntax" has to mean in code.
            continue
        if not (match.group("base").isupper() and match.group("quote").isupper()):
            # A lower/mixed-case pair is ISO-shaped but homograph-risky ("try", "top", "rub",
            # "all" are English words). Admission is the currency contract's own call: the frame
            # is proven only when `fx_conversion_intent` -- which weighs quantity and settledness
            # evidence -- reads THE SAME pair out of the text. One authority for the hard cases,
            # not a second, weaker copy of its rules here.
            if not _lowercase_pair_proven_by_currency_contract(
                text, match.group("base"), match.group("quote")
            ):
                continue
        start, end = match.span()
        counter += 1
        roles = [
            _role(canonical, "base_currency", *match.span("base"), f"cur{counter}"),
            _role(canonical, "quote_currency", *match.span("quote"), f"cur{counter}"),
        ]
        if match.group("amount"):
            roles.append(_role(canonical, "amount", *match.span("amount"), f"amt{counter}"))
        evidence.append(
            SemanticFrameEvidence(
                frame_id=f"formal:{counter}:fx_quote",
                frame_scope=canonical.span(start, end),
                predicate_span=canonical.span(*match.span("operator")),
                family="fx_quote",
                polarity=Polarity.AFFIRMED,
                roles=tuple(roles),
                provenance=ProofProvenance.FORMAL_GRAMMAR,
            )
        )
        claimed.append((start, end))

    for match in _ARITHMETIC_RE.finditer(text):
        start, end = match.span()
        if any(start < c_end and c_start < end for c_start, c_end in claimed):
            continue
        counter += 1
        evidence.append(
            SemanticFrameEvidence(
                frame_id=f"formal:{counter}:calculation",
                frame_scope=canonical.span(start, end),
                predicate_span=canonical.span(*match.span("operator")),
                family="calculation",
                polarity=Polarity.AFFIRMED,
                roles=(_role(canonical, "expression", start, end, f"expr{counter}"),),
                provenance=ProofProvenance.FORMAL_GRAMMAR,
            )
        )
        claimed.append((start, end))

    for match in _STRUCTURED_FIELD_RE.finditer(text):
        start, end = match.span()
        if any(start < c_end and c_start < end for c_start, c_end in claimed):
            continue
        counter += 1
        evidence.append(
            SemanticFrameEvidence(
                frame_id=f"formal:{counter}:structured_field",
                frame_scope=canonical.span(start, end),
                predicate_span=canonical.span(*match.span("operator")),
                family="structured_field",
                polarity=Polarity.AFFIRMED,
                roles=(
                    _role(canonical, "field_name", *match.span("key"), f"fld{counter}"),
                    _role(canonical, "field_value", *match.span("value"), f"fld{counter}"),
                ),
                provenance=ProofProvenance.FORMAL_GRAMMAR,
            )
        )
        claimed.append((start, end))
    return tuple(evidence)


def _role(
    canonical: CanonicalText, name: str, start: int, end: int, group: str, ordinal: int = 0
) -> SemanticRoleEvidence:
    return SemanticRoleEvidence(
        role_name=name,
        value_span=canonical.span(start, end),
        coordination_group_id=group,
        member_ordinal=ordinal,
    )


# --- proof path 2: bounded semantic model proof -----------------------------------------------
#
# The model's entire job is to POINT. It selects a family from a closed list this runtime owns,
# points at the predicate, points at each role filler, states the coordination membership, and
# states the polarity. It writes no arguments, invents no surfaces, and names no operation
# parameters. Everything it returns is re-derived from the canonical text before anything uses it,
# so the worst a bad proposal can do is abstain.

_PROPOSAL_SYSTEM_PROMPT = """\
You label the semantic structure of one message. You do NOT answer it and you do NOT act on it.

Return ONLY a JSON object:
{"frames": [
  {"frame_id": "f1",
   "family": "<one of the families listed below>",
   "scope": "<the exact substring of the message this request occupies>",
   "predicate": "<the exact substring naming the action or the thing asked for; may be omitted>",
   "polarity": "affirmed" | "negated" | "unresolved",
   "roles": [{"role": "<a role of that family>",
              "text": "<the exact substring filling that role>",
              "group": "<same id for members of one coordinated list>",
              "ordinal": 0}]}]}

Rules:
- Every "scope", "predicate" and "text" MUST be copied character for character from the message.
  Never paraphrase, never correct spelling, never expand an abbreviation, never translate.
- "polarity" is one of exactly three, and most frames in an ordinary message are "affirmed":
    "affirmed"   - the message asks for this. Plain requests, questions and commands are affirmed.
    "negated"    - the message says this must NOT be done.
    "unresolved" - ONLY when doing it depends on a condition that has not been settled.
  Never use "unresolved" merely because you are unsure; leave the frame out instead.
- Coordinated members of one list share a "group" and count 0, 1, 2 ... in "ordinal".
- Text that is not part of any request you are sure of: leave it out. Omitting is always correct.
- If you are not sure of a frame, omit that frame. Return {"frames": []} rather than guess.

Families and their roles:
%s
"""


def _catalog_line(contract: FrameContract) -> str:
    """One family, described by what it ANSWERS and what it binds.

    The roles-only rendering this replaces emitted `- quantitative_reasoning: roles ` -- a family
    with no ordinary subject roles described by an empty list, which is not a choice a reader can
    make. Measured live on qwen2.5:7b: the model never selected it, labelling "work out the ratio
    between them" as a self-contained `calculation` instead, which loses the prerequisite edges that
    make a derived figure wait for its operands. A family the runtime renders unreadable is a VOOL
    defect, not a model limitation (CLAUDE.md 50).

    Generated from the contract, not written out beside it, so a newly registered family is offered
    without anyone remembering to edit a prompt.
    """
    if not contract.roles:
        return (
            f"- {contract.family}: NO roles -- use this for a request that computes over the "
            f"results of the other frames rather than naming its own subject "
            f"({contract.output_contract}). Give it a scope and a predicate and an empty role list."
        )
    roles = ", ".join(contract.roles)
    listed = f" ({contract.fan_out_role} may list several)" if contract.fan_out_role else ""
    return f"- {contract.family}: {contract.output_contract}. Roles: {roles}{listed}"


def proposal_prompt(catalog: str = "") -> str:
    """The bounded proposer's system prompt, generated from the registered contracts."""
    lines = catalog or "\n".join(
        _catalog_line(contract) for contract in known_frame_contracts()
    )
    return _PROPOSAL_SYSTEM_PROMPT % lines


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_proposal(
    raw: str, canonical: CanonicalText
) -> tuple[tuple[SemanticFrameEvidence, ...], tuple[Abstention, ...]]:
    """Turn a proposer's reply into typed evidence, locating every surface in the real text.

    A proposer may point with offsets or with the substring itself. The substring form is not a
    relaxation: the runtime SEARCHES for it inside the frame's own scope and refuses the frame when
    it is not there, so a surface the model invented, paraphrased or corrected cannot resolve and
    cannot become a role. What it buys is that a weaker model does not have to count code points to
    be useful -- which is a VOOL problem, not a model one, and making a step needlessly hard is the
    runtime's defect rather than the model's limit.
    """
    body = str(raw or "").strip()
    if not body:
        return (), (Abstention("", "", AbstentionReason.MALFORMED_PROPOSAL, "empty reply"),)
    match = _JSON_OBJECT_RE.search(body)
    if match is None:
        return (), (Abstention("", "", AbstentionReason.MALFORMED_PROPOSAL, "no JSON object"),)
    try:
        payload = json.loads(match.group(0))
    except (TypeError, ValueError) as exc:
        return (), (Abstention("", "", AbstentionReason.MALFORMED_PROPOSAL, str(exc)),)
    if not isinstance(payload, dict):
        return (), (Abstention("", "", AbstentionReason.MALFORMED_PROPOSAL, "not an object"),)

    evidence: list[SemanticFrameEvidence] = []
    abstentions: list[Abstention] = []
    for position, item in enumerate(list(payload.get("frames") or [])):
        if not isinstance(item, dict):
            abstentions.append(
                Abstention(f"proposed:{position}", "", AbstentionReason.MALFORMED_PROPOSAL)
            )
            continue
        frame_id = str(item.get("frame_id") or f"proposed:{position}")
        family = str(item.get("family") or "")
        polarity = _polarity(item.get("polarity"))
        if polarity is None:
            abstentions.append(
                Abstention(
                    frame_id, family, AbstentionReason.POLARITY_NOT_PROVEN, str(item.get("polarity"))
                )
            )
            continue
        scope = _locate(canonical, item.get("scope"), 0, canonical.length)
        if scope is None:
            abstentions.append(
                Abstention(frame_id, family, AbstentionReason.SPAN_NOT_BOUND, "scope")
            )
            continue
        # A proposer may weld an instruction the runtime obeys onto the front of the demand it is
        # describing. The frame may claim the demand; it may not claim the constraint.
        scope = _scope_without_leading_constraint(canonical, scope)
        # An ABSENT predicate is the frame itself, not a refusal. A frame carries its predicate to
        # answer one question -- "is a role filler just the request-word?" -- and when the proposer
        # does not point at one, the scope answers it at least as strictly: a role that spans the
        # whole frame is not a role. Measured live on qwen2.5:7b: `"predicate": ""` threw away a
        # complete, correct, two-member location list. Losing proven role structure over a field
        # that adds nothing the scope does not already give is the ceremony this contract rejects.
        predicate = _locate(canonical, item.get("predicate"), scope.start, scope.end)
        if predicate is None and str(item.get("predicate") or "").strip():
            # Pointed at something, and it is not in the frame. That IS a refusal: the proposer
            # believes in a predicate the text does not contain there.
            abstentions.append(
                Abstention(frame_id, family, AbstentionReason.SPAN_NOT_BOUND, "predicate")
            )
            continue
        if predicate is None:
            predicate = scope
        located: list[tuple[str, EntitySpan]] = []
        broken = ""
        cursor: dict[str, int] = {}
        for role_item in list(item.get("roles") or []):
            if not isinstance(role_item, dict):
                broken = "role is not an object"
                break
            role_name = str(role_item.get("role") or "")
            needle = role_item.get("text")
            # Repeated fillers ("weather in Paris and Paris") are two members that resolve to one
            # string, so each search starts after the previous member of the same role.
            begin = max(scope.start, cursor.get(role_name, scope.start))
            span = _locate(canonical, needle, begin, scope.end)
            if span is None:
                span = _locate(canonical, needle, scope.start, scope.end)
            if span is None:
                broken = f"{role_name}:{needle!r}"
                break
            cursor[role_name] = span.end
            located.append((role_name, span))
        roles = _derive_coordination(located)
        if broken:
            abstentions.append(
                Abstention(frame_id, family, AbstentionReason.INVENTED_SURFACE, broken)
            )
            continue
        evidence.append(
            SemanticFrameEvidence(
                frame_id=frame_id,
                frame_scope=scope,
                predicate_span=predicate,
                family=family,
                polarity=polarity,
                roles=tuple(roles),
                provenance=ProofProvenance.BOUNDED_MODEL,
            )
        )
    return tuple(evidence), tuple(abstentions)


def _derive_coordination(
    located: Sequence[tuple[str, EntitySpan]],
) -> tuple[SemanticRoleEvidence, ...]:
    """Coordination membership, DERIVED from role membership and span order.

    Which list a filler belongs to and where it sits in that list carry no information the runtime
    does not already hold: a frame has one list per role, and the order of a list is the order its
    members appear in the text. Both are therefore computed here rather than demanded from the
    proposer.

    That distinction is the whole point and it is narrow. `coordination_group_id` and
    `member_ordinal` are BOOKKEEPING -- runtime identifiers with no independent semantic content.
    Everything the proposer actually knows and the runtime does not stays required and stays
    checked: which span fills which role, where the frame begins and ends, and whether it is
    asserted. Measured on qwen2.5:7b: 2 of 23 frames survived when these two fields had to be
    reproduced verbatim, and 23 of 23 survived once they were derived -- with identical semantic
    content either way. Refusing 21 correct proofs over an identifier the model was never the
    authority for is ceremony, not proof.

    Coordination that is genuinely UNCERTAIN is a different matter and is refused, not derived:
    two members of one role whose spans overlap cannot be distinct fillers, and `validate_frame`
    abstains on them rather than picking an order.
    """
    ordered = sorted(located, key=lambda pair: (pair[1].start, pair[1].end))
    counters: dict[str, int] = {}
    roles: list[SemanticRoleEvidence] = []
    for role_name, span in ordered:
        ordinal = counters.get(role_name, 0)
        counters[role_name] = ordinal + 1
        roles.append(
            SemanticRoleEvidence(
                role_name=role_name,
                value_span=span,
                # One list per role per frame. A frame contract declares at most one coordinating
                # role, so a second independent list of the same role is not a shape any contract
                # describes -- and inventing one here would be the runtime guessing structure.
                coordination_group_id=f"{role_name}:members",
                member_ordinal=ordinal,
            )
        )
    return tuple(roles)


def _polarity(value: Any) -> Polarity | None:
    """A polarity the proposer STATED, or None. There is no default and no inference."""
    if isinstance(value, Polarity):
        return value
    try:
        return Polarity(str(value).strip().casefold())
    except ValueError:
        return None


def _ordinal(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _scope_without_leading_constraint(canonical: Any, scope: Any) -> Any:
    """`scope` narrowed past any CONSTRAINT clause the proposer welded onto the front of it.

    A proposed frame scope is model-authored text located verbatim in the request, and a proposer
    may hand back a scope that spans an instruction the runtime OBEYS plus the demand after it.
    Measured on build 162ec5ca, acceptance turn 7, replaying the runtime's own captured frame call
    to `qwen2.5:7b`:

        {"family": "quantitative_reasoning", "polarity": "affirmed",
         "scope": "Do NOT search the web for this. From memory: what is the boiling point of
                   water at sea level in Celsius?"}

    Everything downstream was already correct -- the demand ledger minted two units with the
    prohibition excluded, the plan resolved the clean clause, and the certified author was asked
    exactly it -- so the ONLY thing carrying the welded text was this scope, and it surfaced as
    "Could not be answered: - Do NOT search the web for this. From memory: ..." through
    `decision.failure_lines`. A correctly minted demand with a wrongly described requirement.

    Turn IR already owns clause boundaries and already classifies a prohibition as
    `ClauseKind.CONSTRAINT`, so this re-uses that verdict rather than adding a second opinion. Only
    a LEADING run of constraint clauses is dropped, and only when a demand clause follows: the
    prohibition itself keeps every other effect it has -- `core.retrieval_constraints` still reports
    it and still forbids retrieval for the turn. This narrows what a frame CLAIMS TO ASK FOR; it
    does not edit displayed text and does not relax any restriction.
    """

    try:
        from core.turn_ir import ClauseKind, parse_turn_ir
    except Exception:
        return scope
    text = canonical.text[scope.start : scope.end]
    try:
        clauses = parse_turn_ir(text).clauses
    except Exception:
        return scope
    if len(clauses) < 2 or clauses[0].kind is not ClauseKind.CONSTRAINT:
        return scope
    first_demand = next(
        (clause for clause in clauses if clause.kind is not ClauseKind.CONSTRAINT), None
    )
    if first_demand is None:
        return scope
    probe = str(first_demand.request_text or "").strip()
    if not probe:
        return scope
    offset = text.find(probe)
    if offset <= 0:
        return scope
    return canonical.span(scope.start + offset, scope.end)


def _locate(
    canonical: CanonicalText, needle: Any, begin: int, end: int
) -> EntitySpan | None:
    """The span of `needle` inside `[begin, end)`, or None when the text does not contain it.

    Also accepts an explicit `[start, end]` pair, which is what a proposer that can count offsets
    should send. Either way the result is a span over THIS canonical text: there is no path here
    that fabricates one.
    """
    if isinstance(needle, (list, tuple)) and len(needle) == 2:
        try:
            start, stop = int(needle[0]), int(needle[1])
        except (TypeError, ValueError):
            return None
        if not (begin <= start < stop <= end):
            return None
        return canonical.span(start, stop)
    probe = canonical.normalize(str(needle or ""))
    if not probe.strip():
        return None
    index = canonical.text.find(probe, begin, end)
    if index < 0:
        return None
    return canonical.span(index, index + len(probe), label=probe)


def bounded_model_evidence(
    canonical: CanonicalText,
    propose: Callable[[str, str], str] | None,
    *,
    skip_spans: Sequence[tuple[int, int]] = (),
) -> tuple[tuple[SemanticFrameEvidence, ...], tuple[Abstention, ...]]:
    """Ask the injected proposer for typed evidence, or abstain when there is none to ask.

    `propose` absent is a normal, safe state -- a local-only lane with nothing wired, a turn whose
    model is down, a unit test. It produces zero frames and zero provider calls, never a guess.
    """
    if propose is None:
        return (), (
            Abstention("", "", AbstentionReason.PROPOSER_ABSTAINED, "no proposer available"),
        )
    try:
        raw = propose(proposal_prompt(), canonical.text)
    except Exception as exc:  # a proposer fault is an abstention, never a fabricated frame
        return (), (
            Abstention("", "", AbstentionReason.PROPOSER_ABSTAINED, f"{type(exc).__name__}: {exc}"),
        )
    evidence, abstentions = parse_proposal(raw, canonical)
    if skip_spans:
        evidence = tuple(
            item
            for item in evidence
            if not any(
                item.frame_scope.start < end and start < item.frame_scope.end
                for start, end in skip_spans
            )
        )
    return evidence, abstentions


# --- the one entry point ----------------------------------------------------------------------


def prove_turn(
    request: str,
    *,
    propose: Callable[[str, str], str] | None = None,
) -> SemanticTurnProof:
    """Everything `request` proves about its own semantic roles.

    Both paths run over the WHOLE text and `_arbitrate` decides the overlaps. The bounded path used
    to be told to skip whatever the formal grammars had claimed, which sounds like sensible
    deduplication and is in fact how a prohibition became a live currency call: "never convert EUR
    to JPY" was claimed by the formal FX grammar, so the one reader that could see the "never" was
    never asked about that region at all.
    """
    canonical = CanonicalText.of(request)
    formal = formal_grammar_evidence(canonical)
    proposed, abstentions = bounded_model_evidence(canonical, propose)
    proof = validate_proof(canonical, (*formal, *proposed))
    return SemanticTurnProof(
        canonical=proof.canonical,
        frames=proof.frames,
        abstentions=(*proof.abstentions, *abstentions),
        unclaimed_spans=proof.unclaimed_spans,
    )


__all__ = [
    "Abstention",
    "AbstentionReason",
    "EvidenceStatus",
    "FrameContract",
    "Polarity",
    "ProofProvenance",
    "ProvenFrame",
    "ProvenRole",
    "SemanticFrameEvidence",
    "SemanticRoleEvidence",
    "SemanticTurnProof",
    "bounded_model_evidence",
    "formal_grammar_evidence",
    "frame_contract",
    "known_frame_contracts",
    "parse_proposal",
    "proposal_prompt",
    "prove_turn",
    "register_frame_contract",
    "unregister_frame_contract",
    "validate_frame",
    "validate_proof",
]
