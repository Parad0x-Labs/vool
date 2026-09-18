"""SWITCHBOARD Repair 4: Ollama's native-tools path must fail explicitly, at the adapter
boundary, when a turn required a tool call and none arrived -- `tool_calls: []`, no `tool_calls`
key, or ordinary prose instead of a call.

Before the repair: `_extract_ollama_tool_calls` (by design, correctly) never raises on an
absent/empty `tool_calls` array -- an ordinary conversational turn with no tools attached must
still get an answer. But `_invoke_ollama_chat` reused that same silence for `tools_required=True`
turns too, so a required call that never arrived became a "successful" ModelResponse(text=...,
error=None) -- differing from the OpenAI-compatible cloud lane's native path, which already fails
closed with "required native tool call is missing" in the equivalent shape
(_extract_native_openai_tool_result).

Uses a REAL local HTTP server (not `unittest.mock.patch` on `requests.post`), per the operator's
explicit instruction, so the actual adapter code -- `requests.post`, `response.json()`, extraction
-- all run for real.
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
from core.cloud_tool_call_contract import MalformedToolArgumentsError, build_cloud_tool_definitions
from core.normalized_provider_result import ProviderErrorClass, classify_error_class


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    body: bytes = b"{}"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        cls = type(self)
        body = cls.body
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
    _FixtureHandler.body = b"{}"


def _set(payload: dict) -> None:
    _FixtureHandler.body = json.dumps(payload).encode("utf-8")


def _adapter(port: int, *, supports_json_mode: bool = False) -> OpenAICompatibleAdapter:
    runtime_config = {"base_url": f"http://127.0.0.1:{port}", "timeout_seconds": 5.0}
    if supports_json_mode:
        runtime_config["supports_json_mode"] = True
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="fixture-ollama",
            provider_id="fixture-ollama:model",
            model_name="model",
            metadata={"runtime_family": "ollama"},
            runtime_config=runtime_config,
        )
    )


TOOLS = build_cloud_tool_definitions(
    [{"intent": "sandbox.run_command", "description": "Run a bounded command.", "arguments": {"command": "string"}}]
)
NATIVE_NAME = TOOLS[0].name


def _tools_required_request() -> ModelRequest:
    return ModelRequest(
        task_kind="tool_intent",
        prompt="run a command",
        output_mode="tool_intent",
        messages=[{"role": "user", "content": "run a command"}],
        tools=TOOLS,
        tool_choice="required",
        tools_required=True,
    )


# --- failure shapes --------------------------------------------------------------------------


def test_empty_tool_calls_array_fails_closed(fixture_server) -> None:
    _set({"message": {"content": "", "tool_calls": []}})
    with runtime_flags.override("ollama_native_tools", True):
        error = None
        try:
            _adapter(fixture_server).run_structured_task(_tools_required_request())
        except Exception as exc:
            error = exc
    assert isinstance(error, MalformedToolArgumentsError), f"got {type(error).__name__}: {error}"
    assert classify_error_class(error) == ProviderErrorClass.MALFORMED_TOOL_CALL


def test_no_tool_calls_key_and_empty_content_fails_closed(fixture_server) -> None:
    _set({"message": {"content": ""}})
    with runtime_flags.override("ollama_native_tools", True):
        error = None
        try:
            _adapter(fixture_server).run_structured_task(_tools_required_request())
        except Exception as exc:
            error = exc
    assert isinstance(error, MalformedToolArgumentsError), f"got {type(error).__name__}: {error}"


def test_ordinary_prose_fails_closed_when_tools_required(fixture_server) -> None:
    """The defect this repair specifically names: the model chatted instead of calling. Empty
    text is not the only failure shape -- non-empty prose that is not a valid call must fail too,
    matching the cloud native lane's `_tool_call_text_from_content` fallback, which also refuses
    ordinary prose it cannot parse as a call."""
    _set({"message": {"content": "Sure, I can help with that -- what would you like to run?"}})
    with runtime_flags.override("ollama_native_tools", True):
        error = None
        try:
            _adapter(fixture_server).run_structured_task(_tools_required_request())
        except Exception as exc:
            error = exc
    assert isinstance(error, MalformedToolArgumentsError), f"got {type(error).__name__}: {error}"
    assert classify_error_class(error) == ProviderErrorClass.MALFORMED_TOOL_CALL


# --- control cases: must still succeed ---------------------------------------------------------


def test_a_valid_native_tool_call_still_succeeds(fixture_server) -> None:
    _set(
        {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": NATIVE_NAME, "arguments": {"command": "pwd"}}}],
            }
        }
    )
    with runtime_flags.override("ollama_native_tools", True):
        response = _adapter(fixture_server).run_structured_task(_tools_required_request())
    assert response.tool_calls[0].intent == "sandbox.run_command"


def test_ordinary_prose_still_succeeds_when_tools_are_not_required(fixture_server) -> None:
    """Control: an ordinary conversational turn that never asked for a required tool call must be
    unaffected -- the flag this repair adds is gated on `tools_required`, not on tools merely
    being present."""
    _set({"message": {"content": "The answer is 42."}})
    request = ModelRequest(
        task_kind="chat",
        prompt="what is the answer",
        output_mode="plain_text",
        messages=[{"role": "user", "content": "what is the answer"}],
        tools=TOOLS,
    )
    with runtime_flags.override("ollama_native_tools", True):
        response = _adapter(fixture_server).run_text_task(request)
    assert response.output_text == "The answer is 42."
    assert response.error is None


def test_ordinary_prose_still_succeeds_when_native_tools_flag_is_off(fixture_server) -> None:
    """Control: with the kill switch off, `native_tools` is always empty, so the new check never
    fires -- tools_required alone must not be enough to trip it. `supports_json_mode` is declared
    so the pre-invocation `assert_envelope_carries_tools` gate (a different, already-accepted
    check) is satisfied by the structured-output fallback and this test reaches the code actually
    under test here, not that earlier gate."""
    _set({"message": {"content": "The answer is 42."}})
    with runtime_flags.override("ollama_native_tools", False):
        response = _adapter(fixture_server, supports_json_mode=True).run_structured_task(_tools_required_request())
    assert response.output_text == "The answer is 42."
    assert response.error is None
