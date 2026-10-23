"""Independent P-256 approval identity. This native build is not proven.

Certificate signatures alone do not prove Android hardware/auth policy. Without
trusted roots, revocation and a reviewed attestation-extension verifier this
module records evidence as unproven and never enables phone approval.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from itertools import pairwise

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

BUILD_PROVEN = False


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def public_key(value):
    data = base64.b64decode(value["public_key"], validate=True)
    if len(data) > 1024:
        raise ValueError("Invalid approval key.")
    if value.get("encoding") == "x963":
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), data)
    elif value.get("encoding") == "spki":
        key = serialization.load_der_public_key(data)
    else:
        raise ValueError("Unknown approval key encoding.")
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("P-256 approval key required.")
    return key


def record(value, *, pairing_id, challenge, device_public_key):
    if value is None:
        return {"state": "missing_use_computer", "build_proven": False}
    if not isinstance(value, dict):
        return {"state": "invalid_use_computer", "build_proven": False}
    try:
        key = public_key(value)
        platform = value.get("platform")
        if platform not in {"ios", "android"}:
            raise ValueError("Unknown approval platform.")
        result = {
            "state": "unproven_use_computer",
            "build_proven": False,
            "platform": platform,
            "public_key": value["public_key"],
            "encoding": value["encoding"],
            "attestation": "not_checked_in_this_build" if platform == "ios" else "unproven",
        }
        if platform == "android":
            chain = value.get("chain")
            if not isinstance(chain, list) or not 2 <= len(chain) <= 8:
                raise ValueError("Attestation chain missing.")
            certificates = [
                x509.load_der_x509_certificate(base64.b64decode(item, validate=True))
                for item in chain
                if isinstance(item, str) and len(item) <= 16384
            ]
            if len(certificates) != len(chain):
                raise ValueError("Invalid attestation chain.")
            now = datetime.now(timezone.utc)
            for certificate in certificates:
                if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
                    raise ValueError("Attestation certificate expired.")
            for child, issuer in pairwise(certificates):
                child.verify_directly_issued_by(issuer)
            certificates[-1].verify_directly_issued_by(certificates[-1])
            if certificates[0].public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            ) != key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
                raise ValueError("Attestation key mismatch.")
            extension = certificates[0].extensions.get_extension_for_oid(
                x509.ObjectIdentifier("1.3.6.1.4.1.11129.2.1.17")
            )
            expected = hashlib.sha256(
                canonical(
                    {
                        "kind": "vool.mobile.approval-key.v1",
                        "pairing_id": pairing_id,
                        "challenge": challenge,
                        "device_public_key": device_public_key,
                    }
                )
            ).hexdigest()
            result.update(
                chain_signatures="valid",
                root_trust="not_checked",
                hardware_and_auth_policy="not_checked",
                revocation="not_checked",
                challenge_digest=expected,
                attestation_extension_sha256=hashlib.sha256(extension.value.value).hexdigest(),
            )
        return result
    except Exception:
        # This is the untrusted native-evidence boundary. Parser/algorithm
        # failures never escape into a partially committed pairing.
        return {"state": "invalid_use_computer", "build_proven": False}


def verify_exact_signature(evidence, signature, expected):
    """Cryptographic fact binding only; never an approval or a hardware verdict."""
    if set(expected) != {"kind", "request_id", "action", "payload_sha256", "challenge_digest", "expires_at"}:
        raise ValueError("Exact action facts required.")
    expiry = expected["expires_at"]
    if type(expiry) not in (int, float) or not time.time() < expiry <= time.time() + 300:
        raise ValueError("Approval expired.")
    for field in ("payload_sha256", "challenge_digest"):
        raw = expected[field]
        if not isinstance(raw, str) or len(raw) != 64 or any(c not in "0123456789abcdef" for c in raw):
            raise ValueError("Invalid action digest.")
    key = public_key(evidence)
    key.verify(base64.b64decode(signature, validate=True), canonical(expected), ec.ECDSA(hashes.SHA256()))
    return True


def can_approve(evidence):
    # Never trust build_proven/hardware fields provided by the phone.
    return BUILD_PROVEN and evidence.get("state") == "verified"
