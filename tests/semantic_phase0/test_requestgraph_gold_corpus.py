"""The gold RequestGraph corpus: frozen on every field, valid, self-consistent, and hostile to the
review's exploit. Also re-runs the corruption attacks ON THE GOLD CASES themselves so the evaluator
is proven against the corpus it will score, not only against a synthetic fixture."""
from __future__ import annotations

import dataclasses
import importlib.util
import pathlib
import sys

import pytest

from core.semantic.graph_diff import AXES, compare_graphs
from core.semantic.graph_serialization import graph_digest, graph_from_dict, graph_to_dict
from core.semantic.request_graph import (
    Constraint,
    ConstraintKind,
    DependencyEdge,
    InterpretationState,
    OutputFormat,
    PresentationContract,
    RequestGraph,
    Slot,
)
from core.semantic.types import RequestShape
from ops import semantic_requestgraph_gold as gold_corpus

_V2_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "semantic_resolver_differential.py"


def _v2_corpus():
    spec = importlib.util.spec_from_file_location("semantic_resolver_differential_v2", _V2_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # dataclass decoration resolves the owning module by name
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module.CORPUS


# -- freeze + validity --------------------------------------------------------------


def test_gold_digest_is_frozen() -> None:
    assert gold_corpus.gold_digest() == gold_corpus.GOLD_DIGEST, (
        "the gold corpus changed. That is allowed only as a deliberate diff whose commit message says "
        "which annotation changed and why; then re-pin GOLD_DIGEST. Never tune gold to a producer."
    )


def test_texts_are_the_v2_development_corpus_verbatim() -> None:
    v2 = {c["id"]: c for c in _v2_corpus()}
    assert [c.id for c in gold_corpus.gold_cases()] == list(v2)
    for case in gold_corpus.gold_cases():
        assert case.text == v2[case.id]["text"], case.id
        assert case.cls == v2[case.id]["cls"], case.id


@pytest.mark.parametrize("case_id", [c.id for c in gold_corpus.gold_cases()])
def test_every_gold_graph_is_valid_and_conserves_source(case_id: str) -> None:
    g = gold_corpus.gold_graph(case_id)
    assert g.uncovered_source == (), (case_id, g.uncovered_source)
    assert not any(r.unresolved for r in g.requests), case_id
    assert all(r.span is not None for r in g.requests), case_id
    assert all(r.state is InterpretationState.RESOLVED for r in g.requests), case_id
    for slot in g.slots:
        assert slot.expected, (case_id, slot.id)
    # Deterministic: a second construction is byte-identical.
    assert graph_digest(g) == graph_digest(gold_corpus.gold_graph(case_id))
    assert graph_from_dict(graph_to_dict(g, include_text=True)) == g


def test_gold_digests_are_distinct_across_cases() -> None:
    digests = [graph_digest(g) for g in gold_corpus.gold_graphs().values()]
    assert len(set(digests)) == len(digests)


def test_v2_labels_are_consistent_with_the_gold_graphs() -> None:
    """The count-only V2 labels must be DERIVABLE from the gold (they were never wrong, only thin)."""
    v2 = {c["id"]: c for c in _v2_corpus()}
    for case_id, g in gold_corpus.gold_graphs().items():
        label = v2[case_id]
        # V2 counted ANSWERABLE slots: a request the turn itself retracted is represented in the
        # gold (the Retraction supersedes it) but was never an intent to answer.
        superseded = {rt.supersedes for rt in g.retractions}
        live = [r for r in g.requests if r.id not in superseded]
        assert (len(live) > 0) == (label["intents"] > 0), case_id
        has_forbid = any(c.kind in {ConstraintKind.RETRIEVAL_FORBIDDEN, ConstraintKind.TOOL_FORBIDDEN} for c in g.constraints)
        assert bool(g.prohibitions or g.retractions) == bool(label["prohibition"]), case_id
        if label["prohibition"] and not g.retractions:
            assert has_forbid, case_id
        ambiguous = any(s.state is InterpretationState.AMBIGUOUS for s in g.slots) or g.shape is RequestShape.CONDITIONAL
        assert ambiguous == bool(label["ambiguous"]), case_id


def test_shapes_are_the_expected_ones() -> None:
    shapes = {cid: g.shape for cid, g in gold_corpus.gold_graphs().items()}
    assert shapes["us1"] is RequestShape.CONDITIONAL
    assert shapes["pr2"] is RequestShape.MID_TURN_CORRECTION
    assert shapes["pr1"] is RequestShape.UNKNOWN
    assert shapes["mi3"] is RequestShape.MULTI_CLAUSE
    assert shapes["sd1"] is RequestShape.SINGLE


# -- correctness vs invention -------------------------------------------------------


def test_gold_scored_against_itself_is_strict_20_of_20() -> None:
    results = {cid: compare_graphs(g, gold_corpus.gold_graph(cid)) for cid, g in gold_corpus.gold_graphs().items()}
    failed = {cid: r.failed_axes() for cid, r in results.items() if not r.whole_turn_correct}
    assert not failed, failed
    assert len(results) == 20


@pytest.mark.parametrize("case_id", [c.id for c in gold_corpus.gold_cases()])
def test_invented_counterpart_with_matching_counts_fails_hard(case_id: str) -> None:
    gold = gold_corpus.gold_graph(case_id)
    cand = gold_corpus.invented_counterpart(case_id)
    assert len(cand.requests) == len(gold.requests)
    assert len(cand.slots) == len(gold.slots)
    cmp = compare_graphs(gold, cand)
    assert not cmp.whole_turn_correct
    if gold.requests:
        assert "slot_coverage" in cmp.failed_axes() and "request_meaning" in cmp.failed_axes()
        assert cmp.missing_requests and cmp.invented_requests
    else:
        assert "prohibitions" in cmp.failed_axes()
    assert "source_binding" in cmp.failed_axes()


# -- corruption attacks applied to the gold cases themselves -------------------------


def _slot_replace(g: RequestGraph, index: int, **changes) -> RequestGraph:
    slots = list(g.slots)
    slots[index] = dataclasses.replace(slots[index], **changes)
    return dataclasses.replace(g, slots=tuple(slots))


def _drop_slot(g: RequestGraph, sid: str) -> RequestGraph:
    return dataclasses.replace(
        g,
        slots=tuple(s for s in g.slots if s.id != sid),
        requests=tuple(dataclasses.replace(r, slot_ids=tuple(x for x in r.slot_ids if x != sid)) for r in g.requests),
    )


def _drop_constraints(g: RequestGraph, kind: ConstraintKind) -> RequestGraph:
    return dataclasses.replace(g, constraints=tuple(c for c in g.constraints if c.kind is not kind))


def _add_constraint(g: RequestGraph, kind: ConstraintKind, scope=()) -> RequestGraph:
    return dataclasses.replace(g, constraints=(*g.constraints, Constraint(id="c-attack", kind=kind, scope=scope)))  # type: ignore[arg-type]


def _swap_first_two_operand_roles(g: RequestGraph, index: int) -> RequestGraph:
    slot = g.slots[index]
    a, b_ = slot.operands[0], slot.operands[1]
    return _slot_replace(g, index, operands=(dataclasses.replace(a, role=b_.role), dataclasses.replace(b_, role=a.role), *slot.operands[2:]))


def _requantify(g: RequestGraph, index: int, **q) -> RequestGraph:
    slot = g.slots[index]
    ops = list(slot.operands)
    for i, op in enumerate(ops):
        if op.quantity is not None:
            ops[i] = dataclasses.replace(op, quantity=dataclasses.replace(op.quantity, **q))
            break
    return _slot_replace(g, index, operands=tuple(ops))


def _rebind_mention_occurrence(g: RequestGraph, mention_id: str, occurrence: int) -> RequestGraph:
    target = next(m for m in g.mentions if m.id == mention_id)
    spans = g.canonical.find_all(target.surface)
    return dataclasses.replace(
        g, mentions=tuple(dataclasses.replace(m, span=spans[occurrence], occurrence=occurrence) if m.id == mention_id else m for m in g.mentions)
    )


ATTACKS = [
    ("am1", "collapsed ambiguity", lambda g: _slot_replace(g, 0, state=InterpretationState.RESOLVED, ambiguity=None), "ambiguity"),
    ("am1", "narrowed alternatives", lambda g: _slot_replace(g, 0, ambiguity=dataclasses.replace(g.slots[0].ambiguity, alternatives=("gold",))), "ambiguity"),
    ("pr2", "missing retraction", lambda g: dataclasses.replace(g, retractions=()), "retractions"),
    ("pr2", "wrong retraction target", lambda g: dataclasses.replace(g, retractions=(dataclasses.replace(g.retractions[0], supersedes=None),)), "retraction_target"),
    ("us1", "missing conditional dependency", lambda g: dataclasses.replace(g, dependencies=()), "dependencies"),
    ("us1", "wrong time-constraint scope", lambda g: dataclasses.replace(g, constraints=tuple(dataclasses.replace(c, scope=("req-1",)) if c.kind is ConstraintKind.TIME else c for c in g.constraints)), "constraints"),
    ("mi2", "invented dependency", lambda g: dataclasses.replace(g, dependencies=(DependencyEdge(id="d-x", from_request="req-1", to_request="req-0"),)), "dependencies"),  # type: ignore[arg-type]
    ("mi3", "missing value dependency", lambda g: dataclasses.replace(g, dependencies=()), "dependencies"),
    ("mi3", "wrong value reference", lambda g: dataclasses.replace(g, dependencies=(dataclasses.replace(g.dependencies[0], value_ref="slot-2"),)), "dependency_target"),  # type: ignore[arg-type]
    ("mi3", "missing slot", lambda g: _drop_slot(g, "slot-3"), "slot_coverage"),
    ("op2", "missing slot", lambda g: _drop_slot(g, "slot-2"), "slot_coverage"),
    ("op2", "extra slot", lambda g: dataclasses.replace(g, slots=(*g.slots, Slot(id="slot-x", request_id="req-0", expected="palladium spot price")), requests=(dataclasses.replace(g.requests[0], slot_ids=(*g.requests[0].slot_ids, "slot-x")),)), "slot_coverage"),  # type: ignore[arg-type]
    ("rt1", "swapped operands", lambda g: _swap_first_two_operand_roles(g, 0), "operands"),
    ("rt1", "swapped operands (roles)", lambda g: _swap_first_two_operand_roles(g, 0), "roles"),
    ("rt2", "swapped operands", lambda g: _swap_first_two_operand_roles(g, 1), "operands"),
    ("fx1", "wrong currency", lambda g: _requantify(g, 0, currency="GBP"), "quantities"),
    ("fx1", "incorrect quantity", lambda g: _requantify(g, 0, exact="100"), "quantities"),
    ("lpg1", "wrong token", lambda g: _requantify(g, 1, asset="BTC"), "quantities"),
    ("rt1", "wrong unit", lambda g: _requantify(g, 0, unit="kg"), "quantities"),
    ("sd1", "false retrieval obligation", lambda g: _add_constraint(g, ConstraintKind.RETRIEVAL_REQUIRED, scope=("req-0",)), "retrieval_required"),
    ("sl1", "missing retrieval obligation", lambda g: _drop_constraints(g, ConstraintKind.RETRIEVAL_REQUIRED), "retrieval_required"),
    # A recency cue IMPLIES retrieval, so on lu2 the obligation is only missing once both are gone.
    ("lu2", "missing retrieval obligation", lambda g: _drop_constraints(_drop_constraints(g, ConstraintKind.RETRIEVAL_REQUIRED), ConstraintKind.FRESHNESS), "retrieval_required"),
    ("lu2", "dropped freshness cue", lambda g: _drop_constraints(g, ConstraintKind.FRESHNESS), "constraints"),
    ("rt2", "dropped freshness cue", lambda g: _drop_constraints(g, ConstraintKind.FRESHNESS), "constraints"),
    ("pr1", "tool required when prohibited", lambda g: _add_constraint(g, ConstraintKind.RETRIEVAL_REQUIRED), "tool_conflict"),
    ("pr1", "missing prohibition", lambda g: dataclasses.replace(g, prohibitions=(g.prohibitions[0],)), "prohibitions"),
    ("pr1", "dropped retrieval prohibition", lambda g: _drop_constraints(g, ConstraintKind.RETRIEVAL_FORBIDDEN), "retrieval_forbidden"),
    ("sl2", "wrong presentation contract", lambda g: dataclasses.replace(g, presentation=PresentationContract(fmt=OutputFormat.JSON)), "presentation"),
    ("mi1", "invented certainty", lambda g: _slot_replace(g, 1, state=InterpretationState.RESOLVED, reason=""), "interpretation_state"),
    ("sd2", "false UNKNOWN", lambda g: _slot_replace(g, 0, state=InterpretationState.UNKNOWN_MEANING, reason="?"), "interpretation_state"),
    ("sl1", "false NEEDS_INPUT", lambda g: _slot_replace(g, 0, state=InterpretationState.NEEDS_INPUT), "interpretation_state"),
    ("pq1", "wrong entity occurrence", lambda g: _rebind_mention_occurrence(g, "m-1", 0), "entity_identity"),
    ("mi2", "wrong entity", lambda g: dataclasses.replace(g, mentions=tuple(dataclasses.replace(m, entity_key="Tesla Motors Club") if m.id == "m-1" else m for m in g.mentions)), "entity_identity"),
    ("mi1", "wrong entity (operand)", lambda g: _slot_replace(g, 1, operands=(dataclasses.replace(g.slots[1].operands[0], mention_id="m-0"),)), "operands"),
    ("pr2", "dropped source stretch", lambda g: dataclasses.replace(g, retractions=(dataclasses.replace(g.retractions[0], span=None),)), "source_binding"),
]


@pytest.mark.parametrize(("case_id", "attack", "mutate", "axis"), ATTACKS, ids=[f"{c}:{a}" for c, a, _m, _x in ATTACKS])
def test_corruption_of_a_gold_case_fails_its_axis(case_id: str, attack: str, mutate, axis: str) -> None:
    assert axis in AXES
    gold = gold_corpus.gold_graph(case_id)
    mutant = mutate(gold)
    assert mutant != gold, "the attack must change the graph"
    cmp = compare_graphs(gold, mutant)
    assert axis in cmp.failed_axes(), (case_id, attack, cmp.failed_axes())
    assert not cmp.whole_turn_correct


def test_swapped_operands_on_gold_pass_nothing_by_symmetry() -> None:
    """A swap is not 'the same multiset': operands AND roles must both fail on rt1."""
    gold = gold_corpus.gold_graph("rt1")
    cmp = compare_graphs(gold, _swap_first_two_operand_roles(gold, 0))
    assert {"operands", "roles"} <= set(cmp.failed_axes())
