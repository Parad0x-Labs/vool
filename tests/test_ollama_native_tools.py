"""Native tool calling on the Ollama lane.

Envelopes here are the real shapes measured against a live daemon on 2026-07-28 (qwen3:8b and
qwen3:0.6b, five unseen phrasings each). The detail worth a test is that Ollama returns
`arguments` as a JSON **object** where OpenAI returns a JSON **string**; one adapter class
serves both, so both have to parse.

The live drive is not replayed here — it needs a running daemon — but the dispatch rates it
produced are the reason this path exists: with the catalog offered natively, qwen3:8b called a
tool on 5 of 5 phrasings that the prose lane refuses, and qwen3:0.6b on 2 of 5.
"""
from __future__ import annotations

import json

import pytest

from adapters.openai_compatible_adapter import (
    _extract_ollama_chat_text,
    _extract_ollama_tool_call_text,
    _extract_ollama_tool_calls,
)
from core.cloud_provider_contract import CloudToolDefinition
from core.cloud_tool_call_contract import (
    DuplicateToolCallError,
    MalformedToolArgumentsError,
    UnknownToolNameError,
)

LIST_DIR = CloudToolDefinition(
    intent="machine.list_directory",
    name="machine__list_directory",
    description="List a directory.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)
READ_FILE = CloudToolDefinition(
    intent="workspace.read_file",
    name="workspace__read_file",
    description="Read a file.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)
DEFINITIONS = (LIST_DIR, READ_FILE)


def _envelope(tool_calls, content: str = "") -> dict:
    return {"message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}


def test_object_arguments_are_parsed() -> None:
    """Ollama's own shape, measured live from qwen3:8b."""

    data = _envelope(
        [{"function": {"name": "machine__list_directory", "arguments": {"path": "~/Desktop/x"}}}]
    )
    parsed = json.loads(_extract_ollama_tool_call_text(data, definitions=DEFINITIONS))
    assert parsed == {"intent": "machine.list_directory", "arguments": {"path": "~/Desktop/x"}}


def test_string_arguments_are_parsed() -> None:
    """The OpenAI shape, since an OpenAI-compatible proxy may sit in front of a local model."""

    data = _envelope(
        [{"function": {"name": "machine__list_directory", "arguments": '{"path": "/tmp/x"}'}}]
    )
    parsed = json.loads(_extract_ollama_tool_call_text(data, definitions=DEFINITIONS))
    assert parsed["arguments"] == {"path": "/tmp/x"}


def test_provider_name_resolves_to_the_runtime_intent() -> None:
    """`machine.list_directory` is not a legal function name; the encoded name must map back."""

    data = _envelope([{"function": {"name": "workspace__read_file", "arguments": {}}}])
    parsed = json.loads(_extract_ollama_tool_call_text(data, definitions=DEFINITIONS))
    assert parsed["intent"] == "workspace.read_file"


def test_an_invented_tool_name_is_not_a_tool_call() -> None:
    """A hallucinated name must never dispatch -- and must fail LOUDLY, not silently become "no
    call happened" (which used to read identically to the model choosing not to call anything)."""

    data = _envelope([{"function": {"name": "bash", "arguments": {"cmd": "rm -rf /"}}}])
    with pytest.raises(UnknownToolNameError):
        _extract_ollama_tool_call_text(data, definitions=DEFINITIONS)


def test_unparseable_arguments_raise_instead_of_silently_becoming_empty() -> None:
    """A valid name with broken arguments must not silently become an empty-argument call --
    that is indistinguishable downstream from a genuine, correct zero-argument call. The defect
    is named (MalformedToolArgumentsError) so the caller can report it, not paper over it."""

    data = _envelope(
        [{"function": {"name": "machine__list_directory", "arguments": "{not json"}}]
    )
    with pytest.raises(MalformedToolArgumentsError):
        _extract_ollama_tool_call_text(data, definitions=DEFINITIONS)


def test_no_tool_calls_key_is_not_a_tool_call() -> None:
    assert _extract_ollama_tool_call_text({"message": {"content": "hello"}}, definitions=DEFINITIONS) == ""


def test_empty_tool_calls_list_is_not_a_tool_call() -> None:
    assert _extract_ollama_tool_call_text(_envelope([]), definitions=DEFINITIONS) == ""


def test_a_call_beside_prose_is_still_a_call() -> None:
    """Measured: models emit a sentence of preamble alongside the call.

    Reading `content` first would score this as chat and lose the dispatch.
    """

    data = _envelope(
        [{"function": {"name": "machine__list_directory", "arguments": {"path": "/x"}}}],
        content="Sure, let me look at that folder for you.",
    )
    assert _extract_ollama_tool_call_text(data, definitions=DEFINITIONS)
    assert _extract_ollama_chat_text(data) == "Sure, let me look at that folder for you."


def test_flat_shape_without_a_function_wrapper_is_accepted() -> None:
    data = _envelope([{"name": "machine__list_directory", "arguments": {"path": "/x"}}])
    parsed = json.loads(_extract_ollama_tool_call_text(data, definitions=DEFINITIONS))
    assert parsed["intent"] == "machine.list_directory"


class _Manifest:
    def __init__(self, family: str = "ollama") -> None:
        self.provider_id = "ollama-local"
        self.provider_name = "ollama"
        self.model_name = "qwen3:8b"
        self.runtime_config = {"base_url": "http://127.0.0.1:11434"}
        self.metadata = {"runtime_family": family}


@pytest.fixture()
def adapter():
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    return OpenAICompatibleAdapter(_Manifest())  # type: ignore[arg-type]


def _request(tools=DEFINITIONS):
    from adapters.base_adapter import ModelRequest

    return ModelRequest(task_kind="chat", prompt="hi", tools=tuple(tools))


def test_native_tools_can_be_switched_off(adapter) -> None:
    """The flag ships ON as of 2026-07-28; it remains a kill switch, so both states are tested."""

    from core import runtime_flags

    with runtime_flags.override("ollama_native_tools", False):
        assert adapter._ollama_native_tools(_request()) == ()


def test_native_tools_are_offered_when_the_flag_is_enabled(adapter) -> None:
    from core import runtime_flags

    with runtime_flags.override("ollama_native_tools", True):
        assert adapter._ollama_native_tools(_request()) == DEFINITIONS


def test_a_turn_with_no_tools_is_not_an_error(adapter) -> None:
    """The cloud path raises on an empty tool list; a local conversational turn must not.

    Most local turns carry no tools and still have to be answered.
    """

    from core import runtime_flags

    with runtime_flags.override("ollama_native_tools", True):
        assert adapter._ollama_native_tools(_request(tools=())) == ()


def test_a_non_ollama_runtime_never_gets_the_ollama_tool_path(adapter) -> None:
    from core import runtime_flags

    adapter.manifest.metadata["runtime_family"] = "openai"
    with runtime_flags.override("ollama_native_tools", True):
        assert adapter._ollama_native_tools(_request()) == ()


# --------------------------------------------------------------------------------------
# End-to-end through the adapter's own response handling. Parsing the envelope correctly is
# not enough — the completion path has to read the tool call BEFORE it falls back to content,
# and that ordering lives at the call site, not in the extractor.
# --------------------------------------------------------------------------------------


class _Response:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.fixture()
def _post(monkeypatch: pytest.MonkeyPatch):
    """Serve one canned Ollama envelope and capture the outbound payload."""

    captured: dict = {}

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _Response(captured["reply"])

    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", _fake_post)
    monkeypatch.setattr(
        "adapters.openai_compatible_adapter.OpenAICompatibleAdapter._gate_local_model_load",
        lambda self: None,
    )
    return captured


def test_the_outbound_payload_carries_tools_when_enabled(adapter, _post) -> None:
    from core import runtime_flags

    _post["reply"] = _envelope([], content="hi")
    with runtime_flags.override("ollama_native_tools", True):
        adapter._invoke_ollama_chat(_request(), force_json=False)
    names = [item["function"]["name"] for item in _post["payload"]["tools"]]
    assert names == ["machine__list_directory", "workspace__read_file"]
    # Sent unstrict on purpose: Ollama accepts `strict` but does not enforce it like a cloud
    # provider does, so the guarantee would be advertised and not upheld. We validate instead.
    assert all(item["function"]["strict"] is False for item in _post["payload"]["tools"])


def test_the_outbound_payload_has_no_tools_key_when_disabled(adapter, _post) -> None:
    from core import runtime_flags

    _post["reply"] = _envelope([], content="hi")
    with runtime_flags.override("ollama_native_tools", False):
        adapter._invoke_ollama_chat(_request(), force_json=False)
    assert "tools" not in _post["payload"]


def test_a_tool_call_beside_prose_wins_over_the_prose(adapter, _post) -> None:
    """The ordering test. Reading `content` first scores a real dispatch as chat.

    Measured live: models routinely emit "Sure, let me look at that folder" alongside the call.
    """

    from core import runtime_flags

    _post["reply"] = _envelope(
        [{"function": {"name": "machine__list_directory", "arguments": {"path": "/x"}}}],
        content="Sure, let me look at that folder for you.",
    )
    with runtime_flags.override("ollama_native_tools", True):
        response = adapter._invoke_ollama_chat(_request(), force_json=False)
    assert json.loads(response.output_text) == {
        "intent": "machine.list_directory",
        "arguments": {"path": "/x"},
    }


def test_prose_still_comes_back_when_there_is_no_tool_call(adapter, _post) -> None:
    from core import runtime_flags

    _post["reply"] = _envelope([], content="The folder holds three files.")
    with runtime_flags.override("ollama_native_tools", True):
        response = adapter._invoke_ollama_chat(_request(), force_json=False)
    assert response.output_text == "The folder holds three files."


def test_an_invented_name_raises_end_to_end_instead_of_silently_answering(adapter, _post) -> None:
    """A hallucinated tool name must fail the whole call, not quietly hand back the prose that
    happened to sit beside it -- that prose was never validated as a real, ungrounded answer, it
    is just text the model wrote alongside a call it invented."""

    from core import runtime_flags

    _post["reply"] = _envelope(
        [{"function": {"name": "bash", "arguments": {"cmd": "rm -rf /"}}}],
        content="I will run a shell command.",
    )
    with runtime_flags.override("ollama_native_tools", True):
        with pytest.raises(UnknownToolNameError):
            adapter._invoke_ollama_chat(_request(), force_json=False)


def test_format_and_tools_are_never_sent_together(adapter, _post) -> None:
    """Ollama's `format` grammar suppresses native tool calling; sending both is catastrophic.

    Measured 2026-07-28 on qwen3:8b with 57 tool definitions, tool_choice="required" and the
    tool_intent json_schema in one body: zero `tool_calls`, and the model degenerated in the
    content channel — repeating `"arguments": {"path": ...}` for 222 seconds until num_predict
    cut it off with done_reason="length" and unparseable JSON. The turn died on a 60s provider
    budget, which was then spent, so no second candidate was tried either. The identical body
    with `format` removed returned a clean native tool call in 35s.

    This was introduced by adding `tools` to the payload without excluding the force_json branch.
    """

    from core import runtime_flags

    adapter.manifest.runtime_config["supports_json_schema"] = True
    _post["reply"] = _envelope([], content="hi")
    request = _request()
    request.contract = {"json_schema": {"type": "object", "required": ["intent"]}}
    with runtime_flags.override("ollama_native_tools", True):
        adapter._invoke_ollama_chat(request, force_json=True)
    payload = _post["payload"]
    assert payload["tools"], "native tools should still be offered"
    assert "format" not in payload, "a format grammar alongside tools suppresses tool calling"


def test_format_is_still_used_when_no_tools_are_offered(adapter, _post) -> None:
    """Structured-output turns that carry no tools must keep their grammar."""

    from core import runtime_flags

    adapter.manifest.runtime_config["supports_json_schema"] = True
    _post["reply"] = _envelope([], content="{}")
    request = _request(tools=())
    request.contract = {"json_schema": {"type": "object", "required": ["intent"]}}
    with runtime_flags.override("ollama_native_tools", True):
        adapter._invoke_ollama_chat(request, force_json=True)
    assert _post["payload"]["format"] == {"type": "object", "required": ["intent"]}


# --------------------------------------------------------------------------------------
# Parallel calls. Ollama emits them routinely; taking only the first lost the rest silently.
# --------------------------------------------------------------------------------------


def _read(path: str) -> dict:
    return {"function": {"name": "workspace__read_file", "arguments": {"path": path}}}


def test_every_call_in_a_parallel_batch_is_kept() -> None:
    """The measured shape. qwen3:8b answered "read README.md and SECURITY.md" with TWO calls.

    Before this, one file was dispatched and the second was discarded with nothing recorded, so
    the model answered as though it had read both. A dropped call is not a smaller answer, it is
    a wrong one.
    """

    calls = _extract_ollama_tool_calls(
        _envelope([_read("README.md"), _read("SECURITY.md")]), definitions=DEFINITIONS
    )

    assert [call.arguments["path"] for call in calls] == ["README.md", "SECURITY.md"]
    assert all(call.intent == "workspace.read_file" for call in calls)


def test_the_first_call_still_drives_the_canonical_text() -> None:
    """Single-step consumers are unchanged: the text is the first call, exactly as before."""

    envelope = _envelope([_read("README.md"), _read("SECURITY.md")])

    assert json.loads(_extract_ollama_tool_call_text(envelope, definitions=DEFINITIONS)) == {
        "intent": "workspace.read_file",
        "arguments": {"path": "README.md"},
    }


def test_one_invented_name_refuses_the_whole_batch() -> None:
    """Fail closed, matching `parse_native_tool_calls` on the cloud lane, and LOUDLY: raises
    UnknownToolNameError rather than silently returning an empty batch.

    Executing the valid half of a reply that also invented a tool would dispatch work the model
    proposed alongside something it was never offered.
    """

    forged = {"function": {"name": "evil__exfiltrate", "arguments": {}}}

    with pytest.raises(UnknownToolNameError):
        _extract_ollama_tool_calls(_envelope([_read("README.md"), forged]), definitions=DEFINITIONS)
    with pytest.raises(UnknownToolNameError):
        _extract_ollama_tool_call_text(_envelope([_read("README.md"), forged]), definitions=DEFINITIONS)


def test_a_parallel_batch_reaches_the_response_object(adapter, _post) -> None:
    """The whole batch has to survive the adapter, not just the extractor."""

    from core import runtime_flags

    _post["reply"] = _envelope([_read("README.md"), _read("SECURITY.md")])
    with runtime_flags.override("ollama_native_tools", True):
        response = adapter._invoke_ollama_chat(_request(), force_json=False)

    assert [call.arguments["path"] for call in response.tool_calls] == ["README.md", "SECURITY.md"]


# --------------------------------------------------------------------------------------
# Malformed-call handling closure pass: named, typed failures instead of silent drops/defaults.
# --------------------------------------------------------------------------------------


def test_duplicate_call_id_is_rejected_deterministically() -> None:
    """The exact same call id appearing twice in one batch is a distinct defect from either call
    being individually valid -- must not silently execute (or silently drop) either one."""

    entry = {"id": "call-1", "function": {"name": "workspace__read_file", "arguments": {"path": "README.md"}}}
    with pytest.raises(DuplicateToolCallError):
        _extract_ollama_tool_calls(_envelope([entry, dict(entry)]), definitions=DEFINITIONS)


def test_duplicate_call_with_no_id_is_detected_by_name_and_arguments() -> None:
    """Ollama does not always send an id. Without one, an EXACT repeat (same name, same
    arguments) is still a duplicate; two calls to the same tool with DIFFERENT arguments are
    NOT -- that is a legitimate parallel batch (see test_every_call_in_a_parallel_batch_is_kept)."""

    same_twice = {"function": {"name": "workspace__read_file", "arguments": {"path": "README.md"}}}
    with pytest.raises(DuplicateToolCallError):
        _extract_ollama_tool_calls(_envelope([same_twice, dict(same_twice)]), definitions=DEFINITIONS)


def test_two_different_calls_with_no_id_are_not_flagged_as_duplicates() -> None:
    """Control: different arguments, no id on either -- must NOT raise."""

    calls = _extract_ollama_tool_calls(
        _envelope([_read("README.md"), _read("SECURITY.md")]), definitions=DEFINITIONS
    )
    assert len(calls) == 2


def test_arguments_that_are_neither_string_nor_object_are_malformed() -> None:
    """A number or a list where an object is expected is exactly as malformed as broken JSON
    text -- both must raise, neither should default to {}."""

    data = _envelope([{"function": {"name": "machine__list_directory", "arguments": 42}}])
    with pytest.raises(MalformedToolArgumentsError):
        _extract_ollama_tool_calls(data, definitions=DEFINITIONS)


def test_a_genuinely_empty_argument_call_is_not_treated_as_malformed() -> None:
    """Control: a real, correct zero-argument call (arguments omitted, or an empty object/string)
    must still work -- only a call that CLAIMED content and failed to parse is malformed."""

    for arguments in (None, {}, ""):
        data = _envelope([{"function": {"name": "machine__list_directory", "arguments": arguments}}])
        calls = _extract_ollama_tool_calls(data, definitions=DEFINITIONS)
        assert calls[0].arguments == {}
