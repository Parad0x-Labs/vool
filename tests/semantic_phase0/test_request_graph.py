"""RequestGraph v1: stable identity, source conservation, distinct states, referential integrity.

Pure data-and-invariant tests — no model, no network. They pin the laws the Astra redesign requires
before anything downstream may depend on the graph.
"""
from __future__ import annotations

import dataclasses

import pytest

from core.semantic.canonical_text import CanonicalText
from core.semantic.request_graph import (
    Ambiguity,
    Constraint,
    ConstraintKind,
    DependencyEdge,
    DependencyKind,
    InterpretationState,
    Mention,
    Operand,
    Prohibition,
    Quantity,
    Request,
    RequestGraph,
    RequestGraphError,
    Retraction,
    SemanticRole,
    Slot,
    TerminalDisposition,
)

TEXT = "what is the price of gold and how much silver can I buy with 1 BTC"


def _canon() -> CanonicalText:
    return CanonicalText.of(TEXT)


def _valid_graph() -> RequestGraph:
    c = _canon()
    gold = c.find("gold", kind="asset")
    silver = c.find("silver", kind="asset")
    btc = c.find("BTC", kind="asset")
    return RequestGraph(
        turn_id="turn-0",  # type: ignore[arg-type]
        canonical=c,
        requests=(
            Request(id="req-0", source_text="the price of gold", slot_ids=("slot-0",),  # type: ignore[arg-type]
                    state=InterpretationState.RESOLVED),
            Request(id="req-1", source_text="how much silver can I buy with 1 BTC",  # type: ignore[arg-type]
                    slot_ids=("slot-1",), state=InterpretationState.RESOLVED),
        ),
        slots=(
            Slot(id="slot-0", request_id="req-0", expected="gold spot price",  # type: ignore[arg-type]
                 state=InterpretationState.RESOLVED,
                 operands=(Operand(role=SemanticRole.SUBJECT, mention_id="m-gold"),)),  # type: ignore[arg-type]
            Slot(id="slot-1", request_id="req-1", expected="amount of silver",  # type: ignore[arg-type]
                 state=InterpretationState.RESOLVED,
                 operands=(
                     Operand(role=SemanticRole.TARGET, mention_id="m-silver"),  # type: ignore[arg-type]
                     Operand(role=SemanticRole.PAYMENT, mention_id="m-btc",  # type: ignore[arg-type]
                             quantity=Quantity(raw="1 BTC", exact="1", asset="BTC")),
                 )),
        ),
        mentions=(
            Mention(id="m-gold", span=gold, surface="gold", role=SemanticRole.SUBJECT),  # type: ignore[arg-type]
            Mention(id="m-silver", span=silver, surface="silver", role=SemanticRole.TARGET),  # type: ignore[arg-type]
            Mention(id="m-btc", span=btc, surface="BTC", role=SemanticRole.PAYMENT),  # type: ignore[arg-type]
        ),
    )


def test_a_valid_graph_constructs_and_exposes_its_slot_set() -> None:
    g = _valid_graph()
    assert g.slot_ids == frozenset({"slot-0", "slot-1"})
    assert len(g.slots_for("req-1")) == 1  # type: ignore[arg-type]
    assert g.is_source_conserved()


# -- law 1: unique stable identity -------------------------------------------


def test_duplicate_slot_ids_are_refused() -> None:
    g = _valid_graph()
    dup = dataclasses.replace(g.slots[1], id="slot-0", request_id="req-0")  # type: ignore[arg-type]
    with pytest.raises(RequestGraphError, match="duplicate slot ids"):
        dataclasses.replace(g, slots=(g.slots[0], dup),
                            requests=(dataclasses.replace(g.requests[0], slot_ids=("slot-0",)),))


# -- law 4: referential integrity --------------------------------------------


def test_slot_referencing_a_missing_request_is_refused() -> None:
    g = _valid_graph()
    orphan = dataclasses.replace(g.slots[0], request_id="req-does-not-exist")  # type: ignore[arg-type]
    with pytest.raises(RequestGraphError, match="missing request"):
        dataclasses.replace(g, slots=(orphan, g.slots[1]))


def test_slot_not_listed_by_its_request_is_refused() -> None:
    g = _valid_graph()
    # req-0 claims no slots, but slot-0 still points at it.
    with pytest.raises(RequestGraphError, match="not listed by its request"):
        dataclasses.replace(g, requests=(dataclasses.replace(g.requests[0], slot_ids=()), g.requests[1]))


def test_operand_result_ref_to_missing_slot_is_refused() -> None:
    g = _valid_graph()
    bad = dataclasses.replace(
        g.slots[1],
        operands=(Operand(role=SemanticRole.RESULT_REF, result_ref="slot-404"),),  # type: ignore[arg-type]
    )
    with pytest.raises(RequestGraphError, match="missing slot"):
        dataclasses.replace(g, slots=(g.slots[0], bad))


def test_self_referential_and_dangling_dependencies_are_refused() -> None:
    g = _valid_graph()
    with pytest.raises(RequestGraphError, match="self-referential"):
        dataclasses.replace(g, dependencies=(
            DependencyEdge(id="d0", from_request="req-0", to_request="req-0"),))  # type: ignore[arg-type]
    with pytest.raises(RequestGraphError, match="missing request endpoint"):
        dataclasses.replace(g, dependencies=(
            DependencyEdge(id="d1", from_request="req-1", to_request="req-404"),))  # type: ignore[arg-type]


def test_value_dependency_to_missing_slot_is_refused() -> None:
    g = _valid_graph()
    with pytest.raises(RequestGraphError, match="value_ref names missing slot"):
        dataclasses.replace(g, dependencies=(
            DependencyEdge(id="d2", from_request="req-1", to_request="req-0",  # type: ignore[arg-type]
                           kind=DependencyKind.VALUE, value_ref="slot-404"),))  # type: ignore[arg-type]


def test_prohibition_scope_to_missing_request_is_refused() -> None:
    g = _valid_graph()
    with pytest.raises(RequestGraphError, match="scope names missing request"):
        dataclasses.replace(g, prohibitions=(
            Prohibition(id="p0", target="web", scope=("req-404",)),))  # type: ignore[arg-type]


def test_a_scoped_constraint_binds_to_a_real_request() -> None:
    g = _valid_graph()
    ok = dataclasses.replace(g, constraints=(
        Constraint(id="c0", kind=ConstraintKind.RETRIEVAL_FORBIDDEN, detail="no web",
                   scope=("req-0",)),))  # type: ignore[arg-type]
    assert ok.constraints[0].kind is ConstraintKind.RETRIEVAL_FORBIDDEN
    with pytest.raises(RequestGraphError, match="scope names missing request"):
        dataclasses.replace(g, constraints=(
            Constraint(id="c1", kind=ConstraintKind.TOOL_FORBIDDEN, scope=("req-404",)),))  # type: ignore[arg-type]


def test_retraction_superseding_a_missing_target_is_refused() -> None:
    g = _valid_graph()
    with pytest.raises(RequestGraphError, match="supersedes missing"):
        dataclasses.replace(g, retractions=(
            Retraction(id="r0", target="the silver lookup", supersedes="req-404"),))  # type: ignore[arg-type]


# -- law 2: spans bind to THIS turn's text -----------------------------------


def test_a_span_from_another_text_is_refused() -> None:
    g = _valid_graph()
    other = CanonicalText.of("a completely different message about weather")
    foreign = other.find("weather", kind="topic")
    bad = dataclasses.replace(g.mentions[0], span=foreign)
    with pytest.raises(RequestGraphError, match="not bound to this turn"):
        dataclasses.replace(g, mentions=(bad, g.mentions[1], g.mentions[2]))


# -- law 3: distinct states, and occurrence identity -------------------------


def test_the_distinct_states_are_not_collapsed() -> None:
    # The Astra review's core naming requirement: these are separate members, not one 'unknown'.
    names = {s.value for s in InterpretationState}
    for required in ("unknown_meaning", "answerable_without_tool", "unsupported_capability",
                     "tool_forbidden", "retrieval_forbidden", "needs_input", "ambiguous", "unresolved"):
        assert required in names
    disp = {d.value for d in TerminalDisposition}
    for required in ("answered", "unknown", "unverified", "blocked", "denied", "needs_input",
                     "not_applicable", "failed", "cancelled", "superseded"):
        assert required in disp


def test_a_repeated_entity_has_distinct_mentions_with_distinct_spans() -> None:
    c = CanonicalText.of("weather in Paris and the population of Paris")
    first = c.find("Paris", kind="location")
    second = c.find("Paris", start=first.end, kind="location")
    assert first.start != second.start  # occurrence identity, not a global first-match dedupe
    g = RequestGraph(
        turn_id="t",  # type: ignore[arg-type]
        canonical=c,
        requests=(Request(id="r0", source_text=c.text, slot_ids=("s0",)),),  # type: ignore[arg-type]
        slots=(Slot(id="s0", request_id="r0"),),  # type: ignore[arg-type]
        mentions=(
            Mention(id="pm0", span=first, surface="Paris"),  # type: ignore[arg-type]
            Mention(id="pm1", span=second, surface="Paris"),  # type: ignore[arg-type]
        ),
    )
    assert {m.span.start for m in g.mentions} == {first.start, second.start}


def test_ambiguity_and_unresolved_are_first_class() -> None:
    c = CanonicalText.of("how much gold or silver with 1 btc")
    g = RequestGraph(
        turn_id="t",  # type: ignore[arg-type]
        canonical=c,
        requests=(Request(id="r0", source_text=c.text, slot_ids=("s0",),  # type: ignore[arg-type]
                          state=InterpretationState.AMBIGUOUS),),
        slots=(Slot(id="s0", request_id="r0", state=InterpretationState.AMBIGUOUS,  # type: ignore[arg-type]
                    ambiguity=Ambiguity(alternatives=("gold", "silver"), reason="either/or",
                                        needs_clarification=True)),),
    )
    (slot,) = g.slots
    assert slot.ambiguity is not None and slot.ambiguity.needs_clarification
    assert slot.state is InterpretationState.AMBIGUOUS


def test_unresolved_source_is_represented_not_dropped() -> None:
    c = CanonicalText.of("do the thing with the flibbertigibbet")
    g = RequestGraph(
        turn_id="t",  # type: ignore[arg-type]
        canonical=c,
        requests=(Request(id="r0", source_text="do the thing with the flibbertigibbet",  # type: ignore[arg-type]
                          slot_ids=(), state=InterpretationState.UNRESOLVED, unresolved=True),),
        uncovered_source=("flibbertigibbet",),
    )
    assert g.is_source_conserved()
    assert g.requests[0].unresolved and g.uncovered_source == ("flibbertigibbet",)
