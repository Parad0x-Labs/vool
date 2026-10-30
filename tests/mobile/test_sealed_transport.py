"""Sealed phone actions over a real daemon and its separate listener."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

import pytest
from nacl.public import Box, PublicKey
from nacl.signing import SigningKey

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import enable, http


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def pair(desktop, rights=None, approval_key=None, *, activate_chat=True):
    from core.mobile_companion.sealing import open_envelope, seal

    base, runtime, _home = desktop
    value = enable(runtime)
    phone = f"http://127.0.0.1:{value['port']}"
    status, start = http(base, "/api/mobile/pairing/start", {})
    assert status == 200
    status, challenge = http(phone, "/api/mobile/pairing/challenge?pairing_id=" + start["pairing_id"])
    assert status == 200
    binding = challenge["sealing"]["binding"]
    assert hashlib.sha256(bytes.fromhex(challenge["desktop_public_key"])).hexdigest() == start["desktop_fingerprint"]
    from nacl.signing import VerifyKey

    VerifyKey(bytes.fromhex(challenge["desktop_public_key"])).verify(canonical(binding), bytes.fromhex(challenge["sealing"]["signature"]))
    key = SigningKey.generate()
    curve = key.to_curve25519_private_key()
    shared = Box(curve, PublicKey(bytes.fromhex(binding["public_key"]))).shared_key()
    proof = {"kind": "vool.mobile.pairing.proof.v1", "pairing_id": start["pairing_id"],
             "code": start["code"], "challenge": challenge["challenge"],
             "device_public_key": key.verify_key.encode().hex(), "expires_at": challenge["expires_at_epoch"]}
    body = {"pairing_id": start["pairing_id"], "code": start["code"], "device_name": "Test phone",
            "platform": "android", "device_public_key": proof["device_public_key"], "approval_key": approval_key,
            "device_proof": {"algorithm": "ed25519", "signature": key.sign(canonical(proof)).signature.hex()}}
    from core.mobile_companion.sealing import VERSION

    wire = {**seal({"kind": VERSION + ":claim", "body": body}, shared, "request"),
            "public_key": curve.public_key.encode().hex()}
    status, response = http(phone, "/api/mobile/pairing/claim", wire)
    assert status == 200, response
    assert "grant_secret" not in json.dumps(response)
    claim = open_envelope(response, shared, "response")["body"]
    if activate_chat or rights:
        rights = {**({"chat": True} if activate_chat else {}), **(rights or {})}
        status, changed = http(base, "/api/mobile/settings", {"operation": "device_rules", "device_id": claim["device_id"], "rules": rights})
        assert status == 200, changed
    return phone, key, shared, claim


def action(key, shared, claim, name="devices.me", params=None):
    from core.device_link.protocol import request_signature_payload
    from core.mobile_companion.sealing import VERSION, seal

    params = params or {}
    nonce = secrets.token_hex(16)
    timestamp = datetime.now(timezone.utc).isoformat()
    proof = {"kind": "vool.mobile.request.v1", "device_id": claim["device_id"], "action": name,
             "params_sha256": hashlib.sha256(canonical(params)).hexdigest(), "nonce": nonce,
             "timestamp": timestamp, "grant_id": claim["grant_id"]}
    hmac_payload = request_signature_payload(grant_id=claim["grant_id"], verb="vool.action",
                                             params={"action": name, **params}, nonce=nonce, timestamp=timestamp)
    body = {"device_id": claim["device_id"], "grant_id": claim["grant_id"], "action": name,
            "params": params, "nonce": nonce, "timestamp": timestamp,
            "device_signature": key.sign(canonical(proof)).signature.hex(),
            "signature": hmac.new(claim["grant_secret"].encode(), canonical(hmac_payload), hashlib.sha256).hexdigest()}
    return {**seal({"kind": VERSION + ":request", "body": body}, shared, "request"), "device_id": claim["device_id"]}


def opened(response, shared, wire):
    from core.mobile_companion.sealing import VERSION, open_envelope

    value = open_envelope(response, shared, "response")
    assert value["kind"] == VERSION + ":response"
    assert value["request_hash"] == hashlib.sha256(canonical(wire)).hexdigest()
    return value["body"]


def test_sealed_pairing_request_response_and_durable_replay(desktop):
    from core.web.api.mobile_companion_api import reset_mobile_companion_service

    phone, key, shared, claim = pair(desktop)
    wire = action(key, shared, claim)
    status, response = http(phone, "/api/mobile/companion", wire)
    assert status == 200
    assert opened(response, shared, wire)["result"]["device_id"] == claim["device_id"]
    assert "Test phone" not in json.dumps(response)
    status, replay = http(phone, "/api/mobile/companion", wire)
    assert status == 409
    assert opened(replay, shared, wire)["code"] == "replay_detected"
    reset_mobile_companion_service()
    status, replay = http(phone, "/api/mobile/companion", wire)
    assert status == 409
    assert opened(replay, shared, wire)["code"] == "replay_detected"


@pytest.mark.parametrize("sabotage", ["plaintext", "tamper", "wrong_key", "wrong_device", "wrong_purpose", "downgrade"])
def test_sealed_transport_refuses_sabotage_and_receipts_it(desktop, sabotage):
    from core.mobile_companion.sealing import VERSION, open_envelope, seal

    phone, key, shared, claim = pair(desktop)
    wire = action(key, shared, claim)
    if sabotage == "plaintext":
        wire = open_envelope(wire, shared, "request")["body"]
    elif sabotage == "tamper":
        wire["ciphertext"] = ("A" if wire["ciphertext"][0] != "A" else "B") + wire["ciphertext"][1:]
    elif sabotage == "wrong_key":
        wire = {**seal({"kind": VERSION + ":request", "body": {}}, secrets.token_bytes(32), "request"), "device_id": claim["device_id"]}
    elif sabotage == "wrong_device":
        wire["device_id"] = "phone:unknown"
    elif sabotage == "wrong_purpose":
        wire = {**seal({"kind": VERSION + ":response", "body": {}}, shared, "request"), "device_id": claim["device_id"]}
    elif sabotage == "downgrade":
        wire["protocol"] = "v1"
    status, _response = http(phone, "/api/mobile/companion", wire)
    assert status in {400, 401}
    base, _runtime, _home = desktop
    status, receipts = http(base, "/api/mobile/receipts")
    assert status == 200 and receipts["chain_verified"]
    assert any(r["kind"] in {"security.seal_refused", "security.plaintext_refused"} for r in receipts["receipts"])


def test_revoked_phone_gets_only_a_sealed_refusal(desktop):
    phone, key, shared, claim = pair(desktop)
    base, _runtime, _home = desktop
    assert http(base, "/api/mobile/devices/revoke", {"device_id": claim["device_id"]})[0] == 200
    wire = action(key, shared, claim)
    status, response = http(phone, "/api/mobile/companion", wire)
    assert status == 401
    assert opened(response, shared, wire)["code"] == "device_revoked"


def test_legacy_desktop_route_refuses_remote_plaintext():
    from starlette.testclient import TestClient

    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    with TestClient(create_app(RuntimeServices()), client=("192.168.1.51", 53000)) as client:
        response = client.post("/api/mobile/companion", headers={"Host": "localhost"}, json={})
        assert response.status_code == 403
        assert response.json()["code"] == "companion_listener_required"


def test_python_and_typescript_share_curve_keys_and_sealed_vectors():
    import subprocess
    from pathlib import Path

    from core.mobile_companion.sealing import directional_key, seal

    seed = bytes(range(32))
    peer = SigningKey(bytes(range(32, 64))).to_curve25519_private_key()
    private = SigningKey(seed).to_curve25519_private_key()
    shared = Box(private, peer.public_key).shared_key()
    value = {"message": "A different question: café, 雨, שלום", "sequence": 42}
    nonce = bytes(range(24))
    expected = {"seed": seed.hex(), "peer": peer.public_key.encode().hex(), "shared": shared.hex(),
                "public": private.public_key.encode().hex(), "nonce": nonce.hex(), "value": value,
                "key": directional_key(shared, "request").hex(), "wire": seal(value, shared, "request", nonce=nonce)}
    program = """
import assert from 'node:assert/strict';
import fs from 'node:fs';
import nacl from './src/vendor/nacl-fast.cjs';
import {curveSecret,directionalKey,sealBody,openBody} from './src/protocol/sealed.ts';
import {fromHex,toHex} from './src/protocol/sha256.ts';
const v=JSON.parse(fs.readFileSync(0,'utf8'));
const privateKey=curveSecret(v.seed);
assert.equal(toHex(nacl.box.keyPair.fromSecretKey(privateKey).publicKey),v.public);
const shared=nacl.box.before(fromHex(v.peer),privateKey);
assert.equal(toHex(shared),v.shared);
assert.equal(toHex(directionalKey(shared,'request')),v.key);
const wire=sealBody(v.value,shared,'request',fromHex(v.nonce));
assert.deepEqual(wire,v.wire);
assert.deepEqual(openBody(wire,shared,'request'),v.value);
assert.throws(()=>openBody(wire,shared,'response'));
wire.ciphertext='A'+wire.ciphertext.slice(1);
assert.throws(()=>openBody(wire,shared,'request'));
console.log('curve conversion, directional keys, Unicode ciphertext and tamper vectors pass');
"""
    result = subprocess.run(["node", "--input-type=module", "-e", program],
                            cwd=Path(__file__).resolve().parents[2] / "mobile",
                            input=json.dumps(expected), text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_expired_grant_is_refused_inside_sealed_response_and_receipted(desktop):
    from core.device_link import protocol
    from core.web.api.mobile_companion_api import mobile_companion_service

    phone, key, shared, claim = pair(desktop)
    service = mobile_companion_service()
    grant = dict(service.store.device(claim["device_id"])["grant"])
    grant["expires_at"] = "2000-01-01T00:00:00+00:00"
    grant["signature"] = protocol.sign_with_authority_key(service.authority_key(), canonical(protocol.grant_signature_payload(grant)))
    service.store.mark_device(claim["device_id"], grant=grant)
    service.registry.register(grant)
    wire = action(key, shared, claim)
    status, response = http(phone, "/api/mobile/companion", wire)
    assert status == 401
    assert opened(response, shared, wire)["code"] == "grant_expired"
    base, _runtime, _home = desktop
    _status, receipts = http(base, "/api/mobile/receipts")
    assert any(r["kind"] == "security.authorization_refused" and r["detail"]["code"] == "grant_expired" for r in receipts["receipts"])


def test_shared_phone_client_on_the_live_separate_listener(desktop, tmp_path):
    import os
    import subprocess
    from pathlib import Path

    base, runtime, _home = desktop
    value = enable(runtime)
    phone = f"http://127.0.0.1:{value['port']}"
    result = subprocess.run(["node", "e2e/sealed-journey.mjs"],
                            cwd=Path(__file__).resolve().parents[2] / "mobile",
                            env={**os.environ, "VOOL_DESKTOP_URL": base, "VOOL_PHONE_URL": phone},
                            text=True, capture_output=True, timeout=30)
    (tmp_path / "sealed-journey.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr


def test_transport_identity_and_action_bounds_do_not_bloat_receipts(desktop):
    from core.web.api.mobile_companion_api import mobile_companion_service
    phone,key,shared,claim=pair(desktop)
    wire=action(key,shared,claim,'signer.'+'x'*4096)
    status,response=http(phone,'/api/mobile/companion',wire)
    assert status==400 and opened(response,shared,wire)['code']=='invalid_request'
    status,response=http(phone,'/api/mobile/companion',{**wire,'device_id':'x'*4096})
    assert status==400
    assert 'x'*4096 not in json.dumps(mobile_companion_service().store.receipts()[0])


def test_every_new_phone_right_starts_off_until_pc_grants_it(desktop):
    from tests.mobile.test_phone_notifications import call
    phone = pair(desktop, activate_chat=False)
    status, record = call(*phone, "devices.me")
    assert status == 200 and record["result"]["rules"]
    assert not any(record["result"]["rules"].values())
    assert call(*phone,"chats.send",{"message":"No implicit chat grant", "idempotency_key":secrets.token_hex(16)})[0] == 403
    assert http(desktop[0],"/api/mobile/settings",{"operation":"device_rules","device_id":phone[3]["device_id"],"rules":{"chat":True}})[0] == 200
    assert call(*phone,"devices.me")[1]["result"]["rules"]["chat"] is True
