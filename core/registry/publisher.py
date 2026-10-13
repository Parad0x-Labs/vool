"""Offline publication helpers. Web sessions cannot invoke this signing authority."""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.registry.admission import admit
from core.registry.artifact import inspect
from core.registry.codec import canonical, digest, require
from core.registry.trust import root_document, validate_entry, verify


def add_signature(envelope: dict, private_key: Ed25519PrivateKey) -> dict:
    require(isinstance(private_key, Ed25519PrivateKey), "registry signing requires an Ed25519 key")
    require(isinstance(envelope, dict) and set(envelope) == {"signed", "signatures"}, "invalid signing envelope")
    public = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    keyid = digest({"ed25519": public})
    signatures = [item for item in envelope["signatures"] if item["keyid"] != keyid]
    signatures.append({"keyid": keyid, "sig": base64.b64encode(private_key.sign(canonical(envelope["signed"]))).decode()})
    return {"signed": envelope["signed"], "signatures": signatures}


def admission(raw: bytes, policy: dict, review: dict, root: dict, *, now: int, lifetime=7 * 86400) -> dict:
    root_document(root, now)
    evidence = verify(review, root, "review")
    require(evidence["policy_sha256"] in root["policies"], "policy is not approved by the offline root")
    result = admit(inspect(raw), policy, evidence, now=now)
    result.update(schema="vool.registry.admission.v1", issued_at=now, expires=now + lifetime)
    validate_entry(result, root, now)
    return {"signed": result, "signatures": []}


def authorize_catalog(root: dict, admissions: list[dict], *, epoch: int, revoked: list[str], now: int,
                      lifetime=86400) -> dict:
    root_document(root, now)
    for envelope in admissions:
        validate_entry(verify(envelope, root, "admission"), root, now)
    require(type(epoch) is int and epoch > 0 and 0 < lifetime <= 86400, "invalid offline catalog epoch or lifetime")
    return {"signed": {"schema": "vool.registry.catalog_authorization.v1", "root_version": root["version"],
        "epoch": epoch, "issued_at": now, "expires": now + lifetime,
        "entries_sha256": digest(admissions), "revoked_sha256": digest(revoked)}, "signatures": []}


def catalog(root: dict, admissions: list[dict], *, sequence: int, revoked: list[str], authorization: dict, now: int,
            lifetime=3600) -> dict:
    root_document(root, now)
    approved = verify(authorization, root, "admission")
    require(approved["entries_sha256"] == digest(admissions) and approved["revoked_sha256"] == digest(revoked),
            "catalog bytes differ from offline authorization")
    require(type(sequence) is int and sequence > 0 and 0 < lifetime <= 48 * 3600 and
            now + lifetime <= approved["expires"], "invalid catalog sequence or lifetime")
    return {"signed": {"schema": "vool.registry.catalog.v1", "root_version": root["version"],
        "sequence": sequence, "issued_at": now, "expires": now + lifetime, "entries": admissions, "revoked": revoked,
        "authorization": authorization},
        "signatures": []}


def attach_to_directory(directory: dict, envelope: dict, trusted_catalog, *, now: int) -> dict:
    """The existing canonical catalog owns discovery and its signed reviewed projection.

    Community repository CI proposes this change for maintainer merge. The public
    server is a reader/cache; it cannot mutate discovery flags or approve a row.
    """
    require(isinstance(directory, dict) and isinstance(directory.get("entries"), list), "canonical catalog is missing")
    trusted_catalog.accept(envelope, now=now)
    return {**directory, "registry": envelope}


def main(argv=None) -> int:
    import argparse
    import getpass
    from pathlib import Path

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    from core.registry.codec import RegistryError, decode

    parser = argparse.ArgumentParser(description="Offline registry signature, no key generation or remote writes")
    parser.add_argument("envelope", type=Path)
    parser.add_argument("--encrypted-key", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        pem = args.encrypted_key.read_bytes()
        require(b"ENCRYPTED PRIVATE KEY" in pem and len(pem) <= 16384, "an encrypted offline key is required")
        key = load_pem_private_key(pem, password=getpass.getpass("Offline key passphrase: ").encode())
        envelope = add_signature(decode(args.envelope.read_bytes()), key)
        with args.output.open("xb") as output:
            output.write(canonical(envelope))
        print("Signature added; no publication performed.")
        return 0
    except (RegistryError, OSError, ValueError, TypeError):
        print("Signing refused. Check the envelope and encrypted key locally.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
