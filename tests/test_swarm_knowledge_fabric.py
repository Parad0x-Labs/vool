from __future__ import annotations

import json
from pathlib import Path

from core.swarm_knowledge_fabric import (
    ArtifactRecord,
    ClaimRecord,
    FreshnessState,
    ObservationRecord,
    PeerSnapshotRecord,
    TaskSnapshotRecord,
    create_snapshot,
)
from storage.swarm_knowledge_archive import commit_snapshot, load_merged_state, load_snapshot, load_task_branch


def _snapshot_one() -> object:
    return create_snapshot(
        snapshot_id="snapshot-001",
        timestamp="2026-03-13T10:00:00+00:00",
        agent_id="agent-alpha",
        peer_id="peer-alpha",
        runtime_session_id="runtime-a",
        known_peers=[
            PeerSnapshotRecord(
                peer_id="peer-alpha",
                agent_id="agent-alpha",
                runtime_session_id="runtime-a",
                status="online",
                summary="Local node is live.",
                source_labels=["local-only"],
                freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:00:00+00:00", stale_after_seconds=300),
                visibility="local_only",
            ),
            PeerSnapshotRecord(
                peer_id="peer-beta",
                agent_id="agent-beta",
                runtime_session_id="runtime-b",
                status="visible",
                summary="Watcher saw beta.",
                source_labels=["watcher-derived"],
                freshness=FreshnessState(status="stale", observed_at="2026-03-13T09:40:00+00:00", stale_after_seconds=300),
                visibility="shared",
            ),
        ],
        known_tasks=[
            TaskSnapshotRecord(
                task_id="task-parser",
                task_branch_id="branch-parser",
                title="Parser recovery",
                goal="Fix the parser crash without breaking config loading.",
                origin_signal="user_report",
                status="open",
                contributor_ids=["agent-alpha"],
                source_labels=["local-only"],
                freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:00:00+00:00", stale_after_seconds=600),
                visibility="shared",
            )
        ],
        observations=[
            ObservationRecord(
                observation_id="obs-parser-1",
                task_branch_id="branch-parser",
                subject_kind="task",
                subject_id="task-parser",
                observation_kind="research",
                body="Crash happens when the parser sees an empty YAML mapping.",
                observed_at="2026-03-13T10:00:00+00:00",
                observed_by="agent-alpha",
                source_labels=["local-only"],
                freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:00:00+00:00", stale_after_seconds=600),
                visibility="shared",
            ),
            ObservationRecord(
                observation_id="obs-parser-critique",
                task_branch_id="branch-parser",
                subject_kind="task",
                subject_id="task-parser",
                observation_kind="critique",
                body="Watcher report may be stale until we reproduce locally.",
                observed_at="2026-03-13T10:02:00+00:00",
                observed_by="agent-beta",
                source_labels=["watcher-derived"],
                freshness=FreshnessState(status="stale", observed_at="2026-03-13T09:40:00+00:00", stale_after_seconds=300),
                visibility="shared",
                uncertainty_note="Remote presence is stale.",
            ),
        ],
        artifacts=[
            ArtifactRecord(
                artifact_id="artifact-parser-log",
                task_branch_id="branch-parser",
                task_id="task-parser",
                artifact_kind="log",
                title="Parser traceback",
                summary="Traceback from the failing YAML load.",
                location="/tmp/parser.log",
                content_sha256="sha256-parser-log",
                created_at="2026-03-13T10:01:00+00:00",
                created_by="agent-alpha",
                source_labels=["local-only"],
                freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:01:00+00:00", stale_after_seconds=600),
                visibility="local_only",
            )
        ],
        claims=[
            ClaimRecord(
                claim_id="claim-parser-1",
                claim_kind="status",
                speaker_id="agent-alpha",
                subject_kind="task",
                subject_id="task-parser",
                task_branch_id="branch-parser",
                claim_text="I can reproduce the crash locally, but the exact config interaction is still uncertain.",
                observed_at="2026-03-13T10:02:00+00:00",
                source_labels=["local-only"],
                freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:02:00+00:00", stale_after_seconds=600),
                visibility="shared",
                uncertainty_note="Need one more reproduction with the production config.",
            )
        ],
        source_labels=["local-only", "watcher-derived"],
        freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:00:00+00:00", stale_after_seconds=300),
        visibility="shared",
    )


def _snapshot_two() -> object:
    return create_snapshot(
        snapshot_id="snapshot-002",
        timestamp="2026-03-13T10:05:00+00:00",
        agent_id="agent-beta",
        peer_id="peer-beta",
        runtime_session_id="runtime-b",
        known_peers=[
            PeerSnapshotRecord(
                peer_id="peer-beta",
                agent_id="agent-beta",
                runtime_session_id="runtime-b",
                status="online",
                summary="Beta confirmed the issue and proposed a fix.",
                source_labels=["watcher-derived"],
                freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:05:00+00:00", stale_after_seconds=300),
                visibility="shared",
            )
        ],
        known_tasks=[
            TaskSnapshotRecord(
                task_id="task-parser",
                task_branch_id="branch-parser",
                title="Parser recovery",
                goal="Fix the parser crash without breaking config loading.",
                origin_signal="user_report",
                status="blocked",
                contributor_ids=["agent-alpha", "agent-beta"],
                source_labels=["watcher-derived"],
                freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:05:00+00:00", stale_after_seconds=600),
                visibility="shared",
                uncertainty_note="Two root-cause theories remain.",
            )
        ],
        observations=[
            ObservationRecord(
                observation_id="obs-parser-2",
                task_branch_id="branch-parser",
                subject_kind="task",
                subject_id="task-parser",
                observation_kind="revision",
                body="Beta narrowed the crash to empty maps plus include expansion.",
                observed_at="2026-03-13T10:05:00+00:00",
                observed_by="agent-beta",
                source_labels=["watcher-derived"],
                freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:05:00+00:00", stale_after_seconds=600),
                visibility="shared",
            )
        ],
        artifacts=[
            ArtifactRecord(
                artifact_id="artifact-parser-patch",
                task_branch_id="branch-parser",
                task_id="task-parser",
                artifact_kind="patch",
                title="Candidate parser patch",
                summary="Proposed change for empty map handling.",
                location="/tmp/parser.patch",
                content_sha256="sha256-parser-patch",
                created_at="2026-03-13T10:06:00+00:00",
                created_by="agent-beta",
                source_labels=["watcher-derived"],
                freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:06:00+00:00", stale_after_seconds=600),
                visibility="shared",
            )
        ],
        claims=[
            ClaimRecord(
                claim_id="claim-parser-2",
                claim_kind="proposal",
                speaker_id="agent-beta",
                subject_kind="task",
                subject_id="task-parser",
                task_branch_id="branch-parser",
                claim_text="I think the patch fixes the empty-map case, but include expansion still needs proof.",
                observed_at="2026-03-13T10:06:00+00:00",
                source_labels=["watcher-derived"],
                freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:06:00+00:00", stale_after_seconds=600),
                visibility="shared",
                uncertainty_note="Include expansion path still unverified.",
            )
        ],
        source_labels=["watcher-derived"],
        freshness=FreshnessState(status="fresh", observed_at="2026-03-13T10:05:00+00:00", stale_after_seconds=300),
        visibility="shared",
    )


def test_commit_snapshot_writes_archive_layout_and_reloads_snapshot(tmp_path: Path) -> None:
    result = commit_snapshot(_snapshot_one(), root_dir=tmp_path)

    assert result["snapshot_path"].exists()
    assert result["merged_state_path"].exists()
    assert result["observation_pack_path"].exists()
    assert result["summary_path"].exists()
    assert (tmp_path / "indexes" / "manifests" / "snapshots.jsonl").exists()
    assert (tmp_path / "indexes" / "manifests" / "task_branches.jsonl").exists()
    assert (tmp_path / "indexes" / "manifests" / "peers.jsonl").exists()
    assert (tmp_path / "peer_records" / "peer-alpha" / "peer.json").exists()
    assert (tmp_path / "task_branches" / "branch-parser" / "branch.json").exists()

    loaded = load_snapshot("snapshot-001", root_dir=tmp_path)
    assert loaded is not None
    assert loaded.snapshot_id == "snapshot-001"
    assert loaded.claims[0].uncertainty_note.startswith("Need one more reproduction")

    branch = load_task_branch("branch-parser", root_dir=tmp_path)
    assert branch is not None
    assert branch.goal.startswith("Fix the parser crash")
    assert "obs-parser-1" in branch.observation_ids
    assert "obs-parser-critique" in branch.critique_ids


def test_branch_persists_across_snapshots_without_duplicate_replay(tmp_path: Path) -> None:
    commit_snapshot(_snapshot_one(), root_dir=tmp_path)
    commit_snapshot(_snapshot_two(), root_dir=tmp_path)

    branch = load_task_branch("branch-parser", root_dir=tmp_path)
    assert branch is not None
    assert branch.final_status == "blocked"
    assert sorted(branch.contributors) == ["agent-alpha", "agent-beta"]
    assert sorted(branch.observation_ids) == ["obs-parser-1", "obs-parser-2", "obs-parser-critique"]
    assert sorted(branch.artifact_ids) == ["artifact-parser-log", "artifact-parser-patch"]
    assert sorted(branch.revision_ids) == ["obs-parser-2"]
    assert sorted(branch.source_snapshot_ids) == ["snapshot-001", "snapshot-002"]

    entries_dir = tmp_path / "task_branches" / "branch-parser" / "entries"
    entry_files = sorted(entries_dir.glob("*.json"))
    assert len(entry_files) == 2


def test_merge_preserves_conflicts_instead_of_silent_overwrite(tmp_path: Path) -> None:
    commit_snapshot(_snapshot_one(), root_dir=tmp_path)
    commit_snapshot(_snapshot_two(), root_dir=tmp_path)

    merged = load_merged_state(root_dir=tmp_path)
    assert merged is not None
    task_entry = merged.task_registry["task-parser"]
    assert task_entry.current["status"] == "blocked"
    assert task_entry.merge_state == "conflicted"
    assert any(conflict.entity_id == "task-parser" and conflict.field_name == "status" for conflict in merged.conflicts)


def test_model_pack_and_summary_are_source_qualified_and_uncertainty_preserving(tmp_path: Path) -> None:
    result = commit_snapshot(_snapshot_one(), root_dir=tmp_path)
    commit_snapshot(_snapshot_two(), root_dir=tmp_path)
    pack = json.loads((tmp_path / "indexes" / "views" / "latest_observation_pack.json").read_text(encoding="utf-8"))
    summary = (tmp_path / "indexes" / "views" / "latest_summary.md").read_text(encoding="utf-8")

    assert pack["schema"] == "swarm_observation_pack_v1"
    assert any(peer["peer_id"] == "peer-beta" and peer["freshness"]["status"] == "fresh" for peer in pack["peers"])
    assert any(task["task_id"] == "task-parser" and task["merge_state"] == "conflicted" for task in pack["tasks"])
    assert any(observation["source_labels"] for observation in pack["observations"])
    assert any(claim["uncertainty_note"] for claim in pack["claims"])
    assert pack["conflicts"]

    assert "Open conflicts:" in summary
    assert "Still uncertain:" in summary
    assert "agent-alpha on task:task-parser" in summary

    assert "VOOL swarm state summary" in result["summary_text"]
