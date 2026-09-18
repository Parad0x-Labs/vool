"""Failure-first seams for the UsePod provider lane.

Every test in this file was written to run unchanged against the pinned base (df49f096) with only this
directory overlaid, where each FAILS for the reason its name states, and against the candidate, where
each passes. They pin the defects at the boundaries where they occur, not a scripted conversation:

* UsePod's token is a URL path segment with no vendor shape, so the central redactor let every
  token-bearing URL through (error text, log lines, escaped or percent-encoded copies);
* the per-turn fetch ledger stored the full request URL;
* the generic OpenAI-compatible lane re-raised ``requests``' own HTTP error, whose text names the full
  URL, and chained the original onto its replacement;
* Settings had no UsePod provider: the only door was the custom endpoint, which stores a pasted proxy
  URL as a non-secret "base URL" (copied into manifests) and demands an API key UsePod ignores.

Negative controls ride beside each family so a repair cannot pass by masking everything.
All tokens here are SYNTHETIC.
"""
from __future__ import annotations

import json
import traceback

import pytest
import requests

UUID_TOKEN = "7d2f9c4e-1a3b-4c5d-8e6f-9a0b1c2d3e4f"
OPAQUE_TOKEN = "tkn_Q8w3Zr5Yt1Uv7Xp9"

LEAKING_TEXT = [
    pytest.param(
        f"402 Client Error: Payment Required for url: https://api.usepod.ai/proxy/{UUID_TOKEN}/v1/chat/completions",
        UUID_TOKEN,
        id="http-library-error-text",
    ),
    pytest.param(f"POST /proxy/{UUID_TOKEN}/v1/messages -> 503", UUID_TOKEN, id="bare-path-log-line"),
    pytest.param(
        '{"base_url": "https:\\/\\/api.usepod.ai\\/proxy\\/' + UUID_TOKEN + '\\/v1"}', UUID_TOKEN, id="json-escaped-slashes"
    ),
    pytest.param(
        f"https://relay.example.test/redirect?next=%2Fproxy%2F{OPAQUE_TOKEN}%2Fbalance", OPAQUE_TOKEN, id="percent-encoded-opaque-token"
    ),
    pytest.param(f"curl http://127.0.0.1:8123/proxy/{OPAQUE_TOKEN}?stream=1", OPAQUE_TOKEN, id="loopback-origin-query-suffix"),
]

READABLE_TEXT = [
    pytest.param("POST https://api.usepod.ai/proxy/x402/v1/chat/completions -> 402", id="accountless-x402-path"),
    pytest.param("GET https://api.usepod.ai/v1/marketplace/models", id="public-feed-path"),
    pytest.param("see /proxy/short/v1 for the demo", id="segment-too-short-to-be-a-token"),
    pytest.param("route the build through the corporate proxy/ gateway", id="ordinary-prose"),
]


@pytest.mark.parametrize(("text", "secret"), LEAKING_TEXT)
def test_the_central_redactor_masks_a_credential_carried_in_a_proxy_path(text: str, secret: str) -> None:
    from core.secret_redaction import contains_secret, redact_secrets

    assert secret not in redact_secrets(text)
    assert contains_secret(text)


@pytest.mark.parametrize("text", READABLE_TEXT)
def test_the_central_redactor_leaves_credential_free_proxy_text_readable(text: str) -> None:
    from core.secret_redaction import contains_secret, redact_secrets

    assert redact_secrets(text) == text
    assert not contains_secret(text)


def test_the_turn_fetch_ledger_keeps_where_a_call_went_but_not_the_path_token() -> None:
    from core.remote_fetch_policy import _FetchLedger

    ledger = _FetchLedger()
    ledger.note(f"https://api.usepod.ai/proxy/{UUID_TOKEN}/balance", status=200)
    ledger.note("https://api.usepod.ai/v1/marketplace/models", status=200)
    entries = ledger.entries()
    assert UUID_TOKEN not in json.dumps(entries)
    assert [entry["host"] for entry in entries] == ["api.usepod.ai", "api.usepod.ai"]
    assert entries[1]["url"] == "https://api.usepod.ai/v1/marketplace/models"


def _error_response(url: str, status: int, body: bytes) -> requests.Response:
    response = requests.models.Response()
    response.status_code = status
    response.url = url
    response.reason = "Synthetic"
    response._content = body
    return response


@pytest.mark.parametrize(
    "body",
    [b"", b'{"error": {"type": "insufficient_balance", "message": "token has no balance"}}'],
    ids=["empty-body-reraise-path", "json-body-excerpt-path"],
)
def test_an_http_failure_on_the_openai_compatible_lane_carries_no_path_token(body: bytes) -> None:
    from adapters.openai_compatible_adapter import _raise_for_status_with_cause

    url = f"https://api.usepod.ai/proxy/{UUID_TOKEN}/v1/chat/completions"
    with pytest.raises(requests.HTTPError) as caught:
        _raise_for_status_with_cause(_error_response(url, 402, body))
    rendered = "".join(traceback.format_exception(caught.value))
    assert UUID_TOKEN not in rendered
    assert "402" in str(caught.value)


def test_a_success_response_on_the_openai_compatible_lane_raises_nothing() -> None:
    from adapters.openai_compatible_adapter import _raise_for_status_with_cause

    _raise_for_status_with_cause(_error_response(f"https://api.usepod.ai/proxy/{UUID_TOKEN}/v1/models", 200, b"{}"))


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield tmp_path
    runtime_paths.configure_runtime_home(None)


def _dispatch_post(path: str, body: dict) -> tuple[int, dict, str]:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    response = dispatch_post(
        path=path,
        body=body,
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
    )
    text = response.body.decode("utf-8")
    return response.status, json.loads(text), text


def _dispatch_get(path: str, query: dict | None = None) -> str:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    return dispatch_get(path=path, query=query or {}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool").body.decode("utf-8")


def _manifest_rows_text() -> str:
    from storage.model_provider_manifest import list_provider_manifests

    return json.dumps([row.model_dump() for row in list_provider_manifests()], default=str)


@pytest.mark.parametrize(
    "pasted",
    [
        pytest.param(f"https://api.usepod.ai/proxy/{UUID_TOKEN}/v1", id="openai-base-url-paste"),
        pytest.param(f"https://api.usepod.ai/proxy/{UUID_TOKEN}", id="anthropic-base-url-paste"),
        pytest.param(UUID_TOKEN, id="bare-token-paste"),
    ],
)
def test_settings_stores_a_usepod_credential_as_a_token_never_as_a_base_url(isolated_home, pasted: str) -> None:
    from core import credential_store

    status, payload, text = _dispatch_post("/api/settings/credentials", {"provider": "usepod", "value": pasted})
    assert status == 200, payload
    assert UUID_TOKEN not in text
    assert credential_store.get_credential("llm.cloud.usepod") == UUID_TOKEN
    assert not credential_store.get_credential("llm.cloud.custom_base_url")
    assert UUID_TOKEN not in _dispatch_get("/api/settings/credentials")
    assert UUID_TOKEN not in _manifest_rows_text()


def test_settings_refuses_a_usepod_paste_that_is_not_a_proxy_credential(isolated_home) -> None:
    from core import credential_store

    for pasted in (
        "https://api.usepod.ai/proxy/x402/v1",  # the accountless path carries no token
        f"https://api.usepod.ai/v1/chat/completions?token={UUID_TOKEN}",  # not the documented shape
        f"http://api.usepod.ai/proxy/{UUID_TOKEN}/v1",  # a credential never travels over cleartext
    ):
        status, payload, text = _dispatch_post("/api/settings/credentials", {"provider": "usepod", "value": pasted})
        assert status == 400, (pasted, payload)
        assert UUID_TOKEN not in text
    assert not credential_store.get_credential("llm.cloud.usepod")
