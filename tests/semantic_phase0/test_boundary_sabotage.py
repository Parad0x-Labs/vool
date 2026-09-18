"""Boundary sabotage at the seams THIS lane owns (the 2026-09-08 migration audit's minimum set).

Every case injects a corruption at the consuming boundary -- not at the graph builder -- and asserts
the named law refuses it. A graph comparator cannot certify runtime conservation; these are the
tests that can. Deterministic: no model, no network, no agent.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from core.semantic.bridges.demand import slot_results_from_demand_records, slot_results_from_ledger
from core.semantic.bridges.ledger import SOURCE_UNREADABLE, frozen_slot_ids, snapshot_graph_payload
from core.semantic.bridges.plan import proposed_clauses_from_graph
from core.semantic.canonical_text import CanonicalText
from core.semantic.graph_builder import GraphBuildError, RequestGraphBuilder
from core.semantic.graph_parser import parse_graph_reply
from core.semantic.graph_serialization import GraphSerializationError, graph_digest, graph_from_dict, graph_to_dict
from core.semantic.producers.lexical import lexical_request_graph
from core.semantic.publication_conservation import PublishedSlot, verify_publication
from core.semantic.request_graph import (
    DependencyEdge,
    DependencyKind,
    RequestGraphError,
    Retraction,
    TerminalDisposition,
)
from core.semantic.resolver import proposals_from_graph
from core.semantic.slot_reconciliation import SlotResult, reconcile
from core.semantic.turn_graph import REQUEST_GRAPH_KEY, current_graph_projection, graph_projection
from ops import semantic_requestgraph_gold as gold_corpus

TEXT = "what's the weather in oslo and how did tesla close"


def _bound(graph, sid, disposition, content=""):
    return SlotResult(slot_id=sid, disposition=disposition, content=content, turn_id=str(graph.turn_id), graph_digest=graph_digest(graph))


# -- I10: cross-turn injection ------------------------------------------------------------------


def test_another_turns_results_cannot_satisfy_this_graph() -> None:
    a = lexical_request_graph(TEXT, turn_id="turn-A")
    b = lexical_request_graph(TEXT, turn_id="turn-B")  # same text, same local ids u1/u2
    foreign = tuple(_bound(a, sid, TerminalDisposition.ANSWERED, "x") for sid in ("u1", "u2"))
    rec = reconcile(b, foreign, require_binding=True)
    assert not rec.conserved and rec.foreign == ("u1", "u2") and rec.missing == ("u1", "u2")
    own = tuple(_bound(b, sid, TerminalDisposition.ANSWERED, "x") for sid in ("u1", "u2"))
    assert reconcile(b, own, require_binding=True).conserved


def test_unbound_results_are_refused_when_binding_is_required() -> None:
    g = lexical_request_graph(TEXT, turn_id="t")
    loose = (SlotResult(slot_id="u1", disposition=TerminalDisposition.ANSWERED, content="x"),  # type: ignore[arg-type]
             SlotResult(slot_id="u2", disposition=TerminalDisposition.UNKNOWN))  # type: ignore[arg-type]
    assert reconcile(g, loose).conserved                         # pure fixtures may stay unbound
    strict = reconcile(g, loose, require_binding=True)
    assert not strict.conserved and strict.unbound == ("u1", "u2")


# -- I07: question-as-answer and duplicate suppression -----------------------------------------


def test_an_executed_record_without_evidence_is_unverified_not_answered() -> None:
    from core.agent_runtime.demand_ownership import DemandRecord

    g = lexical_request_graph(TEXT, turn_id="t")
    record = DemandRecord(demand_id="u1", request="what's the weather in oslo", capability="weather", lane_id="live",
                          attempted=True, terminal_state="executed", member_unit_ids=("u1",))
    (result,) = slot_results_from_demand_records([record], g)
    assert result.disposition is TerminalDisposition.UNVERIFIED and result.content == ""
    (with_evidence,) = slot_results_from_demand_records([record], g, answers={"u1": "Oslo: 12 C, overcast"})
    assert with_evidence.disposition is TerminalDisposition.ANSWERED and with_evidence.content == "Oslo: 12 C, overcast"
    assert with_evidence.turn_id == "t" and with_evidence.graph_digest == graph_digest(g)


def test_duplicate_records_reach_reconciliation_and_are_refused() -> None:
    from core.agent_runtime.demand_ownership import DemandRecord

    g = lexical_request_graph(TEXT, turn_id="t")
    twice = [
        DemandRecord(demand_id="u1", request="a", capability="weather", lane_id="live", attempted=True,
                     terminal_state="executed", member_unit_ids=("u1",)),
        DemandRecord(demand_id="u1", request="a", capability="weather", lane_id="live", attempted=True,
                     terminal_state="failed", member_unit_ids=("u1",)),
        DemandRecord(demand_id="u2", request="b", capability="market", lane_id="live", attempted=False,
                     terminal_state="not_attempted", member_unit_ids=("u2",)),
    ]
    results = slot_results_from_demand_records(twice, g, answers={"u1": "Oslo: 12 C"})
    assert len(results) == 3, "nothing may be de-duplicated before the law sees it"
    rec = reconcile(g, results, require_binding=True)
    assert not rec.conserved and rec.duplicated == ("u1",)


def test_a_batch_marking_every_member_executed_answers_only_members_with_evidence() -> None:
    from core.agent_runtime.demand_ownership import DemandRecord

    g = lexical_request_graph(TEXT, turn_id="t")
    batch = DemandRecord(demand_id="u1", request="both", capability="live", lane_id="live", attempted=True,
                         terminal_state="executed", member_unit_ids=("u1", "u2"))
    results = slot_results_from_demand_records([batch], g, answers={"u1": "Oslo: 12 C"})
    by_id = {r.slot_id: r for r in results}
    assert by_id["u1"].disposition is TerminalDisposition.ANSWERED
    assert by_id["u2"].disposition is TerminalDisposition.UNVERIFIED


# -- F01 / I09: publication needs renderings, refuses invented ids, and synthetic labels ----------


def test_empty_renderings_do_not_certify_publication() -> None:
    g = lexical_request_graph(TEXT, turn_id="t")
    results = tuple(_bound(g, sid, TerminalDisposition.ANSWERED, "x") for sid in ("u1", "u2"))
    rec = reconcile(g, results, require_binding=True)
    assert rec.conserved
    empty = (PublishedSlot(slot_id="u1"), PublishedSlot(slot_id="u2"))  # type: ignore[arg-type]
    verdict = verify_publication(rec, empty)
    assert not verdict.conserved and verdict.hidden == ("u1", "u2")


def test_a_published_id_no_terminal_slot_owns_is_invented() -> None:
    g = lexical_request_graph(TEXT, turn_id="t")
    results = tuple(_bound(g, sid, TerminalDisposition.ANSWERED, "x") for sid in ("u1", "u2"))
    rec = reconcile(g, results, require_binding=True)
    shown = (PublishedSlot(slot_id="u1", rendering="a"), PublishedSlot(slot_id="u2", rendering="b"), PublishedSlot(slot_id="u9", rendering="?"))  # type: ignore[arg-type]
    verdict = verify_publication(rec, shown)
    assert not verdict.conserved and verdict.invented == ("u9",)


def test_ledger_satisfied_without_bytes_evidence_is_unverified() -> None:
    g = lexical_request_graph(TEXT, turn_id="t")
    rows = [{"unit_id": "u1", "kind": "demand", "state": "satisfied", "evidence_source": "slice_answer_record"},
            {"unit_id": "u2", "kind": "demand", "state": "satisfied", "evidence_source": "served_answer_evidence"}]
    no_evidence = slot_results_from_ledger(rows, graph=g)
    assert {r.disposition for r in no_evidence} == {TerminalDisposition.UNVERIFIED}
    with_evidence = slot_results_from_ledger(rows, answered_content={"u1": "text_ladder:satisfied"}, graph=g)
    assert {r.slot_id: r.disposition for r in with_evidence} == {"u1": TerminalDisposition.ANSWERED, "u2": TerminalDisposition.UNVERIFIED}
    assert all(r.graph_digest == graph_digest(g) for r in with_evidence)


# -- V01 / V02: versions and unreadable snapshots ------------------------------------------------


def test_an_unknown_serialization_version_is_refused() -> None:
    g = gold_corpus.gold_graph("fx1")
    payload = graph_to_dict(g, include_text=True)
    assert graph_from_dict(payload) == g
    with pytest.raises(GraphSerializationError, match="serialization"):
        graph_from_dict({**payload, "serialization": "future.v999"})
    tampered = {**payload, "canonical": {**payload["canonical"], "text": "a different message"}}
    with pytest.raises(GraphSerializationError, match="digest"):
        graph_from_dict(tampered)


def test_an_unreadable_stored_graph_is_never_reminted_from_text() -> None:
    text = gold_corpus.gold_case("mi3").text
    good = snapshot_graph_payload(lexical_request_graph(text, turn_id="t"))
    ids, source = frozen_slot_ids({"request_text": text, "request_graph": {**good, "serialization": "future.v999"}})
    assert source == SOURCE_UNREADABLE and ids == frozenset()
    ids, source = frozen_slot_ids({"request_text": text, "request_graph": {"schema": "nope"}})
    assert source == SOURCE_UNREADABLE and ids == frozenset()


# -- D01: cycles refused; typed retraction targets ---------------------------------------------------


def test_a_dependency_cycle_is_refused_at_construction_and_by_the_builder() -> None:
    g = gold_corpus.gold_graph("mi3")
    (edge,) = g.dependencies
    back = DependencyEdge(id="d-back", from_request=edge.to_request, to_request=edge.from_request, kind=DependencyKind.ORDERING)  # type: ignore[arg-type]
    with pytest.raises(RequestGraphError, match="cycle"):
        dataclasses.replace(g, dependencies=(edge, back))
    b = RequestGraphBuilder("a then b", turn_id="t")
    r0, r1 = b.add_request("a"), b.add_request("b")
    b.add_dependency(r1, r0)
    with pytest.raises(GraphBuildError, match="cycle"):
        b.add_dependency(r0, r1)


def test_the_parser_records_a_cycle_as_an_invalid_dependency_not_an_edge() -> None:
    c = CanonicalText.of("price of gold then price of silver")
    reply = json.dumps({"requests": [
        {"key": "r1", "source": "price of gold", "depends_on": [{"request": "r2", "kind": "value"}]},
        {"key": "r2", "source": "price of silver", "depends_on": [{"request": "r1", "kind": "value"}]},
    ]})
    parsed = parse_graph_reply(reply, canonical=c, turn_id="t")
    assert parsed.graph is not None and len(parsed.graph.dependencies) == 1
    assert any(n.startswith("invalid_dependency:cycle") for n in parsed.notes)


def test_a_slot_level_retraction_withdraws_that_slot_not_the_request() -> None:
    g = gold_corpus.gold_graph("op2")  # one request, three slots
    slot_only = Retraction(id="rt-x", target="platinum", supersedes="slot-2", supersedes_kind="slot")  # type: ignore[arg-type]
    graph = dataclasses.replace(g, retractions=(slot_only,))
    (proposal,) = proposals_from_graph(graph)
    assert proposal.slot_ids == ("slot-0", "slot-1")
    whole = dataclasses.replace(g, retractions=(Retraction(id="rt-y", target="prices", supersedes="req-0", supersedes_kind="request"),))  # type: ignore[arg-type]
    assert proposals_from_graph(whole) == ()
    with pytest.raises(RequestGraphError, match="supersedes missing slot"):
        dataclasses.replace(g, retractions=(Retraction(id="rt-z", target="x", supersedes="req-0", supersedes_kind="slot"),))  # type: ignore[arg-type]


def test_an_ambiguous_string_needs_an_explicit_kind_in_the_builder() -> None:
    b = RequestGraphBuilder("price of gold", turn_id="t")
    rid = b.add_request("price of gold", request_id="u1")
    b.add_slot(rid, slot_id="u1")
    with pytest.raises(GraphBuildError, match="pass supersedes_kind"):
        b.add_retraction("gold", supersedes="u1")  # type: ignore[arg-type]
    b.add_retraction("gold", supersedes="u1", supersedes_kind="request")  # type: ignore[arg-type]
    assert b.build().retractions[0].supersedes_kind == "request"


# -- I02: ids survive the planner adapter ---------------------------------------------------------------


def test_proposed_clauses_carry_the_graph_ids() -> None:
    g = gold_corpus.gold_graph("mi3")
    clauses = proposed_clauses_from_graph(g)
    assert {c.request_id for c in clauses} == {r.id for r in g.requests}
    assert all(c.slot_ids == g.requests[[r.id for r in g.requests].index(c.request_id)].slot_ids for c in clauses)


# -- projection provenance ---------------------------------------------------------------------------------


def test_a_projection_from_another_turn_reads_as_absent() -> None:
    g = lexical_request_graph(TEXT, turn_id="turn-A")
    context = {REQUEST_GRAPH_KEY: graph_projection(g, producer="lexical", producer_version="v")}
    assert current_graph_projection(context, turn_id="turn-A") is not None
    assert current_graph_projection(context, turn_id="turn-B") is None
    assert current_graph_projection({REQUEST_GRAPH_KEY: {"schema": "forged"}}) is None


def test_the_projection_key_is_stripped_from_inbound_bodies() -> None:
    from core.request_trust import RESERVED_TRUST_KEYS

    assert REQUEST_GRAPH_KEY in RESERVED_TRUST_KEYS


# -- A01: construction is pure --------------------------------------------------------------------------


def test_lexical_construction_registers_no_lifecycle_and_writes_no_turn_state(monkeypatch) -> None:
    from core import execution_requirements as er

    calls = {"n": 0}

    def _boom(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("lifecycle registration during graph construction")

    monkeypatch.setattr(er, "_register_lifecycle", _boom, raising=True)
    context: dict = {"surface": "api"}
    graph = lexical_request_graph("what is the price of gold now and the weather in Rome", turn_id="t", source_context=context)
    assert graph.slot_ids
    assert calls["n"] == 0
    assert er.CURRENT_REQUIREMENT_KEY not in context and set(context) == {"surface"}


def test_the_producer_inherits_the_frozen_prohibitions_from_the_request() -> None:
    from core.turn_contract import TurnRequest
    from core.turn_prohibitions import prohibitions_from_text

    turn = TurnRequest.from_ingress(user_text="what is the price of gold", source_context={}, request_id="r", turn_id="t", session_id="s")
    tightened = dataclasses.replace(turn, prohibitions=prohibitions_from_text("no web. what is the price of gold"))
    graph = lexical_request_graph(tightened, turn_id="t")
    assert {p.target for p in graph.prohibitions} >= {"web"}
    plain = lexical_request_graph(turn, turn_id="t")
    assert not plain.prohibitions
