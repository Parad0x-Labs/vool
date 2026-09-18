from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from storage.db import get_connection

ProviderSourceType = Literal["local_path", "http", "subprocess"]
WeightLocation = Literal["external", "user-supplied", "bundled"]
AdapterType = Literal[
    "openai_compatible",
    "local_subprocess",
    "local_model_path",
    "optional_transformers",
    "peft_lora_adapter",
    "local_qwen_provider",
    "cloud_fallback_provider",
    "usepod",
]

_CANONICAL_CAPABILITIES = {
    "summarize",
    "classify",
    "format",
    "extract",
    "code_basic",
    "code_complex",
    "long_context",
    "structured_json",
    "tool_intent",
    "multimodal",
}

_LEGACY_CAPABILITY_ALIASES = {
    "summarization": "summarize",
    "classification": "classify",
    "candidate_shard_generation": "extract",
    "normalization_assist": "format",
}


class ModelProviderManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider_name: str = Field(min_length=2, max_length=128)
    model_name: str = Field(min_length=1, max_length=256)
    source_type: ProviderSourceType
    adapter_type: Optional[AdapterType] = None
    license_name: Optional[str] = Field(default=None, max_length=128)
    license_reference: Optional[str] = Field(default=None, max_length=512)
    license_url_or_reference: Optional[str] = Field(default=None, max_length=512)
    weight_location: WeightLocation = "external"
    weights_bundled: Optional[bool] = None
    redistribution_allowed: Optional[bool] = None
    runtime_dependency: str = Field(default="", max_length=256)
    notes: str = Field(default="", max_length=4096)
    capabilities: list[str] = Field(default_factory=list, max_length=24)
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalize_inputs(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        data = dict(values)
        if not data.get("license_reference") and data.get("license_url_or_reference"):
            data["license_reference"] = data.get("license_url_or_reference")
        if not data.get("license_url_or_reference") and data.get("license_reference"):
            data["license_url_or_reference"] = data.get("license_reference")
        if data.get("weights_bundled") is not None and not data.get("weight_location"):
            data["weight_location"] = "bundled" if bool(data["weights_bundled"]) else "external"
        return data

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            clean = str(item).strip().lower()
            if not clean:
                continue
            clean = _LEGACY_CAPABILITY_ALIASES.get(clean, clean)
            if clean not in _CANONICAL_CAPABILITIES:
                raise ValueError(f"Unsupported model capability: {clean}")
            if clean not in seen:
                seen.append(clean)
        return seen

    @model_validator(mode="after")
    def _validate_weight_flags(self) -> ModelProviderManifest:
        if self.weights_bundled is not None:
            expected = "bundled" if self.weights_bundled else self.weight_location
            if self.weights_bundled and self.weight_location != "bundled":
                self.weight_location = "bundled"
            if not self.weights_bundled and self.weight_location == "bundled":
                raise ValueError("weights_bundled=false conflicts with weight_location='bundled'")
            _ = expected
        return self

    @property
    def provider_id(self) -> str:
        return f"{self.provider_name}:{self.model_name}"

    @property
    def resolved_license_reference(self) -> str | None:
        return self.license_reference or self.license_url_or_reference

    @property
    def weights_are_bundled(self) -> bool:
        if self.weights_bundled is not None:
            return bool(self.weights_bundled)
        return self.weight_location == "bundled"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_columns(conn: Any) -> set[str]:
    rows = conn.execute("PRAGMA table_info(model_provider_manifests)").fetchall()
    return {str(row[1]) for row in rows}


def _add_column_if_missing(conn: Any, name: str, definition: str) -> None:
    columns = _table_columns(conn)
    if name not in columns:
        conn.execute(f"ALTER TABLE model_provider_manifests ADD COLUMN {name} {definition}")


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_provider_manifests (
                provider_name TEXT NOT NULL,
                model_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                adapter_type TEXT,
                license_name TEXT,
                license_url_or_reference TEXT,
                weight_location TEXT NOT NULL DEFAULT 'external',
                redistribution_allowed INTEGER,
                runtime_dependency TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                capabilities_json TEXT NOT NULL DEFAULT '[]',
                runtime_config_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (provider_name, model_name)
            )
            """
        )
        _add_column_if_missing(conn, "runtime_dependency", "TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_model_provider_manifests_enabled
            ON model_provider_manifests(enabled, provider_name, model_name)
            """
        )
        conn.commit()
    finally:
        conn.close()


def upsert_provider_manifest(manifest: ModelProviderManifest) -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO model_provider_manifests (
                provider_name, model_name, source_type, adapter_type, license_name,
                license_url_or_reference, weight_location, redistribution_allowed, runtime_dependency, notes,
                capabilities_json, runtime_config_json, metadata_json, enabled,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE(
                    (SELECT created_at FROM model_provider_manifests WHERE provider_name = ? AND model_name = ?),
                    ?
                ),
                ?
            )
            """,
            (
                manifest.provider_name,
                manifest.model_name,
                manifest.source_type,
                manifest.adapter_type,
                manifest.license_name,
                manifest.resolved_license_reference,
                manifest.weight_location,
                None if manifest.redistribution_allowed is None else int(bool(manifest.redistribution_allowed)),
                manifest.runtime_dependency,
                manifest.notes,
                json.dumps(manifest.capabilities, sort_keys=True),
                json.dumps(manifest.runtime_config, sort_keys=True),
                json.dumps(manifest.metadata, sort_keys=True),
                int(bool(manifest.enabled)),
                manifest.provider_name,
                manifest.model_name,
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_provider_manifests(*, enabled_only: bool = False, limit: int = 256) -> list[ModelProviderManifest]:
    _init_table()
    conn = get_connection()
    try:
        where = "WHERE enabled = 1" if enabled_only else ""
        rows = conn.execute(
            f"""
            SELECT *
            FROM model_provider_manifests
            {where}
            ORDER BY provider_name ASC, model_name ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_manifest(dict(row)) for row in rows]
    finally:
        conn.close()


def get_provider_manifest(provider_name: str, model_name: str) -> ModelProviderManifest | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM model_provider_manifests
            WHERE provider_name = ? AND model_name = ?
            LIMIT 1
            """,
            (provider_name, model_name),
        ).fetchone()
        return _row_to_manifest(dict(row)) if row else None
    finally:
        conn.close()


def set_provider_manifest_enabled(
    provider_name: str,
    model_name: str,
    *,
    enabled: bool,
    metadata_updates: dict[str, Any] | None = None,
) -> ModelProviderManifest | None:
    manifest = get_provider_manifest(provider_name, model_name)
    if manifest is None:
        return None
    payload = manifest.model_dump(mode="python")
    payload["enabled"] = bool(enabled)
    if metadata_updates:
        merged_metadata = dict(payload.get("metadata") or {})
        merged_metadata.update(dict(metadata_updates))
        payload["metadata"] = merged_metadata
    updated = ModelProviderManifest.model_validate(payload)
    upsert_provider_manifest(updated)
    return updated


def manifests_missing_license_metadata(*, enabled_only: bool = False) -> list[ModelProviderManifest]:
    out: list[ModelProviderManifest] = []
    for manifest in list_provider_manifests(enabled_only=enabled_only):
        if not str(manifest.license_name or "").strip() or not str(manifest.resolved_license_reference or "").strip():
            out.append(manifest)
    return out


def load_provider_manifest_file(path: str | Path) -> list[ModelProviderManifest]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("providers", [raw])
    if not isinstance(raw, list):
        raise ValueError("Provider manifest file must contain an object or a list of objects.")
    return [ModelProviderManifest.model_validate(item) for item in raw]


def _row_to_manifest(row: dict[str, Any]) -> ModelProviderManifest:
    weight_location = row.get("weight_location") or "external"
    return ModelProviderManifest.model_validate(
        {
            "provider_name": row["provider_name"],
            "model_name": row["model_name"],
            "source_type": row["source_type"],
            "adapter_type": row.get("adapter_type"),
            "license_name": row.get("license_name"),
            "license_reference": row.get("license_url_or_reference"),
            "license_url_or_reference": row.get("license_url_or_reference"),
            "weight_location": weight_location,
            "weights_bundled": bool(weight_location == "bundled"),
            "redistribution_allowed": None
            if row.get("redistribution_allowed") is None
            else bool(row.get("redistribution_allowed")),
            "runtime_dependency": row.get("runtime_dependency") or "",
            "notes": row.get("notes") or "",
            "capabilities": json.loads(row.get("capabilities_json") or "[]"),
            "runtime_config": json.loads(row.get("runtime_config_json") or "{}"),
            "metadata": json.loads(row.get("metadata_json") or "{}"),
            "enabled": bool(row.get("enabled", 1)),
        }
    )
