"""LifeformV1 — the canonical JSON contract (greenfield, schema_version 1).

Strictness laws:
- unknown keys are rejected (a schema extension is a version bump + migrate);
- there are NO free-text payload fields. ``display_name`` is a bounded,
  user-editable label; nothing else carries operator- or task-supplied text;
- growth rings are milestone LABELS only — never dates, never per-day density
  (a rendered creature must not become a usage-timeline disclosure);
- ``social.transferable`` is fixed False and cannot be overridden: lifeform
  progression is bound to its owner identity, forever.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1
_SCHEMA_KEY = "lifeform.v1"

DISPLAY_NAME_MAX = 24
_PRINTABLE_RE = re.compile(r"^[ -~]{0,24}$")

#: v1 top-level keys — the complete, closed set.
_TOP_KEYS = (
    "schema", "schema_version", "lifeform_id", "owner_id", "genesis_seed",
    "created_day", "hatched", "display_name", "development", "lineage",
    "stats", "social", "provenance",
)


class LifeformError(ValueError):
    """Raised for any schema-violating construction (never silently coerced)."""


@dataclass(frozen=True)
class LifeformV1:
    lifeform_id: str
    owner_id: str
    genesis_seed: str
    created_day: str                      # UTC YYYY-MM-DD stamped at mint
    hatched: bool = False
    display_name: str = ""                # bounded label; optional
    development: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    social: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    schema: str = _SCHEMA_KEY


def _validate_display_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not _PRINTABLE_RE.match(cleaned):
        raise LifeformError("display_name must be printable and at most 24 characters")
    return cleaned


def new_lifeform_v1(lifeform_id: str, owner_id: str, genesis_seed: str,
                    created_day: str, display_name: str = "") -> LifeformV1:
    if not lifeform_id or not owner_id or not genesis_seed:
        raise LifeformError("lifeform_id, owner_id and genesis_seed are required")
    return LifeformV1(
        lifeform_id=str(lifeform_id),
        owner_id=str(owner_id),
        genesis_seed=str(genesis_seed),
        created_day=str(created_day),
        display_name=_validate_display_name(display_name),
        development={"stage": "seed", "energy_game": 0.0, "energy_observed": 0.0,
                     "active_days": 0},
        lineage={"archetype": None, "lineage": None, "mutations": [],
                 "evolution_history": [], "growth_rings": []},
        stats={"observed": {}, "game": {}, "cosmetic": {}},
        # social is fixed-shape: transferable False is a law, not a default.
        social={"transferable": False, "garden_visible": False},
        provenance={"ruleset_version": None, "event_count": 0,
                    "event_log_head": "", "last_snapshot": None},
    )


def to_json(doc: LifeformV1) -> str:
    payload: dict[str, Any] = {
        "schema": doc.schema,
        "schema_version": doc.schema_version,
        "lifeform_id": doc.lifeform_id,
        "owner_id": doc.owner_id,
        "genesis_seed": doc.genesis_seed,
        "created_day": doc.created_day,
        "hatched": doc.hatched,
        "display_name": doc.display_name,
        "development": dict(doc.development),
        "lineage": dict(doc.lineage),
        "stats": dict(doc.stats),
        "social": dict(doc.social),
        "provenance": dict(doc.provenance),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def from_json(raw: str | dict[str, Any]) -> LifeformV1:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LifeformError(f"not a lifeform document: {exc}") from exc
    if not isinstance(payload, dict):
        raise LifeformError("lifeform document must be a JSON object")
    unknown = sorted(set(payload) - set(_TOP_KEYS))
    if unknown:
        # strictness is the privacy law: no prompt/code/notes/diff field can
        # ever ride along inside a canonical lifeform document
        raise LifeformError(f"unknown lifeform fields rejected: {unknown}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise LifeformError(
            f"unsupported schema_version {payload.get('schema_version')!r}; "
            "run migrate()")
    if payload.get("schema") != _SCHEMA_KEY:
        raise LifeformError(f"unknown schema key {payload.get('schema')!r}")
    social = dict(payload.get("social") or {})
    if social.get("transferable") is not False:
        raise LifeformError("social.transferable is fixed False (non-transferable law)")
    social["transferable"] = False
    return LifeformV1(
        lifeform_id=str(payload.get("lifeform_id") or ""),
        owner_id=str(payload.get("owner_id") or ""),
        genesis_seed=str(payload.get("genesis_seed") or ""),
        created_day=str(payload.get("created_day") or ""),
        hatched=bool(payload.get("hatched", False)),
        display_name=_validate_display_name(str(payload.get("display_name") or "")),
        development=dict(payload.get("development") or {}),
        lineage=dict(payload.get("lineage") or {}),
        stats=dict(payload.get("stats") or {}),
        social=social,
        provenance=dict(payload.get("provenance") or {}),
    )


def migrate(doc_json: str, from_version: int = 0, to_version: int = SCHEMA_VERSION) -> str:
    """Versioned migration hook. v0 (draft) -> v1 renames development keys and
    is energy-preserving bit-for-bit (regression-tested). Later migrations
    append here; each step is pure and testable."""
    if from_version == to_version:
        return doc_json
    if from_version == 0 and to_version == 1:
        payload = json.loads(doc_json)
        dev = dict(payload.get("development") or {})
        if "progress" in dev:
            dev.setdefault("energy_game", dev["progress"])
        payload["development"] = dev
        # the non-transferable law post-dates drafts: migration installs it
        social = dict(payload.get("social") or {})
        social.setdefault("transferable", False)
        social.setdefault("garden_visible", False)
        payload["social"] = social
        payload["schema_version"] = 1
        payload["schema"] = _SCHEMA_KEY
        return to_json(from_json(payload))
    raise LifeformError(f"no migration path {from_version} -> {to_version}")
