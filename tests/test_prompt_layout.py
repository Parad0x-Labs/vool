"""Cache-friendly request layout, the lead-run breakpoint and the router session key.

Every request here is a REAL request VOOL logged on the everyday sets (tests/fixtures/
prompt_layout_logged_requests.json): the layout flags move whole messages, so with them off the wire
must be byte-identical to the logged one, and with them on no byte of any message may change.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import experiment_switches as runtime_flags
from core import runtime_paths
from core.internal_message_schema import TURN_DIRECTIVES_HEADER, is_turn_directives_message
from core.prompt_cache import (
    CachePlan,
    apply_cache_breakpoints,
    stable_lead_run_end,
    unmarked_messages,
)
from core.prompt_layout import apply_prompt_layout, context_last, stable_grounding_first
from storage.model_provider_manifest import ModelProviderManifest

FIXTURE = Path(__file__).parent / "fixtures" / "prompt_layout_logged_requests.json"
LOGGED: list[dict[str, Any]] = json.loads(FIXTURE.read_text())
UNMARKED = [r for r in LOGGED if not r["shape"].startswith("marked:")]
MAIN = [r for r in UNMARKED if "turn_context" in r["shape"] and "evidence_bootstrap" in r["shape"]]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def _all_flags_on():
    return [runtime_flags.override(n, True) for n in ("prompt_layout_context_last", "prompt_layout_stable_grounding_first")]


def _manifest(provider: str, model: str, base_url: str) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider, model_name=model, source_type="http", adapter_type="openai_compatible",
        license_name="Apache-2.0", license_reference="https://www.apache.org/licenses/LICENSE-2.0",
        weight_location="external", redistribution_allowed=True, runtime_dependency="openai-compatible",
        capabilities=["summarize"], runtime_config={"base_url": base_url, "api_path": "/v1/chat/completions"},
        metadata={"runtime_family": "openai-compatible"}, enabled=True,
    )


def _openrouter(model: str = "z-ai/glm-5.3-flashx") -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(_manifest("openrouter-byok", model, "https://openrouter.ai/api"))


def _request(messages: list[dict[str, Any]], **extra: Any) -> ModelRequest:
    return ModelRequest(task_kind="chat", prompt="q", messages=copy.deepcopy(messages), max_output_tokens=64,
                        metadata={}, **extra)


def test_the_fixture_is_real_and_covers_every_main_shape() -> None:
    assert len(UNMARKED) >= 25 and len(MAIN) >= 8
    shapes = {r["shape"] for r in MAIN}
    assert any(s.startswith("persona,records_absence,turn_context+clock,evidence_bootstrap,retrieved_context") for s in shapes)
    assert any(s.startswith("persona,user_turn,assistant,") for s in shapes)


# ---- flags off: nothing changes --------------------------------------------------------------------

@pytest.mark.parametrize("record", UNMARKED, ids=[f"{i}" for i in range(len(UNMARKED))])
def test_flags_off_the_layout_is_the_same_list_object(record) -> None:
    messages = record["messages"]
    assert apply_prompt_layout(messages) is messages


@pytest.mark.parametrize("record", UNMARKED, ids=[f"{i}" for i in range(len(UNMARKED))])
def test_flags_off_the_adapter_wire_is_byte_identical_to_the_logged_request(record) -> None:
    payload = _openrouter()._build_openai_payload(_request(record["messages"]), force_json=False, stream=False)
    assert json.dumps(payload["messages"], sort_keys=True) == json.dumps(record["messages"], sort_keys=True)
    assert "session_id" not in payload


def test_every_switch_is_off_by_default_and_a_typo_stays_off(monkeypatch) -> None:
    assert set(runtime_flags.names()) == {"prompt_layout_context_last", "prompt_layout_stable_grounding_first",
                                          "prompt_cache_lead_run_marker", "router_session_key"}
    for name in runtime_flags.names():
        assert runtime_flags.switch_enabled(name) is False
    monkeypatch.setenv("VOOL_ROUTER_SESSION_KEY", "ture")
    assert runtime_flags.switch_enabled("router_session_key") is False
    monkeypatch.setenv("VOOL_ROUTER_SESSION_KEY", "1")
    assert runtime_flags.switch_enabled("router_session_key") is True
    # the shipped flag inventory is untouched
    from core import runtime_flags as shipped
    assert not {"prompt_layout_context_last", "router_session_key"} & {f.name for f in shipped.all_flags()}


# ---- flags on: whole messages move, no byte changes ------------------------------------------------

@pytest.mark.parametrize("record", UNMARKED, ids=[f"{i}" for i in range(len(UNMARKED))])
def test_flags_on_the_same_messages_come_back_in_another_order(record) -> None:
    ctxs = _all_flags_on()
    for c in ctxs:
        c.__enter__()
    try:
        out = apply_prompt_layout(record["messages"])
    finally:
        for c in reversed(ctxs):
            c.__exit__(None, None, None)
    key = lambda m: json.dumps(m, sort_keys=True)  # noqa: E731
    assert sorted(map(key, out)) == sorted(map(key, record["messages"]))
    assert out[-1] == record["messages"][-1]  # the current turn stays last
    assert out[0] == record["messages"][0]    # and the persona stays first


def test_the_per_turn_context_moves_behind_the_evidence_run_and_only_that() -> None:
    record = next(r for r in MAIN if r["shape"].startswith("persona,records_absence,turn_context+clock,evidence_bootstrap,retrieved_context,user_turn"))
    messages = record["messages"]
    out = context_last(messages)
    assert [is_turn_directives_message(m) for m in out] == [False, False, False, False, True, False]
    assert out[:2] == messages[:2] and out[-1] == messages[-1]
    assert out is not messages and messages[2]["content"].startswith(TURN_DIRECTIVES_HEADER)  # input untouched


def test_stable_grounding_goes_up_behind_the_persona_and_the_history_stays_in_order() -> None:
    record = next(r for r in MAIN if r["shape"].startswith("persona,user_turn,assistant,records_absence,turn_context+clock"))
    messages = record["messages"]
    out = stable_grounding_first(messages)
    heads = [_text(m)[:20] for m in out]
    assert heads[0].startswith("You are VOOL")
    assert heads[1].startswith("<records_absence")
    assert heads[2].startswith("<retrieved_context>")
    # the chat history keeps its order behind them
    hist = [m for m in out if m["role"] in ("user", "assistant") and not _text(m).startswith("Relevant context")]
    assert [m["content"] for m in hist][:-1] == [m["content"] for m in messages[1:3]]


def test_a_tool_loop_is_left_exactly_as_it_was() -> None:
    loops = [r for r in UNMARKED if r["shape"].startswith("toolloop-persona")]
    assert loops
    ctxs = _all_flags_on()
    for c in ctxs:
        c.__enter__()
    try:
        for record in loops:
            assert apply_prompt_layout(record["messages"]) is record["messages"]
    finally:
        for c in reversed(ctxs):
            c.__exit__(None, None, None)


def test_a_judgement_call_is_left_exactly_as_it_was() -> None:
    singles = [r for r in UNMARKED if len(r["messages"]) == 1]
    assert singles
    with runtime_flags.override("prompt_layout_context_last", True), runtime_flags.override("prompt_layout_stable_grounding_first", True):
        for record in singles:
            assert apply_prompt_layout(record["messages"]) is record["messages"]


def test_the_wire_with_layout_on_carries_the_same_bytes_and_the_evidence_seal_still_binds() -> None:
    """Through the real adapter, with a real seal: every evidence unit still has its carrier."""
    from core.bootstrap_context import seal_request_evidence

    record = next(r for r in MAIN if r["shape"].startswith("persona,records_absence,turn_context+clock,evidence_bootstrap,retrieved_context,user_turn"))
    msgs = record["messages"]
    ev_idx = [i for i, m in enumerate(msgs) if _text(m).startswith("Relevant context and evidence")]
    ctx_idx = [i for i, m in enumerate(msgs) if is_turn_directives_message(m)]
    clock_text = next(t for t in [_text(msgs[ctx_idx[0]])] if "The current date and time is" in t)
    start = clock_text.index("The current date and time is")
    clock = clock_text[start:clock_text.index(").", start) + 2]
    source: dict[str, Any] = {}
    seal_request_evidence(
        source, session_id="chat-a", question="q", turn_id="t1",
        messages=[{"role": m["role"], "content": m["content"]} for m in msgs],
        evidence_texts=[msgs[ev_idx[0]]["content"]], capsule_text="", evidence_message_indices=ev_idx + ctx_idx,
        reference_clock={"source": "runtime_clock", "date": "2026-11-19", "text": clock},
    )
    sealed = source["admitted_capsule_evidence"]
    assert sealed.get("reference_clock")

    def _wire(on: bool):
        req = ModelRequest(task_kind="chat", prompt="q", messages=copy.deepcopy(msgs), max_output_tokens=64,
                           metadata={"admitted_capsule_evidence": copy.deepcopy(sealed),
                                     "request_evidence_message_indices": ev_idx + ctx_idx})
        if on:
            ctxs = _all_flags_on()
            for c in ctxs:
                c.__enter__()
        try:
            payload = _openrouter()._build_openai_payload(req, force_json=False, stream=False)
        finally:
            if on:
                for c in reversed(ctxs):
                    c.__exit__(None, None, None)
        return payload, req.metadata["admitted_capsule_evidence"]

    off_payload, off_record = _wire(False)
    on_payload, on_record = _wire(True)
    assert [m["content"] for m in on_payload["messages"]] != [m["content"] for m in off_payload["messages"]]
    assert sorted(m["content"] for m in on_payload["messages"]) == sorted(m["content"] for m in off_payload["messages"])
    assert on_record["evidence_texts"] == off_record["evidence_texts"] == [msgs[ev_idx[0]]["content"]]
    assert on_record["reference_clock"] == off_record["reference_clock"] and on_record["reference_clock"]


# ---- the lead-run breakpoint ---------------------------------------------------------------------

CLAUDE = CachePlan(True, "anthropic_on_openrouter", 1.25)


def test_the_default_marker_is_unchanged_with_the_lead_run_flag_off() -> None:
    messages = copy.deepcopy(MAIN[0]["messages"])
    out, placed = apply_cache_breakpoints(messages, CLAUDE)
    assert placed == 1 and out[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert all(not isinstance(m["content"], list) for m in out[1:])


def test_a_lead_run_under_the_minimum_is_not_marked() -> None:
    """The logged persona alone is about 420 Haiku tokens: under 512, so no breakpoint is paid for."""
    record = next(r for r in MAIN if r["shape"].startswith("persona,user_turn,assistant,turn_context"))
    with runtime_flags.override("prompt_cache_lead_run_marker", True):
        assert stable_lead_run_end(record["messages"]) is None
        out, placed = apply_cache_breakpoints(record["messages"], CLAUDE)
    assert placed == 0 and out is record["messages"]


def test_with_the_layout_on_the_run_reaches_the_minimum_and_the_marker_sits_at_its_end() -> None:
    record = next(r for r in MAIN if r["shape"].startswith("persona,user_turn,assistant,records_absence,turn_context+clock,evidence_bootstrap,retrieved_context"))
    with runtime_flags.override("prompt_layout_stable_grounding_first", True):
        laid = apply_prompt_layout(record["messages"])
    with runtime_flags.override("prompt_cache_lead_run_marker", True):
        end = stable_lead_run_end(laid)
        out, placed = apply_cache_breakpoints(laid, CLAUDE)
    assert end == 2 and placed == 1
    assert out[2]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert _text(out[2]) == _text(laid[2])  # same text
    assert all(not isinstance(m["content"], list) for i, m in enumerate(out) if i != 2)
    assert sum(len(_text(m)) for m in laid[:3]) / 3.0 >= 512


def test_a_single_message_judgement_call_is_never_marked() -> None:
    single = next(r for r in UNMARKED if len(r["messages"]) == 1)
    with runtime_flags.override("prompt_cache_lead_run_marker", True):
        out, placed = apply_cache_breakpoints(single["messages"], CLAUDE)
    assert placed == 0 and out is single["messages"]


def test_unmarking_restores_the_original_messages_exactly() -> None:
    record = next(r for r in MAIN if r["shape"].startswith("persona,records_absence,turn_context+clock,evidence_bootstrap,retrieved_context"))
    marked, placed = apply_cache_breakpoints(record["messages"], CLAUDE)
    assert placed == 1 and marked != record["messages"]
    assert unmarked_messages(marked) == record["messages"]
    assert unmarked_messages(record["messages"]) is record["messages"]


def test_a_marked_request_still_binds_its_evidence_at_the_gateway() -> None:
    """The seal is made on the unmarked messages; the gateway must read it against the unmarked view."""
    from core.bootstrap_context import seal_request_evidence
    from core.provider_invocation_gateway import _finalize_request_evidence_payload

    import adapters.openai_compatible_adapter as adapter_module

    msgs = [
        {"role": "system", "content": "You are VOOL. persona"},
        {"role": "user", "content": "Relevant context and evidence:\nline one\nline two"},
        {"role": "system", "content": "<retrieved_context>\nDistilled local facts. my dentist is Dr Ferro\n</retrieved_context>"},
        {"role": "user", "content": "who is my dentist?"},
    ]
    source: dict[str, Any] = {}
    seal_request_evidence(source, session_id="c", question="who is my dentist?", turn_id="t", messages=msgs,
                          evidence_texts=[msgs[1]["content"], msgs[2]["content"]], capsule_text=msgs[2]["content"],
                          evidence_message_indices=[1, 2])
    for mark in (False, True):
        req = ModelRequest(task_kind="chat", prompt="q", metadata={"admitted_capsule_evidence": copy.deepcopy(source["admitted_capsule_evidence"])})
        stamped = [{**m, "_vool_evidence_support": i in (1, 2)} for i, m in enumerate(msgs)]
        final = adapter_module._finalize_provider_evidence_messages(req, stamped)
        if mark:
            final, _ = apply_cache_breakpoints(final, CLAUDE)
        payload = {"messages": final}
        _finalize_request_evidence_payload(req, payload)
        record = req.metadata["admitted_capsule_evidence"]
        assert record["evidence_texts"] == [msgs[1]["content"], msgs[2]["content"]], mark
        assert record["text"] == msgs[2]["content"], mark
        if mark:
            assert payload["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}  # the wire keeps the marker


# ---- the router session key ----------------------------------------------------------------------

def test_the_session_key_is_off_by_default_and_never_on_other_lanes() -> None:
    from core.router_session_key import session_key_for_request

    req = _request(UNMARKED[0]["messages"], context={"chat_id": "chat-1"})
    assert session_key_for_request(req, openrouter_lane=True) == ""
    with runtime_flags.override("router_session_key", True):
        assert session_key_for_request(req, openrouter_lane=False) == ""
        assert len(session_key_for_request(req, openrouter_lane=True)) == 32


def test_the_session_key_is_opaque_stable_across_restarts_and_per_chat(tmp_path) -> None:
    import core.router_session_key as rsk

    with runtime_flags.override("router_session_key", True):
        a1 = rsk.router_session_key("chat-1")
        a2 = rsk.router_session_key("chat-1")
        b = rsk.router_session_key("chat-2")
        assert a1 == a2 and a1 != b
        assert "chat-1" not in a1 and all(c in "0123456789abcdef" for c in a1) and len(a1) == 32
        rsk._CACHE.clear()  # a restart: the secret is read back from disk
        assert rsk.router_session_key("chat-1") == a1
        secret = Path(runtime_paths.active_data_dir()) / "router_session_secret"
        assert secret.exists() and oct(secret.stat().st_mode & 0o777) == "0o600" and secret.stat().st_size == 32
        rsk.forget_install_secret()
        assert rsk.router_session_key("chat-1") != a1  # re-keyed: old values are unreachable


def test_a_request_with_no_chat_gets_no_key_and_a_private_chat_gets_none() -> None:
    from core.private_chat import PRIVATE_TURN_KEY
    from core.router_session_key import session_key_for_request

    with runtime_flags.override("router_session_key", True):
        assert session_key_for_request(_request(UNMARKED[0]["messages"]), openrouter_lane=True) == ""
        assert session_key_for_request(_request(UNMARKED[0]["messages"], context={"chat_id": "c"}), openrouter_lane=True)
        private = _request(UNMARKED[0]["messages"], context={"chat_id": "c", PRIVATE_TURN_KEY: True})
        assert session_key_for_request(private, openrouter_lane=True) == ""


def test_the_adapter_sends_session_id_on_openrouter_only_and_only_with_the_flag() -> None:
    messages = MAIN[0]["messages"]
    with runtime_flags.override("router_session_key", True):
        on = _openrouter()._build_openai_payload(_request(messages, context={"chat_id": "chat-9"}), force_json=False, stream=False)
        other = OpenAICompatibleAdapter(_manifest("custom", "some-model", "https://api.example.test"))
        off_lane = other._build_openai_payload(_request(messages, context={"chat_id": "chat-9"}), force_json=False, stream=False)
        nochat = _openrouter()._build_openai_payload(_request(messages), force_json=False, stream=False)
    assert len(on["session_id"]) == 32 and "chat-9" not in json.dumps(on)
    assert "session_id" not in off_lane and "session_id" not in nochat
    flag_off = _openrouter()._build_openai_payload(_request(messages, context={"chat_id": "chat-9"}), force_json=False, stream=False)
    assert "session_id" not in flag_off
    assert {k: v for k, v in on.items() if k != "session_id"} == flag_off
