from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.runtime_paths import data_path
from core.swarm_knowledge_fabric import (
    MergedSwarmState,
    NetworkSnapshot,
    TaskBranchEntry,
    TaskBranchState,
    build_model_observation_pack,
    derive_task_branches,
    merge_snapshots,
    render_human_summary,
)


def archive_root(*, root_dir: str | Path | None = None) -> Path:
    if root_dir is not None:
        return Path(root_dir).expanduser().resolve()
    return data_path("swarm_fabric")


def ensure_archive_layout(*, root_dir: str | Path | None = None) -> dict[str, Path]:
    root = archive_root(root_dir=root_dir)
    layout = {
        "root": root,
        "snapshots": root / "snapshots",
        "task_branches": root / "task_branches",
        "peer_records": root / "peer_records",
        "indexes": root / "indexes",
        "manifests": root / "indexes" / "manifests",
        "current": root / "indexes" / "current",
        "views": root / "indexes" / "views",
    }
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    return layout


def commit_snapshot(
    snapshot: NetworkSnapshot,
    *,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    layout = ensure_archive_layout(root_dir=root_dir)
    snapshot_path = _write_snapshot(snapshot=snapshot, layout=layout)
    branches, entries_by_branch = derive_task_branches(snapshot)
    branch_paths = {}
    entry_paths = {}
    for branch in branches:
        existing = load_task_branch(branch.branch_id, root_dir=layout["root"])
        merged_branch = _merge_branch(existing=existing, incoming=branch)
        branch_path = _write_branch(branch=merged_branch, layout=layout)
        branch_paths[merged_branch.branch_id] = branch_path
        entry_paths[merged_branch.branch_id] = []
        for entry in entries_by_branch.get(merged_branch.branch_id, []):
            path = _write_branch_entry(entry=entry, layout=layout)
            entry_paths[merged_branch.branch_id].append(path)
        _append_jsonl(
            layout["manifests"] / "task_branches.jsonl",
            {
                "branch_id": merged_branch.branch_id,
                "task_id": merged_branch.task_id,
                "updated_at": merged_branch.updated_at,
                "goal": merged_branch.goal,
                "final_status": merged_branch.final_status,
                "merge_state": merged_branch.merge_state,
                "source_snapshot_ids": list(merged_branch.source_snapshot_ids),
                "path": str(branch_path),
            },
        )
    merged_state = merge_snapshots(
        snapshots=[snapshot],
        existing_state=load_merged_state(root_dir=layout["root"]),
    )
    merged_state_path = _write_json(layout["current"] / "merged_state.json", merged_state.model_dump(mode="json"))
    peer_paths = _write_peer_records(merged_state=merged_state, layout=layout, snapshot=snapshot)
    observation_pack = build_model_observation_pack(state=merged_state)
    summary_text = render_human_summary(state=merged_state)
    pack_path = _write_json(layout["views"] / "latest_observation_pack.json", observation_pack)
    summary_path = _write_text(layout["views"] / "latest_summary.md", summary_text + "\n")
    _append_jsonl(
        layout["manifests"] / "snapshots.jsonl",
        {
            "snapshot_id": snapshot.snapshot_id,
            "timestamp": snapshot.timestamp,
            "agent_id": snapshot.agent_id,
            "peer_id": snapshot.peer_id,
            "runtime_session_id": snapshot.runtime_session_id,
            "source_labels": list(snapshot.source_labels),
            "visibility": snapshot.visibility,
            "path": str(snapshot_path),
        },
    )
    _append_jsonl(
        layout["manifests"] / "merges.jsonl",
        {
            "generated_at": merged_state.generated_at,
            "snapshot_id": snapshot.snapshot_id,
            "merged_snapshot_ids": list(merged_state.merged_snapshot_ids),
            "peer_count": len(merged_state.peer_registry),
            "task_count": len(merged_state.task_registry),
            "artifact_count": len(merged_state.artifact_registry),
            "observation_count": len(merged_state.observation_registry),
            "conflict_count": len([item for item in merged_state.conflicts if item.resolution_state != "resolved"]),
            "path": str(merged_state_path),
        },
    )
    return {
        "snapshot_path": snapshot_path,
        "branch_paths": branch_paths,
        "branch_entry_paths": entry_paths,
        "peer_paths": peer_paths,
        "merged_state_path": merged_state_path,
        "observation_pack_path": pack_path,
        "summary_path": summary_path,
        "merged_state": merged_state,
        "observation_pack": observation_pack,
        "summary_text": summary_text,
    }


def load_snapshot(snapshot_id: str, *, root_dir: str | Path | None = None) -> NetworkSnapshot | None:
    layout = ensure_archive_layout(root_dir=root_dir)
    manifest_path = layout["manifests"] / "snapshots.jsonl"
    if not manifest_path.exists():
        return None
    clean_snapshot_id = str(snapshot_id or "").strip()
    if not clean_snapshot_id:
        return None
    for line in reversed(manifest_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        item = json.loads(line)
        if str(item.get("snapshot_id") or "") != clean_snapshot_id:
            continue
        path = Path(str(item.get("path") or "")).expanduser().resolve()
        if not path.exists():
            return None
        return NetworkSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    return None


def load_task_branch(branch_id: str, *, root_dir: str | Path | None = None) -> TaskBranchState | None:
    root = archive_root(root_dir=root_dir)
    path = root / "task_branches" / str(branch_id or "").strip() / "branch.json"
    if not path.exists():
        return None
    return TaskBranchState.model_validate_json(path.read_text(encoding="utf-8"))


def load_merged_state(*, root_dir: str | Path | None = None) -> MergedSwarmState | None:
    root = archive_root(root_dir=root_dir)
    path = root / "indexes" / "current" / "merged_state.json"
    if not path.exists():
        return None
    return MergedSwarmState.model_validate_json(path.read_text(encoding="utf-8"))


def _write_snapshot(*, snapshot: NetworkSnapshot, layout: dict[str, Path]) -> Path:
    stamp = snapshot.timestamp.replace(":", "-")
    snapshot_dir = layout["snapshots"] / snapshot.timestamp[:4] / snapshot.timestamp[5:7] / snapshot.timestamp[8:10]
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / f"{stamp}__{snapshot.snapshot_id}.json"
    return _write_json(path, snapshot.model_dump(mode="json"))


def _write_branch(*, branch: TaskBranchState, layout: dict[str, Path]) -> Path:
    branch_dir = layout["task_branches"] / branch.branch_id
    branch_dir.mkdir(parents=True, exist_ok=True)
    (branch_dir / "entries").mkdir(parents=True, exist_ok=True)
    return _write_json(branch_dir / "branch.json", branch.model_dump(mode="json"))


def _write_branch_entry(*, entry: TaskBranchEntry, layout: dict[str, Path]) -> Path:
    branch_dir = layout["task_branches"] / entry.branch_id / "entries"
    branch_dir.mkdir(parents=True, exist_ok=True)
    safe_timestamp = entry.timestamp.replace(":", "-")
    path = branch_dir / f"{safe_timestamp}__{entry.entry_id}.json"
    return _write_json(path, entry.model_dump(mode="json"))


def _write_peer_records(
    *,
    merged_state: MergedSwarmState,
    layout: dict[str, Path],
    snapshot: NetworkSnapshot,
) -> dict[str, Path]:
    peer_paths: dict[str, Path] = {}
    for peer_id, envelope in merged_state.peer_registry.items():
        peer_dir = layout["peer_records"] / peer_id
        (peer_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        current_path = _write_json(peer_dir / "peer.json", envelope.model_dump(mode="json"))
        peer_paths[peer_id] = current_path
        peer_snapshot_path = _write_json(
            peer_dir / "snapshots" / f"{snapshot.timestamp.replace(':', '-')}__{snapshot.snapshot_id}.json",
            envelope.model_dump(mode="json"),
        )
        _append_jsonl(
            layout["manifests"] / "peers.jsonl",
            {
                "peer_id": peer_id,
                "snapshot_id": snapshot.snapshot_id,
                "agent_id": str(envelope.current.get("agent_id") or ""),
                "freshness": envelope.freshness.model_dump(mode="json"),
                "merge_state": envelope.merge_state,
                "path": str(peer_snapshot_path),
            },
        )
    return peer_paths


def _merge_branch(*, existing: TaskBranchState | None, incoming: TaskBranchState) -> TaskBranchState:
    if existing is None:
        return incoming
    merged = existing.model_copy(deep=True)
    if str(incoming.goal or "").strip():
        merged.goal = str(incoming.goal or "").strip()
    if str(incoming.origin_signal or "").strip():
        merged.origin_signal = str(incoming.origin_signal or "").strip()
    if str(incoming.task_id or "").strip():
        merged.task_id = str(incoming.task_id or "").strip()
    merged.contributors = sorted(set(merged.contributors) | set(incoming.contributors))
    merged.observation_ids = sorted(set(merged.observation_ids) | set(incoming.observation_ids))
    merged.artifact_ids = sorted(set(merged.artifact_ids) | set(incoming.artifact_ids))
    merged.critique_ids = sorted(set(merged.critique_ids) | set(incoming.critique_ids))
    merged.revision_ids = sorted(set(merged.revision_ids) | set(incoming.revision_ids))
    merged.claim_ids = sorted(set(merged.claim_ids) | set(incoming.claim_ids))
    merged.source_labels = sorted(set(merged.source_labels) | set(incoming.source_labels))
    merged.source_snapshot_ids = sorted(set(merged.source_snapshot_ids) | set(incoming.source_snapshot_ids))
    if incoming.merge_state == "conflicted" or existing.merge_state == "conflicted":
        merged.merge_state = "conflicted"
    elif incoming.merge_state == "finalized" or existing.merge_state == "finalized":
        merged.merge_state = "finalized"
    else:
        merged.merge_state = incoming.merge_state or existing.merge_state
    if str(incoming.final_status or "").strip():
        merged.final_status = str(incoming.final_status or "").strip()
    merged.visibility = incoming.visibility if incoming.visibility == "external" else existing.visibility
    merged.updated_at = incoming.updated_at
    return merged


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    return path


def _write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)
    return path
