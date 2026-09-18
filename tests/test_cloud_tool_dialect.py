"""Cloud tool dialect chosen by capability, and tool-call recovery on every lane.

Two behaviours are under test.

**Dialect.** Before this, `_native_tools` required `hostname == openrouter.ai`, so a user's own
OpenAI, Anthropic, Groq, DeepSeek or Gemini key received no native function schemas at all and
fell back to a prose catalog — the weakest lane handed to the strongest models. The flag keeps
the old rule as the default so nothing changes until it is deliberately switched on.

**Recovery.** The contract accepts one or more schema-valid native calls. Measured on the free
OpenRouter tier, models answer a tool request with the call written as JSON in `content`, or
with a malformed `tool_calls` list. Those replies used to throw the turn away even when a valid
call was recoverable from content. Recovery only ever runs when tools were genuinely offered — the name
check inside `_tool_call_text_from_content` cannot reject anything against an empty offered set,
so attempting it on a toolless turn would let any JSON-shaped chat dispatch.
"""
from __future__ import annotations

import json

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import (
    OpenAICompatibleAdapter,
    _extract_native_openai_tool_text,
    _repaired_tool_call_from_payload,
)
from core import runtime_flags
from core.cloud_provider_contract import CloudToolDefinition

LIST_DIR = CloudToolDefinition(
    intent="machine.list_directory",
    name="machine__list_directory",
    description="List a directory.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)
DEFINITIONS = (LIST_DIR,)


def _reply(message: dict) -> dict:
    return {"choices": [{"message": message, "finish_reason": "stop"}]}


def _native_call(name: str = "machine__list_directory", arguments: str = '{"path": "/x"}', call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


class _Manifest:
    def __init__(self, *, base_url: str, provider_name: str = "custom", metadata: dict | None = None):
        self.provider_id = "p"
        self.provider_name = provider_name
        self.model_name = "some-model"
        self.runtime_config = {"base_url": base_url}
        self.metadata = dict(metadata or {})


def _adapter(**kwargs) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(_Manifest(**kwargs))  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Dialect selection
# --------------------------------------------------------------------------------------


def test_the_historical_hostname_rule_holds_exactly_when_the_flag_is_off() -> None:
    """With the flag off the historical rule must hold exactly, on both sides.

    This test previously said "with the flag off" in its docstring and never set the flag, while
    `capability_tool_dialect` defaults to **on**. It therefore exercised the capability path while
    asserting the hostname path, and passed only because the old capability path fell through to
    the same hostname rule when nothing was declared. It now sets the flag it names.
    """

    with runtime_flags.override("capability_tool_dialect", False):
        assert _adapter(base_url="https://openrouter.ai/api")._supports_native_tools() is True
        assert _adapter(base_url="https://api.openai.com")._supports_native_tools() is False


def test_an_undeclared_remote_openai_lane_is_offered_native_tools() -> None:
    """The blocker this fix exists for, pinned on the default (flag on) path.

    `tool_support` is a hand-maintained list in core/runtime_provider_defaults.py. Five shipped
    lanes - `llamacpp-local`, `mlx-local`, `kimi-remote`, `openai-compatible-remote` and
    `tether-remote` - ship `["structured_json", "code_complex"]` and so received ZERO native
    schemas: two LOCAL lanes and the user's own OpenAI/Anthropic/Groq key, handed the weakest
    dialect VOOL has because of a static string rather than anything the server said.

    A declaration that omits `tool_calls` is not evidence the server lacks them.
    """

    adapter = _adapter(
        base_url="https://api.openai.com",
        metadata={"tool_support": ["structured_json", "code_complex"]},
    )

    assert adapter._supports_native_tools() is True


def test_a_provider_published_parameter_list_is_authoritative_in_both_directions() -> None:
    """`supported_parameters` comes from the provider's own catalog, so its silence means no.

    This is the line between the two signals: a fetched, per-model list can be trusted to be
    complete, and a hand-maintained allowlist cannot.
    """

    says_no = _adapter(
        base_url="https://example.test", metadata={"supported_parameters": ["temperature", "top_p"]}
    )
    says_yes = _adapter(
        base_url="https://example.test", metadata={"supported_parameters": ["tools", "temperature"]}
    )

    assert says_no._supports_native_tools() is False
    assert says_yes._supports_native_tools() is True


def test_capability_flag_gives_a_direct_openai_key_native_tools() -> None:
    adapter = _adapter(
        base_url="https://api.openai.com",
        metadata={"tool_support": ["structured_json", "tool_calls"]},
    )
    with runtime_flags.override("capability_tool_dialect", True):
        assert adapter._supports_native_tools() is True


def test_openrouter_supported_parameters_select_native() -> None:
    """`supported_parameters` is what OpenRouter's live catalog publishes per model."""

    adapter = _adapter(
        base_url="https://example.test",
        metadata={"supported_parameters": ["tools", "temperature"]},
    )
    with runtime_flags.override("capability_tool_dialect", True):
        assert adapter._supports_native_tools() is True


def test_a_model_without_tool_capability_stays_on_the_prompted_lane() -> None:
    adapter = _adapter(
        base_url="https://example.test",
        metadata={"supported_parameters": ["temperature", "top_p"]},
    )
    with runtime_flags.override("capability_tool_dialect", True):
        assert adapter._supports_native_tools() is False


def test_an_explicit_manifest_setting_overrides_capability() -> None:
    adapter = _adapter(base_url="https://openrouter.ai/api")
    adapter.manifest.runtime_config["tool_dialect"] = "prompted"
    with runtime_flags.override("capability_tool_dialect", True):
        assert adapter._supports_native_tools() is False


def test_the_hostname_rule_remains_a_floor() -> None:
    """Turning the flag on must not take native tools away from a lane that had them."""

    adapter = _adapter(base_url="https://openrouter.ai/api")
    with runtime_flags.override("capability_tool_dialect", True):
        assert adapter._supports_native_tools() is True


# --------------------------------------------------------------------------------------
# Recovery from a malformed native envelope
# --------------------------------------------------------------------------------------


def test_a_well_formed_native_call_is_used_directly() -> None:
    text = _extract_native_openai_tool_text(_reply({"tool_calls": [_native_call()]}), definitions=DEFINITIONS)
    assert json.loads(text)["intent"] == "machine.list_directory"


def test_valid_native_batch_prefers_the_validated_first_call_over_content() -> None:
    """A valid provider batch is evidence, not a malformed envelope."""

    # Distinct call_ids: a real provider batch never reuses one, and a REPEATED id is itself a
    # distinct defect (DuplicateToolCallError) this module now also checks for.
    message = {
        "tool_calls": [_native_call(call_id="call_1"), _native_call(call_id="call_2")],
        "content": '{"intent": "machine.list_directory", "arguments": {"path": "/recovered"}}',
    }
    text = _extract_native_openai_tool_text(_reply(message), definitions=DEFINITIONS)
    assert json.loads(text)["arguments"] == {"path": "/x"}


def test_malformed_native_batch_recovers_from_content() -> None:
    malformed = _native_call()
    malformed["function"]["name"] = "unregistered_tool"
    message = {
        "tool_calls": [_native_call(), malformed],
        "content": '{"intent": "machine.list_directory", "arguments": {"path": "/recovered"}}',
    }
    text = _extract_native_openai_tool_text(_reply(message), definitions=DEFINITIONS)
    assert json.loads(text)["arguments"] == {"path": "/recovered"}


def test_zero_native_calls_recover_from_content() -> None:
    message = {
        "tool_calls": [],
        "content": '{"intent": "machine.list_directory", "arguments": {"path": "/recovered"}}',
    }
    text = _extract_native_openai_tool_text(_reply(message), definitions=DEFINITIONS)
    assert json.loads(text)["arguments"] == {"path": "/recovered"}


def test_a_malformed_envelope_with_nothing_recoverable_still_raises() -> None:
    """Recovery must not turn a genuinely broken turn into a silent success."""

    malformed = _native_call()
    malformed["function"]["name"] = "unregistered_tool"
    message = {"tool_calls": [_native_call(), malformed], "content": "I am not sure."}
    with pytest.raises(ValueError):
        _extract_native_openai_tool_text(_reply(message), definitions=DEFINITIONS)


# --------------------------------------------------------------------------------------
# Recovery on the prompted lane
# --------------------------------------------------------------------------------------


def test_prose_json_on_the_prompted_lane_is_recovered() -> None:
    message = {"content": 'Sure!\n{"intent": "machine.list_directory", "arguments": {"path": "/x"}}'}
    text = _repaired_tool_call_from_payload(_reply(message), definitions=DEFINITIONS)
    assert json.loads(text) == {"intent": "machine.list_directory", "arguments": {"path": "/x"}}


def test_the_provider_side_name_is_accepted_and_mapped_back() -> None:
    message = {"content": '{"name": "machine__list_directory", "parameters": {"path": "/x"}}'}
    text = _repaired_tool_call_from_payload(_reply(message), definitions=DEFINITIONS)
    assert json.loads(text)["intent"] == "machine.list_directory"


def test_ordinary_chat_is_not_turned_into_a_tool_call() -> None:
    message = {"content": "The folder holds three files: a, b and c."}
    assert _repaired_tool_call_from_payload(_reply(message), definitions=DEFINITIONS) == ""


def test_an_invented_tool_name_is_not_recovered() -> None:
    message = {"content": '{"tool": "bash", "arguments": {"cmd": "rm -rf /"}}'}
    assert _repaired_tool_call_from_payload(_reply(message), definitions=DEFINITIONS) == ""


def test_an_empty_reply_is_not_a_tool_call() -> None:
    assert _repaired_tool_call_from_payload({}, definitions=DEFINITIONS) == ""
    assert _repaired_tool_call_from_payload(_reply({}), definitions=DEFINITIONS) == ""


# --------------------------------------------------------------------------------------
# End to end through the adapter, including the guard against toolless dispatch
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
    captured: dict = {}

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        return _Response(captured["reply"])

    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", _fake_post)
    return captured


def test_prompted_lane_dispatches_a_call_written_as_prose(_post) -> None:
    adapter = _adapter(base_url="https://api.openai.com")
    _post["reply"] = _reply(
        {"content": '{"intent": "machine.list_directory", "arguments": {"path": "/x"}}'}
    )
    request = ModelRequest(
        task_kind="chat", prompt="list /x", output_mode="tool_intent", tools=DEFINITIONS
    )
    response = adapter._invoke_openai_compatible(request, force_json=False)
    assert json.loads(response.output_text)["intent"] == "machine.list_directory"


def test_a_turn_that_offered_no_tools_never_dispatches(_post) -> None:
    """The guard that matters. With no offered set, the name check cannot reject anything.

    A user pasting JSON that happens to look like a call must come back as text.
    """

    adapter = _adapter(base_url="https://api.openai.com")
    _post["reply"] = _reply({"content": '{"intent": "machine.list_directory", "arguments": {}}'})
    request = ModelRequest(task_kind="chat", prompt="what is this json", tools=())
    response = adapter._invoke_openai_compatible(request, force_json=False)
    assert response.output_text == '{"intent": "machine.list_directory", "arguments": {}}'


def test_a_cloud_tool_turn_gets_room_to_reason(_post) -> None:
    """The measured cloud-drop cause.

    `_max_output_tokens` gives a `tool_intent` turn 700 tokens. The Ollama lane then adds a
    2048-token thinking reserve; the cloud lane never did. A reasoning model spent the 700 on
    chain-of-thought and the reply came back `finish_reason: "length"` with no `tool_calls`,
    which surfaced to the user as "malformed provider response".

    Measured against nvidia/nemotron-3-nano-30b-a3b:free — 3/5 phrasings dispatched at the old
    budget, 5/5 with this reserve. Same model, same prompts, only the room changed.
    """

    adapter = _adapter(base_url="https://openrouter.ai/api")
    _post["reply"] = _reply({"tool_calls": [_native_call()]})
    request = ModelRequest(
        task_kind="tool_intent",
        prompt="list /x",
        output_mode="tool_intent",
        tools=DEFINITIONS,
        max_output_tokens=700,
    )
    with runtime_flags.override("cloud_tool_reasoning_reserve", True):
        adapter._invoke_openai_compatible(request, force_json=False)
    assert _post["payload"]["max_tokens"] >= 3000


def test_an_ordinary_chat_turn_keeps_the_callers_budget(_post) -> None:
    """On a chat turn the budget genuinely describes the answer wanted; do not inflate it."""

    adapter = _adapter(base_url="https://openrouter.ai/api")
    _post["reply"] = _reply({"content": "hello"})
    request = ModelRequest(
        task_kind="chat", prompt="hi", output_mode="plain_text", max_output_tokens=240
    )
    with runtime_flags.override("cloud_tool_reasoning_reserve", True):
        adapter._invoke_openai_compatible(request, force_json=False)
    assert _post["payload"]["max_tokens"] == 240


def test_a_tool_turn_carrying_no_tools_keeps_the_callers_budget(_post) -> None:
    adapter = _adapter(base_url="https://openrouter.ai/api")
    _post["reply"] = _reply({"content": "hello"})
    request = ModelRequest(
        task_kind="chat", prompt="hi", output_mode="tool_intent", tools=(), max_output_tokens=700
    )
    with runtime_flags.override("cloud_tool_reasoning_reserve", True):
        adapter._invoke_openai_compatible(request, force_json=False)
    assert _post["payload"]["max_tokens"] == 700


def test_an_ordinary_turn_carrying_the_catalog_gets_room_to_call(_post) -> None:
    """The budget follows the tools, not the label the classifier put on the turn.

    `core/memory_first_router.py` attaches the full catalog with `tool_choice: "auto"` on a
    `plain_text` turn under the `plain_text_tool_catalog` flag. Keyed on the label, that turn went
    out at 240 tokens — against a measurement where 512 already ends `finish_reason: "length"` with
    no call. The model may still just talk; `max_tokens` is a ceiling and only emitted tokens are
    billed, so this is room to call, not spend.
    """

    adapter = _adapter(base_url="https://openrouter.ai/api")
    # `_native_tools` withholds native schemas unless the mode is `tool_intent`, so on this lane the
    # catalog is prose and the call comes back as prose — the shape the repair path exists for.
    _post["reply"] = _reply(
        {"content": '{"intent": "machine.list_directory", "arguments": {"path": "/x"}}'}
    )
    request = ModelRequest(
        task_kind="chat",
        prompt="audit the skills in there",
        output_mode="plain_text",
        tools=DEFINITIONS,
        max_output_tokens=240,
    )
    with runtime_flags.override("cloud_tool_reasoning_reserve", True):
        adapter._invoke_openai_compatible(request, force_json=False)
    assert _post["payload"]["max_tokens"] >= 3000


def test_a_caller_budget_above_the_floor_is_not_lowered(_post) -> None:
    adapter = _adapter(base_url="https://openrouter.ai/api")
    _post["reply"] = _reply({"tool_calls": [_native_call()]})
    request = ModelRequest(
        task_kind="tool_intent",
        prompt="list /x",
        output_mode="tool_intent",
        tools=DEFINITIONS,
        max_output_tokens=8000,
    )
    with runtime_flags.override("cloud_tool_reasoning_reserve", True):
        adapter._invoke_openai_compatible(request, force_json=False)
    assert _post["payload"]["max_tokens"] == 8000


def test_the_reserve_can_be_switched_off(_post) -> None:
    adapter = _adapter(base_url="https://openrouter.ai/api")
    _post["reply"] = _reply({"tool_calls": [_native_call()]})
    request = ModelRequest(
        task_kind="tool_intent",
        prompt="list /x",
        output_mode="tool_intent",
        tools=DEFINITIONS,
        max_output_tokens=700,
    )
    with runtime_flags.override("cloud_tool_reasoning_reserve", False):
        adapter._invoke_openai_compatible(request, force_json=False)
    assert _post["payload"]["max_tokens"] == 700


def test_a_plain_text_turn_is_not_scanned_for_tool_calls(_post) -> None:
    """output_mode must also gate recovery: a chat turn is not a tool turn.

    The assertion compares against the exact bytes the provider sent. Checking only that the
    reply "looks like JSON" would pass either way, since a recovered call is also JSON — a
    weaker assertion here let a sabotage of this very gate go undetected once.
    """

    # A preamble makes the two outcomes distinguishable. Without it the raw content and the
    # canonical recovered call serialise to the same bytes and the assertion proves nothing.
    raw = 'Here you go: {"intent": "machine.list_directory", "arguments": {"path": "/x"}}'
    adapter = _adapter(base_url="https://api.openai.com")
    _post["reply"] = _reply({"content": raw})
    request = ModelRequest(
        task_kind="chat", prompt="echo that", output_mode="plain_text", tools=DEFINITIONS
    )
    response = adapter._invoke_openai_compatible(request, force_json=False)
    assert response.output_text == raw


# --------------------------------------------------------------------------------------
# Capability by probe. Only loopback servers are asked, because a probe is a real request.
# --------------------------------------------------------------------------------------


class _ProbeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


def _probe_adapter(monkeypatch, *, base_url: str, status: int | None, calls: list):
    """An adapter whose loopback probe meets a server returning `status` (None = unreachable)."""

    import adapters.openai_compatible_adapter as mod

    mod._NATIVE_TOOL_PROBE_CACHE.clear()

    def fake_post(url, **kwargs):
        calls.append((url, kwargs.get("json", {})))
        if status is None:
            raise OSError("connection refused")
        return _ProbeResponse(status)

    monkeypatch.setattr(mod.requests, "post", fake_post)
    return _adapter(base_url=base_url, metadata={"tool_support": ["structured_json"]})


def test_a_local_server_that_accepts_tools_is_measured_not_assumed(monkeypatch) -> None:
    calls: list = []
    adapter = _probe_adapter(monkeypatch, base_url="http://127.0.0.1:8080", status=200, calls=calls)

    assert adapter._supports_native_tools() is True
    assert len(calls) == 1, "the loopback server should have been asked exactly once"
    assert "tools" in calls[0][1], "the probe must actually carry a tool to be a probe"
    assert calls[0][1].get("max_tokens") == 1, "a probe must not generate real output"


def test_a_local_server_that_rejects_tools_is_believed(monkeypatch) -> None:
    """400/422 is the server saying it does not understand `tools`. That is real evidence."""

    for status in (400, 422):
        calls: list = []
        adapter = _probe_adapter(monkeypatch, base_url="http://127.0.0.1:8080", status=status, calls=calls)
        assert adapter._supports_native_tools() is False, f"status {status} should demote the lane"


def test_a_probe_result_is_cached_rather_than_repeated_every_turn(monkeypatch) -> None:
    calls: list = []
    adapter = _probe_adapter(monkeypatch, base_url="http://127.0.0.1:8080", status=200, calls=calls)

    for _ in range(4):
        adapter._supports_native_tools()

    assert len(calls) == 1, "capability was re-probed on later turns"


def test_a_server_error_is_not_evidence_about_tool_support(monkeypatch) -> None:
    """A 500 or a rate limit says nothing about capability, so it must not be cached as a negative."""

    calls: list = []
    adapter = _probe_adapter(monkeypatch, base_url="http://127.0.0.1:8080", status=503, calls=calls)

    assert adapter._supports_native_tools() is True, "a 503 was read as 'no tool support'"


def test_an_unreachable_local_server_does_not_lose_the_lane_its_tools(monkeypatch) -> None:
    calls: list = []
    adapter = _probe_adapter(monkeypatch, base_url="http://127.0.0.1:8080", status=None, calls=calls)

    assert adapter._supports_native_tools() is True


def test_a_remote_lane_is_never_probed_because_a_probe_costs_money(monkeypatch) -> None:
    """The rule that keeps routing from spending the user's money to answer a capability question."""

    calls: list = []
    adapter = _probe_adapter(monkeypatch, base_url="https://api.openai.com", status=200, calls=calls)

    assert adapter._supports_native_tools() is True
    assert calls == [], "a remote provider was billed for a capability probe"
