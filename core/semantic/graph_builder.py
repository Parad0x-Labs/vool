"""Build a ``RequestGraph`` with minted stable ids, text-bound spans and source conservation.

Every producer of a graph -- the model-first resolver's parser, the deterministic lexical producer,
the gold corpus -- goes through this builder, so the three laws a producer is most likely to break
are enforced in ONE place rather than remembered in three:

* **Ids are minted, never positional.** ``req-N`` / ``slot-N`` / ``m-N`` / ``c-N`` / ``d-N`` are
  handed out as records are added and never renumbered; a record added later cannot shift an id
  minted earlier (the "skipped clause shifts dependency indices" defect).
* **Mentions bind by OCCURRENCE.** A surface that appears twice is two occurrences with two spans;
  ``add_mention`` binds the next unclaimed occurrence (optionally inside a request's own span, or
  an explicit occurrence index) and records which occurrence it took. Binding "the first global
  match" for every request is the repeated-entity defect this exists to prevent.
* **Uncovered source is conserved.** ``build()`` finds every stretch of the turn no recorded span
  covers; a stretch that still carries a content word becomes an UNRESOLVED request with that
  fragment as its source, and is listed in ``uncovered_source``. Text is never silently dropped.

The filler vocabulary below is conservation ACCOUNTING only -- it decides whether an uncovered
stretch is "and", "please" or an actual request fragment. It routes nothing and is not consulted by
any interpretation; the comparator (``graph_diff``) uses the same tokenizer so both sides agree on
what counts as content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.semantic.canonical_text import CanonicalText, EntitySpan
from core.semantic.request_graph import (
    Ambiguity,
    Constraint,
    ConstraintId,
    ConstraintKind,
    DependencyEdge,
    DependencyId,
    DependencyKind,
    InterpretationState,
    Mention,
    MentionId,
    Operand,
    OutputFormat,
    PresentationContract,
    Prohibition,
    Quantity,
    Request,
    RequestGraph,
    RequestId,
    Retraction,
    SemanticRole,
    Slot,
    SlotId,
    TurnId,
)
from core.semantic.types import RequestShape


class GraphBuildError(ValueError):
    """A producer asked for something the text cannot support (an unfindable surface, an unknown
    request id, an occurrence that does not exist). Producers that must not raise (the model
    parser) catch this and record an UNRESOLVED state instead."""


#: Word tokens, apostrophes included ("what's", "don't"). Underscore is not a word character here.
_TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")

#: Function words and politeness that carry no obligation of their own. Conservation accounting
#: only (see the module docstring); deliberately without any retraction/prohibition cue ("wait",
#: "cancel", "stop", "ignore", "instead", "not", "no") -- those are content and must be accounted.
_FILLER_TOKENS: frozenset[str] = frozenset((
    "a", "an", "the", "and", "or", "plus", "then", "too", "as", "well", "also", "btw", "pls",
    "please", "thx", "thanks", "thank", "you", "ok", "okay", "hey", "hi", "hello", "yo", "so",
    "now", "just", "kindly", "i", "me", "my", "mine", "we", "us", "our", "you", "your", "it",
    "its", "is", "are", "am", "was", "were", "be", "been", "being", "do", "does", "did", "can",
    "could", "would", "should", "will", "shall", "may", "might", "what", "whats", "how", "tell",
    "give", "show", "let", "know", "want", "need", "of", "to", "for", "in", "on", "at", "by",
    "with", "from", "about", "this", "that", "these", "those", "there", "here", "one", "some",
    "any", "all", "more", "much", "many", "again", "very", "really", "quite", "rather", "like",
    "which", "who", "whom", "whose", "when", "where", "why",
))


def content_tokens(text: str) -> tuple[tuple[int, int, str], ...]:
    """Every token that is not filler, as ``(start, end, lowered)``. Shared with the comparator."""
    out: list[tuple[int, int, str]] = []
    for match in _TOKEN_RE.finditer(text):
        word = match.group(0).lower().replace("’", "'")
        if word in _FILLER_TOKENS:
            continue
        out.append((match.start(), match.end(), word))
    return tuple(out)


@dataclass
class _RequestDraft:
    id: RequestId
    source_text: str
    span: EntitySpan | None
    parent_id: RequestId | None
    state: InterpretationState
    unresolved: bool
    family: str = ""
    slot_ids: list[SlotId] = field(default_factory=list)


class RequestGraphBuilder:
    """Accumulate records, then ``build()`` one validated ``RequestGraph``."""

    def __init__(self, canonical: CanonicalText | str, *, turn_id: str, id_prefix: str = "") -> None:
        self.canonical = canonical if isinstance(canonical, CanonicalText) else CanonicalText.of(canonical)
        self._turn_id = TurnId(str(turn_id))
        self._prefix = str(id_prefix or "")
        self._counters: dict[str, int] = {}
        self._requests: dict[RequestId, _RequestDraft] = {}
        self._slots: list[Slot] = []
        self._mentions: list[Mention] = []
        self._constraints: list[Constraint] = []
        self._prohibitions: list[Prohibition] = []
        self._retractions: list[Retraction] = []
        self._dependencies: list[DependencyEdge] = []
        self._presentation: PresentationContract | None = None
        self._shape: RequestShape | None = None
        #: (normalized surface) -> claimed occurrence indices, so repeats bind left to right.
        self._claimed: dict[str, set[int]] = {}
        #: (normalized source text) -> next search cursor, so two identical clauses get two spans.
        self._source_cursor: dict[str, int] = {}
        #: Ids in use, PER KIND: the graph's uniqueness law is per record kind, so a request and a
        #: slot may legitimately share the string "u1" (the lexical producer relies on exactly that).
        self._used_ids: dict[str, set[str]] = {}

    # -- id minting ----------------------------------------------------------

    def _mint(self, kind: str, explicit: str | None) -> str:
        used = self._used_ids.setdefault(kind, set())
        if explicit:
            value = str(explicit)
            if value in used:
                raise GraphBuildError(f"{kind} id {value!r} already minted in this graph")
            used.add(value)
            return value
        while True:
            n = self._counters.get(kind, 0)
            self._counters[kind] = n + 1
            value = f"{self._prefix}{kind}-{n}"
            if value not in used:
                used.add(value)
                return value

    # -- records -------------------------------------------------------------

    def add_request(
        self,
        source_text: str,
        *,
        span: EntitySpan | None = None,
        state: InterpretationState = InterpretationState.RESOLVED,
        parent_id: RequestId | None = None,
        unresolved: bool = False,
        request_id: str | None = None,
        family: str = "",
    ) -> RequestId:
        text = str(source_text or "")
        if span is None and text.strip():
            span = self._locate_source(text)
        if unresolved:
            state = InterpretationState.UNRESOLVED
        rid = RequestId(self._mint("req", request_id))
        if parent_id is not None and parent_id not in self._requests:
            raise GraphBuildError(f"parent request {parent_id!r} does not exist")
        self._requests[rid] = _RequestDraft(
            id=rid, source_text=text, span=span, parent_id=parent_id, state=state,
            unresolved=bool(unresolved), family=str(family or ""),
        )
        return rid

    def _locate_source(self, text: str) -> EntitySpan | None:
        probe = self.canonical.normalize(text.strip())
        key = probe.casefold()
        start = self._source_cursor.get(key, 0)
        found = self.canonical.find(probe, start=start)
        if found is None:
            # Case-insensitive retry: a producer may quote the clause with different casing.
            index = self.canonical.text.casefold().find(key, start)
            if index < 0:
                return None
            found = self.canonical.span(index, index + len(probe))
        self._source_cursor[key] = found.end
        return found

    def add_slot(
        self,
        request_id: RequestId,
        *,
        expected: str = "",
        state: InterpretationState = InterpretationState.RESOLVED,
        operands: tuple[Operand, ...] = (),
        quantity: Quantity | None = None,
        ambiguity: Ambiguity | None = None,
        reason: str = "",
        slot_id: str | None = None,
    ) -> SlotId:
        draft = self._requests.get(request_id)
        if draft is None:
            raise GraphBuildError(f"request {request_id!r} does not exist")
        sid = SlotId(self._mint("slot", slot_id))
        if ambiguity is not None and state is InterpretationState.RESOLVED:
            state = InterpretationState.AMBIGUOUS
        self._slots.append(
            Slot(
                id=sid,
                request_id=request_id,
                expected=str(expected or ""),
                state=state,
                operands=tuple(operands),
                quantity=quantity,
                ambiguity=ambiguity,
                reason=str(reason or ""),
            )
        )
        draft.slot_ids.append(sid)
        return sid

    def add_mention(
        self,
        surface: str,
        *,
        within: EntitySpan | None = None,
        occurrence: int | None = None,
        kind: str = "",
        role: SemanticRole = SemanticRole.OTHER,
        entity_key: str = "",
        alternatives: tuple[str, ...] = (),
        mention_id: str | None = None,
    ) -> MentionId:
        """Bind ``surface`` to ONE occurrence in the text and record which one.

        Resolution order: an explicit ``occurrence`` index; else the first unclaimed occurrence
        inside ``within`` (a request's own span); else the first unclaimed occurrence anywhere.
        Raises ``GraphBuildError`` when the surface is not in the text or every occurrence is taken
        -- a producer that cannot raise turns that into an UNRESOLVED slot with a reason.
        """
        probe = self.canonical.normalize(str(surface or ""))
        if not probe.strip():
            raise GraphBuildError("a mention needs a non-empty surface")
        all_spans = self.canonical.find_all(probe, kind=kind)
        if not all_spans:
            lowered = self.canonical.text.casefold()
            key = probe.casefold()
            all_spans = tuple(
                self.canonical.span(m.start(), m.end(), label=probe, kind=kind)
                for m in re.finditer(re.escape(key), lowered)
            )
        if not all_spans:
            raise GraphBuildError(f"surface {surface!r} is not in the text")
        claimed = self._claimed.setdefault(probe.casefold(), set())
        if occurrence is not None:
            if not 0 <= int(occurrence) < len(all_spans):
                raise GraphBuildError(
                    f"surface {surface!r} has {len(all_spans)} occurrence(s); index {occurrence} does not exist"
                )
            index = int(occurrence)
        else:
            candidates = [
                i
                for i, s in enumerate(all_spans)
                if i not in claimed and (within is None or (s.start >= within.start and s.end <= within.end))
            ]
            if not candidates:
                raise GraphBuildError(
                    f"no unclaimed occurrence of {surface!r}" + (" inside the given span" if within else "")
                )
            index = candidates[0]
        claimed.add(index)
        mid = MentionId(self._mint("m", mention_id))
        self._mentions.append(
            Mention(
                id=mid,
                span=all_spans[index],
                surface=all_spans[index].resolve(self.canonical),
                role=role,
                entity_key=str(entity_key or ""),
                alternatives=tuple(alternatives),
                occurrence=index,
            )
        )
        return mid

    def add_constraint(
        self,
        kind: ConstraintKind,
        *,
        detail: str = "",
        span: EntitySpan | None = None,
        scope: tuple[RequestId, ...] = (),
        constraint_id: str | None = None,
    ) -> ConstraintId:
        cid = ConstraintId(self._mint("c", constraint_id))
        self._constraints.append(Constraint(id=cid, kind=kind, detail=str(detail or ""), span=span, scope=tuple(scope)))
        return cid

    def add_prohibition(
        self,
        target: str,
        *,
        scope: tuple[RequestId, ...] = (),
        span: EntitySpan | None = None,
        prohibition_id: str | None = None,
    ) -> ConstraintId:
        pid = ConstraintId(self._mint("p", prohibition_id))
        self._prohibitions.append(Prohibition(id=pid, target=str(target or ""), scope=tuple(scope), span=span))
        return pid

    def add_retraction(
        self,
        target: str,
        *,
        supersedes: RequestId | SlotId | None = None,
        span: EntitySpan | None = None,
        retraction_id: str | None = None,
        supersedes_kind: str | None = None,
    ) -> ConstraintId:
        """``supersedes_kind`` may be omitted only when the id names exactly one of {request, slot};
        a string that is both (the lexical producer's u1 == u1) must say which it means."""
        kind = supersedes_kind
        if supersedes is not None and kind is None:
            is_request = supersedes in self._requests
            is_slot = any(s.id == supersedes for s in self._slots)
            if is_request and is_slot:
                raise GraphBuildError(f"{supersedes!r} names both a request and a slot; pass supersedes_kind")
            if not (is_request or is_slot):
                raise GraphBuildError(f"retraction supersedes unknown id {supersedes!r}")
            kind = "request" if is_request else "slot"
        rid = ConstraintId(self._mint("rt", retraction_id))
        self._retractions.append(Retraction(
            id=rid, target=str(target or ""), supersedes=supersedes, span=span, supersedes_kind=kind or "request",
        ))
        return rid

    def add_dependency(
        self,
        from_request: RequestId,
        to_request: RequestId,
        *,
        kind: DependencyKind = DependencyKind.ORDERING,
        value_ref: SlotId | None = None,
        dependency_id: str | None = None,
    ) -> DependencyId:
        if from_request == to_request:
            raise GraphBuildError("a request cannot depend on itself")
        if self._reaches(to_request, from_request):
            raise GraphBuildError(f"dependency {from_request} -> {to_request} would close a cycle")
        did = DependencyId(self._mint("d", dependency_id))
        self._dependencies.append(
            DependencyEdge(id=did, from_request=from_request, to_request=to_request, kind=kind, value_ref=value_ref)
        )
        return did

    def _reaches(self, start: RequestId, goal: RequestId) -> bool:
        """Whether ``goal`` is reachable from ``start`` along existing prerequisite edges."""
        frontier = [start]
        seen: set[str] = set()
        while frontier:
            node = frontier.pop()
            if node == goal:
                return True
            if node in seen:
                continue
            seen.add(node)
            frontier.extend(d.to_request for d in self._dependencies if d.from_request == node)
        return False

    def set_presentation(
        self,
        *,
        fmt: OutputFormat = OutputFormat.UNSPECIFIED,
        fields: tuple[str, ...] = (),
        ordering: str = "",
        units: str = "",
        brevity: str = "",
        literal_output: bool = False,
    ) -> None:
        self._presentation = PresentationContract(
            fmt=fmt, fields=tuple(fields), ordering=str(ordering or ""), units=str(units or ""),
            brevity=str(brevity or ""), literal_output=bool(literal_output),
        )

    def set_shape(self, shape: RequestShape) -> None:
        """Override the derived shape. Producers normally leave this to ``build()``."""
        self._shape = shape

    # -- read-back for producers ---------------------------------------------

    def request_span(self, request_id: RequestId) -> EntitySpan | None:
        draft = self._requests.get(request_id)
        return draft.span if draft is not None else None

    def request_ids(self) -> tuple[RequestId, ...]:
        return tuple(self._requests)

    # -- conservation --------------------------------------------------------

    def _covered_intervals(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for draft in self._requests.values():
            if draft.span is not None:
                out.append((draft.span.start, draft.span.end))
        for m in self._mentions:
            out.append((m.span.start, m.span.end))
        for c in self._constraints:
            if c.span is not None:
                out.append((c.span.start, c.span.end))
        for p in self._prohibitions:
            if p.span is not None:
                out.append((p.span.start, p.span.end))
        for r in self._retractions:
            if r.span is not None:
                out.append((r.span.start, r.span.end))
        return out

    def uncovered_stretches(self) -> tuple[EntitySpan, ...]:
        """Runs of CONTENT tokens no recorded span touches, as spans over the canonical text."""
        covered = self._covered_intervals()
        tokens = content_tokens(self.canonical.text)
        stretches: list[list[tuple[int, int, str]]] = []
        previous_index = -2
        for index, token in enumerate(tokens):
            start, end, _word = token
            if any(start < c_end and c_start < end for c_start, c_end in covered):
                continue
            if index == previous_index + 1 and stretches:
                stretches[-1].append(token)
            else:
                stretches.append([token])
            previous_index = index
        return tuple(self.canonical.span(run[0][0], run[-1][1]) for run in stretches)

    def _derived_shape(self) -> RequestShape:
        if self._shape is not None:
            return self._shape
        if any(d.kind is DependencyKind.CONDITIONAL for d in self._dependencies):
            return RequestShape.CONDITIONAL
        if self._retractions:
            return RequestShape.MID_TURN_CORRECTION
        interpreted = [d for d in self._requests.values() if not d.unresolved]
        if len(interpreted) > 1:
            return RequestShape.MULTI_CLAUSE
        if len(interpreted) == 1:
            return RequestShape.SINGLE
        return RequestShape.UNKNOWN

    def build(self, *, conserve_source: bool = True) -> RequestGraph:
        """Validate and freeze. With ``conserve_source`` every uncovered content stretch becomes an
        UNRESOLVED request (source preserved) and is listed in ``uncovered_source``."""
        uncovered: list[str] = []
        if conserve_source:
            for stretch in self.uncovered_stretches():
                fragment = stretch.resolve(self.canonical)
                uncovered.append(fragment)
                self.add_request(fragment, span=stretch, state=InterpretationState.UNRESOLVED, unresolved=True)
        requests = tuple(
            Request(
                id=d.id,
                source_text=d.source_text,
                span=d.span,
                parent_id=d.parent_id,
                slot_ids=tuple(d.slot_ids),
                state=d.state,
                unresolved=d.unresolved,
                family=d.family,
            )
            for d in self._requests.values()
        )
        return RequestGraph(
            turn_id=self._turn_id,
            canonical=self.canonical,
            requests=requests,
            slots=tuple(self._slots),
            mentions=tuple(self._mentions),
            constraints=tuple(self._constraints),
            prohibitions=tuple(self._prohibitions),
            retractions=tuple(self._retractions),
            dependencies=tuple(self._dependencies),
            presentation=self._presentation,
            uncovered_source=tuple(uncovered),
            shape=self._derived_shape(),
        )


def operand(
    role: SemanticRole,
    *,
    mention_id: MentionId | None = None,
    quantity: Quantity | None = None,
    result_ref: SlotId | None = None,
) -> Operand:
    """Keyword-only constructor so a producer cannot bind an operand by position."""
    return Operand(role=role, mention_id=mention_id, quantity=quantity, result_ref=result_ref)


def graph_summary(graph: RequestGraph) -> dict[str, Any]:
    """Structural, TEXT-FREE summary safe for telemetry: counts, states and kinds only."""
    states: dict[str, int] = {}
    for s in graph.slots:
        states[s.state.value] = states.get(s.state.value, 0) + 1
    return {
        "shape": graph.shape.value,
        "request_count": len(graph.requests),
        "unresolved_request_count": sum(1 for r in graph.requests if r.unresolved),
        "slot_count": len(graph.slots),
        "slot_states": dict(sorted(states.items())),
        "mention_count": len(graph.mentions),
        "constraint_kinds": sorted({c.kind.value for c in graph.constraints}),
        "prohibition_count": len(graph.prohibitions),
        "retraction_count": len(graph.retractions),
        "dependency_kinds": sorted({d.kind.value for d in graph.dependencies}),
        "presentation": graph.presentation.fmt.value if graph.presentation is not None else "",
        "uncovered_count": len(graph.uncovered_source),
    }


__all__ = [
    "GraphBuildError",
    "RequestGraphBuilder",
    "content_tokens",
    "graph_summary",
    "operand",
]
