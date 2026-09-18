"""Sign a release: build the platform-neutral signed manifest for update artifacts.

Release engineering tool — the signer's counterpart of core/updater/manifest.py. Takes
an unsigned manifest template (JSON with all metadata + artifacts whose `url`/`size`/
`sha256` are filled in or computed here from local files), signs every artifact with
the publisher Ed25519 key (signature over the artifact BYTES), then signs the manifest
canonically, and writes the publishable signed manifest.

The private key comes from --key-file (outside the repo — enforced) or the
VOOL_RELEASE_SIGNING_KEY_HEX environment variable. It is never written anywhere.

Usage:
    python -m installer.update_release \
        --template release.template.json \
        --artifacts-dir dist/0.6.0 \
        --key-file ~/.vool-release-secrets/release-2026-09.private.hex \
        --key-id release-2026-09 \
        --sequence 48 \
        --out dist/0.6.0/manifest.json
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from core.updater.manifest import CHANNELS, SCHEMA, canonical_manifest_bytes

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_private_key(path: Path | None, env: dict[str, str]) -> tuple[str, str]:
    """Return (key_id, private_hex) — refusing keys stored inside the repo."""
    if path is not None:
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.relative_to(REPO_ROOT)
            raise SystemExit("ERROR: the signing key must live OUTSIDE this repository.")
        except ValueError:
            pass
        return resolved.read_text(encoding="utf-8").strip().lower()
    from_env = str(env.get("VOOL_RELEASE_SIGNING_KEY_HEX") or "").strip().lower()
    if not from_env:
        raise SystemExit("ERROR: no signing key (use --key-file outside the repo, or VOOL_RELEASE_SIGNING_KEY_HEX).")
    return from_env


def sign(private_hex: str, payload: bytes) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    return base64.b64encode(key.sign(payload)).decode("ascii")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build_signed_manifest(
    *,
    template_path: Path,
    artifacts_dir: Path,
    private_hex: str,
    key_id: str,
    sequence: int,
    published_at: str,
    channel: str,
    version: str,
    minimum_compatible: str,
    notes: str,
    build_commit: str = "",
) -> dict:
    template: dict = {}
    if template_path and Path(template_path).exists():
        template = json.loads(Path(template_path).read_text(encoding="utf-8"))
    artifacts = template.get("artifacts") or {}
    resolved: dict[str, dict] = {}
    for platform, entry in artifacts.items():
        local = Path(entry.get("path") or "")
        if not local.is_absolute():
            local = (artifacts_dir / local.name) if artifacts_dir else local
        blob = local.read_bytes()
        resolved[str(platform)] = {
            "url": str(entry.get("url") or ""),
            "size": len(blob),
            "sha256": sha256_of(local),
            "signature": sign(private_hex, blob),
        }
    if not resolved:
        raise SystemExit("ERROR: template declares no artifacts.")
    manifest = {
        "schema": SCHEMA,
        "product": "vool",
        "channel": channel or str(template.get("channel") or "stable"),
        "version": version or str(template.get("version") or ""),
        "sequence": int(sequence),
        "published_at": published_at,
        "minimum_compatible": minimum_compatible or str(template.get("minimum_compatible") or "0.0.0"),
        "notes": notes or str(template.get("notes") or ""),
        "artifacts": resolved,
    }
    pinned_commit = str(build_commit or template.get("build_commit") or "").strip().lower()
    if pinned_commit:
        manifest["build_commit"] = pinned_commit
    if not manifest["version"]:
        raise SystemExit("ERROR: --version (or template version) is required.")
    manifest["signature"] = {"key_id": key_id, "sig": sign(private_hex, canonical_manifest_bytes(manifest))}
    return manifest


def main(argv: list[str] | None = None) -> int:
    # C15: the ONE bounded unattended preflight before any signing/release step.
    from core.unattended_preflight import preflight

    preflight("installer.update_release")
    parser = argparse.ArgumentParser(prog="vool-sign-release")
    parser.add_argument("--template", default="", help="unsigned manifest template JSON")
    parser.add_argument("--artifacts-dir", default="", help="directory holding the artifact files")
    parser.add_argument("--key-file", default="", help="publisher PRIVATE key file (outside the repo)")
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--sequence", type=int, required=True, help="monotonic per-channel sequence (anti-replay)")
    parser.add_argument("--version", default="")
    parser.add_argument("--channel", default="stable", choices=CHANNELS)
    parser.add_argument(
        "--build-commit",
        default="",
        help="exact 40-hex checkout SHA the artifact was built from (pinned into the signed bytes; "
        "the restarted app must report it on /healthz before the update finalizes)",
    )
    parser.add_argument("--minimum-compatible", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--out", required=True, help="output path for the signed manifest")
    args = parser.parse_args(argv)

    import os

    private_hex = load_private_key(Path(args.key_file) if args.key_file else None, dict(os.environ))
    manifest = build_signed_manifest(
        template_path=Path(args.template) if args.template else None,
        artifacts_dir=Path(args.artifacts_dir) if args.artifacts_dir else None,
        private_hex=private_hex,
        key_id=args.key_id,
        sequence=args.sequence,
        published_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        channel=args.channel,
        version=args.version,
        minimum_compatible=args.minimum_compatible,
        notes=args.notes,
        build_commit=args.build_commit,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"signed manifest for {manifest['version']} (sequence {manifest['sequence']}) → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
