"""Paid pins attempt their own tools; Auto retains its declared-capability filter.

The loopback provider scripts model output only. Ranking, reservation, adapter payloads,
tool validation and failure receipts run through the production path without paid calls.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest, explain_provider_exclusions
from core.provider_routing import rank_provider_candidates
from storage.model_provider_manifest import ModelProviderManifest, upsert_provider_manifest
from tests._blackbox_served_rig import ScriptedProvider
from tests.test_explicit_pin_authority_p0 import (
    _isolated_runtime as _isolated_runtime,
)
from tests.test_explicit_pin_authority_p0 import (
    _set_policy,
)
from tests.test_explicit_pin_authority_p0 import (
    anthropic_lane as anthropic_lane,
)
from tests.test_explicit_pin_authority_p0 import (
    free_openrouter_lane as free_openrouter_lane,
)


def _paid(provider: str = "openai", **updates) -> ModelProviderManifest:
    manifest = ModelProviderManifest(
        provider_name=f"{provider}-byok",
        model_name="selected-chat",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        weight_location="external",
        weights_bundled=False,
        capabilities=["structured_json", "summarize"],
        runtime_config={"base_url": "https://example.com/v1", "tool_dialect": "native"},
        metadata={"cost_class": "paid_cloud"},
    )
    return manifest.model_copy(update=updates)


def _request(manifest: ModelProviderManifest, **updates) -> ModelSelectionRequest:
    return ModelSelectionRequest(
        **{
            "task_kind": "tool_intent",
            "output_mode": "tool_intent",
            "preferred_provider": manifest.provider_name,
            "preferred_model": manifest.model_name,
            "allow_paid_fallback": True,
            "explicit_model_pin": True,
            **updates,
        }
    )


@pytest.mark.parametrize("provider", ["anthropic", "openai", "groq", "google", "deepseek", "moonshot"])
def test_paid_lane_is_eligible_only_when_the_tool_round_is_explicitly_pinned(provider) -> None:
    manifest = _paid(provider)
    registry = ModelRegistry()
    registry.register_manifest(manifest)
    # A preferred or sticky identity is insufficient: Auto still refuses this lane.
    assert registry.rank_manifests(_request(manifest, explicit_model_pin=False)) == []
    assert registry.rank_manifests(_request(manifest)) == [manifest]
    assert rank_provider_candidates(
        registry, task_kind="tool_intent", output_mode="tool_intent", role="drone",
        preferred_provider=manifest.provider_name, preferred_model=manifest.model_name,
        allow_paid_fallback=True, explicit_model_pin=True,
    ) == [manifest]


@pytest.mark.parametrize(
    "updates,request_updates,reason",
    [
        ({"enabled": False}, {}, "disabled"),
        ({"weights_bundled": True}, {}, "bundled_weights_forbidden"),
        ({"license_name": "", "license_reference": "", "license_url_or_reference": ""}, {}, "missing_license_metadata"),
        ({}, {"allow_paid_fallback": False}, "paid_not_permitted"),
        ({}, {"local_only": True}, "not_local_in_auto_local_only_mode"),
        ({}, {"exclude_provider_ids": ["openai-byok:selected-chat"]}, "excluded_by_request"),
        ({}, {"min_trust": 1.1}, "below_min_trust"),
        ({"capabilities": ["summarize"]}, {"output_mode": "json_object"}, "missing_required_capability:structured_json"),
        ({}, {"task_kind": "multimodal_review"}, "missing_required_capability:multimodal"),
    ],
)
def test_paid_tool_pin_preserves_other_hard_gates(updates, request_updates, reason) -> None:
    manifest = _paid(**updates)
    request = _request(manifest, **request_updates)
    rows = explain_provider_exclusions([manifest], request)
    assert rows[0]["status"] == reason


@pytest.mark.parametrize(
    "manifest,updates",
    [
        (_paid(), {"preferred_model": "other-model"}),
        (_paid(), {"preferred_provider": "other-provider"}),
        (_paid(metadata={"cost_class": "free_local"}), {"preferred_model": "other-model"}),
        (_paid(metadata={"cost_class": "free_local"}), {"preferred_provider": "other-provider"}),
        (_paid(metadata={"cost_class": "remote_unknown"}), {}),
    ],
)
def test_the_exception_never_makes_an_unmatched_or_unknown_cost_model_eligible(manifest, updates) -> None:
    registry = ModelRegistry()
    registry.register_manifest(manifest)
    assert registry.rank_manifests(_request(manifest, **updates)) == []


def test_an_explicit_local_pin_attempts_its_tool_round_while_auto_still_requires_the_declaration() -> None:
    manifest = _paid(metadata={"cost_class": "free_local"})
    registry = ModelRegistry()
    registry.register_manifest(manifest)
    assert "tool_intent" not in manifest.capabilities
    # The user's explicit pin is attempted, as it was before the hard-capability law.
    assert registry.rank_manifests(_request(manifest)) == [manifest]
    # A preferred or sticky identity, and Auto, keep the declared-capability filter.
    assert registry.rank_manifests(_request(manifest, explicit_model_pin=False)) == []
    assert registry.rank_manifests(
        _request(manifest, explicit_model_pin=False, preferred_provider=None, preferred_model=None)
    ) == []
    rows = explain_provider_exclusions([manifest], _request(manifest, explicit_model_pin=False))
    assert rows[0]["status"] == "missing_required_capability:tool_intent"


@pytest.mark.parametrize(
    "updates,request_updates,reason",
    [
        ({"capabilities": ["summarize", "tool_intent"]}, {"output_mode": "json_object"}, "missing_required_capability:structured_json"),
        ({}, {"task_kind": "multimodal_review"}, "missing_required_capability:multimodal"),
        ({"capabilities": ["summarize"]}, {}, "no_required_capability"),
        ({"enabled": False}, {}, "disabled"),
        ({}, {"min_trust": 1.1}, "below_min_trust"),
    ],
)
def test_an_explicit_local_pin_is_never_given_any_other_hard_capability(updates, request_updates, reason) -> None:
    manifest = _paid(metadata={"cost_class": "free_local"}, **updates)
    rows = explain_provider_exclusions([manifest], _request(manifest, **request_updates))
    assert rows[0]["status"] == reason


def _tool_turn(manifest, monkeypatch):
    from core.memory_first_router import MemoryFirstRouter

    monkeypatch.setattr("core.memory_first_router.should_probe_health", lambda *a, **kw: False)
    monkeypatch.setattr("core.memory_first_router._cached_free_vram_gb", lambda: None)
    _set_policy(mode="off", daily_cap=25)
    text = "Read the project note using the workspace tool."
    return MemoryFirstRouter()._execute_provider_task(
        task=SimpleNamespace(task_id="paid-pin-tool", task_summary=text),
        classification={"task_class": "chat_conversation"},
        interpretation=SimpleNamespace(reconstructed_text=text),
        context_result=SimpleNamespace(
            retrieval_confidence_score=0.2,
            report=SimpleNamespace(
                retrieval_confidence=0.2,
                to_dict=lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []},
            ),
            assembled_context=lambda: "", context_snippets=lambda: [],
        ),
        persona=SimpleNamespace(tone="neutral"), task_hash="paid-pin-tool-hash",
        task_kind="tool_intent", output_mode="tool_intent", allow_paid_fallback=False,
        provider_role="drone", surface="cli",
        source_context={
            "_owner_local": True, "turn_id": "paid-pin-tool-turn", "session_id": "paid-pin-tool-session",
            "requested_model": manifest.provider_id, "model_selection": "pin",
        },
    )


def test_pinned_tool_round_sends_native_tools_and_validates_the_response(anthropic_lane, free_openrouter_lane, monkeypatch) -> None:
    call = {"id": "read-note", "type": "function", "function": {
        "name": "workspace__read_file", "arguments": '{"path":"project-note.txt"}',
    }}
    with ScriptedProvider({}, default=call) as provider:
        manifest = anthropic_lane.model_copy(update={"runtime_config": {
            **anthropic_lane.runtime_config, "base_url": provider.base_url,
            "api_path": "/v1/chat/completions", "tool_dialect": "native",
        }})
        upsert_provider_manifest(manifest)
        assert "tool_intent" not in manifest.capabilities
        decision = _tool_turn(manifest, monkeypatch)
        assert decision.used_model, decision.details
        assert decision.provider_id == manifest.provider_id
        assert decision.tool_calls and decision.tool_calls[0].intent == "workspace.read_file"
        assert provider.calls and {c["model"] for c in provider.calls} == {manifest.model_name}
        assert "workspace__read_file" in provider.calls[0]["tools"]


def test_a_paid_pin_with_no_tool_carrier_reports_its_adapter_failure_without_substitution(anthropic_lane, free_openrouter_lane, monkeypatch) -> None:
    with ScriptedProvider({}, default="No tools supported.") as provider:
        manifest = anthropic_lane.model_copy(update={
            "capabilities": ["summarize"],
            "runtime_config": {
                **anthropic_lane.runtime_config, "base_url": provider.base_url,
                "api_path": "/v1/chat/completions", "tool_dialect": "prompted",
                "supports_json_mode": False, "supports_json_schema": False,
            },
        })
        upsert_provider_manifest(manifest)
        decision = _tool_turn(manifest, monkeypatch)
        assert not decision.used_model
        assert decision.source == "selected_model_blocked"
        assert decision.details["attempted"] == [manifest.provider_id]
        assert decision.details["model_was_attempted"] is True
        assert "REQUIRED_TOOLS_NOT_OFFERED" in str(decision.details)
        assert provider.calls == []


def test_an_explicit_local_pin_of_a_bundle_reasoning_model_reaches_dispatch_on_a_tool_turn(monkeypatch) -> None:
    from core.local_model_bundles import manifest_profile_for_model

    profile = manifest_profile_for_model(model_name="qwen3:14b", bundle_role="reasoning")
    assert "tool_intent" not in profile["capabilities"]
    call = {"function": {"name": "workspace__read_file", "arguments": {"path": "project-note.txt"}}}
    with ScriptedProvider({"qwen3:14b": call}) as provider:
        manifest = ModelProviderManifest(
            provider_name="ollama-local",
            model_name="qwen3:14b",
            source_type="http",
            adapter_type="openai_compatible",
            license_name="Apache-2.0",
            license_reference="https://example.com/license",
            weight_location="external",
            runtime_dependency="ollama",
            capabilities=profile["capabilities"],
            runtime_config={"base_url": provider.base_url, "timeout_seconds": 30},
            metadata={"runtime_family": "ollama", "cost_class": "free_local", "bundle_role": "reasoning"},
        )
        upsert_provider_manifest(manifest)
        # Selection is the subject, not whoever's machine runs it: fit a 14B model on a 64 GB host.
        monkeypatch.setattr(
            "core.provider_routing._device_probe",
            lambda: SimpleNamespace(ram_gb=64.0, vram_gb=0.0, accelerator="cpu", vram_free_gb=None),
        )
        from core.system_resources import ResourceSnapshot

        # The dispatch governor must see the same simulated host as model selection.
        monkeypatch.setattr(
            "core.system_resources.snapshot",
            lambda **_kwargs: ResourceSnapshot(
                total_ram_gb=64.0, available_ram_gb=60.0, used_ram_gb=4.0,
                free_fraction=60.0 / 64.0, memory_pressure="normal", load_1m=0.0,
                cpu_count=8, load_per_core=0.0, swap_used_gb=0.0,
                ollama_loaded_gb=0.0, comfyui_running=False, reserve_gb=4.0,
                usable_gb=56.0,
            ),
        )
        decision = _tool_turn(manifest, monkeypatch)
    assert decision.used_model, decision.details
    assert decision.provider_id == manifest.provider_id
    assert provider.calls and {c["model"] for c in provider.calls} == {"qwen3:14b"}
    assert decision.tool_calls and decision.tool_calls[0].intent == "workspace.read_file"
