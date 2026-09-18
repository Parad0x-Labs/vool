"""External-signer transport contract: VOOL builds the exact bytes, hands out ONE bound signing
request, the wallet signs OUTSIDE the process, and VOOL accepts nothing but a valid signature
over those exact bytes. Both documented transports ride one submit door: Phantom's injected
request({signTransaction}) -> base58 signature, and WalletConnect's solana_signTransaction ->
signature (+ optional base64 transaction). Replays and substituted bytes are refused.
"""
from __future__ import annotations

import base64
import json

import pytest

from tests.wallet._rig import DESTINATION, DEVNET, ExtensionSigner

pytestmark = [pytest.mark.safety]


@pytest.fixture
def ext():
    with ExtensionSigner() as signer:
        yield signer


def _external_wallet(custody, ext):
    return custody.register_external_signer_wallet(ext.public_key, network=DEVNET, label="phantom")


def _proposal(proposals, lifecycle, wallet_id, amount=1000, **kw):
    p = proposals.propose_transaction(wallet_id=wallet_id, destination=DESTINATION, amount_minor=amount, asset="SOL", origin="user", **kw)
    lifecycle.default_lifecycle().prepare(p.proposal_id)
    return p


def test_signing_request_carries_exact_bytes_for_both_transports_and_no_key_material(wallet_env, ext):
    from core.vool_wallet import b58decode
    from core.wallet import custody, external_signing, lifecycle, proposals

    profile = _external_wallet(custody, ext)
    p = _proposal(proposals, lifecycle, profile.wallet_id)
    request = lifecycle.default_lifecycle().request_external_signature(p.proposal_id)
    assert request["proposal_id"] == p.proposal_id and request["public_key"] == ext.public_key and request["network"] == DEVNET
    message = base64.b64decode(request["message_b64"])
    assert b58decode(request["message_b58"]) == message
    tx = base64.b64decode(request["unsigned_transaction_b64"])
    assert message in tx and len(tx) == 1 + 64 + len(message)  # one zero signature slot + the exact message
    assert request["transports"]["phantom_injected"] == {"method": "signTransaction", "params": {"message": request["message_b58"]}}
    assert request["transports"]["walletconnect"] == {"method": "solana_signTransaction", "params": {"transaction": request["unsigned_transaction_b64"]}}
    assert not any(k in json.dumps(request).lower() for k in ("seed", "secret", "private", "phrase", "pin"))
    assert proposals.get_proposal(p.proposal_id).state == proposals.STATE_AWAITING_SIGNATURE
    stored = external_signing.get_signing_request(request["request_id"])
    assert stored["state"] == external_signing.STATE_OPEN


def test_phantom_path_signature_over_exact_message_broadcasts_once(wallet_env, ext):
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import custody, lifecycle, proposals

    profile = _external_wallet(custody, ext)
    p = _proposal(proposals, lifecycle, profile.wallet_id)
    engine = lifecycle.default_lifecycle()
    request = engine.request_external_signature(p.proposal_id)
    signature = ext.sign_message(b58decode(request["message_b58"]))  # what the extension does with request()
    receipt = engine.submit_external_signature(request["request_id"], signature_b58=b58encode(signature))
    assert receipt.state == proposals.STATE_CONFIRMED and wallet_env["rpc"].send_count() == 1
    sent = wallet_env["rpc"].sent[0]
    assert sent[1:65] == signature and sent[65:] == b58decode(request["message_b58"])  # the broadcast bytes are the exact message + that signature


def test_walletconnect_path_signed_transaction_bytes_must_equal_the_request(wallet_env, ext):
    from solders.signature import Signature
    from solders.transaction import Transaction

    from core.wallet import custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = _external_wallet(custody, ext)
    p = _proposal(proposals, lifecycle, profile.wallet_id)
    engine = lifecycle.default_lifecycle()
    request = engine.request_external_signature(p.proposal_id)
    tx = Transaction.from_bytes(base64.b64decode(request["unsigned_transaction_b64"]))
    message = bytes(tx.message)
    good = Transaction.populate(tx.message, [Signature.from_bytes(ext.sign_message(message))])
    # substituted bytes: a different amount signed by the same key, presented as the answer
    other = _proposal(proposals, lifecycle, profile.wallet_id, amount=999_999, memo="swap")
    other_request = engine.request_external_signature(other.proposal_id)
    other_tx = Transaction.from_bytes(base64.b64decode(other_request["unsigned_transaction_b64"]))
    swapped = Transaction.populate(other_tx.message, [Signature.from_bytes(ext.sign_message(bytes(other_tx.message)))])
    with pytest.raises(WalletFault) as exc:
        engine.submit_external_signature(request["request_id"], signed_transaction_b64=base64.b64encode(bytes(swapped)).decode())
    assert exc.value.code == "wallet_signature_invalid" and exc.value.context["reason"] == "transaction_bytes_mismatch"
    assert wallet_env["rpc"].send_count() == 0
    receipt = engine.submit_external_signature(request["request_id"], signed_transaction_b64=base64.b64encode(bytes(good)).decode())
    assert receipt.state == proposals.STATE_CONFIRMED and wallet_env["rpc"].send_count() == 1


def test_forged_or_foreign_key_signature_is_refused_before_broadcast(wallet_env, ext):
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = _external_wallet(custody, ext)
    p = _proposal(proposals, lifecycle, profile.wallet_id)
    engine = lifecycle.default_lifecycle()
    request = engine.request_external_signature(p.proposal_id)
    with ExtensionSigner() as stranger:
        foreign = stranger.sign_message(b58decode(request["message_b58"]))
    with pytest.raises(WalletFault) as exc:
        engine.submit_external_signature(request["request_id"], signature_b58=b58encode(foreign))
    assert exc.value.code == "wallet_signature_invalid"
    ext.tamper = True
    with pytest.raises(WalletFault) as exc2:
        engine.submit_external_signature(request["request_id"], signature_b58=b58encode(ext.sign_message(b"tampered" + b58decode(request["message_b58"])[8:])))
    assert exc2.value.code == "wallet_signature_invalid"
    assert wallet_env["rpc"].send_count() == 0
    assert proposals.get_proposal(p.proposal_id).state == proposals.STATE_AWAITING_SIGNATURE  # a bad answer does not burn the proposal


def test_replayed_signing_request_never_broadcasts_twice(wallet_env, ext):
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = _external_wallet(custody, ext)
    p = _proposal(proposals, lifecycle, profile.wallet_id)
    engine = lifecycle.default_lifecycle()
    request = engine.request_external_signature(p.proposal_id)
    sig = b58encode(ext.sign_message(b58decode(request["message_b58"])))
    engine.submit_external_signature(request["request_id"], signature_b58=sig)
    with pytest.raises(WalletFault) as exc:
        engine.submit_external_signature(request["request_id"], signature_b58=sig)
    assert exc.value.code == "wallet_duplicate_payment"
    with pytest.raises(WalletFault) as exc2:
        engine.request_external_signature(p.proposal_id)
    assert exc2.value.code == "wallet_duplicate_payment"
    assert wallet_env["rpc"].send_count() == 1


def test_signing_requests_expire_and_a_pocket_wallet_never_gets_one(wallet_env, ext):
    from core.wallet import custody, external_signing, lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = _external_wallet(custody, ext)
    p = _proposal(proposals, lifecycle, profile.wallet_id)
    engine = lifecycle.default_lifecycle()
    request = engine.request_external_signature(p.proposal_id)
    real_now = external_signing._now
    with pytest.MonkeyPatch.context() as clock:
        clock.setattr(external_signing, "_now", lambda: real_now() + external_signing.REQUEST_TTL_SECONDS + 1)
        with pytest.raises(WalletFault) as exc:
            engine.submit_external_signature(request["request_id"], signature_b58="1" * 88)
    assert exc.value.code == "wallet_approval_rejected" and exc.value.context["reason"] == "signing_request_expired"
    pocket = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="246810").profile
    q = _proposal(proposals, lifecycle, pocket.wallet_id)
    with pytest.raises(WalletFault) as exc2:
        engine.request_external_signature(q.proposal_id)
    assert exc2.value.code == "wallet_signing_unavailable"


def test_models_and_plugins_have_no_signer_callback_surface():
    """The only signer callbacks are internal to core.wallet; no tool contract, plugin hook or
    API body can carry one. The external path is a data contract (bytes out, signature in)."""
    import inspect

    from core.wallet import lifecycle, proposals

    assert "external_signature_provider" not in inspect.signature(proposals.propose_transaction).parameters
    assert "external_signature_provider" not in inspect.signature(lifecycle.PaymentLifecycle.request_external_signature).parameters
    assert "external_signature_provider" not in inspect.signature(lifecycle.PaymentLifecycle.submit_external_signature).parameters
    from core.web.api import wallet_api

    source = inspect.getsource(wallet_api)
    assert "external_signature_provider" not in source and "signer_for" not in source
