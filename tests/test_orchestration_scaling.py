from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.parent_orchestrator import _resolved_subtask_width
from core.task_decomposer import _scaled_templates
from network.assist_models import validate_assist_payload


def test_scaled_templates_expand_to_target_count() -> None:
    base = [
        {
            "task_type": "research",
            "subtask_type": "source_comparison",
            "required_capabilities": ["research"],
            "summary": "Compare baseline options.",
            "reward": {"points": 8, "wnull_pending": 4},
        }
    ]
    out = _scaled_templates(base, abstract_inputs=["alpha lane", "beta lane"], target_count=10)
    assert len(out) == 10
    assert out[0]["subtask_type"] == "source_comparison"
    assert str(out[-1]["subtask_type"]).startswith("parallel_evidence_lane_")


def test_task_offer_allows_ten_helpers() -> None:
    payload = {
        "task_id": "task-12345678",
        "parent_agent_id": "peer-" + ("a" * 20),
        "capsule_id": "capsule-12345678",
        "task_type": "research",
        "subtask_type": "source_comparison",
        "summary": "Compare options safely.",
        "required_capabilities": ["research"],
        "max_helpers": 10,
        "priority": "normal",
        "reward_hint": {"points": 1, "wnull_pending": 1},
        "capsule": {"schema_version": 1},
        "deadline_ts": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }
    model = validate_assist_payload("TASK_OFFER", payload)
    assert int(model.max_helpers) == 10


def test_task_offer_rejects_more_than_ten_helpers() -> None:
    payload = {
        "task_id": "task-12345678",
        "parent_agent_id": "peer-" + ("b" * 20),
        "capsule_id": "capsule-12345678",
        "task_type": "research",
        "subtask_type": "source_comparison",
        "summary": "Compare options safely.",
        "required_capabilities": ["research"],
        "max_helpers": 11,
        "priority": "normal",
        "reward_hint": {"points": 1, "wnull_pending": 1},
        "capsule": {"schema_version": 1},
        "deadline_ts": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }
    with pytest.raises(ValueError):
        validate_assist_payload("TASK_OFFER", payload)


def test_resolved_subtask_width_uses_auto_worker_recommendation(monkeypatch) -> None:
    defaults = {
        "orchestration.max_subtasks_per_parent": 3,
        "orchestration.max_subtasks_hard_cap": 10,
        "orchestration.local_worker_auto_detect": True,
        "orchestration.local_worker_pool_max": 10,
        "orchestration.local_worker_pool_target": 0,
    }

    monkeypatch.setattr(
        "core.parent_orchestrator.policy_engine.get",
        lambda path, default=None: defaults.get(path, default),
    )
    monkeypatch.setattr(
        "core.parent_orchestrator.resolve_local_worker_capacity",
        lambda requested, hard_cap: (8, 8),
    )

    assert _resolved_subtask_width() == 8


def test_resolved_subtask_width_respects_manual_policy_target_and_hard_cap(monkeypatch) -> None:
    defaults = {
        "orchestration.max_subtasks_per_parent": 3,
        "orchestration.max_subtasks_hard_cap": 10,
        "orchestration.local_worker_auto_detect": True,
        "orchestration.local_worker_pool_max": 10,
        "orchestration.local_worker_pool_target": 25,
    }

    monkeypatch.setattr(
        "core.parent_orchestrator.policy_engine.get",
        lambda path, default=None: defaults.get(path, default),
    )
    monkeypatch.setattr(
        "core.parent_orchestrator.resolve_local_worker_capacity",
        lambda requested, hard_cap: (25, 8),
    )

    assert _resolved_subtask_width() == 10
