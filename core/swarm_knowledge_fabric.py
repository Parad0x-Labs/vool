from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SourceLabel = Literal[
    "watcher-derived",
    "public-bridge-derived",
    "local-only",
    "shared-peer-report",
    "external",
    "merge-derived",
    "future/unsupported",
]
VisibilityLabel = Literal["local_only", "shared", "external"]
FreshnessLabel = Literal["fresh", "stale", "unknown"]
MergeStateLabel = Literal["clean", "conflicted", "merged"]
BranchMergeStateLabel = Literal["open", "merged", "conflicted", "finalized"]

_VISIBILITY_RANK = {"local_only": 0, "shared": 1, "external": 2}
_MERGEABLE_SET_FIELDS = {
    "source_labels",
    "contributor_ids",
    "contributors",
    "artifact_ids",
    "observation_ids",
    "critique_ids",
    "revision_ids",
    "claim_ids",
    "known_peer_ids",
    "task_branch_ids",
    "source_snapshot_ids",
}
_NON_CONFLICT_FIELDS = {
    "freshness",
    "visibility",
    "source_labels",
    "metadata",
    "merge_state",
    "conflict_ids",
    "first_seen_at",
    "last_seen_at",
    "source_snapshot_ids",
    "merged_from_snapshot_ids",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_snapshot_id() -> str:
    return f"snapshot-{uuid.uuid4().hex}"


def create_branch_id(seed: str) -> str:
    clean_seed = str(seed or "").strip() or uuid.uuid4().hex
    digest = hashlib.sha256(clean_seed.encode("utf-8")).hexdigest()[:16]
    return f"branch-{digest}"


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _dedupe_sorted(values: list[str]) -> list[str]:
    clean = {str(item or "").strip() for item in values if str(item or "").strip()}
    return sorted(clean)


def _parse_ts(value: str | None) -> datetime:
    clean = str(value or "").strip()
    if not clean:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    normalized = clean.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _max_timestamp(*values: str | None) -> str:
    clean = [str(item or "").strip() for item in values if str(item or "").strip()]
    if not clean:
        return ""
    return max(clean, key=_parse_ts)


def _latest_wins(existing_timestamp: str, incoming_timestamp: str) -> bool:
    return _parse_ts(incoming_timestamp) >= _parse_ts(existing_timestamp)


def _merge_visibility(existing: VisibilityLabel, incoming: VisibilityLabel) -> VisibilityLabel:
    if _VISIBILITY_RANK.get(str(incoming), 0) >= _VISIBILITY_RANK.get(str(existing), 0):
        return incoming
    return existing


class FreshnessState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: FreshnessLabel = "unknown"
    observed_at: str = ""
    stale_after_seconds: int = 0
    age_seconds: Optional[int] = None


class MergeVersionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "swarm_snapshot_v1"
    merge_version: str = "swarm_merge_v1"
    parent_snapshot_ids: list[str] = Field(default_factory=list)
    merged_from_snapshot_ids: list[str] = Field(default_factory=list)
    merge_cursor: str = ""
    merge_sequence: int = 0


class ConflictValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_snapshot_id: str
    source_label: str = ""
    observed_at: str = ""
    value: Any = None


class ConflictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    registry: Literal["peer_registry", "task_registry", "artifact_registry", "observation_registry", "snapshot"]
    entity_id: str
    field_name: str
    winner_snapshot_id: str = ""
    winner_value: Any = None
    competing_values: list[ConflictValue] = Field(default_factory=list)
    resolution_state: Literal["open", "acknowledged", "resolved"] = "open"
    created_at: str = Field(default_factory=utcnow)
    note: str = ""


class PeerSnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peer_id: str
    agent_id: str = ""
    runtime_session_id: str = ""
    known_peer_ids: list[str] = Field(default_factory=list)
    capability_tags: list[str] = Field(default_factory=list)
    status: str = ""
    summary: str = ""
    source_labels: list[SourceLabel] = Field(default_factory=list)
    freshness: FreshnessState = Field(default_factory=FreshnessState)
    visibility: VisibilityLabel = "shared"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskSnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_branch_id: str = ""
    title: str = ""
    goal: str = ""
    origin_signal: str = ""
    status: str = ""
    contributor_ids: list[str] = Field(default_factory=list)
    source_labels: list[SourceLabel] = Field(default_factory=list)
    freshness: FreshnessState = Field(default_factory=FreshnessState)
    visibility: VisibilityLabel = "shared"
    uncertainty_note: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    task_branch_id: str = ""
    subject_kind: str = ""
    subject_id: str = ""
    observation_kind: str = "note"
    body: str
    observed_at: str
    observed_by: str = ""
    artifact_ids: list[str] = Field(default_factory=list)
    source_labels: list[SourceLabel] = Field(default_factory=list)
    freshness: FreshnessState = Field(default_factory=FreshnessState)
    visibility: VisibilityLabel = "shared"
    uncertainty_note: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    task_branch_id: str = ""
    task_id: str = ""
    artifact_kind: str = ""
    title: str = ""
    summary: str = ""
    location: str = ""
    content_sha256: str = ""
    created_at: str
    created_by: str = ""
    source_labels: list[SourceLabel] = Field(default_factory=list)
    freshness: FreshnessState = Field(default_factory=FreshnessState)
    visibility: VisibilityLabel = "shared"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClaimRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_kind: str = ""
    speaker_id: str
    subject_kind: str = ""
    subject_id: str = ""
    task_branch_id: str = ""
    claim_text: str
    observed_at: str
    source_labels: list[SourceLabel] = Field(default_factory=list)
    freshness: FreshnessState = Field(default_factory=FreshnessState)
    visibility: VisibilityLabel = "shared"
    uncertainty_note: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class NetworkSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "swarm_snapshot_v1"
    snapshot_id: str = Field(default_factory=create_snapshot_id)
    timestamp: str = Field(default_factory=utcnow)
    agent_id: str
    peer_id: str
    runtime_session_id: str = ""
    known_peers: list[PeerSnapshotRecord] = Field(default_factory=list)
    known_tasks: list[TaskSnapshotRecord] = Field(default_factory=list)
    observations: list[ObservationRecord] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    claims: list[ClaimRecord] = Field(default_factory=list)
    source_labels: list[SourceLabel] = Field(default_factory=list)
    freshness: FreshnessState = Field(default_factory=FreshnessState)
    merge_meta: MergeVersionMetadata = Field(default_factory=MergeVersionMetadata)
    unresolved_conflicts: list[ConflictRecord] = Field(default_factory=list)
    visibility: VisibilityLabel = "shared"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskBranchEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    branch_id: str
    snapshot_id: str
    timestamp: str
    contributor_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    critique_ids: list[str] = Field(default_factory=list)
    revision_ids: list[str] = Field(default_factory=list)
    source_labels: list[SourceLabel] = Field(default_factory=list)
    visibility: VisibilityLabel = "shared"


class TaskBranchState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "task_branch_v1"
    branch_id: str
    task_id: str = ""
    goal: str = ""
    origin_signal: str = ""
    contributors: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    critique_ids: list[str] = Field(default_factory=list)
    revision_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    merge_state: BranchMergeStateLabel = "open"
    final_status: str = ""
    source_labels: list[SourceLabel] = Field(default_factory=list)
    visibility: VisibilityLabel = "shared"
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    source_snapshot_ids: list[str] = Field(default_factory=list)


class RegistryEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    entity_kind: Literal["peer", "task", "artifact", "observation"]
    current: dict[str, Any] = Field(default_factory=dict)
    source_snapshot_ids: list[str] = Field(default_factory=list)
    source_labels: list[str] = Field(default_factory=list)
    first_seen_at: str = ""
    last_seen_at: str = ""
    freshness: FreshnessState = Field(default_factory=FreshnessState)
    visibility: VisibilityLabel = "shared"
    merge_state: MergeStateLabel = "clean"
    conflict_ids: list[str] = Field(default_factory=list)
    merged_from_snapshot_ids: list[str] = Field(default_factory=list)


class MergedSwarmState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "swarm_merge_state_v1"
    generated_at: str = Field(default_factory=utcnow)
    merged_snapshot_ids: list[str] = Field(default_factory=list)
    peer_registry: dict[str, RegistryEnvelope] = Field(default_factory=dict)
    task_registry: dict[str, RegistryEnvelope] = Field(default_factory=dict)
    artifact_registry: dict[str, RegistryEnvelope] = Field(default_factory=dict)
    observation_registry: dict[str, RegistryEnvelope] = Field(default_factory=dict)
    claims_by_id: dict[str, ClaimRecord] = Field(default_factory=dict)
    conflicts: list[ConflictRecord] = Field(default_factory=list)


def create_snapshot(
    *,
    agent_id: str,
    peer_id: str,
    runtime_session_id: str = "",
    known_peers: list[PeerSnapshotRecord] | None = None,
    known_tasks: list[TaskSnapshotRecord] | None = None,
    observations: list[ObservationRecord] | None = None,
    artifacts: list[ArtifactRecord] | None = None,
    claims: list[ClaimRecord] | None = None,
    source_labels: list[SourceLabel] | None = None,
    freshness: FreshnessState | None = None,
    merge_meta: MergeVersionMetadata | None = None,
    unresolved_conflicts: list[ConflictRecord] | None = None,
    visibility: VisibilityLabel = "shared",
    metadata: dict[str, Any] | None = None,
    snapshot_id: str | None = None,
    timestamp: str | None = None,
) -> NetworkSnapshot:
    snapshot = NetworkSnapshot(
        snapshot_id=str(snapshot_id or create_snapshot_id()),
        timestamp=str(timestamp or utcnow()),
        agent_id=str(agent_id or "").strip(),
        peer_id=str(peer_id or "").strip(),
        runtime_session_id=str(runtime_session_id or "").strip(),
        known_peers=list(known_peers or []),
        known_tasks=list(known_tasks or []),
        observations=list(observations or []),
        artifacts=list(artifacts or []),
        claims=list(claims or []),
        source_labels=list(source_labels or []),
        freshness=freshness or FreshnessState(status="unknown", observed_at=str(timestamp or utcnow())),
        merge_meta=merge_meta or MergeVersionMetadata(parent_snapshot_ids=[]),
        unresolved_conflicts=list(unresolved_conflicts or []),
        visibility=visibility,
        metadata=dict(metadata or {}),
    )
    if not snapshot.source_labels:
        collected: list[str] = []
        for collection in (
            snapshot.known_peers,
            snapshot.known_tasks,
            snapshot.observations,
            snapshot.artifacts,
            snapshot.claims,
        ):
            for item in collection:
                collected.extend(list(getattr(item, "source_labels", []) or []))
        snapshot.source_labels = _dedupe_sorted(collected)
    if not snapshot.merge_meta.merge_cursor:
        snapshot.merge_meta.merge_cursor = hashlib.sha256(
            f"{snapshot.snapshot_id}:{snapshot.timestamp}:{snapshot.peer_id}".encode()
        ).hexdigest()[:16]
    return snapshot


def derive_task_branches(snapshot: NetworkSnapshot) -> tuple[list[TaskBranchState], dict[str, list[TaskBranchEntry]]]:
    branches: list[TaskBranchState] = []
    entries: dict[str, list[TaskBranchEntry]] = {}
    claims_by_branch: dict[str, list[str]] = {}
    for claim in snapshot.claims:
        branch_id = str(claim.task_branch_id or "").strip()
        if not branch_id:
            continue
        claims_by_branch.setdefault(branch_id, []).append(claim.claim_id)

    for task in snapshot.known_tasks:
        branch_id = str(task.task_branch_id or "").strip() or create_branch_id(str(task.task_id or task.goal or task.title))
        observation_ids: list[str] = []
        critique_ids: list[str] = []
        revision_ids: list[str] = []
        for observation in snapshot.observations:
            related = branch_id == str(observation.task_branch_id or "").strip()
            related = related or (
                str(task.task_id or "").strip()
                and str(observation.subject_id or "").strip() == str(task.task_id or "").strip()
            )
            if not related:
                continue
            observation_ids.append(observation.observation_id)
            kind = str(observation.observation_kind or "").strip().lower()
            if kind == "critique":
                critique_ids.append(observation.observation_id)
            if kind == "revision":
                revision_ids.append(observation.observation_id)
        artifact_ids = [
            artifact.artifact_id
            for artifact in snapshot.artifacts
            if branch_id == str(artifact.task_branch_id or "").strip()
            or (
                str(task.task_id or "").strip()
                and str(artifact.task_id or "").strip() == str(task.task_id or "").strip()
            )
        ]
        contributor_ids = _dedupe_sorted(
            list(task.contributor_ids)
            + [snapshot.agent_id]
            + [claim.speaker_id for claim in snapshot.claims if str(claim.task_branch_id or "").strip() == branch_id]
        )
        branch_source_labels = _dedupe_sorted(
            list(task.source_labels)
            + [
                label
                for observation in snapshot.observations
                if observation.observation_id in observation_ids
                for label in observation.source_labels
            ]
            + [
                label
                for artifact in snapshot.artifacts
                if artifact.artifact_id in artifact_ids
                for label in artifact.source_labels
            ]
        )
        merge_state: BranchMergeStateLabel = "conflicted" if any(
            str(conflict.entity_id or "") in {str(task.task_id or ""), branch_id}
            for conflict in snapshot.unresolved_conflicts
        ) else "merged"
        branch = TaskBranchState(
            branch_id=branch_id,
            task_id=task.task_id,
            goal=task.goal or task.title,
            origin_signal=task.origin_signal,
            contributors=contributor_ids,
            observation_ids=_dedupe_sorted(observation_ids),
            artifact_ids=_dedupe_sorted(artifact_ids),
            critique_ids=_dedupe_sorted(critique_ids),
            revision_ids=_dedupe_sorted(revision_ids),
            claim_ids=_dedupe_sorted(claims_by_branch.get(branch_id, [])),
            merge_state=merge_state,
            final_status=task.status,
            source_labels=branch_source_labels,
            visibility=task.visibility,
            created_at=snapshot.timestamp,
            updated_at=snapshot.timestamp,
            source_snapshot_ids=[snapshot.snapshot_id],
        )
        entry = TaskBranchEntry(
            entry_id=f"entry-{uuid.uuid4().hex}",
            branch_id=branch_id,
            snapshot_id=snapshot.snapshot_id,
            timestamp=snapshot.timestamp,
            contributor_ids=branch.contributors,
            observation_ids=branch.observation_ids,
            artifact_ids=branch.artifact_ids,
            critique_ids=branch.critique_ids,
            revision_ids=branch.revision_ids,
            source_labels=branch.source_labels,
            visibility=branch.visibility,
        )
        branches.append(branch)
        entries.setdefault(branch_id, []).append(entry)
    return branches, entries


def empty_merged_state() -> MergedSwarmState:
    return MergedSwarmState()


def merge_snapshots(
    *,
    snapshots: list[NetworkSnapshot],
    existing_state: MergedSwarmState | None = None,
    generated_at: str | None = None,
) -> MergedSwarmState:
    state = existing_state.model_copy(deep=True) if existing_state is not None else empty_merged_state()
    conflict_index = {conflict.conflict_id for conflict in state.conflicts}
    ordered = sorted(snapshots, key=lambda item: (_parse_ts(item.timestamp), item.snapshot_id))
    for snapshot in ordered:
        if snapshot.snapshot_id not in state.merged_snapshot_ids:
            state.merged_snapshot_ids.append(snapshot.snapshot_id)
        for peer in snapshot.known_peers:
            _merge_registry_item(
                registry=state.peer_registry,
                registry_name="peer_registry",
                entity_kind="peer",
                entity_id=peer.peer_id,
                payload=peer.model_dump(mode="json"),
                observed_at=_max_timestamp(peer.freshness.observed_at, snapshot.timestamp),
                snapshot_id=snapshot.snapshot_id,
                default_visibility=peer.visibility,
                default_freshness=peer.freshness,
                state=state,
                conflict_index=conflict_index,
            )
        for task in snapshot.known_tasks:
            _merge_registry_item(
                registry=state.task_registry,
                registry_name="task_registry",
                entity_kind="task",
                entity_id=task.task_id,
                payload=task.model_dump(mode="json"),
                observed_at=_max_timestamp(task.freshness.observed_at, snapshot.timestamp),
                snapshot_id=snapshot.snapshot_id,
                default_visibility=task.visibility,
                default_freshness=task.freshness,
                state=state,
                conflict_index=conflict_index,
            )
        for artifact in snapshot.artifacts:
            _merge_registry_item(
                registry=state.artifact_registry,
                registry_name="artifact_registry",
                entity_kind="artifact",
                entity_id=artifact.artifact_id,
                payload=artifact.model_dump(mode="json"),
                observed_at=_max_timestamp(artifact.freshness.observed_at, artifact.created_at, snapshot.timestamp),
                snapshot_id=snapshot.snapshot_id,
                default_visibility=artifact.visibility,
                default_freshness=artifact.freshness,
                state=state,
                conflict_index=conflict_index,
            )
        for observation in snapshot.observations:
            _merge_registry_item(
                registry=state.observation_registry,
                registry_name="observation_registry",
                entity_kind="observation",
                entity_id=observation.observation_id,
                payload=observation.model_dump(mode="json"),
                observed_at=_max_timestamp(observation.freshness.observed_at, observation.observed_at, snapshot.timestamp),
                snapshot_id=snapshot.snapshot_id,
                default_visibility=observation.visibility,
                default_freshness=observation.freshness,
                state=state,
                conflict_index=conflict_index,
            )
        for claim in snapshot.claims:
            if claim.claim_id not in state.claims_by_id:
                state.claims_by_id[claim.claim_id] = claim
            else:
                existing_claim = state.claims_by_id[claim.claim_id]
                if _stable_json(existing_claim.model_dump(mode="json")) != _stable_json(claim.model_dump(mode="json")):
                    conflict = ConflictRecord(
                        conflict_id=_conflict_id("snapshot", claim.claim_id, "claim"),
                        registry="snapshot",
                        entity_id=claim.claim_id,
                        field_name="claim",
                        winner_snapshot_id=snapshot.snapshot_id,
                        winner_value=claim.model_dump(mode="json"),
                        competing_values=[
                            ConflictValue(
                                source_snapshot_id=snapshot.snapshot_id,
                                source_label=",".join(claim.source_labels),
                                observed_at=claim.observed_at,
                                value=claim.model_dump(mode="json"),
                            ),
                            ConflictValue(
                                source_snapshot_id="merged-state",
                                source_label="merge-derived",
                                observed_at=existing_claim.observed_at,
                                value=existing_claim.model_dump(mode="json"),
                            ),
                        ],
                    )
                    if conflict.conflict_id not in conflict_index:
                        state.conflicts.append(conflict)
                        conflict_index.add(conflict.conflict_id)
                if _latest_wins(existing_claim.observed_at, claim.observed_at):
                    state.claims_by_id[claim.claim_id] = claim
        for conflict in snapshot.unresolved_conflicts:
            if conflict.conflict_id not in conflict_index:
                state.conflicts.append(conflict)
                conflict_index.add(conflict.conflict_id)
    state.generated_at = str(generated_at or utcnow())
    state.merged_snapshot_ids = _dedupe_sorted(state.merged_snapshot_ids)
    state.conflicts.sort(key=lambda item: (_parse_ts(item.created_at), item.conflict_id))
    return state


def build_model_observation_pack(
    *,
    state: MergedSwarmState,
    branch: TaskBranchState | None = None,
    max_observations: int = 24,
    max_claims: int = 12,
) -> dict[str, Any]:
    relevant_observation_ids = set(branch.observation_ids if branch else [])
    relevant_artifact_ids = set(branch.artifact_ids if branch else [])
    relevant_claim_ids = set(branch.claim_ids if branch else [])
    observations = []
    for envelope in state.observation_registry.values():
        if relevant_observation_ids and envelope.entity_id not in relevant_observation_ids:
            continue
        observations.append(_observation_pack_entry(envelope))
    observations.sort(key=lambda item: item.get("observed_at") or "", reverse=True)
    artifacts = []
    for envelope in state.artifact_registry.values():
        if relevant_artifact_ids and envelope.entity_id not in relevant_artifact_ids:
            continue
        artifacts.append(_artifact_pack_entry(envelope))
    artifacts.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    claims = []
    for claim in state.claims_by_id.values():
        if relevant_claim_ids and claim.claim_id not in relevant_claim_ids:
            continue
        claims.append(
            {
                "claim_id": claim.claim_id,
                "speaker_id": claim.speaker_id,
                "claim_kind": claim.claim_kind,
                "claim_text": claim.claim_text,
                "subject_kind": claim.subject_kind,
                "subject_id": claim.subject_id,
                "observed_at": claim.observed_at,
                "source_labels": list(claim.source_labels),
                "freshness": claim.freshness.model_dump(mode="json"),
                "visibility": claim.visibility,
                "uncertainty_note": claim.uncertainty_note,
            }
        )
    claims.sort(key=lambda item: item.get("observed_at") or "", reverse=True)
    return {
        "schema": "swarm_observation_pack_v1",
        "generated_at": state.generated_at,
        "snapshot_ids": list(state.merged_snapshot_ids),
        "branch": branch.model_dump(mode="json") if branch is not None else None,
        "peers": [_peer_pack_entry(item) for item in state.peer_registry.values()],
        "tasks": [_task_pack_entry(item) for item in state.task_registry.values()],
        "observations": observations[: max(1, int(max_observations))],
        "artifacts": artifacts[: max(1, int(max_observations))],
        "claims": claims[: max(1, int(max_claims))],
        "conflicts": [conflict.model_dump(mode="json") for conflict in state.conflicts if conflict.resolution_state != "resolved"],
    }


def render_human_summary(*, state: MergedSwarmState, branch: TaskBranchState | None = None) -> str:
    fresh_peers = 0
    stale_peers = 0
    for item in state.peer_registry.values():
        status = str(item.freshness.status or "")
        if status == "fresh":
            fresh_peers += 1
        elif status == "stale":
            stale_peers += 1
    lines = [
        "VOOL swarm state summary",
        f"Generated: {state.generated_at}",
        f"Snapshots merged: {len(state.merged_snapshot_ids)}",
        f"Peers: {len(state.peer_registry)} total ({fresh_peers} fresh, {stale_peers} stale)",
        f"Tasks: {len(state.task_registry)}",
        f"Artifacts: {len(state.artifact_registry)}",
        f"Observations: {len(state.observation_registry)}",
        f"Claims: {len(state.claims_by_id)}",
        f"Open conflicts: {len([item for item in state.conflicts if item.resolution_state != 'resolved'])}",
    ]
    if branch is not None:
        lines.extend(
            [
                "",
                f"Branch: {branch.branch_id}",
                f"Goal: {branch.goal}",
                f"Origin signal: {branch.origin_signal or 'unknown'}",
                f"Contributors: {', '.join(branch.contributors) if branch.contributors else 'none'}",
                f"Merge state: {branch.merge_state}",
                f"Final status: {branch.final_status or 'unknown'}",
            ]
        )
    unresolved = [item for item in state.conflicts if item.resolution_state != "resolved"]
    if unresolved:
        lines.append("")
        lines.append("Unresolved conflicts:")
        for conflict in unresolved[:8]:
            lines.append(
                f"- {conflict.registry}:{conflict.entity_id}.{conflict.field_name} "
                f"(winner={conflict.winner_snapshot_id or 'unknown'})"
            )
    uncertain_claims = [claim for claim in state.claims_by_id.values() if str(claim.uncertainty_note or "").strip()]
    if uncertain_claims:
        lines.append("")
        lines.append("Still uncertain:")
        for claim in uncertain_claims[:8]:
            lines.append(
                f"- {claim.speaker_id} on {claim.subject_kind}:{claim.subject_id}: {claim.uncertainty_note}"
            )
    return "\n".join(lines).strip()


def _merge_registry_item(
    *,
    registry: dict[str, RegistryEnvelope],
    registry_name: Literal["peer_registry", "task_registry", "artifact_registry", "observation_registry"],
    entity_kind: Literal["peer", "task", "artifact", "observation"],
    entity_id: str,
    payload: dict[str, Any],
    observed_at: str,
    snapshot_id: str,
    default_visibility: VisibilityLabel,
    default_freshness: FreshnessState,
    state: MergedSwarmState,
    conflict_index: set[str],
) -> None:
    clean_entity_id = str(entity_id or "").strip()
    if not clean_entity_id:
        return
    incoming = json.loads(_stable_json(payload))
    if clean_entity_id not in registry:
        registry[clean_entity_id] = RegistryEnvelope(
            entity_id=clean_entity_id,
            entity_kind=entity_kind,
            current=incoming,
            source_snapshot_ids=[snapshot_id],
            source_labels=_dedupe_sorted(list(incoming.get("source_labels") or [])),
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            freshness=default_freshness,
            visibility=default_visibility,
            merge_state="clean",
            conflict_ids=[],
            merged_from_snapshot_ids=[snapshot_id],
        )
        return
    envelope = registry[clean_entity_id]
    current = dict(envelope.current or {})
    winner_snapshot_id = snapshot_id if _latest_wins(envelope.last_seen_at, observed_at) else (envelope.source_snapshot_ids[-1] if envelope.source_snapshot_ids else "")
    conflict_ids: list[str] = []
    merged_current = json.loads(_stable_json(current))
    keys = sorted(set(merged_current.keys()) | set(incoming.keys()))
    for key in keys:
        existing_value = merged_current.get(key)
        incoming_value = incoming.get(key)
        if key in _NON_CONFLICT_FIELDS:
            if key == "source_labels":
                merged_current[key] = _dedupe_sorted(list(existing_value or []) + list(incoming_value or []))
            elif key == "visibility":
                merged_current[key] = _merge_visibility(
                    str(existing_value or default_visibility), str(incoming_value or default_visibility)
                )
            elif key == "freshness":
                merged_current[key] = _merge_freshness_payload(existing_value, incoming_value, envelope.last_seen_at, observed_at)
            elif key == "metadata":
                merged_current[key] = _merge_metadata(existing_value, incoming_value)
            continue
        if key in _MERGEABLE_SET_FIELDS and isinstance(existing_value, list) and isinstance(incoming_value, list):
            merged_current[key] = _dedupe_sorted(list(existing_value) + list(incoming_value))
            continue
        if _stable_json(existing_value) == _stable_json(incoming_value):
            continue
        if incoming_value in (None, "", [], {}) and existing_value not in (None, "", [], {}):
            continue
        if existing_value in (None, "", [], {}) and incoming_value not in (None, "", [], {}):
            merged_current[key] = incoming_value
            continue
        chosen_value = incoming_value if _latest_wins(envelope.last_seen_at, observed_at) else existing_value
        merged_current[key] = chosen_value
        conflict = ConflictRecord(
            conflict_id=_conflict_id(registry_name, clean_entity_id, key),
            registry=registry_name,
            entity_id=clean_entity_id,
            field_name=key,
            winner_snapshot_id=winner_snapshot_id,
            winner_value=chosen_value,
            competing_values=[
                ConflictValue(
                    source_snapshot_id=envelope.source_snapshot_ids[-1] if envelope.source_snapshot_ids else "merged-state",
                    source_label=",".join(envelope.source_labels),
                    observed_at=envelope.last_seen_at,
                    value=existing_value,
                ),
                ConflictValue(
                    source_snapshot_id=snapshot_id,
                    source_label=",".join(list(incoming.get("source_labels") or [])),
                    observed_at=observed_at,
                    value=incoming_value,
                ),
            ],
        )
        if conflict.conflict_id not in conflict_index:
            state.conflicts.append(conflict)
            conflict_index.add(conflict.conflict_id)
        conflict_ids.append(conflict.conflict_id)
    envelope.current = merged_current
    envelope.source_snapshot_ids = _dedupe_sorted([*list(envelope.source_snapshot_ids), snapshot_id])
    envelope.merged_from_snapshot_ids = _dedupe_sorted([*list(envelope.merged_from_snapshot_ids), snapshot_id])
    envelope.source_labels = _dedupe_sorted(list(envelope.source_labels) + list(incoming.get("source_labels") or []))
    envelope.first_seen_at = envelope.first_seen_at or observed_at
    envelope.last_seen_at = _max_timestamp(envelope.last_seen_at, observed_at)
    envelope.visibility = _merge_visibility(envelope.visibility, default_visibility)
    envelope.freshness = _merge_freshness_state(envelope.freshness, default_freshness)
    envelope.conflict_ids = _dedupe_sorted(list(envelope.conflict_ids) + conflict_ids)
    envelope.merge_state = "conflicted" if envelope.conflict_ids else "merged"


def _merge_metadata(existing: Any, incoming: Any) -> dict[str, Any]:
    merged = {}
    if isinstance(existing, dict):
        merged.update(existing)
    if isinstance(incoming, dict):
        for key, value in incoming.items():
            if key not in merged or merged.get(key) in (None, "", [], {}):
                merged[key] = value
    return merged


def _merge_freshness_payload(existing: Any, incoming: Any, existing_seen_at: str, incoming_seen_at: str) -> dict[str, Any]:
    left = FreshnessState.model_validate(existing or {})
    right = FreshnessState.model_validate(incoming or {})
    return _merge_freshness_state(left, right).model_dump(mode="json")


def _merge_freshness_state(existing: FreshnessState, incoming: FreshnessState) -> FreshnessState:
    if _latest_wins(existing.observed_at, incoming.observed_at):
        chosen = incoming.model_copy(deep=True)
        if chosen.status == "unknown" and existing.status != "unknown":
            chosen.status = existing.status
        if not chosen.stale_after_seconds and existing.stale_after_seconds:
            chosen.stale_after_seconds = existing.stale_after_seconds
        return chosen
    chosen = existing.model_copy(deep=True)
    if chosen.status == "unknown" and incoming.status != "unknown":
        chosen.status = incoming.status
    if not chosen.stale_after_seconds and incoming.stale_after_seconds:
        chosen.stale_after_seconds = incoming.stale_after_seconds
    return chosen


def _conflict_id(registry_name: str, entity_id: str, field_name: str) -> str:
    digest = hashlib.sha256(f"{registry_name}:{entity_id}:{field_name}".encode()).hexdigest()[:16]
    return f"conflict-{digest}"


def _peer_pack_entry(item: RegistryEnvelope) -> dict[str, Any]:
    current = dict(item.current or {})
    return {
        "peer_id": item.entity_id,
        "agent_id": str(current.get("agent_id") or ""),
        "runtime_session_id": str(current.get("runtime_session_id") or ""),
        "status": str(current.get("status") or ""),
        "summary": str(current.get("summary") or ""),
        "source_labels": list(item.source_labels),
        "freshness": item.freshness.model_dump(mode="json"),
        "visibility": item.visibility,
        "merge_state": item.merge_state,
    }


def _task_pack_entry(item: RegistryEnvelope) -> dict[str, Any]:
    current = dict(item.current or {})
    return {
        "task_id": item.entity_id,
        "task_branch_id": str(current.get("task_branch_id") or ""),
        "title": str(current.get("title") or ""),
        "goal": str(current.get("goal") or ""),
        "origin_signal": str(current.get("origin_signal") or ""),
        "status": str(current.get("status") or ""),
        "source_labels": list(item.source_labels),
        "freshness": item.freshness.model_dump(mode="json"),
        "visibility": item.visibility,
        "merge_state": item.merge_state,
        "uncertainty_note": str(current.get("uncertainty_note") or ""),
    }


def _observation_pack_entry(item: RegistryEnvelope) -> dict[str, Any]:
    current = dict(item.current or {})
    return {
        "observation_id": item.entity_id,
        "task_branch_id": str(current.get("task_branch_id") or ""),
        "subject_kind": str(current.get("subject_kind") or ""),
        "subject_id": str(current.get("subject_id") or ""),
        "observation_kind": str(current.get("observation_kind") or ""),
        "body": str(current.get("body") or ""),
        "observed_at": str(current.get("observed_at") or item.last_seen_at),
        "observed_by": str(current.get("observed_by") or ""),
        "artifact_ids": list(current.get("artifact_ids") or []),
        "source_labels": list(item.source_labels),
        "freshness": item.freshness.model_dump(mode="json"),
        "visibility": item.visibility,
        "merge_state": item.merge_state,
        "uncertainty_note": str(current.get("uncertainty_note") or ""),
    }


def _artifact_pack_entry(item: RegistryEnvelope) -> dict[str, Any]:
    current = dict(item.current or {})
    return {
        "artifact_id": item.entity_id,
        "task_branch_id": str(current.get("task_branch_id") or ""),
        "task_id": str(current.get("task_id") or ""),
        "artifact_kind": str(current.get("artifact_kind") or ""),
        "title": str(current.get("title") or ""),
        "summary": str(current.get("summary") or ""),
        "location": str(current.get("location") or ""),
        "content_sha256": str(current.get("content_sha256") or ""),
        "created_at": str(current.get("created_at") or item.last_seen_at),
        "source_labels": list(item.source_labels),
        "freshness": item.freshness.model_dump(mode="json"),
        "visibility": item.visibility,
        "merge_state": item.merge_state,
    }
