"""The provider config table + key detector: the single source of truth for multi-provider BYOK."""
from __future__ import annotations

import pytest

from core.cloud_providers import (
    PROVIDERS,
    active_provider,
    all_slots,
    config_for,
    detect_provider,
    provider_for_slot,
    slot_for,
)


def test_every_provider_has_a_distinct_slot_and_safe_base():
    slots = [cfg.credential_slot for cfg in PROVIDERS.values()]
    assert len(slots) == len(set(slots)), "slots must be unique"
    assert all_slots() == frozenset(slots)
    for pid, cfg in PROVIDERS.items():
        assert cfg.credential_slot == f"llm.cloud.{pid}"
        if not cfg.user_base_url:
            assert cfg.base_url.startswith("https://"), pid  # vendor bases are HTTPS
    # custom is the only user-supplied base and starts empty
    assert PROVIDERS["custom"].user_base_url and PROVIDERS["custom"].base_url == ""


def test_high_confidence_prefixes_map_correctly():
    cases = {
        "sk-or-v1-abc": "openrouter",
        "sk-ant-abc123": "anthropic",
        "sk-proj-abc123": "openai",
        "gsk_abcdef012345": "groq",
        "AIzaSyABCDEF": "google",
    }
    for key, expected in cases.items():
        guess = detect_provider(key)
        assert guess.provider_id == expected and guess.confidence == "high", key


def test_bare_sk_is_ambiguous_with_candidates():
    guess = detect_provider("sk-abc123def456")  # not sk-or-/sk-ant-/sk-proj-
    assert guess.confidence == "low"
    assert guess.provider_id == "openai"  # the common-case guess
    assert set(guess.candidates) == {"openai", "deepseek", "moonshot"}


def test_specific_prefix_beats_bare_sk():
    # sk-proj- must resolve to openai HIGH, not fall through to the bare-sk low branch.
    assert detect_provider("sk-proj-x").confidence == "high"
    # sk-ant- must not be read as a bare sk- openai key.
    assert detect_provider("sk-ant-x").provider_id == "anthropic"


def test_unknown_and_empty_keys_are_none():
    assert detect_provider("").provider_id is None
    assert detect_provider("hello-world").confidence == "none"


def test_slot_helpers_round_trip():
    assert slot_for("openai") == "llm.cloud.openai"
    assert provider_for_slot("llm.cloud.anthropic") == "anthropic"
    assert provider_for_slot("llm.cloud.nonsense") == ""
    assert config_for("groq").label == "Groq"
    assert config_for("nope") is None


def test_active_provider_resolution_order():
    # explicit policy provider wins
    assert active_provider("anthropic", has_key=lambda s: False) == "anthropic"
    # a single keyed slot is chosen
    assert active_provider("", has_key=lambda s: s == "llm.cloud.groq") == "groq"
    # several keys -> openrouter legacy default if present
    assert active_provider("", has_key=lambda s: s in {"llm.cloud.openrouter", "llm.cloud.openai"}) == "openrouter"
    # no key -> empty
    assert active_provider("", has_key=lambda s: False) == ""
    # an unknown explicit provider falls through to key-based resolution
    assert active_provider("bogus", has_key=lambda s: s == "llm.cloud.openai") == "openai"


def test_openrouter_probe_stays_key_not_models():
    # /models is public on OpenRouter; the probe must use /key so it actually proves the key.
    assert PROVIDERS["openrouter"].probe_path == "/key"
    assert PROVIDERS["openai"].probe_path == "/models"


def test_anthropic_carries_its_version_header():
    assert PROVIDERS["anthropic"].extra_headers.get("anthropic-version")


@pytest.mark.parametrize("pid", list(PROVIDERS))
def test_config_for_is_case_insensitive(pid):
    assert config_for(pid.upper()) is PROVIDERS[pid]
