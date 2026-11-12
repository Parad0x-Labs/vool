"""Bound pre-proposal review; this never authorizes signing or payment execution."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import statistics
import time
from types import SimpleNamespace

from core.mobile_companion.policy import enabled


def preview(params, *, service, device):
    from core.mobile_companion.payments import _check, _table, intent, refuse_unresolved_duplicate
    from core.wallet import amounts, chains, controls, scam_school
    from core.wallet.store import connection

    code, value = intent(params, device)
    if code != 200:
        return code, value
    binding = value["binding"]
    token = value["token"]
    with service.dispatch_lock:
        current = service.store.device(device["device_id"]) or {}
        if current.get("status") != "active" or not enabled(current, "payment_propose"):
            return 403, {"code": "phone_right_off_or_expired", "error": "Payment proposals are off for this phone."}
        with connection() as conn:
            _table(conn)
            existing = conn.execute(
                "SELECT proposal_id,payload_digest,freeze_epoch FROM mobile_payment_claims WHERE device_id=? AND request_key=?",
                (device["device_id"], token),
            ).fetchone()
            freeze_epoch = controls.epochs(conn)["freeze"]
            if existing and existing[2] != freeze_epoch:
                from core.mobile_companion.payments import _fault

                raise _fault("phone_payment_freeze_changed_new_request_required")
            if existing and existing[1] != value["digest"]:
                return 409, {
                    "code": "payment_review_changed",
                    "error": "Discard the saved request before changing the payee or amount.",
                }
            refuse_unresolved_duplicate(conn, binding, existing[0] if existing else "")
            _check(conn, SimpleNamespace(**binding, proposal_id=existing[0] if existing else ""), current)
            rows = conn.execute(
                "SELECT amount_minor FROM wallet_proposals WHERE destination=? AND network=? AND state='confirmed' ORDER BY updated_at DESC LIMIT 20",
                (binding["destination"], binding["network"]),
            ).fetchall()
        warnings = []
        if not rows:
            warnings.append("new_payee")
        history = [int(row[0]) for row in rows]
        if binding["amount_minor"] * 2 >= int(current["payment_cap"]["per_payment_minor"]) or (
            len(history) >= 3 and binding["amount_minor"] > 3 * statistics.median(history)
        ):
            warnings.append("unusual_amount")
        proof = {
            "digest": value["digest"],
            "warnings": warnings,
            "expires_at": time.time() + 300,
            "review_token": secrets.token_hex(16),
            "permission_revision": int(current.get("permission_revision") or 0),
            "freeze_epoch": freeze_epoch,
        }
        reviews = {
            key: row
            for key, row in (current.get("payment_reviews") or {}).items()
            if row.get("expires_at", 0) > time.time()
        }
        reviews[token] = proof
        service.store.mark_device(device["device_id"], payment_reviews=dict(list(reviews.items())[-128:]))
        service.store.append_receipt(
            "payment.review_shown",
            device_id=device["device_id"],
            detail={"digest": value["digest"], "warnings": warnings},
        )
    asset = chains.native_asset(binding["network"])
    return 200, {
        "review_token": proof["review_token"],
        "expires_at": proof["expires_at"],
        "intent_digest": value["digest"],
        "amount": amounts.format_minor(binding["amount_minor"], asset.decimals),
        "asset": asset.symbol,
        "network": binding["network"],
        "destination": binding["destination"],
        "warnings": warnings,
        "approval_location": "computer",
        "fees": "review_on_computer",
        "safety_guidance_sha256": hashlib.sha256(
            (scam_school.GOLDEN_RULE + scam_school.PRINCIPLE).encode()
        ).hexdigest(),
    }


def require(params, *, service, device, digest, conn):
    from core.mobile_companion.payments import _fault, _table

    _table(conn)
    key = str(params.get("idempotency_key") or "")
    existing = conn.execute(
        "SELECT payload_digest,review_json FROM mobile_payment_claims WHERE device_id=? AND request_key=?",
        (device["device_id"], key),
    ).fetchone()
    if existing and existing[0] == digest:
        import json

        return json.loads(existing[1])  # Recover this exact accepted proposal, never mint another.
    current = service.store.device(device["device_id"]) or {}
    review = (current.get("payment_reviews") or {}).get(key) or {}
    from core.wallet import controls

    if review.get("freeze_epoch") != controls.epochs(conn)["freeze"]:
        raise _fault("phone_payment_review_invalidated_by_freeze")
    if (
        review.get("digest") != digest
        or review.get("expires_at", 0) <= time.time()
        or review.get("permission_revision") != int(current.get("permission_revision") or 0)
        or not hmac.compare_digest(str(params.get("review_token") or ""), str(review.get("review_token") or ""))
    ):
        raise _fault("phone_payment_review_required_or_changed")
    warnings = review.get("warnings")
    acknowledged = params.get("warnings_acknowledged")
    if not isinstance(warnings, list) or acknowledged != warnings:
        raise _fault("phone_payment_warnings_require_confirmation")
    return {"digest": digest, "warnings": warnings, "acknowledged": acknowledged, "via": "phone"}
