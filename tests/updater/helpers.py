"""Shared fixtures for the signed-atomic-self-update tests.

Builds real Ed25519 publisher keypairs and signs real manifest/artifact bytes with
`cryptography` — the same primitive the runtime verifies with — so a green test means
the production verifier accepted a genuinely signed document, never a stub.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate_publisher_keypair(seed: bytes | None = None) -> tuple[str, str]:
    """Return (private_hex, public_hex) for a throwaway publisher key."""
    key = (
        Ed25519PrivateKey.from_private_bytes(seed.ljust(32, b"\0")[:32])
        if seed is not None
        else Ed25519PrivateKey.generate()
    )
    private_hex = key.private_bytes_raw().hex()
    public_hex = key.public_key().public_bytes_raw().hex()
    return private_hex, public_hex


def canonical_manifest_bytes(manifest: dict) -> bytes:
    payload = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_bytes(private_hex: str, payload: bytes) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    return base64.b64encode(key.sign(payload)).decode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(
    *,
    version: str = "0.6.0",
    channel: str = "stable",
    sequence: int = 47,
    published_at: str = "2026-09-01T12:00:00Z",
    artifacts: dict[str, bytes] | None = None,
    key_id: str = "release-2026-01",
    private_hex: str = "",
    minimum_compatible: str = "0.4.0",
    build_commit: str = "",
) -> tuple[dict, dict[str, bytes]]:
    """Build a fully signed manifest plus the artifact bytes it describes."""
    artifacts = artifacts if artifacts is not None else {
        "macos-arm64": b"FAKE-APP-BUNDLE-ZIP-" + version.encode() + b"-" + channel.encode()
    }
    artifact_entries: dict[str, dict] = {}
    for platform, blob in artifacts.items():
        artifact_entries[platform] = {
            "url": f"https://updates.example.invalid/vool/{version}/{platform}.zip",
            "size": len(blob),
            "sha256": sha256_hex(blob),
            "signature": sign_bytes(private_hex, blob) if private_hex else "",
        }
    manifest = {
        "schema": "vool.update.manifest.v1",
        "product": "vool",
        "channel": channel,
        "version": version,
        "sequence": sequence,
        "published_at": published_at,
        "minimum_compatible": minimum_compatible,
        "notes": f"release {version}",
        "artifacts": artifact_entries,
    }
    if build_commit:
        manifest["build_commit"] = build_commit
    if private_hex:
        manifest["signature"] = {"key_id": key_id, "sig": sign_bytes(private_hex, canonical_manifest_bytes(manifest))}
    return manifest, artifacts


def manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")


def write_manifest_file(path: Path, manifest: dict) -> Path:
    path.write_bytes(manifest_bytes(manifest))
    return path
