"""`_lane_proof_payload` is the one place actual-model provenance is recorded per turn.

It must always report what REALLY answered (`actual_adapter_provider_id`/`actual_adapter_model_id`,
read from the manifest the router actually invoked), separately from what the autopilot originally
PLANNED (`planned_provider_id`/`planned_model_id`) -- collapsing the two, or reading the actual
fields from the wrong source, would let a substitution happen invisibly: the mismatch flag and the
per-field provenance are what make a fallback auditable instead of silent.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.memory_first_router import _lane_proof_payload


def _classification() -> dict:
    return {"task_class": "chat_conversation"}


def _autopilot_plan(*, provider_id: str, model: str) -> dict:
    return {"selected_provider_id": provider_id, "selected_model": model, "lane": "local"}


def test_provenance_matches_the_manifest_that_actually_answered() -> None:
    manifest = SimpleNamespace(provider_id="local-qwen-http:qwen-local", model_name="qwen-local")
    proof = _lane_proof_payload(
        source_context={"turn_id": "t1", "session_id": "s1"},
        task=SimpleNamespace(task_id="task-1"),
        classification=_classification(),
        task_kind="chat",
        output_mode="plain_text",
        provider_role="queen",
        autopilot_plan=_autopilot_plan(provider_id="local-qwen-http:qwen-local", model="qwen-local"),
        manifest=manifest,
        capability=None,
        phase="completed",
        attempted=["local-qwen-http:qwen-local"],
        failover_used=False,
    )
    assert proof["actual_adapter_provider_id"] == "local-qwen-http:qwen-local"
    assert proof["actual_adapter_model_id"] == "qwen-local"
    assert proof["planned_provider_id"] == "local-qwen-http:qwen-local"
    assert proof["mismatch"] is False


def test_a_fallback_to_a_different_provider_is_visible_not_silent() -> None:
    """The exact shape of a substitution: planned says one provider, the manifest that actually
    answered names a different one. Both must be independently readable off the proof, and
    `mismatch` must flag it -- this is what makes the operator's "never silently substitute"
    invariant auditable after the fact, not just enforced at routing time."""
    manifest = SimpleNamespace(provider_id="openrouter-byok:vendor/other-model:free", model_name="vendor/other-model:free")
    proof = _lane_proof_payload(
        source_context={"turn_id": "t2", "session_id": "s2"},
        task=SimpleNamespace(task_id="task-2"),
        classification=_classification(),
        task_kind="chat",
        output_mode="plain_text",
        provider_role="queen",
        autopilot_plan=_autopilot_plan(provider_id="local-qwen-http:qwen-local", model="qwen-local"),
        manifest=manifest,
        capability=None,
        phase="completed",
        attempted=["local-qwen-http:qwen-local", "openrouter-byok:vendor/other-model:free"],
        failover_used=True,
    )
    assert proof["planned_provider_id"] == "local-qwen-http:qwen-local"
    assert proof["actual_adapter_provider_id"] == "openrouter-byok:vendor/other-model:free"
    assert proof["planned_provider_id"] != proof["actual_adapter_provider_id"]
    assert proof["mismatch"] is True


def test_no_manifest_answered_leaves_actual_provenance_honestly_empty() -> None:
    """No fabricated attribution when nothing actually ran -- an empty string, not the plan."""
    proof = _lane_proof_payload(
        source_context={"turn_id": "t3", "session_id": "s3"},
        task=SimpleNamespace(task_id="task-3"),
        classification=_classification(),
        task_kind="chat",
        output_mode="plain_text",
        provider_role="queen",
        autopilot_plan=_autopilot_plan(provider_id="local-qwen-http:qwen-local", model="qwen-local"),
        manifest=None,
        capability=None,
        phase="blocked",
        attempted=[],
        failover_used=False,
        fallback_reason="no_ranked_provider",
    )
    assert proof["actual_adapter_provider_id"] == ""
    assert proof["actual_adapter_model_id"] == ""
    assert proof["planned_provider_id"] == "local-qwen-http:qwen-local"  # the plan is still visible
