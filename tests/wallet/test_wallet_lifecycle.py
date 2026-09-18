"""The typed payment lifecycle: proposal -> simulation -> limits -> approval -> signing ->
broadcast -> receipt, on testnet only, with duplicate protection, capped x402, token-only
cards, and every refusal filed as a typed fault + security event + redacted receipt.
"""
from __future__ import annotations

import json
import time

import pytest

from tests.wallet._rig import DESTINATION, DEVNET, OTHER_DESTINATION, fault_codes, fault_dumps, sec_codes, x402_body

pytestmark = [pytest.mark.safety]

PIN = "246810"


def _pocket(custody):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _drive(lifecycle, proposal, *, pin=PIN):
    from core.wallet import approval

    lifecycle.prepare(proposal.proposal_id)
    return lifecycle.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(pin))


def test_full_lifecycle_lands_a_testnet_broadcast_and_a_redacted_receipt(wallet_env):
    from core.wallet import custody, lifecycle, proposals, receipts

    profile = _pocket(custody)
    proposal = proposals.propose_transaction(
        wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=5_000, asset="SOL", origin=proposals.ORIGIN_USER, memo="coffee"
    )
    engine = lifecycle.default_lifecycle()
    prepared = engine.prepare(proposal.proposal_id)
    assert prepared.state == proposals.STATE_PENDING_APPROVAL
    assert prepared.simulation["ok"] is True and prepared.simulation["fee_minor"] >= 0

    from core.wallet import approval

    receipt = engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert receipt.state == proposals.STATE_CONFIRMED
    assert receipt.tx_signature and receipt.network == DEVNET
    assert wallet_env["rpc"].send_count() == 1
    trail = [e["state"] for e in proposals.proposal_events(proposal.proposal_id) if e["state"] != proposals.EVENT_APPROVAL_REFUSED]
    assert trail == [
        proposals.STATE_PROPOSED,
        proposals.STATE_SIMULATED,
        proposals.STATE_LIMITS_CHECKED,
        proposals.STATE_PENDING_APPROVAL,
        proposals.STATE_APPROVED,
        proposals.STATE_SIGNED,
        proposals.STATE_BROADCAST,
        proposals.STATE_CONFIRMED,
    ]
    stored = receipts.list_receipts()
    assert stored and stored[0]["tx_signature"] == receipt.tx_signature
    blob = json.dumps(stored).lower()
    assert "pin" not in blob.split('"')  # the pin is never a key in a receipt
    assert PIN not in blob and "seed" not in blob and "phrase" not in blob


def test_wrong_pin_rejects_and_files_a_security_event_without_broadcasting(wallet_env):
    from core.wallet import approval, custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    proposal = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1_000, asset="SOL", origin="user")
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    with pytest.raises(WalletFault) as exc:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover("999999"))
    assert exc.value.code == "wallet_approval_rejected"
    assert wallet_env["rpc"].send_count() == 0
    # a mistyped PIN is a counted refusal, not the end of the payment: the owner may still approve it
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert proposals.approval_refusals(proposal.proposal_id) == 1
    assert "wallet_approval_rejected" in fault_codes()
    assert "SEC_WALLET_APPROVAL_REJECTED" in sec_codes()
    receipt = engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert receipt.state == proposals.STATE_CONFIRMED and wallet_env["rpc"].send_count() == 1
    # ... but not forever: MAX_APPROVAL_ATTEMPTS refusals lock the proposal
    other = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1_000, asset="SOL", origin="user", memo="lock")
    engine.prepare(other.proposal_id)
    for _ in range(lifecycle.MAX_APPROVAL_ATTEMPTS):
        with pytest.raises(WalletFault):
            engine.approve_and_execute(other.proposal_id, approver=approval.PinApprover("999999"))
    assert proposals.get_proposal(other.proposal_id).state == proposals.STATE_REJECTED
    with pytest.raises(WalletFault) as locked:
        engine.approve_and_execute(other.proposal_id, approver=approval.PinApprover(PIN))
    assert locked.value.code == "wallet_duplicate_payment"
    assert wallet_env["rpc"].send_count() == 1


def test_biometric_abstraction_is_a_typed_seam_that_binds_to_the_proposal(wallet_env):
    from core.wallet import approval, custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    a = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1_000, asset="SOL", origin="user")
    b = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=OTHER_DESTINATION, amount_minor=1_000, asset="SOL", origin="user")
    engine = lifecycle.default_lifecycle()
    engine.prepare(a.proposal_id)
    engine.prepare(b.proposal_id)

    class ReplayingBiometric(approval.BiometricApprover):
        def __init__(self) -> None:
            self.seen: list[approval.ApprovalChallenge] = []

        def approve(self, challenge: approval.ApprovalChallenge) -> approval.ApprovalDecision:
            self.seen.append(challenge)
            # an approval minted for proposal A, replayed at proposal B
            return approval.ApprovalDecision(approved=True, method="biometric", challenge_digest=self.seen[0].digest, pin_unlock=PIN)

    bio = ReplayingBiometric()
    receipt = engine.approve_and_execute(a.proposal_id, approver=bio)
    assert receipt.state == proposals.STATE_CONFIRMED
    with pytest.raises(WalletFault) as exc:
        engine.approve_and_execute(b.proposal_id, approver=bio)
    assert exc.value.code == "wallet_approval_rejected"
    assert wallet_env["rpc"].send_count() == 1
    assert approval.UnavailableBiometric().approve(bio.seen[0]).approved is False


def test_per_transaction_daily_and_destination_limits_each_bite(wallet_env):
    from core.wallet import custody, limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=15_000, daily_minor=45_000, per_destination_daily_minor=30_000))  # every payment also holds the 5_000 fee

    def spend(amount, dest=DESTINATION):
        p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=dest, amount_minor=amount, asset="SOL", origin="user")
        from core.wallet import lifecycle

        return _drive(lifecycle.default_lifecycle(), p)

    with pytest.raises(WalletFault) as exc:
        spend(15_001)
    assert exc.value.code == "wallet_limit_exceeded" and exc.value.context["limit"] == "per_transaction"

    spend(9_000)
    spend(6_000)  # 25_000 (with fees) to DESTINATION today
    with pytest.raises(WalletFault) as exc2:
        spend(1)  # destination cap: 15_001 > 15_000
    assert exc2.value.code == "wallet_limit_exceeded" and exc2.value.context["limit"] == "per_destination_daily"

    spend(9_000, OTHER_DESTINATION)  # 39_000 today including fees
    with pytest.raises(WalletFault) as exc3:
        spend(2_000, OTHER_DESTINATION)  # 46_000 > 45_000 daily including fees
    assert exc3.value.code == "wallet_limit_exceeded" and exc3.value.context["limit"] == "daily"
    assert wallet_env["rpc"].send_count() == 3


def test_duplicate_payment_is_collapsed_onto_the_first_proposal_and_never_rebroadcast(wallet_env):
    from core.wallet import custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    kwargs = dict(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=700, asset="SOL", origin="model", idempotency_key="invoice-42")
    first = proposals.propose_transaction(**kwargs)
    again = proposals.propose_transaction(**kwargs)
    assert again.proposal_id == first.proposal_id
    engine = lifecycle.default_lifecycle()
    _drive(engine, first)
    assert wallet_env["rpc"].send_count() == 1
    # a re-drive of a finished proposal is a typed duplicate, not a second broadcast
    with pytest.raises(WalletFault) as exc:
        _drive(engine, first)
    assert exc.value.code == "wallet_duplicate_payment"
    # and the same key with DIFFERENT content is a conflict, not a silent reuse
    with pytest.raises(WalletFault) as exc2:
        proposals.propose_transaction(**{**kwargs, "amount_minor": 701})
    assert exc2.value.code == "wallet_duplicate_payment"
    assert wallet_env["rpc"].send_count() == 1


def test_concurrent_identical_proposals_yield_one_broadcast(wallet_env):
    import threading

    from core.wallet import approval, custody, lifecycle, proposals

    profile = _pocket(custody)
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=300, asset="SOL", origin="user", idempotency_key="race-1")
    engine = lifecycle.default_lifecycle()
    engine.prepare(p.proposal_id)
    outcomes: list[str] = []

    def worker():
        try:
            engine.approve_and_execute(p.proposal_id, approver=approval.PinApprover(PIN))
            outcomes.append("ok")
        except Exception as exc:
            outcomes.append(getattr(exc, "code", type(exc).__name__))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert outcomes.count("ok") == 1, outcomes
    assert wallet_env["rpc"].send_count() == 1


def test_mainnet_is_disabled_by_construction(wallet_env, monkeypatch):
    from core.wallet import config, custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    assert config.network_allowed("solana-mainnet") is False
    assert config.network_allowed("solana-mainnet-beta") is False
    assert config.network_allowed("mainnet") is False
    assert config.network_allowed(DEVNET) is True
    with pytest.raises(WalletFault) as exc:
        custody.create_watch_only_wallet(DESTINATION, network="solana-mainnet")
    assert exc.value.code == "wallet_network_disabled"
    with pytest.raises(WalletFault) as exc2:
        lifecycle.RpcBroadcaster("https://api.mainnet-beta.solana.com", network=DEVNET)
    assert exc2.value.code == "wallet_network_disabled"
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", "https://api.mainnet-beta.solana.com")
    profile = _pocket(custody)
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1, asset="SOL", origin="user")
    with pytest.raises(WalletFault) as exc3:
        lifecycle.default_lifecycle().prepare(p.proposal_id)
    assert exc3.value.code == "wallet_network_disabled"


def test_failed_simulation_stops_before_approval_and_records_the_fault(wallet_env):
    from core.wallet import custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    wallet_env["rpc"].simulate_ok = False
    profile = _pocket(custody)
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1, asset="SOL", origin="user")
    with pytest.raises(WalletFault) as exc:
        lifecycle.default_lifecycle().prepare(p.proposal_id)
    assert exc.value.code == "wallet_simulation_failed"
    assert proposals.get_proposal(p.proposal_id).state == proposals.STATE_FAILED
    assert "wallet_simulation_failed" in fault_codes()


def test_broadcast_failure_is_terminal_failed_with_no_confirmed_receipt(wallet_env):
    from core.wallet import custody, lifecycle, proposals, receipts
    from core.wallet.errors import WalletFault

    wallet_env["rpc"].send_ok = False
    profile = _pocket(custody)
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1, asset="SOL", origin="user")
    with pytest.raises(WalletFault) as exc:
        _drive(lifecycle.default_lifecycle(), p)
    assert exc.value.code == "wallet_broadcast_failed"
    assert proposals.get_proposal(p.proposal_id).state == proposals.STATE_FAILED
    assert all(r["state"] != proposals.STATE_CONFIRMED for r in receipts.list_receipts())


def test_x402_detection_and_capped_approval_flow(wallet_env, monkeypatch):
    from core.wallet import custody, lifecycle, proposals, x402
    from core.wallet.errors import WalletFault

    assert x402.detect_x402(200, {}, {"ok": True}) is None
    assert x402.detect_x402(402, {}, {"error": "nope"}) is None  # no accepts[] -> not a payable request
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "2000")
    request = x402.detect_x402(402, {"Content-Type": "application/json"}, x402_body(amount_minor=1500))
    assert request is not None and request.amount_minor == 1500 and request.pay_to == DESTINATION and request.network == DEVNET
    header_only = x402.detect_x402(402, {"X-Payment-Required": json.dumps(x402_body(amount_minor=1))}, b"")
    assert header_only is not None and header_only.amount_minor == 1

    profile = _pocket(custody)
    proposal = x402.propose_from_x402(request, wallet_id=profile.wallet_id)
    assert proposal.origin == proposals.ORIGIN_X402 and proposal.amount_minor == 1500
    receipt = _drive(lifecycle.default_lifecycle(), proposal)
    assert receipt.state == proposals.STATE_CONFIRMED and receipt.origin == proposals.ORIGIN_X402

    over = x402.detect_x402(402, {}, x402_body(amount_minor=2001))
    with pytest.raises(WalletFault) as exc:
        x402.propose_from_x402(over, wallet_id=profile.wallet_id)
    assert exc.value.code == "wallet_x402_cap_exceeded"
    foreign = x402.detect_x402(402, {}, x402_body(amount_minor=1, network="solana-mainnet"))
    with pytest.raises(WalletFault) as exc2:
        x402.propose_from_x402(foreign, wallet_id=profile.wallet_id)
    assert exc2.value.code == "wallet_network_disabled"
    assert wallet_env["rpc"].send_count() == 1


def test_card_provider_abstraction_stores_tokens_only_and_refuses_pan_or_cvv(wallet_env):
    from core.wallet import cards
    from core.wallet.errors import WalletFault

    token = cards.register_card_token(provider="stripe", token_ref="tok_1Abc", last4="4242", brand="visa", exp_month=12, exp_year=2031)
    assert token.token_ref == "tok_1Abc" and token.last4 == "4242"
    assert cards.list_card_tokens()[0]["token_ref"] == "tok_1Abc"

    for bad in ("4242424242424242", "4242 4242 4242 4242", "378282246310005"):
        with pytest.raises(WalletFault) as exc:
            cards.register_card_token(provider="stripe", token_ref=bad, last4="4242", brand="visa", exp_month=1, exp_year=2030)
        assert exc.value.code == "wallet_card_data_refused"
    with pytest.raises(WalletFault) as exc2:
        cards.register_card_token(provider="stripe", token_ref="tok_x", last4="4242", brand="visa", exp_month=1, exp_year=2030, cvv="123")
    assert exc2.value.code == "wallet_card_data_refused"
    assert cards.looks_like_pan("4242424242424242") and not cards.looks_like_pan("tok_4242424242424242")
    assert "wallet_card_data_refused" in fault_codes()
    assert "SEC_WALLET_CARD_DATA_REFUSED" in sec_codes()
    assert "42424242" not in fault_dumps()


def test_receipts_land_in_the_blackbox_journal_and_effect_ledger_redacted(wallet_env):
    from core.effect_gateway import close_effect_receipt_scope, effect_receipts, open_effect_receipt_scope
    from core.wallet import custody, lifecycle, proposals
    from storage.blackbox.journal import Journal

    profile = _pocket(custody)
    source_context = {"session_id": "sess-wallet", "turn_id": "turn-wallet-1", "request_id": "req-wallet-1"}
    open_effect_receipt_scope(source_context)
    try:
        p = proposals.propose_transaction(
            wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=11, asset="SOL", origin="model", source_context=source_context
        )
        receipt = _drive(lifecycle.default_lifecycle(source_context=source_context), p)
        ledger = [r for r in effect_receipts() if r.get("effect_class") in {lifecycle.EFFECT_CLASS_SIGN, lifecycle.EFFECT_CLASS_TRANSACTION}]
    finally:
        close_effect_receipt_scope()
    assert ledger and ledger[-1]["lifecycle"] == "succeeded"
    entries = [dict(e) for e in Journal(wallet_env["blackbox"]).entries()]
    kinds = [e.get("kind") for e in entries]
    assert "payment_intended" in kinds and "payment_terminal" in kinds
    terminal = [e for e in entries if e.get("kind") == "payment_terminal"][-1]
    assert terminal["tx_signature"] == receipt.tx_signature and terminal["session_id"] == "sess-wallet"
    dumped = json.dumps(entries).lower()
    assert PIN not in dumped and "seed" not in dumped and "phrase" not in dumped
    assert kinds.index("payment_intended") < kinds.index("payment_terminal")


def test_wallet_redaction_strips_every_secret_shaped_field():
    from core.wallet.redaction import redact_wallet_record

    raw = {
        "recovery_phrase": "apple banana", "seed": "00" * 32, "pin": "1234", "private_key": "x", "pan": "4242424242424242",
        "cvv": "123", "card_number": "1", "nested": {"secret": "s", "keep": "passphrase=correct-horse-battery-staple-9"},
        "tx_signature": "5abc", "amount_minor": 3,
    }
    clean = redact_wallet_record(raw)
    assert set(clean) == {"nested", "tx_signature", "amount_minor"}
    assert set(clean["nested"]) == {"keep"} and clean["nested"]["keep"] != "passphrase=correct-horse-battery-staple-9"


def test_wallet_faults_are_declared_in_the_one_catalog_with_security_bijection():
    from core.faults.catalog import all_codes, get_spec
    from core.security_events.catalog import sec_spec_for_fault_code

    codes = set(all_codes())
    expected = {
        "wallet_disabled": False, "wallet_network_disabled": True, "wallet_signing_unavailable": False, "wallet_signature_invalid": True,
        "wallet_confirmation_required": False, "wallet_pin_invalid": False, "wallet_limit_exceeded": True, "wallet_duplicate_payment": True,
        "wallet_approval_rejected": True, "wallet_x402_cap_exceeded": True, "wallet_card_data_refused": True,
        "wallet_simulation_failed": False, "wallet_broadcast_failed": False,
        "wallet_environment_inactive": True, "wallet_amount_invalid": False, "wallet_caller_refused": True,
        "wallet_unlock_throttled": True, "wallet_backup_unavailable": True, "wallet_storage_class_refused": False,
        "wallet_setup_state_invalid": False, "wallet_credential_mismatch": False,
        "wallet_quote_unavailable": False, "wallet_quote_expired": False, "wallet_quote_mismatch": True,
        "wallet_insufficient_funds": False, "wallet_recipient_refused": False,
    }
    for code, security in expected.items():
        assert code in codes, code
        spec = get_spec(code)
        assert spec.authority.startswith("core.wallet"), code
        assert spec.security_relevant is security, code
        if security:
            assert sec_spec_for_fault_code(code).sec_code == "SEC_" + code.upper()


def test_status_surface_is_public_safe_and_counts_pending_approvals(wallet_env):
    from core.wallet import custody, lifecycle, proposals, status

    disabled = status.wallet_status()
    assert disabled["enabled"] is True and disabled["custody_mode"] == "none" and disabled["network"] == DEVNET
    profile = _pocket(custody)
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1, asset="SOL", origin="model")
    lifecycle.default_lifecycle().prepare(p.proposal_id)
    s = status.wallet_status()
    assert s["custody_mode"] == custody.MODE_POCKET_SEALED and s["pending_approvals"] == 1
    assert s["mainnet_enabled"] is False and s["signing_preference"] == custody.MODE_EXTERNAL_SIGNER
    assert s["pending"][0]["proposal_id"] == p.proposal_id and s["pending"][0]["origin"] == "model"
    # the approval METHOD name ("pin" / "password" / "device") is public by design wherever an account is listed
    # (the account list and the Crypto Pilot rows); the guard is for material
    def _without_method(value):
        if isinstance(value, dict):
            return {kk: _without_method(vv) for kk, vv in value.items() if kk != "approval_method"}
        if isinstance(value, list):
            return [_without_method(item) for item in value]
        return value

    material_scan = json.dumps(_without_method(s)).lower()
    assert "public_key" in s and not any(k in material_scan for k in ("seed", "phrase", "private", "pin"))
    assert time.time() - 5 < s["generated_at_epoch"] < time.time() + 5


def test_a_broadcast_signature_survives_free_text_redaction_while_unregistered_key_shapes_do_not(wallet_env):
    # A tx signature is 64 bytes of base58 -- the exact shape of a secret key -- so the runtime's
    # shape-based redactor used to mask it out of chat replies (seen as `[redacted-key]` in the
    # served proof). The wallet publishes every signature it broadcasts; the redactor keeps that
    # exact value readable and still masks every unregistered key-shaped run.
    from core.secret_redaction import contains_secret, redact_secrets
    from core.wallet import approval, custody, lifecycle, proposals

    profile = _pocket(custody)
    proposal = proposals.propose_transaction(
        wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=5_000, asset="SOL", origin=proposals.ORIGIN_USER, memo="redaction"
    )
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    receipt = engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    signature = receipt.tx_signature
    assert signature and len(signature) >= 64

    unregistered = "5" + "K" * 63  # key-shaped, never minted by the wallet
    text = f"paid: tx_signature={signature}; someone pasted {unregistered}"
    redacted = redact_secrets(text)
    assert signature in redacted, redacted
    assert unregistered not in redacted and "[redacted-key]" in redacted
    assert contains_secret(f"tx {signature}") is False
    assert contains_secret(f"key {unregistered}") is True
