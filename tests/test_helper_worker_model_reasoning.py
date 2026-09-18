from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.model_teacher_pipeline import TeacherCandidate
from core.task_capsule import build_task_capsule
from sandbox.helper_worker import run_task_capsule
from storage.migrations import run_migrations


def _capsule(task_type: str = "research"):
    return build_task_capsule(
        parent_agent_id="parent-node-1234567890abcdef",
        task_id="task-helper-model-1234",
        task_type=task_type,
        subtask_type="subtask-a",
        summary="Analyze safe scaling approach.",
        sanitized_context={
            "problem_class": "system_design",
            "abstract_inputs": ["regional meet cluster", "delta sync", "snapshot fallback"],
            "known_constraints": ["local-first", "no private data leak"],
            "environment_tags": {"os": "linux"},
        },
        allowed_operations=["research", "summarize", "validate"],
        deadline_ts=datetime.now(timezone.utc) + timedelta(minutes=5),
        learning_allowed=False,
    )


def test_helper_worker_uses_model_reasoning_when_available():
    run_migrations()
    candidate = TeacherCandidate(
        task_kind="summarization",
        provider_name="local",
        model_name="qwen-lite",
        output_text="Use regional clusters and bounded deltas.\n- Keep metadata hot\n- Fetch payload on demand",
        confidence=0.84,
        source_model_tag="local:qwen-lite",
        provenance={},
    )
    with patch("sandbox.helper_worker.ModelTeacherPipeline.run", return_value=candidate):
        outcome = run_task_capsule(_capsule(), helper_agent_id="helper-node-1234567890")
    assert "Research capsule reviewed for" not in outcome.result.summary
    assert outcome.result.summary.startswith("Use regional clusters")
    assert any(item.startswith("model:local:qwen-lite") for item in outcome.result.evidence)
    assert outcome.result.confidence >= 0.8


def test_helper_worker_reports_honestly_when_model_unavailable():
    """No scripted stand-in: a failed/absent model result must not be dressed up as a finding."""
    run_migrations()
    with patch("sandbox.helper_worker.ModelTeacherPipeline.run", return_value=None):
        outcome = run_task_capsule(_capsule(), helper_agent_id="helper-node-1234567890")
    assert "Research capsule reviewed for" not in outcome.result.summary
    assert "Top signal" not in outcome.result.summary
    assert outcome.result.result_type == "model_unavailable"
    assert outcome.result.confidence == 0.0
    assert "model_unavailable" in outcome.result.risk_flags
    assert outcome.result.evidence == []
    assert outcome.result.abstract_steps == []


def test_helper_worker_reports_honestly_when_model_raises():
    """The same honest path fires whether the pipeline returns None or raises."""
    run_migrations()
    with patch("sandbox.helper_worker.ModelTeacherPipeline.run", side_effect=RuntimeError("boom")):
        outcome = run_task_capsule(_capsule(), helper_agent_id="helper-node-1234567890")
    assert outcome.result.result_type == "model_unavailable"
    assert outcome.result.confidence == 0.0


def test_helper_worker_requests_drone_lane_swarm_reasoning():
    run_migrations()
    candidate = TeacherCandidate(
        task_kind="summarization",
        provider_name="local",
        model_name="qwen-lite",
        output_text="Use regional clusters and bounded deltas.",
        confidence=0.84,
        source_model_tag="local:qwen-lite",
        provenance={},
        provider_role="drone",
        swarm_provider_ids=["local:qwen-lite", "local:qwen-mini"],
    )
    with patch("sandbox.helper_worker.ModelTeacherPipeline.run", return_value=candidate) as run_mock:
        outcome = run_task_capsule(_capsule(), helper_agent_id="helper-node-1234567890")
    assert outcome.result.summary.startswith("Use regional clusters")
    _, kwargs = run_mock.call_args
    assert kwargs["provider_role"] == "drone"
    assert int(kwargs["swarm_size"]) >= 1
