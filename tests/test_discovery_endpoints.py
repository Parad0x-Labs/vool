"""The discovery HTTP doors: GET /api/discovery (cache-only truth) and POST
/api/discovery/refresh (one provider, one explicit operator action).

Guards mirror the other keyed POSTs (content-type, same-origin, loopback owner, closed field
list); GET never touches the network; the refresh response is the assertion with no key
material; an unknown provider is a typed 404; and a web-search provider is refused as a model
discovery subject rather than half-answered.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post
from tests._credential_intelligence_support import FakeProviderServer

PROVIDER_KEY = "sk-or-v1-" + "f" * 56


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture
def rig(monkeypatch):
    with FakeProviderServer() as server:
        from core.credential_intelligence.provider_registry import default_registry

        base = default_registry().get("openrouter")
        descriptor = base.__class__(**{**base.__dict__, "verify_endpoint": f"{server.url}/api/v1/key"})

        def respond(record):
            if record["path"].endswith("/key"):
                return (401, {"error": {"code": 401, "message": "no"}}) if not record["auth_present"] else (200, {"data": {"label": "h", "limit_remaining": None}})
            if record["path"].endswith("/models"):
                return (401, {"error": {"code": 401, "message": "no"}}) if not record["auth_present"] else (200, {"object": "list", "data": [{"id": "lab/one", "context_length": 8192}]})
            return (404, {"error": {"code": 404, "message": "no"}})

        server.responses = respond
        import core.credential_intelligence.provider_registry as pr

        table = {d.provider_id: d for d in pr.default_registry().providers()}
        table["openrouter"] = descriptor
        registry = pr.ProviderRegistry(table)
        monkeypatch.setattr(pr, "default_registry", lambda: registry)
        monkeypatch.setattr(
            "core.credential_store.get_credential",
            lambda name: PROVIDER_KEY if name == "llm.cloud.openrouter" else None,
        )
        yield server


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _get(path):
    return dispatch_get(path=path, query={}, runtime=_rt(), model_name="vool")


def _post(body, headers=None):
    return dispatch_post(
        path="/api/discovery/refresh",
        body=body,
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        client_host="127.0.0.1",
        workspace_root_provider=lambda: "/tmp",
    )


def _body(response):
    return json.loads(response.body.decode("utf-8"))


def test_get_is_cache_only_and_shows_nothing_before_a_refresh(rig):
    payload = _body(_get("/api/discovery"))
    assert payload["providers"] == {}
    assert rig.request_count == 0


def test_refresh_observes_and_get_serves_the_same_truth(rig):
    result = _body(_post({"provider": "openrouter"}))
    assert result["ok"] is True
    assert result["last_refresh_status"] == "verified"
    assert [m["model_id"] for m in result["models"]] == ["lab/one"]
    assert PROVIDER_KEY not in json.dumps(result)
    served = _body(_get("/api/discovery"))["providers"]["openrouter"]
    assert served["evidence"] == "observed"
    assert served["fresh"] is True


def test_guards_reject_bad_content_type_origin_fields_and_nonowner(rig):
    assert _post({"provider": "openrouter"}, headers={"content-type": "text/plain"}).status == 415
    assert _post({"provider": "openrouter"}, headers={"content-type": "application/json", "origin": "https://evil.example"}).status == 403
    assert _post({"provider": "openrouter", "surprise": 1}).status == 400
    assert _post({"provider": ""}).status == 400


def test_unknown_provider_is_a_typed_404(rig):
    response = _post({"provider": "nosuch"})
    assert response.status == 404
    assert _body(response)["error"] == "unknown provider"


def test_search_provider_is_refused_as_a_model_discovery_subject(rig):
    response = _post({"provider": "brave"})
    assert response.status == 404, "a search provider must not half-answer model discovery"


def test_refresh_without_a_key_sends_nothing(rig, monkeypatch):
    monkeypatch.setattr("core.credential_store.get_credential", lambda name: None)
    result = _body(_post({"provider": "openrouter"}))
    assert result["last_refresh_status"] == "no_key"
    assert rig.request_count == 0
