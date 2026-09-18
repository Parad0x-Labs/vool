from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
from core.cloud_provider_contract import CloudModelRequest
from core.cloud_transport import PolicyBoundCloudTransport
from core.runtime_provider_defaults import openrouter_attribution_headers

EXPECTED_HEADERS = {
    "HTTP-Referer": "https://vool.dev",
    "X-OpenRouter-Title": "VOOL",
    "X-OpenRouter-Categories": "personal-agent,programming-app",
}


def _assert_vool_headers(headers: dict[str, str]) -> None:
    assert {name: headers.get(name) for name in EXPECTED_HEADERS} == EXPECTED_HEADERS


def test_factory_uses_defaults_without_an_environment_override(monkeypatch):
    for name in (
        "VOOL_OPENROUTER_REFERER",
        "OPENROUTER_REFERER",
        "OPENROUTER_HTTP_REFERER",
        "VOOL_OPENROUTER_TITLE",
        "OPENROUTER_TITLE",
        "OPENROUTER_X_TITLE",
        "VOOL_OPENROUTER_CATEGORIES",
        "OPENROUTER_CATEGORIES",
    ):
        monkeypatch.delenv(name, raising=False)
    assert openrouter_attribution_headers() == EXPECTED_HEADERS


def test_openai_compatible_openrouter_request_forces_attribution_last(monkeypatch):
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "sk-or-test")
    manifest = SimpleNamespace(
        provider_name="openrouter-byok",
        provider_id="openrouter-byok:test-model",
        model_name="test-model",
        runtime_config={
            "base_url": "https://openrouter.ai/api/v1",
            "api_path": "/chat/completions",
            "api_key_env": "TEST_OPENROUTER_KEY",
            "headers": {
                **EXPECTED_HEADERS,
                "HTTP-Referer": "https://caller.example",
                "X-OpenRouter-Title": "Caller",
                "X-OpenRouter-Categories": "caller-category",
            },
        },
        metadata={},
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
    request = ModelRequest(task_kind="chat", prompt="hello", messages=[{"role": "user", "content": "hello"}])

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        OpenAICompatibleAdapter(manifest).run_text_task(request)

    sent = post.call_args.kwargs["headers"]
    _assert_vool_headers(sent)
    assert sent["HTTP-Referer"] != "https://caller.example"
    assert sent["X-OpenRouter-Title"] != "Caller"
    assert sent["X-OpenRouter-Categories"] != "caller-category"


def test_openrouter_cloud_provider_puts_attribution_on_every_provider_request():
    class Transport:
        def __init__(self):
            self.calls: list[dict[str, object]] = []

        def request_json(self, **kwargs):
            self.calls.append(kwargs)
            url = str(kwargs["url"])
            if url.endswith("/models"):
                return 200, {}, {"data": [{"id": "free-model", "pricing": {"prompt": "0", "completion": "0"}}]}
            if url.endswith("/auth/key"):
                return 200, {}, {"data": {"limit_remaining": 0}}
            return 200, {}, {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    transport = Transport()
    provider = OpenRouterCloudProvider()
    assert provider.validate_credentials(transport) == (True, "ok")
    provider.discover_models(transport)
    provider.get_account_limits(transport)
    provider.send_request(
        transport,
        CloudModelRequest(
            task_id="task",
            turn_id="turn",
            subtask_id="subtask",
            model_call_id="call",
            model_id="free-model",
            messages=({"role": "user", "content": "hello"},),
            max_output_tokens=10,
        ),
    )

    assert len(transport.calls) == 4
    for call in transport.calls:
        _assert_vool_headers(call["headers"])


def test_policy_bound_openrouter_transport_overwrites_caller_attribution():
    captured: dict[str, object] = {}

    class Response:
        status_code = 200
        headers: dict[str, str] = {}
        content = b"{}"

        def json(self):
            return {"ok": True}

        def close(self):
            return None

    def requester(*_args, **kwargs):
        captured.update(kwargs)
        return Response()

    transport = PolicyBoundCloudTransport(
        allowed_hosts=("openrouter.ai",),
        requester=requester,
        resolver=lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
        peer_ip_getter=lambda _response: "93.184.216.34",
        network_allowed=lambda: True,
    )
    transport.request_json(
        method="GET",
        url="https://openrouter.ai/api/v1/models",
        headers={"HTTP-Referer": "https://caller.example", "X-OpenRouter-Title": "Caller"},
    )

    _assert_vool_headers(captured["headers"])


def test_openrouter_catalog_request_carries_attribution(monkeypatch, tmp_path):
    import core.openrouter_catalog as catalog

    response = mock.Mock()
    response.status = 200
    response.read.return_value = json.dumps({"data": []}).encode("utf-8")
    monkeypatch.setattr(catalog, "_cache_path", lambda: tmp_path / "catalog.json")
    with mock.patch.object(catalog, "open_remote_url", return_value=response) as get:
        assert catalog.refresh_openrouter_catalog(api_key="sk-or-test") == ()

    sent = get.call_args.kwargs["headers"]
    _assert_vool_headers(sent)
    assert sent["Authorization"] == "Bearer sk-or-test"


def test_openrouter_auth_probe_request_carries_attribution(monkeypatch):
    import core.cloud_connection_state as connection_state

    monkeypatch.setattr(connection_state, "_base_url", lambda _provider: "https://openrouter.ai/api/v1")
    # OpenRouter's documented /key answer: an empty 200 is no longer a green probe (2026-09-14).
    response = SimpleNamespace(status=200, read=lambda *_a: b'{"data": {"label": "attribution", "limit_remaining": null}}')
    with mock.patch("core.remote_fetch_policy.open_remote_url", return_value=response) as get:
        assert connection_state._probe_once("openrouter", "sk-or-test") == (
            connection_state.STATE_OK,
            "authorized",
            200,
        )

    sent = get.call_args.kwargs["headers"]
    _assert_vool_headers(sent)
    assert sent["Authorization"] == "Bearer sk-or-test"
