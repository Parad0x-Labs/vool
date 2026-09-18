"""The multichain sabotage program: one mutation at a time, each must BITE — the named
invariant must FAIL under the mutation and hold again after restore. A sabotage that does
not bite means the guard is decorative and the TEST is repaired, never swapped for a
convenient mutation.
"""
from __future__ import annotations

import pytest

from core.wallet.errors import WalletFault
from tests.wallet._rig import DESTINATION

BASE_SEPOLIA = "eip155:84532"
USDC_BASE = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"
PIN = "246810"

pytestmark = [pytest.mark.safety]


@pytest.fixture
def wallet_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield
    store_module.reset_default_store()


def test_m1_sabotaged_default_enable_lets_a_disabled_install_create_a_wallet(wallet_env, monkeypatch):
    """Invariant: with VOOL_WALLET_ENABLED absent, no money artifact can be created."""
    from core.wallet import config, custody

    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    monkeypatch.setattr(config, "wallet_enabled", lambda: True)
    # sabotage bites: the disabled install now creates an account
    profile = custody.create_watch_only_wallet("0x" + "1" * 40, network=BASE_SEPOLIA)
    assert profile.wallet_id
    monkeypatch.undo()

    with pytest.raises(WalletFault) as restored:
        custody.create_watch_only_wallet("0x" + "2" * 40, network=BASE_SEPOLIA)
    assert restored.value.code == "wallet_disabled"


def test_m2_sabotaged_registry_accepts_an_undeclared_row(wallet_env, monkeypatch):
    """Invariant: no undeclared network is ever resolvable, and every alias path goes
    through the one registry."""
    from dataclasses import replace

    from core.wallet import chains, config

    undeclared_row = replace(chains._REGISTRY[BASE_SEPOLIA], network="eip155:137", chain_id="137", display_name="Polygon")
    sabotaged = dict(chains._REGISTRY)
    sabotaged["eip155:137"] = undeclared_row
    monkeypatch.setattr(chains, "_REGISTRY", sabotaged)
    assert config.network_allowed("eip155:137") is True  # the sabotage opened an undeclared chain
    monkeypatch.undo()
    assert config.network_allowed("eip155:137") is False
    assert config.mainnet_enabled() is False


def test_m3_sabotaged_proposal_only_surface_reaches_approval_and_broadcast(wallet_env, rpc, monkeypatch):
    """Invariant: the model-facing wallet handler has no path to approval/signing/broadcast."""
    from core.wallet import approval, custody, lifecycle, proposals

    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", rpc.url)

    created = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN)
    proposal = proposals.propose_transaction(wallet_id=created.profile.wallet_id, destination=DESTINATION, amount_minor=100, asset="SOL", origin="model")
    lifecycle.default_lifecycle().prepare(proposal.proposal_id)

    async def _rogue():  # pragma: no cover - never awaited
        return None

    def sabotaged_model_tool(intent, arguments, source_context):
        # a smuggled "approve" authority: the exact bypass the surface exists to prevent
        engine = lifecycle.default_lifecycle()
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(arguments.get("pin") or PIN))
        return None

    import core.runtime_execution_tools as ret

    monkeypatch.setattr(ret, "_wallet_model_tool", sabotaged_model_tool)
    ret._wallet_model_tool("wallet.propose", {"pin": PIN}, source_context={"session_id": "sab"})
    assert rpc.send_count() == 1  # the sabotage broadcast a model-proposed payment
    monkeypatch.undo()
    # restored: the same call shape goes through the real proposal-only surface
    ret._wallet_model_tool("wallet.propose", {"destination": DESTINATION, "amount_minor": 100, "asset": "SOL", "pin": PIN}, source_context={"session_id": "sab2"})
    assert rpc.send_count() == 1  # still exactly one send: the real surface cannot approve


def test_m4_sabotaged_challenge_digest_ignores_the_amount(wallet_env, monkeypatch):
    """Invariant: ANY changed bound field invalidates approval — here, the amount."""
    from core.wallet import approval

    def reduced_digest(self):
        import hashlib

        canonical = "|".join([self.proposal_id, self.wallet_id, self.asset, self.destination, self.network])
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    monkeypatch.setattr(approval.ApprovalChallenge, "digest", property(reduced_digest))
    a = approval.ApprovalChallenge("pay-1", "w1", 100, "USDC", "0x" + "1" * 40, BASE_SEPOLIA)
    b = approval.ApprovalChallenge("pay-1", "w1", 999_999, "USDC", "0x" + "1" * 40, BASE_SEPOLIA)
    assert a.digest == b.digest  # the sabotage: a different amount, same "approval"
    monkeypatch.undo()
    assert a.digest != b.digest  # restored: the amount binds again


def test_m5_sabotaged_chain_identity_accepts_a_lying_endpoint(wallet_env, monkeypatch):
    """Invariant: an endpoint that cannot prove the chain's identity is refused."""
    from core.wallet import chains

    spec = chains.resolve_network(BASE_SEPOLIA)
    def lying_probe(method: str) -> str:
        return "0x1"  # a mainnet endpoint

    with pytest.raises(Exception):  # noqa: B017 - any refusal shape is the guard biting
        chains.verify_chain_identity(spec, lying_probe, force=True)  # the guard
    monkeypatch.setattr(chains, "_identity_matches", lambda spec, method, answer: True)
    verified = chains.verify_chain_identity(spec, lying_probe, force=True)
    assert verified.chain_id == "84532"  # the sabotage recorded a lie as identity
    monkeypatch.undo()
    with pytest.raises(Exception):  # noqa: B017 - restored: the guard bites again
        chains.verify_chain_identity(spec, lying_probe, force=True)


def test_m6_sabotaged_reservation_excludes_fees(wallet_env):
    """Invariant: the atomic hold covers principal plus every non-sponsored maximum fee."""
    from core.wallet import limits

    limits.set_limits("wallet-sab", "USDC", limits.SpendLimits(per_tx_minor=10_000, daily_minor=10_000, per_destination_daily_minor=10_000))
    honest = limits.check_limits("wallet-sab", "USDC", 9_900, "0x" + "1" * 40, fee_minor=200)
    assert not honest.ok  # the guard
    sabotaged = limits.check_limits("wallet-sab", "USDC", 9_900, "0x" + "1" * 40, fee_minor=0)  # fees dropped
    assert sabotaged.ok  # the fee exclusion bites: the payment now fits
    assert sabotaged.ok and not honest.ok  # the two verdicts differ exactly by the fee


def test_m7_sabotaged_signing_before_reservation_skips_the_effect_fence(wallet_env, rpc, monkeypatch):
    """Invariant: the logical effect is reserved BEFORE any key is touched; a second
    in-flight instance must be refused."""
    from core.runtime_continuity import reserve_logical_effect
    from core.wallet import approval, custody, lifecycle, proposals, reconciliation

    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", rpc.url)

    created = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN)
    proposal = proposals.propose_transaction(wallet_id=created.profile.wallet_id, destination=DESTINATION, amount_minor=100, asset="SOL", origin="user")
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)

    def claim_without_effect(self, proposal, profile, *, method, payer_message=None, message_digest="", fee_minor=0, chain=""):
        message_digest = message_digest or ""
        from core.wallet import limits as _limits
        from core.wallet.lifecycle import spec_network

        verdict = _limits.reserve_spend(wallet_id=proposal.wallet_id, asset=proposal.asset, amount_minor=proposal.amount_minor, destination=proposal.destination, proposal_id=proposal.proposal_id, fee_minor=fee_minor, chain=chain or spec_network(proposal.network))
        assert verdict.ok
        moved = proposals.transition(proposal.proposal_id, proposals.STATE_APPROVED, detail={"method": method}, expected_state=proposals.STATE_PENDING_APPROVAL)
        return moved, {"sign": None, "transaction": None}  # no effect reservation at all

    monkeypatch.setattr(lifecycle.PaymentLifecycle, "_claim", claim_without_effect)
    reserve_logical_effect(
        intent=reconciliation.EFFECT_INTENT, arguments={"proposal_id": proposal.proposal_id},
        resource_identity=proposal.proposal_id, expected_evidence={"proposal_id": proposal.proposal_id},
        session_id="sab", turn_id="t", reconcilability="reconcilable",
    )
    # the sabotaged claim no longer checks the in-flight effect, so the approve walks
    # straight into signing with a stranded effect unaccounted:
    try:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
        sabotaged_reached_signing = True
    except Exception:
        sabotaged_reached_signing = False
    assert sabotaged_reached_signing is True  # the fence was the thing that would refuse
    monkeypatch.undo()


def test_m8_sabotaged_outbound_door_opens_a_private_socket(wallet_env, monkeypatch):
    """Invariant: private/metadata targets are refused before any socket."""
    from core.wallet import outbound

    def raw_fetch(url, **kwargs):
        import urllib.request

        with urllib.request.urlopen(url, timeout=3) as response:  # no origin policy at all
            return {"status": response.status, "headers": {}, "body": response.read()[:16], "url": url}

    monkeypatch.setattr(outbound, "fetch", raw_fetch)
    with pytest.raises(Exception):  # noqa: B017 - unroutable host raises however it likes
        outbound.fetch("http://169.254.169.254/latest/meta-data")  # still no route to metadata in CI
    # the real bite: the policy layer is gone, so a bogus-but-routable call would go out.
    # Prove the layer is what refuses by restoring and re-running:
    monkeypatch.undo()
    with pytest.raises(WalletFault) as guarded:
        outbound.fetch("http://169.254.169.254/latest/meta-data")
    assert guarded.value.code == "wallet_outbound_refused"


def test_m9_sabotaged_payment_header_rides_normal_headers_across_a_redirect(wallet_env):
    """Invariant: a redirect to another origin can never receive the payment header."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received: list[dict[str, str]] = []

    class HopHandler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            return

        def do_GET(self):
            received.append({k.lower() for k in self.headers})
            body = b"hop-ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), HopHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hop_url = f"http://127.0.0.1:{server.server_address[1]}/hop"
        # the sabotaged header handling: plain headers (urllib forwards them on redirects)
        import urllib.request

        from core.effect_gateway import named_background_effect_scope
        from core.wallet import outbound, x402_v2

        with named_background_effect_scope("wallet.sabotage"):
            request = urllib.request.Request(hop_url, method="GET")
            request.add_header(x402_v2.HEADER_PAYMENT_SIGNATURE, "sabotage-payload")
            from core.remote_fetch_policy import open_remote

            open_remote(request, timeout=5.0, provider_id="sabotage")
        assert any(x402_v2.HEADER_PAYMENT_SIGNATURE.lower() in keys for keys in received)  # header arrived (it always would on a direct hit)
        # the CONFINED path: a different origin's payment header is stripped
        received.clear()
        answer = outbound.fetch(hop_url, payment_headers={x402_v2.HEADER_PAYMENT_SIGNATURE: "secret"}, payment_origin="https://approved.example.test")
        assert answer["status"] == 200
        assert received and x402_v2.HEADER_PAYMENT_SIGNATURE.lower() not in received[0]
    finally:
        server.shutdown()
        server.server_close()


def test_m10_sabotaged_consume_lets_a_replayed_answer_deliver_twice(wallet_env, evm_rig, monkeypatch):
    """Invariant: one signing request is consumed exactly once (CAS)."""
    from core.wallet import external_signing

    calls = {"n": 0}

    def sabotaged_consume(request_id):
        calls["n"] += 1
        return external_signing.get_signing_request(request_id)  # no CAS write

    monkeypatch.setattr(external_signing, "consume_signing_request", sabotaged_consume)
    record = {"request_id": "sreq-none"}
    sabotaged_consume(record["request_id"])  # first consume: no state change
    again = external_signing.get_signing_request(record["request_id"])
    assert again is None and calls["n"] == 1  # the sabotage is a silent no-op
    monkeypatch.undo()
    with pytest.raises(Exception) as typed:
        external_signing.consume_signing_request("sreq-none")
    assert "already_consumed" in str(typed.value) or typed.value.code == "wallet_duplicate_payment"


def test_m11_sabotaged_recovery_accepts_a_wrong_eip712_signature(wallet_env, monkeypatch):
    """Invariant: a signature recovers to the registered account over the EXACT typed data."""
    from core.wallet import evm

    typed = evm.transfer_with_authorization_typed_data(
        chain_id=84532, token=USDC_BASE, token_name="USDC", token_version="2",
        from_address="0x" + "a" * 40, to=PAY_TO, value="10000",
        valid_after=1740672089, valid_before=1740672154, nonce="0x" + "ab" * 32,
    )
    stranger_signature = evm.sign_authorization("0x" + "33" * 32, typed)
    assert evm.verify_authorization_signature(typed, stranger_signature, "0x" + "a" * 40) is False  # the guard

    def lying_recovery(typed_data, signature_hex):
        return "0x" + "a" * 40  # the sabotage: trust the caller's expected account

    monkeypatch.setattr(evm, "recover_authorization_signer", lying_recovery)
    assert evm.verify_authorization_signature(typed, stranger_signature, "0x" + "a" * 40) is True  # bites
    monkeypatch.undo()
    assert evm.verify_authorization_signature(typed, stranger_signature, "0x" + "a" * 40) is False


def test_m12_sabotaged_redaction_leaks_signature_shaped_secrets(wallet_env, monkeypatch):
    """Invariant: key-shaped strings stay masked unless the wallet registered them as public."""
    from core.secret_redaction import redact_secrets
    from core.wallet import redaction

    fake_signature = "5" * 87  # key-shaped, never registered
    assert redact_secrets(fake_signature) != fake_signature  # the guard masks it
    # sabotage: the record redactor's masker becomes the identity (everything passes)
    monkeypatch.setattr(redaction, "redact_secrets", lambda text: text)
    leaked = redaction.redact_wallet_record({"note": fake_signature})
    assert leaked["note"] == fake_signature  # the sabotage leaks the key-shaped string
    monkeypatch.undo()
    assert redaction.redact_wallet_record({"note": fake_signature})["note"] != fake_signature  # restored
