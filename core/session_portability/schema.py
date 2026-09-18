"""The typed bundle schema: one version, one digest law, forward-only migration.

The payload is canonical JSON (sorted keys, tight separators) so the same content always
produces the same bytes and the same `bundle_id`. `exported_at` is deliberately OUTSIDE the
digest: the bundle's identity is the identity of its CONTENT, not of the moment it was written.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

SCHEMA_NAME = "vool.session_bundle"
CURRENT_SCHEMA_VERSION = 1

#: The scope statement every bundle carries. Served conversation records and receipts are the
#: cargo; the model's hidden internal reasoning is not part of a session bundle and no part of
#: this format claims it.
SCOPE_NOTE = (
    "Served conversation records, receipts and evidence references only. "
    "Model-internal hidden reasoning is not part of this bundle."
)

#: Evidence imported from a bundle is history. These are the exact reader-facing marks the
#: shared observation reader (`core.observation_evidence`) treats as non-current.
IMPORTED_EVIDENCE_FRESHNESS_POLICY = "imported_evidence_is_history"
IMPORTED_FRESH_MARK = False
IMPORTED_LIFECYCLE_MARK = "restored"

#: Cumulative pack bounds. A bundle is ONE conversation's portable truth, not a home backup;
#: over-bound exports and imports refuse with BUNDLE_LIMIT_EXCEEDED.
BOUNDS: dict[str, int] = {
    "turns": 2000,
    "dialogue_turns": 4000,
    "summaries": 500,
    "obligation_sets": 500,
    "tool_receipts": 5000,
    "session_events": 5000,
    "embedded_files": 200,
    "embedded_total_bytes": 64 * 1024 * 1024,
    "profile_items": 200,
    "finalizations": 2000,
    "model_provider_records": 5000,
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def digest_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def bundle_digest(payload: dict[str, Any]) -> str:
    """The content identity of a bundle: the canonical digest of the payload minus the identity
    field itself and the wall-clock export stamp."""
    trimmed = {k: v for k, v in payload.items() if k not in ("bundle_id", "exported_at")}
    return digest_of(trimmed)


def new_bundle_id(payload: dict[str, Any]) -> str:
    return bundle_digest(payload)


@dataclass(frozen=True)
class ExportCounts:
    turns: int = 0
    summaries: int = 0
    obligation_sets: int = 0
    tool_receipts: int = 0
    session_events: int = 0
    evidence_refs: int = 0
    embedded_files: int = 0
    profile_items: int = 0
    finalizations: int = 0
    model_provider_records: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "turns": self.turns,
            "summaries": self.summaries,
            "obligation_sets": self.obligation_sets,
            "tool_receipts": self.tool_receipts,
            "session_events": self.session_events,
            "evidence_refs": self.evidence_refs,
            "embedded_files": self.embedded_files,
            "profile_items": self.profile_items,
            "finalizations": self.finalizations,
            "model_provider_records": self.model_provider_records,
        }


@dataclass(frozen=True)
class MigrationStep:
    """One forward migration `from_version -> from_version+1`, mutating the payload in place."""

    from_version: int
    note: str
    apply: Any = field(default=None)  # Callable[[dict], None]


#: Registered forward migrations. v0 (pre-release shape): turns lived under `items`, and the
#: receipts/evidence envelopes did not exist yet.
MIGRATIONS: list[MigrationStep] = [
    MigrationStep(
        from_version=0,
        note="v0->v1: `items` renamed `turns`; receipts/evidence envelopes added empty.",
        apply=lambda payload: _migrate_v0_to_v1(payload),
    ),
]


def _migrate_v0_to_v1(payload: dict[str, Any]) -> None:
    if "turns" not in payload and isinstance(payload.get("items"), list):
        payload["turns"] = payload.pop("items")
    payload.setdefault("receipts", {"tool_receipts": [], "session_events": []})
    payload.setdefault(
        "evidence",
        {"references": [], "embedded": [], "freshness_policy": IMPORTED_EVIDENCE_FRESHNESS_POLICY},
    )
    payload.setdefault("model_provider", [])
    payload.setdefault("profile_refs", {"format": "vool.operator_profile.v1", "items": []})
    payload.setdefault("attachment_metadata", [])


def migrate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Bring an older payload to CURRENT_SCHEMA_VERSION. Returns (payload, migrated_from).

    `migrated_from` is -1 when no migration ran. A NEWER-than-current schema raises through the
    caller (the api seam maps it to BUNDLE_SCHEMA_TOO_NEW) — forward-only, never guessed.
    """
    version = int(payload.get("schema_version") or 0)
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(f"bundle schema {version} is newer than this runtime understands")
    migrated_from = -1
    while version < CURRENT_SCHEMA_VERSION:
        step = next((m for m in MIGRATIONS if m.from_version == version), None)
        if step is None or step.apply is None:
            raise ValueError(f"no migration registered from schema version {version}")
        step.apply(payload)
        payload["schema_version"] = version + 1
        version += 1
        migrated_from = migrated_from if migrated_from != -1 else step.from_version
    return payload, migrated_from
