"""Correction 2 / R3: fork-aware fee evidence and exact settlement on OP-stack rows.

SIMULATED CHAINS. The operator fee's formula depends on the fork the receipt's block was produced under: Isthmus charges
`gas × scalar ÷ 1e6 + constant`, Jovian `gas × scalar × 100 + constant`. The applicable fork is established from the
canonical block header's `extraData` version byte (0 before Jovian, 1 from Jovian on) corroborated by the receipt's
Jovian fields (`daFootprintGasScalar`, `blobGasUsed`); unknown or inconsistent evidence leaves the operator component
bounded, never exact. Zero-operator receipts, missing `l1Fee`, non-OP rows, failed principals and exact-once
refinement keep their contracts. The quote's operator ceiling is the chain oracle's (`getOperatorFee`), fork-correct by
construction.
"""
from __future__ import annotations

import pytest

from tests.wallet._rig_evm_native import ScriptedEvmNativeChain
from tests.wallet.test_crypto_pilot_dispatch_evm import (
    BASE_MAINNET,
    _approve,
    _hold,
    _pending,
    _quote,
    _ready_pilot_wallet,
    _route,
)
from tests.wallet.test_crypto_pilot_dispatch_evm import pilot as evm_pilot

pytestmark = [pytest.mark.safety]

pilot = evm_pilot
GAS, PRICE, L1, SCALAR, CONSTANT = 21_000, 2, 300, 100, 7
JOVIAN_TOTAL = GAS * PRICE + L1 + GAS * SCALAR * 100 + CONSTANT  # 210_042_307
ISTHMUS_TOTAL = GAS * PRICE + L1 + GAS * SCALAR // 10**6 + CONSTANT  # 42_309
V0 = "0x00" + format(250, "08x") + format(6, "08x")
V1 = "0x01" + format(250, "08x") + format(6, "08x") + format(0, "016x")


def _receipt(*, jovian_fields: bool = True, operator: bool = True) -> dict:
    receipt = {"gasUsed": hex(GAS), "effectiveGasPrice": hex(PRICE), "l1Fee": hex(L1)}
    if operator:
        receipt.update({"operatorFeeScalar": hex(SCALAR), "operatorFeeConstant": hex(CONSTANT)})
    if jovian_fields:
        receipt.update({"daFootprintGasScalar": hex(400), "blobGasUsed": hex(40_000)})
    return receipt


def _base_spec():
    from core.wallet import chains

    return chains.resolve_network(BASE_MAINNET)


# --- the evidence rule ---------------------------------------------------------------------------------------------

def test_the_reviewers_jovian_receipt_is_bounded_without_header_evidence_and_exact_with_a_version_1_header():
    from core.wallet import settlement

    alone = settlement.evm_fee_evidence(_receipt(), _base_spec())
    assert alone.total is None and "operatorFee" in alone.missing and alone.fork == "unknown", alone
    assert alone.known_minor == GAS * PRICE + L1, "the components that are read stay counted as the known subtotal"
    proven = settlement.evm_fee_evidence(_receipt(), _base_spec(), block={"extraData": V1})
    assert (proven.total, proven.fork, proven.missing) == (JOVIAN_TOTAL, "jovian", ())


def test_an_isthmus_receipt_under_a_version_0_header_uses_the_isthmus_formula():
    from core.wallet import settlement

    proven = settlement.evm_fee_evidence(_receipt(jovian_fields=False), _base_spec(), block={"extraData": V0})
    assert (proven.total, proven.fork) == (ISTHMUS_TOTAL, "isthmus")


@pytest.mark.parametrize(("receipt", "block", "why"), [
    (_receipt(jovian_fields=True), {"extraData": V0}, "jovian receipt fields under a pre-Jovian header"),
    (_receipt(jovian_fields=False), {"extraData": V1}, "a Jovian header without the receipt's DA footprint"),
    (_receipt(jovian_fields=True), {"extraData": "0x02" + "00" * 16}, "an extraData version this owner does not know"),
    (_receipt(jovian_fields=False), {"extraData": "0x"}, "an empty extraData"),
    (_receipt(jovian_fields=False), {}, "a header without extraData"),
    (_receipt(jovian_fields=False), None, "no header at all"),
])
def test_unknown_or_inconsistent_version_evidence_stays_bounded(receipt, block, why):
    from core.wallet import settlement

    evidence = settlement.evm_fee_evidence(receipt, _base_spec(), block=block)
    assert evidence.total is None and "operatorFee" in evidence.missing and evidence.fork == "unknown", (why, evidence)


def test_no_operator_fields_are_a_zero_on_any_fork_and_rows_without_an_operator_ignore_headers():
    from core.wallet import chains, settlement

    for block in ({"extraData": V0}, {"extraData": V1}, {}, None):
        evidence = settlement.evm_fee_evidence(_receipt(jovian_fields=False, operator=False), _base_spec(), block=block)
        assert (evidence.total, evidence.missing) == (GAS * PRICE + L1, ()), block
    ethereum = settlement.evm_fee_evidence({"gasUsed": hex(GAS), "effectiveGasPrice": hex(PRICE)}, chains.resolve_network("eip155:1"), block={"extraData": V1})
    assert (ethereum.total, ethereum.fork) == (GAS * PRICE, "")


def test_a_malformed_or_missing_l1_fee_stays_missing_whatever_the_fork():
    from core.wallet import settlement

    receipt = _receipt()
    receipt["l1Fee"] = "0xzz"
    evidence = settlement.evm_fee_evidence(receipt, _base_spec(), block={"extraData": V1})
    assert evidence.total is None and "l1Fee" in evidence.missing and evidence.fork == "jovian"


# --- through settlement, limits and refinement ---------------------------------------------------------------------

def _jovian(pilot, **overrides):
    chain = ScriptedEvmNativeChain(chain_id=8453, fee_model="op_stack", l1_fee=40_000_000_000_000, operator_fee=7, operator_fee_scalar=100, fork="jovian", **overrides)
    chain.__enter__()
    _route(pilot, BASE_MAINNET, chain.url)
    return chain


def _charge(chain) -> int:
    receipt = next(iter(chain.receipts.values()))
    gas, price = int(receipt["gasUsed"], 16), int(receipt["effectiveGasPrice"], 16)
    return gas * price + chain.l1_fee + chain.operator_charge(gas)


def test_a_jovian_transfer_settles_the_jovian_operator_fee_exactly_within_the_quoted_ceiling(pilot):
    from core.wallet import transfers

    chain = _jovian(pilot)
    try:
        wallet = _ready_pilot_wallet(BASE_MAINNET)
        chain.fund(wallet["address"], 10**18)
        engine, proposal = _pending(wallet, BASE_MAINNET)
        quote = _quote(proposal.proposal_id)
        transfer = _approve(engine, proposal.proposal_id, quote)["transfer"]
        charged = _charge(chain)
        assert charged > 21_000 * 100 * 100, "the Jovian operator term dwarfs the Isthmus one"
        assert (transfer["state"], transfer["fee_state"], transfer["charged_fee_minor"]) == ("confirmed", "exact", str(charged))
        assert charged <= int(quote["fields"]["fee_max_minor"]), "the oracle's operator estimate priced the Jovian term into the ceiling"
        assert _hold(proposal.proposal_id) == (10**15, charged, "settled")
        view = transfers.latest_receipt(proposal.proposal_id)
        assert view["fee_fork"] == "jovian"
    finally:
        chain.__exit__(None, None, None)


def test_an_isthmus_transfer_with_a_scalar_term_is_unchanged(pilot):
    chain = ScriptedEvmNativeChain(chain_id=8453, fee_model="op_stack", l1_fee=40_000_000_000_000, operator_fee=7, operator_fee_scalar=100)
    chain.__enter__()
    try:
        _route(pilot, BASE_MAINNET, chain.url)
        wallet = _ready_pilot_wallet(BASE_MAINNET)
        chain.fund(wallet["address"], 10**18)
        engine, proposal = _pending(wallet, BASE_MAINNET)
        transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
        charged = _charge(chain)
        assert chain.operator_charge(21_000) == 21_000 * 100 // 10**6 + 7
        assert (transfer["fee_state"], transfer["charged_fee_minor"], transfer["fee_fork"]) == ("exact", str(charged), "isthmus")
        assert _hold(proposal.proposal_id) == (10**15, charged, "settled")
    finally:
        chain.__exit__(None, None, None)


def test_a_jovian_receipt_under_an_inconsistent_header_is_bounded_then_refined_once_when_the_header_agrees(pilot):
    from core.wallet import settlement, transfers

    chain = _jovian(pilot, extra_data_override=V0)
    try:
        wallet = _ready_pilot_wallet(BASE_MAINNET)
        chain.fund(wallet["address"], 10**18)
        engine, proposal = _pending(wallet, BASE_MAINNET)
        quote = _quote(proposal.proposal_id)
        transfer = _approve(engine, proposal.proposal_id, quote)["transfer"]
        ceiling = int(quote["fields"]["fee_max_minor"])
        assert (transfer["state"], transfer["fee_state"], transfer["charged_fee_minor"], transfer["fee_missing"], transfer["fee_fork"]) == ("confirmed", "bounded", None, ["operatorFee"], "unknown")
        assert _hold(proposal.proposal_id) == (10**15, ceiling, "settled")
        chain.extra_data_override = None
        settlement.observe_open_transfers()
        refined = transfers.latest_receipt(proposal.proposal_id)
        charged = _charge(chain)
        assert (refined["fee_state"], refined["charged_fee_minor"], refined["fee_fork"]) == ("exact", str(charged), "jovian")
        assert _hold(proposal.proposal_id) == (10**15, charged, "settled")
        settlement.observe_open_transfers()
        assert _hold(proposal.proposal_id) == (10**15, charged, "settled")
    finally:
        chain.__exit__(None, None, None)


def test_a_failed_jovian_execution_counts_zero_principal_and_the_jovian_fee(pilot):
    from core.wallet import settlement, transfers

    chain = _jovian(pilot)
    try:
        chain.mine_mode = "revert"
        wallet = _ready_pilot_wallet(BASE_MAINNET)
        chain.fund(wallet["address"], 10**18)
        engine, proposal = _pending(wallet, BASE_MAINNET)
        chain.finalized_block = 50
        assert _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]["state"] == "pending"
        chain.finalized_block = chain.block_number
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        charged = _charge(chain)
        assert (view["state"], view["fee_state"], view["charged_fee_minor"], view["fee_fork"]) == ("failed_on_chain", "exact", str(charged), "jovian")
        assert _hold(proposal.proposal_id) == (0, charged, "settled")
    finally:
        chain.__exit__(None, None, None)
