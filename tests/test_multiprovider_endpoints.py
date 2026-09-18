"""Provider-aware cloud endpoints: credentials whitelist widened to the provider slots (still
closed), status/test/models take a provider, and every existing guard stays intact."""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.cloud_model_catalog import curated_models
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _rt():
    return RuntimeServices(display_name="VOOL")


def _post(path, body, headers=None):
    res = dispatch_post(path=path, body=body,
                        headers=headers if headers is not None else {"content-type": "application/json"},
                        runtime=_rt(), model_name="vool", workspace_root_provider=lambda: "/tmp", client_host="127.0.0.1")
    return res.status, json.loads(res.body.decode("utf-8"))


def _get(path, query=None):
    res = dispatch_get(path=path, query=query or {}, runtime=_rt(), model_name="vool")
    return json.loads(res.body.decode("utf-8"))


def test_credentials_accepts_a_provider_slot():
    status, body = _post("/api/settings/credentials", {"provider": "openai", "value": "sk-proj-" + "a" * 40})
    assert status == 200 and body["provider"] == "openai" and body["connected"] is True


def test_credentials_still_rejects_arbitrary_names():
    assert _post("/api/settings/credentials", {"name": "llm.evil", "value": "x"})[0] == 400
    assert _post("/api/settings/credentials", {"provider": "bogus", "value": "x"})[0] == 400
    # name and provider must agree
    assert _post("/api/settings/credentials", {"name": "llm.cloud.openai", "provider": "groq", "value": "x"})[0] == 400


def test_credentials_guards_intact():
    assert _post("/api/settings/credentials", {"provider": "openai", "value": "x", "junk": 1})[0] == 400
    assert _post("/api/settings/credentials", {"provider": "openai", "value": "x"}, headers={"content-type": "text/plain"})[0] == 415
    assert _post("/api/settings/credentials", {"provider": "openai", "value": "x"},
                 headers={"content-type": "application/json", "origin": "https://evil.example"})[0] == 403


def test_openrouter_backward_compat_credentials():
    status, body = _post("/api/settings/credentials", {"name": "llm.cloud.openrouter", "value": "sk-or-" + "b" * 40, "label": "OpenRouter"})
    assert status == 200 and body["connected"] is True


def test_providers_endpoint_lists_all():
    ids = {p["id"] for p in _get("/api/cloud/providers")["providers"]}
    assert {"openrouter", "openai", "anthropic", "groq", "google", "deepseek", "moonshot", "custom"} <= ids


def test_models_endpoint_serves_curated_for_a_direct_provider():
    body = _get("/api/cloud/models", {"provider": ["openai"]})
    assert body["provider"] == "openai" and body["free_count"] == 0
    ids = {m["id"] for m in body["models"]}
    assert "gpt-4.1-mini" in ids
    assert all(m["provider"] == "openai" for m in body["models"])
    assert len(body["models"]) == len(curated_models("openai"))


def test_models_endpoint_openrouter_still_free_first():
    body = _get("/api/cloud/models", {"provider": ["openrouter"]})
    assert body["provider"] == "openrouter" and "free_count" in body


def test_switch_accepts_a_provider_field(monkeypatch):
    """The provider field still selects the lane; a DIRECT provider's concrete pin is a
    metered-spend choice, so it now refuses unconfirmed and persists on confirmation."""
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    status, body = _post("/api/cloud/model", {"model": "gpt-4.1-mini", "provider": "openai"})
    assert status == 409 and body["code"] == "paid_model_confirm_required"
    status, body = _post("/api/cloud/model", {"model": "gpt-4.1-mini", "provider": "openai", "confirm_paid": True})
    assert status == 200 and body["ok"] is True and body["model"] == "gpt-4.1-mini"
    from core.cloud_escalation_policy import load_policy
    assert load_policy().provider == "openai"


def test_switch_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    assert _post("/api/cloud/model", {"model": "x", "provider": "bogus"})[0] == 400
