"""Versioned NaCl transport bound to the QR-pinned desktop authority.

The Ed25519 identity and its Curve25519 conversion are the same identity.
The transport carries ciphertext only; the existing grant and device proofs
remain inside it and retain their own revocation/expiry/action checks.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

from nacl.exceptions import CryptoError
from nacl.public import Box, PublicKey
from nacl.secret import SecretBox
from nacl.signing import VerifyKey

VERSION = "vool.mobile.sealed.v2"


def _bytes(value):
    from core.web.api.mobile_companion_api import _canonical_bytes

    return _canonical_bytes(value)


def _public(service):
    return service.authority_key().to_curve25519_private_key().public_key.encode().hex()


def shared_key(service, public_key):
    key = PublicKey(bytes.fromhex(public_key))
    return Box(service.authority_key().to_curve25519_private_key(), key).shared_key()


def directional_key(shared, direction):
    if direction not in {"request", "response"}:
        raise ValueError("invalid direction")
    return hashlib.sha256(shared + (VERSION + ":" + direction).encode()).digest()


def seal(value, shared, direction, *, nonce=None):
    nonce = nonce or secrets.token_bytes(24)
    encrypted = SecretBox(directional_key(shared, direction)).encrypt(_bytes(value), nonce)
    return {"protocol": VERSION, "nonce": nonce.hex(), "ciphertext": base64.b64encode(encrypted.ciphertext).decode()}


def open_envelope(envelope, shared, direction):
    if not isinstance(envelope, dict) or envelope.get("protocol") != VERSION:
        raise ValueError("sealed transport required")
    nonce = bytes.fromhex(envelope["nonce"])
    if len(nonce) != 24:
        raise ValueError("invalid nonce")
    ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    raw = SecretBox(directional_key(shared, direction)).decrypt(ciphertext, nonce)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid envelope")
    return value


def _reply(response, shared, request):
    from core.web.api.mobile_companion_api import _json

    return _json(
        response.status,
        seal(
            {
                "kind": VERSION + ":response",
                "request_hash": hashlib.sha256(_bytes(request)).hexdigest(),
                "body": json.loads(response.body),
            },
            shared,
            "response",
        ),
    )


def challenge_response(path, query, *, runtime, model_name, client_host):
    from core.web.api import mobile_companion_api as api

    response = api.handle_mobile_companion_get(
        path, query, runtime=runtime, model_name=model_name, client_host=client_host
    )
    if response.status != 200 or path != "/api/mobile/pairing/challenge":
        return response
    service = api.mobile_companion_service()
    payload = json.loads(response.body)
    binding = {
        "kind": VERSION + ":pairing",
        "public_key": _public(service),
        "desktop_fingerprint": service.desktop_fingerprint(),
        "pairing_id": payload["pairing_id"],
        "challenge": payload["challenge"],
        "expires_at_epoch": payload["expires_at_epoch"],
    }
    payload["sealing"] = {
        "binding": binding,
        "signature": service.authority_key().sign(_bytes(binding)).signature.hex(),
    }
    return api._json(200, payload)


def dispatch(path, envelope, headers, *, runtime, model_name, client_host, active=lambda: True):
    from core.web.api import mobile_companion_api as api

    service = api.mobile_companion_service()
    store = service.store
    if envelope.get("protocol") != VERSION:
        service.ingress.refused("security.plaintext_refused", reason="phone_listener")
        return api._err(400, "sealed_transport_required")
    device_id = envelope.get("device_id") or ""
    pairing = path == "/api/mobile/pairing/claim"
    if not isinstance(device_id, str) or len(device_id) > 128:
        service.ingress.refused("security.seal_refused", reason="device_binding_too_large")
        return api._err(400, "invalid_request")
    try:
        if pairing:
            public_key = str(envelope.get("public_key") or "")
        else:
            record = store.device(device_id) or {}
            public_key = str(record.get("encryption_public_key") or "")
        shared = shared_key(service, public_key)
        inner = open_envelope(envelope, shared, "request")
        expected_kind = VERSION + (":claim" if pairing else ":request")
        if inner.get("kind") != expected_kind:
            raise ValueError("wrong envelope purpose")
        body = inner["body"]
        if not isinstance(body, dict):
            raise ValueError("invalid body")
        if pairing:
            # Reject a phone trying to encrypt with a key unrelated to the
            # Ed25519 key whose possession the claim proves.
            converted = VerifyKey(bytes.fromhex(body["device_public_key"])).to_curve25519_public_key().encode().hex()
            if not secrets.compare_digest(converted, public_key):
                raise ValueError("identity/key mismatch")
        elif body.get("device_id") != device_id:
            raise ValueError("device binding mismatch")
    except (ValueError, KeyError, TypeError, CryptoError, RecursionError):
        service.ingress.refused("security.seal_refused", reason="envelope_authentication_failed")
        return api._err(401, "sealed_authentication_failed")
    if not active():
        return _reply(api._err(403, "phone_access_off"), shared, envelope)
    if pairing:
        from core.mobile_companion.approval_key import record
        from core.mobile_companion.policy import defaults

        # Cheap admission precedes certificate parsing. The canonical claim below
        # rechecks the session under the lock before consuming a code or a grant.
        pairing_id = body.get("pairing_id")
        if not isinstance(pairing_id, str) or len(pairing_id) > 128:
            service.ingress.refused("pairing.failed", reason="invalid_pairing")
            return _reply(api._err(400, "invalid_request"), shared, envelope)
        session = store.pairing(pairing_id)
        if not session:
            service.ingress.refused("pairing.failed", reason="unknown_pairing")
            return _reply(api._err(404, "pairing_unknown"), shared, envelope)
        if session.get("status") == "claimed":
            service.ingress.refused("pairing.failed", reason="code_replayed")
            return _reply(api._err(409, "pairing_used"), shared, envelope)
        if float(session.get("expires_at_epoch") or 0) <= time.time():
            service.ingress.refused("pairing.failed", reason="expired")
            return _reply(api._err(410, "pairing_expired"), shared, envelope)
        if int(session.get("attempts") or 0) >= api._PAIRING_MAX_ATTEMPTS:
            service.ingress.refused("pairing.failed", reason="attempt_cap")
            return _reply(api._err(429, "pairing_locked"), shared, envelope)
        if session.get("status") != "waiting":
            service.ingress.refused("pairing.failed", reason="session_unavailable")
            return _reply(api._err(409, "pairing_unavailable"), shared, envelope)
        # Untrusted native evidence never holds the shared action-dispatch lock.
        evidence = record(
            body.get("approval_key"), pairing_id=pairing_id,
            challenge=session.get("challenge"), device_public_key=body.get("device_public_key"),
        )
        if evidence["state"] == "invalid_use_computer":
            service.ingress.refused("security.approval_key_refused", reason="invalid_approval_key")
            return _reply(api._err(400, "invalid_approval_key"), shared, envelope)
        with service.dispatch_lock:
            if not active():
                return _reply(api._err(403, "phone_access_off"), shared, envelope)
            # Validate and construct all sealed fields before consuming the code
            # or issuing a grant. There is no active partially sealed device.
            fields = dict(encryption_public_key=public_key, sealed_protocol=VERSION, rules=defaults(),
                          right_expiry={}, approval_key=evidence)
            response = api.claim_pairing(body, headers, service, client_host, sealed_fields=fields)
            if response.status == 200:
                result = json.loads(response.body)
                from core.mobile_companion.device_notices import record as record_notice

                record_notice(service, result["device_id"], "device_paired")
        return _reply(response, shared, envelope)
    # Durably consume the *wire* nonce before dispatch. The grant authority's
    # own signed nonce is checked too; restarting cannot replay a captured wire.
    if (store.device(device_id) or {}).get("status") != "active":
        return _reply(api._err(401, "device_revoked"), shared, envelope)
    nonce_key = hashlib.sha256((device_id + ":" + str(envelope.get("nonce"))).encode()).hexdigest()
    if not store.claim_wire_nonce(nonce_key, time.time()):
        service.ingress.refused("security.replay_detected", reason="wire_nonce_reused")
        return _reply(api._err(409, "replay_detected"), shared, envelope)
    if not store.allow_rate("sealed:" + device_id, cap=api._DEVICE_RATE_CAP, window=api._RATE_WINDOW):
        return _reply(api._err(429, "device_rate_limited"), shared, envelope)
    from core.web.api.runtime import default_workspace_root

    response = api.dispatch_companion(
        body,
        headers,
        runtime=runtime,
        model_name=model_name,
        # Identity is stable across Wi-Fi/VPN/relay. This is a server-derived,
        # deny-only remote peer, never an owner-local sentinel.
        client_host="mobile:" + device_id,
        workspace_root_provider=default_workspace_root,
        service=service,
    )
    if not active():
        # The accepted action may have finished. Keep its identity queued rather
        # than claiming it was certainly refused or leaking its late reply.
        return _reply(api._err(503, "phone_access_off"), shared, envelope)
    return _reply(response, shared, envelope)
