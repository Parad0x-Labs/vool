from __future__ import annotations

from adapters.base_adapter import ModelRequest, ModelResponse, ModelStreamChunk
from core.cloud_provider_contract import CloudModelRequest
from core.disclosure.replacer import replace_texts
from core.disclosure.restore import restore_response, restore_stream
from core.disclosure.wire import request_context


def test_frozen_broker_request_keeps_its_metadata_identity():
    request = CloudModelRequest("task", "turn", "sub", "call", "model", (), 100)
    metadata = request.metadata
    first = request_context(request, provider="recording", cloud=True)
    assert request.metadata is metadata
    assert first["turn_id"] == "turn"
    assert request_context(request, provider="recording", cloud=True) == first


def test_display_restoration_never_changes_executable_tool_arguments():
    prepared = replace_texts(["Mira Example"], contact_names=["Mira Example"])
    request = ModelRequest("answer", "question", metadata={"_disclosure_restore": prepared})
    response = ModelResponse(output_text="[Person 1] is available.")
    assert restore_response(request, response).output_text == "Mira Example is available."
    request.output_mode = "tool_intent"
    assert restore_response(request, response).output_text == "[Person 1] is available."


def test_streaming_placeholder_split_across_frames_is_restored_once():
    prepared = replace_texts(["Mira Example"], contact_names=["Mira Example"])
    request = ModelRequest("answer", "question", metadata={"_disclosure_restore": prepared})
    chunks = [ModelStreamChunk(delta_text="Hello [Per"), ModelStreamChunk(delta_text="son 1]"),
              ModelStreamChunk(delta_text=".", done=True, usage={"completion_tokens": 3})]
    actual = list(restore_stream(request, chunks))
    assert "".join(c.delta_text for c in actual) == "Hello Mira Example."
    assert actual[-1].usage == {"completion_tokens": 3}
