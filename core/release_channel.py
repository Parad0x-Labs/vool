from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.runtime_guard import looks_placeholder_text
from core.runtime_paths import config_path


@dataclass(frozen=True)
class ReleaseArtifact:
    platform: str
    role: str
    path: str
    sha256: str | None = None


@dataclass(frozen=True)
class ReleaseManifest:
    channel_name: str
    release_version: str
    protocol_version: int
    schema_generation: int
    minimum_compatible_release: str
    rollout_stage: str
    update_strategy: str
    requires_clean_runtime: bool
    signed_write_required: bool
    notes: str
    artifacts: tuple[ReleaseArtifact, ...]


def load_release_manifest() -> ReleaseManifest:
    candidates = [
        config_path("release", "update_channel.json"),
        config_path("release", "update_channel.sample.json"),
    ]
    for candidate in candidates:
        if candidate.exists():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            artifacts = tuple(
                ReleaseArtifact(
                    platform=str(item["platform"]),
                    role=str(item["role"]),
                    path=str(item["path"]),
                    sha256=str(item.get("sha256") or "") or None,
                )
                for item in list(data.get("artifacts") or [])
            )
            return ReleaseManifest(
                channel_name=str(data["channel_name"]),
                release_version=str(data["release_version"]),
                protocol_version=int(data["protocol_version"]),
                schema_generation=int(data["schema_generation"]),
                minimum_compatible_release=str(data["minimum_compatible_release"]),
                rollout_stage=str(data["rollout_stage"]),
                update_strategy=str(data["update_strategy"]),
                requires_clean_runtime=bool(data.get("requires_clean_runtime", True)),
                signed_write_required=bool(data.get("signed_write_required", True)),
                notes=str(data.get("notes") or ""),
                artifacts=artifacts,
            )
    raise FileNotFoundError("No release manifest found under config/release/")


def release_manifest_warnings() -> list[str]:
    try:
        manifest = load_release_manifest()
    except FileNotFoundError:
        return ["release manifest is missing"]
    warnings: list[str] = []
    if looks_placeholder_text(manifest.release_version):
        warnings.append("release version is still placeholder text")
    if looks_placeholder_text(manifest.minimum_compatible_release):
        warnings.append("minimum compatible release is still placeholder text")
    if manifest.protocol_version < 1:
        warnings.append("protocol_version must be >= 1")
    if manifest.schema_generation < 1:
        warnings.append("schema_generation must be >= 1")
    if not manifest.artifacts:
        warnings.append("release manifest does not declare any artifacts")
    for artifact in manifest.artifacts:
        if looks_placeholder_text(artifact.path):
            warnings.append(f"artifact path for {artifact.platform}/{artifact.role} is placeholder text")
        if not str(artifact.sha256 or "").strip():
            warnings.append(f"artifact sha256 for {artifact.platform}/{artifact.role} is missing")
    return warnings


def release_manifest_snapshot() -> dict[str, Any]:
    manifest = load_release_manifest()
    return {
        "channel_name": manifest.channel_name,
        "release_version": manifest.release_version,
        "protocol_version": manifest.protocol_version,
        "schema_generation": manifest.schema_generation,
        "minimum_compatible_release": manifest.minimum_compatible_release,
        "rollout_stage": manifest.rollout_stage,
        "update_strategy": manifest.update_strategy,
        "requires_clean_runtime": manifest.requires_clean_runtime,
        "signed_write_required": manifest.signed_write_required,
        "notes": manifest.notes,
        "artifacts": [artifact.__dict__ for artifact in manifest.artifacts],
        "warnings": release_manifest_warnings(),
    }
