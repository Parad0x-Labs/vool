"""The owner's choice of payment channels: a channel that is off pays nothing, at every door that could pay through it.

A payment channel is one way a paid request is paid (VOOL's own x402 client; x402 or MPP on Solana through Solana
pay-kit). The owner turns each one on or off in Settings (Crypto, Payment channels). These tests pin what "off" means
on the doors themselves: no new payment is parked on it, a payment parked before is refused before the owner is asked
or anything is held, and a claimed payment's signature does not leave. Turning the channel back on pays as before.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from tests.asgi_harness import asgi_request
from tests.wallet._rig import DESTINATION, DEVNET, ScriptedX402Resource

pytestmark = [pytest.mark.safety]

PIN = "246810"
HEADERS = {"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}


@pytest.fixture
def app(wallet_env, monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_UI_CAPABILITY_SHA256", raising=False)
    from apps.vool_api_server import create_app
    from core.web.api import wallet_api
    from core.web.api.runtime import RuntimeServices

    wallet_api.reset_caller_binding_for_tests()
    yield create_app(RuntimeServices(display_name="VOOL"))
    wallet_api.reset_caller_binding_for_tests()


def _post(app, path: str, body: dict[str, Any], *, headers: dict[str, str] | None = None):
    status, _h, raw = asgi_request(app, method="POST", path=path, headers=headers or HEADERS, body=json.dumps(body).encode())
    return status, json.loads(raw or b"{}")


def _get(app, path: str):
    status, _h, raw = asgi_request(app, method="GET", path=path, headers={"Host": "127.0.0.1"})
    return status, json.loads(raw or b"{}")


def _pocket():
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _channels_by_id() -> dict[str, dict[str, Any]]:
    from core.wallet import channels

    return {row["channel_id"]: row for row in channels.status()}


def _approve(proposal_id: str, pin: str = PIN):
    from core.wallet import approval, lifecycle

    return lifecycle.default_lifecycle().approve_and_execute(proposal_id, approver=approval.PinApprover(pin))


class _TurnsTheChannelOffWhileAsked:
    """The owner's answer arrives after the channel was switched off (a second window, the credential's slow check):
    the door already let the payment through, so only the claim can still stop it."""

    def __init__(self, channel_id: str, pin: str = PIN) -> None:
        self.channel_id, self.pin, self.calls = channel_id, pin, 0

    def approve(self, challenge):
        from core.wallet import approval, channels

        self.calls += 1
        channels.set_on(self.channel_id, False)
        return approval.PinApprover(self.pin).approve(challenge)


def test_every_channel_starts_on_and_the_owners_choice_is_kept_in_the_wallet_store(wallet_env):
    from core.wallet import channels
    from core.wallet.errors import WalletFault
    from core.wallet.store import connection

    rows = _channels_by_id()
    assert list(rows) == ["x402", "paykit_x402", "paykit_mpp", "x402_sdk"]
    assert all(row["on"] and row["default_on"] for row in rows.values()), rows
    # what each channel can pay on today comes from the registry: VOOL's x402 on the rows that declare the exact scheme,
    # pay-kit on every Solana row whose transfer lane is ready (Mainnet's through the Crypto Pilot sheet), the x402 SDK
    # on every EVM row with a registered EIP-3009 token whose Crypto Pilot lane is ready (Base Sepolia and Base)
    assert rows["x402"]["networks"] == ["solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1", "eip155:84532", "eip155:11155111", "eip155:97"], rows["x402"]
    assert rows["paykit_x402"]["networks"] == rows["paykit_mpp"]["networks"] == ["solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"]
    assert rows["x402_sdk"]["networks"] == ["eip155:84532", "eip155:8453"] and rows["x402_sdk"]["built_by"] == "x402 Foundation SDK", rows["x402_sdk"]

    off = channels.set_on("paykit_mpp", False)
    assert off["on"] is False and channels.is_on("paykit_mpp") is False and channels.is_on("paykit_x402") is True
    assert _channels_by_id()["paykit_mpp"]["on"] is False
    with connection() as conn:
        assert conn.execute("SELECT value FROM wallet_controls WHERE key = 'channel.paykit_mpp'").fetchone()[0] == "off"
    assert channels.set_on("paykit_mpp", True)["on"] is True

    # only the exact value the writer stores turns a channel on: anything else in the row is off, never a default
    with connection() as conn:
        conn.execute("UPDATE wallet_controls SET value = 'yes' WHERE key = 'channel.paykit_mpp'")
    assert channels.is_on("paykit_mpp") is False and _channels_by_id()["paykit_mpp"]["on"] is False

    with pytest.raises(WalletFault) as unknown:
        channels.set_on("card_rails", True)
    assert (unknown.value.code, unknown.value.context["reason"]) == ("wallet_not_found", "unknown_payment_channel")


def test_an_x402_offer_on_a_channel_that_is_off_parks_nothing_and_pays_once_it_is_on(wallet_env, monkeypatch):
    from core.wallet import channels, proposals, x402
    from core.wallet.errors import WalletFault

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    profile = _pocket()
    channels.set_on("x402", False)
    with ScriptedX402Resource(wallet_env["rpc"]) as resource:
        with pytest.raises(WalletFault) as refused:
            x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert (refused.value.code, refused.value.context["reason"], refused.value.context["channel"]) == ("wallet_payment_channel_off", "payment_channel_off", "x402")
        # the 402 was read; nothing was parked, held or sent for it
        assert resource.challenges == 1 and proposals.list_proposals(limit=10) == [] and wallet_env["rpc"].send_count() == 0
        assert x402.binding_for_digest(x402._request_digest("GET", resource.url)) is None

        channels.set_on("x402", True)
        parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert parked.status == x402.OUTCOME_PAYMENT_REQUIRED and parked.proposal_id
        receipt = _approve(parked.proposal_id)
        assert receipt.state == proposals.STATE_CONFIRMED and wallet_env["rpc"].send_count() == 1
        delivered = x402.retry_paid_resource(parked.proposal_id)
        assert delivered.status == x402.OUTCOME_DELIVERED and len(resource.deliveries) == 1


def test_a_payment_parked_before_its_channel_was_turned_off_waits_and_is_refused_before_the_pin(wallet_env, monkeypatch):
    from core.wallet import channels, limits, proposals, status, x402
    from core.wallet.errors import WalletFault

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    profile = _pocket()
    with ScriptedX402Resource(wallet_env["rpc"]) as resource:
        parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        channels.set_on("x402", False)
        pending = status.wallet_status()["pending"]
        assert [(row["proposal_id"], row["payment_channel"]) for row in pending] == [(parked.proposal_id, {"channel_id": "x402", "label": "x402, built into VOOL", "on": False})]
        for pin in ("000000", PIN):
            with pytest.raises(WalletFault) as refused:
                _approve(parked.proposal_id, pin)
            assert (refused.value.code, refused.value.context["reason"], refused.value.context["channel"]) == ("wallet_payment_channel_off", "payment_channel_off", "x402")
        # the owner was never asked: a wrong PIN was not weighed or counted, nothing is held or sent, and the payment waits
        assert proposals.approval_refusals(parked.proposal_id) == 0
        assert limits.reservation_state(parked.proposal_id) == "" and wallet_env["rpc"].send_count() == 0
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        # the same request asked again while the channel is off is refused too, and parks nothing new
        with pytest.raises(WalletFault) as again:
            x402.fetch_paid_resource(resource.url.replace("/paid/report", "/paid/report?again=1"), wallet_id=profile.wallet_id)
        assert again.value.code == "wallet_payment_channel_off" and len(proposals.list_proposals(limit=10)) == 1

        channels.set_on("x402", True)
        # with the channel on, the owner is asked again: the same wrong PIN is weighed and counted this time
        with pytest.raises(WalletFault):
            _approve(parked.proposal_id, "000000")
        assert proposals.approval_refusals(parked.proposal_id) == 1 and wallet_env["rpc"].send_count() == 0
        receipt = _approve(parked.proposal_id)
        assert receipt.state == proposals.STATE_CONFIRMED and wallet_env["rpc"].send_count() == 1


def test_a_channel_turned_off_while_the_owner_is_asked_holds_and_sends_nothing(wallet_env, monkeypatch):
    from core.wallet import channels, limits, proposals, x402
    from core.wallet.errors import WalletFault

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    profile = _pocket()
    with ScriptedX402Resource(wallet_env["rpc"]) as resource:
        parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        racing = _TurnsTheChannelOffWhileAsked("x402")
        from core.wallet import lifecycle

        with pytest.raises(WalletFault) as refused:
            lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=racing)
        assert racing.calls == 1 and (refused.value.code, refused.value.context["channel"]) == ("wallet_payment_channel_off", "x402")
        assert limits.reservation_state(parked.proposal_id) == "" and wallet_env["rpc"].send_count() == 0
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL

        channels.set_on("x402", True)
        assert _approve(parked.proposal_id).state == proposals.STATE_CONFIRMED and wallet_env["rpc"].send_count() == 1


def test_the_crypto_pilot_sheet_checks_the_channel_before_the_credential_and_again_inside_its_claim(wallet_env, monkeypatch):
    """An x402 payment from a Crypto Pilot wallet: refused before the credential is asked while the channel is off,
    and refused inside the claim transaction when the channel is turned off while the credential is checked (the
    quote stays open, nothing is held or sent). On again, the same quote pays once."""
    import uuid

    from core.wallet import capabilities, chains, channels, lifecycle, limits, pilot_custody, proposals, quotes, x402
    from core.wallet.errors import WalletFault

    solana_devnet = chains.resolve_network(DEVNET).network
    node = wallet_env["rpc"]
    node.real_signature = True
    node.status_keyed = True
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setattr(capabilities, "PILOT_TRANSFER_READY_ROWS", frozenset({solana_devnet}))
    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
    chains.invalidate_chain_identity()
    created = pilot_custody.create_pilot_wallet(network=solana_devnet, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"ch-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    engine = lifecycle.default_lifecycle()
    try:
        with ScriptedX402Resource(node) as resource:
            parked = x402.fetch_paid_resource(resource.url, wallet_id=created["wallet_id"])
            assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL
            quote = quotes.mint_quote(parked.proposal_id)

            channels.set_on("x402", False)
            unasked = _TurnsTheChannelOffWhileAsked("x402")
            with pytest.raises(WalletFault) as refused:
                engine.approve_pilot_transfer(parked.proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=unasked)
            assert unasked.calls == 0 and refused.value.code == "wallet_payment_channel_off"

            channels.set_on("x402", True)
            racing = _TurnsTheChannelOffWhileAsked("x402")
            with pytest.raises(WalletFault) as raced:
                engine.approve_pilot_transfer(parked.proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=racing)
            assert racing.calls == 1 and (raced.value.code, raced.value.context["channel"]) == ("wallet_payment_channel_off", "x402")
            assert limits.reservation_state(parked.proposal_id) == "" and node.send_count() == 0
            assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL

            channels.set_on("x402", True)
            from core.wallet import approval

            paid = engine.approve_pilot_transfer(parked.proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))
            assert paid["duplicate"] is False and node.send_count() == 1, paid
    finally:
        chains.invalidate_chain_identity()


def test_payments_no_channel_carries_pay_with_every_channel_off(wallet_env):
    """The owner's own transfers and a model's request are not paid requests: the channel switches never touch them."""
    from core.wallet import channels, lifecycle, proposals

    profile = _pocket()
    for channel in channels.CHANNELS:
        channels.set_on(channel.channel_id, False)
    engine = lifecycle.default_lifecycle()
    for sent, origin in enumerate((proposals.ORIGIN_USER, proposals.ORIGIN_MODEL), start=1):
        proposal = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1500, asset="SOL", origin=origin, idempotency_key=f"plain:{origin}")
        assert engine.prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        assert _approve(proposal.proposal_id).state == proposals.STATE_CONFIRMED and wallet_env["rpc"].send_count() == sent


def test_a_claimed_payments_signature_does_not_leave_while_its_channel_is_off(app, wallet_env, monkeypatch):
    """The external signer's doors: no bytes are handed out for a payment on a channel that is off, and a payment
    claimed (held, its signing request open) before its channel was turned off is refused when its signature is
    submitted. Nothing is broadcast; the hold and the request stay as they are, so the same signature completes it
    once the channel is on again."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import channels, external_signing, lifecycle, limits, proposals, x402
    from core.wallet.errors import WalletFault

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    key = Ed25519PrivateKey.generate()
    _status, registered = _post(app, "/api/wallet/external", {"public_key": b58encode(key.public_key().public_bytes_raw()), "network": DEVNET, "label": "ext"})
    wallet_id = registered["wallet"]["wallet_id"]
    engine = lifecycle.default_lifecycle()
    with ScriptedX402Resource(wallet_env["rpc"]) as resource:
        parked = x402.fetch_paid_resource(resource.url, wallet_id=wallet_id)
        # off before the bytes are asked for: no request, no hold
        channels.set_on("x402", False)
        with pytest.raises(WalletFault) as unasked:
            engine.request_external_signature(parked.proposal_id)
        assert unasked.value.code == "wallet_payment_channel_off" and limits.reservation_state(parked.proposal_id) == ""
        assert external_signing.open_request_for_proposal(parked.proposal_id) is None
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL

        channels.set_on("x402", True)
        view = engine.request_external_signature(parked.proposal_id)
        assert limits.reservation_state(parked.proposal_id) == limits.RESERVATION_RESERVED
        signature = b58encode(key.sign(base64.b64decode(view["message_b64"])))

        channels.set_on("x402", False)
        # the open request is not handed out again while the channel is off
        with pytest.raises(WalletFault) as resumed:
            engine.request_external_signature(parked.proposal_id)
        assert resumed.value.code == "wallet_payment_channel_off"
        with pytest.raises(WalletFault) as refused:
            engine.submit_external_signature(view["request_id"], signature_b58=signature)
        assert (refused.value.code, refused.value.context["channel"]) == ("wallet_payment_channel_off", "x402")
        assert wallet_env["rpc"].send_count() == 0
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_AWAITING_SIGNATURE
        assert limits.reservation_state(parked.proposal_id) == limits.RESERVATION_RESERVED

        channels.set_on("x402", True)
        receipt = engine.submit_external_signature(view["request_id"], signature_b58=signature)
        assert receipt.state in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST} and wallet_env["rpc"].send_count() == 1

        # a replayed answer for a payment already sent is a duplicate, never "nothing was sent", whatever the channel
        channels.set_on("x402", False)
        with pytest.raises(WalletFault) as replayed:
            engine.submit_external_signature(view["request_id"], signature_b58=signature)
        assert replayed.value.code == "wallet_duplicate_payment" and wallet_env["rpc"].send_count() == 1


def test_a_payment_stranded_after_its_claim_is_not_handed_to_a_signer_while_its_channel_is_off(app, wallet_env, monkeypatch):
    """The recovery seam (a crash between the claim and the signing request) re-opens a request: not while the
    channel is off."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import channels, external_signing, lifecycle, proposals, x402
    from core.wallet.errors import WalletFault

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    key = Ed25519PrivateKey.generate()
    _status, registered = _post(app, "/api/wallet/external", {"public_key": b58encode(key.public_key().public_bytes_raw()), "network": DEVNET, "label": "ext"})
    with ScriptedX402Resource(wallet_env["rpc"]) as resource:
        parked = x402.fetch_paid_resource(resource.url, wallet_id=registered["wallet"]["wallet_id"])
        stranded = proposals.transition(parked.proposal_id, proposals.STATE_APPROVED, detail={"method": "external_signer"}, expected_state=proposals.STATE_PENDING_APPROVAL)
        assert stranded is not None
        channels.set_on("x402", False)
        with pytest.raises(WalletFault) as refused:
            lifecycle.default_lifecycle().request_external_signature(parked.proposal_id)
        assert (refused.value.code, refused.value.context["channel"]) == ("wallet_payment_channel_off", "x402")
        assert external_signing.open_request_for_proposal(parked.proposal_id) is None
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_APPROVED and wallet_env["rpc"].send_count() == 0


@pytest.mark.parametrize(("origin", "channel"), [("x402_paykit", "paykit_x402"), ("mpp_paykit", "paykit_mpp")])
def test_a_pay_kit_channel_that_is_off_parks_nothing_at_any_door(app, wallet_env, origin, channel):
    """The owner's local propose door takes an origin as given: a pay-kit payment proposed there while its channel is off
    is refused before anything is written. The other pay-kit channel is a separate choice and keeps working."""
    from core.wallet import channels, custody, proposals

    _post(app, "/api/wallet/pocket/create", {"pin": PIN, "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
    channels.set_on(channel, False)
    status, refused = _post(app, "/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 1500, "asset": "SOL", "origin": origin, "idempotency_key": "pk:off"})
    assert status == 403 and refused["error"] == "wallet_payment_channel_off" and refused["fault"]["context"]["channel"] == channel, refused
    assert refused["message"].startswith("That payment goes through a payment channel that is turned off")
    assert proposals.list_proposals(limit=10) == []
    other = "mpp_paykit" if origin == "x402_paykit" else "x402_paykit"
    status, parked = _post(app, "/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 1500, "asset": "SOL", "origin": other, "idempotency_key": "pk:other"})
    assert status == 200 and parked["proposal"]["origin"] == other, parked


def test_the_settings_door_sets_one_channel_and_is_a_trusted_owner_door(app, wallet_env, monkeypatch):
    from core.wallet import channels

    status, body = _post(app, "/api/wallet/channels", {"channel": "paykit_x402", "on": False})
    assert status == 200 and body["channel"]["channel_id"] == "paykit_x402" and body["channel"]["on"] is False, body
    assert [(row["channel_id"], row["on"]) for row in body["payment_channels"]] == [("x402", True), ("paykit_x402", False), ("paykit_mpp", True), ("x402_sdk", True)]
    status, st = _get(app, "/api/wallet/status")
    assert [(row["channel_id"], row["on"]) for row in st["status"]["crypto_pilot"]["payment_channels"]] == [("x402", True), ("paykit_x402", False), ("paykit_mpp", True), ("x402_sdk", True)]

    # only a boolean is a choice; an unknown channel is not one
    status, bad = _post(app, "/api/wallet/channels", {"channel": "x402", "on": "false"})
    assert (status, bad["error"]) == (400, "channel_choice_invalid") and channels.is_on("x402") is True
    status, unknown = _post(app, "/api/wallet/channels", {"channel": "card_rails", "on": True})
    assert (status, unknown["error"], unknown["fault"]["context"]["reason"]) == (404, "wallet_not_found", "unknown_payment_channel")

    # a page on another localhost port cannot change the choice (the door is a trusted Settings door)
    cross = {"Host": "127.0.0.1:11435", "Origin": "http://127.0.0.1:3000", "Content-Type": "application/json"}
    status, refused = _post(app, "/api/wallet/channels", {"channel": "x402", "on": False}, headers=cross)
    assert (status, refused["error"], refused["fault"]["context"]["reason"]) == (403, "wallet_caller_refused", "origin_mismatch")
    assert channels.is_on("x402") is True

    # with Crypto off, nothing about payments changes
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "0")
    status, off = _post(app, "/api/wallet/channels", {"channel": "x402", "on": False})
    assert (status, off["error"]) == (403, "wallet_disabled")
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    assert channels.is_on("x402") is True


def test_the_library_channels_say_whether_this_install_can_use_them(monkeypatch):
    """The Settings rows say when the pay or x402 extra is missing, or Python is too old for pay-kit, without importing
    either library."""
    import importlib.machinery
    import types

    from core.wallet import channels

    def rows() -> list[tuple[str, bool, str, str]]:
        return [(c["channel_id"], c["available"], c["reason"], c["needs"]) for c in (channels.view(channel, on=True) for channel in channels.CHANNELS)]

    imported: list[str] = []
    monkeypatch.setattr(channels.importlib.util, "find_spec", lambda name, *a: imported.append(name) or None)
    assert rows() == [("x402", True, "available", ""), ("paykit_x402", False, "paykit_not_installed", "pay extra"), ("paykit_mpp", False, "paykit_not_installed", "pay extra"),
                      ("x402_sdk", False, "x402_sdk_not_installed", "x402 extra")]
    assert set(imported) == {"solana_pay_kit", "x402"}

    monkeypatch.setattr(channels.importlib.util, "find_spec", lambda name, *a: importlib.machinery.ModuleSpec(name, None))
    assert [row[1:3] for row in rows()] == [(True, "available")] * 4
    monkeypatch.setattr(channels, "sys", types.SimpleNamespace(version_info=(3, 10, 14)))
    assert [row[1:3] for row in rows()] == [(True, "available"), (False, "python_below_3_11"), (False, "python_below_3_11"), (True, "available")]


def test_a_choice_that_cannot_be_read_is_said_as_unknown_and_keeps_the_pending_list(wallet_env, monkeypatch):
    from core.wallet import channels, status, x402

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    profile = _pocket()
    with ScriptedX402Resource(wallet_env["rpc"]) as resource:
        parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)

    def unreadable(*_a, **_k):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(channels, "is_on", unreadable)
    monkeypatch.setattr(channels, "status", unreadable)
    current = status.wallet_status()
    assert [(row["proposal_id"], row["payment_channel"]["on"]) for row in current["pending"]] == [(parked.proposal_id, None)]
    assert current["pending_approvals"] == 1
    assert status.crypto_pilot_status()["payment_channels"] == [{"error": "unreadable:RuntimeError"}]
