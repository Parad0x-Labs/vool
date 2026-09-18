"""Key-transport safety: a Bearer API key must never travel to a plaintext, non-loopback host.

One canonical gate — ``core.cloud_providers.is_safe_key_transport`` — is shared by the credentials
endpoint, the connection probe, and the completion adapter. The audit found a raw
``startswith("http://127.0.0.1")`` / ``host.startswith("127.")`` check that accepted
``http://127.0.0.1.evil.com`` (a name that merely starts with the loopback string but resolves to
an external host), leaking the key in cleartext. These tests pin the parsed-host behaviour and the
three call sites.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.cloud_providers import is_safe_key_transport
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_post


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


# ---- unit: the canonical gate -------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "https://api.anthropic.com/v1",
    "https://internal.corp/v1",          # https anywhere is safe (key encrypted in transit)
    "http://127.0.0.1:8080/v1",
    "http://127.0.0.1/v1",
    "http://localhost:1234/v1",
    "http://[::1]:9000/v1",
    "http://127.5.9.3/v1",               # all of 127.0.0.0/8 is loopback
])
def test_safe_transports_accepted(url) -> None:
    assert is_safe_key_transport(url) is True


@pytest.mark.parametrize("url", [
    "http://127.0.0.1.evil.com/v1",      # THE bypass: starts with the loopback string, resolves external
    "http://localhost.evil.com/v1",
    "http://127.0.0.1@evil.com/v1",      # userinfo is 127.0.0.1; real host is evil.com
    "http://evil.com/v1",
    "http://10.0.0.5/v1",                # private but not loopback -> cleartext to another box
    "http://169.254.169.254/latest",     # link-local metadata over http
    "ftp://127.0.0.1/v1",
    "http://1270001/v1",                 # not an IP, not localhost
    "",
    "not a url",
])
def test_unsafe_transports_rejected(url) -> None:
    assert is_safe_key_transport(url) is False


# ---- integration: the credentials endpoint (custom base URL) ------------------------------

def _post_custom(base_url):
    res = dispatch_post(
        path="/api/settings/credentials",
        body={"provider": "custom", "value": "sk-customkey-0123456789abcdef", "base_url": base_url},
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def test_custom_endpoint_rejects_loopback_lookalike_host() -> None:
    status, body = _post_custom("http://127.0.0.1.evil.com/v1")
    assert status == 400
    assert "base url" in body.get("error", "").lower()


def test_custom_endpoint_rejects_plaintext_external_host() -> None:
    assert _post_custom("http://evil.com/v1")[0] == 400


def test_custom_endpoint_accepts_https() -> None:
    assert _post_custom("https://api.my-proxy.example/v1")[0] == 200


def test_custom_endpoint_accepts_real_loopback() -> None:
    assert _post_custom("http://127.0.0.1:8080/v1")[0] == 200


# ---- integration: the connection probe withholds the key over an insecure transport --------

def test_probe_refuses_insecure_transport_and_never_calls_out(monkeypatch) -> None:
    from core import cloud_connection_state as ccs

    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("requests.get must not run for an insecure transport")

    monkeypatch.setattr("requests.get", _boom)
    # A custom provider whose base_url is a plaintext external host (e.g. via an env override that
    # bypasses the endpoint validation) must not emit the Bearer key at all. The custom pair is
    # read as ONE fact through resolved_custom_pair (r4), so the override is pinned the way the
    # product actually receives it — the env alias — instead of the retired _base_url seam.
    monkeypatch.setenv("VOOL_CUSTOM_BASE_URL", "http://127.0.0.1.evil.com")
    state, detail, _http_status = ccs._probe_once("custom", "sk-should-not-be-sent")
    assert state == ccs.STATE_FAILED
    assert "insecure" in detail
    assert called["n"] == 0
