"""SWITCHBOARD final micro-repair N2: a response shaped like `{"choices": [{}]}` -- a choice
entry present, but with no `message` object at all (so no textual content is even structurally
possible) -- raises adapters.openai_compatible_adapter._extract_openai_text's own bare
RuntimeError("OpenAI-compatible response did not include textual content."), but
classify_error_class returned None for it: previously unclassified.

Fix: a precise marker, "did not include textual content" -> MALFORMED_PROVIDER_RESPONSE, added
before the broader "malformed provider response" marker.

Required distinctions, all preserved:
  structurally malformed              -> MALFORMED_PROVIDER_RESPONSE  (this repair)
  structurally valid but no content   -> EMPTY_PROVIDER_RESPONSE      (SWITCHBOARD Repair 3, untouched)
  required native call absent         -> MALFORMED_TOOL_CALL          (SWITCHBOARD Repair 3, untouched)

Uses a REAL local HTTP server (not `unittest.mock.patch` on `requests.post`), per the operator's
standing instruction, so the actual adapter code -- requests.post, response.json(), extraction --
runs for real.
"""
from __future__ import annotations

import http.server
import json
import threading
from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter, _extract_openai_text
from core.normalized_provider_result import ProviderErrorClass, classify_error_class


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    body: bytes = b"{}"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        cls = type(self)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cls.body)))
        self.end_headers()
        self.wfile.write(cls.body)


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


def _adapter(port: int) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="fixture-cloud",
            provider_id="fixture-cloud:model",
            model_name="model",
            metadata={"runtime_family": "openai-compatible"},
            runtime_config={"base_url": f"http://127.0.0.1:{port}", "api_path": "/v1/chat/completions", "timeout_seconds": 5.0},
        )
    )


def _plain_request() -> ModelRequest:
    return ModelRequest(task_kind="chat", prompt="hello", output_mode="plain_text", messages=[{"role": "user", "content": "hello"}])


# --- unit level: the extraction function + classification directly -----------------------------


def test_choice_entry_missing_message_raises_and_classifies_malformed() -> None:
    with pytest.raises(RuntimeError, match="did not include textual content"):
        _extract_openai_text({"choices": [{}]})
    error = None
    try:
        _extract_openai_text({"choices": [{}]})
    except Exception as exc:
        error = exc
    assert classify_error_class(error) == ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE


def test_message_missing_content_key_raises_and_classifies_malformed() -> None:
    error = None
    try:
        _extract_openai_text({"choices": [{"message": {}}]})
    except Exception as exc:
        error = exc
    assert error is not None
    assert classify_error_class(error) == ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE


def test_content_of_the_wrong_type_raises_and_classifies_malformed() -> None:
    """A structurally malformed choice: `content` present but neither a string nor a list."""
    error = None
    try:
        _extract_openai_text({"choices": [{"message": {"content": 42}}]})
    except Exception as exc:
        error = exc
    assert error is not None
    assert classify_error_class(error) == ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE


def test_is_retryable_is_false_for_the_new_malformed_class() -> None:
    from core.normalized_provider_result import is_retryable

    error_class = classify_error_class(RuntimeError("OpenAI-compatible response did not include textual content."))
    assert error_class == ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE
    assert is_retryable(error_class) is False


# --- preserved distinctions: must NOT be reclassified by the new marker -------------------------


def test_empty_but_structurally_valid_content_still_classifies_as_empty_not_malformed(fixture_server) -> None:
    """Content `""` is a syntactically valid, if unhelpful, string -- _extract_openai_text returns
    it successfully. The SWITCHBOARD Repair 3 empty-response check downstream in
    _invoke_openai_compatible is what then refuses it, with its own distinct
    "no usable text and no tool call" message -> EMPTY_PROVIDER_RESPONSE, untouched by this repair."""
    _set({"choices": [{"message": {"content": ""}}]})
    error = None
    try:
        _adapter(fixture_server).run_text_task(_plain_request())
    except Exception as exc:
        error = exc
    assert error is not None
    assert classify_error_class(error) == ProviderErrorClass.EMPTY_PROVIDER_RESPONSE


def test_required_native_call_absent_still_classifies_as_malformed_tool_call() -> None:
    """A separate, pre-existing bare RuntimeError from _extract_native_openai_tool_result --
    "required native tool call is missing" -- must keep classifying MALFORMED_TOOL_CALL, not get
    swept into the new, differently-worded MALFORMED_PROVIDER_RESPONSE marker."""
    error_class = classify_error_class(RuntimeError("required native tool call is missing"))
    assert error_class == ProviderErrorClass.MALFORMED_TOOL_CALL


# --- controls: ordinary success paths are unaffected --------------------------------------------


def test_valid_ordinary_text_still_succeeds(fixture_server) -> None:
    _set({"choices": [{"message": {"content": "The answer is 42."}}]})
    response = _adapter(fixture_server).run_text_task(_plain_request())
    assert response.output_text == "The answer is 42."
    assert response.error is None


def test_valid_native_call_with_no_text_still_succeeds(fixture_server) -> None:
    """content=None alongside a VALID tool_calls array is not a malformed structure -- the tool
    call IS the content."""
    from core.cloud_tool_call_contract import build_cloud_tool_definitions

    tools = build_cloud_tool_definitions(
        [{"intent": "sandbox.run_command", "description": "Run a bounded command.", "arguments": {"command": "string"}}]
    )
    native_name = tools[0].name
    _set(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": native_name, "arguments": '{"command":"pwd"}'}}],
                    }
                }
            ]
        }
    )
    response = _adapter(fixture_server).run_structured_task(
        ModelRequest(
            task_kind="tool_intent", prompt="run a command", output_mode="tool_intent",
            messages=[{"role": "user", "content": "run a command"}], tools=tools, tool_choice="required", tools_required=True,
        )
    )
    assert response.tool_calls[0].intent == "sandbox.run_command"
