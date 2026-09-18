from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from adapters.cloud_provider_common import CloudProviderRequestError
from adapters.cloudflare_workers_ai_provider import CloudflareWorkersAIProvider
from adapters.generic_openai_cloud_provider import GenericOpenAICloudProvider
from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
from core.cloud_provider_contract import CloudModelRequest, PricingState, ProviderErrorKind
from core.cloud_provider_registry import CloudProviderRegistry, DeclarativeOpenAIProvider
from core.cloud_tool_call_contract import build_cloud_tool_definitions


class _Transport:
    def __init__(self, responses: dict[str, tuple[int, dict[str, str], Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        for suffix, response in self.responses.items():
            if str(kwargs["url"]).endswith(suffix):
                return response
        raise AssertionError(f"unexpected URL: {kwargs['url']}")


def _request(model_id: str) -> CloudModelRequest:
    return CloudModelRequest(
        task_id="task",
        turn_id="turn",
        subtask_id="sub",
        model_call_id="call",
        model_id=model_id,
        messages=({"role": "user", "content": "hello"},),
        max_output_tokens=50,
    )


def test_openrouter_discovers_verified_free_and_unknown_pricing() -> None:
    """The per-token pair decides; an absent per-request fee does not make a model unreadable.

    OpenRouter publishes `pricing.request` for no model in the live catalog, so the second entry
    here is the SHAPE every real model arrives in. Reading it as unknown classed all 338 live
    models unknown and no free route could be selected. An unpublished per-token rate is the case
    that is genuinely unreadable, and it still is.
    """
    transport = _Transport(
        {
            "/models": (
                200,
                {},
                {
                    "data": [
                        {
                            "id": "free-model",
                            "pricing": {"prompt": "0", "completion": "0", "request": "0"},
                            "context_length": 8192,
                            "supported_parameters": ["tools", "response_format"],
                        },
                        {"id": "live-shape-free-model", "pricing": {"prompt": "0", "completion": "0"}},
                        {"id": "unknown-model", "pricing": {"completion": "0"}},
                    ]
                },
            )
        }
    )
    models = OpenRouterCloudProvider().discover_models(transport)
    assert models[0].pricing_state == PricingState.FREE
    assert models[0].verified_zero_price
    assert {"tool_calling", "structured_output"}.issubset(models[0].capabilities)
    assert models[1].pricing_state == PricingState.FREE
    assert models[1].verified_zero_price
    assert models[2].pricing_state == PricingState.UNKNOWN
    assert not models[2].verified_zero_price


def test_generic_provider_never_assumes_undisclosed_pricing_is_free() -> None:
    provider = GenericOpenAICloudProvider(
        provider_id="custom", base_url="https://models.example.com/v1", credential_name="custom.key"
    )
    transport = _Transport({"/models": (200, {}, {"data": [{"id": "model-a"}]})})
    model = provider.discover_models(transport)[0]
    assert model.pricing_state == PricingState.UNKNOWN
    assert provider.estimate_request_cost(model, input_tokens=10, output_tokens=10) is None


def test_cloudflare_discovery_and_request_parsing() -> None:
    provider = CloudflareWorkersAIProvider(account_id="acct-123")
    transport = _Transport(
        {
            "/models/search": (200, {}, {"result": [{"name": "@cf/model", "context_length": 4096}]}),
            "/run/@cf/model": (200, {}, {"result": {"response": "done", "usage": {"input_tokens": 3}}}),
        }
    )
    model = provider.discover_models(transport)[0]
    assert model.model_id == "@cf/model"
    assert model.pricing_state == PricingState.UNKNOWN
    response = provider.send_request(transport, _request(model.model_id))
    assert response.output_text == "done"
    assert response.usage["input_tokens"] == 3


def _tool_required_request(model_id: str) -> CloudModelRequest:
    tools = build_cloud_tool_definitions(
        [{"intent": "workspace.read_file", "description": "Read a file.", "arguments": {"path": "string"}}]
    )
    return replace(_request(model_id), tools=tools, tool_choice="required")


def test_cloudflare_fails_closed_when_tools_are_required() -> None:
    """This adapter's request body never carries `tools` and its parsing never reads `tool_calls`.

    Silently sending the tool-required turn anyway would let a turn that needed a real tool call
    come back as unlabeled prose — indistinguishable from a genuine, ungrounded answer. It must
    refuse before the network call, not after.
    """
    provider = CloudflareWorkersAIProvider(account_id="acct-123")
    transport = _Transport({"/run/tool-model": (200, {}, {"result": {"response": "ignored"}})})

    with pytest.raises(RuntimeError, match="does not support required tools"):
        provider.send_request(transport, _tool_required_request("tool-model"))

    assert transport.calls == []  # no toolless request was silently sent
    error = provider.classify_provider_error(RuntimeError("cloudflare-workers-ai does not support required tools"))
    assert error.kind == ProviderErrorKind.TOOL_UNSUPPORTED


def test_generic_provider_fails_closed_when_tools_are_required() -> None:
    provider = GenericOpenAICloudProvider(
        provider_id="custom", base_url="https://models.example.com/v1", credential_name="custom.key"
    )
    transport = _Transport({"/chat/completions": (200, {}, {"choices": [{"message": {"content": "ignored"}}]})})

    with pytest.raises(RuntimeError, match="does not support required tools"):
        provider.send_request(transport, _tool_required_request("tool-model"))

    assert transport.calls == []
    error = provider.classify_provider_error(RuntimeError("custom does not support required tools"))
    assert error.kind == ProviderErrorKind.TOOL_UNSUPPORTED


def test_cloudflare_and_generic_still_serve_toolless_requests_normally() -> None:
    """Control: a turn that does NOT require tools must not be affected by the new gate."""
    cloudflare = CloudflareWorkersAIProvider(account_id="acct-123")
    cloudflare_transport = _Transport({"/run/plain-model": (200, {}, {"result": {"response": "ok"}})})
    assert cloudflare.send_request(cloudflare_transport, _request("plain-model")).output_text == "ok"

    generic = GenericOpenAICloudProvider(
        provider_id="custom", base_url="https://models.example.com/v1", credential_name="custom.key"
    )
    generic_transport = _Transport({"/chat/completions": (200, {}, {"choices": [{"message": {"content": "ok"}}]})})
    assert generic.send_request(generic_transport, _request("plain-model")).output_text == "ok"


@pytest.mark.parametrize(
    ("payload"),
    [
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {}}]},
        {},
    ],
)
def test_openrouter_fails_closed_on_empty_http_200(payload) -> None:
    """An HTTP 200 with no usable content must not read as a normal, empty-but-valid answer.

    Before this fix, an empty/absent `choices` array silently became `CloudModelResponse("", ...)`
    -- indistinguishable from "the model genuinely said nothing" and passed contract validation
    with no error at all.
    """
    provider = OpenRouterCloudProvider()
    transport = _Transport({"/chat/completions": (200, {}, payload)})

    with pytest.raises(RuntimeError, match="no content in any choice"):
        provider.send_request(transport, _request("empty-model"))


def test_generic_provider_fails_closed_on_empty_http_200() -> None:
    provider = GenericOpenAICloudProvider(
        provider_id="custom", base_url="https://models.example.com/v1", credential_name="custom.key"
    )
    transport = _Transport({"/chat/completions": (200, {}, {"choices": []})})

    with pytest.raises(RuntimeError, match="no content in any choice"):
        provider.send_request(transport, _request("empty-model"))


def test_cloudflare_fails_closed_on_empty_http_200() -> None:
    provider = CloudflareWorkersAIProvider(account_id="acct-123")
    transport = _Transport({"/run/empty-model": (200, {}, {"result": {"response": ""}})})

    with pytest.raises(RuntimeError, match="no content in result"):
        provider.send_request(transport, _request("empty-model"))


def test_all_three_empty_response_errors_classify_as_malformed() -> None:
    """The failure must be observable as MALFORMED_RESPONSE, not swallowed as UNKNOWN."""
    for provider in (
        OpenRouterCloudProvider(),
        GenericOpenAICloudProvider(provider_id="custom", base_url="https://models.example.com/v1", credential_name="k"),
        CloudflareWorkersAIProvider(account_id="acct-123"),
    ):
        error = provider.classify_provider_error(RuntimeError("malformed provider response: no content in any choice"))
        assert error.kind == ProviderErrorKind.MALFORMED_RESPONSE


def test_openrouter_sends_and_parses_one_native_tool_call() -> None:
    tools = build_cloud_tool_definitions(
        [
            {
                "intent": "sandbox.run_command",
                "description": "Run a bounded command.",
                "arguments": {"command": "string", "cwd": "string optional"},
            }
        ]
    )
    native_name = tools[0].name
    transport = _Transport(
        {
            "/chat/completions": (
                200,
                {},
                {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-native-1",
                                        "type": "function",
                                        "function": {
                                            "name": native_name,
                                            "arguments": '{"command":"pwd","cwd":null}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            )
        }
    )
    response = OpenRouterCloudProvider().send_request(
        transport,
        replace(_request("tool-model"), tools=tools, tool_choice="required"),
    )
    body = transport.calls[-1]["body"]
    assert body["tool_choice"] == "required"
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == native_name
    assert body["tools"][0]["function"]["strict"] is True
    assert response.tool_calls[0].intent == "sandbox.run_command"
    assert response.tool_calls[0].arguments == {"command": "pwd", "cwd": None}
    assert response.output_text == (
        '{"_native_tool_call_id":"call-native-1","arguments":{"command":"pwd","cwd":null},'
        '"intent":"sandbox.run_command"}'
    )


def test_openrouter_accepts_a_valid_batch_but_exposes_only_the_first_step() -> None:
    tools = build_cloud_tool_definitions(
        [
            {
                "intent": "sandbox.run_command",
                "description": "Run a bounded command.",
                "arguments": {"command": "string", "cwd": "string optional"},
            }
        ]
    )
    native_name = tools[0].name
    calls = [
        {
            "id": f"call-native-{index}",
            "type": "function",
            "function": {
                "name": native_name,
                "arguments": json.dumps({"command": command, "cwd": None}),
            },
        }
        for index, command in ((1, "pwd"), (2, "git status --short"))
    ]
    transport = _Transport(
        {
            "/chat/completions": (
                200,
                {},
                {
                    "choices": [{"message": {"content": None, "tool_calls": calls}}],
                    "usage": {},
                },
            )
        }
    )

    response = OpenRouterCloudProvider().send_request(
        transport,
        replace(_request("tool-model"), tools=tools, tool_choice="required"),
    )

    assert [call.arguments["command"] for call in response.tool_calls] == [
        "pwd",
        "git status --short",
    ]
    assert json.loads(response.output_text) == {
        "_native_tool_call_id": "call-native-1",
        "arguments": {"command": "pwd", "cwd": None},
        "intent": "sandbox.run_command",
    }


@pytest.mark.parametrize(
    "message",
    [
        {"content": '{"tool":"bash","arguments":{"command":"pwd"}}'},
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "bad",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command":"pwd"}'},
                }
            ],
        },
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "bad-json",
                    "type": "function",
                    "function": {"name": "sandbox__run_command", "arguments": "{broken"},
                }
            ],
        },
    ],
)
def test_openrouter_never_converts_invalid_tool_syntax_into_chat_text(message) -> None:
    tools = build_cloud_tool_definitions(
        [
            {
                "intent": "sandbox.run_command",
                "description": "Run a bounded command.",
                "arguments": {"command": "string"},
            }
        ]
    )
    transport = _Transport(
        {"/chat/completions": (200, {}, {"choices": [{"message": message}], "usage": {}})}
    )
    with pytest.raises(RuntimeError, match="malformed provider response"):
        OpenRouterCloudProvider().send_request(
            transport,
            replace(_request("tool-model"), tools=tools, tool_choice="required"),
        )


@pytest.mark.parametrize("status_code", [401, 403])
def test_error_classification_is_sanitized_and_bounded(status_code) -> None:
    provider = OpenRouterCloudProvider()
    auth = provider.classify_provider_error(CloudProviderRequestError(status_code, "raw-provider-secret"))
    rate = provider.classify_provider_error(CloudProviderRequestError(429, "rate", retry_after_seconds=2.0))
    timeout = provider.classify_provider_error(TimeoutError("timeout"))
    assert auth.kind == ProviderErrorKind.AUTH and "secret" not in auth.safe_message
    assert rate.kind == ProviderErrorKind.RATE_LIMITED and rate.retry_after_seconds == 2.0
    assert timeout.kind == ProviderErrorKind.TIMEOUT and timeout.billing_ambiguous


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (CloudProviderRequestError(404, "removed"), ProviderErrorKind.MODEL_REMOVED),
        (CloudProviderRequestError(429, "quota exhausted"), ProviderErrorKind.QUOTA_EXHAUSTED),
        (RuntimeError("context token length exceeded"), ProviderErrorKind.CONTEXT_OVERFLOW),
        (RuntimeError("tool unsupported"), ProviderErrorKind.TOOL_UNSUPPORTED),
        (RuntimeError("malformed json"), ProviderErrorKind.MALFORMED_RESPONSE),
        (RuntimeError("network host failed"), ProviderErrorKind.NETWORK),
    ],
)
def test_required_provider_error_taxonomy(error, kind) -> None:
    assert OpenRouterCloudProvider().classify_provider_error(error).kind == kind


def test_registry_accepts_declarative_provider_not_executable_adapter() -> None:
    registry = CloudProviderRegistry()
    registry.add_openrouter()
    custom = registry.add_declarative_openai(
        DeclarativeOpenAIProvider(
            provider_id="custom",
            base_url="https://models.example.com/v1",
            credential_name="custom.key",
        )
    )
    assert registry.get("custom") is custom
    assert [provider.provider_id for provider in registry.list()] == ["custom", "openrouter"]
    with pytest.raises(ValueError, match="HTTPS"):
        registry.add_declarative_openai(
            DeclarativeOpenAIProvider("unsafe", "http://127.0.0.1:8000/v1", "unsafe.key")
        )


def test_registry_has_no_executable_plugin_registration_bypass() -> None:
    registry = CloudProviderRegistry()
    assert not hasattr(registry, "register")
    assert not hasattr(registry, "add_adapter")
    with pytest.raises(ValueError, match="HTTPS"):
        registry.add_declarative_openai(
            DeclarativeOpenAIProvider("loopback", "http://localhost/v1", "loopback.key")
        )
    with pytest.raises(ValueError, match="HTTPS"):
        registry.add_declarative_openai(
            DeclarativeOpenAIProvider("userinfo", "https://token@models.example.com/v1", "userinfo.key")
        )


# ---- finish_reason and reasoning-as-content (2026-09-06 root cause) ---------------------------


def _openrouter_chat_transport(message: dict[str, Any], *, finish_reason: str, usage: dict[str, Any] | None = None) -> _Transport:
    return _Transport(
        {
            "/chat/completions": (
                200,
                {},
                {
                    "choices": [{"message": message, "finish_reason": finish_reason}],
                    "usage": usage or {"prompt_tokens": 10, "completion_tokens": 20},
                },
            )
        }
    )


def test_openrouter_reports_the_choice_finish_reason_on_the_response() -> None:
    transport = _openrouter_chat_transport({"role": "assistant", "content": "The Passat is larger."}, finish_reason="STOP")
    response = OpenRouterCloudProvider().send_request(transport, _request("nvidia/nemotron-3.5-lightning:free"))
    assert response.output_text == "The Passat is larger."
    assert response.finish_reason == "stop"


def test_openrouter_keeps_a_truncated_answer_but_says_it_was_cut_off() -> None:
    transport = _openrouter_chat_transport({"role": "assistant", "content": "The Passat is larger and the"}, finish_reason="length")
    response = OpenRouterCloudProvider().send_request(transport, _request("nvidia/nemotron-3.5-lightning:free"))
    # The adapter translates the wire faithfully; refusing the draft is the router validator's job.
    assert response.output_text == "The Passat is larger and the"
    assert response.finish_reason == "length"


def test_openrouter_content_that_is_the_reasoning_is_refused_as_malformed_not_served_as_an_answer() -> None:
    thinking = "Here's a thinking process: the user wants a comparison of two cars. " * 6
    transport = _openrouter_chat_transport(
        {"role": "assistant", "content": thinking, "reasoning": thinking},
        finish_reason="length",
        usage={"prompt_tokens": 1200, "completion_tokens": 520},
    )
    provider = OpenRouterCloudProvider()
    with pytest.raises(RuntimeError) as excinfo:
        provider.send_request(transport, _request("nvidia/nemotron-3.5-lightning:free"))
    message = str(excinfo.value)
    assert "reasoning-only content" in message
    assert "finish_reason=length" in message and "completion_tokens=520" in message and "max_tokens=50" in message
    assert provider.classify_provider_error(excinfo.value).kind == ProviderErrorKind.MALFORMED_RESPONSE


def test_openrouter_a_cut_off_copy_of_the_reasoning_is_still_the_reasoning() -> None:
    thinking = "Here's a thinking process: the user wants a comparison of two cars. " * 6
    transport = _openrouter_chat_transport(
        {"role": "assistant", "content": thinking[:260], "reasoning": thinking},
        finish_reason="length",
    )
    with pytest.raises(RuntimeError, match="reasoning-only content"):
        OpenRouterCloudProvider().send_request(transport, _request("nvidia/nemotron-3.5-lightning:free"))


def test_openrouter_an_answer_beside_separate_reasoning_is_served_as_the_answer() -> None:
    thinking = "Let me think about production years, size classes and prices. " * 5
    transport = _openrouter_chat_transport(
        {"role": "assistant", "content": "Passat: 1973, midsize. Golf: 1974, compact.", "reasoning": thinking},
        finish_reason="stop",
    )
    response = OpenRouterCloudProvider().send_request(transport, _request("nvidia/nemotron-3.5-lightning:free"))
    assert response.output_text == "Passat: 1973, midsize. Golf: 1974, compact."
    assert response.finish_reason == "stop"


def test_openrouter_a_short_answer_that_merely_opens_like_the_reasoning_is_not_the_reasoning() -> None:
    from adapters.openrouter_cloud_provider import _content_is_the_reasoning

    assert _content_is_the_reasoning("Golf is smaller.", "Golf is smaller. Let me verify the wheelbase figures first...") is False
    assert _content_is_the_reasoning("  same   text ", "same text") is True
