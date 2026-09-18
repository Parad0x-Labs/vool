from __future__ import annotations

from types import SimpleNamespace

from core.shard_ranker import rank


def _candidate(*, shard_id: str, source_type: str = "peer_received", trust_score: float = 0.7) -> dict:
    return {
        "shard_id": shard_id,
        "source_type": source_type,
        "trust_score": trust_score,
        "semantic_match": 0.76,
        "environment_match": 0.75,
        "quality_score": 0.72,
        "freshness_ts": "",
        "local_validation_count": 1,
        "local_failure_count": 0,
        "risk_flags": [],
        "reuse_outcomes": {},
    }


def test_rank_prefers_peer_received_shard_with_proven_reuse_success() -> None:
    task = SimpleNamespace(task_id="task-1")
    baseline = _candidate(shard_id="peer-baseline")
    proven = _candidate(shard_id="peer-proven")
    proven["reuse_outcomes"] = {
        "total_count": 4,
        "success_count": 4,
        "durable_count": 3,
        "selected_count": 4,
        "selected_success_count": 4,
        "selected_durable_count": 3,
        "answer_backed_count": 4,
        "answer_backed_success_count": 4,
        "answer_backed_durable_count": 3,
        "quality_backed_count": 4,
        "quality_backed_success_count": 4,
        "quality_backed_durable_count": 3,
    }

    ranked = rank([baseline, proven], task)

    assert ranked[0]["shard_id"] == "peer-proven"
    assert ranked[0]["reuse_outcome_adjustment"] > ranked[1]["reuse_outcome_adjustment"]


def test_rank_does_not_apply_remote_reuse_bonus_to_local_generated_shard() -> None:
    task = SimpleNamespace(task_id="task-2")
    local_candidate = _candidate(shard_id="local-1", source_type="local_generated")
    local_candidate["reuse_outcomes"] = {
        "total_count": 8,
        "success_count": 8,
        "durable_count": 8,
    }

    ranked = rank([local_candidate], task)

    assert ranked[0]["reuse_outcome_adjustment"] == 0.0


def test_rank_does_not_apply_remote_reuse_bonus_from_incidental_success_without_answer_backed_proof() -> None:
    task = SimpleNamespace(task_id="task-3")
    incidental = _candidate(shard_id="peer-incidental")
    incidental["reuse_outcomes"] = {
        "total_count": 3,
        "success_count": 3,
        "durable_count": 2,
        "selected_count": 0,
        "selected_success_count": 0,
        "selected_durable_count": 0,
        "answer_backed_count": 0,
        "answer_backed_success_count": 0,
        "answer_backed_durable_count": 0,
    }

    ranked = rank([incidental], task)

    assert ranked[0]["reuse_outcome_adjustment"] == 0.0


def test_rank_prefers_peer_received_shard_with_quality_backed_reuse_over_answer_backed_only_history() -> None:
    task = SimpleNamespace(task_id="task-4")
    answer_backed_only = _candidate(shard_id="peer-answer-backed")
    answer_backed_only["reuse_outcomes"] = {
        "answer_backed_count": 5,
        "answer_backed_success_count": 5,
        "answer_backed_durable_count": 4,
        "quality_backed_count": 0,
        "quality_backed_success_count": 0,
        "quality_backed_durable_count": 0,
    }
    quality_backed = _candidate(shard_id="peer-quality-backed")
    quality_backed["reuse_outcomes"] = {
        "answer_backed_count": 2,
        "answer_backed_success_count": 2,
        "answer_backed_durable_count": 1,
        "quality_backed_count": 2,
        "quality_backed_success_count": 2,
        "quality_backed_durable_count": 1,
    }

    ranked = rank([answer_backed_only, quality_backed], task)

    assert ranked[0]["shard_id"] == "peer-quality-backed"
    assert ranked[0]["reuse_outcome_adjustment"] > ranked[1]["reuse_outcome_adjustment"]
