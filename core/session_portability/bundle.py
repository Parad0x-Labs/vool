"""The bundle file format: a zip of signed manifest + canonical payload + embedded files,
optionally sealed in an AES-256-GCM envelope.

Integrity chain: the sha256 manifest covers the payload and every attachment; the Ed25519
node signature (signature.json) covers the manifest bytes; a passphrase envelope binds the
whole container with GCM. Editing content AND recomputing hashes fails the signature; a
re-signed forgery carries a foreign key and is refused without explicit operator confirmation.

Archive-parsing law: structural dangers — duplicate or normalization-colliding names, paths
outside the member allowlist (which subsumes absolute paths, `..` and backslash traversal),
symlink/special members, unsupported compression, zip-encryption flags, excessive member
count, per-member size, total expanded size and compression-ratio bombs — are refused BEFORE
any member body is read. Members are read to memory only; nothing is ever extracted to a path.
"""

from __future__ import annotations

import hashlib
import io
import json
import secrets
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

from core.session_portability.schema import SCHEMA_NAME, bundle_digest, canonical_json

MANIFEST_MEMBER = "manifest.json"
PAYLOAD_MEMBER = "bundle.json"
ATTACHMENT_PREFIX = "attachments/"
ZIP_DATE_TIME = (2026, 1, 1, 0, 0, 0)  # fixed stamp: the same content writes the same bytes

ENVELOPE_FORMAT = "vool.session_bundle.encrypted.v1"
KDF_NAME = "PBKDF2-HMAC-SHA256"
KDF_ITERATIONS = 200_000
KDF_ITERATIONS_MIN = 10_000
KDF_ITERATIONS_MAX = 2_000_000
CIPHER_NAME = "AES-256-GCM"
ENVELOPE_MAX_CIPHERTEXT_BYTES = 256 * 1024 * 1024

#: Archive bounds. Refused before member bodies are read.
ARCHIVE_MAX_MEMBERS = 64
ARCHIVE_MAX_MEMBER_BYTES = 64 * 1024 * 1024
ARCHIVE_MAX_TOTAL_BYTES = 256 * 1024 * 1024
ARCHIVE_MAX_RATIO = 500
ARCHIVE_KNOWN_MEMBERS = frozenset({PAYLOAD_MEMBER, MANIFEST_MEMBER}) | {"signature.json"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _member_name_safe(name: str) -> bool:
    if name in ARCHIVE_KNOWN_MEMBERS:
        return True
    if name.startswith(ATTACHMENT_PREFIX):
        rest = name[len(ATTACHMENT_PREFIX):]
        return bool(rest) and "/" not in rest and "\\" not in rest and ".." not in rest
    return False


def build_manifest(payload_bytes: bytes, attachments: dict[str, bytes]) -> dict[str, Any]:
    entries: dict[str, str] = {PAYLOAD_MEMBER: sha256_bytes(payload_bytes)}
    for path in sorted(attachments):
        entries[path] = sha256_bytes(attachments[path])
    digest_lines = "\n".join(f"{p} {entries[p]}" for p in sorted(entries))
    return {
        "manifest_version": 2,
        "algorithm": "sha256",
        "entries": entries,
        "bundle_digest": hashlib.sha256(digest_lines.encode("utf-8")).hexdigest(),
    }


def write_bundle(
    out_path: Path,
    payload: dict[str, Any],
    attachments: dict[str, bytes],
    *,
    passphrase: str = "",
) -> dict[str, Any]:
    """Write the signed bundle file. Returns {bundle_id, sha256, encrypted, manifest_digest,
    signer_fingerprint, trust_origin}."""
    from core.session_portability import signing

    payload = dict(payload)
    payload["bundle_id"] = bundle_digest(payload)
    payload_bytes = canonical_json(payload)
    manifest = build_manifest(payload_bytes, attachments)
    manifest_bytes = canonical_json(manifest)
    signature = signing.sign_manifest(manifest_bytes)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in (
            (PAYLOAD_MEMBER, payload_bytes),
            (MANIFEST_MEMBER, manifest_bytes),
            (signing.SIGNATURE_MEMBER, canonical_json(signature)),
            *((path, attachments[path]) for path in sorted(attachments)),
        ):
            info = zipfile.ZipInfo(filename=name, date_time=ZIP_DATE_TIME)
            info.external_attr = 0o600 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    raw = buf.getvalue()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if passphrase:
        envelope = seal_envelope(raw, passphrase, manifest["bundle_digest"])
        out_path.write_bytes(envelope)
    else:
        out_path.write_bytes(raw)
    return {
        "bundle_id": payload["bundle_id"],
        "sha256": sha256_bytes(raw),
        "encrypted": bool(passphrase),
        "manifest_digest": manifest["bundle_digest"],
        "signer_fingerprint": signature["signer_fingerprint"],
        "trust_origin": signing.classify_origin(signature),
    }


def seal_envelope(raw: bytes, passphrase: str, manifest_digest: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ITERATIONS
    ).derive(passphrase.encode("utf-8"))
    aad = f"{ENVELOPE_FORMAT}:{manifest_digest}".encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, raw, aad)
    return canonical_json(
        {
            "format": ENVELOPE_FORMAT,
            "cipher": CIPHER_NAME,
            "kdf": {
                "name": KDF_NAME,
                "iterations": KDF_ITERATIONS,
                "salt": salt.hex(),
                "length": 32,
            },
            "nonce": nonce.hex(),
            "aad": aad.decode("utf-8"),
            "ciphertext": ciphertext.hex(),
        }
    )


def _envelope_param_bound(envelope: dict[str, Any]) -> None:
    """Refuse hostile envelope/KDF parameters BEFORE any key derivation: an envelope that
    would burn minutes of CPU per attempt, or that names absurd key material lengths, is
    itself the denial-of-service."""
    from core.session_portability.api import PortabilityRefused

    kdf = envelope.get("kdf") or {}
    if str(kdf.get("name") or "") != KDF_NAME:
        raise PortabilityRefused("BUNDLE_ARCHIVE_UNSAFE", "Unsupported envelope KDF.")
    try:
        iterations = int(kdf.get("iterations"))
        salt = bytes.fromhex(str(kdf.get("salt") or ""))
        nonce = bytes.fromhex(str(envelope.get("nonce") or ""))
        length = int(kdf.get("length"))
        ciphertext = bytes.fromhex(str(envelope.get("ciphertext") or ""))
    except (ValueError, TypeError) as exc:
        raise PortabilityRefused(
            "BUNDLE_ARCHIVE_UNSAFE", "Envelope key material is malformed."
        ) from exc
    if not (KDF_ITERATIONS_MIN <= iterations <= KDF_ITERATIONS_MAX):
        raise PortabilityRefused(
            "BUNDLE_ARCHIVE_UNSAFE",
            f"Envelope KDF iteration count outside the accepted range "
            f"({KDF_ITERATIONS_MIN}-{KDF_ITERATIONS_MAX}).",
        )
    if len(salt) < 8 or len(salt) > 64:
        raise PortabilityRefused("BUNDLE_ARCHIVE_UNSAFE", "Envelope salt length is out of bounds.")
    if len(nonce) != 12:
        raise PortabilityRefused("BUNDLE_ARCHIVE_UNSAFE", "Envelope nonce must be 12 bytes.")
    if length != 32:
        raise PortabilityRefused("BUNDLE_ARCHIVE_UNSAFE", "Envelope key length must be 32 bytes.")
    if len(ciphertext) > ENVELOPE_MAX_CIPHERTEXT_BYTES:
        raise PortabilityRefused(
            "BUNDLE_ARCHIVE_UNSAFE", "Envelope ciphertext exceeds the size bound."
        )


def open_envelope(envelope: dict[str, Any], passphrase: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    from core.session_portability.api import PortabilityRefused

    _envelope_param_bound(envelope)
    kdf = envelope.get("kdf") or {}
    try:
        key = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=int(kdf.get("length") or 32),
            salt=bytes.fromhex(str(kdf.get("salt") or "")),
            iterations=int(kdf.get("iterations") or KDF_ITERATIONS),
        ).derive(passphrase.encode("utf-8"))
        aad = str(envelope.get("aad") or "").encode("utf-8")
        plain = AESGCM(key).decrypt(
            bytes.fromhex(str(envelope.get("nonce") or "")),
            bytes.fromhex(str(envelope.get("ciphertext") or "")),
            aad,
        )
        return plain
    except (InvalidTag, ValueError, TypeError) as exc:
        raise PortabilityRefused(
            "BUNDLE_DECRYPTION_FAILED",
            "The bundle's passphrase did not open it (or the bundle was modified). "
            "Nothing was read.",
        ) from exc


def _precheck_members(infolist: list) -> None:
    """Structural archive safety, decided on ZipInfo metadata BEFORE any body is read."""
    from core.session_portability.api import PortabilityRefused

    if len(infolist) > ARCHIVE_MAX_MEMBERS:
        raise PortabilityRefused(
            "BUNDLE_ARCHIVE_UNSAFE",
            f"Bundle declares {len(infolist)} members, over the bound of {ARCHIVE_MAX_MEMBERS}.",
        )
    seen: dict[str, str] = {}
    total = 0
    for info in infolist:
        name = info.filename
        if not _member_name_safe(name):
            raise PortabilityRefused(
                "BUNDLE_ARCHIVE_UNSAFE",
                f"Bundle member '{name}' is outside the bundle's member allowlist "
                "(absolute paths, traversal, backslashes and unknown names are refused).",
            )
        canonical = unicodedata.normalize("NFC", name).casefold()
        if canonical in seen:
            raise PortabilityRefused(
                "BUNDLE_ARCHIVE_UNSAFE",
                f"Bundle member '{name}' collides with '{seen[canonical]}' "
                "(duplicate or normalization-colliding names are refused).",
            )
        seen[canonical] = name
        if info.flag_bits & 0x1:
            raise PortabilityRefused(
                "BUNDLE_ARCHIVE_UNSAFE",
                f"Bundle member '{name}' carries a zip-encryption flag.",
            )
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise PortabilityRefused(
                "BUNDLE_ARCHIVE_UNSAFE",
                f"Bundle member '{name}' uses an unsupported compression method.",
            )
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type not in (0, 0o100000):  # absent or regular file only
            raise PortabilityRefused(
                "BUNDLE_ARCHIVE_UNSAFE",
                f"Bundle member '{name}' is not a regular file (symlinks and special "
                "members are refused).",
            )
        if info.file_size > ARCHIVE_MAX_MEMBER_BYTES:
            raise PortabilityRefused(
                "BUNDLE_ARCHIVE_UNSAFE",
                f"Bundle member '{name}' declares {info.file_size} bytes, over the "
                f"per-member bound of {ARCHIVE_MAX_MEMBER_BYTES}.",
            )
        total += info.file_size
        if total > ARCHIVE_MAX_TOTAL_BYTES:
            raise PortabilityRefused(
                "BUNDLE_ARCHIVE_UNSAFE",
                f"Bundle's expanded size exceeds {ARCHIVE_MAX_TOTAL_BYTES} bytes.",
            )
        compress_size = max(1, info.compress_size)
        if info.file_size > (1 << 20) and info.file_size // compress_size > ARCHIVE_MAX_RATIO:
            raise PortabilityRefused(
                "BUNDLE_ARCHIVE_UNSAFE",
                f"Bundle member '{name}' has a compression ratio over {ARCHIVE_MAX_RATIO}:1 "
                "(compression bombs are refused).",
            )


def _read_members(raw: bytes) -> dict[str, bytes]:
    from core.session_portability.api import PortabilityRefused

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            _precheck_members(zf.infolist())
            if zf.testzip() is not None:
                raise PortabilityRefused(
                    "BUNDLE_MALFORMED", "The bundle's zip container is corrupt."
                )
            return {name: zf.read(name) for name in zf.namelist()}
    except zipfile.BadZipFile as exc:
        raise PortabilityRefused(
            "BUNDLE_MALFORMED", "This file is not a readable session bundle."
        ) from exc


def verify_members(members: dict[str, bytes]) -> dict[str, Any]:
    """Verify every member against the manifest (tamper), then the signature (authenticity)."""
    from core.session_portability import signing
    from core.session_portability.api import PortabilityRefused

    required = {MANIFEST_MEMBER, PAYLOAD_MEMBER, signing.SIGNATURE_MEMBER}
    if not required.issubset(members):
        missing = sorted(required - set(members))
        if missing == [signing.SIGNATURE_MEMBER]:
            raise PortabilityRefused(
                "BUNDLE_UNSIGNED",
                "This bundle carries no signature; unsigned bundles are refused. "
                "Re-export it with a current VOOL build.",
            )
        raise PortabilityRefused(
            "BUNDLE_MALFORMED",
            f"The bundle is missing required members: {', '.join(sorted(missing))}.",
        )
    try:
        manifest = json.loads(members[MANIFEST_MEMBER])
    except ValueError as exc:
        raise PortabilityRefused("BUNDLE_MALFORMED", "The manifest is not valid JSON.") from exc
    entries = manifest.get("entries") or {}
    for name, recorded in sorted(entries.items()):
        actual = sha256_bytes(members.get(name, b""))
        if actual != recorded:
            raise PortabilityRefused(
                "BUNDLE_TAMPERED",
                f"Bundle member '{name}' does not match its recorded hash — the bundle was "
                "modified or corrupted. Nothing was imported.",
            )
    covered = set(entries) | {MANIFEST_MEMBER, signing.SIGNATURE_MEMBER}
    for name in members:
        if name not in covered:
            raise PortabilityRefused(
                "BUNDLE_TAMPERED",
                f"Bundle member '{name}' is not covered by the manifest. Nothing was imported.",
            )
    digest_lines = "\n".join(f"{p} {entries[p]}" for p in sorted(entries))
    if manifest.get("bundle_digest") != hashlib.sha256(digest_lines.encode("utf-8")).hexdigest():
        raise PortabilityRefused(
            "BUNDLE_TAMPERED", "The manifest's own digest does not match its entries."
        )
    # Authenticity: the signature must cover EXACTLY these manifest bytes. Recomputing the
    # manifest over edited content invalidates it; a re-signed forgery carries a foreign key.
    signature_record = signing.decode_signature_member(members[signing.SIGNATURE_MEMBER])
    if signature_record is None or not signing.verify_signature(
        signature_record, members[MANIFEST_MEMBER]
    ):
        raise PortabilityRefused(
            "BUNDLE_SIGNATURE_INVALID",
            "The bundle's signature does not verify over its manifest — the bundle was "
            "modified or was not signed by the key it names. Nothing was imported.",
        )
    return manifest


def read_bundle(
    path: Path, *, passphrase: str = ""
) -> tuple[dict[str, Any], dict[str, Any], dict[str, bytes], dict[str, Any]]:
    """Open a bundle file. Returns (payload, manifest, attachment members, signature record),
    fully verified: archive safety, member hashes, then signature."""
    from core.session_portability import signing
    from core.session_portability.api import PortabilityRefused

    if not path.is_file():
        raise PortabilityRefused("BUNDLE_PATH_MISSING", f"No bundle file at {path}")
    raw = path.read_bytes()
    stripped = raw.lstrip()
    if stripped.startswith(b"{"):
        # Encrypted envelope.
        try:
            envelope = json.loads(raw)
        except ValueError as exc:
            raise PortabilityRefused("BUNDLE_MALFORMED", "The envelope is not valid JSON.") from exc
        if not passphrase:
            raise PortabilityRefused(
                "BUNDLE_DECRYPTION_FAILED",
                "This bundle is encrypted; a passphrase is required to read it.",
            )
        if envelope.get("format") != ENVELOPE_FORMAT:
            raise PortabilityRefused("BUNDLE_MALFORMED", "Unknown bundle envelope format.")
        raw = open_envelope(envelope, passphrase)

    members = _read_members(raw)
    manifest = verify_members(members)
    try:
        payload = json.loads(members[PAYLOAD_MEMBER])
    except ValueError as exc:
        raise PortabilityRefused("BUNDLE_MALFORMED", "The payload is not valid JSON.") from exc
    attachments = {
        name: data for name, data in members.items() if name.startswith(ATTACHMENT_PREFIX)
    }
    signature_record = signing.decode_signature_member(members[signing.SIGNATURE_MEMBER]) or {}
    return payload, manifest, attachments, signature_record
