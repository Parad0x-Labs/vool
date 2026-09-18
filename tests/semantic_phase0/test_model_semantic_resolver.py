"""The model-backed SemanticResolver emits a typed RequestGraph: interprets, never guesses, never
gains authority. Every test uses a deterministic stub backend -- no model, no network.

Pinned here, one test each, are the four parse defects the 2026-09-08 review demonstrated on the
clause-list resolver: a malformed clause disappearing, the clause cap truncating content, a skipped
clause shifting dependency indices, and a repeated entity binding to the first global occurrence.
"""
from __future__ import annotations

import json

import pytest

from core.semantic.admission import admit
from core.semantic.canonical_text import CanonicalText
from core.semantic.graph_diff import compare_graphs
from core.semantic.graph_parser import parse_graph_reply
from core.semantic.request_graph import (
    ConstraintKind,
    DependencyKind,
    InterpretationState,
    OutputFormat,
)
from core.semantic.resolver import (
    PROMPT_VERSION,
    ModelSemanticResolver,
    _Breaker,
    proposals_from_graph,
)
from core.semantic.types import GraphSemanticResolver, IntentProposal, ReasonCode, RequestShape, SemanticResolver
from ops import semantic_requestgraph_gold as gold_corpus

OPERATIONS = ("market.quote", "weather.forecast", "workspace.read_file", "currency.convert")


def _resolver(reply, **kw) -> ModelSemanticResolver:
    """A resolver whose backend returns `reply` (a str) or the result of calling it."""
    def backend(system: str, user: str) -> str:
        assert "OPERATIONS" in user and "USER MESSAGE" in user
        return reply(system, user) if callable(reply) else reply

    return ModelSemanticResolver(backend, **kw)


def _canonical(text: str) -> CanonicalText:
    return CanonicalText.of(text)


def _reply(**payload) -> str:
    return json.dumps(payload)


def _req(key, source, *, operation=None, slots=None, state=None, depends_on=None, reason=None):
    out = {"key": key, "source": source, "operation": operation}
    if slots is not None:
        out["slots"] = slots
    if state:
        out["state"] = state
    if reason:
        out["reason"] = reason
    if depends_on:
        out["depends_on"] = depends_on
    return out


def _slot(expected, *operands, key=None, state=None, reason=None, ambiguity=None):
    out = {"expected": expected, "operands": list(operands)}
    if key:
        out["key"] = key
    if state:
        out["state"] = state
    if reason:
        out["reason"] = reason
    if ambiguity:
        out["ambiguity"] = ambiguity
    return out


def _op(role, text=None, *, entity=None, quantity=None, occurrence=None, result_of=None):
    out = {"role": role}
    if text is not None:
        out["text"] = text
    if entity:
        out["entity"] = entity
    if quantity:
        out["quantity"] = quantity
    if occurrence is not None:
        out["occurrence"] = occurrence
    if result_of:
        out["result_of"] = result_of
    return out


# -- the contract ----------------------------------------------------------------


def test_resolver_satisfies_both_protocols() -> None:
    resolver = ModelSemanticResolver()
    assert isinstance(resolver, GraphSemanticResolver)
    assert isinstance(resolver, SemanticResolver)


def test_prepare_is_pure_and_versioned() -> None:
    resolver = ModelSemanticResolver(catalog_descriptions={"market.quote": "a live price"})
    prepared = resolver.prepare(_canonical("price of gold"), operations=OPERATIONS, turn_id="t-1")
    assert prepared is not None
    assert prepared.prompt_version == PROMPT_VERSION and prepared.schema_version
    assert "price of gold" in prepared.user_prompt and "market.quote - a live price" in prepared.user_prompt
    assert prepared.json_schema["required"] == ["requests"]
    other_turn = resolver.prepare(_canonical("price of gold"), operations=OPERATIONS, turn_id="t-2")
    assert other_turn is not None and other_turn.cache_key != prepared.cache_key
    other_catalog = resolver.prepare(_canonical("price of gold"), operations=OPERATIONS[:1], turn_id="t-1")
    assert other_catalog is not None and other_catalog.cache_key != prepared.cache_key


def test_no_operations_or_empty_text_prepares_nothing_and_calls_no_backend() -> None:
    calls = {"n": 0}

    def backend(system: str, user: str) -> str:
        calls["n"] += 1
        return "{}"

    resolver = ModelSemanticResolver(backend)
    assert resolver.prepare(_canonical("hi"), operations=()) is None
    assert resolver.prepare(_canonical("   "), operations=OPERATIONS) is None
    assert resolver.interpret(_canonical("hi"), operations=()) is None
    assert resolver.propose(_canonical("   "), operations=OPERATIONS) == ()
    assert calls["n"] == 0


# -- happy path --------------------------------------------------------------------


def test_well_formed_reply_becomes_a_typed_graph_with_text_bound_spans() -> None:
    canonical = _canonical("what is the price of gold and the weather in Rome")
    reply = _reply(
        requests=[
            _req("r1", "the price of gold", operation="market.quote",
                 slots=[_slot("gold spot price", _op("subject", "gold", entity="gold"))]),
            _req("r2", "the weather in Rome", operation="weather.forecast",
                 slots=[_slot("current weather in Rome", _op("subject", "Rome", entity="Rome"))],
                 depends_on=[{"request": "r1", "kind": "ordering"}]),
        ],
        constraints=[{"kind": "retrieval_required", "requests": ["r1", "r2"]}],
        presentation={"format": "table"},
    )
    graph = _resolver(reply).interpret(canonical, operations=OPERATIONS, turn_id="t")
    assert graph is not None
    assert [r.family for r in graph.requests] == ["market.quote", "weather.forecast"]
    assert graph.shape is RequestShape.MULTI_CLAUSE
    assert graph.uncovered_source == ()
    assert {m.span.resolve(canonical) for m in graph.mentions} == {"gold", "Rome"}
    assert graph.dependencies[0].kind is DependencyKind.ORDERING
    assert graph.dependencies[0].from_request == graph.requests[1].id
    assert graph.presentation is not None and graph.presentation.fmt is OutputFormat.TABLE
    assert any(c.kind is ConstraintKind.RETRIEVAL_REQUIRED for c in graph.constraints)

    proposals = proposals_from_graph(graph)
    assert [p.operation for p in proposals] == ["market.quote", "weather.forecast"]
    assert [p.index for p in proposals] == [0, 1] and proposals[1].depends_on == (0,)
    assert proposals[0].request_id == graph.requests[0].id and proposals[0].slot_ids == graph.requests[0].slot_ids
    assert proposals[0].spans[0].resolve(canonical) == "gold"


def test_legacy_clause_array_reply_is_still_read() -> None:
    canonical = _canonical("what is the price of gold and the weather in Rome")
    reply = (
        '[{"request":"the price of gold","operation":"market.quote","entities":[{"text":"gold","kind":"asset"}]},'
        '{"request":"the weather in Rome","operation":"weather.forecast","entities":[{"text":"Rome","kind":"location"}],"depends_on":[0]}]'
    )
    proposals = _resolver(reply).propose(canonical, operations=OPERATIONS)
    assert [p.operation for p in proposals] == ["market.quote", "weather.forecast"]
    assert proposals[1].depends_on == (0,)
    assert proposals[0].spans[0].resolve(canonical) == "gold"


# -- the four review defects, each pinned -------------------------------------------


def test_malformed_entry_is_preserved_as_an_unresolved_obligation_not_dropped() -> None:
    """Review defect 1: a malformed clause used to disappear. Its text must survive as UNRESOLVED."""
    canonical = _canonical("price of gold and the weather in Rome")
    reply = _reply(requests=[
        _req("r1", "price of gold", operation="market.quote", slots=[_slot("gold spot price", _op("subject", "gold"))]),
        "the weather in Rome",  # not an object
    ])
    graph = _resolver(reply).interpret(canonical, operations=OPERATIONS)
    assert graph is not None
    unresolved = [r for r in graph.requests if r.unresolved]
    assert len(unresolved) == 1 and "weather in Rome" in unresolved[0].source_text
    assert unresolved[0].state is InterpretationState.UNRESOLVED
    assert graph.is_source_conserved()


def test_request_cap_conserves_the_remaining_text_as_unresolved() -> None:
    """Review defect 2: the clause cap used to truncate content silently."""
    words = [f"alpha{i}" for i in range(6)]
    canonical = _canonical(", ".join(f"price of {w}" for w in words))
    reply = _reply(requests=[
        _req(f"r{i}", f"price of {w}", operation="market.quote", slots=[_slot(f"{w} price", _op("subject", w))])
        for i, w in enumerate(words)
    ])
    graph = _resolver(reply, max_requests=2).interpret(canonical, operations=OPERATIONS)
    assert graph is not None
    interpreted = [r for r in graph.requests if not r.unresolved]
    assert len(interpreted) == 2
    leftover = " ".join(r.source_text for r in graph.requests if r.unresolved)
    for w in words[2:]:
        assert w in leftover, f"{w} was dropped by the cap"


def test_dependency_keys_survive_a_skipped_entry() -> None:
    """Review defect 3: re-indexing after a skipped clause used to re-point depends_on integers."""
    canonical = _canonical("price of gold, then junk, then how much silver that buys")
    reply = _reply(requests=[
        _req("r1", "price of gold", operation="market.quote", slots=[_slot("gold spot price", _op("subject", "gold"), key="s1")]),
        42,  # skipped
        _req("r3", "how much silver that buys", operation="market.quote",
             slots=[_slot("silver amount", _op("target", "silver"), _op("payment", result_of="s1"))],
             depends_on=[{"request": "r1", "kind": "value", "slot": "s1"}]),
    ])
    graph = _resolver(reply).interpret(canonical, operations=OPERATIONS)
    assert graph is not None
    edge = next(d for d in graph.dependencies if d.kind is DependencyKind.VALUE)
    gold_req = next(r for r in graph.requests if r.source_text == "price of gold")
    silver_req = next(r for r in graph.requests if r.source_text == "how much silver that buys")
    assert edge.from_request == silver_req.id and edge.to_request == gold_req.id
    assert edge.value_ref == gold_req.slot_ids[0]
    proposals = proposals_from_graph(graph)
    silver = next(p for p in proposals if p.request_text == "how much silver that buys")
    assert proposals[silver.depends_on[0]].request_text == "price of gold"


def test_repeated_entity_across_two_requests_binds_two_occurrences() -> None:
    """Review defect 4: the same surface in two clauses used to bind the first occurrence twice."""
    canonical = _canonical("weather in Paris and the population of Paris")
    reply = _reply(requests=[
        _req("r1", "weather in Paris", operation="weather.forecast", slots=[_slot("weather", _op("subject", "Paris"))]),
        _req("r2", "the population of Paris", slots=[_slot("population", _op("subject", "Paris"))]),
    ])
    graph = _resolver(reply).interpret(canonical, operations=OPERATIONS)
    assert graph is not None
    occurrences = sorted(m.occurrence for m in graph.mentions)
    assert occurrences == [0, 1]
    second = next(m for m in graph.mentions if m.occurrence == 1)
    assert second.span.start >= graph.requests[1].span.start


# -- unknown preserved, never fabricated ---------------------------------------------


def test_entity_not_in_text_mints_no_span_and_marks_the_slot_unresolved() -> None:
    canonical = _canonical("what is the price of gold")
    reply = _reply(requests=[_req("r1", "what is the price of gold", operation="market.quote",
                                  slots=[_slot("price", _op("subject", "gold"), _op("subject", "platinum"))])])
    graph = _resolver(reply).interpret(canonical, operations=OPERATIONS)
    assert graph is not None
    assert [m.surface for m in graph.mentions] == ["gold"]
    (slot,) = graph.slots
    assert slot.state is InterpretationState.UNRESOLVED and "platinum" in slot.reason


def test_empty_decomposition_is_not_nothing_asked() -> None:
    canonical = _canonical("do the thing with the flibbertigibbet")
    graph = _resolver('{"requests": []}').interpret(canonical, operations=OPERATIONS)
    assert graph is not None
    assert graph.requests and all(r.unresolved for r in graph.requests)
    assert "flibbertigibbet" in graph.requests[0].source_text
    assert proposals_from_graph(graph)[0].operation == "unknown"


def test_prohibition_only_reply_keeps_the_prohibition_and_mints_no_clause() -> None:
    canonical = _canonical("no web pls, and dont quote gold")
    reply = _reply(requests=[], prohibitions=[{"target": "web", "text": "no web pls"}, {"target": "gold", "text": "dont quote gold"}],
                   constraints=[{"kind": "retrieval_forbidden", "text": "no web"}])
    graph = _resolver(reply).interpret(canonical, operations=OPERATIONS)
    assert graph is not None
    assert [p.target for p in graph.prohibitions] == ["web", "gold"]
    assert any(c.kind is ConstraintKind.RETRIEVAL_FORBIDDEN for c in graph.constraints)
    assert not any(not r.unresolved for r in graph.requests)
    assert proposals_from_graph(graph) == ()  # nothing to admit; the prohibition is still there


def test_retraction_supersedes_the_named_request() -> None:
    canonical = _canonical("Search the web for XRP price. WAIT. Cancel the search before execution.")
    reply = _reply(requests=[_req("r1", "Search the web for XRP price.", operation="market.quote",
                                  slots=[_slot("XRP price", _op("subject", "XRP", entity="XRP"))])],
                   retractions=[{"target": "search", "text": "WAIT. Cancel the search before execution.", "supersedes": "r1"}])
    graph = _resolver(reply).interpret(canonical, operations=OPERATIONS)
    assert graph is not None
    assert graph.retractions[0].supersedes == graph.requests[0].id
    assert graph.shape is RequestShape.MID_TURN_CORRECTION
    assert proposals_from_graph(graph) == ()  # a cancelled obligation is not a clause to run


def test_unknown_state_and_kind_are_preserved_typed_not_invented() -> None:
    canonical = _canonical("price of gold by noon")
    reply = _reply(requests=[_req("r1", "price of gold", operation="market.quote", state="confident",
                                  slots=[_slot("gold price", _op("subject", "gold"))])],
                   constraints=[{"kind": "deadline", "text": "by noon", "requests": ["r1"]}])
    graph = _resolver(reply).interpret(canonical, operations=OPERATIONS)
    assert graph is not None
    assert graph.requests[0].state is InterpretationState.UNRESOLVED  # 'confident' is not a state
    (constraint,) = graph.constraints
    assert constraint.kind is ConstraintKind.USER_RESTRICTION and "deadline" in constraint.detail


# -- abstain-and-fallback ---------------------------------------------------------------


@pytest.mark.parametrize("junk", ["", "I think you want the weather", "```json\nnot json\n```", "{", "[1, 2"])
def test_unparseable_reply_abstains_never_guesses(junk: str) -> None:
    resolver = _resolver(junk)
    assert resolver.interpret(_canonical("do a thing"), operations=OPERATIONS) is None
    assert resolver.propose(_canonical("do a thing"), operations=OPERATIONS) == ()


def test_backend_exception_abstains() -> None:
    def boom(system: str, user: str) -> str:
        raise RuntimeError("provider down")

    assert ModelSemanticResolver(boom).interpret(_canonical("hi"), operations=OPERATIONS) is None


def test_non_string_and_enormous_replies_abstain() -> None:
    assert _resolver(lambda s, u: {"not": "a string"}).interpret(_canonical("hi"), operations=OPERATIONS) is None  # type: ignore[arg-type,return-value]
    giant = "{" + ("x" * 300_000) + "}"
    assert _resolver(giant).interpret(_canonical("x"), operations=OPERATIONS) is None


def test_finish_without_a_backend_is_the_runtime_path() -> None:
    resolver = ModelSemanticResolver()
    prepared = resolver.prepare(_canonical("price of gold"), operations=OPERATIONS, turn_id="t")
    assert prepared is not None
    reply = _reply(requests=[_req("r1", "price of gold", operation="market.quote", slots=[_slot("gold price", _op("subject", "gold"))])])
    graph = resolver.finish(prepared, reply)
    assert graph is not None and graph.turn_id == "t"
    assert resolver.finish(prepared, "nonsense") is None
    with pytest.raises(RuntimeError, match="needs a backend"):
        resolver.interpret(_canonical("x"), operations=OPERATIONS)


def test_array_embedded_in_prose_and_fences_is_extracted() -> None:
    canonical = _canonical("price of gold")
    reply = 'Sure! Here you go:\n```json\n{"requests":[{"key":"r1","source":"price of gold","operation":"market.quote"}]}\n```\nHope that helps.'
    (proposal,) = _resolver(reply).propose(canonical, operations=OPERATIONS)
    assert proposal.operation == "market.quote"


# -- breaker ----------------------------------------------------------------------------


def test_breaker_opens_after_consecutive_failures_and_stops_calling_backend() -> None:
    calls = {"n": 0}

    def boom(system: str, user: str) -> str:
        calls["n"] += 1
        raise RuntimeError("down")

    resolver = ModelSemanticResolver(boom, breaker=_Breaker(threshold=3, cooloff_s=60.0))
    for _ in range(3):
        assert resolver.interpret(_canonical("hi"), operations=OPERATIONS) is None
    assert calls["n"] == 3
    assert resolver.prepare(_canonical("hi"), operations=OPERATIONS) is None  # open: nothing prepared
    assert resolver.interpret(_canonical("hi"), operations=OPERATIONS) is None
    assert calls["n"] == 3


def test_breaker_closes_after_a_success() -> None:
    b = _Breaker(threshold=2, cooloff_s=60.0)
    b.record_failure()
    b.record_success()
    b.record_failure()
    assert not b.is_open()


# -- the contract can express the gold -------------------------------------------------------


def _gold_reply(case_id: str) -> str:
    """Hand-written replies in the reply contract for three gold cases."""
    if case_id == "rt1":
        return _reply(requests=[_req("r1", "how many litecoin one bitcoin buys", operation="market.quote",
            slots=[_slot("amount of litecoin one bitcoin buys",
                         _op("target", "litecoin", entity="LTC"),
                         _op("payment", "bitcoin", entity="BTC", quantity={"raw": "one bitcoin", "exact": "1", "asset": "BTC"}))])],
            constraints=[{"kind": "retrieval_required", "requests": ["r1"]}])
    if case_id == "pr2":
        return _reply(requests=[_req("r1", "Search the web for XRP price.", operation="market.quote",
            slots=[_slot("XRP price via web search", _op("subject", "XRP", entity="XRP"))])],
            constraints=[{"kind": "retrieval_required", "detail": "web", "requests": ["r1"]}],
            retractions=[{"target": "search", "text": "WAIT. Cancel the search before execution.", "supersedes": "r1"}])
    if case_id == "us1":
        return _reply(requests=[
            _req("r1", "if it rains in Tallinn tomorrow", operation="weather.forecast",
                 slots=[_slot("rain forecast for Tallinn tomorrow", _op("subject", "Tallinn", entity="Tallinn"))]),
            _req("r2", "tell me the price of gold", operation="market.quote",
                 slots=[_slot("gold spot price", _op("subject", "gold", entity="gold"))],
                 depends_on=[{"request": "r1", "kind": "conditional"}])],
            constraints=[{"kind": "time", "text": "tomorrow", "detail": "tomorrow", "requests": ["r1"]},
                         {"kind": "retrieval_required", "requests": ["r1", "r2"]}])
    raise KeyError(case_id)


@pytest.mark.parametrize("case_id", ["rt1", "pr2", "us1"])
def test_a_correct_reply_in_the_contract_scores_strict_whole_turn_against_gold(case_id: str) -> None:
    case = gold_corpus.gold_case(case_id)
    graph = _resolver(_gold_reply(case_id)).interpret(_canonical(case.text), operations=OPERATIONS, turn_id="t")
    assert graph is not None
    cmp = compare_graphs(gold_corpus.gold_graph(case_id), graph)
    assert cmp.whole_turn_correct, (case_id, cmp.failed_axes(), cmp.to_dict())


def test_parser_reports_repairs_as_text_free_notes() -> None:
    canonical = _canonical("price of gold and the weather in Rome")
    reply = _reply(requests=[_req("r1", "price of gold", slots=[_slot("gold price", _op("subject", "zorblax"))]), "junk"])
    parsed = parse_graph_reply(reply, canonical=canonical, turn_id="t")
    assert not parsed.abstained and parsed.graph is not None
    assert any(n.startswith("unfindable_entity") for n in parsed.notes)
    assert any(n.startswith("malformed_entry") for n in parsed.notes)
    assert "zorblax" not in " ".join(parsed.notes) and "Rome" not in " ".join(parsed.notes)


# -- the boundary: the resolver cannot mint authority ------------------------------------------


def test_a_proposed_unknown_operation_is_refused_by_admission() -> None:
    canonical = _canonical("do something exotic")
    reply = _reply(requests=[_req("r1", "do something exotic", operation="exotic.operation")])
    (proposal,) = _resolver(reply).propose(canonical, operations=OPERATIONS)
    assert proposal.operation == "exotic.operation"  # preserved; admission decides
    result = admit(proposal, canonical=canonical, contract_lookup=lambda name: None)
    assert result.admitted is False and result.reason is ReasonCode.UNKNOWN_OPERATION


def test_proposals_and_graphs_carry_no_authority_fields() -> None:
    canonical = _canonical("price of gold")
    reply = _reply(requests=[_req("r1", "price of gold", operation="market.quote")])
    resolver = _resolver(reply)
    (proposal,) = resolver.propose(canonical, operations=OPERATIONS)
    graph = resolver.interpret(canonical, operations=OPERATIONS)
    assert isinstance(proposal, IntentProposal) and graph is not None
    for forbidden in ("side_effect_class", "approval_requirement", "permission_actions", "read_only", "allowed", "admitted"):
        assert not hasattr(proposal, forbidden) and not hasattr(graph, forbidden)
        assert not any(hasattr(r, forbidden) for r in graph.requests)
