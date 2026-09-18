"""/api/cloud/models + /api/cloud/model: the dropdown's data source and switch path.

Free models always first from live catalog data; paid ordering is user-selectable; the switch
POST drives the same shared helper as the `cloud model` chat command and is owner-gated by the
TCP peer, never by a body field.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from core import runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

PAYLOAD = {
    "data": [
        {"id": "zzz/alpha-big", "name": "Alpha Big", "context_length": 500000,
         "pricing": {"prompt": "0.000001", "completion": "0.000002", "request": "0"}},
        {"id": "qwen/qwen-mega", "name": "Qwen Mega", "context_length": 100000,
         "pricing": {"prompt": "0.000003", "completion": "0.000004", "request": "0"}},
        {"id": "deepseek/deepseek-chat-v3:free", "name": "DeepSeek Free", "context_length": 163840,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
        {"id": "qwen/qwen3-coder:free", "name": "Qwen3 Coder Free", "context_length": 262144,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
    ]
}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    import core.openrouter_catalog as cat

    cache = tmp_path / "catalog_cache.json"
    cache.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "payload": PAYLOAD}))
    monkeypatch.setattr(cat, "_cache_path", lambda: cache)
    monkeypatch.setattr(
        cat, "refresh_openrouter_catalog",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("no network in tests")),
    )
    yield
    runtime_paths.configure_runtime_home(None)


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _models(query=None):
    res = dispatch_get(path="/api/cloud/models", query=query or {}, runtime=_rt(), model_name="vool")
    return json.loads(res.body.decode("utf-8"))


def _switch(body, *, client_host="127.0.0.1", headers=None):
    res = dispatch_post(
        path="/api/cloud/model",
        body=body,
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=client_host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def _switch_auto(body, *, client_host="127.0.0.1", headers=None):
    res = dispatch_post(
        path="/api/cloud/auto-model",
        body=body,
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=client_host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def test_free_models_always_come_first_with_prices():
    payload = _models()
    ids = [m["id"] for m in payload["models"]]
    assert payload["free_count"] == 2
    assert set(ids[:2]) == {"deepseek/deepseek-chat-v3:free", "qwen/qwen3-coder:free"}
    free_row = next(m for m in payload["models"] if m["id"] == "deepseek/deepseek-chat-v3:free")
    assert free_row["free"] is True and free_row["prompt_usd_per_m"] == 0.0
    paid_row = next(m for m in payload["models"] if m["id"] == "zzz/alpha-big")
    assert paid_row["free"] is False and paid_row["prompt_usd_per_m"] == 1.0  # 1e-6 * 1M
    assert payload["auto_free_model"] == "auto"


def test_paid_ordering_modes():
    by_name = [m["id"] for m in _models({"order": ["name"]})["models"][2:]]
    assert by_name == ["qwen/qwen-mega", "zzz/alpha-big"]
    by_context = [m["id"] for m in _models({"order": ["context"]})["models"][2:]]
    assert by_context == ["zzz/alpha-big", "qwen/qwen-mega"]
    featured = [m["id"] for m in _models({"order": ["featured"]})["models"][2:]]
    assert featured == ["qwen/qwen-mega", "zzz/alpha-big"], "known family outranks bigger context in featured"


def test_coding_models_are_tagged():
    payload = _models()
    coder = next(m for m in payload["models"] if m["id"] == "qwen/qwen3-coder:free")
    assert coder["coding"] is True


def test_refresh_failure_degrades_to_cache_not_invention():
    payload = _models({"refresh": ["1"]})
    assert payload["refresh"]["ok"] is False
    assert payload["free_count"] == 2, "stale cache still served after a failed refresh"


def test_switch_persists_via_the_shared_helper(monkeypatch):
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    status, payload = _switch({"model": "deepseek/deepseek-chat-v3:free"})
    assert status == 200 and payload["ok"] is True and payload["model"].endswith(":free")
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model == "deepseek/deepseek-chat-v3:free"


def test_switch_is_owner_gated_by_tcp_peer(monkeypatch):
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    status, payload = _switch({"model": "a/b"}, client_host="192.168.1.50")
    assert status == 403 and payload["ok"] is False
    assert payload["error"] == "owner_local_required"
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model == "", "a non-loopback peer must never change the model"


def test_switch_guards_reject_bad_shapes(monkeypatch):
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    assert _switch({"model": "not a real model!"})[0] == 400  # spaces/illegal -> rejected
    assert _switch({"model": "a/b", "extra": 1})[0] == 400
    assert _switch({"model": "a/b"}, headers={"content-type": "text/plain"})[0] == 415
    assert _switch({"model": "a/b"}, headers={"content-type": "application/json", "origin": "https://evil.example"})[0] == 403


def test_auto_fallback_endpoint_is_verified_free_strict_and_owner_local():
    status, payload = _switch_auto({"model": "qwen/qwen3-coder:free"})
    assert status == 200 and payload["ok"] is True
    assert _models()["auto_free_model"] == "qwen/qwen3-coder:free"

    assert _switch_auto({"model": "zzz/alpha-big"})[0] == 400
    assert _switch_auto({"model": "not/catalogued:free"})[0] == 400
    assert _switch_auto({"model": "auto"}, client_host="192.168.1.50")[0] == 403
    assert _models()["auto_free_model"] == "qwen/qwen3-coder:free"
