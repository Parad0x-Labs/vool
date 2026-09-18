"""A chat pin must resolve without replacing defaults or becoming an Auto candidate."""
from types import SimpleNamespace

import pytest

from core import cloud_escalation_policy as cep
from core import runtime_paths
from core import runtime_provider_defaults as defaults
from core.memory_first_router import MemoryFirstRouter
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest, rank_providers


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.mark.parametrize("provider,model", [
    ("openrouter", "nvidia/nemotron-3.5-lightning:free"),
    ("groq", "llama-3.3-70b-versatile"),
])
def test_pin_resolves_after_default_retirement_without_entering_auto(provider, model):
    before = cep.load_policy()
    defaults.register_chat_cloud_model(provider, model)
    cep.save_chat_model_selection("openclaw:" + "a" * 20, model=model, provider=provider)
    defaults.register_chat_cloud_model("openrouter", "different/control:free")
    registry = ModelRegistry()
    selected = registry.get_manifest(f"{provider}-byok", model)
    assert selected and selected.enabled and selected.metadata["chat_selection_only"]
    router = SimpleNamespace(registry=registry)
    request = model if provider == "openrouter" else provider + ":" + model
    context = {"requested_model": request, "model_selection": "pin"}
    resolved = MemoryFirstRouter._requested_model_manifest(router, context)
    assert resolved.provider_id == selected.provider_id
    preferred_provider, preferred_model = MemoryFirstRouter._requested_model_preferences(router, context)
    assert (preferred_provider, preferred_model) == (f"{provider}-byok", model)
    assert rank_providers([selected], ModelSelectionRequest(
        task_kind="summarize", output_mode="plain_text", allow_paid_fallback=True,
    )) == []
    assert rank_providers([selected], ModelSelectionRequest(
        task_kind="summarize", output_mode="plain_text", allow_paid_fallback=True,
        preferred_provider=preferred_provider, preferred_model=preferred_model,
    )) == [selected]
    defaults._retire_openrouter_lanes(keep={"some/default:free"})
    defaults._retire_lanes(f"{provider}-byok", keep={"some-default"})
    defaults.retire_nonactive_provider_lanes("anthropic")
    assert registry.get_manifest(f"{provider}-byok", model).enabled
    assert cep.load_policy() == before


def test_old_default_pinned_by_a_chat_becomes_chat_only_when_retired(monkeypatch):
    monkeypatch.setattr("core.credential_store.has_credential", lambda _: True)
    defaults.activate_provider_byok("groq", env={})
    registry = ModelRegistry()
    model = "llama-3.3-70b-versatile"
    assert not registry.get_manifest("groq-byok", model).metadata["chat_selection_only"]
    cep.save_chat_model_selection("openclaw:" + "b" * 20, model=model, provider="groq")
    defaults.retire_nonactive_provider_lanes("openrouter")
    kept = registry.get_manifest("groq-byok", model)
    assert kept.enabled and kept.metadata["chat_selection_only"]
    defaults.deactivate_provider_byok("groq")
    assert not registry.get_manifest("groq-byok", model).enabled
