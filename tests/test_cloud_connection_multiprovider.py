"""Per-provider connection probe: each provider probes its own endpoint, the cache is keyed by
provider so one provider's green never shows for another, and the active provider is resolved."""
from __future__ import annotations

import json
import types

import pytest

from core import cloud_connection_state as ccs


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    for cfg_env in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(cfg_env, raising=False)
    monkeypatch.setattr(ccs, "_state_path", lambda: tmp_path / "state.json")
    keys: dict[str, str] = {}
    monkeypatch.setattr("core.credential_store.get_credential", lambda name: keys.get(name))
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: bool(keys.get(name)))
    ccs.reset_probe_rate_limit_for_tests()
    return keys


def _requests(monkeypatch, by_url):
    calls = []

    def fake_get(url, headers=None, timeout=None, **_):
        calls.append({"url": url, "headers": dict(headers or {})})
        status = by_url(url)
        # A custom endpoint's /models is auth-gated: without the key it demands one (401). The
        # one probe policy asks keylessly first, and a stand-in that answered 200 to the keyless
        # ask would make Test certify an arbitrary key as a "public endpoint" refusal instead
        # of green -- the exact shape the unified policy exists to prevent.
        if url.endswith("/models") and not (headers or {}).get("Authorization"):
            status = 401
        # HTTPResponse-shaped: the probe reads .status and the body through the outbound door. A
        # 2xx must carry the provider's documented answer (a key record for OpenRouter's /key, a
        # model list for /models); an empty 200 is not a verified key.
        answer = {"data": {"label": "test"}} if url.endswith("/key") else {"object": "list", "data": []}
        body = json.dumps(answer).encode("utf-8") if status == 200 else b""
        return types.SimpleNamespace(status=status, read=lambda *_a: body)

    monkeypatch.setattr("core.remote_fetch_policy.open_remote_url", fake_get)
    return calls


def test_openai_probes_models_not_key(env, monkeypatch):
    env["llm.cloud.openai"] = "sk-proj-good"
    calls = _requests(monkeypatch, lambda url: 200)
    res = ccs.run_auth_probe(provider="openai", now=1000.0)
    assert res["state"] == ccs.STATE_OK and res["provider"] == "openai"
    assert calls[0]["url"] == "https://api.openai.com/v1/models"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-proj-good"


def test_openrouter_still_probes_key(env, monkeypatch):
    env["llm.cloud.openrouter"] = "sk-or-good"
    calls = _requests(monkeypatch, lambda url: 200)
    res = ccs.run_auth_probe(provider="openrouter", now=1000.0)
    assert res["state"] == ccs.STATE_OK
    assert calls[0]["url"].endswith("/key")


def test_anthropic_sends_version_header(env, monkeypatch):
    env["llm.cloud.anthropic"] = "sk-ant-good"
    calls = _requests(monkeypatch, lambda url: 200)
    ccs.run_auth_probe(provider="anthropic", now=1000.0)
    assert calls[0]["headers"].get("anthropic-version")


def test_cache_is_per_provider_no_cross_green(env, monkeypatch):
    env["llm.cloud.openai"] = "sk-proj-good"
    env["llm.cloud.groq"] = "gsk_bad"
    # Groq base is api.groq.com/openai/v1, so match the host, not the substring "openai".
    _requests(monkeypatch, lambda url: 200 if "api.openai.com" in url else 401)
    ccs.run_auth_probe(provider="openai", now=1000.0)
    ccs.run_auth_probe(provider="groq", now=1000.0)
    assert ccs.connection_status(provider="openai")["state"] == ccs.STATE_OK
    assert ccs.connection_status(provider="groq")["state"] == ccs.STATE_FAILED  # not the openai green


def test_key_change_degrades_green_to_untested(env, monkeypatch):
    env["llm.cloud.openai"] = "sk-proj-good"
    _requests(monkeypatch, lambda url: 200)
    ccs.run_auth_probe(provider="openai", now=1000.0)
    assert ccs.connection_status(provider="openai")["state"] == ccs.STATE_OK
    env["llm.cloud.openai"] = "sk-proj-different"  # new key, no probe yet
    assert ccs.connection_status(provider="openai")["state"] == ccs.STATE_UNTESTED


def test_state_file_never_contains_key_material(env, monkeypatch):
    env["llm.cloud.openai"] = "sk-proj-SECRET-VALUE-123"
    _requests(monkeypatch, lambda url: 200)
    ccs.run_auth_probe(provider="openai", now=1000.0)
    blob = (env and (ccs._state_path()).read_text(encoding="utf-8"))
    assert "sk-proj-SECRET-VALUE-123" not in blob and "SECRET-VALUE" not in blob


def test_no_key_is_no_key(env):
    assert ccs.connection_status(provider="groq")["state"] == ccs.STATE_NO_KEY


def test_active_provider_default_is_openrouter_legacy(env, monkeypatch):
    # No policy provider + only an openrouter key -> status with no provider arg resolves openrouter.
    env["llm.cloud.openrouter"] = "sk-or-good"
    _requests(monkeypatch, lambda url: 200)
    res = ccs.connection_status(probe=True, now=1000.0)
    assert res["provider"] == "openrouter" and res["state"] == ccs.STATE_OK


def test_custom_base_url_change_flips_green_to_untested(env, monkeypatch):
    # A custom endpoint re-pointed to a new host (SAME key) must not keep a stale green: the cache
    # identity binds the resolved endpoint, so the pill degrades to untested until a fresh probe.
    monkeypatch.setenv("VOOL_CUSTOM_API_KEY", "sk-fixed-custom-key")
    monkeypatch.setenv("VOOL_CUSTOM_BASE_URL", "https://api.alpha.example/v1")
    _requests(monkeypatch, lambda url: 200)
    ccs.reset_probe_rate_limit_for_tests()
    assert ccs.run_auth_probe(provider="custom", now=2000.0)["state"] == ccs.STATE_OK
    # Re-point to a different host — the cached green for the old host must NOT carry over.
    monkeypatch.setenv("VOOL_CUSTOM_BASE_URL", "https://api.beta.example/v1")
    later = ccs.connection_status(provider="custom", probe=False, now=2001.0)
    assert later["state"] == ccs.STATE_UNTESTED
