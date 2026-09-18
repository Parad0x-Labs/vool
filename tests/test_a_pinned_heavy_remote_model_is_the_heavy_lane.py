"""A pinned heavy REMOTE model is the heavy lane; the autopilot's local view must not block it.

Live failure 2026-09-08 (build 0.5.0, operator profile): the operator pinned a 26B free cloud model.
The autopilot read the size in that name as an explicit heavy request, found no heavy LOCAL lane,
raised ``explicit_heavy_lane_unavailable``, and the router refused the turn before any adapter ran
while ``ranked_candidates`` held exactly that pinned model. Every ordinary question ended in
"I couldn't get a usable model response".

The genuine block stays: a heavy request with only small lanes ranked is still refused rather than
served by a smaller model behind the label (the control below, and
tests/test_local_inference_autopilot.py::test_memory_router_blocks_explicit_heavy_plan_without_adapter_fallback).

Held-out names only.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest import mock

from core.memory_first_router import MemoryFirstRouter
from core.model_health import reset_provider_health
from core.runtime_task_events import register_runtime_event_sink, unregister_runtime_event_sink
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest

HEAVY_PIN = "vendor/big-mixture-30b-a6b-it:free"
SMALL_PIN = "vendor/mid-12b-it:free"


def _remote(model_name: str) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://example.invalid/license",
        weight_location="external",
        runtime_dependency="stub",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "https://openrouter.ai/api/v1", "timeout_seconds": 30},
        metadata={"deployment_class": "remote", "cost_class": "paid_cloud", "tokens_per_second": 40.0},
    )


def _heavy_plan(selected_model: str | None):
    return SimpleNamespace(
        lane="deep",
        selected_provider_id=None,
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1",
            "lane": "deep",
            "explicit_heavy": True,
            "selected_provider_id": None,
            "selected_model": selected_model,
            "verifier_required": True,
            "verifier_provider_id": None,
            "verifier_model": None,
            "prefix_cache": {"backend": "", "supported": False},
            "warnings": ["no_local_or_provider_lane_available", "explicit_heavy_lane_unavailable"],
        },
    )


def _reset_db() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def _drive(stream: str, ranked: list[ModelProviderManifest], plan, text: str):
    router = MemoryFirstRouter()
    # The routing plan reads its candidates from the registry, the ranker mock orders them.
    ranked = [router.registry.register_manifest(manifest) for manifest in ranked]
    events: list[dict] = []
    register_runtime_event_sink(stream, events.append)
    adapters_built: list[str] = []

    class _LaneReachedError(RuntimeError):
        """Raised by the adapter stub: the router got past every pre-adapter gate for this manifest."""

    def _build(manifest, *args, **kwargs):
        adapters_built.append(str(manifest.provider_id))
        raise _LaneReachedError(str(manifest.provider_id))

    task = SimpleNamespace(task_id=f"{stream}-task", task_summary=text)
    interpretation = SimpleNamespace(reconstructed_text=text)
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)
    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=ranked), mock.patch.object(
            router.registry, "build_adapter", side_effect=_build
        ), mock.patch("core.memory_first_router.build_local_inference_autopilot_plan", return_value=plan), mock.patch(
            # the operator's catalog cache prices these :free pins at zero; the paid fence is not under test
            # here. Both bindings: the router imported the name, the plan's fence reads the module.
            "core.model_selection_policy.is_verified_free_cloud_manifest",
            return_value=True,
        ), mock.patch("core.memory_first_router.is_verified_free_cloud_manifest", return_value=True):
            decision = None
            with contextlib.suppress(_LaneReachedError):
                decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "chat_conversation"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash=f"{stream}-hash",
                task_kind="normalization_assist",
                output_mode="plain_text",
                allow_paid_fallback=False,
                provider_role="auto",
                surface="openclaw",
                # No requested_model here: an explicit pin is resolved against the registry before
                # ranking, and these manifests are ranked by the mock, not registered. The fake plan
                # carries the explicit_heavy verdict the pin would have produced.
                source_context={
                    "runtime_event_stream_id": stream,
                    "surface": "openclaw",
                    "turn_id": f"{stream}-turn",
                    "session_id": f"{stream}-session",
                },
                )
    finally:
        unregister_runtime_event_sink(stream)
    return decision, events, adapters_built


def test_a_pinned_heavy_remote_model_is_not_blocked_by_the_missing_local_heavy_lane() -> None:
    _reset_db()
    decision, events, built = _drive(
        "pinned-heavy-remote-lane",
        [_remote(HEAVY_PIN)],
        _heavy_plan(HEAVY_PIN),
        "what would a set of four all-season tyres cost for a 2012 estate, roughly?",
    )
    blocked = [e for e in events if e["event_type"] == "model_routing_failed" and e.get("rejection_reason") == "explicit_heavy_lane_unavailable"]
    assert not blocked, "the pinned heavy remote model was refused before any adapter ran"
    assert decision is None or decision.source != "autopilot_blocked"
    selected = [e for e in events if e["event_type"] == "model_lane_selected"]
    assert selected and selected[0]["provider_id"] == f"openrouter-byok:{HEAVY_PIN}"
    # The strongest signal: the runtime built the adapter for the pinned model itself.
    assert built == [f"openrouter-byok:{HEAVY_PIN}"]


def test_a_heavy_request_with_only_small_lanes_ranked_is_still_refused() -> None:
    _reset_db()
    decision, events, built = _drive(
        "heavy-request-small-lanes",
        [_remote(SMALL_PIN)],
        _heavy_plan(SMALL_PIN),
        "run this through the 40b heavy lane: summarise the tyre market for estates",
    )
    assert decision.source == "autopilot_blocked" and decision.used_model is False
    proof = [e for e in events if e["event_type"] == "model_lane_proof"][-1]
    assert proof["phase"] == "blocked" and proof["fallback_reason"] == "explicit_heavy_lane_unavailable"
    assert built == []
