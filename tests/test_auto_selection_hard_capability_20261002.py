"""Hard capability eligibility for Auto selection.

A turn's required capabilities split into two kinds:

* HARD — the turn's output/tool/modality contract (``structured_json``, ``tool_intent``,
  ``multimodal``). A model whose manifest declares it lacks one cannot serve that turn, and no
  ranking, pin, availability fallback or downstream failover may make it eligible. An empty
  eligible set goes to the existing truthful refusal (no provider / selected model blocked /
  ``no_multimodal_provider``) instead of dispatching to a model that cannot do the job.
* PREFERENCE — descriptive skill tags (``summarize``, ``classify``, ``long_context`` ...). They
  rank candidates; they never remove one that clears every hard rule.

Measured shapes these cases pin: a tool step with only a structured_json-only model, a JSON
turn with only a JSON-less model, and an external-image review with only a text model were all
DISPATCHED to the incapable model, because partial coverage returned as soon as no complete
candidate existed. The exclusion receipt also called the removed candidate "eligible".
"""
from __future__ import annotations

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import runtime_flags
from core.cloud_tool_call_contract import build_cloud_tool_definitions
from core.execution_requirements import RequiredToolsNotOfferedError
from core.media_analysis_pipeline import MediaAnalysisPipeline
from core.media_ingestion import _normalize_item
from core.model_failover import select_with_failover
from core.model_health import reset_provider_health
from core.model_output_contracts import json_schema_for_mode
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest, explain_provider_exclusions
from core.orchestration.task_envelope import TaskEnvelopeV1
from core.provider_routing import rank_provider_candidates, resolve_provider_routing_plan_for_envelope
from storage.db import get_connection
from storage.model_provider_manifest import ModelProviderManifest
from tests._blackbox_served_rig import ScriptedProvider

# The capability sets VOOL's own local bundle profile declares (core.local_model_bundles.
# manifest_profile_for_model): a reasoning lane carries structured_json but deliberately no
# tool_intent; a general lane carries both. Neither declares multimodal.
REASONING_CAPS = ["summarize", "classify", "format", "extract", "structured_json", "code_basic", "code_complex", "long_context"]
GENERAL_CAPS = ["summarize", "classify", "format", "extract", "structured_json", "code_basic", "tool_intent"]
JSONLESS_CAPS = ["summarize", "classify", "format"]
VISION_CAPS = ["summarize", "structured_json", "multimodal"]


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


def _http_local(provider: str, model: str, capabilities: list[str], base_url: str) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider,
        model_name=model,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://example.com/license",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=capabilities,
        runtime_config={"base_url": base_url, "timeout_seconds": 30},
        metadata={"runtime_family": "ollama", "cost_class": "free_local"},
    )


def _registry(*manifests: ModelProviderManifest) -> ModelRegistry:
    _clear_manifests()
    reset_provider_health()
    registry = ModelRegistry()
    for manifest in manifests:
        registry.register_manifest(manifest)
    return registry


def _models(ranked) -> list[str]:
    return [manifest.model_name for manifest in ranked]


# --------------------------------------------------------------------------------------------
# All candidates lack a hard capability -> nothing is eligible
# --------------------------------------------------------------------------------------------


def test_tool_step_with_only_a_tool_incapable_model_has_no_eligible_route() -> None:
    registry = _registry(_local("reason-local", "reasonix:8b", REASONING_CAPS))

    owner = registry.rank_manifests(ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent"))
    # The exact live call shape the Auto tool step uses (role drone, swarm 4, trust floor).
    live = rank_provider_candidates(
        registry,
        task_kind="tool_intent",
        output_mode="tool_intent",
        role="drone",
        swarm_size=4,
        min_trust=0.45,
    )

    assert _models(owner) == []
    assert _models(live) == []


def test_json_turn_with_only_a_jsonless_model_has_no_eligible_route() -> None:
    registry = _registry(_local("plain-local", "plainish:8b", JSONLESS_CAPS))

    ranked = registry.rank_manifests(ModelSelectionRequest(task_kind="classification", output_mode="json_object"))

    assert _models(ranked) == []


def test_media_review_never_hands_an_external_image_to_a_text_only_model() -> None:
    item = _normalize_item({"kind": "image", "url": "https://upload.wikimedia.org/wikipedia/commons/a/a3/cat.png"})
    assert item is not None and item.requires_multimodal and not item.blocked
    with ScriptedProvider({}, default="A text model describing an image it never saw.") as provider:
        registry = _registry(_http_local("text-local", "texty:8b", GENERAL_CAPS, provider.base_url))

        result = MediaAnalysisPipeline(registry).analyze(
            task_id="hard-cap-media-1",
            task_summary="What does this picture show?",
            evidence_items=[item.to_dict()],
        )

        assert result.used_provider is False
        assert result.reason == "no_multimodal_provider"
        assert provider.calls == []


def test_media_review_goes_to_the_model_that_declares_image_input() -> None:
    item = _normalize_item({"kind": "image", "url": "https://upload.wikimedia.org/wikipedia/commons/a/a3/cat.png"})
    assert item is not None and item.requires_multimodal and not item.blocked
    with ScriptedProvider({}, default='{"summary": "a cat on a sofa"}') as provider:
        registry = _registry(
            _http_local("text-local", "texty:8b", GENERAL_CAPS, provider.base_url),
            _http_local("vision-local", "seer:8b", VISION_CAPS, provider.base_url),
        )

        # The adapter's local-load gate reads the REAL machine's free RAM; a scripted loopback
        # stub loads nothing, so pin the governor snapshot the way the governor's own tests do
        # and the proof stays about SELECTION, not about whoever else is using the Mac tonight.
        from unittest import mock

        import core.resource_governor as _gov

        def _roomy(available: float = 24.0, total: float = 32.0):
            snap = mock.Mock()
            snap.total_ram_gb = total
            snap.available_ram_gb = available
            snap.memory_pressure = "normal"
            snap.swap_used_gb = 1.0
            snap.load_per_core = 0.5
            snap.comfyui_running = False
            snap.ollama_loaded_gb = 0.0
            snap.usable_gb = max(0.0, available - total * 0.2)
            return snap

        with (
            mock.patch.object(_gov.sr, "snapshot", return_value=_roomy()),
            mock.patch.object(_gov, "resident_models", return_value=[]),
        ):
            result = MediaAnalysisPipeline(registry).analyze(
                task_id="hard-cap-media-2",
                task_summary="What does this picture show?",
                evidence_items=[item.to_dict()],
            )

        assert result.used_provider is True
        assert result.provider_id == "vision-local:seer:8b"
        assert {call["model"] for call in provider.calls} == {"seer:8b"}


# --------------------------------------------------------------------------------------------
# Requirements decide the set; the same registry serves the turns its models can do
# --------------------------------------------------------------------------------------------


def test_changing_requirements_or_declared_capability_moves_the_eligible_set() -> None:
    registry = _registry(
        _local("reason-local", "reasonix:8b", REASONING_CAPS),
        _local("general-local", "generalist:8b", GENERAL_CAPS),
    )

    plain = registry.rank_manifests(ModelSelectionRequest(task_kind="summarization", output_mode="plain_text"))
    tool = registry.rank_manifests(ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent"))
    media = registry.rank_manifests(ModelSelectionRequest(task_kind="multimodal_review", output_mode="summary_block"))

    assert set(_models(plain)) == {"reasonix:8b", "generalist:8b"}
    assert _models(tool) == ["generalist:8b"]
    assert _models(media) == []

    # The reasoning lane is re-declared tool-capable (e.g. a newer model under the same tag):
    # the very next tool step sees it; the general lane loses tool_intent and leaves the set.
    registry.register_manifest(_local("reason-local", "reasonix:8b", [*REASONING_CAPS, "tool_intent"]))
    registry.register_manifest(_local("general-local", "generalist:8b", [c for c in GENERAL_CAPS if c != "tool_intent"]))
    after = registry.rank_manifests(ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent"))

    assert _models(after) == ["reasonix:8b"]


def test_a_preference_tag_ranks_candidates_but_never_removes_one() -> None:
    # qa_long requires {summarize, long_context}: neither is an output/tool/modality contract.
    registry = _registry(
        _local("long-local", "longctx:8b", ["summarize", "long_context", "format"]),
        _local("short-local", "shortctx:8b", ["summarize", "format"]),
    )

    ranked = registry.rank_manifests(ModelSelectionRequest(task_kind="qa_long", output_mode="plain_text"))

    assert _models(ranked) == ["longctx:8b", "shortctx:8b"]


# --------------------------------------------------------------------------------------------
# Pins: exact where valid, never made capable by being pinned
# --------------------------------------------------------------------------------------------


def test_a_pin_on_a_model_missing_a_hard_capability_is_not_kept_for_that_step() -> None:
    registry = _registry(
        _local("reason-local", "reasonix:8b", REASONING_CAPS),
        _local("general-local", "generalist:8b", GENERAL_CAPS),
    )
    pin = {"preferred_provider": "reason-local", "preferred_model": "reasonix:8b"}

    tool = registry.rank_manifests(ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent", **pin))
    chat = registry.rank_manifests(
        ModelSelectionRequest(task_kind="normalization_assist", output_mode="plain_text", **pin)
    )

    assert "reasonix:8b" not in _models(tool)
    assert _models(chat) == ["reasonix:8b"]


# --------------------------------------------------------------------------------------------
# Receipts and downstream fallback carry the same law
# --------------------------------------------------------------------------------------------


def test_exclusion_receipt_names_the_missing_hard_capability() -> None:
    manifests = [
        _local("reason-local", "reasonix:8b", REASONING_CAPS),
        _local("general-local", "generalist:8b", GENERAL_CAPS),
    ]

    rows = {
        row["model"]: row
        for row in explain_provider_exclusions(
            manifests, ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent")
        )
    }

    assert rows["reasonix:8b"]["status"] == "missing_required_capability:tool_intent"
    assert rows["generalist:8b"]["status"] == "eligible"


def test_failover_after_the_capable_model_never_reaches_an_incapable_one() -> None:
    registry = _registry(
        _local("reason-local", "reasonix:8b", REASONING_CAPS),
        _local("general-local", "generalist:8b", GENERAL_CAPS),
    )

    decision = select_with_failover(
        registry,
        ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent"),
        attempted_provider_ids=["general-local:generalist:8b"],
    )

    assert decision.selected is None


# --------------------------------------------------------------------------------------------
# Envelope requirements: malformed input never widens eligibility; unknown stays visible
# --------------------------------------------------------------------------------------------


def _envelope(task_id: str, **constraints) -> TaskEnvelopeV1:
    return TaskEnvelopeV1(
        task_id=task_id,
        parent_task_id="",
        role="researcher",
        goal="Summarize the attached report",
        model_constraints=dict(constraints),
    )


def test_a_single_required_tool_given_as_a_string_is_honored_not_split() -> None:
    registry = _registry(
        _local("web-local", "webby:8b", ["summarize"], metadata={"tool_support": ["web_search"]}),
    )

    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-tools-str", required_tool_support="web_search"),
        task_kind="summarization",
        output_mode="plain_text",
    )

    assert plan.selected is not None and plan.selected.model_name == "webby:8b"


@pytest.mark.parametrize("malformed", ["12,000", "twelve thousand", -5])
def test_a_malformed_context_requirement_never_admits_a_known_small_window(malformed) -> None:
    registry = _registry(
        _local("small-local", "smallwin:8b", ["summarize"], metadata={"context_window": 4096}),
    )

    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-ctx-bad", min_context_tokens=malformed),
        task_kind="summarization",
        output_mode="plain_text",
    )

    assert plan.selected is None
    assert any("min_context_tokens" in note for note in plan.selection_notes)


def test_an_unknown_window_is_named_and_ranks_after_a_known_sufficient_one() -> None:
    # Equal base scores tie-break on provider name, descending: the unknown-window model ranks
    # first before any context evidence is applied.
    registry = _registry(
        _local("zzz-local", "unknownwin:8b", ["summarize"]),
        _local("aaa-local", "widewin:8b", ["summarize"], metadata={"context_window": 32768}),
    )

    plan = resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-ctx-unknown", min_context_tokens=12000),
        task_kind="summarization",
        output_mode="plain_text",
        swarm_size=2,
    )

    assert plan.selected is not None and plan.selected.model_name == "widewin:8b"
    assert [m.model_name for m in plan.candidates] == ["widewin:8b", "unknownwin:8b"]
    assert any("unknownwin:8b" in note and "unknown" in note for note in plan.selection_notes)


# --------------------------------------------------------------------------------------------
# Tool-step eligibility follows the dialect the adapter actually sends, in both flag states
# --------------------------------------------------------------------------------------------
#
# A lane declaring ``tool_dialect: prompted`` and no JSON mode/schema is excluded from a tool step
# only when its payload would carry neither native tools nor a structured-output fallback. The
# adapter decides native tools: with ``capability_tool_dialect`` OFF (the rollback path) it
# ignores the declaration and keeps the historical OpenRouter rule, so an OpenRouter lane still
# gets native tools there and must stay eligible. Measured on 92879e9: that lane was ranked
# nowhere although its real payload carried the native tool and the final guard accepted it.

READ_TOOLS = build_cloud_tool_definitions(
    [{"intent": "workspace.read_file", "description": "Read one workspace file.", "arguments": {"path": "string"}}]
)


def _router_lane(provider: str, model: str, base_url: str, **runtime) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider,
        model_name=model,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://example.com/license",
        weight_location="external",
        runtime_dependency="openai-compatible-server",
        capabilities=GENERAL_CAPS,
        runtime_config={"base_url": base_url, "api_path": "/v1/chat/completions", "timeout_seconds": 10, **runtime},
        metadata={"runtime_family": "openai-compatible", "cost_class": "free_local"},
    )


def _read_request() -> ModelRequest:
    return ModelRequest(
        task_kind="tool_intent",
        prompt="open notes/quarterly.md and tell me the first heading",
        messages=[{"role": "user", "content": "open notes/quarterly.md and tell me the first heading"}],
        output_mode="tool_intent",
        contract={"json_schema": json_schema_for_mode("tool_intent")},
        tools=READ_TOOLS,
        tool_choice="required",
        tools_required=True,
    )


def _tool_step(registry: ModelRegistry, **request) -> list[str]:
    return _models(
        registry.rank_manifests(
            ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent", allow_paid_fallback=True, **request)
        )
    )


def _native_read_reply() -> dict:
    return {"function": {"name": READ_TOOLS[0].name, "arguments": {"path": "notes/quarterly.md"}}}


def test_rollback_flag_off_keeps_the_openrouter_prompted_lane_eligible_and_its_native_tool_reaches_the_wire() -> None:
    with ScriptedProvider({}, default=_native_read_reply()) as provider:
        lane = _router_lane("openrouter-byok", "vendor/reader-7b", provider.base_url, tool_dialect="prompted")
        registry = _registry(lane)
        with runtime_flags.override("capability_tool_dialect", False):
            ranked = _tool_step(registry)
            live = rank_provider_candidates(
                registry, task_kind="tool_intent", output_mode="tool_intent", role="drone", swarm_size=4,
                min_trust=0.0, allow_paid_fallback=True,
            )
            adapter = OpenAICompatibleAdapter(lane)
            adapter.invoke(_read_request())

    assert ranked == ["vendor/reader-7b"]
    assert _models(live) == ["vendor/reader-7b"]
    assert len(provider.calls) == 1
    assert provider.calls[0]["model"] == "vendor/reader-7b"
    assert provider.calls[0]["tools"] == [READ_TOOLS[0].name]


def test_rollback_flag_off_reads_the_openrouter_rule_by_hostname_too() -> None:
    lane = _router_lane("hosted-router", "vendor/hosted-9b", "https://openrouter.ai/api/v1", tool_dialect="prompted")
    registry = _registry(lane)
    with runtime_flags.override("capability_tool_dialect", False):
        ranked = _tool_step(registry)
        payload = OpenAICompatibleAdapter(lane)._build_openai_payload(_read_request(), force_json=True, stream=False)
    with runtime_flags.override("capability_tool_dialect", True):
        ranked_on = _tool_step(registry)

    assert ranked == ["vendor/hosted-9b"]
    assert [tool["function"]["name"] for tool in payload["tools"]] == [READ_TOOLS[0].name]
    assert ranked_on == []


def test_rollback_flag_off_still_excludes_a_prompted_lane_the_openrouter_rule_does_not_cover() -> None:
    with ScriptedProvider({}, default=_native_read_reply()) as provider:
        lane = _router_lane("lab-gateway", "labmodel:14b", provider.base_url, tool_dialect="prompted")
        registry = _registry(lane)
        with runtime_flags.override("capability_tool_dialect", False):
            ranked = _tool_step(registry)
            rows = explain_provider_exclusions(
                [lane], ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent", allow_paid_fallback=True)
            )
            with pytest.raises(RequiredToolsNotOfferedError):
                OpenAICompatibleAdapter(lane).invoke(_read_request())

    assert ranked == []
    assert rows[0]["status"] == "prompted_tool_dialect_without_structured_output"
    assert provider.calls == []


def test_flag_on_honors_the_prompted_declaration_on_the_openrouter_lane() -> None:
    with ScriptedProvider({}, default=_native_read_reply()) as provider:
        lane = _router_lane("openrouter-byok", "vendor/reader-7b", provider.base_url, tool_dialect="prompted")
        registry = _registry(lane)
        with runtime_flags.override("capability_tool_dialect", True):
            ranked = _tool_step(registry)
            with pytest.raises(RequiredToolsNotOfferedError):
                OpenAICompatibleAdapter(lane).invoke(_read_request())

    assert ranked == []
    assert provider.calls == []


@pytest.mark.parametrize("flag_on", [True, False], ids=["flag-on", "flag-off"])
def test_pins_and_failover_follow_the_effective_dialect(flag_on) -> None:
    with ScriptedProvider({}) as provider:
        router = _router_lane("openrouter-byok", "vendor/reader-7b", provider.base_url, tool_dialect="prompted")
        gateway = _router_lane("lab-gateway", "labmodel:14b", provider.base_url, tool_dialect="prompted")
        native = _router_lane("lab-native", "nativelab:14b", provider.base_url, tool_dialect="native")
        registry = _registry(router, gateway, native)
        with runtime_flags.override("capability_tool_dialect", flag_on):
            pinned_router = _tool_step(registry, preferred_provider="openrouter-byok", preferred_model="vendor/reader-7b")
            pinned_gateway = _tool_step(registry, preferred_provider="lab-gateway", preferred_model="labmodel:14b")
            after_router = select_with_failover(
                registry,
                ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent", allow_paid_fallback=True),
                attempted_provider_ids=["openrouter-byok:vendor/reader-7b", "lab-native:nativelab:14b"],
            )
            after_native = select_with_failover(
                registry,
                ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent", allow_paid_fallback=True),
                attempted_provider_ids=["lab-native:nativelab:14b"],
            )

    # The gateway lane carries neither carrier in either state: never pinned, never failed over to.
    assert "labmodel:14b" not in pinned_gateway
    assert after_router.selected is None
    if flag_on:
        assert "vendor/reader-7b" not in pinned_router
        assert after_native.selected is None
    else:
        assert pinned_router[0] == "vendor/reader-7b"
        assert after_native.selected is not None and after_native.selected.model_name == "vendor/reader-7b"


@pytest.mark.parametrize("flag_on", [True, False], ids=["flag-on", "flag-off"])
@pytest.mark.parametrize("json_flag", ["supports_json_mode", "supports_json_schema"])
def test_prompted_lane_with_structured_output_stays_eligible_in_both_flag_states(flag_on, json_flag) -> None:
    reply = '{"intent": "workspace.read_file", "arguments": {"path": "notes/quarterly.md"}}'
    with ScriptedProvider({}, default=reply) as provider:
        lane = _router_lane("lab-gateway", "labjson:14b", provider.base_url, tool_dialect="prompted", **{json_flag: True})
        registry = _registry(lane)
        with runtime_flags.override("capability_tool_dialect", flag_on):
            ranked = _tool_step(registry)
            adapter = OpenAICompatibleAdapter(lane)
            payload = adapter._build_openai_payload(_read_request(), force_json=True, stream=False)
            adapter.invoke(_read_request())

    assert ranked == ["labjson:14b"]
    assert payload.get("response_format") and not payload.get("tools")
    assert len(provider.calls) == 1


@pytest.mark.parametrize("native_on", [True, False], ids=["ollama-native-tools-on", "ollama-native-tools-off"])
def test_native_ollama_lane_follows_its_own_native_tool_rule_not_the_declaration(native_on) -> None:
    # The native Ollama chat lane never reads tool_dialect: ``ollama_native_tools`` decides.
    reply = {"function": {"name": READ_TOOLS[0].name, "arguments": {"path": "notes/quarterly.md"}}}
    with ScriptedProvider({"ollamaread:4b": reply}) as provider:
        lane = ModelProviderManifest(
            provider_name="ollama-local",
            model_name="ollamaread:4b",
            source_type="http",
            adapter_type="openai_compatible",
            license_name="Apache-2.0",
            license_reference="https://example.com/license",
            weight_location="external",
            runtime_dependency="ollama",
            capabilities=GENERAL_CAPS,
            runtime_config={"base_url": provider.base_url, "timeout_seconds": 30, "tool_dialect": "prompted"},
            metadata={"runtime_family": "ollama", "cost_class": "free_local"},
        )
        registry = _registry(lane)
        with runtime_flags.override("ollama_native_tools", native_on):
            ranked = _tool_step(registry)
            payload = OpenAICompatibleAdapter(lane)._build_ollama_payload(_read_request(), force_json=True, stream=False)

    if native_on:
        assert ranked == ["ollamaread:4b"]
        assert [tool["function"]["name"] for tool in payload["tools"]] == [READ_TOOLS[0].name]
    else:
        assert ranked == []
        assert not payload.get("tools") and not payload.get("format")


# ---------------------------------------------------------------- the local runtime lanes declare it
_LOCAL_RUNTIME_LANES = [
    ("vllm-local", "_ensure_vllm_provider", {"VLLM_BASE_URL": "http://127.0.0.1:8000/v1"}, {"model_name": "qwen3-8b"}),
    ("llamacpp-local", "_ensure_llamacpp_provider", {"LLAMACPP_BASE_URL": "http://127.0.0.1:8080/v1"}, {"model_name": "qwen3-8b"}),
    (
        "llamacpp-local",
        "_ensure_llamacpp_deep_provider",
        {"VOOL_LLAMACPP_DEEP_BASE_URL": "http://127.0.0.1:8090/v1", "VOOL_LLAMACPP_DEEP_MODEL": "qwen3-14b"},
        {},
    ),
    ("mlx-local", "_ensure_mlx_provider", {"MLX_BASE_URL": "http://127.0.0.1:8081/v1"}, {"model_name": "qwen3-8b-mlx"}),
]


@pytest.mark.parametrize("provider,ensure,env,kwargs", _LOCAL_RUNTIME_LANES, ids=lambda v: v if isinstance(v, str) else "")
@pytest.mark.parametrize("role", ["auto", "drone"])
def test_a_machine_whose_only_local_runtime_is_this_lane_keeps_its_tool_turns(provider, ensure, env, kwargs, role) -> None:
    """vLLM, llama.cpp and MLX lanes carry native tool schemas through their adapter; the hard
    law reads the declaration, so the lane VOOL registers must declare tool selection or a
    machine running only that runtime has no candidate for any tool step."""
    from core import runtime_provider_defaults

    _clear_manifests()
    registry = ModelRegistry()
    assert getattr(runtime_provider_defaults, ensure)(registry, env=env, **kwargs)
    ranked = rank_provider_candidates(registry, task_kind="tool_intent", output_mode="tool_intent", role=role, min_trust=0.45)
    assert [m.provider_name for m in ranked] == [provider]


@pytest.mark.parametrize("provider,ensure,env,kwargs", _LOCAL_RUNTIME_LANES, ids=lambda v: v if isinstance(v, str) else "")
def test_a_lane_persisted_before_it_declared_tool_selection_is_registered_again(provider, ensure, env, kwargs) -> None:
    """An install upgraded in place keeps its stored manifest; the boot-time ensure must not
    short-circuit on a stored lane that predates the declaration."""
    from core import runtime_provider_defaults

    _clear_manifests()
    registry = ModelRegistry()
    getattr(runtime_provider_defaults, ensure)(registry, env=env, **kwargs)
    (stored,) = registry.list_manifests(enabled_only=True)
    registry.register_manifest(
        stored.model_copy(update={"capabilities": [c for c in stored.capabilities if c != "tool_intent"]})
    )
    assert "tool_intent" not in registry.list_manifests(enabled_only=True)[0].capabilities

    getattr(runtime_provider_defaults, ensure)(registry, env=env, **kwargs)
    (refreshed,) = registry.list_manifests(enabled_only=True)
    assert refreshed.provider_name == provider
    assert "tool_intent" in refreshed.capabilities
    ranked = rank_provider_candidates(registry, task_kind="tool_intent", output_mode="tool_intent", role="auto", min_trust=0.45)
    assert [m.provider_name for m in ranked] == [provider]
