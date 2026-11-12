"""Canonical skill identity — the ONE parser-owned interpretation of skill authenticity.

Transplanted from the Pass #1 experiment (exp/skill-system-pass1) and extended with
the two-digest model:

    PACKAGE_DIGEST              sha256 over the EXACT raw source bytes of SKILL.md
    EFFECTIVE_INSTRUCTION_DIGEST  sha256 over the canonical parsed form:
                                  sorted manifest (minus digest keys) + comment-stripped body

Both exist because they authenticate DIFFERENT things:

- Two packages differing only by hidden HTML comments or whitespace have the same
  effective instructions but different packages. Pinning a task to "what the model
  will be told" uses the EFFECTIVE digest; detecting that a source package mutated
  (S6) requires the PACKAGE digest.
- Identity verification (`content-hash` declared vs computed) is over the EFFECTIVE
  representation: semver + different visible bytes can never share an identity.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = "2"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PUBLISHER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_HIDDEN_BLOCK_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class SkillIdentityError(ValueError):
    """Raised when a skill cannot form a valid identity or fails tamper checks."""


class IdentityConflict(ValueError):
    """The same canonical ref appeared with different effective instruction bytes."""


class LegacyShadowError(ValueError):
    """A LEGACY_UNVERIFIED skill attempted to supersede a VERIFIED one."""


# The ONLY two trust states a parsed skill can carry. identity=None can never mean
# verified: parse_skill assigns VERIFIED exclusively when a canonical identity was
# declared AND passed tamper verification.
VERIFIED = "VERIFIED"
LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"


def satisfies_pin(verification: str, identity: "SkillIdentity | None", stored_pin: dict) -> bool:
    """Pin satisfaction requires an actual VERIFIED canonical identity.

    A legacy (identity=None) skill structurally cannot satisfy a pin: it has no
    ref and no hash. This makes sabotage S1 unrepresentable rather than avoided.
    """
    if verification != VERIFIED or identity is None:
        return False
    return (
        stored_pin.get("ref") == identity.ref
        and stored_pin.get("hash") == identity.digests.effective_instructions
    )


def assert_no_identity_conflict(seen: dict[str, str], ref: str, effective_digest: str) -> None:
    """Same publisher/id@version twice with different effective bytes => hard conflict.

    Scope or load order NEVER chooses between them silently (Pass #3 law C).
    Identical effective bytes under one ref remain fine: package-byte differences
    with identical effective instructions are exactly the two-digest law of Pass #2.
    """
    prior = seen.get(ref)
    if prior is not None and prior != effective_digest:
        raise IdentityConflict(
            f"{ref} loaded with two different effective digests "
            f"({prior[:12]} vs {effective_digest[:12]}); refusing silent selection")
    seen[ref] = effective_digest


@dataclass(frozen=True)
class SkillDigests:
    package: str                 # exact raw bytes
    effective_instructions: str  # canonical parsed form


def strip_hidden_blocks(body: str) -> tuple[str, tuple[str, ...]]:
    """Remove HTML comments from a body BEFORE any hashing or injection.

    Returns (clean_body, extracted_blocks). Extracted blocks are recorded for
    provenance/diffing but are NEVER instruction-bearing.
    """
    blocks = tuple(m.group(0) for m in _HIDDEN_BLOCK_RE.finditer(body))
    return _HIDDEN_BLOCK_RE.sub("", body).strip(), blocks


def compute_effective_digest(manifest: dict, clean_body: str) -> str:
    canon = {k: v for k, v in sorted(manifest.items())
             if k not in ("content-hash", "package-digest")}
    payload = json.dumps(canon, sort_keys=True, separators=(",", ":")) + "\n\x00\n" + clean_body
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_package_digest(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def compute_digests(manifest: dict, raw_body: str) -> SkillDigests:
    clean, _ = strip_hidden_blocks(raw_body)
    return SkillDigests(
        package=compute_package_digest(raw_body.encode("utf-8")),
        effective_instructions=compute_effective_digest(manifest, clean),
    )


def build_identity(manifest: dict, digests: SkillDigests) -> "SkillIdentity":
    """Validate manifest fields and bind them to computed digests."""
    publisher = str(manifest.get("publisher", "")).strip().lower()
    skill_id = str(manifest.get("id", "")).strip().lower()
    version = str(manifest.get("version", "")).strip()
    if not _PUBLISHER_RE.match(publisher):
        raise SkillIdentityError(f"invalid publisher: {publisher!r}")
    if not _ID_RE.match(skill_id):
        raise SkillIdentityError(f"invalid skill id: {skill_id!r}")
    if not _SEMVER_RE.match(version):
        raise SkillIdentityError(f"version must be semver X.Y.Z, got {version!r}")
    declared = str(manifest.get("content-hash", "")).strip().lower()
    if declared and declared != digests.effective_instructions:
        raise SkillIdentityError(
            f"effective-instruction hash mismatch: declared {declared[:12]}, "
            f"computed {digests.effective_instructions[:12]}")
    return SkillIdentity(publisher, skill_id, version, digests)


@dataclass(frozen=True)
class SkillIdentity:
    publisher: str
    skill_id: str
    version: str
    digests: SkillDigests

    @property
    def ref(self) -> str:
        return f"{self.publisher}/{self.skill_id}@{self.version}"

    @property
    def short(self) -> str:
        return f"{self.ref}#{self.digests.effective_instructions[:12]}"

    def __str__(self) -> str:
        return self.short


def parse_skill_md(path: Path) -> tuple[dict, str, bytes]:
    """Split one SKILL.md into (manifest, body, raw_bytes). Raises on malformed frontmatter."""
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise SkillIdentityError(f"{path}: missing YAML frontmatter")
    import yaml

    loaded = yaml.safe_load(m.group(1))
    if not isinstance(loaded, dict):
        raise SkillIdentityError(f"{path}: frontmatter is not a mapping")
    return loaded, (m.group(2) or "").strip(), raw
