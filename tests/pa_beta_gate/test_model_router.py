"""pa_beta_gate — model router reliability (deterministic).

Includes the SCHEMA-FAILURE ESCALATION feature built this session: previously a
contract/schema failure was surfaced (validation_state='contract_failed') and the
malformed answer was returned as-is. Now the router treats a contract failure as a
soft failure and escalates to the next ranked candidate, keeping the first
contract-failed decision only as a last resort if every candidate fails.

All deterministic: fabricated manifests + mocked adapters/validator, no live model.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from core.local_inference_autopilot import (
    _deep_lane_fits,
    _message_complexity,
    build_local_inference_autopilot_plan,
)
from core.memory_first_router import (
    VERIFIER_DRAFT_CAVEAT,
    MemoryFirstRouter,
    ModelExecutionDecision,
    _apply_verifier_gate,
    _verifier_verdict,
    apply_verifier_draft_caveat,
    resolve_fallback_budget_seconds,
)
from core.model_health import reset_provider_health
from core.output_validator import OutputValidationResult
from core.provider_routing import ProviderCapabilityTruth
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest

pytestmark = [pytest.mark.pa_beta]


def _manifest(provider_name: str, model: str = "qwen2.5:7b") -> ModelProviderManifest:
    # adapter_type is deliberately NOT one of the local tool-certification probe's targets
    # (_PROBEABLE_ADAPTERS): these fixtures test ROUTER contracts (escalation order, verifier
    # verdicts, annotation), not authorship eligibility. A probeable loopback manifest with no
    # certification rows is correctly refused by the pre-call authorship fence, which would
    # make every adapter call_count assertion assert the fence instead of the routing under
    # test. A lane outside the probe's reach is operator-attested by the authority's own rule,
    # so the router proceeds and the routing contracts stay observable.
    return ModelProviderManifest(
        provider_name=provider_name,
        model_name=model,
        source_type="http",
        adapter_type="local_subprocess",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen2.5",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"deployment_class": "local", "orchestration_role": "queen", "tokens_per_second": 12.0,
                  "tool_support": ["structured_json", "code_complex"]},
    )


def _adapter(text="A grounded answer."):
    a = mock.Mock()
    a.health_check.return_value = {"ok": True}
    a.supports_streaming.return_value = False
    a.estimate_cost_class.return_value = "free_local"
    a.get_license_metadata.return_value = {}
    a.run_text_task.return_value = ModelResponse(output_text=text, confidence=0.72)
    return a


def _run(router, ranked, validate_fn):
    task = SimpleNamespace(task_id="pa-router", task_summary="answer a normal question")
    interpretation = SimpleNamespace(reconstructed_text="answer a normal question")
    report = SimpleNamespace(retrieval_confidence=0.2)
    report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=report)
    # The A9 routing plan is minted from the REGISTRY's real inventory and fences every ranked
    # candidate against it, so the fixtures must be registered — a manifest that exists only in
    # the mocked ranking is exactly the candidate the plan refuses to spend on.
    for manifest in ranked:
        router.registry.register_manifest(manifest)
    with mock.patch("core.memory_first_router._cached_free_vram_gb", return_value=None), mock.patch(
        "core.memory_first_router.rank_provider_candidates", return_value=list(ranked)
    ), mock.patch("core.memory_first_router.validate_provider_output", side_effect=validate_fn), mock.patch.object(
        router.registry, "build_adapter", return_value=_adapter()
    ) as build_adapter:
        decision = router._execute_provider_task(
            task=task,
            classification={"task_class": "chat_conversation"},
            interpretation=interpretation,
            context_result=context_result,
            persona=SimpleNamespace(),
            task_hash="pa-router-hash",
            task_kind="normalization_assist",
            output_mode="plain_text",
            allow_paid_fallback=False,
            provider_role="queen",
            surface="openclaw",
            # The A9 routing-plan fence mints one plan per root turn and fails closed without
            # a server-stamped turn/session identity ("routing_identity_missing") — the served
            # path always carries both. A unit test that bypassed every door is exactly the
            # caller that fence exists to refuse, so the fixture stamps the same identity.
            source_context={
                "surface": "openclaw",
                "turn_id": "pa-router-turn",
                "session_id": "pa-router-session",
            },
        )
    return decision, build_adapter


def _fail_first():
    """Fail the first contract validation (whichever candidate the autopilot tries
    first), pass the rest — order-independent."""
    state = {"failed": False}

    def _val(*, provider_id, output_mode, raw_text, trace_id=None):
        if not state["failed"]:
            state["failed"] = True
            return OutputValidationResult(ok=False, normalized_text=raw_text, error="contract mismatch")
        return OutputValidationResult(ok=True, normalized_text=raw_text)

    return _val


@pytest.fixture(autouse=True)
def _db():
    run_migrations()
    reset_provider_health()


# ---------------------------------------------------------------------------
# Schema/contract-failure escalation (the feature)
# ---------------------------------------------------------------------------

def test_contract_failure_escalates_to_the_next_candidate():
    router = MemoryFirstRouter()
    m1, m2 = _manifest("ollama-a"), _manifest("ollama-b")
    decision, build_adapter = _run(router, [m1, m2], _fail_first())

    assert build_adapter.call_count == 2, "router did not escalate past the contract-failing candidate"
    assert decision.validation_state == "valid"       # the escalated-to candidate's output validated
    assert decision.provider_id in {m1.provider_id, m2.provider_id}
    assert decision.failover_used is True


def test_all_candidates_failing_contract_returns_the_first_annotated_not_nothing():
    router = MemoryFirstRouter()
    m1, m2 = _manifest("ollama-a"), _manifest("ollama-b")

    def _always_fail(*, provider_id, output_mode, raw_text, trace_id=None):
        return OutputValidationResult(ok=False, normalized_text=raw_text, error="contract mismatch")

    decision, build_adapter = _run(router, [m1, m2], _always_fail)

    assert build_adapter.call_count == 2              # both candidates were tried
    assert decision.validation_state == "contract_failed"  # honestly annotated
    assert decision.used_model is True                # still delivers an answer, not silence


def test_valid_first_response_does_not_escalate():
    router = MemoryFirstRouter()
    m1, m2 = _manifest("ollama-a"), _manifest("ollama-b")

    def _all_ok(*, provider_id, output_mode, raw_text, trace_id=None):
        return OutputValidationResult(ok=True, normalized_text=raw_text)

    decision, build_adapter = _run(router, [m1, m2], _all_ok)

    assert build_adapter.call_count == 1              # common path unchanged: no needless second call
    assert decision.validation_state == "valid"
    assert decision.provider_id in {m1.provider_id, m2.provider_id}


# ---------------------------------------------------------------------------
# Verifier gating (the feature): a failing verifier withholds the clean "done"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verifier_text,expected", [
    ("VERDICT: FAIL - the staging port should be 8096, not 9090.", "fail"),
    ("VERDICT: PASS - correct and complete.", "pass"),
    ("FAIL: the answer is missing a critical caveat.", "fail"),
    ("Looks good to me, no blocking concerns.", "invalid"),
    ("", "invalid"),
    # Codex Phase 2 F6 — structured-first parsing:
    ("FAIL is not the right call here. VERDICT: PASS - the answer is safe.", "pass"),  # explicit beats negated prose
    ('{"verdict":"FAIL","reason":"missing exact value"}', "fail"),                     # JSON verdict
    ('{"verdict":"pass"}', "pass"),
    ("**VERDICT: PASS** looks correct", "pass"),                                       # markdown-wrapped
    ("verdict: fail", "fail"),                                                          # lowercase
    ("VERDICT: PASS at first, but on reflection VERDICT: FAIL.", "fail"),               # last explicit wins
])
def test_verifier_verdict_parsing(verifier_text, expected):
    assert _verifier_verdict(verifier_text) == expected


def test_apply_verifier_gate_flags_a_failed_verifier():
    d = ModelExecutionDecision(source="provider_execution", task_hash="h", trust_score=0.9, used_model=True, details={})
    _apply_verifier_gate(d, "flagged")
    assert d.details["needs_review"] is True
    assert d.details["verifier_gate"] == "flagged"
    assert d.trust_score <= 0.5


@pytest.mark.parametrize(
    "status",
    [
        "independent_completed",
        "not_required",
        "blocked",
        "degraded_same_model",
        "independent_failed",
        "independent_malformed",
    ],
)
def test_apply_verifier_gate_leaves_passing_or_unavailable_untouched(status):
    d = ModelExecutionDecision(source="provider_execution", task_hash="h", trust_score=0.9, used_model=True, details={})
    _apply_verifier_gate(d, status)
    assert "needs_review" not in d.details
    assert d.trust_score == 0.9  # unchanged


def test_verify_primary_response_flags_a_failing_verdict():
    router = MemoryFirstRouter()
    primary = _manifest("ollama-primary")
    verifier = _manifest("ollama-verifier", "qwen3:14b")
    plan = {
        "verifier_required": True, "verifier_provider_id": verifier.provider_id,
        "verifier_model": "qwen3:14b", "selected_provider_id": primary.provider_id,
    }
    v_adapter = _adapter("VERDICT: FAIL - the staging port should be 8096, not 9090.")
    with mock.patch.object(router.registry, "build_adapter", return_value=v_adapter):
        status = router._verify_primary_response(
            primary_manifest=primary,
            primary_request=ModelRequest(
                task_kind="action_plan",
                prompt="check the staging port",
            ),
            primary_response=ModelResponse(output_text="the staging port is 9090", confidence=0.7),
            ranked_manifests=[primary, verifier],
            autopilot_plan=plan,
            task=SimpleNamespace(task_id="v"),
            classification={"task_class": "debugging"},
            task_kind="action_plan",
            output_mode="plain_text",
            source_context={"surface": "openclaw"},
            failed_provider_ids=set(),
        )
    assert status == "flagged"


def test_verify_primary_response_passes_a_clean_verdict():
    router = MemoryFirstRouter()
    primary = _manifest("ollama-primary")
    verifier = _manifest("ollama-verifier", "qwen3:14b")
    plan = {
        "verifier_required": True, "verifier_provider_id": verifier.provider_id,
        "verifier_model": "qwen3:14b", "selected_provider_id": primary.provider_id,
    }
    v_adapter = _adapter("VERDICT: PASS - correct and complete.")
    with mock.patch.object(router.registry, "build_adapter", return_value=v_adapter):
        status = router._verify_primary_response(
            primary_manifest=primary,
            primary_request=ModelRequest(
                task_kind="action_plan",
                prompt="check the staging port",
            ),
            primary_response=ModelResponse(output_text="the staging port is 8096", confidence=0.7),
            ranked_manifests=[primary, verifier],
            autopilot_plan=plan,
            task=SimpleNamespace(task_id="v"),
            classification={"task_class": "debugging"},
            task_kind="action_plan",
            output_mode="plain_text",
            source_context={"surface": "openclaw"},
            failed_provider_ids=set(),
        )
    assert status == "independent_completed"


# ---------------------------------------------------------------------------
# Lane routing, complexity, VRAM fit, budget, verifier-required (deterministic)
# ---------------------------------------------------------------------------

def _cap(model_id, *, role_fit, tps, tools=("structured_json",)):
    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{model_id}", model_id=model_id, role_fit=role_fit,
        context_window=8192, tool_support=tools, structured_output_support=True,
        tokens_per_second=tps, ram_budget_gb=0.0, vram_budget_gb=0.0, quantization="q4_K_M",
        locality="local", privacy_class="local_private", queue_depth=0, max_safe_concurrency=1,
        availability_state="ready",
    )


def _caps():
    return (
        _cap("qwen2.5:0.5b", role_fit="drone", tps=313.0),
        _cap("qwen3:8b", role_fit="drone", tps=16.4),
        _cap("qwen3:14b", role_fit="queen", tps=11.4, tools=("structured_json", "code_complex")),
        _cap("qwen2.5:32b", role_fit="queen", tps=0.3, tools=("structured_json", "code_complex")),
    )


def _plan(user_text, task_kind, output_mode, provider_role, *, free_vram_gb=None):
    return build_local_inference_autopilot_plan(
        user_text=user_text, task_kind=task_kind, output_mode=output_mode,
        provider_role=provider_role, capability_truth=_caps(), free_vram_gb=free_vram_gb,
    )


# --- lane routing: easy -> small, deep repo/code -> large ---
LANE_CASES = [
    ("Hi", "normalization_assist", "plain_text", "auto", "tiny"),
    ("hey there", "normalization_assist", "plain_text", "auto", "tiny"),
    ("thanks so much", "normalization_assist", "plain_text", "auto", "tiny"),
    ("classify this tool intent", "tool_intent", "tool_intent", "drone", "tiny"),
    ("what can we do today?", "normalization_assist", "plain_text", "auto", "daily"),
    ("summarize my notes from yesterday", "normalization_assist", "plain_text", "auto", "daily"),
    ("read pyproject.toml and summarize the project metadata", "normalization_assist", "plain_text", "queen", "daily"),
    ("patch the local runtime and run the tests", "coding_help_complex", "action_plan", "queen", "deep"),
    ("refactor the auth module across these files and run tests", "coding_help_complex", "action_plan", "queen", "deep"),
    ("debug this cross-file production bug and propose a fix", "coding_help_complex", "action_plan", "queen", "deep"),
]


@pytest.mark.parametrize("text,task_kind,output_mode,role,lane", LANE_CASES)
def test_lane_routing(text, task_kind, output_mode, role, lane):
    plan = _plan(text, task_kind, output_mode, role)
    assert plan.lane == lane, f"{text!r} -> {plan.lane}, expected {lane}"


def test_easy_task_uses_the_small_model_and_deep_uses_the_large():
    tiny = _plan("Hi", "normalization_assist", "plain_text", "auto")
    deep = _plan("patch the local runtime and run tests", "coding_help_complex", "action_plan", "queen")
    assert tiny.selected_model == "qwen2.5:0.5b"
    assert deep.selected_model == "qwen3:14b"          # deep, but never defaults to the oversized 32b
    assert "qwen2.5:32b" not in {deep.selected_model, deep.verifier_model}


# --- complexity routing boundary ---
COMPLEXITY_CASES = [
    ("Hi", "trivial"), ("hey there", "trivial"), ("thanks!", "trivial"),
    ("build me a full-stack dashboard", "heavy"), ("create a telegram bot with an api backend", "heavy"),
    ("build me test123.null website", "heavy"),
    ("how do i build a website?", ""), ("the docs say to run npm run build", ""),
    ("what changed in the config?", ""), ("write a short reply", ""),
]


@pytest.mark.parametrize("text,expected", COMPLEXITY_CASES)
def test_message_complexity_routing(text, expected):
    assert _message_complexity(text) == expected


# --- VRAM fit: deep downgrades when the model does not fit ---
def test_deep_lane_fits_gates_on_free_vram():
    caps = _caps()
    assert _deep_lane_fits(caps, free_vram_gb=12.0) is True     # 14b fits in 12GB
    assert _deep_lane_fits(caps, free_vram_gb=6.9) is False     # does not fit a 7GB card
    assert _deep_lane_fits(caps, free_vram_gb=None) is True     # unknown -> allowed


@pytest.mark.parametrize("free_vram,expected_lane", [(12.0, "deep"), (6.9, "daily"), (None, "deep")])
def test_deep_downgrades_to_daily_when_vram_is_tight(free_vram, expected_lane):
    plan = _plan("patch the local runtime and run tests", "coding_help_complex", "action_plan", "queen",
                 free_vram_gb=free_vram)
    assert plan.lane == expected_lane


# --- fallback budget matrix ---
@pytest.mark.parametrize("lane", ["deep", "cloud", "human"])
def test_heavy_lanes_have_no_fallback_budget(lane):
    assert resolve_fallback_budget_seconds(lane, forced_cpu=False, no_usable_gpu=False) is None
    assert resolve_fallback_budget_seconds(lane, forced_cpu=True, no_usable_gpu=True) is None


def test_ordinary_lane_budget_gpu_vs_cpu():
    assert resolve_fallback_budget_seconds("daily", forced_cpu=False, no_usable_gpu=False) == 60.0
    assert resolve_fallback_budget_seconds("daily", forced_cpu=True, no_usable_gpu=False) == 180.0
    assert resolve_fallback_budget_seconds("daily", forced_cpu=False, no_usable_gpu=True) == 180.0


# --- verifier-required for deep / action-plan work ---
def test_deep_action_plan_requires_a_verifier():
    deep = _plan("patch the local runtime and run tests", "coding_help_complex", "action_plan", "queen")
    assert deep.verifier_required is True
    assert deep.verifier_model and deep.verifier_model != deep.selected_model  # independent lane


def test_trivial_turn_needs_no_verifier():
    tiny = _plan("Hi", "normalization_assist", "plain_text", "auto")
    assert tiny.verifier_required is False


# ---------------------------------------------------------------------------
# All-models-fail / no-provider behavior (Codex F10)
# ---------------------------------------------------------------------------

def _drive(router, ranked, build_adapter_mock):
    task = SimpleNamespace(task_id="fail", task_summary="answer a normal question")
    interp = SimpleNamespace(reconstructed_text="answer a normal question")
    report = SimpleNamespace(retrieval_confidence=0.2)
    report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    cr = SimpleNamespace(retrieval_confidence_score=0.2, report=report)
    for manifest in ranked:
        router.registry.register_manifest(manifest)
    with mock.patch("core.memory_first_router._cached_free_vram_gb", return_value=None), mock.patch(
        "core.memory_first_router.rank_provider_candidates", return_value=list(ranked)
    ), mock.patch.object(router.registry, "build_adapter", build_adapter_mock):
        return router._execute_provider_task(
            task=task, classification={"task_class": "chat_conversation"}, interpretation=interp,
            context_result=cr, persona=SimpleNamespace(), task_hash="h", task_kind="normalization_assist",
            output_mode="plain_text", allow_paid_fallback=False, provider_role="queen", surface="openclaw",
            source_context={"surface": "openclaw", "turn_id": "pa-router-fail-turn", "session_id": "pa-router-fail-session"},
        )


def test_no_ranked_provider_is_honest_no_fabricated_answer():
    router = MemoryFirstRouter()
    build_adapter = mock.Mock()
    decision = _drive(router, [], build_adapter)
    assert decision.used_model is False
    assert not (decision.output_text or "")        # never fabricates an answer
    assert build_adapter.call_count == 0            # no provider was invoked


def test_all_ranked_providers_failing_is_honest():
    router = MemoryFirstRouter()
    m1, m2 = _manifest("ollama-a"), _manifest("ollama-b")
    bad = _adapter()
    bad.run_text_task.side_effect = RuntimeError("provider down")
    decision = _drive(router, [m1, m2], mock.Mock(return_value=bad))
    assert decision.used_model is False
    assert not (decision.output_text or "")        # all failed -> no fabricated answer, honest degradation


# ---------------------------------------------------------------------------
# Verifier FAIL is made visible to the user (draft caveat), never blocked
# ---------------------------------------------------------------------------

def test_flagged_answer_gets_a_visible_draft_caveat():
    d = ModelExecutionDecision(source="provider_execution", task_hash="h", output_text="answer", details={})
    _apply_verifier_gate(d, "flagged")                       # verifier FAIL sets needs_review
    out = apply_verifier_draft_caveat("Here is the plan.", d)
    assert VERIFIER_DRAFT_CAVEAT in out                      # the user sees the caveat...
    assert "Here is the plan." in out                        # ...and still gets the answer (not blocked)


def test_passing_answer_gets_no_caveat():
    d = ModelExecutionDecision(source="provider_execution", task_hash="h", details={})
    _apply_verifier_gate(d, "independent_completed")         # PASS -> no needs_review
    assert apply_verifier_draft_caveat("The answer.", d) == "The answer."


@pytest.mark.parametrize(
    "status",
    [
        "blocked",
        "degraded_same_model",
        "independent_failed",
        "independent_malformed",
        "not_required",
    ],
)
def test_verifier_unavailable_adds_no_caveat_spam(status):
    d = ModelExecutionDecision(source="provider_execution", task_hash="h", details={})
    _apply_verifier_gate(d, status)                          # unavailable -> no needs_review, no caveat
    assert apply_verifier_draft_caveat("The answer.", d) == "The answer."


def test_draft_caveat_is_idempotent():
    d = ModelExecutionDecision(source="provider_execution", task_hash="h", details={"needs_review": True})
    once = apply_verifier_draft_caveat("The answer.", d)
    assert apply_verifier_draft_caveat(once, d) == once      # not doubled on re-render


def test_draft_caveat_is_a_noop_on_empty_response():
    d = ModelExecutionDecision(source="provider_execution", task_hash="h", details={"needs_review": True})
    assert apply_verifier_draft_caveat("", d) == ""


def test_draft_caveat_does_not_break_an_exact_response_shape():
    d = ModelExecutionDecision(source="provider_execution", task_hash="h", details={"needs_review": True})

    assert apply_verifier_draft_caveat(
        "Apple falls through air\nEarth draws every mass\nGravity binds worlds",
        d,
        preserve_exact_shape=True,
    ).count("\n") == 2


def test_simple_answer_without_verifier_is_unchanged():
    # normal simple answers (no verifier ran) are returned exactly as-is
    d = ModelExecutionDecision(source="provider_execution", task_hash="h", details={})
    assert apply_verifier_draft_caveat("2 + 2 is 4.", d) == "2 + 2 is 4."
