"""Approval evidence never turns an unproven native build into an approver."""
import base64
import copy
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_phone_notifications import call
from tests.mobile.test_sealed_transport import pair


def evidence(private):
    return {'platform':'ios','public_key':base64.b64encode(private.public_key().public_bytes(serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)).decode(),'encoding':'x963','build_proven':True,'state':'verified'}


def test_pairing_records_independent_public_key_but_never_trusts_phone_hardware_claims(desktop):
    from core.mobile_companion.approval_key import can_approve
    from core.web.api.mobile_companion_api import mobile_companion_service
    private=ec.generate_private_key(ec.SECP256R1())
    info=evidence(private)
    phone=pair(desktop,approval_key=info)
    record=mobile_companion_service().store.device(phone[3]['device_id'])['approval_key']
    assert record['public_key']==info['public_key'] and record['attestation']=='not_checked_in_this_build'
    assert record['build_proven'] is False and not can_approve(record)
    assert call(*phone,'wallet.approve',{'credential':'not-a-real-pin'})[0]==403
    assert call(*phone,'approvals.resolve',{'decision':'allow','approval_id':'planted'})[0]==403
    assert http(desktop[0],'/api/mobile/settings',{'operation':'device_rules','device_id':phone[3]['device_id'],
        'rules':{'tool_approval':True}})[0]==400
    assert 'PRIVATE' not in str(record)


def test_absent_approval_key_preserves_chat_pairing_and_approvals_stay_off(desktop):
    from core.web.api.mobile_companion_api import mobile_companion_service

    phone = pair(desktop, approval_key=None)
    assert mobile_companion_service().store.device(phone[3]["device_id"])["approval_key"]["state"] == "missing_use_computer"
    assert call(*phone, "devices.me")[0] == 200


def test_malformed_key_refuses_cleanly_and_same_code_can_pair_without_approval(desktop):
    from nacl.public import Box, PublicKey
    from nacl.signing import SigningKey

    from core.mobile_companion.sealing import VERSION, seal
    from core.web.api.mobile_companion_api import mobile_companion_service
    from tests.mobile.test_phone_listener_served import enable
    from tests.mobile.test_sealed_transport import canonical, opened

    base, runtime, _ = desktop
    phone = "http://127.0.0.1:" + str(enable(runtime)["port"])
    status, start = http(base, "/api/mobile/pairing/start", {})
    assert status == 200, start
    status, challenge = http(phone, "/api/mobile/pairing/challenge?pairing_id=" + start["pairing_id"])
    assert status == 200, challenge
    key = SigningKey.generate()
    curve = key.to_curve25519_private_key()
    shared = Box(curve, PublicKey(bytes.fromhex(challenge["sealing"]["binding"]["public_key"]))).shared_key()
    proof = {"kind": "vool.mobile.pairing.proof.v1", "pairing_id": start["pairing_id"],
             "code": start["code"], "challenge": challenge["challenge"],
             "device_public_key": key.verify_key.encode().hex(), "expires_at": challenge["expires_at_epoch"]}
    body = {"pairing_id": start["pairing_id"], "code": start["code"], "device_name": "Retry phone",
            "platform": "ios", "device_public_key": proof["device_public_key"],
            "approval_key": {"public_key": "bad", "encoding": "spki"},
            "device_proof": {"algorithm": "ed25519", "signature": key.sign(canonical(proof)).signature.hex()}}

    def claim(value):
        wire = {**seal({"kind": VERSION + ":claim", "body": value}, shared, "request"),
                "public_key": curve.public_key.encode().hex()}
        status, response = http(phone, "/api/mobile/pairing/claim", wire)
        return status, opened(response, shared, wire)

    status, refused = claim(body)
    assert status == 400 and refused["code"] == "invalid_approval_key", refused
    service = mobile_companion_service()
    assert not service.store.devices()
    assert service.store.pairings()[start["pairing_id"]]["status"] == "waiting"
    assert any(row["kind"] == "security.approval_key_refused"
               for row in http(base, "/api/mobile/receipts")[1]["receipts"])
    status, accepted = claim({**body, "approval_key": None})
    assert status == 200, accepted
    assert len(service.store.devices()) == 1
    device = service.store.device(accepted["device_id"])
    assert device["approval_key"]["state"] == "missing_use_computer"
    assert not any(device["rules"].values())
    assert call(phone, key, shared, accepted, "devices.me")[0] == 200


def test_exact_fact_signature_cannot_be_reused_for_changed_money_tool_or_expiry():
    from core.mobile_companion.approval_key import canonical, verify_exact_signature
    private=ec.generate_private_key(ec.SECP256R1()); info=evidence(private)
    facts={'kind':'vool.mobile.approval.v1','request_id':'one-request','action':'wallet.approve',
        'payload_sha256':'a'*64,'challenge_digest':'b'*64,'expires_at':time.time()+60}
    signature=base64.b64encode(private.sign(canonical(facts),ec.ECDSA(hashes.SHA256()))).decode()
    assert verify_exact_signature(info,signature,facts)
    for name,value in [('payload_sha256','c'*64),('challenge_digest','d'*64),('action','approvals.resolve'),('request_id','another-request')]:
        with pytest.raises(InvalidSignature):verify_exact_signature(info,signature,{**facts,name:value})
    for expiry in [time.time()-1,float('nan'),float('inf')]:
        with pytest.raises(ValueError):verify_exact_signature(info,signature,{**facts,'expires_at':expiry})


def test_android_signed_chain_without_trusted_hardware_auth_proof_is_unproven():
    from core.mobile_companion.approval_key import can_approve, record
    root=ec.generate_private_key(ec.SECP256R1()); leaf=ec.generate_private_key(ec.SECP256R1())
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Local attestation test root')])
    now=datetime.now(timezone.utc)
    root_cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(root.public_key()).serial_number(1).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(root,hashes.SHA256())
    leaf_cert=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Test key')])).issuer_name(name).public_key(leaf.public_key()).serial_number(2).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.UnrecognizedExtension(x509.ObjectIdentifier('1.3.6.1.4.1.11129.2.1.17'),b'\x30\x00'),critical=False).sign(root,hashes.SHA256())
    info={'platform':'android','encoding':'spki','public_key':base64.b64encode(leaf.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).decode(),
        'chain':[base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode() for c in [leaf_cert,root_cert]]}
    result=record(info,pairing_id='test',challenge='challenge',device_public_key='a'*64)
    assert result['chain_signatures']=='valid'
    assert result['root_trust']=='not_checked' and result['hardware_and_auth_policy']=='not_checked'
    assert not can_approve(result) and result['state']=='unproven_use_computer'
