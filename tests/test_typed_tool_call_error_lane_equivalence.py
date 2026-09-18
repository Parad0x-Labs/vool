"""SWITCHBOARD Repair 2: the SAME malformed native tool call must classify IDENTICALLY on the
OpenAI-compatible (System A cloud) lane and the Ollama (System A local) lane -- both are served by
the exact same `OpenAICompatibleAdapter` class, dispatching on `manifest.metadata.runtime_family`.

Before the repair: `_invoke_openai_compatible` caught the shared `ValueError` base class before
`ToolCallParseError`, so an UnknownToolNameError/MalformedToolArgumentsError/DuplicateToolCallError
raised by `parse_native_tool_calls` got flattened into a bare `RuntimeError("malformed provider
response: ...")` on the cloud lane -- classifying MALFORMED_PROVIDER_RESPONSE (the wire format was
fine) instead of MALFORMED_TOOL_CALL (the MODEL's call was bad), while the identical scenario on
Ollama (`_extract_ollama_tool_calls` raises the typed subclass directly, nothing wraps it) correctly
classified MALFORMED_TOOL_CALL.

Uses a REAL local HTTP server (not `unittest.mock.patch` on `requests.post`) so both production
adapter code paths -- including the actual `requests.post` call and JSON deserialization -- run for
real, per the operator's explicit instruction not to depend on internal mocking for this proof.
"""
from __future__ import annotations

import http.server
import json
import threading
from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.cloud_tool_call_contract import (
    DuplicateToolCallError,
    MalformedToolArgumentsError,
    UnknownToolNameError,
    build_cloud_tool_definitions,
)
from core.normalized_provider_result import ProviderErrorClass, classify_error_class


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    """Serves a per-test-configured malformed tool-call reply, OpenAI-shaped on
    /v1/chat/completions and Ollama-shaped on /api/chat -- the SAME logical scenario in each
    provider's own wire format."""

    payload_openai: dict = {}
    payload_ollama: dict = {}

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path.startswith("/api/chat"):
            body = json.dumps(self._FixtureHandler_payload_ollama()).encode("utf-8")
        else:
            body = json.dumps(self._FixtureHandler_payload_openai()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _FixtureHandler_payload_openai(self):
        return type(self).payload_openai

    def _FixtureHandler_payload_ollama(self):
        return type(self).payload_ollama


@pytest.fixture()
def fixture_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    thread.join(timeout=5)


TOOLS = build_cloud_tool_definitions(
    [{"intent": "sandbox.run_command", "description": "Run a bounded command.", "arguments": {"command": "string"}}]
)
NATIVE_NAME = TOOLS[0].name


def _cloud_adapter(port: int) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="fixture-cloud", provider_id="fixture-cloud:model", model_name="model",
            metadata={"runtime_family": "openai-compatible"},
            runtime_config={"base_url": f"http://127.0.0.1:{port}", "api_path": "/v1/chat/completions", "timeout_seconds": 5.0},
        )
    )


def _ollama_adapter(port: int) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="fixture-ollama", provider_id="fixture-ollama:model", model_name="model",
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": f"http://127.0.0.1:{port}", "timeout_seconds": 5.0},
        )
    )


def _tool_request() -> ModelRequest:
    return ModelRequest(
        task_kind="tool_intent", prompt="run a command", output_mode="tool_intent",
        messages=[{"role": "user", "content": "run a command"}],
        tools=TOOLS, tool_choice="required", tools_required=True,
    )


def test_malformed_json_arguments_classify_identically_on_both_lanes(fixture_server) -> None:
    port = fixture_server
    _FixtureHandler.payload_openai = {
        "choices": [{"message": {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": NATIVE_NAME, "arguments": "{not valid json"}}]}}]
    }
    _FixtureHandler.payload_ollama = {
        "message": {"content": None, "tool_calls": [{"id": "c1", "function": {"name": NATIVE_NAME, "arguments": "{not valid json"}}]}
    }

    cloud_exc = None
    try:
        _cloud_adapter(port).run_structured_task(_tool_request())
    except Exception as exc:
        cloud_exc = exc

    ollama_exc = None
    try:
        _ollama_adapter(port).run_structured_task(_tool_request())
    except Exception as exc:
        ollama_exc = exc

    assert isinstance(cloud_exc, MalformedToolArgumentsError), f"cloud lane raised {type(cloud_exc).__name__}: {cloud_exc}"
    assert isinstance(ollama_exc, MalformedToolArgumentsError), f"ollama lane raised {type(ollama_exc).__name__}: {ollama_exc}"
    assert classify_error_class(cloud_exc) == ProviderErrorClass.MALFORMED_TOOL_CALL
    assert classify_error_class(ollama_exc) == ProviderErrorClass.MALFORMED_TOOL_CALL
    assert classify_error_class(cloud_exc) == classify_error_class(ollama_exc)


def test_unknown_tool_name_classifies_identically_on_both_lanes(fixture_server) -> None:
    port = fixture_server
    _FixtureHandler.payload_openai = {
        "choices": [{"message": {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "evil__exfiltrate", "arguments": "{}"}}]}}]
    }
    _FixtureHandler.payload_ollama = {
        "message": {"content": None, "tool_calls": [{"id": "c1", "function": {"name": "evil__exfiltrate", "arguments": {}}}]}
    }

    cloud_exc = None
    try:
        _cloud_adapter(port).run_structured_task(_tool_request())
    except Exception as exc:
        cloud_exc = exc

    ollama_exc = None
    try:
        _ollama_adapter(port).run_structured_task(_tool_request())
    except Exception as exc:
        ollama_exc = exc

    assert isinstance(cloud_exc, UnknownToolNameError), f"cloud lane raised {type(cloud_exc).__name__}: {cloud_exc}"
    assert isinstance(ollama_exc, UnknownToolNameError), f"ollama lane raised {type(ollama_exc).__name__}: {ollama_exc}"
    assert classify_error_class(cloud_exc) == classify_error_class(ollama_exc) == ProviderErrorClass.MALFORMED_TOOL_CALL


def test_duplicate_call_id_classifies_identically_on_both_lanes(fixture_server) -> None:
    port = fixture_server
    dup_openai = [
        {"id": "same-id", "type": "function", "function": {"name": NATIVE_NAME, "arguments": '{"command":"pwd"}'}},
        {"id": "same-id", "type": "function", "function": {"name": NATIVE_NAME, "arguments": '{"command":"ls"}'}},
    ]
    dup_ollama = [
        {"id": "same-id", "function": {"name": NATIVE_NAME, "arguments": {"command": "pwd"}}},
        {"id": "same-id", "function": {"name": NATIVE_NAME, "arguments": {"command": "ls"}}},
    ]
    _FixtureHandler.payload_openai = {"choices": [{"message": {"content": None, "tool_calls": dup_openai}}]}
    _FixtureHandler.payload_ollama = {"message": {"content": None, "tool_calls": dup_ollama}}

    cloud_exc = None
    try:
        _cloud_adapter(port).run_structured_task(_tool_request())
    except Exception as exc:
        cloud_exc = exc

    ollama_exc = None
    try:
        _ollama_adapter(port).run_structured_task(_tool_request())
    except Exception as exc:
        ollama_exc = exc

    assert isinstance(cloud_exc, DuplicateToolCallError), f"cloud lane raised {type(cloud_exc).__name__}: {cloud_exc}"
    assert isinstance(ollama_exc, DuplicateToolCallError), f"ollama lane raised {type(ollama_exc).__name__}: {ollama_exc}"
    assert classify_error_class(cloud_exc) == classify_error_class(ollama_exc) == ProviderErrorClass.MALFORMED_TOOL_CALL


def test_a_valid_native_call_still_succeeds_on_the_cloud_lane(fixture_server) -> None:
    """Control: the repair must not have broken the ordinary success path."""
    port = fixture_server
    _FixtureHandler.payload_openai = {
        "choices": [{"message": {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": NATIVE_NAME, "arguments": '{"command":"pwd"}'}}]}}]
    }
    response = _cloud_adapter(port).run_structured_task(_tool_request())
    assert response.tool_calls[0].intent == "sandbox.run_command"
