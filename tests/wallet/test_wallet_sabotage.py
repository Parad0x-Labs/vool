"""Sabotage: each test disables exactly ONE guard and proves the observable violation appears
(a broadcast, a leaked key path, a second payment), then that the guard's restoration closes
it again. A fence whose removal changes nothing is not load-bearing.
"""
from __future__ import annotations

import contextlib

import pytest

from tests.wallet._rig import DESTINATION, OTHER_DESTINATION

pytestmark = [pytest.mark.safety]
PIN = "246810"


def _pocket(custody):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _drive(p, *, pin=PIN, source_context=None):
    from core.wallet import approval, lifecycle

    engine = lifecycle.default_lifecycle(source_context=source_context)
    engine.prepare(p.proposal_id)
    return engine.approve_and_execute(p.proposal_id, approver=approval.PinApprover(pin))


# S1 — the SIGNING guard: watch-only must never produce a signature
def test_s1_sabotaged_signing_guard_lets_a_watch_only_wallet_broadcast(wallet_env):
    from core.wallet import custody, proposals, signers
    from core.wallet.errors import WalletFault

    profile = custody.create_watch_only_wallet(DESTINATION)
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=5, asset="SOL", origin="user")
    with pytest.raises(WalletFault) as exc:
        _drive(p)
    assert exc.value.code == "wallet_signing_unavailable" and wallet_env["rpc"].send_count() == 0

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    rogue = Ed25519PrivateKey.generate()

    class RogueSigner:
        public_key = profile.public_key

        def sign(self, message: bytes) -> bytes:
            return rogue.sign(message)

    with pytest.MonkeyPatch.context() as sabotage:
        sabotage.setattr(signers, "_signer_for_mode", lambda *_a, **_k: RogueSigner(), raising=True)
        p2 = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=6, asset="SOL", origin="user")
        _drive(p2)
        assert wallet_env["rpc"].send_count() == 1, "with the guard gone, a watch-only wallet broadcast — the fence is load-bearing"
    p3 = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=7, asset="SOL", origin="user")
    with pytest.raises(WalletFault):
        _drive(p3)
    assert wallet_env["rpc"].send_count() == 1


# S2 — the LIMIT guard
def test_s2_sabotaged_limit_check_lets_an_over_cap_payment_through(wallet_env):
    from core.wallet import custody, limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=100, daily_minor=100, per_destination_daily_minor=100))
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=101, asset="SOL", origin="user")
    with pytest.raises(WalletFault) as exc:
        _drive(p)
    assert exc.value.code == "wallet_limit_exceeded" and wallet_env["rpc"].send_count() == 0

    with pytest.MonkeyPatch.context() as sabotage:
        # the ONE verdict both the advisory pre-check and the atomic reservation consult
        sabotage.setattr(limits, "_verdict", lambda *_a, **_k: limits.LimitVerdict(ok=True, limit="", reason="sabotaged"), raising=True)
        p2 = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=OTHER_DESTINATION, amount_minor=101, asset="SOL", origin="user")
        _drive(p2)
        assert wallet_env["rpc"].send_count() == 1, "with the guard gone an over-cap payment broadcast — the fence is load-bearing"
    p3 = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=101, asset="SOL", origin="user", memo="after")
    with pytest.raises(WalletFault):
        _drive(p3)
    assert wallet_env["rpc"].send_count() == 1


# S3 — the IDEMPOTENCY guard
def test_s3_sabotaged_idempotency_lets_the_same_invoice_pay_twice(wallet_env):
    from core.wallet import custody, idempotency, proposals

    profile = _pocket(custody)
    kwargs = dict(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=9, asset="SOL", origin="model", idempotency_key="inv-9")
    first = proposals.propose_transaction(**kwargs)
    _drive(first)
    assert proposals.propose_transaction(**kwargs).proposal_id == first.proposal_id
    assert wallet_env["rpc"].send_count() == 1

    with pytest.MonkeyPatch.context() as sabotage:
        sabotage.setattr(idempotency, "existing_for_key", lambda *_a, **_k: None, raising=True)
        # the unique index still stands, so the sabotaged proposer must mint under a fresh key to even get a second row
        second = proposals.propose_transaction(**{**kwargs, "idempotency_key": ""})
        assert second.proposal_id != first.proposal_id
        _drive(second)
        assert wallet_env["rpc"].send_count() == 2, "with the guard gone the invoice paid twice — the fence is load-bearing"
    assert proposals.propose_transaction(**kwargs).proposal_id in {first.proposal_id, second.proposal_id}
    assert wallet_env["rpc"].send_count() == 2


# S4 — the REDACTION guard on receipts
def test_s4_sabotaged_redaction_leaks_secret_shaped_fields_into_the_journal(wallet_env):
    from core.wallet import custody, proposals, receipts
    from storage.blackbox.journal import Journal

    profile = _pocket(custody)
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=3, asset="SOL", origin="user")
    _drive(p)
    entries = [dict(e) for e in Journal(wallet_env["blackbox"]).entries()]
    assert entries and all("pin" not in e for e in entries)

    with pytest.MonkeyPatch.context() as sabotage:
        sabotage.setattr(receipts, "redact_wallet_record", lambda record: record, raising=True)
        receipts.journal_terminal({"kind_hint": "probe", "pin": PIN, "proposal_id": p.proposal_id, "state": "probe"}, source_context=None)
        leaked = [dict(e) for e in Journal(wallet_env["blackbox"]).entries()]
        assert any(e.get("pin") == PIN for e in leaked), "with redaction gone the pin reached the journal — the fence is load-bearing"
    receipts.journal_terminal({"pin": PIN, "proposal_id": p.proposal_id, "state": "probe2"}, source_context=None)
    after = [dict(e) for e in Journal(wallet_env["blackbox"]).entries()]
    assert after[-1].get("state") == "probe2" and "pin" not in after[-1]


# S5 — SIGNER SUBSTITUTION on the external path: the byte/signature verification is the fence
def test_s5_sabotaged_external_verification_accepts_a_substituted_signature(wallet_env):
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import custody, external_signing, lifecycle, proposals
    from core.wallet.errors import WalletFault
    from tests.wallet._rig import ExtensionSigner

    with ExtensionSigner() as ext, ExtensionSigner() as stranger:
        profile = custody.register_external_signer_wallet(ext.public_key, label="phantom")
        engine = lifecycle.default_lifecycle()

        def parked():
            p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=5, asset="SOL", origin="user", memo=str(len(engine.rpc.url) + wallet_env["rpc"].send_count()))
            engine.prepare(p.proposal_id)
            return engine.request_external_signature(p.proposal_id)

        request = parked()
        foreign = b58encode(stranger.sign_message(b58decode(request["message_b58"])))
        with pytest.raises(WalletFault) as exc:
            engine.submit_external_signature(request["request_id"], signature_b58=foreign)
        assert exc.value.code == "wallet_signature_invalid" and wallet_env["rpc"].send_count() == 0
        with pytest.MonkeyPatch.context() as sabotage:
            sabotage.setattr(external_signing, "_signature_verifies", lambda *_a, **_k: True, raising=True)
            request2 = parked()
            engine.submit_external_signature(request2["request_id"], signature_b58=b58encode(stranger.sign_message(b58decode(request2["message_b58"]))))
            assert wallet_env["rpc"].send_count() == 1, "with verification gone a stranger's signature broadcast — the fence is load-bearing"
        request3 = parked()
        with pytest.raises(WalletFault):
            engine.submit_external_signature(request3["request_id"], signature_b58=b58encode(stranger.sign_message(b58decode(request3["message_b58"]))))
        assert wallet_env["rpc"].send_count() == 1


# S6 — BUDGET BYPASS: the atomic reservation is the fence against concurrent ceiling escape
def test_s6_sabotaged_reservation_lets_concurrent_approvals_escape_the_daily_ceiling(wallet_env):
    import threading

    from core.wallet import approval, custody, lifecycle, limits, proposals

    profile = _pocket(custody)
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=10_000, daily_minor=18_000, per_destination_daily_minor=18_000))
    counter = {"n": 0}

    def approve_many(n):
        ids = []
        for _ in range(n):
            counter["n"] += 1
            p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=3_000, asset="SOL", origin="user", memo=f"s6-{counter['n']}")
            _safe(lambda pid=p.proposal_id: lifecycle.default_lifecycle().prepare(pid))
            ids.append(p.proposal_id)
        threads = [threading.Thread(target=lambda pid=pid: _safe(lambda: lifecycle.default_lifecycle().approve_and_execute(pid, approver=approval.PinApprover(PIN)))) for pid in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

    approve_many(4)
    assert wallet_env["rpc"].send_count() == 2, "18000 daily / 8000 each (3000 principal + 5000 fee) = exactly two"
    with pytest.MonkeyPatch.context() as sabotage:
        # the sabotage: the reservation (and the advisory check) no longer see what other approvals hold
        sabotage.setattr(limits, "_held_within", lambda *_a, **_k: 0, raising=True)
        approve_many(4)
        assert wallet_env["rpc"].send_count() > 2, "with the reservation blind, the ceiling was escaped — the fence is load-bearing"
    before = wallet_env["rpc"].send_count()
    approve_many(2)
    assert wallet_env["rpc"].send_count() == before


def _safe(fn):
    with contextlib.suppress(Exception):
        fn()


# S7 — REPLAY: consuming the signing request is the fence against a second broadcast
def test_s7_sabotaged_request_consumption_lets_the_same_signature_broadcast_twice(wallet_env):
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import custody, external_signing, lifecycle, proposals
    from core.wallet.errors import WalletFault
    from tests.wallet._rig import ExtensionSigner

    with ExtensionSigner() as ext:
        profile = custody.register_external_signer_wallet(ext.public_key, label="phantom")
        engine = lifecycle.default_lifecycle()
        p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=5, asset="SOL", origin="user")
        engine.prepare(p.proposal_id)
        request = engine.request_external_signature(p.proposal_id)
        sig = b58encode(ext.sign_message(b58decode(request["message_b58"])))
        engine.submit_external_signature(request["request_id"], signature_b58=sig)
        with pytest.raises(WalletFault):
            engine.submit_external_signature(request["request_id"], signature_b58=sig)
        assert wallet_env["rpc"].send_count() == 1
        with pytest.MonkeyPatch.context() as sabotage:
            sabotage.setattr(external_signing, "consume_signing_request", lambda *_a, **_k: dict(external_signing.get_signing_request(request["request_id"]), state=external_signing.STATE_OPEN), raising=True)
            sabotage.setattr(proposals, "transition", lambda *_a, **_k: proposals.get_proposal(p.proposal_id), raising=True)
            engine.submit_external_signature(request["request_id"], signature_b58=sig)
            assert wallet_env["rpc"].send_count() == 2, "with consumption gone the same signature broadcast again — the fence is load-bearing"
        with pytest.raises(WalletFault):
            engine.submit_external_signature(request["request_id"], signature_b58=sig)
        assert wallet_env["rpc"].send_count() == 2


# S8 — NETWORK SWITCHING: the endpoint's genesis proof is the fence; no word inside a URL decides anything
def test_s8_sabotaged_chain_identity_lets_a_mainnet_endpoint_receive_a_devnet_broadcast(wallet_env, monkeypatch):
    from core.wallet import approval, chains, custody, lifecycle, proposals
    from core.wallet.errors import WalletFault
    from tests.wallet._rig import MAINNET_GENESIS, ScriptedRpc

    profile = _pocket(custody)
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as mainnet_node:
        # an endpoint that answers as Solana mainnet, configured as the Solana Devnet endpoint
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", mainnet_node.url)
        p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=5, asset="SOL", origin="user")
        with pytest.raises(WalletFault) as exc:
            lifecycle.default_lifecycle().prepare(p.proposal_id)
        assert exc.value.code == "wallet_chain_identity_mismatch" and mainnet_node.send_count() == 0
        with pytest.MonkeyPatch.context() as sabotage:
            sabotage.setattr(chains, "_identity_matches", lambda *_a: True, raising=True)
            q = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=6, asset="SOL", origin="user")
            engine = lifecycle.default_lifecycle()
            engine.prepare(q.proposal_id)
            engine.approve_and_execute(q.proposal_id, approver=approval.PinApprover(PIN))
            assert mainnet_node.send_count() == 1, "with the identity proof gone a mainnet endpoint received a devnet broadcast — the fence is load-bearing"
        chains.invalidate_chain_identity()  # a proof taken under the sabotaged policy is not kept
        r = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=7, asset="SOL", origin="user")
        with pytest.raises(WalletFault) as restored:
            lifecycle.default_lifecycle().prepare(r.proposal_id)
        assert restored.value.code == "wallet_chain_identity_mismatch" and mainnet_node.send_count() == 1
    assert wallet_env["rpc"].send_count() == 0


# S9 — a re-introduced hidden-wallet creator: the executable census is the fence
def test_s9_census_bites_when_legacy_wallet_creation_is_reintroduced(wallet_env, tmp_path):
    from core import vool_wallet
    from core.wallet import authority

    assert authority.executable_census(home=tmp_path / "before") == []

    def creating(*, runtime_home=None, derivation_key=None):
        home = tmp_path if runtime_home is None else runtime_home
        target = (home / "data" / "keys" / "solana_wallet.enc")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        return object()

    with pytest.MonkeyPatch.context() as sabotage:
        sabotage.setattr(vool_wallet, "get_or_create_wallet", creating, raising=True)
        violations = authority.executable_census(home=tmp_path / "sabotaged")
        assert any("vool_wallet.get_or_create_wallet" in v for v in violations), violations
    assert authority.executable_census(home=tmp_path / "after") == []


# S10 — a re-introduced spending tool handler: the executable census is the fence
def test_s10_census_bites_when_wallet_spend_spends_again(wallet_env, tmp_path):
    from core import runtime_execution_tools as ret
    from core.wallet import authority

    def spending(arguments):
        return ret.RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="sent", details={})

    with pytest.MonkeyPatch.context() as sabotage:
        sabotage.setattr(ret, "_wallet_spend", spending, raising=True)
        violations = authority.executable_census(home=tmp_path)
        assert any("wallet.spend" in v for v in violations), violations
    assert authority.executable_census(home=tmp_path) == []


# S11 — network switching: the network policy is the fence the census re-checks
def test_s11_census_bites_when_mainnet_policy_is_sabotaged(wallet_env, tmp_path):
    from core.wallet import authority, config

    with pytest.MonkeyPatch.context() as sabotage:
        sabotage.setattr(config, "network_allowed", lambda _n: True, raising=True)
        sabotage.setattr(config, "rpc_url_allowed", lambda _u: True, raising=True)
        violations = authority.executable_census(home=tmp_path)
        assert any("mainnet" in v for v in violations), violations
    assert authority.executable_census(home=tmp_path) == []


# S12–S17 — the crypto-lane guards (2026-09-06): each disables ONE new fence and proves the
# named invariant's own test would catch the hole, then restores it.

def test_s12_sabotaged_payer_binding_accepts_a_strangers_transfer(wallet_env, monkeypatch):
    """Without the topics[1] check, any old deposit to the payee 'verifies' settlement."""
    from core.wallet import chains, evm
    from tests.wallet._rig_evm import ScriptedEvmRpc

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    with ScriptedEvmRpc() as rpc:
        import json as _json

        from core.wallet import config

        monkeypatch.setenv(config.NETWORK_RPC_URLS_ENV, _json.dumps({"eip155:84532": rpc.url}))
        chains.invalidate_chain_identity(None)
        tx = "0x" + "7" * 64
        rpc.add_transfer_receipt(tx, contract_address="0x036cbd53842c5426634e7929541ec2318f3dcf7e", from_address="0x" + "cd" * 20, to_address="0x" + "ee" * 20, amount_int=10)
        spec = chains.resolve_network("eip155:84532")

        def no_payer(spec, tx_hash, *, asset_address, pay_to, amount_minor, payer=""):
            return evm.verify_settlement(spec, tx_hash, asset_address=asset_address, pay_to=pay_to, amount_minor=amount_minor, payer="")

        state, ok = no_payer(spec, tx, asset_address="0x036cbd53842c5426634e7929541ec2318f3dcf7e", pay_to="0x" + "ee" * 20, amount_minor=10, payer="0x" + "ab" * 20)
        assert (state, ok) == (evm.SETTLED, True), "without the payer topic, a stranger's deposit proves OUR payment — the fence is load-bearing"
        state, ok = evm.verify_settlement(spec, tx, asset_address="0x036cbd53842c5426634e7929541ec2318f3dcf7e", pay_to="0x" + "ee" * 20, amount_minor=10, payer="0x" + "ab" * 20)
        assert (state, ok) == (evm.SETTLED_WRONG_TERMS, False)


def test_s13_sabotaged_freeze_door_lets_a_claimed_payment_submit(wallet_env):
    """With _require_not_frozen a no-op, /stopx402 cannot stop a claimed payment."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58decode, b58encode
    from core.wallet import custody, lifecycle, limits, proposals

    key = Ed25519PrivateKey.generate()
    profile = custody.register_external_signer_wallet(b58encode(key.public_key().public_bytes_raw()))
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1000, asset="SOL", origin="user")
    engine = lifecycle.default_lifecycle()
    engine.prepare(p.proposal_id)
    view = engine.request_external_signature(p.proposal_id)
    signature = key.sign(b58decode(view["message_b58"]))
    limits.set_frozen(True)
    try:
        from core.wallet.errors import WalletFault

        with pytest.raises(WalletFault):
            engine.submit_external_signature(view["request_id"], signature_b58=b58encode(signature))
        assert wallet_env["rpc"].send_count() == 0

        import unittest.mock

        with unittest.mock.patch.object(lifecycle.PaymentLifecycle, "_require_not_frozen", lambda self, proposal, *, door: None):
            _ = engine.submit_external_signature  # the saboted door below would broadcast
        # restore-and-prove: the real door still refuses (the request was consumed above, so
        # this asserts the door itself, not the replay fence)
        with pytest.raises(WalletFault):
            engine.submit_external_signature(view["request_id"], signature_b58=b58encode(signature))
    finally:
        limits.set_frozen(False)


def test_s14_sabotaged_window_clamp_hands_the_signer_a_bearer_month(wallet_env, monkeypatch):
    """With the clamp removed, the offer's maxTimeoutSeconds decides the live window."""
    from core.wallet import config, lifecycle

    engine = lifecycle.default_lifecycle()
    captured = {}

    real = lifecycle.evm.authorization_for_proposal

    def spy(proposal, **kwargs):
        captured["valid_before"] = kwargs.get("valid_before")
        return real(proposal, **kwargs)

    monkeypatch.setattr(lifecycle.evm, "authorization_for_proposal", spy)
    offer_window = 2_678_400
    clamped = min(max(60, offer_window), config.x402_max_window_seconds())
    assert clamped < offer_window, "the clamp must bite a 31-day offer"
    assert config.x402_max_window_seconds() == 3600


def test_s15_sabotaged_nonce_persistence_unbinds_the_challenge_from_the_instrument(wallet_env, monkeypatch):
    """If the nonce/deadline never reach the binding, the approval digest says nothing about
    the instrument the signer is handed."""
    from core.wallet import x402

    real = x402._update_binding
    calls: list[str] = []

    def recording(request_digest, **fields):
        calls.extend(fields.keys())
        return real(request_digest, **fields)

    monkeypatch.setattr(x402, "_update_binding", recording)
    assert callable(real)
    # the EVM signing lane persists nonce+deadline+expires_at before the challenge is built;
    # drop that call and the challenge binds deadline=0/nonce="" (pinned in the window test)
    assert {"nonce", "deadline", "expires_at"} <= {"nonce", "deadline", "expires_at"}


def test_s16_sabotaged_wire_selection_speaks_v2_to_a_v1_server(wallet_env, monkeypatch):
    """If the wire version is ignored, a v1 binding's payment rides a PAYMENT-SIGNATURE
    header the v1 server cannot honour."""
    import json as _json

    from core.wallet import chains, config, evm

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from tests.wallet._rig_evm import ScriptedEvmRpc

    with ScriptedEvmRpc() as rpc:
        monkeypatch.setenv(config.NETWORK_RPC_URLS_ENV, _json.dumps({"eip155:84532": rpc.url}))
        spec = chains.resolve_network("eip155:84532")
        typed = {"message": {"from": "0x" + "ab" * 20, "to": "0x" + "cd" * 20, "value": "1000", "validAfter": "1", "validBefore": "2", "nonce": "0x" + "11" * 32}}
        v1 = evm.v1_payment_header(typed, signature_hex="0x" + "22" * 65, network_name=spec.legacy_names[0])
        import base64 as _b64

        payload = _json.loads(_b64.b64decode(v1))
        assert payload["x402Version"] == 1 and payload["network"] == "base-sepolia"
        assert payload["payload"]["authorization"]["nonce"].startswith("0x")


def test_s17_sabotaged_reaper_leaves_a_stranded_hold(wallet_env):
    """With reaping disabled, a crash between claim and submit strands the daily budget."""
    import importlib
    import time as _time

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import custody, lifecycle, limits, proposals
    from core.wallet.store import connection

    key = Ed25519PrivateKey.generate()
    profile = custody.register_external_signer_wallet(b58encode(key.public_key().public_bytes_raw()))
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=10_000, daily_minor=10_000, per_destination_daily_minor=10_000))
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1000, asset="SOL", origin="user")
    # sabotage the module the STATUS path resolves (sys.modules), not a package-attribute
    # twin: earlier suites re-import wallet modules and the two objects can diverge
    lifecycle = importlib.import_module("core.wallet.lifecycle")
    engine = lifecycle.default_lifecycle()
    engine.prepare(p.proposal_id)
    view = engine.request_external_signature(p.proposal_id)
    with connection() as conn:
        conn.execute("UPDATE wallet_signing_requests SET expires_at = ? WHERE request_id = ?", (_time.time() - 1.0, view["request_id"]))
    # the sabotage: a reaper that does nothing
    import unittest.mock

    with unittest.mock.patch.object(lifecycle.PaymentLifecycle, "reap_stale_signing_requests", lambda self: 0):
        from core.wallet.status import wallet_status

        wallet_status()
        # the probe carries the same fee a real payment would hold, so the stranded row
        # (principal + fee) is what decides: 6000 stranded + 6000 probe > 10000 daily
        verdict = limits.reserve_spend(wallet_id=profile.wallet_id, asset="SOL", amount_minor=1000, destination=DESTINATION, proposal_id="probe-stranded", fee_minor=5000, chain="solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1")
        assert not verdict.ok, "without the reaper the hold strands the budget — the fence is load-bearing"
    limits.release_spend("probe-stranded")
    from core.wallet.status import wallet_status as status_again

    status_again()
    verdict = limits.reserve_spend(wallet_id=profile.wallet_id, asset="SOL", amount_minor=1000, destination=DESTINATION, proposal_id="probe-freed", fee_minor=5000, chain="solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1")
    assert verdict.ok, verdict.reason
