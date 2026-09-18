"""The RequestGraph: one preserved, versioned contract for what a user turn asked.

This is the architectural spine the 2026-09-08 Astra review requires. The prior design was
``user text -> list of operation guesses``; that cannot preserve WHICH requests were made, so every
downstream stage (planner, demand ownership, execution, finalization, presentation) re-derived "what
the user asked" from raw text under its own ontology, and requests, prohibitions, operand roles and
slots drifted or vanished between stages. The RequestGraph fixes that by giving every requested
obligation a STABLE identity, minted once, that survives the whole pipeline.

It is deliberately a small set of composable frozen records, not a god-object. A producer (the
model-first resolver, a deterministic recognizer, or a test) BUILDS a graph; the trusted controller
VALIDATES it; downstream stages ADAPT to its ids rather than re-parsing text. Nothing here executes,
authorizes, or renders — it only records interpreted meaning and the obligations to preserve.

Four laws it enforces at construction (the rest are enforced by the reconciliation and admission
layers that consume it):

1. **Stable identity.** Every request, slot, mention, constraint and dependency has an id that is
   unique within the graph and never re-derived downstream.
2. **Source conservation.** Every request and every uncovered stretch of the turn is represented.
   Text that could not be interpreted becomes an UNRESOLVED request/slot, never a silent drop.
3. **Distinct states.** "Could not understand", "answerable without a tool", "capability
   unsupported", "tool forbidden", "retrieval forbidden", "failed", "unverified" and "needs input"
   are different states and are never collapsed into one ``unknown``.
4. **Referential integrity.** Every id a record points at (a slot's parent request, a dependency's
   endpoints, a prohibition's scope, a retraction's target) must exist in the graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NewType

from core.semantic.canonical_text import CanonicalText, EntitySpan
from core.semantic.types import RequestShape

SCHEMA = "vool.request_graph.v1"

# -- stable identities (minted once, carried everywhere) ---------------------
# NewType keeps them distinct to a type checker at zero runtime cost. They are plain strings at
# runtime; the builder mints them (e.g. "req-0", "slot-1") and they are frozen into the graph.
TurnId = NewType("TurnId", str)
RequestId = NewType("RequestId", str)
SlotId = NewType("SlotId", str)
MentionId = NewType("MentionId", str)
ConstraintId = NewType("ConstraintId", str)
DependencyId = NewType("DependencyId", str)


class InterpretationState(str, Enum):
    """What interpretation concluded about ONE request/slot. These are deliberately distinct — the
    Astra review names collapsing them into a single ``unknown`` as a core defect."""

    RESOLVED = "resolved"                    # interpreted with confidence
    AMBIGUOUS = "ambiguous"                  # several readings survive (carry alternatives)
    UNRESOLVED = "unresolved"                # source preserved, not yet interpreted
    UNKNOWN_MEANING = "unknown_meaning"      # cannot tell what was meant
    ANSWERABLE_WITHOUT_TOOL = "answerable_without_tool"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    TOOL_FORBIDDEN = "tool_forbidden"        # a tool would serve it but the turn forbids tools
    RETRIEVAL_FORBIDDEN = "retrieval_forbidden"
    NEEDS_INPUT = "needs_input"              # requires clarification before it can proceed


class TerminalDisposition(str, Enum):
    """How a slot FINISHED. Assigned only by the reconciliation layer, never by the resolver.
    Every frozen SlotId must end at exactly one of these (the slot-conservation law)."""

    ANSWERED = "answered"                    # requires real answer/evidence content
    UNKNOWN = "unknown"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"
    DENIED = "denied"
    NEEDS_INPUT = "needs_input"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class SemanticRole(str, Enum):
    """An operand's role by MEANING, never by position. 'BTC to silver' and 'silver to BTC' differ
    only in role; binding by argument order is the operand-drift defect this exists to prevent."""

    SUBJECT = "subject"
    SOURCE = "source"
    TARGET = "target"
    PAYMENT = "payment"
    DESTINATION = "destination"
    COMPARISON_A = "comparison_a"
    COMPARISON_B = "comparison_b"
    RESULT_REF = "result_ref"                # refers to another slot's result
    OTHER = "other"


class ConstraintKind(str, Enum):
    TIME = "time"
    LOCATION = "location"
    FRESHNESS = "freshness"                  # needs current data
    RETRIEVAL_REQUIRED = "retrieval_required"
    RETRIEVAL_FORBIDDEN = "retrieval_forbidden"
    TOOL_FORBIDDEN = "tool_forbidden"
    OUTPUT_FORMAT = "output_format"
    USER_RESTRICTION = "user_restriction"


class DependencyKind(str, Enum):
    VALUE = "value"                          # needs another slot's RESULT (released only on success)
    ORDERING = "ordering"                    # must run after, no value passed
    CONDITIONAL = "conditional"              # runs only if the prerequisite holds


class OutputFormat(str, Enum):
    PROSE = "prose"
    TABLE = "table"
    JSON = "json"
    LITERAL = "literal"
    LIST = "list"
    UNSPECIFIED = "unspecified"


# -- leaf records ------------------------------------------------------------


@dataclass(frozen=True)
class Mention:
    """A source-bound reference to something named in the turn. Occurrence identity is explicit: a
    repeated 'Paris' is two Mentions with two spans, never one deduplicated (or globally first-bound)
    reference."""

    id: MentionId
    span: EntitySpan                          # bound to the graph's canonical text
    surface: str                              # the words as written
    role: SemanticRole = SemanticRole.OTHER
    entity_key: str = ""                      # resolved identity, "" when unresolved
    alternatives: tuple[str, ...] = ()        # candidate identities when ambiguous
    #: Which occurrence of ``surface`` in the canonical text this is (0-based, left to right).
    #: Occurrence identity made explicit: two mentions of one surface differ here and in span.
    occurrence: int = 0


@dataclass(frozen=True)
class Quantity:
    """A quantity as EXPRESSED, with dimensional identity. Exact value kept as a string so no float
    rounding enters the contract; the arithmetic layer parses it to Decimal/Fraction."""

    raw: str
    exact: str = ""                           # canonical decimal/rational text, "" if not numeric-exact
    unit: str = ""
    currency: str = ""
    asset: str = ""                           # crypto/token identity
    chain: str = ""                           # when material to identity
    alternatives: tuple[str, ...] = ()        # e.g. "kr" -> {SEK, NOK, DKK}


@dataclass(frozen=True)
class Operand:
    """One operand of a request, bound by ROLE. Exactly one of mention_id / quantity / result_ref
    carries the value; the others stay empty."""

    role: SemanticRole
    mention_id: MentionId | None = None
    quantity: Quantity | None = None
    result_ref: SlotId | None = None          # this operand IS another slot's result


@dataclass(frozen=True)
class Ambiguity:
    """Unresolved choice a slot could not settle. Its presence is the AMBIGUOUS state made data."""

    alternatives: tuple[str, ...]
    reason: str = ""
    needs_clarification: bool = False


@dataclass(frozen=True)
class Constraint:
    id: ConstraintId
    kind: ConstraintKind
    detail: str = ""
    span: EntitySpan | None = None
    scope: tuple[RequestId, ...] = ()         # requests it applies to; () = whole turn


@dataclass(frozen=True)
class Prohibition:
    """A first-class 'do not' — scoped and source-bound. Losing this (e.g. an empty resolver output)
    is the missing-prohibition defect; it is a record, not an absence."""

    id: ConstraintId
    target: str                               # what is forbidden (a tool family, an asset, retrieval)
    scope: tuple[RequestId, ...] = ()
    span: EntitySpan | None = None


@dataclass(frozen=True)
class Retraction:
    """A within-turn cancellation/correction with an explicit target and supersession.

    ``supersedes_kind`` says WHAT ``supersedes`` names -- a request or one slot. Ids are plain
    strings at runtime, and a producer may legitimately give a request and a slot the same string
    (the lexical producer does), so the kind is data, never inferred from which set the string is in.
    """

    id: ConstraintId
    target: str                               # request/slot id or described target
    supersedes: RequestId | SlotId | None = None
    span: EntitySpan | None = None
    supersedes_kind: str = "request"          # "request" | "slot"


@dataclass(frozen=True)
class PresentationContract:
    """What the user asked the ANSWER to look like. Carried, never acted on here — the renderer
    consumes it, and may not add or drop slots to satisfy it."""

    fmt: OutputFormat = OutputFormat.UNSPECIFIED
    fields: tuple[str, ...] = ()
    ordering: str = ""
    units: str = ""
    brevity: str = ""
    literal_output: bool = False


@dataclass(frozen=True)
class Slot:
    """One answer obligation with a stable id. A single request may open several slots (e.g. 'gold,
    silver and platinum prices' is one market request with three slots), each of which can
    independently be answered or go missing."""

    id: SlotId
    request_id: RequestId
    expected: str = ""                        # what a satisfying answer must contain
    state: InterpretationState = InterpretationState.UNRESOLVED
    operands: tuple[Operand, ...] = ()
    quantity: Quantity | None = None
    ambiguity: Ambiguity | None = None
    #: Why the slot is UNKNOWN_MEANING / UNRESOLVED / NEEDS_INPUT / UNSUPPORTED_CAPABILITY, when it
    #: is. A typed state without its reason is a verdict nobody can act on or measure.
    reason: str = ""


@dataclass(frozen=True)
class DependencyEdge:
    id: DependencyId
    from_request: RequestId                   # the dependent
    to_request: RequestId                     # the prerequisite
    kind: DependencyKind = DependencyKind.ORDERING
    value_ref: SlotId | None = None           # for VALUE deps: the prerequisite slot whose RESULT is used


@dataclass(frozen=True)
class Request:
    """One thing the user asked. ``unresolved`` preserves source that could not be interpreted rather
    than dropping it. ``parent_id`` links a dependent/child request to its parent."""

    id: RequestId
    source_text: str
    span: EntitySpan | None = None
    parent_id: RequestId | None = None
    slot_ids: tuple[SlotId, ...] = ()
    state: InterpretationState = InterpretationState.UNRESOLVED
    unresolved: bool = False
    #: The capability family a producer believes serves this request (an operation name). A hint
    #: about HOW, never authority over WHETHER -- admission decides that. "" when none was named.
    family: str = ""


class RequestGraphError(ValueError):
    """A graph that violates an identity/conservation/integrity law at construction."""


@dataclass(frozen=True)
class RequestGraph:
    """The whole interpreted contract for one turn. Frozen; validated at construction.

    ``uncovered_source`` records stretches of the turn that no request covers — the explicit form of
    'we did not silently drop part of the request'. A producer that cannot interpret a stretch either
    emits an UNRESOLVED request for it or lists it here; a graph that does neither is not conserving
    source, and the reconciliation layer will treat that as a defect.
    """

    turn_id: TurnId
    canonical: CanonicalText
    schema: str = SCHEMA
    requests: tuple[Request, ...] = ()
    slots: tuple[Slot, ...] = ()
    mentions: tuple[Mention, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    prohibitions: tuple[Prohibition, ...] = ()
    retractions: tuple[Retraction, ...] = ()
    dependencies: tuple[DependencyEdge, ...] = ()
    presentation: PresentationContract | None = None
    uncovered_source: tuple[str, ...] = ()
    #: The structural shape of the turn (``core.semantic.types.RequestShape``). Derived by the
    #: builder from the graph's own records (a CONDITIONAL edge, a Retraction, the request count),
    #: never from phrase rules; this is the receipt's ``shape`` slot, filled from the resolver.
    shape: RequestShape = RequestShape.UNKNOWN

    def __post_init__(self) -> None:
        self._require_unique_ids()
        self._require_referential_integrity()
        self._require_span_binding()

    # -- law 1: stable, unique identity --------------------------------------
    def _require_unique_ids(self) -> None:
        for kind, ids in (
            ("request", [r.id for r in self.requests]),
            ("slot", [s.id for s in self.slots]),
            ("mention", [m.id for m in self.mentions]),
            ("dependency", [d.id for d in self.dependencies]),
        ):
            if len(ids) != len(set(ids)):
                dupes = sorted({i for i in ids if ids.count(i) > 1})
                raise RequestGraphError(f"duplicate {kind} ids: {dupes}")

    # -- law 4: referential integrity ----------------------------------------
    def _require_referential_integrity(self) -> None:
        request_ids = {r.id for r in self.requests}
        slot_ids = {s.id for s in self.slots}
        mention_ids = {m.id for m in self.mentions}
        by_request = {r.id: r for r in self.requests}

        for slot in self.slots:
            if slot.request_id not in request_ids:
                raise RequestGraphError(f"slot {slot.id} references missing request {slot.request_id}")
            if slot.id not in by_request[slot.request_id].slot_ids:
                raise RequestGraphError(
                    f"slot {slot.id} is not listed by its request {slot.request_id}.slot_ids"
                )
            for op in slot.operands:
                if op.mention_id is not None and op.mention_id not in mention_ids:
                    raise RequestGraphError(f"slot {slot.id} operand references missing mention {op.mention_id}")
                if op.result_ref is not None and op.result_ref not in slot_ids:
                    raise RequestGraphError(f"slot {slot.id} operand references missing slot {op.result_ref}")
        for req in self.requests:
            if req.parent_id is not None and req.parent_id not in request_ids:
                raise RequestGraphError(f"request {req.id} references missing parent {req.parent_id}")
            for sid in req.slot_ids:
                if sid not in slot_ids:
                    raise RequestGraphError(f"request {req.id} lists missing slot {sid}")
        for dep in self.dependencies:
            if dep.from_request not in request_ids or dep.to_request not in request_ids:
                raise RequestGraphError(f"dependency {dep.id} references a missing request endpoint")
            if dep.from_request == dep.to_request:
                raise RequestGraphError(f"dependency {dep.id} is self-referential")
            if dep.value_ref is not None and dep.value_ref not in slot_ids:
                raise RequestGraphError(f"dependency {dep.id} value_ref names missing slot {dep.value_ref}")
        for constraint in self.constraints:
            self._require_scope(constraint.id, constraint.scope, request_ids)
        for prohibition in self.prohibitions:
            self._require_scope(prohibition.id, prohibition.scope, request_ids)
        for retraction in self.retractions:
            if retraction.supersedes_kind not in ("request", "slot"):
                raise RequestGraphError(f"retraction {retraction.id} has unknown supersedes_kind {retraction.supersedes_kind!r}")
            if retraction.supersedes is not None:
                universe = request_ids if retraction.supersedes_kind == "request" else slot_ids
                if retraction.supersedes not in universe:
                    raise RequestGraphError(
                        f"retraction {retraction.id} supersedes missing {retraction.supersedes_kind} {retraction.supersedes}"
                    )
        # Dependency edges must be acyclic: a cycle has no executable order, and a projection that
        # "picked one" would silently drop an edge the user stated. Refused at construction.
        adjacency: dict[str, set[str]] = {}
        for dep in self.dependencies:
            adjacency.setdefault(dep.from_request, set()).add(dep.to_request)
        state: dict[str, int] = {}

        def _visit(node: str, stack: list[str]) -> None:
            state[node] = 1
            for nxt in adjacency.get(node, ()):
                if state.get(nxt, 0) == 1:
                    raise RequestGraphError(f"dependency cycle through {' -> '.join([*stack, node, nxt])}")
                if state.get(nxt, 0) == 0:
                    _visit(nxt, [*stack, node])
            state[node] = 2

        for start in adjacency:
            if state.get(start, 0) == 0:
                _visit(start, [])

    def _require_scope(self, owner: str, scope: tuple[str, ...], request_ids: set[str]) -> None:
        for rid in scope:
            if rid not in request_ids:
                raise RequestGraphError(f"{owner} scope names missing request {rid}")

    # -- law 2 (partial): spans must bind to THIS turn's text ----------------
    def _require_span_binding(self) -> None:
        for holder, span in self._all_spans():
            if not span.binds_to(self.canonical):
                raise RequestGraphError(f"{holder} carries a span not bound to this turn's canonical text")

    def _all_spans(self):
        for r in self.requests:
            if r.span is not None:
                yield (f"request {r.id}", r.span)
        for m in self.mentions:
            yield (f"mention {m.id}", m.span)
        for c in self.constraints:
            if c.span is not None:
                yield (f"constraint {c.id}", c.span)
        for p in self.prohibitions:
            if p.span is not None:
                yield (f"prohibition {p.id}", p.span)

    # -- read helpers --------------------------------------------------------
    @property
    def slot_ids(self) -> frozenset[SlotId]:
        """The frozen obligation set — the left side of the slot-conservation law."""
        return frozenset(s.id for s in self.slots)

    def slots_for(self, request_id: RequestId) -> tuple[Slot, ...]:
        return tuple(s for s in self.slots if s.request_id == request_id)

    def is_source_conserved(self) -> bool:
        """Every request either interprets its source or is marked unresolved; nothing pending
        silently. (Full text-coverage proof needs the segmenter; this is the graph-local check.)"""
        return all(
            (not r.unresolved) or r.state is InterpretationState.UNRESOLVED for r in self.requests
        )


__all__ = [
    "SCHEMA",
    "Ambiguity",
    "Constraint",
    "ConstraintId",
    "ConstraintKind",
    "DependencyEdge",
    "DependencyId",
    "DependencyKind",
    "InterpretationState",
    "Mention",
    "MentionId",
    "Operand",
    "OutputFormat",
    "PresentationContract",
    "Prohibition",
    "Quantity",
    "Request",
    "RequestGraph",
    "RequestGraphError",
    "RequestId",
    "Retraction",
    "SemanticRole",
    "Slot",
    "SlotId",
    "TerminalDisposition",
    "TurnId",
]
