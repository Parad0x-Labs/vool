"""Provider-response model attestation.

Three separate facts, previously collapsed into two:

  requested_model        -- what the user/runtime asked for
  runtime_selected_model -- the exact model identifier the runtime selected and sent
                             (ModelResponse.model_name / NormalizedProviderResult.actual_model,
                             sourced from the adapter's own manifest -- unchanged by this work)
  provider_attested_model -- the model identifier the PROVIDER'S RESPONSE BODY itself claims

Before this change, the runtime had no independent capture of the third value at all -- an
OpenAI-compatible chat completion response's top-level "model" field (and Ollama's /api/chat
equivalent) was parsed for `usage`/`content`/`tool_calls` but never for `model`, so a caller asking
"what does the provider say it actually ran" got the same runtime-selected value echoed back a
second time, with zero independent evidence behind it.

`provider_attested_model` is sourced ONLY from the parsed response body (`data.get("model")` in
adapters/openai_compatible_adapter.py); never from the request, the manifest, a fallback plan, or
model memory. None is a valid, honest outcome when the provider sends no model identity -- never
manufactured. A mismatch between the runtime-selected value and the provider-attested value is
preserved as two independent facts, never silently reconciled.

Uses REAL local HTTP servers (not `unittest.mock.patch` on `requests.post`) for the non-streaming
and streaming capture tests, so the actual response.json()/SSE-parse code runs for real; the one
retry-loop test reuses this file's own established mock-sequencing convention (see
test_openrouter_byok_free_lane_retries_one_malformed_turn_then_accepts_native_call in
tests/test_openai_compatible_adapter.py), since expressing "attempt 1 returns X, attempt 2 returns
Y" is what that convention already exists for.
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
from core.cloud_tool_call_contract import build_cloud_tool_definitions
from core.normalized_provider_result import normalize_model_response


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


def _set(payload) -> None:
    _FixtureHandler.body = json.dumps(payload).encode("utf-8") if isinstance(payload, dict) else payload


def _cloud_adapter(port: int, *, model_name: str = "vendor/selected-model") -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="fixture-cloud",
            provider_id=f"fixture-cloud:{model_name}",
            model_name=model_name,
            metadata={"runtime_family": "openai-compatible"},
            runtime_config={"base_url": f"http://127.0.0.1:{port}", "api_path": "/v1/chat/completions", "timeout_seconds": 5.0},
        )
    )


def _ollama_adapter(port: int, *, model_name: str = "selected-local-model") -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="fixture-ollama",
            provider_id=f"fixture-ollama:{model_name}",
            model_name=model_name,
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": f"http://127.0.0.1:{port}", "timeout_seconds": 5.0},
        )
    )


def _plain_request(prompt: str = "hello") -> ModelRequest:
    return ModelRequest(task_kind="chat", prompt=prompt, output_mode="plain_text", messages=[{"role": "user", "content": prompt}])


# --- non-streaming OpenAI-compatible: the three attestation shapes ------------------------------


def test_openai_response_model_is_captured_as_provider_attested(fixture_server) -> None:
    """Case 1: requested=A, selected=A, response model=A -- all three agree, attested is genuine
    response evidence, not merely echoed from the manifest."""
    _set({"choices": [{"message": {"content": "hi"}}], "model": "vendor/selected-model"})
    response = _cloud_adapter(fixture_server, model_name="vendor/selected-model").run_text_task(_plain_request())
    assert response.model_name == "vendor/selected-model"
    assert response.provider_attested_model == "vendor/selected-model"


def test_openai_response_omitting_model_leaves_attestation_none(fixture_server) -> None:
    """Case 3: the provider sent no model identity at all -- None, never manufactured from
    `model_name` (the runtime's own selection)."""
    _set({"choices": [{"message": {"content": "hi"}}]})
    response = _cloud_adapter(fixture_server, model_name="vendor/selected-model").run_text_task(_plain_request())
    assert response.model_name == "vendor/selected-model"
    assert response.provider_attested_model is None


def test_openai_response_model_mismatch_is_preserved_not_reconciled(fixture_server) -> None:
    """Case 4: the runtime sent to "vendor/selected-model" but the provider's response body claims
    "vendor/actually-served-model" -- both facts survive independently; neither is silently
    normalized to match the other."""
    _set({"choices": [{"message": {"content": "hi"}}], "model": "vendor/actually-served-model"})
    response = _cloud_adapter(fixture_server, model_name="vendor/selected-model").run_text_task(_plain_request())
    assert response.model_name == "vendor/selected-model"
    assert response.provider_attested_model == "vendor/actually-served-model"
    assert response.model_name != response.provider_attested_model


def test_openai_malformed_response_never_fabricates_attestation(fixture_server) -> None:
    """Case 5: an unparseable HTTP body must fail the call outright -- there is no ModelResponse
    to inspect, so there is no attestation to fake. The failure itself is the proof."""
    _set(b"{not valid json at all")
    with pytest.raises(ValueError):
        _cloud_adapter(fixture_server).run_text_task(_plain_request())


# --- non-streaming Ollama: same contract, same extraction path ----------------------------------


def test_ollama_response_model_is_captured_as_provider_attested(fixture_server) -> None:
    _set({"message": {"content": "hi"}, "model": "selected-local-model"})
    response = _ollama_adapter(fixture_server, model_name="selected-local-model").run_text_task(_plain_request())
    assert response.model_name == "selected-local-model"
    assert response.provider_attested_model == "selected-local-model"


def test_ollama_response_omitting_model_leaves_attestation_none(fixture_server) -> None:
    _set({"message": {"content": "hi"}})
    response = _ollama_adapter(fixture_server).run_text_task(_plain_request())
    assert response.provider_attested_model is None


def test_ollama_response_model_mismatch_is_preserved(fixture_server) -> None:
    _set({"message": {"content": "hi"}, "model": "a-different-tag-entirely"})
    response = _ollama_adapter(fixture_server, model_name="selected-local-model").run_text_task(_plain_request())
    assert response.model_name == "selected-local-model"
    assert response.provider_attested_model == "a-different-tag-entirely"


# --- streaming: captured once from initial metadata, preserved through the whole stream ---------


def _sse(*objs) -> bytes:
    lines = []
    for obj in objs:
        lines.append(f"data: {json.dumps(obj)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


def test_openai_stream_captures_model_from_initial_chunk_and_preserves_it(fixture_server) -> None:
    """Case 6: the provider only sends "model" on the FIRST frame -- captured once, then carried
    on every subsequent chunk including the terminal one, not lost after the first frame passes."""
    _set(
        _sse(
            {"model": "vendor/streamed-model", "choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 1, "completion_tokens": 2}},
        )
    )
    adapter = _cloud_adapter(fixture_server)
    chunks = list(adapter.stream_text_task(_plain_request()))
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.provider_attested_model == "vendor/streamed-model"
    assert chunks[-1].done is True


def test_openai_stream_with_no_model_identity_stays_none(fixture_server) -> None:
    """Case 7: no frame ever carries "model" -- None throughout, never manufactured."""
    _set(
        _sse(
            {"choices": [{"delta": {"content": "Hi"}}]},
            {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        )
    )
    adapter = _cloud_adapter(fixture_server)
    chunks = list(adapter.stream_text_task(_plain_request()))
    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.provider_attested_model is None


def test_ollama_stream_captures_model_from_initial_chunk_and_preserves_it(fixture_server) -> None:
    ndjson = (
        json.dumps({"model": "selected-local-model", "message": {"content": "Hi"}, "done": False}) + "\n"
        + json.dumps({"message": {"content": ""}, "done": True, "eval_count": 3}) + "\n"
    )
    _set(ndjson.encode("utf-8"))
    with mock.patch("core.local_inference_evidence.record_ollama_generate_benchmark"):
        chunks = list(_ollama_adapter(fixture_server, model_name="selected-local-model").stream_text_task(_plain_request()))
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.provider_attested_model == "selected-local-model"


# --- retry: the final attestation belongs to the successful attempt, never a stale earlier one ---


def _native_tool_request() -> ModelRequest:
    tools = build_cloud_tool_definitions(
        [{"intent": "sandbox.run_command", "description": "Run a bounded command.", "arguments": {"command": "string"}}]
    )
    return ModelRequest(
        task_kind="tool_intent", prompt="run a command", output_mode="tool_intent",
        messages=[{"role": "user", "content": "run a command"}], tools=tools, tool_choice="required",
    )


def test_retry_final_attestation_belongs_to_the_successful_attempt_not_the_failed_one() -> None:
    """Attempt 1 (malformed prose, no recoverable call) claims model "attempt-1-provider-model";
    attempt 2 (a valid native call) claims "attempt-2-provider-model". The final ModelResponse
    must carry the SECOND attempt's attestation -- the first attempt's value must not leak
    through just because it was seen first."""
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="openrouter-byok",
            provider_id="openrouter-byok:vendor/tool-model:free",
            model_name="vendor/tool-model:free",
            metadata={"runtime_family": "openai-compatible", "verified_free": True},
            runtime_config={
                "base_url": "https://openrouter.ai/api/v1", "api_path": "/chat/completions",
                "timeout_seconds": 5.0, "supports_json_mode": True,
            },
        )
    )
    malformed = mock.Mock()
    malformed.raise_for_status.return_value = None
    malformed.json.return_value = {
        "choices": [{"message": {"content": "not a recoverable tool call, just prose"}}],
        "model": "attempt-1-provider-model",
    }
    recovered = mock.Mock()
    recovered.raise_for_status.return_value = None
    recovered.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "retry-2", "type": "function", "function": {"name": "sandbox__run_command", "arguments": '{"command":"pwd"}'}}
                    ],
                }
            }
        ],
        "model": "attempt-2-provider-model",
    }
    with mock.patch("adapters.openai_compatible_adapter.requests.post", side_effect=[malformed, recovered]) as post:
        result = adapter.run_structured_task(_native_tool_request())
    assert post.call_count == 2
    assert result.provider_attested_model == "attempt-2-provider-model"
    assert result.provider_attested_model != "attempt-1-provider-model"


# --- NormalizedProviderResult: the full three-way contract (requested/selected/attested) --------


def _fake_response(*, model_name: str, provider_attested_model, output_text: str = "ok"):
    return SimpleNamespace(
        output_text=output_text, tool_calls=(), error=None, usage={},
        provider_id="p", model_name=model_name, response_id="r1",
        provider_attested_model=provider_attested_model,
    )


def test_normalized_case_1_all_three_agree() -> None:
    normalized = normalize_model_response(
        _fake_response(model_name="A", provider_attested_model="A"),
        requested_model="A", resolved_model="A",
    )
    assert normalized.requested_model == "A"
    assert normalized.actual_model == "A"
    assert normalized.provider_attested_model == "A"


def test_normalized_case_2_fallback_selected_differs_from_requested_attested_matches_selected() -> None:
    """requested=A, runtime fallback selected B, provider attests B."""
    normalized = normalize_model_response(
        _fake_response(model_name="B", provider_attested_model="B"),
        requested_model="A", resolved_model="B",
    )
    assert normalized.requested_model == "A"
    assert normalized.actual_model == "B"
    assert normalized.provider_attested_model == "B"


def test_normalized_case_3_missing_attestation_is_none_not_fabricated() -> None:
    normalized = normalize_model_response(
        _fake_response(model_name="A", provider_attested_model=None),
        requested_model="A", resolved_model="A",
    )
    assert normalized.requested_model == "A"
    assert normalized.actual_model == "A"
    assert normalized.provider_attested_model is None


def test_normalized_case_4_mismatch_between_selected_and_attested_is_preserved() -> None:
    normalized = normalize_model_response(
        _fake_response(model_name="A", provider_attested_model="B"),
        requested_model="A", resolved_model="A",
    )
    assert normalized.requested_model == "A"
    assert normalized.actual_model == "A"
    assert normalized.provider_attested_model == "B"
    assert normalized.actual_model != normalized.provider_attested_model
