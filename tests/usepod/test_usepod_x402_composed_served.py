"""Accountless x402 through ONE served process: the wallet payment authority, the money law and the UsePod transport.

SIMULATION CHAIN: ``tests.wallet._simulated_solana.SimulatedSolanaNode`` answers with the mainnet genesis hash and
executes what it is sent; no public cluster is ever reached. SYNTHETIC PROVIDER: the strict local UsePod stand-in, which
verifies each paid retry against that chain. The daemon is ``apps.vool_api_server`` unchanged, with the production
authorities -- nothing here replaces signing, custody, grant consumption, wallet facts, payment proof generation,
persistence or startup registration.

The first tests were written before the integration existed (failure-first): the daemon must boot with the wallet
payment authority registered, and accountless consent must name the wallet payer without any UsePod token configured.
The paid journey follows: an ordinary chat turn on the pinned model pauses while the payment waits on the Crypto Pilot
card, the owner approves it with the PIN (or rejects it), and the turn answers with the payment on its receipt.
"""
from __future__ import annotations

import json
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests.usepod.strict_usepod_service import Listing, StrictUsePodService
from tests.usepod.test_usepod_served_flow import MARKET, MARKET_ID, MODEL, _answer_text, _completed_receipts, _keep, _session
from tests.usepod.test_usepod_settings_ui import CENTRAL, PlainServedDaemon
from tests.wallet._simulated_solana import MAINNET_GENESIS, USDC_MAINNET_MINT, SimulatedSolanaNode

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
AUTHORITY_LABEL = "core.wallet.usepod_x402:v1"
#: A disposable wallet credential for the synthetic pilot wallet this suite creates in its own home.
PIN = "731946"
X402_PATH = "/proxy/x402/v1/chat/completions"
MESSAGES_PATH = "/proxy/x402/v1/messages"
#: The operator session the Settings consent surface resolves approvals under.
OPERATOR_SESSION = "openclaw:settings-operator"


class WalletServedDaemon(PlainServedDaemon):
    """The production-authority daemon with Crypto on and the Solana mainnet row pointed at the SIMULATION node."""

    def __init__(self, home: Path, chain_url: str) -> None:
        super().__init__(home)
        self.chain_url = chain_url

    def env(self) -> dict[str, str]:
        env = super().env()
        env.update(
            {
                "VOOL_WALLET_ENABLED": "1",
                "VOOL_WALLET_RPC_URLS": json.dumps({SOLANA_MAINNET: self.chain_url}),
                "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
                # the turn waits at most this long for the owner's decision on the payment
                "VOOL_USEPOD_X402_APPROVAL_SECONDS": "120",
            }
        )
        return env

    def door(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        """A trusted wallet door the way the served page calls it: same-origin JSON."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(self.base_url + path, data=data, method=method, headers={"Origin": self.base_url, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=120) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


def ready_pilot_wallet(daemon: WalletServedDaemon, *, network: str = SOLANA_MAINNET, label: str = "x402 payer") -> dict:
    status, created = daemon.door("POST", "/api/wallet/setup/create", {
        "network": network, "method": "pin", "credential": PIN, "credential_confirmation": PIN,
        "creation_key": f"x402-{uuid.uuid4().hex}", "label": label,
    })
    assert status == 200, created
    wallet_id = created["setup"]["wallet_id"]
    status, revealed = daemon.door("POST", "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    assert status == 200 and (revealed.get("backup") or {}).get("ack_token"), (status, sorted(revealed.get("backup") or {}))
    ack_token = revealed["backup"]["ack_token"]
    revealed = None
    status, ready = daemon.door("POST", "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": ack_token})
    assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
    return ready["setup"]


@pytest.fixture(scope="module")
def composed(tmp_path_factory):
    from solders.keypair import Keypair

    root = tmp_path_factory.mktemp("usepod-x402-composed")
    node = SimulatedSolanaNode(genesis_hash=MAINNET_GENESIS).start()
    service = None
    daemon = None
    try:
        node.create_mint(USDC_MAINNET_MINT, decimals=6)
        # the provider's pay-to owner: a fresh SIMULATION key with its USDC account open, as UsePod's would be
        pay_to = str(Keypair().pubkey())
        node.fund_sol(pay_to, 5_000_000)
        node.open_token_account(pay_to, USDC_MAINNET_MINT)
        service = StrictUsePodService(
            models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
            chain=node, pay_to=pay_to, usdc_mint=USDC_MAINNET_MINT,
        ).start()
        daemon = WalletServedDaemon(root / "home", node.url)
        daemon.start()
        yield SimpleNamespace(service=service, node=node, daemon=daemon, pay_to=pay_to)
    finally:
        if daemon is not None:
            daemon.stop()
        if service is not None:
            service.stop()
        node.stop()


def test_the_daemon_boots_with_the_wallet_payment_authority_registered(composed) -> None:
    status, view = composed.daemon.call("GET", "/api/cloud/usepod/discovery")
    _keep("x402_boot_authorities.json", {"status": status, "authorities": (view or {}).get("authorities") if isinstance(view, dict) else view})
    assert status == 200, view
    payment = view["authorities"]["payment"]
    # registered at startup under its own label; no wallet exists yet, so it verifies no network
    assert (payment["installed"], payment["label"], payment["verified_networks"]) == (True, AUTHORITY_LABEL, []), payment


def test_accountless_consent_names_the_wallet_payer_without_a_usepod_token(composed) -> None:
    daemon, service, node = composed.daemon, composed.service, composed.node
    status, environment = daemon.door("POST", "/api/wallet/environment", {"environment": "mainnet"})
    assert status == 200, environment
    wallet = ready_pilot_wallet(daemon)
    node.fund_sol(wallet["address"], 20_000_000)
    node.fund_token(wallet["address"], USDC_MAINNET_MINT, 1_000_000)
    # No token is stored, so the stand-in is chosen as the origin EXPLICITLY, on the lane door.
    status, lane = daemon.call("POST", "/api/cloud/usepod/lane", {"protocol": "openai", "transport_mode": "x402", "origin": service.origin})
    assert status == 200 and lane["origin"] == service.origin, lane
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    assert status == 200 and view["credential"]["configured"] is False, view["credential"]
    assert view["authorities"]["payment"]["verified_networks"] == [SOLANA_MAINNET], view["authorities"]
    status, refreshed = daemon.call("POST", "/api/cloud/usepod/refresh", {})
    assert status == 200, refreshed
    status, policy = daemon.call("POST", "/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
    assert status == 200, policy
    status, approved = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": MODEL})
    assert status == 200, approved
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 50_000, "max_total_atomic": 50_000, "asset": "USDC"})
    _keep("x402_accountless_consent.json", {"status": status, "proposed": proposed, "wallet_address": wallet.get("address")})
    assert status == 200, proposed
    facts = proposed["facts"]
    assert (facts["account_kind"], facts["account"], facts["asset"], facts["unit"], facts["network"], facts["origin"]) == (
        "x402_payer_wallet", wallet["address"], "USDC", "usdc_microunit", SOLANA_MAINNET, service.origin), facts
    x402 = facts["x402"]
    assert (x402["fee_asset"], x402["fee_decimals"], x402["fee_max_atomic"], x402["authority"]) == ("SOL", 9, 5_000, AUTHORITY_LABEL), x402
    assert (x402["observed_principal_balance_atomic"], x402["observed_fee_balance_atomic"]) == (1_000_000, 20_000_000), x402
    assert service.requests_to("/proxy/x402/v1/chat/completions") == [], "consent sends no inference"
    assert node.distinct_sends() == 0, "consent signs and sends nothing"


def test_after_a_restart_the_authority_is_registered_again_and_verifies_the_same_wallet(composed) -> None:
    daemon = composed.daemon
    daemon.stop()
    daemon.start()
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    _keep("x402_restart_authorities.json", {"status": status, "authorities": view.get("authorities"), "lane": view.get("lane"), "origin": view.get("origin"), "spend_approval": view.get("spend_approval")})
    assert status == 200, view
    payment = view["authorities"]["payment"]
    assert (payment["installed"], payment["label"], payment["verified_networks"]) == (True, AUTHORITY_LABEL, [SOLANA_MAINNET]), payment
    # the owner's choices survive the restart: the lane, the explicitly chosen origin and the pending consent
    assert (view["lane"]["transport_mode"], view["origin"]) == ("x402", composed.service.origin), view
    assert (view["spend_approval"] or {}).get("state") == "pending", view["spend_approval"]



# --- the paid journey: ordinary chat, the owner's decision on the Crypto Pilot card, a readable receipt ---------------


def _chat(daemon: WalletServedDaemon, text: str, *, session_id: str) -> tuple[int, object]:
    payload = {"messages": [{"role": "user", "content": text}], "stream": False, "session_id": session_id, "mode": "auto", "model": f"usepod-byok:{MODEL}"}
    return daemon.call("POST", "/api/chat", payload, timeout=300.0)


def _chat_in_background(daemon: WalletServedDaemon, text: str, session_id: str) -> dict:
    turn: dict = {}

    def run() -> None:
        try:
            turn["reply"] = _chat(daemon, text, session_id=session_id)
        except Exception as exc:  # recorded for the assertion that reads it
            turn["error"] = repr(exc)

    turn["worker"] = threading.Thread(target=run, daemon=True)
    turn["worker"].start()
    return turn


def _finish(turn: dict, *, timeout: float = 300.0) -> tuple[int, object]:
    turn["worker"].join(timeout=timeout)
    assert not turn["worker"].is_alive(), "the chat turn never finished"
    assert "error" not in turn, turn["error"]
    return turn["reply"]


def _pin_for(daemon: WalletServedDaemon, session_id: str) -> None:
    """The chat selector's pick for this conversation. It registers the UsePod lane from the chosen origin alone: an
    accountless lane needs no token (a global pin activates a provider lane only when a key resolves)."""
    status, pinned = daemon.call("POST", "/api/cloud/model", {"model": MODEL, "provider": "usepod", "confirm_paid": True, "session_id": session_id})
    assert status == 200 and pinned.get("ok") is True and pinned.get("provider") == "usepod", pinned


def _allow_pending_consent(daemon: WalletServedDaemon) -> dict:
    """The owner's Allow on the proposed consent, then the mint: the doors the Settings consent surface calls."""
    status, pending = daemon.call("POST", "/api/cloud/usepod/spend-approval/pending", {})
    assert status == 200 and (pending.get("pending") or {}).get("status") == "pending", pending
    approval_id = pending["pending"]["approval_id"]
    status, resolved = daemon.call("POST", "/api/mode", {"op": "resolve_approval", "approval_id": approval_id, "decision": "allow", "session_id": OPERATOR_SESSION}, timeout=60.0)
    assert status == 200, resolved
    status, minted = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": approval_id})
    assert status == 200 and minted.get("grant_id"), minted
    return minted


def _pending_payment(daemon: WalletServedDaemon, turn: dict, pay_to: str, *, timeout: float = 120.0) -> dict:
    """The payment the paused turn asks for, as the Crypto Pilot card reads it from the wallet status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, view = daemon.door("GET", "/api/wallet/status")
        pending = ((view or {}).get("status") or {}).get("pending") or [] if status == 200 else []
        rows = [row for row in pending if row.get("destination") == pay_to]
        if rows:
            return rows[-1]
        if not turn["worker"].is_alive():
            raise AssertionError(f"the turn ended without asking the wallet: {turn.get('reply') or turn.get('error')}")
        time.sleep(0.25)
    raise AssertionError("no UsePod payment reached the wallet")


def _owner_approves(daemon: WalletServedDaemon, proposal_id: str) -> dict:
    """The owner's approval on the pilot sheet: the quote it shows, then the PIN."""
    status, quoted = daemon.door("POST", "/api/wallet/quote", {"proposal_id": proposal_id})
    assert status == 200, quoted
    quote = quoted["quote"]
    status, approved = daemon.door("POST", "/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": quote["quote_id"], "quote_digest": quote["digest"], "pin": PIN})
    assert status == 200 and approved["transfer"]["state"] == "confirmed", approved
    return approved


def _usepod_receipts(daemon: WalletServedDaemon, session_id: str) -> tuple[list[dict], list[dict]]:
    events = daemon.events(session_id)
    return events, [receipt for receipt in _completed_receipts(events) if receipt.get("provider") == "usepod"]


def test_an_original_usdc_chat_turn_pauses_for_the_wallet_and_answers_with_the_payment_on_its_receipt(composed) -> None:
    daemon, service, node, pay_to = composed.daemon, composed.service, composed.node, composed.pay_to
    consent = _allow_pending_consent(daemon)  # the USDC consent proposed without a token, still pending after the restart
    sends, received, quotes_before = node.distinct_sends(), node.token_balance(pay_to, USDC_MAINNET_MINT), len(service.requests_to(X402_PATH))
    session_id = _session(f"x402-usdc-{uuid.uuid4()}")
    _pin_for(daemon, session_id)
    turn = _chat_in_background(daemon, "Name one reason harbours build breakwaters.", session_id)
    pending = _pending_payment(daemon, turn, pay_to)
    assert pending["asset"] == "USDC" and turn["worker"].is_alive(), pending
    assert node.distinct_sends() == sends, "nothing is signed before the owner approves"
    approved = _owner_approves(daemon, pending["proposal_id"])
    status, answer = _finish(turn)
    events, receipts = _usepod_receipts(daemon, session_id)
    _keep("x402_usdc_turn.json", {"status": status, "answer": answer, "pending": pending, "approved": approved, "receipts": receipts, "consent": consent, "events": events})
    assert status == 200 and _answer_text(answer).strip(), answer
    assert len(receipts) == 1, receipts
    receipt, paid = receipts[0], int(pending["amount_minor"])
    assert (receipt["transport_mode"], receipt["cost"]["asset"], receipt["x402"]["payment_signature"]) == ("x402", "USDC", approved["transfer"]["tx_id"]), receipt
    chain = receipt["x402"]["chain_confirmation"]
    assert (chain["state"], chain["signature"], chain["wallet_outflow_atomic"], chain["network_fee_atomic"], chain["fee_asset"]) == (
        "recorded", approved["transfer"]["tx_id"], paid, 5_000, "SOL"), chain
    assert receipt["settlement"]["recording"] == "settled_with_evidence", receipt["settlement"]
    lane_rows = [event for event in events if event.get("event_type") == "model_lane_completed"
                 and ((event.get("details") if isinstance(event.get("details"), dict) else event).get("provider_receipt") or {}).get("operation_id") == receipt["operation_id"]]
    assert lane_rows, "the displayed lane row carries the payment receipt"
    status, found = daemon.call("GET", f"/api/money/liabilities/{receipt['reservation_id']}")
    assert status == 200, found
    liability = found["liability"]
    lines = {line["flow"]: line for line in liability["lines"]}
    assert (liability["state"], liability["operation_id"]) == ("settled", receipt["operation_id"]), liability
    assert (lines["wallet_outflow"]["line_state"], int(lines["wallet_outflow"]["actual_atomic"])) == ("exact", paid), lines
    assert (lines["network_fee"]["line_state"], int(lines["network_fee"]["actual_atomic"])) == ("exact", 5_000), lines
    # exactly one payment on the SIMULATION chain, to the quote's pay-to; one quote and one paid retry at the stand-in
    assert node.distinct_sends() == sends + 1 and node.token_balance(pay_to, USDC_MAINNET_MINT) - received == paid
    assert len(service.requests_to(X402_PATH)) - quotes_before == 2


def test_a_novel_sol_turn_pays_in_lamports_under_a_fresh_sol_consent_after_the_usdc_one_was_used(composed) -> None:
    daemon, service, node, pay_to = composed.daemon, composed.service, composed.node, composed.pay_to
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 5_000_000, "max_total_atomic": 5_000_000, "asset": "SOL"})
    assert status == 200 and (proposed["facts"]["asset"], proposed["facts"]["unit"]) == ("SOL", "lamport"), proposed
    _allow_pending_consent(daemon)
    sends, received = node.distinct_sends(), node.sol_balance(pay_to)
    session_id = _session(f"x402-sol-{uuid.uuid4()}")
    _pin_for(daemon, session_id)
    turn = _chat_in_background(daemon, "Suggest a name for a lighthouse café by a rocky shore.", session_id)
    pending = _pending_payment(daemon, turn, pay_to)
    assert pending["asset"] == "SOL", pending
    approved = _owner_approves(daemon, pending["proposal_id"])
    status, answer = _finish(turn)
    events, receipts = _usepod_receipts(daemon, session_id)
    _keep("x402_sol_turn.json", {"status": status, "answer": answer, "pending": pending, "approved": approved, "receipts": receipts, "events": events})
    assert status == 200 and _answer_text(answer).strip(), answer
    assert len(receipts) == 1, receipts
    receipt, lamports = receipts[0], int(pending["amount_minor"])
    assert (receipt["cost"]["asset"], receipt["cost"]["unit"], receipt["x402"]["payment_signature"]) == ("SOL", "lamport", approved["transfer"]["tx_id"]), receipt
    chain = receipt["x402"]["chain_confirmation"]
    assert (chain["state"], chain["wallet_outflow_atomic"], chain["network_fee_atomic"], chain["fee_asset"]) == ("recorded", lamports, 5_000, "SOL"), chain
    assert node.sol_balance(pay_to) - received == lamports and node.distinct_sends() == sends + 1


def test_an_owner_rejection_on_the_card_ends_the_turn_with_nothing_signed_and_no_paid_retry(composed) -> None:
    daemon, service, node, pay_to = composed.daemon, composed.service, composed.node, composed.pay_to
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 50_000, "max_total_atomic": 50_000, "asset": "USDC"})
    assert status == 200, proposed
    consent = _allow_pending_consent(daemon)
    sends, quotes_before = node.distinct_sends(), len(service.requests_to(X402_PATH))
    session_id = _session(f"x402-reject-{uuid.uuid4()}")
    _pin_for(daemon, session_id)
    turn = _chat_in_background(daemon, "Write one line about tide pools at dawn.", session_id)
    pending = _pending_payment(daemon, turn, pay_to)
    status, rejected = daemon.door("POST", "/api/wallet/reject", {"proposal_id": pending["proposal_id"]})
    assert status == 200 and rejected.get("rejected") is True, rejected
    status, answer = _finish(turn)
    events, receipts = _usepod_receipts(daemon, session_id)
    status_grant, against = daemon.call("GET", f"/api/money/liabilities?grant_id={consent['grant_id']}")
    _keep("x402_rejected_turn.json", {"status": status, "answer": answer, "pending": pending, "rejected": rejected, "liabilities": against, "events": events})
    assert "wallet_approval_rejected" in json.dumps(events), [event.get("event_type") for event in events]
    assert node.distinct_sends() == sends, "a rejected payment is never signed or sent"
    assert len(service.requests_to(X402_PATH)) - quotes_before == 1, "the quote only: no paid retry"
    states = [item["state"] for item in against["liabilities"]]
    assert status_grant == 200 and len(states) == 1 and states[0] in {"unsent", "released"}, against


# --- controls: each refuses before any payment ---------------------------------------------------------------------


def _liability_count(daemon: WalletServedDaemon) -> int:
    status, listed = daemon.call("GET", "/api/money/liabilities")
    assert status == 200, listed
    return len(listed["liabilities"])


def _refused_turn(daemon: WalletServedDaemon, text: str, *, prefix: str) -> list[dict]:
    """A turn that must end before any payment: it answers without a payment card ever reaching the wallet."""
    session_id = _session(f"{prefix}-{uuid.uuid4()}")
    _pin_for(daemon, session_id)
    status, answer = _chat(daemon, text, session_id=session_id)
    assert status == 200, answer
    status, view = daemon.door("GET", "/api/wallet/status")
    pending = ((view or {}).get("status") or {}).get("pending") or []
    assert status == 200 and pending == [], pending
    return daemon.events(session_id)


def test_a_withdrawn_route_refuses_the_x402_turn_before_any_quote(composed) -> None:
    daemon, service, node = composed.daemon, composed.service, composed.node
    status, withdrawn = daemon.call("POST", "/api/cloud/usepod/forget-route", {"model_id": MODEL})
    assert status == 200, withdrawn
    try:
        quotes_before, sends = len(service.requests_to(X402_PATH)), node.distinct_sends()
        events = _refused_turn(daemon, "Name one tool a harbour pilot carries aboard.", prefix="x402-route")
        _keep("x402_control_route_withdrawn.json", {"events": events})
        assert "usepod_route_not_approved" in json.dumps(events), [event.get("event_type") for event in events]
        assert len(service.requests_to(X402_PATH)) == quotes_before and node.distinct_sends() == sends
    finally:
        status, approved = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": MODEL})
        assert status == 200, approved


def test_a_testnet_selection_verifies_no_mainnet_network_and_refuses_at_the_pick(composed) -> None:
    daemon, service, node = composed.daemon, composed.service, composed.node
    status, switched = daemon.door("POST", "/api/wallet/environment", {"environment": "testnet"})
    assert status == 200, switched
    try:
        status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
        assert view["authorities"]["payment"]["verified_networks"] == [], "Test networks never verifies Mainnet"
        quotes_before, sends = len(service.requests_to(X402_PATH)), node.distinct_sends()
        events = _refused_turn(daemon, "Name one colour painted on harbour buoys.", prefix="x402-testnet")
        _keep("x402_control_testnet_selected.json", {"events": events})
        assert "wallet_payment_network_unverified" in json.dumps(events), [event.get("event_type") for event in events]
        assert len(service.requests_to(X402_PATH)) == quotes_before and node.distinct_sends() == sends
    finally:
        status, restored = daemon.door("POST", "/api/wallet/environment", {"environment": "mainnet"})
        assert status == 200, restored
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    assert view["authorities"]["payment"]["verified_networks"] == [SOLANA_MAINNET], view["authorities"]["payment"]


def test_a_quote_on_a_network_the_wallet_did_not_verify_is_refused_before_anything_is_reserved(composed) -> None:
    daemon, service, node = composed.daemon, composed.service, composed.node

    def on_devnet(quote: dict) -> dict:
        return {**quote, "accepts": [{**option, "network": SOLANA_DEVNET} for option in quote["accepts"]]}

    liabilities, quotes_before, sends = _liability_count(daemon), len(service.requests_to(X402_PATH)), node.distinct_sends()
    service.faults["quote_mutator"] = on_devnet
    try:
        events = _refused_turn(daemon, "Name one thing a tide table tells a sailor.", prefix="x402-network")
    finally:
        service.faults.pop("quote_mutator", None)
    _keep("x402_control_wrong_network.json", {"events": events})
    assert "quote_network_not_verified_by_payment_authority" in json.dumps(events), [event.get("event_type") for event in events]
    assert len(service.requests_to(X402_PATH)) - quotes_before == 1, "the quote only"
    assert node.distinct_sends() == sends and _liability_count(daemon) == liabilities, "nothing reserved, nothing signed"


def test_a_sol_payment_larger_than_the_wallet_holds_is_refused_before_any_reservation(composed) -> None:
    daemon, service, node = composed.daemon, composed.service, composed.node
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 50_000_000, "max_total_atomic": 50_000_000, "asset": "SOL"})
    assert status == 200, proposed
    _allow_pending_consent(daemon)

    def more_sol_than_held(quote: dict) -> dict:
        # the wallet holds about 0.02 SOL; this call asks 0.03 SOL, inside the owner's 0.05 SOL consent
        return {**quote, "accepts": [{**option, "amount_microunits": 30_000_000} for option in quote["accepts"] if option["asset"] == "SOL"]}

    liabilities, sends = _liability_count(daemon), node.distinct_sends()
    service.faults["quote_mutator"] = more_sol_than_held
    try:
        events = _refused_turn(daemon, "Name one bird that nests on sea cliffs.", prefix="x402-principal")
    finally:
        service.faults.pop("quote_mutator", None)
    _keep("x402_control_insufficient_principal.json", {"events": events, "wallet_lamports": node.sol_balance(composed.wallet_address) if getattr(composed, "wallet_address", "") else None})
    assert "MONEY_LIQUIDITY_INSUFFICIENT" in json.dumps(events), [event.get("event_type") for event in events]
    assert node.distinct_sends() == sends and _liability_count(daemon) == liabilities, "nothing reserved, nothing signed"


def test_a_quote_paying_an_account_that_cannot_receive_usdc_is_refused_before_signing(composed) -> None:
    from solders.keypair import Keypair

    daemon, service, node = composed.daemon, composed.service, composed.node
    stranger = str(Keypair().pubkey())
    node.fund_sol(stranger, 5_000_000)  # lamports, but no USDC token account

    def to_stranger(quote: dict) -> dict:
        return {**quote, "accepts": [{**option, "pay_to": stranger} for option in quote["accepts"] if option["asset"] == "USDC"]}

    liabilities, quotes_before, sends = _liability_count(daemon), len(service.requests_to(X402_PATH)), node.distinct_sends()
    service.faults["quote_mutator"] = to_stranger
    try:
        events = _refused_turn(daemon, "Name one fish that shelters in kelp forests.", prefix="x402-recipient")
    finally:
        service.faults.pop("quote_mutator", None)
    status, listed = daemon.call("GET", "/api/money/liabilities")
    _keep("x402_control_wrong_recipient.json", {"events": events, "liabilities": listed})
    assert "wallet_recipient_refused" in json.dumps(events), [event.get("event_type") for event in events]
    assert len(service.requests_to(X402_PATH)) - quotes_before == 1 and node.distinct_sends() == sends, "the quote only; nothing signed"
    # the claim existed and was released as never sent, so the consent that covered it is not used up
    assert status == 200 and len(listed["liabilities"]) == liabilities + 1 and listed["liabilities"][-1]["state"] == "unsent", listed["liabilities"][-1:]


def test_revoked_x402_consents_refuse_the_turn_at_the_pick(composed) -> None:
    daemon, service, node = composed.daemon, composed.service, composed.node
    status, listed = daemon.call("GET", "/api/money/grants")
    _keep("x402_control_revoked_grants_before.json", {"status": status, "grants": listed})
    assert status == 200, listed
    x402 = [grant for grant in listed["grants"] if grant.get("state") == "active" and "inference_x402" in ((grant.get("spec") or {}).get("operation_kinds") or [])]
    assert x402, listed
    for grant in x402:
        status, revoked = daemon.door("POST", "/api/money/grants/revoke", {"grant_id": grant["grant_id"], "reason": "owner revoked x402 spending (control)"})
        assert status == 200 and revoked["grant"]["state"] == "revoked", revoked
    liabilities, quotes_before, sends = _liability_count(daemon), len(service.requests_to(X402_PATH)), node.distinct_sends()
    events = _refused_turn(daemon, "Name one knot sailors use to moor a boat.", prefix="x402-revoked")
    _keep("x402_control_revoked.json", {"events": events})
    assert "MONEY_AUTHORITY_REVOKED" in json.dumps(events), [event.get("event_type") for event in events]
    assert len(service.requests_to(X402_PATH)) == quotes_before and node.distinct_sends() == sends and _liability_count(daemon) == liabilities


# --- concurrency and the second protocol ---------------------------------------------------------------------------


def test_two_concurrent_turns_under_one_single_payment_consent_pay_exactly_once(composed) -> None:
    """Genuinely concurrent schedule: two conversations send at once under ONE single-payment consent. The money law's
    reservation transaction lets exactly one of them reach the wallet; the other is refused before any card exists."""
    daemon, service, node, pay_to = composed.daemon, composed.service, composed.node, composed.pay_to
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 50_000, "max_total_atomic": 50_000, "asset": "USDC"})
    assert status == 200, proposed
    consent = _allow_pending_consent(daemon)
    sends, received, quotes_before = node.distinct_sends(), node.token_balance(pay_to, USDC_MAINNET_MINT), len(service.requests_to(X402_PATH))
    sessions = [_session(f"x402-concurrent-{index}-{uuid.uuid4()}") for index in range(2)]
    for session_id in sessions:
        _pin_for(daemon, session_id)
    prompts = ("Name one reason lighthouses flash in patterns.", "Name one reason ports dredge their channels.")
    turns = [_chat_in_background(daemon, prompt, session_id) for prompt, session_id in zip(prompts, sessions)]
    deadline = time.monotonic() + 120.0
    pending: list[dict] = []
    while time.monotonic() < deadline:
        status, view = daemon.door("GET", "/api/wallet/status")
        pending = [row for row in (((view or {}).get("status") or {}).get("pending") or []) if row.get("destination") == pay_to]
        finished = [turn for turn in turns if not turn["worker"].is_alive()]
        if (pending and finished) or len(finished) == 2:
            break
        time.sleep(0.25)
    finished = [turn for turn in turns if not turn["worker"].is_alive()]
    _keep("x402_concurrent_before_approval.json", {"pending": pending, "finished_replies": [turn.get("reply") or turn.get("error") for turn in finished]})
    assert len(pending) == 1 and len(finished) == 1, (pending, len(finished))
    waiting = next(turn for turn in turns if turn is not finished[0])
    approved = _owner_approves(daemon, pending[0]["proposal_id"])
    status_paid, _answer_paid = _finish(waiting)
    status_refused, _answer_refused = _finish(finished[0])
    events = {session_id: daemon.events(session_id) for session_id in sessions}
    receipts = {session_id: [receipt for receipt in _completed_receipts(found) if receipt.get("provider") == "usepod"] for session_id, found in events.items()}
    status_listed, against = daemon.call("GET", f"/api/money/liabilities?grant_id={consent['grant_id']}")
    _keep("x402_concurrent_turns.json", {"approved": approved, "receipts": receipts, "liabilities": against, "events": events})
    assert (status_paid, status_refused) == (200, 200)
    paid = [session_id for session_id, found in receipts.items() if found]
    assert len(paid) == 1, receipts
    refused = next(session_id for session_id in sessions if session_id not in paid)
    assert "MONEY_AUTHORITY_EXHAUSTED" in json.dumps(events[refused]), [event.get("event_type") for event in events[refused]]
    assert receipts[paid[0]][0]["x402"]["payment_signature"] == approved["transfer"]["tx_id"]
    assert node.distinct_sends() == sends + 1 and node.token_balance(pay_to, USDC_MAINNET_MINT) - received == int(pending[0]["amount_minor"])
    assert len(service.requests_to(X402_PATH)) - quotes_before == 3, "two quotes and one paid retry"
    assert status_listed == 200 and [item["state"] for item in against["liabilities"]] == ["settled"], against


def test_an_anthropic_protocol_turn_pays_through_the_x402_messages_path(composed) -> None:
    daemon, service, node, pay_to = composed.daemon, composed.service, composed.node, composed.pay_to
    status, lane = daemon.call("POST", "/api/cloud/usepod/lane", {"protocol": "anthropic", "transport_mode": "x402"})
    assert status == 200 and lane["lane"] == {"protocol": "anthropic", "transport_mode": "x402"} and lane["origin"] == service.origin, lane
    try:
        status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 50_000, "max_total_atomic": 50_000, "asset": "USDC"})
        assert status == 200, proposed
        _allow_pending_consent(daemon)
        sends, messages_before, completions_before = node.distinct_sends(), len(service.requests_to(MESSAGES_PATH)), len(service.requests_to(X402_PATH))
        session_id = _session(f"x402-anthropic-{uuid.uuid4()}")
        _pin_for(daemon, session_id)
        turn = _chat_in_background(daemon, "Name one reason sailors reef a sail in strong wind.", session_id)
        pending = _pending_payment(daemon, turn, pay_to)
        approved = _owner_approves(daemon, pending["proposal_id"])
        status, answer = _finish(turn)
        events, receipts = _usepod_receipts(daemon, session_id)
        _keep("x402_anthropic_turn.json", {"status": status, "answer": answer, "pending": pending, "approved": approved, "receipts": receipts, "events": events})
        assert status == 200 and _answer_text(answer).strip(), answer
        assert len(receipts) == 1, receipts
        receipt = receipts[0]
        assert (receipt["protocol"], receipt["endpoint"], receipt["x402"]["payment_signature"]) == ("anthropic", MESSAGES_PATH, approved["transfer"]["tx_id"]), receipt
        assert receipt["x402"]["chain_confirmation"]["state"] == "recorded", receipt["x402"]
        assert len(service.requests_to(MESSAGES_PATH)) - messages_before == 2 and len(service.requests_to(X402_PATH)) == completions_before
        assert node.distinct_sends() == sends + 1
    finally:
        daemon.call("POST", "/api/cloud/usepod/lane", {"protocol": "openai", "transport_mode": "x402"})


# --- one operation through a process death -------------------------------------------------------------------------


def _kill_hard(daemon: WalletServedDaemon) -> None:
    """The daemon's process group ends at once: no shutdown path, no cleanup, exactly like a crash or power loss."""
    os.killpg(os.getpgid(daemon.process.pid), signal.SIGKILL)
    daemon.process.wait(timeout=30)


def _wait_until(predicate, *, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


def _paid_turn_until_the_retry_is_in_flight(composed, *, fault: str, prompt: str, prefix: str) -> tuple[dict, dict, int, int]:
    daemon, service, node, pay_to = composed.daemon, composed.service, composed.node, composed.pay_to
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 50_000, "max_total_atomic": 50_000, "asset": "USDC"})
    assert status == 200, proposed
    _allow_pending_consent(daemon)
    session_id = _session(f"{prefix}-{uuid.uuid4()}")
    _pin_for(daemon, session_id)
    sends, quotes_before = node.distinct_sends(), len(service.requests_to(X402_PATH))
    service.faults[fault] = 45.0
    turn = _chat_in_background(daemon, prompt, session_id)
    pending = _pending_payment(daemon, turn, pay_to)
    approved = _owner_approves(daemon, pending["proposal_id"])
    _wait_until(lambda: len(service.requests_to(X402_PATH)) - quotes_before >= 2, timeout=60.0, what="the paid retry to reach the provider")
    return turn, approved, sends, quotes_before


def _the_operation_paid_by(daemon: WalletServedDaemon, signature: str) -> dict:
    status, listed = daemon.call("GET", "/api/cloud/usepod/x402/unresolved")
    assert status == 200, listed
    found = [item for item in listed["operations"] if item.get("proof_signature") == signature]
    assert len(found) == 1, listed
    return found[0]


def test_a_daemon_killed_while_its_paid_retry_is_held_resumes_that_operation_once(composed) -> None:
    daemon, service, node = composed.daemon, composed.service, composed.node
    turn, approved, sends, quotes_before = _paid_turn_until_the_retry_is_in_flight(
        composed, fault="hold_then_drop_paid_retry_seconds", prompt="Name one reason ships take on a harbour pilot.", prefix="x402-death-held")
    _kill_hard(daemon)
    service.faults.pop("hold_then_drop_paid_retry_seconds", None)
    turn["worker"].join(timeout=60)
    daemon.start()
    signature = approved["transfer"]["tx_id"]
    operation = _the_operation_paid_by(daemon, signature)
    _keep("x402_death_held_listed.json", {"operation": operation, "turn_error": turn.get("error")})
    # the process died with the paid request in flight: paid, unanswered, and the law now holds it as unknown
    assert (operation["state"], operation["liability_state"], operation["resumable"]) == ("paid_retry_sent", "unknown", True), operation
    status, resumed = daemon.call("POST", "/api/cloud/usepod/x402/resume", {"operation_id": operation["operation_id"]}, timeout=180.0)
    _keep("x402_death_held_resumed.json", {"status": status, "resumed": resumed})
    assert status == 200 and resumed["answer"].strip() and resumed["resumed"] is True, resumed
    receipt = resumed["receipt"]
    chain = receipt["x402"]["chain_confirmation"]
    assert (receipt["x402"]["payment_signature"], chain["state"], receipt["settlement"]["recording"]) == (signature, "recorded", "settled_with_evidence"), receipt
    status, found = daemon.call("GET", f"/api/money/liabilities/{receipt['reservation_id']}")
    assert status == 200 and found["liability"]["state"] == "settled", found
    lines = {line["flow"]: line for line in found["liability"]["lines"]}
    assert (lines["wallet_outflow"]["line_state"], lines["network_fee"]["line_state"]) == ("exact", "exact"), lines
    # one payment only, and on the wire: the quote, the held retry, the resend
    assert node.distinct_sends() == sends + 1
    assert len(service.requests_to(X402_PATH)) - quotes_before == 3
    status, again = daemon.call("POST", "/api/cloud/usepod/x402/resume", {"operation_id": operation["operation_id"]})
    assert (status, again.get("code")) == (409, "operation_not_unresolved"), again
    status, listed = daemon.call("GET", "/api/cloud/usepod/x402/unresolved")
    assert all(item["operation_id"] != operation["operation_id"] for item in listed["operations"]), listed


def test_a_daemon_killed_after_the_provider_served_never_pays_again_on_resume(composed) -> None:
    daemon, service, node = composed.daemon, composed.service, composed.node
    turn, approved, sends, quotes_before = _paid_turn_until_the_retry_is_in_flight(
        composed, fault="paid_retry_delay_seconds", prompt="Name one reason lighthouses are painted in bands.", prefix="x402-death-served")
    _kill_hard(daemon)
    service.faults.pop("paid_retry_delay_seconds", None)
    turn["worker"].join(timeout=90)
    daemon.start()
    signature = approved["transfer"]["tx_id"]
    operation = _the_operation_paid_by(daemon, signature)
    assert (operation["state"], operation["liability_state"]) == ("paid_retry_sent", "unknown"), operation
    status, refused = daemon.call("POST", "/api/cloud/usepod/x402/resume", {"operation_id": operation["operation_id"]}, timeout=180.0)
    after = _the_operation_paid_by(daemon, signature)
    _keep("x402_death_served_resume_refused.json", {"status": status, "refused": refused, "after": after})
    # the provider had already settled this signature: the resend is refused and nothing is paid again
    assert (status, refused.get("http_status")) == (502, 409), refused
    assert (after["state"], after["liability_state"]) == ("paid_retry_failed", "unknown"), after
    assert node.distinct_sends() == sends + 1
    status, bounded = daemon.call("POST", "/api/cloud/usepod/x402/resume", {"operation_id": operation["operation_id"]})
    assert (status, bounded.get("code")) == (409, "paid_retry_attempts_exhausted"), bounded


# --- a lane that needs no key is still a usable cloud lane -----------------------------------------------------------


def test_an_accountless_lane_counts_as_a_usable_cloud_connection_and_lists_its_models(composed) -> None:
    """No provider key is stored anywhere in this home, and the only credential-store entry is the UsePod origin the owner
    chose. With the x402 lane selected and the wallet verifying Mainnet, the connection surfaces must say UsePod is usable
    and list its models -- otherwise the chat page offers no UsePod model to an accountless owner."""
    daemon = composed.daemon
    status, connections = daemon.call("GET", "/api/connections")
    status_models, models = daemon.call("GET", "/api/cloud/models?order=name")
    _keep("x402_accountless_connections.json", {"connections": connections, "models_provider": (models or {}).get("provider"), "model_ids": [row.get("id") for row in (models or {}).get("models") or []]})
    assert status == 200 and status_models == 200, (connections, models)
    cloud = [item for item in connections["connections"] if item.get("id") == "cloud"]
    assert cloud == [{"id": "cloud", "label": "UsePod (wallet x402)", "provider": "usepod", "state": "ok", "mode": "accountless_x402"}], connections
    assert models["provider"] == "usepod" and MODEL in [row["id"] for row in models["models"]], models


# --- Settings: the two ways to pay, in a real browser ----------------------------------------------------------------


@pytest.fixture(scope="module")
def browser_session():
    from tests.served_browser import launch_chromium

    manager, browser = launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        manager.stop()


def test_settings_shows_the_funded_token_and_the_wallet_as_two_ways_to_pay(composed, browser_session) -> None:
    daemon, service = composed.daemon, composed.service
    page = browser_session.new_page()
    try:
        page.goto(f"{daemon.base_url}/settings#models", wait_until="networkidle")
        page.wait_for_selector(".usepod-model-search", timeout=20000)
        # the pane paints twice; drive the second draw only
        page.wait_for_timeout(600)
        page.wait_for_selector(".usepod-pay-mode[data-mode='x402']", timeout=20000)
        wallet = page.inner_text(".usepod-pay-mode[data-mode='x402']")
        token = page.inner_text(".usepod-pay-mode[data-mode='prepaid_token']")
        _keep("x402_settings_pay_modes.json", {"wallet": wallet, "token": token})
        assert "Wallet x402 (pay per call) — in use" in wallet and AUTHORITY_LABEL in wallet and SOLANA_MAINNET in wallet and service.origin in wallet, wallet
        assert "Funded token (prepaid)" in token and "in use" not in token and "No token is stored yet" in token, token
        assert page.is_visible("button.usepod-use-prepaid") and page.is_visible("input.usepod-accountless-origin")
        # a fresh consent in SOL through the form: the asset choice and the fact sheet in SOL and lamports
        page.select_option("select.usepod-spend-asset", "SOL")
        assert page.inner_text("span.usepod-spend-unit") == "ceilings in lamports"
        page.fill("input.usepod-spend-percall", "5000000")
        page.fill("input.usepod-spend-total", "5000000")
        page.click("button.usepod-spend-propose")
        page.wait_for_selector("button.usepod-spend-deny", timeout=20000)
        sheet = page.inner_text(".usepod-spend-approval")
        _keep("x402_settings_sol_consent.json", {"sheet": sheet})
        assert "ONE call, up to 0.005000000 SOL (5000000 lamports)" in sheet and "up to 0.000005000 SOL (5000 lamports) per call" in sheet, sheet
        page.click("button.usepod-spend-deny")
        page.wait_for_function(
            "() => /You DENIED this consent/.test((document.querySelector('.usepod-spend-approval') || {innerText: ''}).innerText)",
            timeout=20000,
        )
        # the paid call whose provider had served it before the daemon died stays listed with its real state
        page.wait_for_selector(".usepod-x402-operation", timeout=20000)
        listed = page.inner_text(".usepod-x402-unresolved")
        _keep("x402_settings_unresolved.json", {"listed": listed})
        assert "paid retry failed" in listed and "liability unknown" in listed, listed
    finally:
        page.close()
