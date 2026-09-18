"""Local-first self-heal for provider ranking.

The agent must never go brain-dead (source="no_provider_available") while a usable local model is
installed, just because the requested model *tag* doesn't match. rank_providers ranks strictly on
the requested provider/model first (so an available specialist still wins), then — only if that is
empty — re-ranks with the model/provider as a soft preference so any capable enabled LOCAL provider
serves. The relaxed pass still enforces every hard safety/cost constraint, so it never fails open to
a paid cloud lane. explain_provider_exclusions names why an empty ranking happened.
"""
from __future__ import annotations

import core.policy_engine as policy_engine
from core.model_selection_policy import (
    ModelSelectionRequest,
    explain_provider_exclusions,
    is_verified_free_cloud_manifest,
    rank_providers,
)
from storage.model_provider_manifest import ModelProviderManifest


def _local(model: str, *, provider: str = "ollama-local", enabled: bool = True,
           license_name: str | None = "Apache-2.0") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider,
        model_name=model,
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name=license_name,
        license_reference=("https://ollama.com/library/" + model) if license_name else None,
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"deployment_class": "local", "orchestration_role": "queen", "tokens_per_second": 8.0},
        enabled=enabled,
    )


def _paid(model: str = "gpt-4o") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="openai",
        model_name=model,
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="Proprietary",
        license_reference="https://openai.com/policies",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "https://api.openai.com/v1"},
        metadata={"deployment_class": "remote"},
    )


def _free_cloud(model: str = "tencent/hy3:free") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name=model,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "https://openrouter.ai/api/v1"},
        metadata={"deployment_class": "remote", "cost_class": "paid_cloud"},
    )


def _req(**kw) -> ModelSelectionRequest:
    kw.setdefault("task_kind", "summarize")
    kw.setdefault("output_mode", "plain_text")
    return ModelSelectionRequest(**kw)


def test_exact_tag_match_still_wins_specialist_preserved() -> None:
    manifests = [_local("qwen2.5:7b"), _local("qwen2.5:32b")]
    ranked = rank_providers(manifests, _req(preferred_model="qwen2.5:32b"))
    assert ranked and ranked[0].model_name == "qwen2.5:32b"  # strict pass returns the exact match


def test_tag_mismatch_self_heals_to_available_local_model() -> None:
    # The brain-offline bug: request qwen3:8b, but only qwen2.5:7b is installed.
    manifests = [_local("qwen2.5:7b")]
    ranked = rank_providers(manifests, _req(preferred_model="qwen3:8b"))
    assert len(ranked) == 1 and ranked[0].model_name == "qwen2.5:7b"  # serves, does NOT go brain-dead


def test_no_preference_returns_available_local() -> None:
    manifests = [_local("qwen2.5:7b")]
    assert [m.model_name for m in rank_providers(manifests, _req())] == ["qwen2.5:7b"]


def test_self_heal_never_fails_open_to_paid_cloud(monkeypatch) -> None:
    # Isolate the paid-gate from local-only mode so we prove the RELAXED pass still blocks paid.
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    manifests = [_paid("gpt-4o")]  # only a paid provider, and it doesn't match the requested tag
    ranked = rank_providers(manifests, _req(preferred_model="qwen3:8b", allow_paid_fallback=False))
    assert ranked == []  # tag mismatch would relax, but paid stays hard-excluded -> no spend


def test_paid_still_available_when_explicitly_permitted(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    manifests = [_paid("gpt-4o")]
    ranked = rank_providers(manifests, _req(preferred_model="qwen3:8b", allow_paid_fallback=True))
    assert [m.model_name for m in ranked] == ["gpt-4o"]


def test_selected_verified_free_openrouter_model_is_available_without_paid_fallback(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    from core import openrouter_catalog
    from core.openrouter_catalog import OpenRouterModel

    verified_free = OpenRouterModel(
        model_id="tencent/hy3:free",
        name="HY3 Free",
        context_length=262144,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=0.0,
        supported_parameters=("tools",),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-07-20T00:00:00+00:00",
    )
    monkeypatch.setattr(openrouter_catalog, "safe_all_models", lambda **_kwargs: ((verified_free,), 0.0))
    manifest = _free_cloud()
    assert is_verified_free_cloud_manifest(manifest) is True
    assert [m.model_name for m in rank_providers([manifest], _req(preferred_model=manifest.model_name))] == [
        manifest.model_name
    ]


def test_free_tag_on_another_remote_provider_does_not_bypass_paid_gate(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    manifest = _paid("provider/model:free")
    assert is_verified_free_cloud_manifest(manifest) is False
    assert rank_providers([manifest], _req(preferred_model=manifest.model_name)) == []


def test_disabled_only_yields_empty_and_diagnoses_disabled() -> None:
    manifests = [_local("qwen2.5:7b", enabled=False)]
    assert rank_providers(manifests, _req(preferred_model="qwen3:8b")) == []
    rows = explain_provider_exclusions(manifests, _req(preferred_model="qwen3:8b"))
    assert rows and rows[0]["status"] == "disabled" and rows[0]["enabled"] == "0"


def test_missing_license_excluded_and_diagnosed() -> None:
    manifests = [_local("qwen2.5:7b", license_name=None)]
    assert rank_providers(manifests, _req()) == []
    rows = explain_provider_exclusions(manifests, _req())
    assert rows[0]["status"] == "missing_license_metadata"


def test_explain_marks_tag_mismatched_local_as_eligible() -> None:
    # A tag mismatch is NOT a hard exclusion — the manifest is still eligible under the self-heal.
    manifests = [_local("qwen2.5:7b")]
    rows = explain_provider_exclusions(manifests, _req(preferred_model="qwen3:8b"))
    assert rows[0]["status"] == "eligible"


def test_empty_registry_diagnoses_empty() -> None:
    assert rank_providers([], _req()) == []
    assert explain_provider_exclusions([], _req()) == []


# --------------------------------------------------------------------------------------
# Finding F, 2026-08-04: ordinary tool_intent/chat ranking must prefer a VERIFIED free-cloud
# model over a weaker local one on its own merit -- not only because hardware-fit accidentally
# excluded the local queen model. Before this fix `paid_cloud` carried a flat -0.12 penalty
# regardless of `is_verified_free_cloud_manifest`, so ordinary tool_intent calls (folder listing,
# project overview) defaulted to local qwen3:8b even when a genuinely free, more reliable cloud
# model was enabled and eligible -- the documented source of local-model tool-argument-generation
# failures (Finding B).
# --------------------------------------------------------------------------------------


def _verified_free_catalog_row(model_id: str = "tencent/hy3:free"):
    from core.openrouter_catalog import OpenRouterModel

    return OpenRouterModel(
        model_id=model_id,
        name="HY3 Free",
        context_length=131072,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=0.0,
        supported_parameters=("tools",),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-07-20T00:00:00+00:00",
    )


def test_ordinary_tool_intent_prefers_verified_free_cloud_over_weaker_local(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    from core import openrouter_catalog

    monkeypatch.setattr(
        openrouter_catalog, "safe_all_models", lambda **_kwargs: ((_verified_free_catalog_row(),), 0.0)
    )
    manifests = [_local("qwen3:8b"), _free_cloud()]
    ranked = rank_providers(manifests, _req(task_kind="tool_intent"))

    assert ranked, "both manifests are eligible; ranking must not come back empty"
    assert ranked[0].model_name == "tencent/hy3:free", (
        "a verified-free cloud model must outrank an ordinary local drone model on tool_intent "
        f"routing, got order: {[m.model_name for m in ranked]}"
    )


def test_local_only_mode_still_excludes_the_free_cloud_lane_entirely(monkeypatch) -> None:
    """The Finding F boost is an AUTO ranking preference, never an override of an explicit
    local-only choice -- `local_only_mode()` must still hard-exclude every non-`free_local`
    manifest before the boosted score ever has a chance to matter."""

    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: True)
    from core import openrouter_catalog

    monkeypatch.setattr(
        openrouter_catalog, "safe_all_models", lambda **_kwargs: ((_verified_free_catalog_row(),), 0.0)
    )
    manifests = [_local("qwen3:8b"), _free_cloud()]
    ranked = rank_providers(manifests, _req(task_kind="tool_intent"))

    assert [m.model_name for m in ranked] == ["qwen3:8b"]
