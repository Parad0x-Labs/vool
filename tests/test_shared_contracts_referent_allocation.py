"""The four shared contracts behind the owner's 2026-09-10 23:41-23:50 transcript (FINDINGS F15).

Measured on the c647b707 native app: an allocation ("1000 EUR split equally in 4 parts, buy ADA,
Tesla shares, gold and Chinese yuan") was cut into four fragments and lost three of its outputs;
"check it on the internet" after an unresolved price ask was adjudicated for entity ambiguity;
"why?" after a failed lookup explained itself or was adjudicated; a fragment's notice denied a
lookup the same turn had made; and VERIFIED stood on every failed turn. Each contract is pinned
here at its owning seam, deterministically -- no provider, no network; quotes and fx are scripted
where a computation must be shown to bind.
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

import pytest

from core.conductor import operations as op
from core.conductor import planner

# --- 1. allocation / dependency representation -------------------------------------------------

OWNER_ALLOCATION = (
    "ok another one I have 1000eur, i want to split it eaqully in 4 parths and  buy -ADA, "
    "TESLA shares, gold and chinese yuan. How mcuh of each i will get?"
)
CLEAN_ALLOCATION = (
    "I have 1000 EUR, split it equally in 4 parts and buy ADA, Tesla shares, gold and Chinese "
    "yuan. How much of each will I get?"
)
BASE_BRONZE = (
    "i have 15 base coins, I want to sell half of them to buy BTC and other half to buy bronze. "
    "How much of each I will get?"
)


def _clauses(text: str):
    return planner._deterministic_purchasable_amount_plan(text)


def test_the_owner_allocation_reads_four_targets_four_parts_and_a_currency_payer() -> None:
    roles = op.allocation_purchase_roles(OWNER_ALLOCATION)
    assert roles is not None, "typo-folded split cues ('eaqully', 'parths') must still read"
    assert roles.payment is not None and roles.payment.kind == "currency" and roles.payment.key == "EUR"
    assert roles.quantity == 1000.0 and roles.parts == 4 and roles.share == 250.0
    assert [t.text for t in roles.targets] == ["ada", "tesla", "gold", "chinese yuan"]
    assert [getattr(t.role, "kind", None) for t in roles.targets] == ["crypto", None, "commodity", "currency"]
    assert roles.problem == ""


def test_every_allocation_obligation_is_conserved_as_one_clause() -> None:
    clauses = _clauses(CLEAN_ALLOCATION)
    by_request = {c.request: c for c in clauses}
    # Quotes for the priced assets, one fx bridge for the currency sum, one output per target.
    assert "price of ada" in by_request and "price of gold" in by_request
    assert "how much is 250 EUR in USD" in by_request
    ada = by_request["how much ada can I buy with 250 eur"]
    gold = by_request["how much gold can I buy with 250 eur"]
    assert ada.operation == "quantitative_reasoning" and gold.operation == "quantitative_reasoning"
    bridge = by_request["how much is 250 EUR in USD"].index
    assert bridge in ada.depends_on and by_request["price of ada"].index in ada.depends_on
    assert bridge in gold.depends_on and by_request["price of gold"].index in gold.depends_on
    tesla = by_request["how much tesla can I buy with 250 eur"]
    assert "no price source for tesla" in tesla.unresolved_reason
    yuan = by_request["how much is 250 EUR in CNY"]
    assert yuan.operation == "fx_quote" and not yuan.unresolved_reason
    assert all(c.origin == planner._RUNTIME_OWNED for c in clauses)
    # The whole turn belongs to the purchase: no unit is foreign, so no fragment is split off.
    assert planner._purchase_partition(OWNER_ALLOCATION)[1] == ()


def test_an_unresolved_ticker_payer_earns_one_identifying_question_and_keeps_the_rest() -> None:
    clauses = _clauses(BASE_BRONZE)
    requests = [c.request for c in clauses]
    assert "price of btc" in requests, "the BTC quote is still served"
    btc = next(c for c in clauses if c.request.startswith("how much btc"))
    bronze = next(c for c in clauses if c.request.startswith("how much bronze"))
    assert "'BASE' is a ticker this runtime cannot resolve" in btc.unresolved_reason
    assert "full name" in btc.unresolved_reason, "the ONE identifying question, not a guess"
    assert "no price source for bronze" in bronze.unresolved_reason
    roles = op.allocation_purchase_roles(BASE_BRONZE)
    assert roles is not None and roles.parts == 2 and roles.share == 7.5 and roles.payment is None
    assert roles.payment_ticker_shaped is True


def test_a_split_between_targets_without_a_buy_verb_and_an_asset_payer() -> None:
    text = "I have 2 eth, split it evenly between btc and gold, how much of each do I get?"
    roles = op.allocation_purchase_roles(text)
    assert roles is not None and [t.text for t in roles.targets] == ["btc", "gold"]
    assert roles.payment is not None and roles.payment.kind == "crypto" and roles.share == 1.0
    clauses = _clauses(text)
    btc = next(c for c in clauses if c.request == "how much btc can I buy with 1 eth")
    eth_quote = next(c for c in clauses if c.request == "price of eth")
    assert eth_quote.index in btc.depends_on and not btc.unresolved_reason


def test_a_parts_and_targets_mismatch_is_stated_not_guessed() -> None:
    text = "I have 900 usd, split it into 3 parts and buy btc, eth, sol and ada. how much of each?"
    clauses = _clauses(text)
    derivations = [c for c in clauses if c.operation == "quantitative_reasoning"]
    assert len(derivations) == 4
    assert all("3 equal parts but named 4 things" in c.unresolved_reason for c in derivations)
    assert sum(1 for c in clauses if c.operation == "market_quote") == 4, "quotes still served"


def test_the_single_purchase_controls_do_not_read_as_allocations() -> None:
    for text in (
        "what is the Brent oil price? how much i can buy of oil if i sell 1kg of silver? and how much eth as well",
        "what is the price of ETH and how much of silver I can buy if I sell 1 eth? also how much of gold",
        "how much gold and how much silver can I buy with one bitcoin",
    ):
        assert op.allocation_purchase_roles(text) is None, text
        clauses = _clauses(text)
        assert sum(1 for c in clauses if c.operation == "quantitative_reasoning") == 2, text
        assert not any(c.unresolved_reason for c in clauses), text


QUOTES = {
    "cardano": 0.21, "gold": 4402.0, "bitcoin": 77111.0, "ethereum": 2433.8,
}


def _fake_market(subtask, timeout_s=None):
    from core.live_data_plan import SubtaskLifecycle, SubtaskOutcome

    key = str((subtask.arguments or {}).get("asset_key") or subtask.entity).casefold().replace(" ", "_")
    for name, price in QUOTES.items():
        if name in key or key in name:
            return SubtaskOutcome(
                subtask=subtask, state=SubtaskLifecycle.SUCCEEDED,
                result={"price": price, "currency": "USD", "source": "scripted", "change_24h_pct": 0.0, "retrieved_at": "2026-09-10T20:00:00Z"},
            )
    return SubtaskOutcome(subtask=subtask, state=SubtaskLifecycle.FAILED, failure_reason="no scripted quote")


class _Fx:
    name = "scripted-fx"

    def quote(self, base, quote, timeout_s=None):
        from core.fresh_data.fx import FxQuote, FxQuoteStatus

        rates = {("EUR", "USD"): "1.1", ("EUR", "CNY"): "7.8"}
        rate = rates.get((base.upper(), quote.upper()))
        if rate is None:
            return FxQuote(base=base, quote=quote, status=FxQuoteStatus.UNAVAILABLE, source=self.name, failure_reason="no scripted rate")
        return FxQuote(base=base, quote=quote, status=FxQuoteStatus.AVAILABLE, rate=Decimal(rate), observed_at="2026-09-10T20:00:00Z", retrieved_at="2026-09-10T20:00:00Z", source=self.name)


def test_the_allocation_binds_every_share_from_retrieved_figures_only() -> None:
    from core.conductor import compose_answer, plan_conductor_turn, run_conductor_plan
    from core.conductor.product_decision import ExecutionReport, reduce_execution_report
    from core.conductor.registry import NodeContext

    calls = {"model": 0}

    def no_model(_system, _prompt):
        calls["model"] += 1
        return ""

    plan = plan_conductor_turn(CLEAN_ALLOCATION, ask_model=no_model, propose_semantics=no_model)
    assert plan is not None and calls["model"] == 0, "runtime-owned: no model consulted"
    with mock.patch("core.agent_runtime.live_data_runner._run_market_subtask", _fake_market), \
         mock.patch("core.conductor.fresh_data_operations._configured_fx_providers", lambda ctx: (_Fx(),)), \
         mock.patch("core.conductor.fresh_data_operations._runtime_retrieval_allowed", lambda ctx: True):
        outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=no_model, timeout_s=5.0), plan_deadline_s=20.0)
    assert calls["model"] == 0
    decision = reduce_execution_report(ExecutionReport(
        bound_plan=plan.bound_plan, node_outcomes=tuple(outcomes),
        planned_node_ids=tuple(n.node_id for n in plan.nodes), requirement_nodes=dict(plan.requirement_nodes),
    ))
    text = compose_answer(plan, outcomes, decision).text
    # 250 EUR = 275 USD at the scripted rate: ADA 275 / 0.21, gold 275 / 4402.
    assert ("275" in text and "1,309." in text) or "1309." in text, text
    assert "0.0625" in text, text
    assert "1,950" in text or "1950" in text, text  # 250 EUR x 7.8 = 1950 CNY
    assert "no price source for tesla" in text, text
    assert "Could not be answered" in text or "not answered" in text.casefold()
    assert decision.disposition.value != "fulfilled", "the Tesla share is a stated non-fulfilment"


def test_the_base_question_stands_beside_the_btc_quote_in_the_composed_answer() -> None:
    """A runtime-STATED refusal is its own row: the composer must not drop the BASE identifying
    question as 'covered' by the Bitcoin quote it sits next to (measured before the repair: only
    the bronze row survived)."""
    from core.conductor import compose_answer, plan_conductor_turn, run_conductor_plan
    from core.conductor.product_decision import ExecutionReport, reduce_execution_report
    from core.conductor.registry import NodeContext

    plan = plan_conductor_turn(BASE_BRONZE, ask_model=lambda s, p: "", propose_semantics=lambda s, p: "")
    assert plan is not None
    with mock.patch("core.agent_runtime.live_data_runner._run_market_subtask", _fake_market):
        outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=lambda s, p: "", timeout_s=5.0), plan_deadline_s=20.0)
    decision = reduce_execution_report(ExecutionReport(
        bound_plan=plan.bound_plan, node_outcomes=tuple(outcomes),
        planned_node_ids=tuple(n.node_id for n in plan.nodes), requirement_nodes=dict(plan.requirement_nodes),
    ))
    text = compose_answer(plan, outcomes, decision).text
    assert "Bitcoin: 77111.0 USD" in text, text
    assert "'BASE' is a ticker this runtime cannot resolve" in text, text
    assert "no price source for bronze" in text, text
    assert decision.disposition.value == "partially_fulfilled"


# --- 2. conversational referent: a lookup instruction is the obligation again -------------------


def test_a_lookup_instruction_names_nothing_new_and_is_a_bare_re_ask() -> None:
    from core.live_data_continuation import _continuation_residue, utterance_carries_no_independent_request

    for text in (
        "check it on the internet",
        "why dont u jsut check  it on inernet ffs ?!",
        "look it up online please",
        "just google it",
    ):
        assert _continuation_residue(text) == [], text
        assert utterance_carries_no_independent_request(text), text
    # Naming something new is NOT an instruction to repeat the pending lookup.
    assert _continuation_residue("check the weather in Riga") not in ([], None)
    assert _continuation_residue("what about solana") == ["solana"]
    assert _continuation_residue("why?") == ["why"]


# --- 3. capability routing before entity-ambiguity adjudication ----------------------------------


def test_the_ambiguity_probe_stands_down_for_follow_up_intents_and_re_asks(monkeypatch, tmp_path) -> None:
    from core.entity_ambiguity import single_plain_know_question

    assert single_plain_know_question("why?") is False
    assert single_plain_know_question("what went wrong") is False
    # A re-ask against an obligation on the table: the continuation authority answers first.
    monkeypatch.setattr(
        "core.live_data_continuation.continuation_inherits_live_data",
        lambda text, *, source_context=None: "check $BASE price" if source_context else "",
    )
    assert single_plain_know_question("why dont u jsut check it on inernet ffs", source_context={"runtime_session_id": "s"}) is False
    # Without an obligation the probe keeps its jurisdiction over a genuine plain question.
    assert single_plain_know_question("who is the mayor of Springfield?") is True


# --- 4. faithful reconciliation --------------------------------------------------------------------


def test_a_follow_up_never_binds_to_its_own_attempt(monkeypatch) -> None:
    from core import attempt_followup as af

    own = {"attempt_id": "a-self", "original_request_snapshot": "why?", "lifecycle_state": "RUNNING"}
    prior = {"attempt_id": "a-prior", "original_request_snapshot": "check $BASE price", "lifecycle_state": "PARTIAL_SUCCESS"}
    calls: list[str] = []

    def fake_latest(session_id, *, exclude_attempt_id=""):
        calls.append(exclude_attempt_id)
        return prior if exclude_attempt_id == "a-self" else own

    monkeypatch.setattr("core.runtime_continuity.latest_unresolved_or_partial_attempt", fake_latest)
    attempt, reason = af.resolve_followup_attempt("s", "why?", af.EXPLAIN_ATTEMPT_FAILURE, exclude_attempt_id="")
    assert attempt is prior and "latest unresolved" in reason
    assert calls == ["", "a-self"], "the self record is stepped over by content, then by id"


def test_a_stated_non_fulfilment_makes_the_proof_state_incomplete_not_verified() -> None:
    from core.proof_projection import _fulfilment_gaps

    events = [
        {"event_type": "task_received", "message": "Received request: why?"},
        {"event_type": "task_completed", "message": "Fast-path response ready: ...", "status": "ambiguity_adjudication_unresolved"},
        {"event_type": "runtime_attempt_completed", "message": "Runtime attempt attempt-1 -> PARTIAL_SUCCESS."},
    ]
    gaps = _fulfilment_gaps(events, {"canonical_content": "Could not be answered:\n- price of ada"})
    assert gaps == ["unfulfilled_route", "attempt_unfulfilled", "unanswered_rows"]
    assert _fulfilment_gaps([{"event_type": "task_completed", "message": "ok", "status": "conductor_multi_intent_plan"}], {"canonical_content": "Gold: 4402 USD"}) == []


def test_a_failed_turns_projected_proof_is_incomplete_not_verified() -> None:
    """Through the projection entry point itself, against the real stores: a served turn whose own
    record carries a `task_failed` row projects INCOMPLETE with the gap named, even though its
    finalization row hash-matches (which alone used to earn VERIFIED)."""
    from core.proof_projection import STATE_INCOMPLETE, STATE_VERIFIED, build_turn_proof
    from tests.test_proof_projection import _bind_turn, _emit_turn_event, _insert_finalization

    session, request, turn = "scc-sess-f15", "scc-req-f15", "scc-turn-f15"
    _bind_turn(session, request, "I couldn't produce a normal chat response for that request.")
    _insert_finalization(request, turn, "I couldn't produce a normal chat response for that request.")
    _emit_turn_event(session, turn, request, "task_received", {"message": "Received request: check $BASE price"})
    _emit_turn_event(session, turn, request, "task_failed", {"status": "model_tool_intent_tool_synthesis_failed"})
    proof = build_turn_proof(session_id=session, request_id=request)
    assert proof["state"] == STATE_INCOMPLETE, proof.get("state")
    assert "task_failed" in list(proof.get("state_reasons") or []), proof.get("state_reasons")

    clean_session, clean_request, clean_turn = "scc-sess-f15-ok", "scc-req-f15-ok", "scc-turn-f15-ok"
    _bind_turn(clean_session, clean_request, "Gold: 4402 USD.")
    _insert_finalization(clean_request, clean_turn, "Gold: 4402 USD.")
    _emit_turn_event(clean_session, clean_turn, clean_request, "task_completed", {"status": "conductor_multi_intent_plan"})
    clean = build_turn_proof(session_id=clean_session, request_id=clean_request)
    assert clean["state"] == STATE_VERIFIED, clean.get("state_reasons")


def test_the_withheld_figures_notice_names_the_part_when_it_is_a_planned_sub_turn() -> None:
    from core.model_output_guard import unverified_live_value_notice

    whole = unverified_live_value_notice(("price",), "how much of each will I get?")
    part = unverified_live_value_notice(("price",), "and buy ADA", part_of_turn=True)
    assert whole.startswith("I didn't run any live lookup on this turn")
    assert part.startswith("I didn't run a live lookup for this part of the request (and buy ADA)")


def test_a_quote_that_did_not_come_back_is_a_transport_fact_not_an_internal_fault() -> None:
    from core.conductor.node import ConductorNode, NodeFailureCode
    from core.conductor.registry import NodeContext
    from core.live_data_plan import SubtaskLifecycle, SubtaskOutcome

    def dead_source(subtask, timeout_s=None):
        return SubtaskOutcome(subtask=subtask, state=SubtaskLifecycle.FAILED, failure_reason="HTTPError: 429 Too Many Requests")

    node = ConductorNode(node_id="p:market_quote:ethereum", operation="market_quote", request_text="price of eth", arguments={"asset_key": "ethereum", "entity": "Ethereum", "kind": "crypto"})
    with mock.patch("core.agent_runtime.live_data_runner._run_market_subtask", dead_source):
        with pytest.raises(ConnectionError):
            op._market_run(node, NodeContext(run_generation=lambda s, p: "", timeout_s=2.0))
    from core.conductor.compose import reason_for_outcome
    from core.conductor.node import NodeLifecycle, NodeOutcome

    outcome = NodeOutcome(node=node, state=NodeLifecycle.FAILED, failure_code=NodeFailureCode.TRANSPORT_FAILED, failure_reason="transport_failed")
    assert "could not be reached" in reason_for_outcome(outcome)


# --- 5. the $TICKER price ask is owned by the live-data lane --------------------------------------


def test_a_ticker_written_as_one_is_a_live_data_ask_with_a_stated_unsupported_entity() -> None:
    from core.agent_runtime.fast_live_info_price import ticker_mentions
    from core.execution_requirements import _live_data_classification
    from core.live_data_plan import build_live_data_plan

    assert ticker_mentions("check $BASE price and u will solve the requests :D") == ["BASE"]
    assert ticker_mentions("$BASE coin should not be hard to find the value of it isnt?") == ["BASE"]
    assert ticker_mentions("explain how a stablecoin token works") == []
    assert _live_data_classification("check $BASE price and u will solve the requests :D") is not None
    assert _live_data_classification("$BASE coin should not be hard to find the value of it isnt?") is not None
    plan = build_live_data_plan("check $BASE price and u will solve the requests :D", plan_id="p", attempt_id="a")
    assert plan is not None
    unsupported = [s for s in plan.subtasks if s.operation == "unsupported_market_entity"]
    assert [s.entity for s in unsupported] == ["BASE"]
    assert "full name" in unsupported[0].arguments["reason"]
    resolved = build_live_data_plan("what is the price of $ada and $sol", plan_id="p", attempt_id="a")
    assert resolved is not None and sorted(s.entity for s in resolved.subtasks) == ["Cardano", "Solana"]
