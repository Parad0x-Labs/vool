"""Freeze reach, owner rejection after claim, and the stale-signing-request reaper.

RED at the 1d610653 base: the panic freeze only gated NEW claims, so an already-claimed
payment sailed through ``submit_external_signature``/``_broadcast`` inside the 600 s TTL;
``/api/wallet/reject`` pinned ``pending_approval`` so a claimed proposal could not be
cancelled by its owner; and nothing applied the TTL after a restart, stranding holds.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import pytest

from tests.asgi_harness import asgi_request
from tests.wallet._rig import DESTINATION, DEVNET

pytestmark = [pytest.mark.safety]

HEADERS = {"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}


@pytest.fixture
def app(wallet_env):
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    return create_app(RuntimeServices(display_name="VOOL"))


def _post(app, path: str, body: dict[str, Any]):
    status, _h, raw = asgi_request(app, method="POST", path=path, headers=HEADERS, body=json.dumps(body).encode())
    return status, json.loads(raw or b"{}")


def _get(app, path: str):
    status, _h, raw = asgi_request(app, method="GET", path=path, headers={"Host": "127.0.0.1"})
    return status, json.loads(raw or b"{}")


def _external_lane(app, wallet_env, *, amount_minor: int = 1000):
    """Register an external signer, propose, prepare, open the signing request. Returns
    (engine, wallet profile, proposal, request record, signing key)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import custody, lifecycle, proposals

    key = Ed25519PrivateKey.generate()
    public_key = b58encode(key.public_key().public_bytes_raw())
    _status, registered = _post(app, "/api/wallet/external", {"public_key": public_key, "network": DEVNET, "label": "ext"})
    assert registered["ok"], registered
    wallet_id = registered["wallet"]["wallet_id"]
    _status, proposed = _post(app, "/api/wallet/propose", {"wallet_id": wallet_id, "network": DEVNET, "destination": DESTINATION, "amount_minor": amount_minor, "asset": "SOL", "memo": "freeze test"})
    assert proposed["ok"], proposed
    engine = lifecycle.default_lifecycle()
    prepared = engine.prepare(proposed["proposal"]["proposal_id"])
    view = engine.request_external_signature(prepared.proposal_id)
    record = engine.rpc  # placeholder replaced below
    return engine, wallet_id, prepared, view, key


def test_freeze_blocks_a_submitted_signature_after_claim(app, wallet_env):
    """/stopx402 freezes spending: an already-claimed payment inside its signing-request TTL
    must NOT escape when the signature is submitted while frozen."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58decode, b58encode
    from core.wallet import custody, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    key = Ed25519PrivateKey.generate()
    public_key = b58encode(key.public_key().public_bytes_raw())
    _status, registered = _post(app, "/api/wallet/external", {"public_key": public_key, "network": DEVNET, "label": "ext"})
    wallet_id = registered["wallet"]["wallet_id"]
    _status, proposed = _post(app, "/api/wallet/propose", {"wallet_id": wallet_id, "network": DEVNET, "destination": DESTINATION, "amount_minor": 1000, "asset": "SOL"})
    engine = lifecycle.default_lifecycle()
    prepared = engine.prepare(proposed["proposal"]["proposal_id"])
    view = engine.request_external_signature(prepared.proposal_id)
    assert wallet_env["rpc"].send_count() == 0, "nothing left the machine at claim"

    limits.set_frozen(True)
    try:
        signature = key.sign(base64.b64decode(view["message_b64"]))
        with pytest.raises(WalletFault) as exc:
            engine.submit_external_signature(view["request_id"], signature_b58=b58encode(signature))
        assert exc.value.code == "wallet_limit_exceeded" and exc.value.context.get("limit") == "frozen"
        assert wallet_env["rpc"].send_count() == 0, "the frozen door must not broadcast"
        assert proposals.get_proposal(prepared.proposal_id).state == proposals.STATE_AWAITING_SIGNATURE
    finally:
        limits.set_frozen(False)
    # freeze is a brake, not a kill: unfrozen, the same request still completes lawfully
    receipt = engine.submit_external_signature(view["request_id"], signature_b58=b58encode(signature))
    assert receipt.state in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST}


def test_freeze_blocks_the_pocket_lane_before_any_signing(app, wallet_env):
    from core.wallet import approval, custody, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="135790").profile
    _status, proposed = _post(app, "/api/wallet/propose", {"wallet_id": profile.wallet_id, "network": DEVNET, "destination": DESTINATION, "amount_minor": 1000, "asset": "SOL"})
    engine = lifecycle.default_lifecycle()
    prepared = engine.prepare(proposed["proposal"]["proposal_id"])
    limits.set_frozen(True)
    try:
        with pytest.raises(WalletFault) as exc:
            engine.approve_and_execute(prepared.proposal_id, approver=approval.PinApprover("135790"))
        assert exc.value.code == "wallet_limit_exceeded" and exc.value.context.get("limit") == "frozen"
        assert wallet_env["rpc"].send_count() == 0
    finally:
        limits.set_frozen(False)


def test_owner_rejection_after_claim_expires_the_request_and_releases_the_hold(app, wallet_env):
    """Reject is the owner's cancellation: after claim (awaiting_signature) it must expire the
    open signing request, release the reserved amount and leave a visible rejected trail."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import external_signing, lifecycle, limits, proposals

    key = Ed25519PrivateKey.generate()
    public_key = b58encode(key.public_key().public_bytes_raw())
    _status, registered = _post(app, "/api/wallet/external", {"public_key": public_key, "network": DEVNET, "label": "ext"})
    wallet_id = registered["wallet"]["wallet_id"]
    _status, proposed = _post(app, "/api/wallet/propose", {"wallet_id": wallet_id, "network": DEVNET, "destination": DESTINATION, "amount_minor": 1000, "asset": "SOL"})
    engine = lifecycle.default_lifecycle()
    prepared = engine.prepare(proposed["proposal"]["proposal_id"])
    view = engine.request_external_signature(prepared.proposal_id)

    # shrink the daily window to just above this payment: only a RELEASED hold leaves room
    limits.set_limits(wallet_id, "SOL", limits.SpendLimits(per_tx_minor=2000, daily_minor=2000, per_destination_daily_minor=2000))
    _status, rejected = _post(app, "/api/wallet/reject", {"proposal_id": prepared.proposal_id})
    assert rejected["ok"] and rejected["rejected"] is True, rejected
    record = external_signing.get_signing_request(view["request_id"])
    assert record["state"] == external_signing.STATE_EXPIRED
    assert proposals.get_proposal(prepared.proposal_id).state == proposals.STATE_REJECTED
    # the hold is gone: a fresh reservation of the same size fits the shrunken window
    verdict = limits.reserve_spend(wallet_id=wallet_id, asset="SOL", amount_minor=1000, destination=DESTINATION, proposal_id="probe-after-reject", chain=DEVNET)
    assert verdict.ok, verdict.reason
    limits.release_spend("probe-after-reject")
    assert wallet_env["rpc"].send_count() == 0


def test_the_reaper_expires_stale_requests_after_a_restart_and_frees_the_budget(app, wallet_env):
    """A crash between claim and submit leaves an open request past its TTL. The reaper (run
    from the status read the UI already polls) expires it, releases the hold and terminally
    marks the proposal — no operator action required."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import external_signing, lifecycle, limits, proposals
    from core.wallet.store import connection

    key = Ed25519PrivateKey.generate()
    public_key = b58encode(key.public_key().public_bytes_raw())
    _status, registered = _post(app, "/api/wallet/external", {"public_key": public_key, "network": DEVNET, "label": "ext"})
    wallet_id = registered["wallet"]["wallet_id"]
    _status, proposed = _post(app, "/api/wallet/propose", {"wallet_id": wallet_id, "network": DEVNET, "destination": DESTINATION, "amount_minor": 1000, "asset": "SOL"})
    engine = lifecycle.default_lifecycle()
    prepared = engine.prepare(proposed["proposal"]["proposal_id"])
    view = engine.request_external_signature(prepared.proposal_id)

    # simulate the restart that never applied the TTL: age the request past its window
    with connection() as conn:
        conn.execute("UPDATE wallet_signing_requests SET expires_at = ? WHERE request_id = ?", (time.time() - 1.0, view["request_id"]))

    limits.set_limits(wallet_id, "SOL", limits.SpendLimits(per_tx_minor=2000, daily_minor=2000, per_destination_daily_minor=2000))
    status, body = _get(app, "/api/wallet/status")
    assert status == 200 and body["ok"]
    record = external_signing.get_signing_request(view["request_id"])
    assert record["state"] == external_signing.STATE_EXPIRED
    proposal = proposals.get_proposal(prepared.proposal_id)
    assert proposal.state == proposals.STATE_EXPIRED
    verdict = limits.reserve_spend(wallet_id=wallet_id, asset="SOL", amount_minor=1000, destination=DESTINATION, proposal_id="probe-after-reap", chain=DEVNET)
    assert verdict.ok, "the reaper must release the stranded hold"
    limits.release_spend("probe-after-reap")


def test_a_genuinely_supported_external_payment_still_completes(app, wallet_env):
    """Positive control: the new doors must not break the supported external-signer journey."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58decode, b58encode
    from core.wallet import lifecycle, proposals

    key = Ed25519PrivateKey.generate()
    public_key = b58encode(key.public_key().public_bytes_raw())
    _status, registered = _post(app, "/api/wallet/external", {"public_key": public_key, "network": DEVNET, "label": "ext"})
    wallet_id = registered["wallet"]["wallet_id"]
    _status, proposed = _post(app, "/api/wallet/propose", {"wallet_id": wallet_id, "network": DEVNET, "destination": DESTINATION, "amount_minor": 1000, "asset": "SOL"})
    engine = lifecycle.default_lifecycle()
    prepared = engine.prepare(proposed["proposal"]["proposal_id"])
    view = engine.request_external_signature(prepared.proposal_id)
    signature = key.sign(b58decode(view["message_b58"]))
    receipt = engine.submit_external_signature(view["request_id"], signature_b58=b58encode(signature))
    assert receipt.state in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST}
    assert wallet_env["rpc"].send_count() == 1
