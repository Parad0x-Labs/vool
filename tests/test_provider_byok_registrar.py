"""Generalized BYOK registrar: a direct provider registers an openai_compatible manifest under
<provider>-byok with the right base URL, slot, and paid_cloud cost class; OpenRouter is untouched."""
from __future__ import annotations

from unittest import mock

import pytest

import core.runtime_provider_defaults as rpd


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _active_manifest(provider_name: str):
    from storage.model_provider_manifest import list_provider_manifests

    rows = [m for m in list_provider_manifests(enabled_only=True) if m.provider_name == provider_name]
    return rows[0] if rows else None


def test_direct_provider_is_dormant_without_a_key():
    with mock.patch("core.credential_store.has_credential", lambda s: False):
        assert rpd.activate_provider_byok("openai", env={}) == ""
        assert rpd.activate_provider_byok("groq", env={}) == ""


def test_direct_provider_registers_a_paid_openai_compatible_lane():
    with mock.patch("core.credential_store.has_credential", lambda s: s == "llm.cloud.groq"):
        pid = rpd.activate_provider_byok("groq", env={})
    assert pid == "groq-byok:llama-3.3-70b-versatile"
    m = _active_manifest("groq-byok")
    assert m is not None
    assert m.adapter_type == "openai_compatible"
    assert m.runtime_config["base_url"] == "https://api.groq.com/openai/v1"
    assert m.runtime_config["credential_key"] == "llm.cloud.groq"
    assert m.runtime_config["health_path"] == "/models"
    assert m.metadata["cost_class"] == "paid_cloud"


def test_anthropic_lane_carries_the_version_header():
    with mock.patch("core.credential_store.has_credential", lambda s: s == "llm.cloud.anthropic"):
        rpd.activate_provider_byok("anthropic", env={})
    m = _active_manifest("anthropic-byok")
    assert m is not None and m.runtime_config.get("headers", {}).get("anthropic-version")


def test_env_key_also_activates():
    with mock.patch("core.credential_store.has_credential", lambda s: False):
        pid = rpd.activate_provider_byok("openai", env={"OPENAI_API_KEY": "sk-proj-x"})
    assert pid.startswith("openai-byok")
    assert _active_manifest("openai-byok").runtime_config.get("api_key_env") == "OPENAI_API_KEY"


def test_policy_model_for_this_provider_is_honored():
    from core import cloud_escalation_policy as cep

    cep.save_policy(cep.CloudEscalationPolicy(provider="openai", model="gpt-4.1"))
    with mock.patch("core.credential_store.has_credential", lambda s: s == "llm.cloud.openai"):
        pid = rpd.activate_provider_byok("openai", env={})
    assert pid == "openai-byok:gpt-4.1"


def test_policy_model_for_a_different_provider_is_ignored():
    from core import cloud_escalation_policy as cep

    cep.save_policy(cep.CloudEscalationPolicy(provider="openrouter", model="deepseek/x:free"))
    with mock.patch("core.credential_store.has_credential", lambda s: s == "llm.cloud.groq"):
        pid = rpd.activate_provider_byok("groq", env={})
    assert pid == "groq-byok:llama-3.3-70b-versatile"  # the openrouter policy model must not leak in


def test_deactivate_disables_only_that_provider():
    with mock.patch("core.credential_store.has_credential", lambda s: s in {"llm.cloud.openai", "llm.cloud.groq"}):
        rpd.activate_provider_byok("openai", env={})
        rpd.activate_provider_byok("groq", env={})
    assert rpd.deactivate_provider_byok("openai") >= 1
    assert _active_manifest("openai-byok") is None
    assert _active_manifest("groq-byok") is not None  # groq untouched


@pytest.mark.parametrize("provider, model", [("openrouter", "deepseek/deepseek-v4-flash-0731"), ("groq", "chat-selected-model")])
def test_key_reverification_restores_chat_pins_without_enabling_them_for_auto(provider, model):
    from core import cloud_escalation_policy as cep
    from core.model_registry import ModelRegistry

    cep.save_chat_model_selection("retained-chat", model=model, provider=provider)
    cep.save_chat_model_selection("other-provider", model="unrelated", provider="anthropic")
    with mock.patch("core.credential_store.has_credential", lambda s: s == f"llm.cloud.{provider}"):
        rpd.activate_provider_byok(provider, env={})
        registry = ModelRegistry()
        assert registry.get_manifest(f"{provider}-byok", model).enabled
        rpd.deactivate_provider_byok(provider)
        assert not registry.get_manifest(f"{provider}-byok", model).enabled
    with mock.patch("core.credential_store.has_credential", return_value=False):
        assert rpd.activate_provider_byok(provider, env={}) == ""
        assert not registry.get_manifest(f"{provider}-byok", model).enabled
    with mock.patch("core.credential_store.has_credential", lambda s: s == f"llm.cloud.{provider}"):
        rpd.activate_provider_byok(provider, env={})
        restored = registry.get_manifest(f"{provider}-byok", model)
        assert restored.enabled and restored.metadata["chat_selection_only"] is True
        assert cep.chat_model_selection("retained-chat") == {"provider": provider, "model": model}
        assert registry.get_manifest("anthropic-byok", "unrelated") is None


def test_openrouter_still_routes_through_its_own_path():
    # activate_provider_byok("openrouter") must delegate to the unchanged openrouter registrar,
    # producing an openrouter-byok manifest with the attribution headers.
    with mock.patch("core.credential_store.has_credential", lambda s: s == "llm.cloud.openrouter"):
        pid = rpd.activate_provider_byok("openrouter", env={})
    assert pid.startswith("openrouter-byok")
    m = _active_manifest("openrouter-byok")
    assert m is not None and "HTTP-Referer" in m.runtime_config.get("headers", {})


def test_openrouter_attribution_is_vool_on_every_path():
    # VOOL_MIGRATION.md §2.2/§2.4: the canonical attribution carries exactly the VOOL values, and the
    # live openrouter-byok manifest (the single construction point every OpenRouter request flows
    # through) must ship all three headers. Guards against drift back to VOOL/parad0xlabs and against
    # a request path leaving without one of the three headers.
    h = rpd.openrouter_attribution_headers({})
    assert h == {
        "HTTP-Referer": "https://vool.dev",
        "X-OpenRouter-Title": "VOOL",
        "X-OpenRouter-Categories": "personal-agent,programming-app",
    }
    assert "parad0xlabs" not in h["HTTP-Referer"] and h["X-OpenRouter-Title"] != "VOOL"
    with mock.patch("core.credential_store.has_credential", lambda s: s == "llm.cloud.openrouter"):
        rpd.activate_provider_byok("openrouter", env={})
    hdrs = _active_manifest("openrouter-byok").runtime_config.get("headers", {})
    assert hdrs.get("HTTP-Referer") == "https://vool.dev"
    assert hdrs.get("X-OpenRouter-Title") == "VOOL"
    assert "X-OpenRouter-Categories" in hdrs


def test_custom_without_a_base_url_stays_dormant():
    with mock.patch("core.credential_store.has_credential", lambda s: s == "llm.cloud.custom"):
        assert rpd.activate_provider_byok("custom", env={}) == ""  # no base URL, no default model


def test_custom_base_url_change_reregisters_the_lane(monkeypatch):
    # Re-pointing the custom endpoint must UPDATE the manifest base_url, not keep the stale host:
    # a stale idempotency early-return would keep sending the freshly-entered key to the old host.
    import os

    from core import cloud_escalation_policy as cep

    cep.save_policy(cep.CloudEscalationPolicy(provider="custom", model="my-model"))
    with mock.patch("core.credential_store.has_credential", lambda s: s == "llm.cloud.custom"):
        monkeypatch.setenv("VOOL_CUSTOM_BASE_URL", "https://api.alpha.example/v1")
        rpd.activate_provider_byok("custom", env=dict(os.environ))
        assert _active_manifest("custom-byok").runtime_config["base_url"] == "https://api.alpha.example/v1"
        # Re-point to a new host with the same model — the manifest must follow.
        monkeypatch.setenv("VOOL_CUSTOM_BASE_URL", "https://api.beta.example/v1")
        rpd.activate_provider_byok("custom", env=dict(os.environ))
        assert _active_manifest("custom-byok").runtime_config["base_url"] == "https://api.beta.example/v1"


def test_switching_active_provider_retires_the_previous_lane():
    # v1 = one active cloud provider: after two lanes are enabled, retiring nonactive for the new
    # provider must disable the previous one so a burst can never route to the non-active provider.
    with mock.patch("core.credential_store.has_credential", lambda s: s in {"llm.cloud.openai", "llm.cloud.groq"}):
        rpd.activate_provider_byok("openai", env={})
        rpd.activate_provider_byok("groq", env={})
        assert _active_manifest("openai-byok") is not None
        assert _active_manifest("groq-byok") is not None
        disabled = rpd.retire_nonactive_provider_lanes("groq")
    assert disabled >= 1
    assert _active_manifest("openai-byok") is None   # disabled -> gone from the enabled set
    assert _active_manifest("groq-byok") is not None  # the active provider's lane is kept


# NOTE: the "stale persisted manifest gets rebuilt to VOOL" test was removed in the 2026-07-24
# merge with the Windows lane's PR #63. That lane enforces attribution at the ADAPTER wire layer
# (apply_openrouter_attribution_headers runs last on every OpenRouter request), so a stale manifest
# no longer matters — the wire always carries VOOL regardless of persisted headers. Wire-level
# enforcement is covered by tests/test_openrouter_attribution.py.
