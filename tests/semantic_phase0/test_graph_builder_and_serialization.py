"""RequestGraphBuilder + canonical serialization: minted ids, occurrence binding, source
conservation, derived shape, full-field digest, lossless round trip. Pure; no model, no network."""
from __future__ import annotations

import dataclasses

import pytest

from core.semantic.canonical_text import CanonicalText
from core.semantic.graph_builder import (
    GraphBuildError,
    RequestGraphBuilder,
    content_tokens,
    graph_summary,
    operand,
)
from core.semantic.graph_serialization import (
    GraphSerializationError,
    canonical_json,
    graph_digest,
    graph_from_dict,
    graph_to_dict,
)
from core.semantic.request_graph import (
    Ambiguity,
    ConstraintKind,
    DependencyKind,
    InterpretationState,
    OutputFormat,
    Quantity,
    SemanticRole,
)
from core.semantic.types import RequestShape

TEXT = "what is the price of gold and how much silver can I buy with 1 BTC, do not use twitter"


def _rich() -> RequestGraphBuilder:
    b = RequestGraphBuilder(TEXT, turn_id="t-1")
    r0 = b.add_request("what is the price of gold")
    r1 = b.add_request("how much silver can I buy with 1 BTC")
    m_gold = b.add_mention("gold", within=b.request_span(r0), kind="asset", role=SemanticRole.SUBJECT, entity_key="gold")
    m_silver = b.add_mention("silver", within=b.request_span(r1), kind="asset", role=SemanticRole.TARGET, entity_key="silver")
    m_btc = b.add_mention("BTC", within=b.request_span(r1), kind="asset", role=SemanticRole.PAYMENT, entity_key="BTC")
    b.add_slot(r0, expected="gold spot price", operands=(operand(SemanticRole.SUBJECT, mention_id=m_gold),))
    b.add_slot(
        r1,
        expected="amount of silver",
        operands=(
            operand(SemanticRole.TARGET, mention_id=m_silver),
            operand(SemanticRole.PAYMENT, mention_id=m_btc, quantity=Quantity(raw="1 BTC", exact="1", asset="BTC")),
        ),
    )
    b.add_constraint(ConstraintKind.RETRIEVAL_REQUIRED, scope=(r0, r1))
    b.add_prohibition("twitter", span=b.canonical.find("do not use twitter"))
    b.add_dependency(r1, r0, kind=DependencyKind.ORDERING)
    b.set_presentation(fmt=OutputFormat.TABLE)
    return b


# -- minting + binding -----------------------------------------------------------


def test_builder_mints_stable_ids_and_a_valid_graph() -> None:
    g = _rich().build()
    assert [r.id for r in g.requests] == ["req-0", "req-1"]
    assert g.slot_ids == frozenset({"slot-0", "slot-1"})
    assert g.requests[1].slot_ids == ("slot-1",)
    assert g.is_source_conserved()
    assert g.uncovered_source == ()  # "and", "," are filler/punctuation; everything else is covered
    assert g.shape is RequestShape.MULTI_CLAUSE


def test_repeated_surface_in_two_requests_binds_two_occurrences() -> None:
    b = RequestGraphBuilder("weather in Paris and the population of Paris", turn_id="t")
    r0 = b.add_request("weather in Paris")
    r1 = b.add_request("the population of Paris")
    m0 = b.add_mention("Paris", within=b.request_span(r0), kind="location")
    m1 = b.add_mention("Paris", within=b.request_span(r1), kind="location")
    g = b.build()
    by_id = {m.id: m for m in g.mentions}
    assert by_id[m0].occurrence == 0 and by_id[m1].occurrence == 1
    assert by_id[m0].span.start < by_id[m1].span.start
    assert by_id[m1].span.start >= g.requests[1].span.start  # bound INSIDE its own request


def test_unqualified_repeat_binds_the_next_unclaimed_occurrence_not_the_first_again() -> None:
    b = RequestGraphBuilder("Paris then Paris", turn_id="t")
    b.add_request("Paris then Paris")
    first = b.add_mention("Paris")
    second = b.add_mention("Paris")
    g = b.build()
    occ = {m.id: m.occurrence for m in g.mentions}
    assert occ[first] == 0 and occ[second] == 1


def test_explicit_occurrence_index_is_honoured_and_bounds_checked() -> None:
    b = RequestGraphBuilder("a b a b a", turn_id="t")
    b.add_request("a b a b a")
    mid = b.add_mention("a", occurrence=2)
    assert next(m for m in b.build().mentions if m.id == mid).occurrence == 2
    with pytest.raises(GraphBuildError, match="does not exist"):
        b.add_mention("a", occurrence=7)


def test_unfindable_surface_raises_rather_than_fabricating() -> None:
    b = RequestGraphBuilder("price of gold", turn_id="t")
    b.add_request("price of gold")
    with pytest.raises(GraphBuildError, match="not in the text"):
        b.add_mention("platinum")


def test_slot_for_unknown_request_and_duplicate_explicit_id_are_refused() -> None:
    b = RequestGraphBuilder("x", turn_id="t")
    with pytest.raises(GraphBuildError, match="does not exist"):
        b.add_slot("req-404")  # type: ignore[arg-type]
    b.add_request("x", request_id="u1")
    with pytest.raises(GraphBuildError, match="already minted"):
        b.add_request("x", request_id="u1")


# -- source conservation ---------------------------------------------------------


def test_uncovered_content_stretch_becomes_an_unresolved_request() -> None:
    b = RequestGraphBuilder("price of gold and also the flibbertigibbet index please", turn_id="t")
    b.add_request("price of gold")
    g = b.build()
    unresolved = [r for r in g.requests if r.unresolved]
    assert len(unresolved) == 1
    assert unresolved[0].state is InterpretationState.UNRESOLVED
    assert "flibbertigibbet" in unresolved[0].source_text
    assert g.uncovered_source == (unresolved[0].source_text,)
    assert g.is_source_conserved()


def test_filler_only_stretches_mint_nothing() -> None:
    b = RequestGraphBuilder("please, what is the price of gold and the price of silver, thanks", turn_id="t")
    b.add_request("the price of gold")
    b.add_request("the price of silver")
    g = b.build()
    assert not any(r.unresolved for r in g.requests)
    assert g.uncovered_source == ()


def test_conserve_source_can_be_switched_off_for_partial_producers() -> None:
    b = RequestGraphBuilder("price of gold and the flibbertigibbet index", turn_id="t")
    b.add_request("price of gold")
    g = b.build(conserve_source=False)
    assert len(g.requests) == 1 and g.uncovered_source == ()


def test_content_tokens_keep_retraction_and_prohibition_cues() -> None:
    words = [w for _s, _e, w in content_tokens("WAIT, cancel that, do not search, thanks")]
    assert "wait" in words and "cancel" in words and "not" in words and "search" in words
    assert "thanks" not in words and "that" not in words


# -- derived shape ---------------------------------------------------------------


def test_shape_is_derived_from_records_never_from_phrases() -> None:
    single = RequestGraphBuilder("price of gold", turn_id="t")
    single.add_request("price of gold")
    assert single.build().shape is RequestShape.SINGLE

    cond = RequestGraphBuilder("if it rains, price of gold", turn_id="t")
    a = cond.add_request("if it rains")
    c = cond.add_request("price of gold")
    cond.add_dependency(c, a, kind=DependencyKind.CONDITIONAL)
    assert cond.build().shape is RequestShape.CONDITIONAL

    retr = RequestGraphBuilder("book a flight, cancel that", turn_id="t")
    r = retr.add_request("book a flight")
    retr.add_retraction("the booking", supersedes=r, span=retr.canonical.find("cancel that"))
    assert retr.build().shape is RequestShape.MID_TURN_CORRECTION

    none = RequestGraphBuilder("no web pls", turn_id="t")
    none.add_prohibition("web", span=none.canonical.find("no web pls"))
    assert none.build().shape is RequestShape.UNKNOWN

    override = RequestGraphBuilder("price of gold", turn_id="t")
    override.add_request("price of gold")
    override.set_shape(RequestShape.CROSS_TURN_REFERENCE)
    assert override.build().shape is RequestShape.CROSS_TURN_REFERENCE


def test_ambiguity_on_a_slot_forces_the_ambiguous_state() -> None:
    b = RequestGraphBuilder("how much gold or silver with 1 btc", turn_id="t")
    r = b.add_request("how much gold or silver with 1 btc")
    b.add_slot(r, ambiguity=Ambiguity(alternatives=("gold", "silver"), needs_clarification=True))
    (slot,) = b.build().slots
    assert slot.state is InterpretationState.AMBIGUOUS


# -- serialization ---------------------------------------------------------------


def test_round_trip_is_lossless() -> None:
    g = _rich().build()
    payload = graph_to_dict(g, include_text=True)
    back = graph_from_dict(payload)
    assert back == g
    assert graph_to_dict(back, include_text=True) == payload
    assert graph_digest(back) == graph_digest(g)


def test_text_free_payload_needs_the_canonical_and_checks_its_digest() -> None:
    g = _rich().build()
    payload = graph_to_dict(g, include_text=False)
    assert "text" not in payload["canonical"]
    with pytest.raises(GraphSerializationError, match="no text"):
        graph_from_dict(payload)
    assert graph_from_dict(payload, canonical=g.canonical) == g
    with pytest.raises(GraphSerializationError, match="does not match"):
        graph_from_dict(payload, canonical=CanonicalText.of("a different message entirely"))


def test_malformed_payloads_are_refused_typed() -> None:
    g = _rich().build()
    payload = graph_to_dict(g, include_text=True)
    bad_state = {**payload, "slots": [{**payload["slots"][0], "state": "confident"}, payload["slots"][1]]}
    with pytest.raises(GraphSerializationError, match="slot slot-0"):
        graph_from_dict(bad_state)
    dangling = {**payload, "dependencies": [{**payload["dependencies"][0], "to_request": "req-404"}]}
    with pytest.raises(GraphSerializationError, match="graph law"):
        graph_from_dict(dangling)
    with pytest.raises(GraphSerializationError, match="not an object"):
        graph_from_dict("nope")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda g: dataclasses.replace(g, slots=(dataclasses.replace(g.slots[0], state=InterpretationState.UNKNOWN_MEANING), g.slots[1])),
        lambda g: dataclasses.replace(g, slots=(dataclasses.replace(g.slots[0], reason="cannot tell"), g.slots[1])),
        lambda g: dataclasses.replace(g, mentions=(dataclasses.replace(g.mentions[0], occurrence=1), *g.mentions[1:])),
        lambda g: dataclasses.replace(g, mentions=(dataclasses.replace(g.mentions[0], entity_key="XAU"), *g.mentions[1:])),
        lambda g: dataclasses.replace(g, prohibitions=(dataclasses.replace(g.prohibitions[0], target="reddit"),)),
        lambda g: dataclasses.replace(g, prohibitions=()),
        lambda g: dataclasses.replace(g, dependencies=()),
        lambda g: dataclasses.replace(g, constraints=()),
        lambda g: dataclasses.replace(g, presentation=None),
        lambda g: dataclasses.replace(g, shape=RequestShape.SINGLE),
        lambda g: dataclasses.replace(g, uncovered_source=("leftover",)),
        lambda g: dataclasses.replace(
            g,
            slots=(
                g.slots[0],
                dataclasses.replace(
                    g.slots[1],
                    operands=(
                        g.slots[1].operands[0],
                        dataclasses.replace(g.slots[1].operands[1], quantity=Quantity(raw="2 BTC", exact="2", asset="BTC")),
                    ),
                ),
            ),
        ),
    ],
    ids=[
        "slot_state", "slot_reason", "mention_occurrence", "entity_key", "prohibition_target",
        "prohibition_dropped", "dependency_dropped", "constraint_dropped", "presentation", "shape",
        "uncovered_source", "quantity",
    ],
)
def test_every_meaningful_field_moves_the_digest(mutate) -> None:
    g = _rich().build()
    assert graph_digest(mutate(g)) != graph_digest(g)


def test_digest_is_stable_across_key_order_and_processes() -> None:
    g = _rich().build()
    payload = graph_to_dict(g, include_text=False)
    shuffled = {k: payload[k] for k in reversed(list(payload))}
    assert canonical_json(shuffled) == canonical_json(payload)
    assert graph_digest(g) == graph_digest(_rich().build())


def test_summary_is_text_free() -> None:
    g = _rich().build()
    summary = graph_summary(g)
    rendered = canonical_json(summary).lower()
    for word in ("gold", "silver", "btc", "twitter"):
        assert word not in rendered
    assert summary["slot_count"] == 2 and summary["shape"] == "multi_clause"
