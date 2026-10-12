"""The x402 SDK lane's own rules, without the SDK: the guard on the one signature, the settlement proof, the expiry
release, and the door's refusal when the optional ``x402`` extra is absent.

These run wherever eth-account runs (CI's ``evm`` extra). The SDK drives that exercise them end to end are in
``test_wallet_x402_sdk_pilot.py`` and skip without the extra.
"""
from __future__ import annotations

import base64
import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("eth_account", reason="EVM signing is the optional `evm` extra")

pytestmark = [pytest.mark.safety]

CHAIN_ID = 84532
TOKEN = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"  # the registry's Base Sepolia USDC
PAYEE = "0x209693bc6afc0c5328ba36faf03c514ef312287c"
AMOUNT = 250_000
WINDOW = 60
NOW = 1_800_000_000.0
NONCE = "0x" + "4e" * 32
TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
USED_TOPIC0 = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"
_SDK_TYPES = {"TransferWithAuthorization": [
    {"name": "from", "type": "address"}, {"name": "to", "type": "address"}, {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"}, {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"},
]}


@pytest.fixture
def account():
    from eth_account import Account

    return Account.create()


def _terms(payer: str) -> dict:
    return {"payer": payer, "pay_to": PAYEE, "amount_minor": AMOUNT, "chain_id": CHAIN_ID, "token": TOKEN, "eip712_name": "USDC", "eip712_version": "2", "window_seconds": WINDOW}


def _request(payer: str, **changes) -> tuple[dict, dict, str, dict]:
    """(domain, types, primary type, message) as the SDK hands them to its signer, with ``changes`` applied."""
    domain = {"name": "USDC", "version": "2", "chainId": CHAIN_ID, "verifyingContract": TOKEN}
    message = {"from": payer, "to": PAYEE, "value": AMOUNT, "validAfter": 0, "validBefore": int(NOW) + WINDOW, "nonce": bytes.fromhex(NONCE[2:])}
    types = json.loads(json.dumps(_SDK_TYPES))
    primary = "TransferWithAuthorization"
    for key, value in changes.items():
        where, _, name = key.partition("__")
        if where == "domain":
            domain[name] = value
        elif where == "message":
            message[name] = value
        elif where == "types":
            types = value
        elif where == "primary":
            primary = value
    return domain, types, primary, message


# --- the guard on the one signature -------------------------------------------------------------------------------

REFUSALS = {
    "another primary type": ({"primary": "Permit"}, "x402_sdk_primary_type_not_transfer_with_authorization"),
    "an extra type": ({"types": {**_SDK_TYPES, "Extra": [{"name": "x", "type": "uint256"}]}}, "x402_sdk_type_fields_differ"),
    "a field list in another order": ({"types": {"TransferWithAuthorization": list(reversed(_SDK_TYPES["TransferWithAuthorization"]))}}, "x402_sdk_type_fields_differ"),
    "another token name": ({"domain__name": "USD Coin"}, "x402_sdk_domain_not_the_registry_token"),
    "another token version": ({"domain__version": "1"}, "x402_sdk_domain_not_the_registry_token"),
    "another chain": ({"domain__chainId": 8453}, "x402_sdk_domain_not_the_registry_token"),
    "another token contract": ({"domain__verifyingContract": "0x" + "7" * 40}, "x402_sdk_domain_not_the_registry_token"),
    "another payer": ({"message__from": "0x" + "8" * 40}, "x402_sdk_payer_not_this_wallet"),
    "another payee": ({"message__to": "0x" + "6" * 40}, "x402_sdk_payee_not_approved"),
    "one unit more": ({"message__value": AMOUNT + 1}, "x402_sdk_amount_not_approved"),
    "valid only later": ({"message__validAfter": int(NOW) + 5}, "x402_sdk_valid_after_in_the_future"),
    "already expired": ({"message__validBefore": int(NOW)}, "x402_sdk_window_not_approved"),
    "a longer window": ({"message__validBefore": int(NOW) + WINDOW + 31}, "x402_sdk_window_not_approved"),
    "a 31-byte nonce": ({"message__nonce": b"\x01" * 31}, "x402_sdk_message_malformed"),
}


@pytest.mark.parametrize("case", list(REFUSALS))
def test_the_guard_refuses_typed_data_that_is_not_exactly_the_approved_authorization(account, case):
    from core.wallet import x402_sdk
    from core.wallet.errors import WalletFault

    changes, reason = REFUSALS[case]
    domain, types, primary, message = _request(account.address, **changes)
    with pytest.raises(WalletFault) as refused:
        x402_sdk.verify_authorization_request(domain, types, primary, message, terms=_terms(account.address), now=NOW)
    assert (refused.value.code, refused.value.context["reason"]) == ("wallet_signature_invalid", reason)


def test_the_guard_accepts_the_approved_authorization_within_the_clock_skew_and_signs_what_the_token_verifies(account):
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    from core.wallet import evm, x402_sdk

    domain, types, primary, message = _request(account.address, message__validBefore=int(NOW) + WINDOW + 30)
    typed = x402_sdk.verify_authorization_request(domain, types, primary, message, terms=_terms(account.address), now=NOW)
    signature = evm.sign_authorization(account.key.hex(), typed)
    # the token recovers the signer over ITS domain and the SDK's message: the same digest the wallet signed
    token_view = {"types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"}, {"name": "chainId", "type": "uint256"},
                                             {"name": "verifyingContract", "type": "address"}], **types},
                  "primaryType": primary, "domain": domain, "message": message}
    assert Account.recover_message(encode_typed_data(full_message=token_view), signature=bytes.fromhex(signature.removeprefix("0x"))) == account.address


class _Session:
    """A Crypto Pilot signing session stand-in that signs VOOL's typed-data form with a throwaway key."""

    def __init__(self, key_hex: str) -> None:
        self.key_hex = key_hex
        self.signed: list[dict] = []

    def sign_evm_typed_data(self, typed: dict) -> str:
        from core.wallet import evm

        self.signed.append(typed)
        return evm.sign_authorization(self.key_hex, typed)


def test_the_guarded_signer_signs_once_and_never_hands_refused_data_to_the_key(account):
    from core.wallet import x402_sdk
    from core.wallet.errors import WalletFault

    session = _Session(account.key.hex())
    signer = x402_sdk.GuardedTypedDataSigner(session, terms=_terms(account.address.lower()), clock=lambda: NOW)
    assert signer.address == account.address, "the SDK sees the checksummed address"
    with pytest.raises(WalletFault):
        signer.sign_typed_data(*_request(account.address, message__value=AMOUNT * 2))
    assert session.signed == [] and signer.signature == ""

    signature = signer.sign_typed_data(*_request(account.address))
    assert len(signature) == 65 and len(session.signed) == 1
    assert signer.authorization() == {"kind": "eip3009", "token": TOKEN, "nonce": NONCE, "valid_after": 0, "valid_before": int(NOW) + WINDOW}
    with pytest.raises(WalletFault) as second:
        signer.sign_typed_data(*_request(account.address))
    assert second.value.context["reason"] == "x402_sdk_second_signature_refused" and len(session.signed) == 1


# --- what proves the payment --------------------------------------------------------------------------------------

def _word(address: str) -> str:
    return "0x" + address.lower().removeprefix("0x").rjust(64, "0")


def _receipt(payer: str, *, status="0x1", nonce=NONCE, value=AMOUNT, token=TOKEN, used=True, transferred=True) -> dict:
    logs = []
    if transferred:
        logs.append({"address": token, "topics": [TRANSFER_TOPIC0, _word(payer), _word(PAYEE)], "data": "0x" + format(value, "064x")})
    if used:
        logs.append({"address": token, "topics": [USED_TOPIC0, _word(payer), nonce], "data": "0x"})
    return {"status": status, "logs": logs}


@pytest.mark.parametrize(("case", "receipt_changes", "proven"), [
    ("this payment", {}, True),
    ("reverted: the authorization was not used", {"status": "0x0"}, False),
    ("another authorization of the same amount to the same payee", {"nonce": "0x" + "4f" * 32}, False),
    ("another amount", {"value": AMOUNT - 1}, False),
    ("logs of another contract", {"token": "0x" + "7" * 40}, False),
    ("a transfer without the authorization's use", {"used": False}, False),
    ("the use without the transfer", {"transferred": False}, False),
])
def test_a_receipt_proves_the_payment_only_with_this_authorizations_use_and_its_exact_transfer(account, case, receipt_changes, proven):
    from core.wallet import x402_sdk

    receipt = _receipt(account.address, **receipt_changes)
    assert x402_sdk.proves_authorization(receipt, token=TOKEN, payer=account.address, pay_to=PAYEE, amount_minor=AMOUNT, nonce=NONCE) is proven, case


# --- when an authorization that was never used may end -----------------------------------------------------------

class _Node:
    def __init__(self, *, finalized_time: int | None, used: bool | None) -> None:
        self.finalized_time = finalized_time
        self.used = used
        self.calls: list[tuple[str, list]] = []

    def _call(self, method: str, params: list):
        self.calls.append((method, params))
        if method == "eth_getBlockByNumber":
            assert params[0] == "finalized", "the window is judged at the finalized block only"
            return {"number": hex(500), "timestamp": hex(self.finalized_time)} if self.finalized_time is not None else {}
        if method == "eth_call":
            assert params[1] == hex(500), "the token's state is read at that same finalized block"
            if self.used is None:
                raise OSError("no answer")
            return "0x" + format(1 if self.used else 0, "064x")
        raise AssertionError(method)


@pytest.mark.parametrize(("finalized_time", "used", "verdict"), [
    (int(NOW) + WINDOW, False, "open"),  # the window's last second: the token could still accept it
    (int(NOW) + WINDOW + 1, False, "expired_unused"),
    (int(NOW) + WINDOW + 1, True, "used"),
    (int(NOW) + WINDOW + 1, None, "unreadable"),
    (None, False, "unreadable"),
])
def test_an_authorization_counts_as_unused_only_past_its_window_at_a_finalized_block_that_says_so(finalized_time, used, verdict):
    from core.wallet import settlement

    row = {"from_address": "0x" + "1" * 40, "authorization_json": json.dumps({"kind": "eip3009", "token": TOKEN, "nonce": NONCE, "valid_before": int(NOW) + WINDOW})}
    node = _Node(finalized_time=finalized_time, used=used)
    assert settlement._authorization_expiry(row, node)[0] == verdict
    if verdict == "open":
        assert [method for method, _ in node.calls] == ["eth_getBlockByNumber"], "inside the window the token is not even asked"


def test_an_authorization_row_without_its_signed_window_never_counts_as_unused():
    from core.wallet import settlement

    row = {"from_address": "0x" + "1" * 40, "authorization_json": json.dumps({"kind": "eip3009", "token": TOKEN, "from_block": 7})}
    node = _Node(finalized_time=int(NOW) * 2, used=False)
    assert settlement._authorization_expiry(row, node) == ("unreadable", {"why": "authorization_not_recorded"}) and node.calls == []


def _claimed(proposal, *, authorization: bool) -> None:
    from core.wallet import controls, limits, transfers
    from core.wallet.store import connection

    now = time.time()
    signed = {"kind": "eip3009", "token": TOKEN, "from_block": 7, "nonce": NONCE, "valid_before": int(now) + WINDOW}
    with connection() as conn:
        limits._begin_immediate(conn)
        transfers.insert_claim(
            conn, proposal=proposal, quote_id=f"q-{uuid.uuid4().hex}", quote_digest="quote-digest", challenge_digest="challenge-digest", environment="testnet",
            family="evm", from_address="0x" + "1" * 40, fee_max_minor=0, owner_token="token-1", lease_until=now + 30, dispatch_deadline=now + 60,
            epochs=controls.epochs(conn), enabled_generation=0, now=now, authorization_json=json.dumps(signed) if authorization else "",
        )


def test_an_authorization_that_left_and_was_never_used_ends_failed_with_nothing_counted_never_as_not_sent(wallet_env):
    """Only an authorization row may leave ``unknown`` for a failure without a receipt, and it ends ``failed_on_chain``
    (it left, so it is never ``released``, which says nothing left this device): nothing moved and no fee, so the hold
    settles at zero, and the sheet says it expired unused. Any other row in ``unknown`` is refused that edge, and its
    effect record is never resolved for it."""
    from core.wallet import settlement, transfers
    from tests.wallet.test_crypto_pilot_transfer_store import (
        _hold,
        _move,
        _pending_proposal,
        _proposal_state,
        _row,
        _walk,
    )

    paid, plain = _pending_proposal(), _pending_proposal()
    _claimed(paid, authorization=True)
    _claimed(plain, authorization=False)
    _walk(paid.proposal_id, ("signed", {}), ("dispatching", {}), ("unknown", {}))
    _walk(plain.proposal_id, ("signed", {"tx_id": "sig-plain", "raw_b64": "cmF3"}), ("dispatching", {}), ("unknown", {}))

    _move(paid.proposal_id, "failed_on_chain", "unknown", charged_fee_minor=0)
    view = transfers.view_of(_row(paid.proposal_id))
    assert (view["state"], _proposal_state(paid.proposal_id), _hold(paid.proposal_id)) == ("failed_on_chain", "failed", (0, 0, "settled"))
    assert (view["state_label"], view["dispatched"]) == ("Expired unused", True) and "Nothing moved and no fee" in view["detail"]

    with pytest.raises(transfers.TransferTransitionError, match="edge_not_allowed:unknown->failed_on_chain"):
        _move(plain.proposal_id, "failed_on_chain", "unknown", charged_fee_minor=0)
    assert settlement.apply_transition(plain.proposal_id, "failed_on_chain", "unknown", charged_fee_minor=0) is None
    assert (_row(plain.proposal_id)["state"], _row(plain.proposal_id)["a6_outcome"] or "", _hold(plain.proposal_id)[2]) == ("unknown", "", "reserved")
    assert ("unknown", "released") not in transfers.PROJECTION


# --- the door without the optional extra --------------------------------------------------------------------------

@pytest.fixture
def base_sepolia(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", str(AMOUNT * 2))
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    environment.set_active_environment("testnet")
    yield
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


class _Base402:
    """A v2 402 offering Base Sepolia USDC, written by hand (no SDK): the PAYMENT-REQUIRED header as JSON in base64."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        resource = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def do_GET(self) -> None:
                resource.requests.append({"paid": bool(self.headers.get("PAYMENT-SIGNATURE"))})
                challenge = {"x402Version": 2, "error": "payment required", "resource": {"url": resource.url},
                             "accepts": [{"scheme": "exact", "network": "eip155:84532", "amount": str(AMOUNT), "asset": TOKEN, "payTo": PAYEE,
                                          "maxTimeoutSeconds": WINDOW, "extra": {"name": "USDC", "version": "2"}}]}
                body = b"{}"
                self.send_response(402)
                self.send_header("PAYMENT-REQUIRED", base64.b64encode(json.dumps(challenge).encode()).decode())
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/paid"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()


def test_without_the_extra_a_pilot_wallets_base_offer_is_refused_by_name_and_nothing_is_proposed(base_sepolia, monkeypatch):
    """The extra is made absent the way a missing package is (its import fails), whether or not it is installed."""
    from core.wallet import pilot_custody, proposals, x402, x402_sdk
    from core.wallet.errors import WalletFault

    monkeypatch.setitem(sys.modules, "x402.mechanisms.evm.exact.client", None)
    assert x402_sdk.availability() == (False, "x402_sdk_not_installed")
    pin = "482913"
    created = pilot_custody.create_pilot_wallet(network="eip155:84532", method="pin", credential=pin, credential_confirmation=pin, creation_key=f"r-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=pin)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    with _Base402() as resource:
        with pytest.raises(WalletFault) as refused:
            x402.fetch_paid_resource(resource.url, wallet_id=wallet["wallet_id"])
        assert (refused.value.code, refused.value.context["reason"], refused.value.context["dependency"]) == ("wallet_x402_sdk_unavailable", "x402_sdk_not_installed", "x402")
        assert proposals.list_proposals() == [] and resource.requests == [{"paid": False}]


def test_a_wallet_that_is_not_a_crypto_pilot_wallet_keeps_its_own_x402_lane(base_sepolia):
    from core.wallet import custody, x402_sdk

    watch = custody.create_watch_only_wallet("0x" + "1" * 40, network="eip155:84532")
    headers = {"payment-required": base64.b64encode(json.dumps({"x402Version": 2, "accepts": [{"scheme": "exact", "network": "eip155:84532"}]}).encode()).decode()}
    assert x402_sdk.names_challenge(headers, network="eip155:84532") is True
    assert x402_sdk.claims_challenge(headers, b"", wallet_id=watch.wallet_id) is False

