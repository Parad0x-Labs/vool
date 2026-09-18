"""/api/cloud/status + /api/cloud/test: the connection indicator's backend.

Status is cache-only (pollable), test runs the live auth probe behind the same guard suite as
the other state-changing POSTs, and no response ever carries key material.
"""
from __future__ import annotations

import json
import types

import pytest

from core import cloud_connection_state as ccs
from core import runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

_SECRET = "sk-or-v1-" + "c" * 56


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("VOOL_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(ccs, "_state_path", lambda: tmp_path / "conn_state.json")
    ccs.reset_probe_rate_limit_for_tests()
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture
def keys(monkeypatch):
    saved: dict[str, str] = {}
    monkeypatch.setattr("core.credential_store.get_credential", lambda name: saved.get(name))
    return saved


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _status(query=None):
    return dispatch_get(path="/api/cloud/status", query=query or {}, runtime=_rt(), model_name="vool")


def _test_post(body=None, headers=None):
    return dispatch_post(
        path="/api/cloud/test",
        body=body if body is not None else {},
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
    )


def _body(response):
    import json

    return json.loads(response.body.decode("utf-8"))


def _fake_requests(monkeypatch, *, status=200):
    """Fake the ONE outbound door, not ``requests.get``. The probe has not used requests for a
    long time; a requests fake let the probe's keyed request straight through to the real
    openrouter.ai (the incident of 2026-09-14), and the 401 test passed only because the real
    service answered 401. The answer carries OpenRouter's documented /key shape so a 200 is
    genuinely a verified key."""
    calls: list[str] = []

    def fake_open(url, **_kwargs):
        calls.append(str(url))
        body = (
            json.dumps({"data": {"label": "test", "limit_remaining": None}}).encode("utf-8")
            if status == 200
            else b""
        )
        if status != 200:
            import urllib.error

            raise urllib.error.HTTPError(url, status, "fake", {"Content-Type": "application/json"}, __import__("io").BytesIO(body))
        return types.SimpleNamespace(status=status, read=lambda *_a: body, headers={})

    monkeypatch.setattr("core.remote_fetch_policy.open_remote_url", fake_open)
    return calls


def test_status_no_key_is_gray_and_carries_policy_fields(keys):
    payload = _body(_status())
    assert payload["state"] == "no_key"
    assert "model" in payload and "mode" in payload


def test_status_is_cache_only_without_probe_flag(keys, monkeypatch):
    keys["llm.cloud.openrouter"] = _SECRET
    calls = _fake_requests(monkeypatch)
    payload = _body(_status())
    assert payload["state"] == "untested"
    assert calls == [], "plain status must never hit the network"


def test_test_endpoint_probes_live_and_reports_green(keys, monkeypatch):
    keys["llm.cloud.openrouter"] = _SECRET
    calls = _fake_requests(monkeypatch, status=200)
    res = _test_post()
    assert res.status == 200
    assert _body(res)["state"] == "ok"
    assert len(calls) == 1 and calls[0].endswith("/key")
    # And status now serves the green verdict from cache.
    assert _body(_status())["state"] == "ok"


def test_test_endpoint_reports_red_on_401(keys, monkeypatch):
    keys["llm.cloud.openrouter"] = _SECRET
    _fake_requests(monkeypatch, status=401)
    payload = _body(_test_post())
    assert payload["state"] == "failed" and payload["detail"] == "unauthorized"


def test_guards_reject_bad_content_type_origin_and_fields(keys):
    assert _test_post(headers={"content-type": "text/plain"}).status == 415
    assert _test_post(headers={"content-type": "application/json", "origin": "https://evil.example"}).status == 403
    assert _test_post(body={"surprise": 1}).status == 400


def test_no_response_ever_contains_the_key(keys, monkeypatch):
    keys["llm.cloud.openrouter"] = _SECRET
    _fake_requests(monkeypatch, status=200)
    for raw in (_test_post().body.decode(), _status().body.decode(), _status({"probe": ["1"]}).body.decode()):
        assert _SECRET not in raw
        assert _SECRET[-8:] not in raw
