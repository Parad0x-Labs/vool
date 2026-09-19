"""Authentic bundle identity over VOOL's existing Ed25519 node-signing authority.

The signature covers the MANIFEST bytes; the manifest covers every content member. An attacker
who edits content and recomputes hashes produces a manifest the signature no longer covers; to
make the bundle verify again they must re-sign with THEIR key — and a foreign key is refused
unless the operator explicitly confirms, and a confirmed import stays marked foreign.

Trust model:
- `self`     — signed by THIS home's node key: imports normally;
- `trusted`  — fingerprint listed in the home's trusted-signer registry: imports normally;
- `foreign`  — everyone else: refused without `confirm_untrusted`, marked foreign forever.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any

SIGNATURE_MEMBER = "signature.json"
SIGNATURE_FORMAT = "vool.session_bundle.signature.v1"
SIGNATURE_ALGORITHM = "ed25519"

TRUST_SELF = "self"
TRUST_TRUSTED = "trusted"
TRUST_FOREIGN = "foreign"


def fingerprint_of(public_key_hex: str) -> str:
    digest = hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()
    return f"sha256:{digest[:32]}"


def _trusted_registry_path() -> Path:
    from core.runtime_paths import active_data_dir

    return Path(active_data_dir()) / "trusted_session_signers.json"


def trusted_fingerprints() -> list[str]:
    path = _trusted_registry_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    entries = data.get("fingerprints") if isinstance(data, dict) else data
    return [str(item) for item in (entries or []) if isinstance(item, str)]


def add_trusted_fingerprint(fingerprint: str) -> None:
    """Operator action: add one signer fingerprint to this home's trusted registry."""
    path = _trusted_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current: list[str] = []
    if path.is_file():
        try:
            data = json.loads(path.read_text())
            current = [str(item) for item in (data.get("fingerprints") or []) if isinstance(item, str)]
        except (OSError, ValueError):
            current = []
    if fingerprint not in current:
        current.append(fingerprint)
    path.write_text(json.dumps({"fingerprints": current}, indent=1) + "\n")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def sign_manifest(manifest_bytes: bytes) -> dict[str, Any]:
    """Sign the manifest bytes with THIS node's Ed25519 key."""
    from network import signer

    public_key = signer.get_local_peer_id()
    return {
        "format": SIGNATURE_FORMAT,
        "algorithm": SIGNATURE_ALGORITHM,
        "signer_public_key": public_key,
        "signer_fingerprint": fingerprint_of(public_key),
        "signed": "manifest.sha256",
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "signature": signer.sign(manifest_bytes),
    }


def verify_signature(record: Any, manifest_bytes: bytes) -> bool:
    """Verify a signature record against the manifest bytes. Never raises."""
    if not isinstance(record, dict):
        return False
    if record.get("format") != SIGNATURE_FORMAT or record.get("algorithm") != SIGNATURE_ALGORITHM:
        return False
    public_key = str(record.get("signer_public_key") or "")
    signature = str(record.get("signature") or "")
    if not public_key or not signature:
        return False
    if fingerprint_of(public_key) != str(record.get("signer_fingerprint") or ""):
        return False
    if record.get("signed") != "manifest.sha256":
        return False
    if record.get("manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest():
        return False
    from network import signer

    return signer.verify(manifest_bytes, signature, public_key)


def classify_origin(record: dict[str, Any]) -> str:
    """`self` when signed by this node's key, `trusted` when the fingerprint is registered,
    `foreign` otherwise."""
    from network import signer

    public_key = str(record.get("signer_public_key") or "")
    if public_key and public_key == signer.get_local_peer_id():
        return TRUST_SELF
    if str(record.get("signer_fingerprint") or "") in trusted_fingerprints():
        return TRUST_TRUSTED
    return TRUST_FOREIGN


def decode_signature_member(raw: bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def signature_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
