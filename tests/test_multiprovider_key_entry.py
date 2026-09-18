"""Multi-provider key entry: detect provider from the key, seal under the right slot, ask on an
ambiguous bare sk-, and refuse a non-key secret."""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_command_surface import (
    maybe_handle_bare_secret,
    maybe_handle_cloud_key_command,
)


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    runtime_paths.configure_runtime_home(tmp_path)
    kv: dict[str, str] = {}
    monkeypatch.setattr("core.credential_store.store_credential", lambda name, value, label="": kv.__setitem__(name, value))
    monkeypatch.setattr("core.credential_store.get_credential", lambda name: kv.get(name))
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: name in kv)
    monkeypatch.setattr("core.credential_store.delete_credential", lambda name: bool(kv.pop(name, None) is not None))
    yield kv
    runtime_paths.configure_runtime_home(None)


def test_anthropic_key_seals_under_anthropic_slot(store):
    reply = maybe_handle_cloud_key_command("cloud key sk-ant-" + "a" * 40, owner_local=True)
    assert "saved (ends" in reply and "Anthropic" in reply
    assert store["llm.cloud.anthropic"].startswith("sk-ant-")
    assert "llm.cloud.openrouter" not in store


def test_groq_and_openai_and_gemini_prefixes(store):
    maybe_handle_cloud_key_command("cloud key gsk_" + "b" * 40, owner_local=True)
    maybe_handle_cloud_key_command("cloud key sk-proj-" + "c" * 40, owner_local=True)
    maybe_handle_cloud_key_command("cloud key AIza" + "d" * 36, owner_local=True)
    assert "llm.cloud.groq" in store and "llm.cloud.openai" in store and "llm.cloud.google" in store


def test_ambiguous_bare_sk_is_not_stored_but_asked(store):
    reply = maybe_handle_cloud_key_command("cloud key sk-" + "e" * 40, owner_local=True)
    assert "ambiguous" in reply.lower()
    assert store == {}, "an ambiguous bare sk- must never be auto-stored"


def test_explicit_provider_token_disambiguates(store):
    reply = maybe_handle_cloud_key_command("cloud key sk-" + "f" * 40 + " deepseek", owner_local=True)
    assert "saved (ends" in reply and store["llm.cloud.deepseek"].startswith("sk-")


def test_bare_paste_of_a_direct_key_is_sealed(store):
    reply = maybe_handle_bare_secret("here " + "gsk_" + "g" * 40, owner_local=True)
    assert reply is not None and "saved (ends" in reply and "llm.cloud.groq" in store


def test_bare_paste_ambiguous_sk_asks(store):
    reply = maybe_handle_bare_secret("sk-" + "h" * 40, owner_local=True)
    assert reply is not None and "ambiguous" in reply.lower() and store == {}


def test_openrouter_backward_compat_unchanged(store):
    reply = maybe_handle_cloud_key_command("cloud key sk-or-v1-" + "i" * 40, owner_local=True)
    assert "saved (ends" in reply and "OpenRouter" in reply
    assert store["llm.cloud.openrouter"].startswith("sk-or-")


def test_unknown_explicit_provider_is_rejected(store):
    reply = maybe_handle_cloud_key_command("cloud key sk-proj-" + "j" * 40 + " bogusprov", owner_local=True)
    assert "not a known provider" in reply and store == {}
