"""Slot conservation: every frozen SlotID terminates exactly once, and ANSWERED means content.

The Astra review's core completeness law. Length equality (``ResolutionOutcome`` proposals ==
admissions) is a useful LOCAL invariant but does not establish ``REQUESTED SLOTS == TERMINAL SLOT
RESULTS``: a clause can hide several answer slots, a slot can vanish between planning and
publication, and an admitted prerequisite is not an executed answer. This layer enforces the strong
law on the RequestGraph's frozen slot set:

    frozen SlotID set  ==  terminal SlotID-result set     (ID-set equality, NOT count equality)
    every SlotID has exactly ONE terminal result          (uniqueness)
    a result marked ANSWERED carries real answer content  (no empty 'answered')

It is deliberately independent of how results were produced (heuristic demand records, conductor
obligations, the model path) — a caller adapts its own outcomes into ``SlotResult`` rows keyed by
the graph's stable SlotIDs, and this refuses to call a turn complete unless every obligation
terminated visibly. Publication conservation (a visible disposition per slot in the rendered answer)
builds on this and is a separate layer.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from core.semantic.request_graph import RequestGraph, SlotId, TerminalDisposition


class SlotConservationError(ValueError):
    """A turn was declared complete while a SlotID was missing, duplicated, invented, or an ANSWERED
    slot had no content. Fail-closed: incompleteness must never become permanently immutable."""


@dataclass(frozen=True)
class SlotResult:
    """One terminal outcome for one frozen SlotID. ``content`` is required (non-empty) iff ANSWERED.

    ``turn_id``/``graph_digest`` bind the result to the graph it answers. A local SlotId ("u1") is
    an alias that repeats in every turn; a result carrying another turn's binding is FOREIGN and
    can never satisfy this graph, however well the local ids happen to match.
    """

    slot_id: SlotId
    disposition: TerminalDisposition
    content: str = ""
    detail: str = ""
    turn_id: str = ""
    graph_digest: str = ""


@dataclass(frozen=True)
class Reconciliation:
    conserved: bool
    missing: tuple[SlotId, ...] = field(default_factory=tuple)                 # frozen slots with no result
    invented: tuple[SlotId, ...] = field(default_factory=tuple)               # results for unknown slots
    duplicated: tuple[SlotId, ...] = field(default_factory=tuple)             # slots with >1 result
    answered_without_content: tuple[SlotId, ...] = field(default_factory=tuple)
    foreign: tuple[SlotId, ...] = field(default_factory=tuple)                # results bound to another graph
    unbound: tuple[SlotId, ...] = field(default_factory=tuple)                # results with no binding (when required)
    results: tuple[SlotResult, ...] = field(default_factory=tuple)

    def violations(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.missing:
            out.append(f"missing terminal result for slots: {list(self.missing)}")
        if self.invented:
            out.append(f"terminal result for unknown slots: {list(self.invented)}")
        if self.duplicated:
            out.append(f"more than one terminal result for slots: {list(self.duplicated)}")
        if self.answered_without_content:
            out.append(f"ANSWERED without content for slots: {list(self.answered_without_content)}")
        if self.foreign:
            out.append(f"results bound to another turn/graph for slots: {list(self.foreign)}")
        if self.unbound:
            out.append(f"results carry no graph binding for slots: {list(self.unbound)}")
        return tuple(out)


def reconcile(
    graph: RequestGraph, results: tuple[SlotResult, ...], *, require_binding: bool = False
) -> Reconciliation:
    """Compute the conservation verdict. Never raises — it reports. ``require_conserved`` is the
    fail-closed gate a finalization boundary calls.

    A result that names another turn or another graph digest is FOREIGN: it is excluded from the
    terminal set (so its slot reads missing) and reported. With ``require_binding`` a result that
    carries no binding at all is likewise excluded -- the runtime boundary passes bound results;
    only pure unit fixtures may leave the binding empty."""
    from core.semantic.graph_serialization import graph_digest

    expected_digest = graph_digest(graph)
    expected_turn = str(graph.turn_id or "")
    foreign: list[SlotId] = []
    unbound: list[SlotId] = []
    admitted: list[SlotResult] = []
    for r in results:
        bound_turn = str(r.turn_id or "")
        bound_digest = str(r.graph_digest or "")
        if (bound_turn and bound_turn != expected_turn) or (bound_digest and bound_digest != expected_digest):
            foreign.append(r.slot_id)
            continue
        if require_binding and not (bound_turn and bound_digest):
            unbound.append(r.slot_id)
            continue
        admitted.append(r)
    frozen = graph.slot_ids
    counts = Counter(r.slot_id for r in admitted)
    result_ids = set(counts)
    missing = tuple(sorted(frozen - result_ids))
    invented = tuple(sorted(result_ids - frozen))
    duplicated = tuple(sorted(sid for sid, n in counts.items() if n > 1))
    answered_without_content = tuple(sorted(
        r.slot_id for r in admitted
        if r.disposition is TerminalDisposition.ANSWERED and not str(r.content or "").strip()
    ))
    conserved = not (missing or invented or duplicated or answered_without_content or foreign or unbound)
    return Reconciliation(
        conserved=conserved,
        missing=missing,
        invented=invented,
        duplicated=duplicated,
        answered_without_content=answered_without_content,
        foreign=tuple(sorted(set(foreign))),
        unbound=tuple(sorted(set(unbound))),
        results=tuple(results),
    )


def require_conserved(
    graph: RequestGraph, results: tuple[SlotResult, ...], *, require_binding: bool = False
) -> Reconciliation:
    """Fail-closed: raise unless every frozen SlotID terminated exactly once (ANSWERED with content).
    A finalization/publication boundary calls this before a turn may be declared done."""
    reconciliation = reconcile(graph, results, require_binding=require_binding)
    if not reconciliation.conserved:
        raise SlotConservationError("; ".join(reconciliation.violations()))
    return reconciliation


__all__ = [
    "Reconciliation",
    "SlotConservationError",
    "SlotResult",
    "reconcile",
    "require_conserved",
]
