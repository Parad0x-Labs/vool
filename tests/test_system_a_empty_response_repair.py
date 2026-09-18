"""SWITCHBOARD Repair 3: System A (the primary OpenAICompatibleAdapter cloud path) must fail
closed on an HTTP 200 with no usable content, matching the three CloudProviderAdapter classes
which already refuse the equivalent shape.

Before the repair, `{"choices":[{"message":{"content":""}}]}` produced a "successful"
ModelResponse(text="", error=None) -- indistinguishable downstream from a genuine, deliberate
empty answer, and directly upstream of the empty_synthesis failure mode.

Uses a REAL local HTTP server (not requests.post mocking), per the operator's explicit
instruction, so the actual `response.raise_for_status()` / `response.json()` / extraction code all
run for real.
"""
from __future__ import annotations

import http.server
import json
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.cloud_tool_call_contract import MalformedToolArgumentsError, build_cloud_tool_definitions
from core.memory_first_router import MemoryFirstRouter
from core.normalized_provider_result import (
    EmptyProviderResponseError,
    MalformedProviderResponseError,
    ProviderErrorClass,
    classify_error_class,
    is_retryable,
)
from core.turn_model_call_ledger import begin_turn, reset_for_tests, turn_call_accounting


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    body: bytes = b"{}"
    bodies: list[bytes] = []
    request_count: int = 0
    status: int = 200
    content_type: str = "application/json"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        cls = type(self)
        cls.request_count += 1
        body = cls.bodies.pop(0) if cls.bodies else cls.body
        self.send_response(cls.status)
        self.send_header("Content-Type", cls.content_type)
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
    _FixtureHandler.bodies = []
    _FixtureHandler.request_count = 0
    _FixtureHandler.status = 200
    _FixtureHandler.content_type = "application/json"


def _set(payload):
    _FixtureHandler.body = json.dumps(payload).encode("utf-8")


def _set_sequence(*payloads: dict) -> None:
    _FixtureHandler.bodies = [json.dumps(payload).encode("utf-8") for payload in payloads]
    _FixtureHandler.request_count = 0


def _adapter(port: int, *, supports_json_mode: bool = False) -> OpenAICompatibleAdapter:
    runtime_config = {"base_url": f"http://127.0.0.1:{port}", "api_path": "/v1/chat/completions", "timeout_seconds": 5.0}
    if supports_json_mode:
        runtime_config["supports_json_mode"] = True
    manifest = SimpleNamespace(
        provider_name="fixture-cloud", provider_id="fixture-cloud:model", model_name="model",
        adapter_type="openai_compatible",
        metadata={"runtime_family": "openai-compatible"},
        runtime_config=runtime_config,
    )
    # The fixture endpoint is a loopback OpenAI-compatible lane, which is precisely what
    # `core.final_answer_authorship` refuses to let author until it has been probed -- and these
    # tests drive `_invoke_manifest`, where that fence lives. The subject here is empty-response
    # retry, so the probe model is certified the way an operator would certify it.
    from tests._authorship_certification import certify_for_authorship

    certify_for_authorship(manifest)
    return OpenAICompatibleAdapter(manifest)


def _plain_request() -> ModelRequest:
    return ModelRequest(task_kind="chat", prompt="hello", output_mode="plain_text", messages=[{"role": "user", "content": "hello"}])


def _tools_required_request() -> ModelRequest:
    tools = build_cloud_tool_definitions(
        [{"intent": "sandbox.run_command", "description": "Run a bounded command.", "arguments": {"command": "string"}}]
    )
    return ModelRequest(
        task_kind="tool_intent", prompt="run a command", output_mode="tool_intent",
        messages=[{"role": "user", "content": "run a command"}],
        tools=tools, tool_choice="required", tools_required=True,
    )


# --- failure shapes --------------------------------------------------------------------------


def test_empty_choices_fails_closed(fixture_server) -> None:
    _set({"choices": []})
    with pytest.raises(RuntimeError, match="did not include choices"):
        _adapter(fixture_server).run_text_task(_plain_request())


def test_empty_content_fails_closed(fixture_server) -> None:
    _set({"choices": [{"message": {"content": ""}}]})
    with pytest.raises(RuntimeError, match="no usable text and no tool call"):
        _adapter(fixture_server).run_text_task(_plain_request())
    _set({"choices": [{"message": {"content": ""}}]})
    error_class = None
    try:
        _adapter(fixture_server).run_text_task(_plain_request())
    except Exception as exc:
        error_class = classify_error_class(exc)
    assert error_class == ProviderErrorClass.EMPTY_PROVIDER_RESPONSE


def test_whitespace_only_content_fails_closed(fixture_server) -> None:
    _set({"choices": [{"message": {"content": "   \n\t  "}}]})
    with pytest.raises(RuntimeError, match="no usable text and no tool call"):
        _adapter(fixture_server).run_text_task(_plain_request())


def test_missing_message_fails_closed(fixture_server) -> None:
    _set({"choices": [{}]})
    with pytest.raises(MalformedProviderResponseError, match="message object"):
        _adapter(fixture_server).run_text_task(_plain_request())


def test_missing_choices_fails_as_typed_transient_empty(fixture_server) -> None:
    _set({"id": "response-without-candidates"})
    with pytest.raises(EmptyProviderResponseError) as raised:
        _adapter(fixture_server).run_text_task(_plain_request())
    assert classify_error_class(raised.value) == ProviderErrorClass.EMPTY_PROVIDER_RESPONSE


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"choices": {}}, id="mapping_instead_of_list"),
        pytest.param({"choices": "none"}, id="string_instead_of_list"),
        pytest.param({"choices": [7]}, id="scalar_choice"),
        pytest.param({"choices": [{"message": []}]}, id="list_message"),
    ],
)
def test_malformed_nonempty_choice_shapes_are_nontransient(payload, fixture_server) -> None:
    _set(payload)
    with pytest.raises(MalformedProviderResponseError) as raised:
        _adapter(fixture_server).run_text_task(_plain_request())
    error_class = classify_error_class(raised.value)
    assert error_class == ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE
    assert is_retryable(error_class) is False


def test_malformed_json_body_fails_closed_and_classifies_as_malformed(fixture_server) -> None:
    _FixtureHandler.body = b"{not valid json at all"
    error = None
    try:
        _adapter(fixture_server).run_text_task(_plain_request())
    except Exception as exc:
        error = exc
    assert error is not None
    assert classify_error_class(error) == ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE


def test_tools_required_empty_content_classifies_as_malformed_tool_call(fixture_server) -> None:
    """A tools-required turn (native tool support is offered by default -- this response has no
    `tool_calls` key at all and no recoverable prose call) must classify as a tool-call defect,
    not the generic plain-chat empty-response case or an unclassified transport problem."""
    _set({"choices": [{"message": {"content": ""}}]})
    error = None
    try:
        _adapter(fixture_server, supports_json_mode=True).run_structured_task(_tools_required_request())
    except Exception as exc:
        error = exc
    assert error is not None, "expected a failure, not a silent empty success"
    assert classify_error_class(error) == ProviderErrorClass.MALFORMED_TOOL_CALL, f"got {type(error).__name__}: {error}"


def test_tools_required_empty_content_via_the_structured_fallback_path_raises_typed_error(fixture_server) -> None:
    """The specific new check this repair adds: reached only when native tool support is
    unavailable and the turn fell to the structured-output/json_mode fallback instead."""
    from unittest import mock

    _set({"choices": [{"message": {"content": ""}}]})
    adapter = _adapter(fixture_server, supports_json_mode=True)
    error = None
    with mock.patch.object(adapter, "_native_tools", return_value=()):
        try:
            adapter.run_structured_task(_tools_required_request())
        except Exception as exc:
            error = exc
    assert isinstance(error, MalformedToolArgumentsError), f"got {type(error).__name__}: {error}"
    assert classify_error_class(error) == ProviderErrorClass.MALFORMED_TOOL_CALL


# --- control cases: must still succeed ---------------------------------------------------------


def test_a_valid_native_tool_call_with_empty_text_still_succeeds(fixture_server) -> None:
    """Control: content=None alongside a VALID tool_calls array is not "empty" -- the tool call
    IS the content. Must not be rejected by the empty-response check."""
    tools = build_cloud_tool_definitions(
        [{"intent": "sandbox.run_command", "description": "Run a bounded command.", "arguments": {"command": "string"}}]
    )
    native_name = tools[0].name
    _set(
        {
            "choices": [
                {"message": {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": native_name, "arguments": '{"command":"pwd"}'}}]}}
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


def test_valid_ordinary_text_still_succeeds(fixture_server) -> None:
    _set({"choices": [{"message": {"content": "The answer is 42."}}]})
    response = _adapter(fixture_server).run_text_task(_plain_request())
    assert response.output_text == "The answer is 42."
    assert response.error is None


def _invoke_through_router(port: int):
    adapter = _adapter(port)
    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    router.registry = SimpleNamespace(build_adapter=lambda manifest: adapter)
    context = {"request_id": f"empty-retry-{port}", "surface": "api"}
    reset_for_tests()
    begin_turn(context)
    patches = (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
        mock.patch("core.memory_first_router.provider_cost_class", return_value="free_local"),
        mock.patch("core.memory_first_router.reported_cost_class", return_value="free_local"),
        mock.patch("core.memory_first_router.record_provider_success"),
        mock.patch("core.memory_first_router.record_provider_failure"),
        mock.patch("core.memory_first_router.emit_runtime_event"),
    )
    for patcher in patches:
        patcher.start()
    try:
        result = router._invoke_manifest(
            manifest=adapter.manifest,
            request=_plain_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id=f"task-{port}"),
            source_context=context,
        )
    finally:
        for patcher in reversed(patches):
            patcher.stop()
    return result, turn_call_accounting(context)


def test_first_structurally_empty_response_then_valid_retries_same_model(fixture_server) -> None:
    _set_sequence(
        {"choices": []},
        {
            "model": "same-model",
            "choices": [{"message": {"content": "The same selected model answered on retry."}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 9},
        },
    )

    (_built, response, error), accounting = _invoke_through_router(fixture_server)

    assert error is None
    assert response is not None
    assert response.output_text == "The same selected model answered on retry."
    assert _FixtureHandler.request_count == 2
    assert accounting["calls"] == 2
    assert accounting["failed_calls"] == 1
    assert accounting["completed_calls"] == 1
    assert accounting["failed_error_classes"] == [ProviderErrorClass.EMPTY_PROVIDER_RESPONSE.value]


def test_repeated_empty_response_stops_after_one_same_model_retry(fixture_server) -> None:
    _set_sequence({"choices": []}, {"id": "still-no-choices"}, {"choices": [{"message": {"content": "must not run"}}]})

    (_built, response, error), accounting = _invoke_through_router(fixture_server)

    assert response is None
    assert "did not include choices" in str(error)
    assert _FixtureHandler.request_count == 2
    assert accounting["calls"] == 2
    assert accounting["failed_calls"] == 2
    assert accounting["completed_calls"] == 0


def test_malformed_payload_does_not_consume_the_empty_retry_budget(fixture_server) -> None:
    _set_sequence(
        {"choices": {"message": {"content": "wrong container"}}},
        {"choices": [{"message": {"content": "must not run"}}]},
    )

    (_built, response, error), accounting = _invoke_through_router(fixture_server)

    assert response is None
    assert "choices must be a list" in str(error)
    assert _FixtureHandler.request_count == 1
    assert accounting["failed_calls"] == 1
    assert accounting["failed_error_classes"] == [ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE.value]
