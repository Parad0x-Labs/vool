"""P0 tool-call recovery at the adapter boundary, on both local lanes, over real HTTP.

The live failure this fixes (gold/ETH request, local Qwen): the model expressed a correct tool
intent but emitted it as a text dialect (or as a native envelope with one syntax defect), the
Ollama lane had no content fallback at all (`_invoke_ollama_chat` raised "required native tool
call is missing"), and the turn ended with the intent silently gone. The OpenAI-compatible lane
recovered bare-JSON prose only — Qwen XML `arg_key`/`arg_value` and Gemma ```tool_code``` fences
were unreachable on every lane.

Uses a REAL local HTTP server (not `unittest.mock.patch`), matching
tests/test_ollama_tools_required_repair.py, so `requests.post`, JSON deserialization, extraction
and recovery all run for real.
"""
from __future__ import annotations

import http.server
import json
import threading
from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import runtime_flags
from core.cloud_tool_call_contract import (
    MalformedToolArgumentsError,
    ToolCallParseError,
    build_cloud_tool_definitions,
)
from core.normalized_provider_result import ProviderErrorClass, classify_error_class


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    payload_openai: dict = {}
    payload_ollama: dict = {}

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        cls = type(self)
        payload = cls.payload_ollama if self.path.startswith("/api/chat") else cls.payload_openai
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def fixture_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    thread.join(timeout=5)
    _FixtureHandler.payload_openai = {}
    _FixtureHandler.payload_ollama = {}


TOOLS = build_cloud_tool_definitions(
    [
        {
            "intent": "web.live_value",
            "description": "Look up a live market value.",
            "arguments": {"query": "string"},
        }
    ]
)
NATIVE_NAME = TOOLS[0].name


def _ollama_adapter(port: int) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="fixture-ollama",
            provider_id="fixture-ollama:model",
            model_name="model",
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": f"http://127.0.0.1:{port}", "timeout_seconds": 5.0},
        )
    )


def _cloud_adapter(port: int) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="fixture-cloud",
            provider_id="fixture-cloud:model",
            model_name="model",
            metadata={"runtime_family": "openai-compatible", "tool_support": ["tool_calls"]},
            runtime_config={
                "base_url": f"http://127.0.0.1:{port}",
                "api_path": "/v1/chat/completions",
                "timeout_seconds": 5.0,
            },
        )
    )


def _request() -> ModelRequest:
    return ModelRequest(
        task_kind="tool_intent",
        prompt="what is the price of gold and eth",
        output_mode="tool_intent",
        messages=[{"role": "user", "content": "what is the price of gold and eth"}],
        tools=TOOLS,
        tool_choice="required",
        tools_required=True,
    )


def _run_ollama(port: int, request: ModelRequest):
    with runtime_flags.override("ollama_native_tools", True):
        return _ollama_adapter(port).run_structured_task(request)


# --- the abandonment defect: text-dialect intent must be recovered, on the Ollama lane --------


def test_qwen_xml_call_in_content_is_recovered(fixture_server) -> None:
    _FixtureHandler.payload_ollama = {
        "message": {
            "content": (
                "</think><tool_call>web.live_value"
                "<arg_key>query</arg_key><arg_value>gold price ETH price</arg_value></tool_call>"
            )
        }
    }
    request = _request()
    response = _run_ollama(fixture_server, request)
    assert response.tool_calls, "the expressed intent must not disappear"
    assert len(response.tool_calls) == 1, "one expressed call must resolve to exactly one execution"
    assert response.tool_calls[0].intent == "web.live_value"
    assert response.tool_calls[0].arguments == {"query": "gold price ETH price"}
    payload = json.loads(response.output_text)
    assert payload["intent"] == "web.live_value"
    resolution = request.metadata.get("tool_call_resolution") or {}
    assert resolution.get("state") == "parsed"


def test_hermes_json_call_in_content_is_recovered(fixture_server) -> None:
    _FixtureHandler.payload_ollama = {
        "message": {
            "content": '<tool_call>{"name": "web.live_value", "arguments": {"query": "ETH in USD"}}</tool_call>'
        }
    }
    response = _run_ollama(fixture_server, _request())
    assert response.tool_calls
    assert response.tool_calls[0].arguments == {"query": "ETH in USD"}


def test_gemma_tool_code_fence_in_content_is_recovered(fixture_server) -> None:
    _FixtureHandler.payload_ollama = {
        "message": {"content": '```tool_code\nweb.live_value(query="gold price in USD")\n```'}
    }
    response = _run_ollama(fixture_server, _request())
    assert response.tool_calls
    assert response.tool_calls[0].arguments == {"query": "gold price in USD"}


def test_parallel_text_calls_are_both_recovered(fixture_server) -> None:
    _FixtureHandler.payload_ollama = {
        "message": {
            "content": (
                '<tool_call>{"name": "web.live_value", "arguments": {"query": "gold price"}}</tool_call>'
                '<tool_call>{"name": "web.live_value", "arguments": {"query": "ETH price"}}</tool_call>'
            )
        }
    }
    response = _run_ollama(fixture_server, _request())
    assert [c.arguments["query"] for c in response.tool_calls] == ["gold price", "ETH price"]


def test_malformed_native_arguments_are_repaired_once(fixture_server) -> None:
    _FixtureHandler.payload_ollama = {
        "message": {
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": NATIVE_NAME, "arguments": '{"query": "gold price",}'}}
            ],
        }
    }
    request = _request()
    response = _run_ollama(fixture_server, request)
    assert response.tool_calls
    assert response.tool_calls[0].arguments == {"query": "gold price"}
    assert response.tool_calls[0].call_id == "call_1"
    resolution = request.metadata.get("tool_call_resolution") or {}
    assert resolution.get("state") == "repaired"


def test_recovered_call_ids_are_stable_across_identical_replies(fixture_server) -> None:
    _FixtureHandler.payload_ollama = {
        "message": {
            "content": '<tool_call>{"name": "web.live_value", "arguments": {"query": "gold"}}</tool_call>'
        }
    }
    first = _run_ollama(fixture_server, _request())
    second = _run_ollama(fixture_server, _request())
    assert first.tool_calls[0].call_id
    assert first.tool_calls[0].call_id == second.tool_calls[0].call_id


# --- honest typed failure: never SUCCEEDED, never fabricated ----------------------------------


def test_unrecoverable_content_still_fails_closed_and_typed(fixture_server) -> None:
    _FixtureHandler.payload_ollama = {
        "message": {"content": "I would need to look that up but cannot right now."}
    }
    request = _request()
    error = None
    try:
        _run_ollama(fixture_server, request)
    except Exception as exc:
        error = exc
    assert isinstance(error, ToolCallParseError), f"got {type(error).__name__}: {error}"
    assert classify_error_class(error) == ProviderErrorClass.MALFORMED_TOOL_CALL
    resolution = request.metadata.get("tool_call_resolution") or {}
    assert resolution.get("state") in {"none", "rejected"}


def test_missing_name_rejection_is_typed_not_silent(fixture_server) -> None:
    _FixtureHandler.payload_ollama = {
        "message": {"content": '<tool_call>{"arguments": {"query": "gold"}}</tool_call>'}
    }
    request = _request()
    error = None
    try:
        _run_ollama(fixture_server, request)
    except Exception as exc:
        error = exc
    assert isinstance(error, ToolCallParseError)
    resolution = request.metadata.get("tool_call_resolution") or {}
    assert resolution.get("state") == "rejected"
    assert resolution.get("rejection_kind") == "missing_name"


def test_a_fabricated_tool_result_is_refused_not_executed(fixture_server) -> None:
    _FixtureHandler.payload_ollama = {
        "message": {
            "content": (
                '<function_results>{"gold": "$3,412/oz", "eth": "$4,105"}</function_results>\n'
                "Based on the tool output above, gold is $3,412/oz and ETH is $4,105."
            )
        }
    }
    error = None
    response = None
    try:
        response = _run_ollama(fixture_server, _request())
    except Exception as exc:
        error = exc
    assert error is not None, (
        f"a narrated fake tool result must not become a successful tool-backed answer; "
        f"got response tool_calls={getattr(response, 'tool_calls', None)!r} "
        f"text={getattr(response, 'output_text', '')[:80]!r}"
    )
    assert isinstance(error, ToolCallParseError)


def test_truncated_arguments_are_not_completed(fixture_server) -> None:
    _FixtureHandler.payload_ollama = {
        "message": {"content": '<tool_call>{"name": "web.live_value", "arguments": {"query": "gol'}
    }
    error = None
    try:
        _run_ollama(fixture_server, _request())
    except Exception as exc:
        error = exc
    assert isinstance(error, ToolCallParseError)


# --- the same recovery on the OpenAI-compatible native lane -----------------------------------


def test_cloud_lane_recovers_qwen_xml_content(fixture_server) -> None:
    _FixtureHandler.payload_openai = {
        "choices": [
            {
                "message": {
                    "content": (
                        "<tool_call>web.live_value"
                        "<arg_key>query</arg_key><arg_value>gold price</arg_value></tool_call>"
                    )
                }
            }
        ]
    }
    response = _cloud_adapter(fixture_server).run_structured_task(_request())
    payload = json.loads(response.output_text)
    assert payload["intent"] == "web.live_value"
    assert payload["arguments"] == {"query": "gold price"}


def test_cloud_lane_repairs_trailing_comma_prose_call(fixture_server) -> None:
    _FixtureHandler.payload_openai = {
        "choices": [
            {"message": {"content": '{"name": "web.live_value", "arguments": {"query": "ETH price",}}'}}
        ]
    }
    response = _cloud_adapter(fixture_server).run_structured_task(_request())
    payload = json.loads(response.output_text)
    assert payload["arguments"] == {"query": "ETH price"}


def test_cloud_lane_unrecoverable_still_raises_typed(fixture_server) -> None:
    _FixtureHandler.payload_openai = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": NATIVE_NAME, "arguments": "{not valid json"}}
                    ],
                }
            }
        ]
    }
    error = None
    try:
        _cloud_adapter(fixture_server).run_structured_task(_request())
    except Exception as exc:
        error = exc
    assert isinstance(error, MalformedToolArgumentsError)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
