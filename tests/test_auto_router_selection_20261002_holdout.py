"""Auto selection holdout — frozen 2026-10-02 before tuning (mission auto-router-selection-20261002).

Cases were written from the pinned public base dca4679f's REPRODUCED behavior
(baseline probes P1-P6, preserved in the mission artifacts) and must keep passing after any
further selection tuning. They pin the eligibility-first law and the value-decision priors:

* a REQUIRED capability is eligibility, not a score bonus a cheaper model can out-rank;
* a declared context window below the envelope's serialized-payload requirement is a hard
  rejection, mirroring the cloud lane's ``context_too_small`` law;
* an explicitly required model tool capability is a hard gate; a web.* permission GRANT
  stays a soft preference (it authorizes VOOL's runtime tool, not model-native support);
* latency_budget/quality_target (the envelope vocabulary resource_scheduler already
  consumes) move measured/local-band evidence — never unknown data;
* pins, local-only, privacy, health and zero-candidate behavior are unchanged controls.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.cloud_provider_contract import CloudModelMetadata, CloudTaskRequirements, PricingState, PrivacyClass
from core.cloud_routing import CloudRouteMode, select_cloud_route
from core.model_health import record_provider_failure, reset_provider_health
from core.model_registry import ModelRegistry
from core.model_selection_policy import (
    ModelSelectionRequest,
    explain_provider_exclusions,
    rank_providers,
)
from core.orchestration.task_envelope import TaskEnvelopeV1
from core.provider_routing import resolve_provider_routing_plan_for_envelope
from storage.db import get_connection
from storage.model_provider_manifest import ModelProviderManifest


def _clear_manifests() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


def _local(provider: str, model: str, capabilities: list[str], **extra) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider,
        model_name=model,
        source_type="subprocess",
        license_name="MIT",
        license_reference="https://example.com/license",
        runtime_dependency="ollama",
        capabilities=capabilities,
        **extra,
    )


def _registry(*manifests: ModelProviderManifest) -> ModelRegistry:
    _clear_manifests()
    reset_provider_health()
    registry = ModelRegistry()
    for manifest in manifests:
        registry.register_manifest(manifest)
    return registry


def _envelope(task_id: str, role: str, goal: str, **kwargs) -> TaskEnvelopeV1:
    return TaskEnvelopeV1(task_id=task_id, parent_task_id="", role=role, goal=goal, **kwargs)


# --------------------------------------------------------------------------------------
# Required capability is eligibility (baseline P1)
# --------------------------------------------------------------------------------------


def test_json_turn_excludes_jsonless_model_when_complete_candidate_exists() -> None:
    registry = _registry(
        _local("cheap-local", "qwen3:4b", ["classify"]),
        _local("complete-local", "qwen3:8b", ["classify", "structured_json"]),
    )
    ranked = registry.rank_manifests(
        ModelSelectionRequest(task_kind="classification", output_mode="json_object")
    )
    assert [m.model_name for m in ranked] == ["qwen3:8b"]


def test_missing_hard_capability_never_serves_even_when_nothing_is_complete() -> None:
    # structured_json is the turn's output contract, not a skill preference: a JSON-less model
    # is not eligible for a json_object turn just because no complete candidate exists. The
    # empty ranking goes to the caller's existing refusal instead of an unsupported dispatch.
    registry = _registry(_local("only-local", "qwen3:4b", ["classify"]))
    ranked = registry.rank_manifests(
        ModelSelectionRequest(task_kind="classification", output_mode="json_object")
    )
    assert [m.model_name for m in ranked] == []


def test_modality_change_alters_the_eligible_set() -> None:
    # multimodal_review requires {multimodal, summarize}: a text-only complete-for-summarize
    # model must not serve while a multimodal one exists, and the same registry must keep
    # serving it for a plain summarization turn (requirements changed, set changed).
    text_only = _local("text-local", "texty:7b", ["summarize", "structured_json"])
    multimodal = _local("vision-local", "visiony:8b", ["summarize", "multimodal"])
    registry = _registry(text_only, multimodal)
    review = registry.rank_manifests(ModelSelectionRequest(task_kind="multimodal_review"))
    assert [m.model_name for m in review] == ["visiony:8b"]
    summary = registry.rank_manifests(ModelSelectionRequest(task_kind="summarization"))
    assert "texty:7b" in [m.model_name for m in summary]


def test_rank_providers_direct_matches_registry_eligibility_law() -> None:
    manifests = [
        _local("cheap-local", "qwen3:4b", ["classify"]),
        _local("complete-local", "qwen3:8b", ["classify", "structured_json"]),
    ]
    ranked = rank_providers(manifests, ModelSelectionRequest(task_kind="tag", output_mode="json_object"))
    assert [m.model_name for m in ranked] == ["qwen3:8b"]


def test_explain_provider_exclusions_reports_capability_completeness() -> None:
    manifests = [
        _local("cheap-local", "qwen3:4b", ["classify"]),
        _local("complete-local", "qwen3:8b", ["classify", "structured_json"]),
    ]
    rows = explain_provider_exclusions(manifests, ModelSelectionRequest(task_kind="classification", output_mode="json_object"))
    by_model = {row["model"]: row for row in rows}
    assert by_model["qwen3:4b"]["required_capability_complete"] == "0"
    assert by_model["qwen3:8b"]["required_capability_complete"] == "1"
    # No required capabilities (unknown task kind, plain output) -> blank, never a verdict.
    plain = explain_provider_exclusions(manifests, ModelSelectionRequest(task_kind="freeform_chat"))
    assert all(row["required_capability_complete"] == "" for row in plain)


# --------------------------------------------------------------------------------------
# Context eligibility (baseline P2)
# --------------------------------------------------------------------------------------


def test_envelope_rejects_context_window_below_requirement() -> None:
    registry = _registry(
        _local("small-ctx", "tiny-ctx:7b", ["summarize"], metadata={"context_window": 4096}),
        _local("big-ctx", "wide-ctx:7b", ["summarize"], metadata={"context_window": 32768}),
    )
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope(
            "t-ctx",
            "researcher",
            "Summarize the supplied long document",
            model_constraints={"min_context_tokens": 12000},
        ),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "wide-ctx:7b"
    reasons = {row["provider_id"]: row["reason"] for row in plan.rejected_candidates}
    assert reasons.get("small-ctx:tiny-ctx:7b") == "context_window_too_small"


def test_envelope_context_requirement_includes_completion_headroom() -> None:
    registry = _registry(
        _local("edge-ctx", "edge:7b", ["summarize"], metadata={"context_window": 8192}),
        _local("roomy-ctx", "roomy:7b", ["summarize"], metadata={"context_window": 32768}),
    )
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope(
            "t-head",
            "researcher",
            "Summarize then answer at length",
            model_constraints={"min_context_tokens": 6000, "expected_output_tokens": 4096},
        ),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "roomy:7b"
    assert plan.routing_requirements["required_window_tokens"] == 10096


def test_envelope_unknown_context_window_is_not_rejected() -> None:
    # Unknown (0) must not hard-reject: that would brick every manifest without window
    # metadata. It only loses the known-sufficient preference.
    registry = _registry(_local("plain-local", "plain:7b", ["summarize"]))
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope(
            "t-unknown",
            "researcher",
            "Summarize",
            model_constraints={"min_context_tokens": 12000},
        ),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "plain:7b"
    assert plan.rejected_candidates == ()


# --------------------------------------------------------------------------------------
# Tool requirements (baseline P3 + the hard-channel ruling)
# --------------------------------------------------------------------------------------


def test_envelope_required_tool_support_is_a_hard_gate() -> None:
    registry = _registry(
        _local("plain-local", "plain:8b", ["summarize"]),
        _local(
            "tool-local",
            "toolful:8b",
            ["summarize"],
            metadata={"tool_support": ["web_search"]},
        ),
    )
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope(
            "t-tool",
            "researcher",
            "Search the web and summarize",
            model_constraints={"required_tool_support": ["web_search"]},
        ),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "toolful:8b"
    reasons = {row["provider_id"]: row["reason"] for row in plan.rejected_candidates}
    assert reasons.get("plain-local:plain:8b") == "missing_required_tool_support"


def test_envelope_required_tool_support_rejects_undeclared_support() -> None:
    # Unknown tool support cannot be assumed true: an empty declaration fails the gate.
    registry = _registry(_local("plain-local", "plain:8b", ["summarize"]))
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope(
            "t-tool2",
            "researcher",
            "Search the web",
            model_constraints={"required_tool_support": ["web_search"]},
        ),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is None
    assert plan.rejected_candidates[0]["reason"] == "missing_required_tool_support"


def test_web_permission_grant_stays_a_soft_preference() -> None:
    # A web.* PERMISSION authorizes VOOL's runtime web tool; it is not a demand for
    # model-native web_search, so an otherwise-complete model without native support stays
    # eligible (it merely loses the soft preference when a native supporter is present).
    registry = _registry(_local("plain-local", "plain:8b", ["summarize"]))
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-perm", "researcher", "Find release notes", tool_permissions=("web.search",)),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "plain:8b"


# --------------------------------------------------------------------------------------
# Latency / quality budgets (baseline P4)
# --------------------------------------------------------------------------------------


def test_low_latency_budget_prefers_measured_fast_candidate() -> None:
    registry = _registry(
        _local("slow-heavy", "heavy:14b", ["summarize"], metadata={"tokens_per_second": 4.0}),
        _local("fast-small", "quick:4b", ["summarize"], metadata={"tokens_per_second": 60.0}),
    )
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope(
            "t-fast", "researcher", "Answer in one sentence", latency_budget="low_latency", quality_target="draft"
        ),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "quick:4b"


def test_balanced_default_keeps_the_stronger_candidate() -> None:
    # Same registry, no latency/quality signal: the prior ordering must stand (the fast
    # candidate must not win merely because it is fast when nothing asked for speed).
    registry = _registry(
        _local("slow-heavy", "heavy:14b", ["summarize"], metadata={"tokens_per_second": 4.0}),
        _local("fast-small", "quick:4b", ["summarize"], metadata={"tokens_per_second": 60.0}),
    )
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-bal", "researcher", "Answer in one sentence"),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "heavy:14b"


def test_latency_budget_with_unmeasured_candidates_is_deterministic() -> None:
    # tokens_per_second == 0 is UNKNOWN, never "slow": unmeasured candidates keep the
    # prior ordering under a low_latency budget (optional enrichment absent -> deterministic).
    registry = _registry(
        _local("first", "alpha:7b", ["summarize"]),
        _local("second", "beta:7b", ["summarize"]),
    )
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-unknown-tps", "researcher", "Hi", latency_budget="low_latency"),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None
    assert not any("measured_speed" in row["terms"] for row in plan.selection_scores)


def test_high_quality_target_prefers_the_stronger_local_lane() -> None:
    registry = _registry(
        _local("small", "quick:4b", ["summarize"], metadata={"tokens_per_second": 60.0}),
        _local("strong", "heavy:14b", ["summarize"], metadata={"tokens_per_second": 4.0}),
    )
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-quality", "researcher", "Deep analysis", quality_target="high"),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "heavy:14b"


# --------------------------------------------------------------------------------------
# Value-tradeoff receipt (mission: explain why a fast cheap route lost or won)
# --------------------------------------------------------------------------------------


def test_decision_receipt_carries_scores_and_terms() -> None:
    registry = _registry(
        _local("slow-heavy", "heavy:14b", ["summarize"], metadata={"tokens_per_second": 4.0}),
        _local("fast-small", "quick:4b", ["summarize"], metadata={"tokens_per_second": 60.0}),
    )
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope(
            "t-receipt", "researcher", "Answer", latency_budget="low_latency", quality_target="draft"
        ),
        task_kind="summarization",
        output_mode="plain_text",
    )
    rows = {row["model_id"]: row for row in plan.selection_scores}
    assert set(rows) == {"heavy:14b", "quick:4b"}
    assert rows["quick:4b"]["terms"]["measured_speed"] > 0.0
    assert rows["quick:4b"]["score"] > rows["heavy:14b"]["score"]


def test_decision_note_names_the_speed_tradeoff_when_a_slower_route_wins() -> None:
    # quality high + balanced latency: the stronger lane wins over the fastest measured one,
    # and the receipt must say so with both numbers.
    registry = _registry(
        _local("small", "quick:4b", ["summarize"], metadata={"tokens_per_second": 60.0}),
        _local("strong", "heavy:14b", ["summarize"], metadata={"tokens_per_second": 8.0}),
    )
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-note", "researcher", "Deep analysis", latency_budget="low_latency", quality_target="high"),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "heavy:14b"
    assert any("low_latency budget was overridden" in note for note in plan.selection_notes)


# --------------------------------------------------------------------------------------
# Controls: pins, locality, privacy, health, isolation, fail-closed
# --------------------------------------------------------------------------------------


def test_pinned_manual_choice_is_not_silently_replaced() -> None:
    registry = _registry(
        _local("other-local", "other:8b", ["summarize", "structured_json"]),
        _local("pinned-local", "special:2b", ["summarize"]),
    )
    ranked = registry.rank_manifests(
        ModelSelectionRequest(task_kind="summarization", preferred_provider="pinned-local", preferred_model="special:2b")
    )
    assert [m.model_name for m in ranked] == ["special:2b"]


def test_private_envelope_fails_closed_to_local_only() -> None:
    from storage.model_provider_manifest import ModelProviderManifest as M

    remote = M(
        provider_name="cloudy",
        model_name="cloudy-model",
        source_type="http",
        license_name="Provider",
        license_reference="user-managed",
        runtime_dependency="remote-openai-compatible-provider",
        capabilities=["summarize"],
        runtime_config={"base_url": "https://cloudy.example"},
        metadata={"deployment_class": "cloud"},
    )
    registry = _registry(remote)
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-priv", "researcher", "Private analysis", privacy_class="local_private"),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is None
    assert plan.rejected_candidates[0]["reason"] == "requires_local_provider"


def test_circuit_open_candidate_is_rejected_and_recovers() -> None:
    registry = _registry(
        _local("flaky", "flaky:7b", ["summarize"]),
        _local("steady", "steady:7b", ["summarize"]),
    )
    record_provider_failure("flaky:flaky:7b", error="timeout", failure_threshold=1, cooldown_seconds=60)
    blocked = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-health", "researcher", "Summarize"),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert blocked.selected is not None and blocked.selected.model_name == "steady:7b"
    assert any(row["reason"] == "provider_circuit_open" for row in blocked.rejected_candidates)
    reset_provider_health()
    recovered = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-health2", "researcher", "Summarize"),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert recovered.selected is not None
    assert not any(row["reason"] == "provider_circuit_open" for row in recovered.rejected_candidates)


def test_two_independent_registries_do_not_share_poison() -> None:
    first = _registry(_local("aa", "alpha:7b", ["summarize"]))
    record_provider_failure("aa:alpha:7b", error="timeout", failure_threshold=1, cooldown_seconds=60)
    second = _registry(_local("bb", "beta:7b", ["summarize"]))
    plan = resolve_provider_routing_plan_for_envelope(
        second,
        envelope=_envelope("t-iso", "researcher", "Summarize"),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "beta:7b"
    assert plan.rejected_candidates == ()


def test_zero_eligible_candidates_fails_closed() -> None:
    registry = _registry(_local("disabled", "off:7b", ["summarize"], enabled=False))
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-zero", "researcher", "Summarize"),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is None
    assert plan.candidates == ()


def test_saturated_local_queue_loses_to_an_equal_candidate() -> None:
    registry = _registry(
        _local("loaded", "loaded:7b", ["summarize"], metadata={"queue_depth": 6, "max_safe_concurrency": 2}),
        _local("idle", "idle:7b", ["summarize"]),
    )
    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-queue", "researcher", "Summarize"),
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert plan.selected is not None and plan.selected.model_name == "idle:7b"
    rows = {row["model_id"]: row for row in plan.selection_scores}
    assert rows["loaded:7b"]["terms"]["queue_pressure"] < 0.0


# --------------------------------------------------------------------------------------
# Cloud-lane controls (verified free vs unknown/paid without authorization)
# --------------------------------------------------------------------------------------

CLOUD_NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


def _cloud_model(model_id: str, **extra) -> CloudModelMetadata:
    base = dict(
        provider_id="provider",
        model_id=model_id,
        display_name=model_id,
        pricing_state=PricingState.FREE,
        input_usd_per_token=0.0,
        output_usd_per_token=0.0,
        request_usd=0.0,
        context_window=8192,
        capabilities=("text", "structured_output"),
        discovered_at="2026-10-01T00:00:00+00:00",
        expires_at="2026-10-03T00:00:00+00:00",
        health_state="healthy",
        latency_ms=100.0,
        quota_remaining=10.0,
        rate_limit_remaining=None,
    )
    base.update(extra)
    return CloudModelMetadata(**base)


CLOUD_REQ = CloudTaskRequirements(
    min_context_tokens=1000,
    expected_output_tokens=100,
    required_capabilities=("structured_output",),
    privacy_class=PrivacyClass.PUBLIC,
)


def _cloud_select(models, **kwargs):
    return select_cloud_route(
        tuple(models),
        requirements=CLOUD_REQ,
        mode=CloudRouteMode.LOCAL_FREE,
        enabled_providers=("provider",),
        network_allowed_providers=("provider",),
        privacy_allowed=True,
        now=CLOUD_NOW,
        **kwargs,
    )


def test_cloud_free_mode_still_rejects_unknown_pricing() -> None:
    plan = _cloud_select(
        [
            _cloud_model("unknown-priced", pricing_state=PricingState.UNKNOWN, input_usd_per_token=None, output_usd_per_token=None, request_usd=None),
            _cloud_model("verified-free"),
        ]
    )
    assert plan.primary is not None and plan.primary.model_id == "verified-free"
    assert any(row["reason"] == "pricing_unknown" for row in plan.rejected)


def test_cloud_paid_requires_approval_and_budget() -> None:
    # Under LOCAL_FREE the mode gate rejects the paid model before the approval gate names itself.
    free_mode = _cloud_select([_cloud_model("paid", pricing_state=PricingState.PAID, input_usd_per_token=0.001, output_usd_per_token=0.001)])
    assert free_mode.primary is None
    assert free_mode.rejected[0]["reason"] == "not_verified_free"
    unapproved = select_cloud_route(
        (_cloud_model("paid", pricing_state=PricingState.PAID, input_usd_per_token=0.001, output_usd_per_token=0.001),),
        requirements=CLOUD_REQ,
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        enabled_providers=("provider",),
        network_allowed_providers=("provider",),
        privacy_allowed=True,
        paid_approved=False,
        now=CLOUD_NOW,
    )
    assert unapproved.primary is None
    assert unapproved.rejected[0]["reason"] == "paid_not_approved"
    approved = select_cloud_route(
        (_cloud_model("paid", pricing_state=PricingState.PAID, input_usd_per_token=0.001, output_usd_per_token=0.001),),
        requirements=CLOUD_REQ,
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        enabled_providers=("provider",),
        network_allowed_providers=("provider",),
        privacy_allowed=True,
        paid_approved=True,
        paid_budget_remaining_usd=2.0,
        now=CLOUD_NOW,
    )
    assert approved.primary is not None and approved.estimated_max_usd is not None
    assert approved.estimated_max_usd <= 2.0
