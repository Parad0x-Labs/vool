"""Correction 1 / F3: complete versus partial chain fee evidence.

SIMULATED CHAINS. An OP-stack receipt (Base) without its `l1Fee`, or with a malformed one, is incomplete evidence: the
transfer settles with the principal confirmed and the fee BOUNDED by the approved ceiling (the ledger keeps counting the
ceiling, the receipt says so), and a later receipt that carries the component refines the fee once. A zero `l1Fee` with
no operator fields is a complete zero by the chain's own rule (the Isthmus fields exist iff non-zero); exactly one
operator field is malformed. Rows without an L1 component (Ethereum, BNB Smart Chain, Robinhood Chain) settle exact from
gas and price. A failed execution counts zero principal and the same bounded-then-refined fee. Repeated observation of
complete evidence accounts once.
"""
from __future__ import annotations

import json

import pytest
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain
from tests.wallet.test_crypto_pilot_dispatch_evm import (
    BASE_MAINNET,
    _approve,
    _base,
    _hold,
    _pending,
    _quote,
    _ready_on_base,
    _ready_pilot_wallet,
    _route,
)
from tests.wallet.test_crypto_pilot_dispatch_evm import pilot as evm_pilot

pytestmark = [pytest.mark.safety]

pilot = evm_pilot  # the EVM dispatch corpus's environment fixture, under its own name here


def _edit_receipts(monkeypatch, chain, edit):
    """Serve every `eth_getTransactionReceipt` result through `edit` (the JSON-RPC `result` object); returns the restore."""
    original = chain._answer

    def edited(body):
        kind, answer = original(body)
        if body.get("method") == "eth_getTransactionReceipt" and isinstance(answer, dict) and isinstance(answer.get("result"), dict):
            answer = dict(answer)
            answer["result"] = edit(dict(answer["result"]))
        return kind, answer

    monkeypatch.setattr(chain, "_answer", edited)
    return lambda: monkeypatch.setattr(chain, "_answer", original)


def _without(*fields):
    def edit(result):
        for field in fields:
            result.pop(field, None)
        return result
    return edit


def _with(field, value):
    def edit(result):
        result[field] = value
        return result
    return edit


def _actual(chain) -> int:
    receipt = next(iter(chain.receipts.values()))
    return int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16) + chain.l1_fee + chain.operator_fee


def _evidence(proposal_id: str, kind: str) -> list:
    from core.wallet import transfers

    row = transfers.get_transfer_by_id(proposal_id)
    return [e for e in json.loads(row["evidence_json"]) if isinstance(e, dict) and e.get("kind") == kind]


def test_a_receipt_without_the_l1_fee_settles_bounded_and_is_refined_once_the_chain_serves_it(pilot):
    """The reviewer's original workflow: Base charges the full fee, the receipt omits `result.l1Fee`."""
    from core.wallet import settlement, transfers

    chain = _base(pilot)
    try:
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        quote = _quote(proposal.proposal_id)
        restore = _edit_receipts(pilot, chain, _without("l1Fee"))
        transfer = _approve(engine, proposal.proposal_id, quote)["transfer"]
        actual = _actual(chain)
        ceiling = int(quote["fields"]["fee_max_minor"])
        assert transfer["state"] == "confirmed", "the principal is confirmed: the receipt is canonical and succeeded"
        assert transfer["charged_fee_minor"] is None and transfer["fee_state"] == "bounded", transfer
        assert transfer["fee_missing"] == ["l1Fee"] and int(transfer["fee_known_minor"]) == actual - chain.l1_fee
        assert "at most" in transfer["charged_fee_label"] and "l1Fee" in transfer["charged_fee_label"]
        assert _hold(proposal.proposal_id) == (10**15, ceiling, "settled"), "the ledger keeps the approved ceiling counted"
        assert transfer["in_flight"] is False
        # the chain serves the complete receipt: the fee is refined once, the ledger settles to the exact charge
        restore()
        settlement.observe_open_transfers()
        refined = transfers.latest_receipt(proposal.proposal_id)
        assert (refined["state"], refined["fee_state"], refined["charged_fee_minor"], refined["fee_missing"]) == ("confirmed", "exact", str(actual), [])
        assert _hold(proposal.proposal_id) == (10**15, actual, "settled") and actual < ceiling
        assert len(_evidence(proposal.proposal_id, "fee_refined")) == 1
        # a further observation accounts nothing twice
        settlement.observe_open_transfers()
        assert _hold(proposal.proposal_id) == (10**15, actual, "settled") and len(_evidence(proposal.proposal_id, "fee_refined")) == 1
        assert len(chain.sent) == 1
    finally:
        chain.__exit__(None, None, None)


def test_a_malformed_l1_fee_is_incomplete_evidence_never_zero(pilot):
    from core.wallet import settlement, transfers

    chain = _base(pilot)
    try:
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        quote = _quote(proposal.proposal_id)
        restore = _edit_receipts(pilot, chain, _with("l1Fee", "0xzz"))
        transfer = _approve(engine, proposal.proposal_id, quote)["transfer"]
        assert (transfer["state"], transfer["fee_state"], transfer["charged_fee_minor"], transfer["fee_missing"]) == ("confirmed", "bounded", None, ["l1Fee"])
        assert _hold(proposal.proposal_id)[1] == int(quote["fields"]["fee_max_minor"])
        restore()
        settlement.observe_open_transfers()
        refined = transfers.latest_receipt(proposal.proposal_id)
        assert (refined["fee_state"], refined["charged_fee_minor"]) == ("exact", str(_actual(chain)))
    finally:
        chain.__exit__(None, None, None)


def test_a_zero_l1_fee_with_no_operator_fields_is_a_complete_zero_by_the_chain_rule(pilot):
    """`l1Fee: 0x0` is reported; the operator fields are absent because both are zero (the Isthmus rule)."""
    chain = ScriptedEvmNativeChain(chain_id=8453, fee_model="op_stack", l1_fee=0, operator_fee=0)
    chain.__enter__()
    try:
        _route(pilot, BASE_MAINNET, chain.url)
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
        receipt = next(iter(chain.receipts.values()))
        assert receipt["l1Fee"] == "0x0" and "operatorFeeScalar" not in receipt
        exact = int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16)
        assert (transfer["fee_state"], transfer["charged_fee_minor"], transfer["fee_missing"]) == ("exact", str(exact), [])
        assert _hold(proposal.proposal_id) == (10**15, exact, "settled")
    finally:
        chain.__exit__(None, None, None)


def test_exactly_one_operator_field_is_malformed_evidence(pilot):
    from core.wallet import settlement, transfers

    chain = _base(pilot)
    try:
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        quote = _quote(proposal.proposal_id)
        restore = _edit_receipts(pilot, chain, _without("operatorFeeConstant"))
        transfer = _approve(engine, proposal.proposal_id, quote)["transfer"]
        assert (transfer["fee_state"], transfer["charged_fee_minor"], transfer["fee_missing"]) == ("bounded", None, ["operatorFee"])
        assert int(transfer["fee_known_minor"]) == _actual(chain) - chain.operator_fee
        restore()
        settlement.observe_open_transfers()
        assert transfers.latest_receipt(proposal.proposal_id)["charged_fee_minor"] == str(_actual(chain))
    finally:
        chain.__exit__(None, None, None)


@pytest.mark.parametrize(("network", "chain_id", "fee_model", "extra"), [
    ("eip155:1", 1, "eip1559", {}),
    ("eip155:56", 56, "bsc", {}),
    ("eip155:4663", 4663, "arbitrum", {"gas_used_for_l1": 1_000}),
])
def test_rows_without_an_l1_component_settle_exact_from_gas_and_price(pilot, network, chain_id, fee_model, extra):
    from core.wallet import capabilities, settlement, transfers

    pilot.setattr(capabilities, "PILOT_TRANSFER_READY_ROWS", frozenset({network}))
    chain = ScriptedEvmNativeChain(chain_id=chain_id, fee_model=fee_model, base_fee=2_000_000_000, priority_fee=1_000_000_000, **extra)
    chain.__enter__()
    try:
        _route(pilot, network, chain.url)
        wallet = _ready_pilot_wallet(network)
        chain.fund(wallet["address"], 10**18)
        engine, proposal = _pending(wallet, network, asset="BNB" if fee_model == "bsc" else "ETH")
        _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        receipt = next(iter(chain.receipts.values()))
        assert "l1Fee" not in receipt
        exact = int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16)
        assert (view["state"], view["fee_state"], view["charged_fee_minor"], view["fee_missing"]) == ("confirmed", "exact", str(exact), [])
        assert _hold(proposal.proposal_id) == (10**15, exact, "settled")
    finally:
        chain.__exit__(None, None, None)


def test_a_failed_execution_with_partial_fee_evidence_counts_zero_principal_and_a_bounded_fee_until_refined(pilot):
    from core.wallet import proposals, settlement, transfers

    chain = _base(pilot)
    try:
        chain.mine_mode = "revert"
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        quote = _quote(proposal.proposal_id)
        chain.finalized_block = 50
        restore = _edit_receipts(pilot, chain, _without("l1Fee"))
        assert _approve(engine, proposal.proposal_id, quote)["transfer"]["state"] == "pending"
        chain.finalized_block = chain.block_number
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        assert (view["state"], view["fee_state"], view["charged_fee_minor"], view["fee_missing"]) == ("failed_on_chain", "bounded", None, ["l1Fee"])
        assert _hold(proposal.proposal_id) == (0, int(quote["fields"]["fee_max_minor"]), "settled"), "no principal moved; the fee is bounded by the ceiling"
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_FAILED
        restore()
        settlement.observe_open_transfers()
        refined = transfers.latest_receipt(proposal.proposal_id)
        assert (refined["state"], refined["fee_state"], refined["charged_fee_minor"]) == ("failed_on_chain", "exact", str(_actual(chain)))
        assert _hold(proposal.proposal_id) == (0, _actual(chain), "settled")
    finally:
        chain.__exit__(None, None, None)


def test_repeated_observation_of_complete_evidence_accounts_once(pilot):
    from core.wallet import settlement, transfers

    chain = _base(pilot)
    try:
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
        assert (transfer["fee_state"], transfer["charged_fee_minor"]) == ("exact", str(_actual(chain)))
        before = _hold(proposal.proposal_id)
        settlement.observe_open_transfers()
        settlement.observe_open_transfers()
        assert _hold(proposal.proposal_id) == before == (10**15, _actual(chain), "settled")
        assert _evidence(proposal.proposal_id, "fee_refined") == []
        assert transfers.latest_receipt(proposal.proposal_id)["fee_state"] == "exact"
    finally:
        chain.__exit__(None, None, None)
