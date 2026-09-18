"""Windows VOOL deep-link registration and loopback callback handoff contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import oauth_callback
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post
from installer.bundle import vool_protocol_handler

_BUNDLE = Path(__file__).resolve().parent.parent / "installer" / "bundle"


@pytest.fixture(autouse=True)
def clear_callback_inbox():
    oauth_callback.clear()
    yield
    oauth_callback.clear()


def _body(response):
    return json.loads((response.body or b"{}").decode("utf-8"))


def test_callback_uri_preserves_decoded_code_and_state():
    assert vool_protocol_handler.parse_callback_uri(
        "vool://auth/openrouter/callback?code=abc%2B123&state=state%2Fvalue"
    ) == ("abc+123", "state/value")


@pytest.mark.parametrize(
    "uri",
    (
        "https://auth/openrouter/callback?code=x&state=y",
        "vool://other/openrouter/callback?code=x&state=y",
        "vool://auth/wrong?code=x&state=y",
        "vool://auth/openrouter/callback?code=x",
        "vool://auth/openrouter/callback?code=x&state=y&state=z",
        "vool://auth/openrouter/callback?code=x&state=y#fragment",
    ),
)
def test_callback_uri_rejects_wrong_or_ambiguous_shapes(uri):
    with pytest.raises(ValueError):
        vool_protocol_handler.parse_callback_uri(uri)


def test_loopback_callback_endpoint_delivers_both_values_without_exposing_them():
    response = dispatch_post(
        path="/api/auth/openrouter/callback",
        body={"code": "code-value", "state": "state-value"},
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: ".",
        client_host="127.0.0.1",
    )

    assert response.status == 200
    payload = _body(response)
    assert payload["ok"] is True
    assert "code-value" not in response.body.decode("utf-8")
    assert payload["state"] == "state-value"
    callback = oauth_callback.take_callback()
    assert callback is not None
    assert (callback["code"], callback["state"]) == ("code-value", "state-value")


def test_loopback_callback_endpoint_rejects_remote_and_get_requests():
    post = dispatch_post(
        path="/api/auth/openrouter/callback",
        body={"code": "x", "state": "y"},
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: ".",
        client_host="192.0.2.10",
    )
    get = dispatch_get(
        path="/api/auth/openrouter/callback",
        query={},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
    )
    assert post.status == 403
    assert get.status == 404
    assert oauth_callback.take_callback() is None


def test_protocol_handler_retries_after_starting_bundle(monkeypatch, tmp_path):
    attempts = iter((False, True))
    started: list[Path] = []
    sleeps: list[float] = []

    monkeypatch.setattr(vool_protocol_handler, "_post_callback", lambda code, state: next(attempts))
    monkeypatch.setattr(vool_protocol_handler, "_start_bundle", lambda root: started.append(root))

    assert vool_protocol_handler.deliver_callback(
        "vool://auth/openrouter/callback?code=x&state=y",
        root=tmp_path,
        sleep=sleeps.append,
    ) is True
    assert started == [tmp_path.resolve()]
    assert sleeps == [1.0]


def test_bundle_build_and_installer_register_vool_protocol():
    build = (_BUNDLE / "build_bundle.ps1").read_text(encoding="utf-8")
    iss = (_BUNDLE / "vool.iss").read_text(encoding="utf-8")
    assert '"vool_protocol_handler.py"' in build
    assert 'Source: "{#Stage}\\vool_protocol_handler.py"' in iss
    assert 'Subkey: "Software\\Classes\\vool"' in iss
    assert 'ValueData: "URL:VOOL Protocol"' in iss
    assert 'ValueName: "URL Protocol"' in iss
    assert '"{app}\\vool_protocol_handler.py"' in iss
    assert '"%1"' in iss
    assert 'Root: HKCU' in iss
