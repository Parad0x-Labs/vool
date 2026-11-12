"""The Web0 switch on the request-driven lookup surface (/api/web0/resolve).

Off: the resolve answers a calm refusal and never touches the resolver (no RPC,
no network). On: the previous behavior is unchanged — success, name-not-found
and resolver-unreachable paths included.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post
from core.web0_settings import set_web0_enabled


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _resolve(name: str = "web0"):
    return dispatch_get(
        path="/api/web0/resolve",
        query={"name": [name]},
        runtime=_rt(),
        model_name="vool",
    )


class _Record:
    owner = "Owner1111111111111111111111111111111111111"
    arweave_txid = "ABCdef1234567890ABCdef1234567890ABCdef1234567"
    x402_endpoint = ""


def test_off_refuses_without_touching_the_resolver(monkeypatch) -> None:
    import core.null_resolver as resolver

    def _must_not_run(*_a, **_k):  # pragma: no cover - the assertion is that this never runs
        raise AssertionError("resolver was called while Web0 is off")

    monkeypatch.setattr(resolver, "resolve_null_domain", _must_not_run)
    response = _resolve()
    assert response.status == 403
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["code"] == "web0_off"
    assert "Settings" in payload["error"]


def test_on_resolves_through_the_real_route(monkeypatch) -> None:
    import core.null_resolver as resolver

    set_web0_enabled(True)
    monkeypatch.setattr(resolver, "resolve_null_domain", lambda name: _Record())
    response = _resolve("some-name")
    assert response.status == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["name"] == "some-name"  # the route echoes the normalized (suffix-stripped) name
    assert payload["arweave_txid"] == _Record.arweave_txid


def test_on_name_not_found_is_unchanged(monkeypatch) -> None:
    import core.null_resolver as resolver

    set_web0_enabled(True)
    monkeypatch.setattr(resolver, "resolve_null_domain", lambda name: None)
    response = _resolve("nope")
    assert response.status == 404
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is False


def test_on_resolver_unreachable_is_unchanged(monkeypatch) -> None:
    import core.null_resolver as resolver

    set_web0_enabled(True)

    def _down(*_a, **_k):
        raise OSError("network down")

    monkeypatch.setattr(resolver, "resolve_null_domain", _down)
    response = _resolve("anything")
    assert response.status == 502
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is False
    assert "resolver error" in payload["error"]


def _post_settings(body: dict):
    return dispatch_post(
        path="/api/web0/settings",
        body=body,
        headers={},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )


def test_settings_route_round_trip(monkeypatch) -> None:
    # The write side may start the background work when switching on; this test
    # is about the route contract, so stub the starter (the real starter is
    # exercised in tests/test_web0_boot_gate.py) — otherwise a live poller
    # thread outlives the test and pollutes thread-count assertions elsewhere.
    import core.runtime_backbone as backbone

    monkeypatch.setattr(backbone, "start_web0_background_workers", lambda: None)

    get = dispatch_get(path="/api/web0/settings", query={}, runtime=_rt(), model_name="vool", client_host="127.0.0.1")
    assert get.status == 200
    assert json.loads(get.body.decode("utf-8")) == {"ok": True, "enabled": False}

    post = _post_settings({"enabled": True})
    assert post.status == 200
    assert json.loads(post.body.decode("utf-8")) == {"ok": True, "enabled": True}

    after = dispatch_get(path="/api/web0/settings", query={}, runtime=_rt(), model_name="vool", client_host="127.0.0.1")
    assert json.loads(after.body.decode("utf-8")) == {"ok": True, "enabled": True}

    back_off = _post_settings({"enabled": False})
    assert json.loads(back_off.body.decode("utf-8")) == {"ok": True, "enabled": False}


def test_settings_post_rejects_non_boolean_body() -> None:
    for bad in ({"enabled": "yes"}, {}, {"enabled": 1}, {"other": True}):
        response = _post_settings(bad)
        assert response.status == 400, bad
        assert json.loads(response.body.decode("utf-8"))["ok"] is False


def test_settings_page_serves_the_web0_row() -> None:
    from core.vool_settings_page import render_vool_settings_html

    html = render_vool_settings_html()
    # The row is in the served model, its switch widget is wired, and the page
    # talks to the same /api/web0/settings route the tests above exercise.
    assert '"id":"web0"' in html
    assert '"widget":"web0"' in html
    assert "/api/web0/settings" in html
    assert "Special web addresses" in html
