"""Ed25519 device authority key for Device Link.

Ported from the converged pass's bridge/identity.py (2026-08-26). Same
convention as canonical mesh identity (network/signer.py): an entity's id is
the hex of its Ed25519 *public* key. Keys live in an explicit operator-chosen
path, never inside <runtime_home>/data/keys (that sibling is off-limits).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from nacl.signing import SigningKey

KEY_KIND = "vool-device-link-ed25519-v0"


def generate() -> SigningKey:
    return SigningKey.generate()


def save(signing_key: SigningKey, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": KEY_KIND,
        "seed_b64": signing_key.encode().hex(),
        "public_hex": signing_key.verify_key.encode().hex(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load(path: Path) -> SigningKey:
    raw = json.loads(Path(path).read_text())
    if str(raw.get("kind") or "") != KEY_KIND:
        raise ValueError(f"not a {KEY_KIND} key file: {path}")
    return SigningKey(bytes.fromhex(raw["seed_b64"]))


def load_or_create(path: Path) -> SigningKey:
    if Path(path).exists():
        return load(path)
    key = generate()
    save(key, path)
    return key


def public_hex(signing_key: SigningKey) -> str:
    """device_id / authority_id — hex Ed25519 public key, mesh peer_id convention."""
    return signing_key.verify_key.encode().hex()


def fingerprint(signing_key: SigningKey) -> str:
    """sha256 hex of public key bytes — what a companion pins from the QR code."""
    return hashlib.sha256(signing_key.verify_key.encode()).hexdigest()


def short_fingerprint(signing_key: SigningKey) -> str:
    fp = fingerprint(signing_key)
    return "-".join(fp[i : i + 4] for i in range(0, 16, 4)).upper()
