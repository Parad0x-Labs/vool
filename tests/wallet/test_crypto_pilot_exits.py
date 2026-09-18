"""Crypto Pilot, stage 5: the owner exits on uncertain and never-sent transfers, through the product's doors.

SIMULATED CHAINS. An exit is offered only by fresh evidence, re-checked at the door under the row's lease, and taken
only with the owner's credential. Stop-waiting never un-counts a liability (D7): the hold settles as spent and
unconfirmed, the account is free, and a receipt that appears later still settles the record. A resend sends the
same signed bytes once through the single transmit site with a guard of its own (D9): a wrong PIN, a freeze, Crypto
off or a changed control sends nothing. Discard drops only a signed transfer that never left and can no longer be sent.
"""
from __future__ import annotations

import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

pytestmark = [pytest.mark.safety]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_MAINNET = "eip155:8453"
PIN = "482913"
WRONG_PIN = "593027"
SAME_ORIGIN = {"Host": "127.0.0.1:11435", "Origin": "http://127.0.0.1:11435", "Content-Type": "application/json"}


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_UI_CAPABILITY_SHA256", "VOOL_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import capabilities, chains, lifecycle
    from core.web.api import wallet_api

    monkeypatch.setattr(capabilities, "PILOT_TRANSFER_READY_ROWS", frozenset({SOLANA_DEVNET, BASE_MAINNET}))
    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
    wallet_api.reset_caller_binding_for_tests()
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    yield monkeypatch
    chains.invalidate_chain_identity()
    store_module.reset_default_store()
    wallet_api.reset_caller_binding_for_tests()


@pytest.fixture
def app(home):
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    return create_app(RuntimeServices(display_name="VOOL"))


def _door(app, path: str, body: dict) -> tuple[int, dict]:
    from tests.asgi_harness import asgi_request

    status, _headers, raw = asgi_request(app, method="POST", path=path, headers=SAME_ORIGIN, body=json.dumps(body).encode())
    return status, json.loads(raw or b"{}")


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _ready_pilot_wallet(network: str) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=network, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"x-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    return pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])


def _pending(wallet: dict, network: str, destination: str, amount: int, asset: str):
    from core.wallet import lifecycle, proposals

    proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=destination, amount_minor=amount, asset=asset, origin=proposals.ORIGIN_USER, network=network)
    engine = lifecycle.default_lifecycle()
    assert engine.prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    return engine, proposal


def _approved(engine, proposal_id: str) -> dict:
    from core.wallet import approval, quotes

    quote = quotes.mint_quote(proposal_id)
    return engine.approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))["transfer"]


def _hold(proposal_id: str):
    from core.wallet.store import connection

    with connection() as conn:
        row = conn.execute("SELECT amount_minor, fee_minor, state FROM wallet_spend_ledger WHERE proposal_id = ?", (proposal_id,)).fetchone()
    return tuple(row) if row else None


# --- stop waiting (Solana) ------------------------------------------------------------------------------------------

def test_stop_waiting_on_an_expired_unfound_solana_transfer_keeps_it_counted_frees_nothing_early_and_a_late_receipt_still_settles_it(home, app):
    from core.wallet import limits, proposals, settlement, transfers

    home.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    with ScriptedRpc(genesis_hash=DEVNET_GENESIS) as node:
        node.real_signature = True
        node.status_keyed = True
        node.send_mode = "accept_then_500"
        node.status_mode = "none"
        home.setenv("VOOL_WALLET_TESTNET_RPC_URL", node.url)
        wallet = _ready_pilot_wallet(SOLANA_DEVNET)
        limits.set_limits(wallet["wallet_id"], "SOL", limits.SpendLimits(per_tx_minor=1_000_000, daily_minor=1_200_000, per_destination_daily_minor=1_200_000))
        engine, proposal = _pending(wallet, SOLANA_DEVNET, _sol_key(), 700_000, "SOL")
        transfer = _approved(engine, proposal.proposal_id)
        assert transfer["state"] == "unknown"
        # not offered yet: the exit door refuses, nothing changes
        status, early = _door(app, "/api/wallet/transfers/stop-waiting", {"proposal_id": proposal.proposal_id, "pin": PIN})
        assert status != 200 and "exit_unavailable" in str(((early.get("fault") or {}).get("context") or {}).get("reason")), early
        node.block_height = 250  # past the blockhash's last valid height, status still null
        settlement.observe_open_transfers()
        assert transfers.latest_receipt(proposal.proposal_id)["offered_exit"] == "stop_waiting"
        status, wrong = _door(app, "/api/wallet/transfers/stop-waiting", {"proposal_id": proposal.proposal_id, "pin": WRONG_PIN})
        assert status != 200 and wrong.get("error") == "wallet_pin_invalid"
        assert transfers.latest_receipt(proposal.proposal_id)["state"] == "unknown"
        status, stopped = _door(app, "/api/wallet/transfers/stop-waiting", {"proposal_id": proposal.proposal_id, "pin": PIN})
        assert status == 200, stopped
        view = stopped["transfer"]
        assert (view["state"], view["state_label"], view["in_flight"]) == ("stopped_waiting", "Stopped waiting, not confirmed", False)
        assert _hold(proposal.proposal_id)[2] == "settled" and _hold(proposal.proposal_id)[0] == 700_000, "the liability keeps counting"
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_BROADCAST, "never a refusal state: the same request cannot be re-minted"
        assert not limits.check_limits(wallet["wallet_id"], "SOL", 600_000, _sol_key(), chain=SOLANA_DEVNET).ok
        assert node.send_count() == 1
        # the network answers after all: the record settles with the charged fee, still one send
        node.status_mode = "confirmed"
        settlement.observe_open_transfers()
        after = transfers.latest_receipt(proposal.proposal_id)
        assert (after["state"], after["charged_fee_minor"]) == ("confirmed", str(node.transaction_fee))
        assert _hold(proposal.proposal_id) == (700_000, node.transaction_fee, "settled") and node.send_count() == 1


# --- resend and stop-waiting (EVM) -------------------------------------------------------------------------------------

def _base_chain(home):
    chain = ScriptedEvmNativeChain(chain_id=8453, fee_model="op_stack", l1_fee=40_000_000_000_000, operator_fee=1_000)
    chain.__enter__()
    home.setenv("VOOL_WALLET_RPC_URLS", json.dumps({BASE_MAINNET: chain.url}))
    return chain


def test_a_dropped_evm_transfer_is_resent_once_with_the_same_bytes_and_a_wrong_pin_or_a_freeze_sends_nothing(home, app):
    from core.wallet import limits, settlement, transfers

    chain = _base_chain(home)
    try:
        chain.mine_mode = "never"
        wallet = _ready_pilot_wallet(BASE_MAINNET)
        chain.fund(wallet["address"], 10**18)
        engine, proposal = _pending(wallet, BASE_MAINNET, "0x" + "5" * 40, 10**15, "ETH")
        transfer = _approved(engine, proposal.proposal_id)
        assert transfer["state"] == "unknown" and len(chain.sent) == 1
        chain.pending.clear()  # dropped from the pool
        settlement.observe_open_transfers()
        assert transfers.latest_receipt(proposal.proposal_id)["offered_exit"] == "resend"
        status, wrong = _door(app, "/api/wallet/transfers/resend", {"proposal_id": proposal.proposal_id, "pin": WRONG_PIN})
        assert status != 200 and wrong.get("error") == "wallet_pin_invalid" and len(chain.sent) == 1
        limits.set_frozen(True)
        try:
            status, frozen = _door(app, "/api/wallet/transfers/resend", {"proposal_id": proposal.proposal_id, "pin": PIN})
        finally:
            limits.set_frozen(False)
        assert status != 200 and frozen.get("error") == "wallet_limit_exceeded" and len(chain.sent) == 1
        settlement.observe_open_transfers()  # the freeze and unfreeze moved the epochs; the resend's own baseline is read at the door
        status, resent = _door(app, "/api/wallet/transfers/resend", {"proposal_id": proposal.proposal_id, "pin": PIN})
        assert status == 200, resent
        assert len(chain.sent) == 2 and chain.sent[0] == chain.sent[1], "the same bytes, once more"
        assert resent["transfer"]["state"] == "unknown" and resent["transfer"]["attempts_sent"] == 2
        chain.mine_mode = "auto"
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        assert view["state"] == "confirmed" and len(chain.sent) == 2
    finally:
        chain.__exit__(None, None, None)


def test_stop_waiting_on_a_refused_evm_transfer_frees_the_slot_for_the_next_transfer(home, app):
    from core.wallet import settlement, transfers

    chain = _base_chain(home)
    try:
        wallet = _ready_pilot_wallet(BASE_MAINNET)
        chain.fund(wallet["address"], 10**18)
        engine, proposal = _pending(wallet, BASE_MAINNET, "0x" + "5" * 40, 10**15, "ETH")
        chain.txpool_refusal = "insufficient funds for gas * price + value"
        transfer = _approved(engine, proposal.proposal_id)
        chain.txpool_refusal = None
        assert (transfer["state"], transfer["evidence_kind"]) == ("unknown", "refused") and len(chain.sent) == 0
        settlement.observe_open_transfers()
        assert transfers.latest_receipt(proposal.proposal_id)["offered_exit"] == "stop_waiting_refused"
        status, stopped = _door(app, "/api/wallet/transfers/stop-waiting", {"proposal_id": proposal.proposal_id, "pin": PIN})
        assert status == 200 and stopped["transfer"]["state"] == "stopped_waiting", stopped
        assert _hold(proposal.proposal_id)[2] == "settled"
        # the account's slot is free: the next transfer takes the same nonce, and only one of the two can land
        engine2, second = _pending(wallet, BASE_MAINNET, "0x" + "6" * 40, 2 * 10**14, "ETH")
        view = _approved(engine2, second.proposal_id)
        assert view["state"] == "confirmed" and len(chain.sent) == 1
        assert transfers.get_transfer_by_id(second.proposal_id)["nonce"] == transfers.get_transfer_by_id(proposal.proposal_id)["nonce"]
    finally:
        chain.__exit__(None, None, None)


# --- discard ----------------------------------------------------------------------------------------------------------

def test_discard_drops_only_a_signed_transfer_that_never_left_and_releases_its_hold(home, app):
    from core.wallet import controls, limits, proposals, settlement, transfers
    from core.wallet.store import connection

    chain = _base_chain(home)
    try:
        wallet = _ready_pilot_wallet(BASE_MAINNET)
        chain.fund(wallet["address"], 10**18)
        _engine, proposal = _pending(wallet, BASE_MAINNET, "0x" + "5" * 40, 10**15, "ETH")
        from core.wallet import quotes

        quote = quotes.mint_quote(proposal.proposal_id)
        now = settlement.clock()
        with connection() as conn:
            limits._begin_immediate(conn)
            transfers.insert_claim(
                conn, proposal=proposal, quote_id=quote["quote_id"], quote_digest=quote["digest"], challenge_digest="c", environment="mainnet", family="evm",
                from_address=wallet["address"], fee_max_minor=int(quote["fields"]["fee_max_minor"]), owner_token="dead-request", lease_until=now + 30,
                dispatch_deadline=now + 60, epochs=controls.epochs(conn), enabled_generation=0, now=now, nonce=int(quote["fields"]["tx_params"]["nonce"]),
            )
            transfers.transition(conn, proposal.proposal_id, "signed", expected_state="claimed", now=now, tx_id="0x" + "ab" * 32, raw_b64="cmF3")
        status, _too_early = _door(app, "/api/wallet/transfers/discard", {"proposal_id": proposal.proposal_id, "pin": PIN})
        assert status != 200, "a signed transfer that may still be sent is not discardable"
        home.setattr(settlement, "clock", lambda: now + 61)
        settlement.observe_open_transfers()
        assert transfers.latest_receipt(proposal.proposal_id)["state"] == "signed_revoked"
        status, discarded = _door(app, "/api/wallet/transfers/discard", {"proposal_id": proposal.proposal_id, "pin": PIN})
        assert status == 200 and discarded["transfer"]["state"] == "discarded", discarded
        assert (_hold(proposal.proposal_id)[2], proposals.get_proposal(proposal.proposal_id).state) == ("released", proposals.STATE_FAILED)
        assert len(chain.sent) == 0 and transfers.get_transfer_by_id(proposal.proposal_id)["raw_b64"] is None
    finally:
        chain.__exit__(None, None, None)


def test_the_exit_doors_are_trusted_and_refuse_a_caller_without_a_request(home, app):
    from core.wallet import caller_binding

    for door in ("/api/wallet/transfers/stop-waiting", "/api/wallet/transfers/resend", "/api/wallet/transfers/discard"):
        assert caller_binding.is_trusted_door(door)
    from core.web.api.wallet_api import handle_wallet_post

    answer = handle_wallet_post("/api/wallet/transfers/resend", {"proposal_id": "pay-x", "pin": PIN}, client_host="127.0.0.1", headers=None)
    assert answer.status == 403
