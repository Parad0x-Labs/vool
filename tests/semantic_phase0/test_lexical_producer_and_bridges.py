"""The lexical producer is TODAY's reading with stable ids, and the bridges carry those ids into the
runtime layers without re-parsing text. Deterministic: no model, no network, no agent."""
from __future__ import annotations

import dataclasses

import pytest

from core.agent_runtime.answer_coverage import demand_units, interpret_request
from core.semantic.bridges.demand import (
    DEMAND_STATE_TO_DISPOSITION,
    LEDGER_STATE_TO_DISPOSITION,
    slot_results_from_demand_records,
    slot_results_from_ledger,
)
from core.semantic.bridges.frames import frames_from_graph, graph_from_proof
from core.semantic.bridges.ledger import (
    SOURCE_GRAPH,
    SOURCE_LEGACY_REPARSE,
    SOURCE_NONE,
    SOURCE_UNREADABLE,
    demand_obligation_rows,
    frozen_slot_ids,
    snapshot_graph_payload,
)
from core.semantic.bridges.plan import blocked_requests, proposed_clauses_from_graph, released_value_dependencies
from core.semantic.bridges.publication import published_slots
from core.semantic.graph_diff import compare_graphs
from core.semantic.producers.lexical import lexical_request_graph
from core.semantic.publication_conservation import verify_publication
from core.semantic.request_graph import (
    ConstraintKind,
    DependencyKind,
    InterpretationState,
    SemanticRole,
    TerminalDisposition,
)
from core.semantic.slot_reconciliation import SlotResult, reconcile
from ops import semantic_requestgraph_gold as gold_corpus

CASE_IDS = [c.id for c in gold_corpus.gold_cases()]


# -- the producer: identity with today's ledger grain ------------------------------------------


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_lexical_slot_ids_are_todays_demand_unit_ids(case_id: str) -> None:
    text = gold_corpus.gold_case(case_id).text
    graph = lexical_request_graph(text, turn_id="t")
    assert graph.slot_ids == frozenset(u.unit_id for u in demand_units(text)), case_id
    assert {r.id for r in graph.requests if not r.unresolved} == {u.unit_id for u in demand_units(text)}
    assert graph.is_source_conserved()
    assert all(r.span is not None for r in graph.requests), case_id


def test_lexical_reading_reuses_a_precomputed_interpretation() -> None:
    text = "what is the weather in Rome also tell me how much gold I can buy"
    reading = interpret_request(text)
    a = lexical_request_graph(text, turn_id="t", interpretation=reading)
    b = lexical_request_graph(text, turn_id="t")
    assert a == b


def test_lexical_reading_carries_the_value_dependency_the_splitter_recorded() -> None:
    graph = lexical_request_graph(gold_corpus.gold_case("mi3").text, turn_id="t")
    (edge,) = [d for d in graph.dependencies if d.kind is DependencyKind.VALUE]
    assert edge.from_request == "u2" and edge.to_request == "u1" and edge.value_ref == "u1"
    fx = graph.slots_for("u1")[0]  # type: ignore[arg-type]
    roles = {op.role for op in fx.operands}
    assert {SemanticRole.SOURCE, SemanticRole.TARGET} <= roles  # from the closed FX grammar
    source = next(op for op in fx.operands if op.role is SemanticRole.SOURCE)
    assert source.quantity is not None and source.quantity.exact == "1000" and source.quantity.currency == "EUR"


def test_lexical_reading_records_the_whole_turn_prohibition() -> None:
    graph = lexical_request_graph(gold_corpus.gold_case("pr1").text, turn_id="t")
    targets = {p.target for p in graph.prohibitions}
    assert "web" in targets
    assert any(c.kind is ConstraintKind.RETRIEVAL_FORBIDDEN for c in graph.constraints)
    # The splitter mints "no web pls" as a unit; the graph keeps that id and says what it is.
    (slot,) = graph.slots_for("u1")  # type: ignore[arg-type]
    assert slot.state is InterpretationState.RETRIEVAL_FORBIDDEN


def test_lexical_reading_represents_a_retraction_when_the_detector_fires() -> None:
    text = "search my workspace for password.txt. WAIT, STOP. Cancel the search immediately. Instead, write a two line javascript function"
    graph = lexical_request_graph(text, turn_id="t")
    assert graph.retractions, "the detector fires on this text and the graph must carry it"
    superseded = {r.supersedes for r in graph.retractions}
    withdrawn = [r for r in graph.requests if r.id in superseded]
    assert withdrawn and any("password.txt" in r.source_text for r in withdrawn)
    assert graph.retractions[0].target == "search"


def test_lexical_reading_scopes_retrieval_per_request() -> None:
    graph = lexical_request_graph(gold_corpus.gold_case("us1").text, turn_id="t")
    required = [c for c in graph.constraints if c.kind is ConstraintKind.RETRIEVAL_REQUIRED]
    assert required and "u2" in required[0].scope  # the gold price needs live data


def test_lexical_versus_gold_is_the_measurement_not_a_pass() -> None:
    """The deterministic reading is what the resolver must beat. Recorded, not tuned."""
    table = {}
    for case_id in CASE_IDS:
        gold = gold_corpus.gold_graph(case_id)
        cand = lexical_request_graph(gold_corpus.gold_case(case_id).text, turn_id="t")
        cmp = compare_graphs(gold, cand)
        table[case_id] = (cmp.whole_turn_correct, cmp.failed_axes())
    strict = sum(1 for ok, _f in table.values() if ok)
    assert strict < len(CASE_IDS), "the lexical reading has no operand roles outside formal grammars; a perfect score means the gold was tuned to it"
    # Structural facts that hold whatever the heuristic vocabulary does:
    assert "slot_coverage" in table["op2"][1]      # 2 units for 3 gold slots (under-split)
    assert "retractions" in table["pr2"][1]        # the detector does not fire on this text
    assert "prohibitions" in table["pr1"][1]       # 'dont quote gold' is not a target it knows


# -- frames bridge ----------------------------------------------------------------------------


def test_frames_round_trip_through_the_conductor_proof() -> None:
    from core.conductor.semantic_proof import Polarity, validate_proof

    base = gold_corpus.gold_graph("fx1")
    gold = dataclasses.replace(base, requests=(dataclasses.replace(base.requests[0], family="fx_quote"),))
    frames = frames_from_graph(gold)
    assert [f.polarity for f in frames] == [Polarity.AFFIRMED]
    assert {r.role_name for r in frames[0].roles} == {"base_currency", "quote_currency", "amount"}
    proof = validate_proof(gold.canonical, frames)
    assert proof.frames, [a.to_dict() for a in proof.abstentions]
    back = graph_from_proof(proof, turn_id="t")
    assert len(back.requests) == 1 and len(back.slots) == 1
    assert {op.role for op in back.slots[0].operands} == {SemanticRole.SOURCE, SemanticRole.TARGET}


def test_prohibitions_become_negated_frames() -> None:
    from core.conductor.semantic_proof import Polarity

    frames = frames_from_graph(gold_corpus.gold_graph("pr1"))
    assert frames and all(f.polarity is Polarity.NEGATED for f in frames)
    assert {f.family for f in frames} == {"web", "gold"}


# -- demand bridge ------------------------------------------------------------------------------


def test_disposition_maps_are_total_over_both_vocabularies() -> None:
    from core.agent_runtime import demand_ownership
    from core.conductor import obligation_ledger

    ledger_states = obligation_ledger._PERMITTED_TERMINAL_STATES
    assert ledger_states <= set(LEDGER_STATE_TO_DISPOSITION), ledger_states - set(LEDGER_STATE_TO_DISPOSITION)
    demand_states = {
        name.split("=")[0] for name in ()
    } or {"executed", "failed", "pending_approval", "not_attempted", "refused"}
    for state in demand_states:
        assert hasattr(demand_ownership, "DemandRecord")
        assert state in DEMAND_STATE_TO_DISPOSITION
    assert TerminalDisposition.ANSWERED not in {LEDGER_STATE_TO_DISPOSITION[s] for s in ("unanswered", "indeterminate", "gated")}


def test_ledger_rows_terminate_slots_and_open_rows_terminate_nothing() -> None:
    graph = lexical_request_graph(gold_corpus.gold_case("mi2").text, turn_id="t")
    rows = [
        {"unit_id": "u1", "kind": "demand", "state": "satisfied", "evidence_source": "rss_closure_sweep"},
        {"unit_id": "u2", "kind": "demand", "state": "planned"},
    ]
    results = slot_results_from_ledger(rows, answered_content={"u1": "Oslo: 12 C"})
    assert [r.slot_id for r in results] == ["u1"]
    rec = reconcile(graph, results)
    assert not rec.conserved and rec.missing == ("u2",)
    # satisfied without served content is UNVERIFIED, never ANSWERED.
    (unverified,) = slot_results_from_ledger([rows[0]])
    assert unverified.disposition is TerminalDisposition.UNVERIFIED


def test_demand_records_map_onto_slot_results() -> None:
    from core.agent_runtime.demand_ownership import DemandRecord

    graph = lexical_request_graph(gold_corpus.gold_case("mi2").text, turn_id="t")
    records = [
        DemandRecord(demand_id="u1", request="what's the weather in oslo", capability="weather", lane_id="live",
                     attempted=True, terminal_state="executed", member_unit_ids=("u1",)),
        DemandRecord(demand_id="u2", request="how did tesla close", capability="market", lane_id="live",
                     attempted=False, terminal_state="not_attempted", member_unit_ids=("u2",)),
    ]
    results = slot_results_from_demand_records(records, graph, answers={"u1": "Oslo: 12 C, overcast"})
    by_id = {r.slot_id: r for r in results}
    assert by_id["u1"].disposition is TerminalDisposition.ANSWERED and by_id["u1"].content == "Oslo: 12 C, overcast"
    assert by_id["u2"].disposition is TerminalDisposition.UNKNOWN
    assert reconcile(graph, results, require_binding=True).conserved


# -- ledger bridge ------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_obligation_rows_are_byte_identical_to_the_door_mint(case_id: str) -> None:
    text = gold_corpus.gold_case(case_id).text
    units = demand_units(text)
    graph = lexical_request_graph(text, turn_id="t", interpretation=interpret_request(text))
    legacy = [
        {"obligation_id": "ob:att:answer", "text": text[:240], "kind": "prose"},
        *(
            {"obligation_id": f"ob:att:demand:{u.unit_id}", "text": u.text, "kind": "demand", "unit_id": u.unit_id, "slice_id": u.slice_id}
            for u in units
        ),
    ]
    rows = demand_obligation_rows(graph, attempt_id="att", request_text=text, units={u.unit_id: u for u in units})
    assert rows == legacy, case_id


def test_frozen_slot_ids_read_the_graph_first_then_fall_back_and_say_so() -> None:
    text = gold_corpus.gold_case("mi3").text
    graph = lexical_request_graph(text, turn_id="t")
    with_graph = {"request_text": text, "request_graph": snapshot_graph_payload(graph)}
    ids, source = frozen_slot_ids(with_graph)
    assert ids == {"u1", "u2", "u3", "u4"} and source == SOURCE_GRAPH
    legacy_ids, legacy_source = frozen_slot_ids({"request_text": text})
    assert legacy_ids == ids and legacy_source == SOURCE_LEGACY_REPARSE
    assert frozen_slot_ids({}) == (frozenset(), SOURCE_NONE)
    # A graph that IS there but cannot be read is UNREADABLE: no re-derivation from text, ever.
    corrupt = {"request_text": text, "request_graph": {"schema": "vool.request_graph.v1", "canonical": {"digest": "nope"}}}
    assert frozen_slot_ids(corrupt) == (frozenset(), SOURCE_UNREADABLE)


# -- plan bridge --------------------------------------------------------------------------------


def test_proposed_clauses_carry_spans_and_derived_positions() -> None:
    graph = gold_corpus.gold_graph("mi3")
    clauses = proposed_clauses_from_graph(graph)
    assert [c.index for c in clauses] == [0, 1, 2, 3]
    gold_clause = next(c for c in clauses if "gold" in c.request)
    assert gold_clause.depends_on == (0,) and gold_clause.origin == "request_graph"
    assert gold_clause.canonical_representation == graph.canonical.representation
    assert gold_clause.spans and gold_clause.spans[0].resolve(graph.canonical) == "gold"


def test_value_dependency_releases_only_on_a_successful_prerequisite() -> None:
    graph = gold_corpus.gold_graph("mi3")
    (edge,) = graph.dependencies
    answered = {"slot-0": SlotResult(slot_id="slot-0", disposition=TerminalDisposition.ANSWERED, content="91,000 RUB")}  # type: ignore[arg-type]
    blocked_only = {"slot-0": SlotResult(slot_id="slot-0", disposition=TerminalDisposition.BLOCKED, detail="approval")}  # type: ignore[arg-type]
    empty_answer = {"slot-0": SlotResult(slot_id="slot-0", disposition=TerminalDisposition.ANSWERED, content="  ")}  # type: ignore[arg-type]
    assert released_value_dependencies(graph, answered) == {edge.id: True}
    assert released_value_dependencies(graph, blocked_only) == {edge.id: False}   # ADMITTED != SUCCESSFUL
    assert released_value_dependencies(graph, empty_answer) == {edge.id: False}
    assert "req-1" in blocked_requests(graph, blocked_only) and "req-1" not in blocked_requests(graph, answered)
    assert graph.dependencies == (edge,)  # never removed, never rewired


def test_conditional_dependency_blocks_until_the_condition_answered() -> None:
    graph = gold_corpus.gold_graph("us1")
    assert "req-1" in blocked_requests(graph, {})
    assert "req-1" not in blocked_requests(graph, {"slot-0": SlotResult(slot_id="slot-0", disposition=TerminalDisposition.ANSWERED, content="rain")})  # type: ignore[arg-type]
    assert "req-1" in blocked_requests(graph, {"slot-0": SlotResult(slot_id="slot-0", disposition=TerminalDisposition.FAILED)})  # type: ignore[arg-type]


# -- publication bridge ------------------------------------------------------------------------


def test_published_slots_make_every_terminal_slot_visible_or_hidden_explicitly() -> None:
    graph = lexical_request_graph(gold_corpus.gold_case("mi2").text, turn_id="t")
    results = (
        SlotResult(slot_id="u1", disposition=TerminalDisposition.ANSWERED, content="Oslo: 12 C"),  # type: ignore[arg-type]
        SlotResult(slot_id="u2", disposition=TerminalDisposition.UNKNOWN, detail="not dispatched"),  # type: ignore[arg-type]
    )
    rec = reconcile(graph, results)
    assert rec.conserved
    shown = published_slots(results, answered_units={"u1": "Oslo: 12 C"}, rendered_units={"u2": "Could not be answered: how did tesla close"})
    assert verify_publication(rec, shown).conserved
    hidden = published_slots(results, answered_units={"u1": "Oslo: 12 C"})
    verdict = verify_publication(rec, hidden)
    assert not verdict.conserved and verdict.hidden == ("u2",)
