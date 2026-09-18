"""Mutation-test the acceptance scorer (Differential V3). MANDATORY per the 2026-09-08 review.

One test per deliberate corruption in the review's list. Every mutation of a correct candidate must
make its NAMED axis fail AND break strict whole-turn correctness; a correct candidate -- even with
different ids -- must pass every axis. Above all, the review's exact exploit (unrelated invented
questions with a matching count) must FAIL HARD.
"""
from __future__ import annotations

import dataclasses

from core.semantic.canonical_text import CanonicalText
from core.semantic.graph_builder import RequestGraphBuilder, operand
from core.semantic.graph_diff import AXES, compare_graphs
from core.semantic.request_graph import (
    Ambiguity,
    Constraint,
    ConstraintKind,
    DependencyEdge,
    DependencyKind,
    InterpretationState,
    OutputFormat,
    PresentationContract,
    Quantity,
    Request,
    RequestGraph,
    SemanticRole,
    Slot,
)

TEXT = (
    "look up the price of gold in London today, then how much silver can I buy with 1 BTC, "
    "then convert that to EUR, do not use twitter for that, and the platinum price, "
    "actually skip the platinum question"
)


def _gold(prefix: str = "") -> RequestGraph:
    b = RequestGraphBuilder(TEXT, turn_id="t", id_prefix=prefix)
    c = b.canonical
    r0 = b.add_request("look up the price of gold in London today")
    r1 = b.add_request("how much silver can I buy with 1 BTC")
    r2 = b.add_request("convert that to EUR")
    r3 = b.add_request("the platinum price")
    m_gold = b.add_mention("gold", within=b.request_span(r0), kind="asset", entity_key="gold")
    m_silver = b.add_mention("silver", within=b.request_span(r1), kind="asset", entity_key="silver")
    m_btc = b.add_mention("BTC", within=b.request_span(r1), kind="asset", entity_key="BTC")
    m_eur = b.add_mention("EUR", within=b.request_span(r2), kind="currency", entity_key="EUR")
    m_plat = b.add_mention("platinum", within=b.request_span(r3), kind="asset", entity_key="platinum")
    s0 = b.add_slot(r0, expected="gold spot price", operands=(operand(SemanticRole.SUBJECT, mention_id=m_gold),))
    s1 = b.add_slot(
        r1,
        expected="amount of silver",
        operands=(
            operand(SemanticRole.TARGET, mention_id=m_silver),
            operand(SemanticRole.PAYMENT, mention_id=m_btc, quantity=Quantity(raw="1 BTC", exact="1", asset="BTC")),
        ),
    )
    b.add_slot(
        r2,
        expected="silver value in EUR",
        operands=(operand(SemanticRole.RESULT_REF, result_ref=s1), operand(SemanticRole.TARGET, mention_id=m_eur)),
    )
    b.add_slot(r3, expected="platinum spot price", operands=(operand(SemanticRole.SUBJECT, mention_id=m_plat),))
    b.add_constraint(ConstraintKind.RETRIEVAL_REQUIRED, scope=(r0, r1, r3))
    b.add_constraint(ConstraintKind.LOCATION, detail="London", span=c.find("London"), scope=(r0,))
    b.add_constraint(ConstraintKind.TIME, detail="today", span=c.find("today"), scope=(r0,))
    b.add_prohibition("twitter", scope=(r1,), span=c.find("do not use twitter for that"))
    b.add_retraction("the platinum question", supersedes=r3, span=c.find("actually skip the platinum question"))
    b.add_dependency(r1, r0, kind=DependencyKind.ORDERING)
    b.add_dependency(r2, r1, kind=DependencyKind.VALUE, value_ref=s1)
    b.set_presentation(fmt=OutputFormat.TABLE)
    g = b.build()
    assert g.uncovered_source == (), g.uncovered_source
    assert s0 == f"{prefix}slot-0"
    return g


def _tool_forbidden_gold() -> RequestGraph:
    """The base gold plus: the EUR conversion (req-2) must be done without any tool."""
    g = _gold()
    forbid = Constraint(id="c-nt", kind=ConstraintKind.TOOL_FORBIDDEN, detail="no tools for the conversion", scope=("req-2",))  # type: ignore[arg-type]
    return dataclasses.replace(g, constraints=(*g.constraints, forbid))


def _slot(g: RequestGraph, sid: str) -> Slot:
    return next(s for s in g.slots if s.id == sid)


def _replace_slot(g: RequestGraph, sid: str, **changes) -> RequestGraph:
    return dataclasses.replace(g, slots=tuple(dataclasses.replace(s, **changes) if s.id == sid else s for s in g.slots))


def _fails(mutant: RequestGraph, axis: str) -> bool:
    assert axis in AXES, axis
    cmp = compare_graphs(_gold(), mutant)
    return (axis in cmp.failed_axes()) and (not cmp.whole_turn_correct)


# -- controls: a correct candidate passes ------------------------------------


def test_identical_candidate_passes_every_axis() -> None:
    cmp = compare_graphs(_gold(), _gold())
    assert cmp.whole_turn_correct, cmp.failed_axes()
    assert set(a.axis for a in cmp.axes) == set(AXES)


def test_cross_id_equivalent_candidate_aligns_and_passes() -> None:
    """Different ids, same meaning/spans -> alignment is by source position, not id."""
    cmp = compare_graphs(_gold(), _gold(prefix="x-"))
    assert cmp.whole_turn_correct, cmp.failed_axes()
    assert dict(cmp.aligned_pairs) == {"req-0": "x-req-0", "req-1": "x-req-1", "req-2": "x-req-2", "req-3": "x-req-3"}


def test_candidate_may_account_for_more_source_than_gold() -> None:
    """Superset coverage is fine; only dropped source fails source_binding."""
    g = _gold()
    extra = Constraint(id="c-extra", kind=ConstraintKind.USER_RESTRICTION, detail="", span=g.canonical.find("then"))  # type: ignore[arg-type]
    cand = dataclasses.replace(g, constraints=(*g.constraints, extra))
    cmp = compare_graphs(g, cand)
    assert "source_binding" not in cmp.failed_axes()


# -- THE exploit: unrelated invented questions, matching count ---------------


def test_unrelated_invented_questions_with_matching_count_fail_hard() -> None:
    b = RequestGraphBuilder("completely unrelated question one and also another question two and three and four", turn_id="t")
    for text in ("completely unrelated question one", "another question two", "three", "four"):
        rid = b.add_request(text)
        b.add_slot(rid)
    cand = b.build(conserve_source=False)
    assert len(cand.requests) == len(_gold().requests) and len(cand.slots) == len(_gold().slots)
    cmp = compare_graphs(_gold(), cand)
    assert not cmp.whole_turn_correct
    for axis in ("slot_coverage", "request_meaning", "source_binding"):
        assert axis in cmp.failed_axes()
    assert cmp.missing_requests and cmp.invented_requests


def test_same_text_but_invented_reading_of_it_fails_hard() -> None:
    """Same canonical, same counts, but every request bound to the wrong stretch and empty slots."""
    g = _gold()
    b = RequestGraphBuilder(TEXT, turn_id="t")
    for text in ("look up", "then how", "then convert", "and the"):
        rid = b.add_request(text)
        b.add_slot(rid)
    cand = b.build(conserve_source=False)
    cmp = compare_graphs(g, cand)
    assert not cmp.whole_turn_correct
    assert "request_meaning" in cmp.failed_axes() and "source_binding" in cmp.failed_axes()


# -- one mutation per named corruption, caught by its intended axis ----------


def test_missing_slot_fails_coverage() -> None:
    g = _gold()
    mutant = dataclasses.replace(
        g,
        slots=tuple(s for s in g.slots if s.id != "slot-3"),
        requests=tuple(dataclasses.replace(r, slot_ids=()) if r.id == "req-3" else r for r in g.requests),
    )
    assert _fails(mutant, "slot_coverage")


def test_extra_slot_fails_coverage() -> None:
    g = _gold()
    extra = Slot(id="slot-x", request_id="req-0", expected="gold 24h change")  # type: ignore[arg-type]
    mutant = dataclasses.replace(
        g,
        slots=(*g.slots, extra),
        requests=tuple(dataclasses.replace(r, slot_ids=(*r.slot_ids, "slot-x")) if r.id == "req-0" else r for r in g.requests),
    )
    assert _fails(mutant, "slot_coverage")


def test_missing_request_fails_coverage_and_source_binding() -> None:
    g = _gold()
    mutant = dataclasses.replace(
        g,
        requests=tuple(r for r in g.requests if r.id != "req-3"),
        slots=tuple(s for s in g.slots if s.id != "slot-3"),
        retractions=(dataclasses.replace(g.retractions[0], supersedes=None),),
        constraints=tuple(dataclasses.replace(c, scope=tuple(x for x in c.scope if x != "req-3")) for c in g.constraints),
    )
    assert _fails(mutant, "slot_coverage") and _fails(mutant, "source_binding")


def test_swapped_request_identity_fails_request_meaning() -> None:
    """Spans kept, but the slots (meaning) exchanged between two requests."""
    g = _gold()
    s0, s3 = _slot(g, "slot-0"), _slot(g, "slot-3")
    swapped = tuple(
        dataclasses.replace(s0, request_id="req-3") if s.id == "slot-0"
        else dataclasses.replace(s3, request_id="req-0") if s.id == "slot-3"
        else s
        for s in g.slots
    )
    requests = tuple(
        dataclasses.replace(r, slot_ids=("slot-3",)) if r.id == "req-0"
        else dataclasses.replace(r, slot_ids=("slot-0",)) if r.id == "req-3"
        else r
        for r in g.requests
    )
    mutant = dataclasses.replace(g, slots=swapped, requests=requests)
    assert _fails(mutant, "request_meaning")


def test_wrong_entity_fails_operands_and_entity_identity() -> None:
    g = _gold()
    s1 = _slot(g, "slot-1")
    mutant = _replace_slot(g, "slot-1", operands=(dataclasses.replace(s1.operands[0], mention_id="m-0"), s1.operands[1]))  # gold, not silver
    assert _fails(mutant, "operands") and _fails(mutant, "entity_identity")


def test_wrong_entity_occurrence_fails_entity_identity() -> None:
    """Same surface, other occurrence: 'platinum' inside the retraction instead of the request."""
    g = _gold()
    second = g.canonical.find_all("platinum")[1]
    mutant = dataclasses.replace(
        g, mentions=tuple(dataclasses.replace(m, span=second, occurrence=1) if m.id == "m-4" else m for m in g.mentions)
    )
    assert _fails(mutant, "entity_identity")


def test_swapped_operands_fail_operands_and_roles() -> None:
    g = _gold()
    s1 = _slot(g, "slot-1")
    mutant = _replace_slot(
        g, "slot-1",
        operands=(
            dataclasses.replace(s1.operands[0], role=SemanticRole.PAYMENT),
            dataclasses.replace(s1.operands[1], role=SemanticRole.TARGET),
        ),
    )
    assert _fails(mutant, "operands") and _fails(mutant, "roles")


def test_incorrect_quantity_fails_quantities() -> None:
    g = _gold()
    s1 = _slot(g, "slot-1")
    mutant = _replace_slot(g, "slot-1", operands=(s1.operands[0], dataclasses.replace(s1.operands[1], quantity=Quantity(raw="2 BTC", exact="2", asset="BTC"))))
    assert _fails(mutant, "quantities")


def test_wrong_unit_fails_quantities() -> None:
    g = _gold()
    s1 = _slot(g, "slot-1")
    mutant = _replace_slot(g, "slot-1", operands=(s1.operands[0], dataclasses.replace(s1.operands[1], quantity=Quantity(raw="1 BTC", exact="1", unit="kg", asset="BTC"))))
    assert _fails(mutant, "quantities")


def test_wrong_currency_or_token_fails_quantities() -> None:
    g = _gold()
    s1 = _slot(g, "slot-1")
    mutant = _replace_slot(g, "slot-1", operands=(s1.operands[0], dataclasses.replace(s1.operands[1], quantity=Quantity(raw="1 BTC", exact="1", asset="ETH"))))
    assert _fails(mutant, "quantities")


def test_missing_prohibition_fails_prohibitions() -> None:
    assert _fails(dataclasses.replace(_gold(), prohibitions=()), "prohibitions")


def test_wrong_prohibition_scope_fails_prohibition_scope() -> None:
    g = _gold()
    mutant = dataclasses.replace(g, prohibitions=(dataclasses.replace(g.prohibitions[0], scope=("req-0",)),))  # type: ignore[arg-type]
    assert _fails(mutant, "prohibition_scope")


def test_missing_retraction_fails_retractions() -> None:
    assert _fails(dataclasses.replace(_gold(), retractions=()), "retractions")


def test_wrong_retraction_target_fails_retraction_target() -> None:
    g = _gold()
    mutant = dataclasses.replace(g, retractions=(dataclasses.replace(g.retractions[0], supersedes="req-0"),))  # type: ignore[arg-type]
    assert _fails(mutant, "retraction_target")


def test_missing_dependency_fails_dependencies() -> None:
    g = _gold()
    assert _fails(dataclasses.replace(g, dependencies=(g.dependencies[1],)), "dependencies")


def test_invented_dependency_fails_dependencies() -> None:
    g = _gold()
    extra = DependencyEdge(id="d-x", from_request="req-0", to_request="req-3", kind=DependencyKind.ORDERING)  # type: ignore[arg-type]
    assert _fails(dataclasses.replace(g, dependencies=(*g.dependencies, extra)), "dependencies")


def test_wrong_dependency_target_fails_dependency_target() -> None:
    g = _gold()
    rewired = dataclasses.replace(g.dependencies[1], to_request="req-0", value_ref="slot-0")  # type: ignore[arg-type]
    mutant = dataclasses.replace(g, dependencies=(g.dependencies[0], rewired))
    assert _fails(mutant, "dependency_target") and _fails(mutant, "dependencies")


def test_wrong_value_reference_alone_fails_dependency_target() -> None:
    g = _gold()
    wrong_ref = dataclasses.replace(g.dependencies[1], value_ref="slot-0")  # type: ignore[arg-type]
    mutant = dataclasses.replace(g, dependencies=(g.dependencies[0], wrong_ref))
    assert _fails(mutant, "dependency_target")


def test_collapsed_ambiguity_fails_ambiguity_and_state() -> None:
    c = CanonicalText.of("how much gold or silver with 1 btc")
    gold = RequestGraph(
        turn_id="t", canonical=c,  # type: ignore[arg-type]
        requests=(Request(id="r0", source_text=c.text, span=c.find(c.text), slot_ids=("s0",),  # type: ignore[arg-type]
                          state=InterpretationState.AMBIGUOUS),),
        slots=(Slot(id="s0", request_id="r0", state=InterpretationState.AMBIGUOUS,  # type: ignore[arg-type]
                    ambiguity=Ambiguity(alternatives=("gold", "silver"), needs_clarification=True)),),
    )
    collapsed = dataclasses.replace(
        gold,
        slots=(dataclasses.replace(gold.slots[0], state=InterpretationState.RESOLVED, ambiguity=None),),
        requests=(dataclasses.replace(gold.requests[0], state=InterpretationState.RESOLVED),),
    )
    cmp = compare_graphs(gold, collapsed)
    assert "ambiguity" in cmp.failed_axes() and "interpretation_state" in cmp.failed_axes()
    assert not cmp.whole_turn_correct
    narrowed = dataclasses.replace(
        gold, slots=(dataclasses.replace(gold.slots[0], ambiguity=Ambiguity(alternatives=("gold",))),)
    )
    assert "ambiguity" in compare_graphs(gold, narrowed).failed_axes()


def test_false_unknown_fails_interpretation_state() -> None:
    mutant = _replace_slot(_gold(), "slot-0", state=InterpretationState.UNKNOWN_MEANING, reason="cannot tell")
    assert _fails(mutant, "interpretation_state")


def test_invented_certainty_fails_interpretation_state() -> None:
    gold = _replace_slot(_gold(), "slot-0", state=InterpretationState.UNKNOWN_MEANING, reason="asset unclear")
    cand = _replace_slot(_gold(), "slot-0", state=InterpretationState.RESOLVED)
    cmp = compare_graphs(gold, cand)
    assert "interpretation_state" in cmp.failed_axes() and not cmp.whole_turn_correct


def test_false_needs_input_fails_interpretation_state() -> None:
    assert _fails(_replace_slot(_gold(), "slot-2", state=InterpretationState.NEEDS_INPUT), "interpretation_state")


def test_false_retrieval_obligation_fails_retrieval_required() -> None:
    g = _gold()
    widened = tuple(dataclasses.replace(c, scope=()) if c.kind is ConstraintKind.RETRIEVAL_REQUIRED else c for c in g.constraints)
    assert _fails(dataclasses.replace(g, constraints=widened), "retrieval_required")  # now also req-2


def test_missing_retrieval_obligation_fails_retrieval_required() -> None:
    g = _gold()
    dropped = tuple(c for c in g.constraints if c.kind is not ConstraintKind.RETRIEVAL_REQUIRED)
    assert _fails(dataclasses.replace(g, constraints=dropped), "retrieval_required")


def test_dropped_tool_forbidden_fails_retrieval_forbidden() -> None:
    gold = _tool_forbidden_gold()
    dropped = tuple(c for c in gold.constraints if c.kind is not ConstraintKind.TOOL_FORBIDDEN)
    cmp = compare_graphs(gold, dataclasses.replace(gold, constraints=dropped))
    assert "retrieval_forbidden" in cmp.failed_axes() and not cmp.whole_turn_correct
    assert compare_graphs(gold, gold).whole_turn_correct


def test_tool_required_where_prohibited_fails_tool_conflict() -> None:
    """Gold forbids tools on the EUR conversion; a candidate that demands retrieval there conflicts."""
    gold = _tool_forbidden_gold()
    cand = dataclasses.replace(
        _gold(),
        constraints=tuple(
            dataclasses.replace(c, scope=("req-0", "req-1", "req-2", "req-3")) if c.kind is ConstraintKind.RETRIEVAL_REQUIRED else c
            for c in _gold().constraints
        ),
    )
    cmp = compare_graphs(gold, cand)
    assert "tool_conflict" in cmp.failed_axes() and not cmp.whole_turn_correct
    # A whole-turn prohibition plus any retrieval demand is a conflict too.
    turn_forbidden = dataclasses.replace(
        gold, constraints=tuple(dataclasses.replace(c, scope=()) if c.kind is ConstraintKind.TOOL_FORBIDDEN else c for c in gold.constraints)
    )
    assert "tool_conflict" in compare_graphs(turn_forbidden, cand).failed_axes()


def test_wrong_presentation_contract_fails_presentation() -> None:
    assert _fails(dataclasses.replace(_gold(), presentation=PresentationContract(fmt=OutputFormat.JSON)), "presentation")
    assert _fails(dataclasses.replace(_gold(), presentation=PresentationContract(fmt=OutputFormat.TABLE, literal_output=True)), "presentation")


def test_wrong_time_or_location_constraint_fails_constraints() -> None:
    g = _gold()
    moved = tuple(dataclasses.replace(c, scope=("req-1",)) if c.kind is ConstraintKind.LOCATION else c for c in g.constraints)
    assert _fails(dataclasses.replace(g, constraints=moved), "constraints")
    dropped = tuple(c for c in g.constraints if c.kind is not ConstraintKind.TIME)
    assert _fails(dataclasses.replace(g, constraints=dropped), "constraints")


def test_dropped_source_stretch_fails_source_binding() -> None:
    """The candidate keeps every request but un-binds the retraction text: that source vanished."""
    g = _gold()
    mutant = dataclasses.replace(g, retractions=(dataclasses.replace(g.retractions[0], span=None),))
    assert _fails(mutant, "source_binding")


def test_a_graph_over_a_different_text_aligns_to_nothing() -> None:
    other = _gold()
    b = RequestGraphBuilder("the weather in Oslo", turn_id="t")
    b.add_slot(b.add_request("the weather in Oslo"))
    cmp = compare_graphs(other, b.build())
    assert cmp.missing_requests == tuple(r.id for r in other.requests)
    assert "source_binding" in cmp.failed_axes() and not cmp.whole_turn_correct


def test_report_dict_names_every_axis() -> None:
    report = compare_graphs(_gold(), _gold()).to_dict()
    assert report["whole_turn_correct"] is True
    assert set(report["axes"]) == set(AXES)
