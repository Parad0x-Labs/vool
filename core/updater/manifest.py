"""The signed platform-neutral release manifest (schema v1) and its verifier.

One document describes a release for every platform — macOS today, Windows/Linux
installers later, sharing this exact contract. It is signed by the publisher's Ed25519
key over its canonical JSON form (all fields except `signature`, sorted keys, compact
separators), and each artifact entry additionally carries an Ed25519 signature over the
artifact BYTES, so a hostile mirror cannot substitute the payload even when it can
re-serve a valid old manifest.

Verification is fail-closed and ordered so the strongest signal wins:

    malformed → unsigned → no trusted publisher → unsupported schema
    → unknown signing key → signature mismatch → invalid fields → published in the future

Every refusal is a typed `ManifestReason` with a plain-language message; nothing raises
out of `parse_and_verify_manifest`.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from core.updater.trust import TrustedPublishers
from core.updater.versions import Semver, parse_semver

SCHEMA = "vool.update.manifest.v1"
PRODUCT = "vool"
#: The release channels a manifest may name, explicit and exhaustive. A manifest is
#: served to apps configured for the SAME channel only. `beta` is retained so manifests
#: signed before `preview` was introduced stay verifiable; new releases name one of
#: stable / preview / developer.
CHANNELS = ("stable", "beta", "preview", "developer")

#: How far into the future a manifest may be dated before we call clock tampering.
MAX_PUBLISHED_AT_SKEW = timedelta(hours=48)

_SIGNATURE_KEY = "signature"


class ManifestReason(Enum):
    OK = "ok"
    MALFORMED = "malformed_manifest"
    UNSIGNED = "unsigned_manifest"
    NO_TRUSTED_PUBLISHER = "no_trusted_publisher"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    UNKNOWN_SIGNING_KEY = "unknown_signing_key"
    SIGNATURE_MISMATCH = "signature_mismatch"
    INVALID = "invalid_manifest"
    PUBLISHED_IN_FUTURE = "published_in_future"

    def plain_message(self) -> str:
        return {
            ManifestReason.OK: "The update information checked out.",
            ManifestReason.MALFORMED: "The update information arrived damaged, so it was ignored.",
            ManifestReason.UNSIGNED: "The update is missing its publisher's signature, so it was ignored.",
            ManifestReason.NO_TRUSTED_PUBLISHER: (
                "This copy of the app has no publisher key configured, so it cannot confirm where "
                "updates come from — no updates will be offered."
            ),
            ManifestReason.UNSUPPORTED_SCHEMA: "The update uses a format this version of the app doesn't understand.",
            ManifestReason.UNKNOWN_SIGNING_KEY: (
                "The update was signed with a key this app doesn't trust, so it was ignored."
            ),
            ManifestReason.SIGNATURE_MISMATCH: (
                "The update's signature didn't match its contents, so it was ignored — that's what "
                "should happen when something has been altered."
            ),
            ManifestReason.INVALID: "The update information is incomplete or wrong, so it was ignored.",
            ManifestReason.PUBLISHED_IN_FUTURE: (
                "The update is dated in the future, which usually means a clock problem — it was ignored."
            ),
        }[self]


@dataclass(frozen=True)
class ArtifactEntry:
    platform: str
    url: str
    size: int
    sha256: str
    signature_b64: str

    @classmethod
    def from_dict(cls, platform: str, data: dict[str, Any]) -> ArtifactEntry:
        return cls(
            platform=str(platform),
            url=str(data.get("url") or ""),
            size=int(data.get("size") or 0),
            sha256=str(data.get("sha256") or "").strip().lower(),
            signature_b64=str(data.get("signature") or "").strip(),
        )


@dataclass(frozen=True)
class VerifiedManifest:
    channel: str
    version: str
    sequence: int
    published_at: datetime
    minimum_compatible: str
    notes: str
    key_id: str
    artifacts: dict[str, ArtifactEntry] = field(default_factory=dict)
    #: The exact checkout SHA this artifact was built from, when release engineering
    #: pins one. Part of the signed canonical bytes (every field except `signature`
    #: is committed), so a mismatch here is a signature failure like any other. Empty
    #: on manifests that predate the field; the health gate then falls back to
    #: matching the manifest `version`.
    build_commit: str = ""

    @property
    def expected_health_identity(self) -> str:
        """The exact string the restarted app must report: the pinned build commit
        when present (the exact installed SHA), else the manifest version."""
        return self.build_commit or self.version

    def artifact_for(self, platform_key: str) -> ArtifactEntry | None:
        return self.artifacts.get(platform_key)

    @property
    def semver(self) -> Semver:
        return parse_semver(self.version)

    @property
    def canonical_sha256(self) -> str:
        return hashlib.sha256(canonical_manifest_bytes(self._source)).hexdigest()

    _source: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class ManifestVerification:
    ok: bool
    reason: ManifestReason
    manifest: VerifiedManifest | None = None

    @property
    def plain_message(self) -> str:
        return self.reason.plain_message()


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """The exact bytes a signature commits to: every field except `signature`, sorted,
    compact. Whitespace and key order in transit are irrelevant; any VALUE change is not."""
    payload = {k: v for k, v in manifest.items() if k != _SIGNATURE_KEY}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _parse_datetime(text: str) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_hex(text: str, length: int) -> bool:
    return len(text) == length and all(c in "0123456789abcdef" for c in text.lower())


def _validate_fields(data: dict[str, Any]) -> VerifiedManifest | None:
    if data.get("product") != PRODUCT:
        return None
    channel = data.get("channel")
    if channel not in CHANNELS:
        return None
    try:
        parse_semver(str(data.get("version") or ""))
        minimum_compatible = str(data.get("minimum_compatible") or "0.0.0")
        if minimum_compatible:
            parse_semver(minimum_compatible)
    except ValueError:
        return None
    sequence = data.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        return None
    build_commit = str(data.get("build_commit") or "").strip().lower()
    if build_commit and not _is_hex(build_commit, 40):
        return None
    published = _parse_datetime(str(data.get("published_at") or ""))
    if published is None:
        return None
    raw_artifacts = data.get("artifacts")
    if not isinstance(raw_artifacts, dict) or not raw_artifacts:
        return None
    artifacts: dict[str, ArtifactEntry] = {}
    for platform, entry in raw_artifacts.items():
        if not isinstance(entry, dict):
            return None
        parsed = ArtifactEntry.from_dict(str(platform), entry)
        if not parsed.url or not parsed.url.startswith(("http://", "https://")):
            return None
        if parsed.size <= 0:
            return None
        if not _is_hex(parsed.sha256, 64):
            return None
        if not parsed.signature_b64:
            return None
        artifacts[parsed.platform] = parsed
    return VerifiedManifest(
        channel=str(channel),
        version=str(data.get("version")),
        sequence=int(sequence),
        published_at=published,
        minimum_compatible=str(data.get("minimum_compatible") or "0.0.0"),
        notes=str(data.get("notes") or ""),
        key_id="",
        artifacts=artifacts,
        build_commit=build_commit,
        _source=data,
    )


def verify_ed25519(public_key_hex: str, payload: bytes, signature_b64: str) -> bool:
    """Ed25519 verify with the pinned public key; any encoding or crypto error is False."""
    key_hex = str(public_key_hex or "").strip().lower()
    if len(key_hex) != 64:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
        public_key.verify(base64.b64decode(str(signature_b64 or ""), validate=True), payload)
        return True
    except (InvalidSignature, ValueError, TypeError, binascii.Error):
        return False


def parse_and_verify_manifest(
    raw: bytes | str, trust: TrustedPublishers, *, now: datetime | None = None
) -> ManifestVerification:
    """Verify and parse a signed manifest. Never raises; every failure is typed."""
    now = now or datetime.now(timezone.utc)
    try:
        if isinstance(raw, bytes):
            data = json.loads(raw.decode("utf-8"))
        else:
            data = json.loads(str(raw))
    except (ValueError, UnicodeDecodeError):
        return ManifestVerification(False, ManifestReason.MALFORMED)
    if not isinstance(data, dict):
        return ManifestVerification(False, ManifestReason.MALFORMED)

    signature = data.get(_SIGNATURE_KEY)
    if not isinstance(signature, dict) or not str(signature.get("sig") or ""):
        return ManifestVerification(False, ManifestReason.UNSIGNED)
    if not trust.is_configured:
        return ManifestVerification(False, ManifestReason.NO_TRUSTED_PUBLISHER)
    if data.get("schema") != SCHEMA:
        return ManifestVerification(False, ManifestReason.UNSUPPORTED_SCHEMA)

    key_id = str(signature.get("key_id") or "")
    if not trust.is_known_key(key_id):
        return ManifestVerification(False, ManifestReason.UNKNOWN_SIGNING_KEY)
    if not verify_ed25519(trust.public_key_hex(key_id), canonical_manifest_bytes(data), str(signature.get("sig"))):
        return ManifestVerification(False, ManifestReason.SIGNATURE_MISMATCH)

    parsed = _validate_fields(data)
    if parsed is None:
        return ManifestVerification(False, ManifestReason.INVALID)
    if parsed.published_at > now + MAX_PUBLISHED_AT_SKEW:
        return ManifestVerification(False, ManifestReason.PUBLISHED_IN_FUTURE)

    verified = VerifiedManifest(
        channel=parsed.channel,
        version=parsed.version,
        sequence=parsed.sequence,
        published_at=parsed.published_at,
        minimum_compatible=parsed.minimum_compatible,
        notes=parsed.notes,
        key_id=key_id,
        artifacts=parsed.artifacts,
        build_commit=parsed.build_commit,
        _source=data,
    )
    return ManifestVerification(True, ManifestReason.OK, manifest=verified)


def verify_artifact_bytes(blob: bytes, entry: ArtifactEntry, public_key_hex: str) -> bool:
    """Verify an artifact's own Ed25519 signature (signed over the raw artifact bytes)."""
    return verify_ed25519(public_key_hex, blob, entry.signature_b64)


__all__ = [
    "CHANNELS",
    "SCHEMA",
    "ArtifactEntry",
    "ManifestReason",
    "ManifestVerification",
    "VerifiedManifest",
    "canonical_manifest_bytes",
    "parse_and_verify_manifest",
    "verify_artifact_bytes",
    "verify_ed25519",
]
