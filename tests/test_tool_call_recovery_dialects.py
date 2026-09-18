"""P0 tool-call recovery: one canonical resolution for every local-model tool dialect.

A valid tool intent from a local model must either execute exactly once or end in an honest
typed failure — it must not disappear because the one generated call was malformed. The RED
fixtures here are the actual failure shapes measured live: the Qwen/Hermes XML call VOOL
stripped on 2026-07-31 (`core/tool_call_dialects.py` docstring), the qwen3-coder XML drift
Ollama's own parser 500s on (ollama/ollama#17276, #16383, #14834 — stray close tags, a
`<parameter>` with no `<function>` wrapper, unexpected EOF), and Gemma's pythonic
```tool_code``` fence (no parser existed anywhere in the repo for it).

Contract under test — `core.tool_call_recovery.resolve_tool_calls`:
  * normalizes ChatML-style bare JSON, Hermes `<tool_call>{json}</tool_call>`, Qwen XML
    (`arg_key`/`arg_value` and `<function=name><parameter=k>v</parameter>` coder form) and
    Gemma fenced `tool_code` into the one canonical `CloudToolCall`;
  * validates name, arguments, schema, call id and parallel-call structure through the same
    single authority the native lanes use (`parse_native_tool_calls`);
  * applies exactly ONE bounded deterministic repair for recoverable syntax/schema defects;
  * never invents a missing required argument and never executes an ambiguous call;
  * mints deterministic call ids so re-resolving the same output cannot mint new identities.
"""
from __future__ import annotations

import pytest

from core.cloud_tool_call_contract import build_cloud_tool_definitions
from core.tool_call_recovery import MAX_REPAIR_ATTEMPTS, resolve_tool_calls

TOOLS = build_cloud_tool_definitions(
    [
        {
            "intent": "web.live_value",
            "description": "Look up a live market value.",
            "arguments": {"query": "string"},
        },
        {
            "intent": "sandbox.run_command",
            "description": "Run a bounded command.",
            "arguments": {"command": "string"},
        },
        {
            "intent": "machine.read_file",
            "description": "Read a workspace file.",
            "arguments": {"path": "string", "max_lines": {"type": "integer", "optional": True}},
        },
    ]
)


def _resolve(text: str, *, raw_native_calls=None):
    return resolve_tool_calls(content=text, raw_native_calls=raw_native_calls, definitions=TOOLS)


# --- the four dialects, well-formed: all must parse to the same canonical call ----------------


def test_chatml_bare_json_object_parses() -> None:
    resolution = _resolve('{"name": "web.live_value", "arguments": {"query": "gold price"}}')
    assert resolution.state == "parsed"
    assert resolution.calls[0].intent == "web.live_value"
    assert resolution.calls[0].arguments == {"query": "gold price"}


def test_hermes_json_in_xml_parses() -> None:
    resolution = _resolve(
        '<tool_call>{"name": "web.live_value", "arguments": {"query": "ETH price"}}</tool_call>'
    )
    assert resolution.state == "parsed"
    assert resolution.calls[0].intent == "web.live_value"
    assert resolution.calls[0].arguments == {"query": "ETH price"}


def test_qwen_arg_key_arg_value_parses() -> None:
    # The verbatim 2026-07-31 shape, including the stray leading close tag.
    resolution = _resolve(
        "</think><tool_call>machine.read_file"
        "<arg_key>path</arg_key><arg_value>api/apache/handlers.py</arg_value>"
        "<arg_key>max_lines</arg_key><arg_value>200</arg_value></tool_call>"
    )
    assert resolution.state == "parsed"
    assert resolution.calls[0].intent == "machine.read_file"
    assert resolution.calls[0].arguments == {"path": "api/apache/handlers.py", "max_lines": 200}


def test_qwen_coder_function_parameter_xml_parses() -> None:
    resolution = _resolve(
        "<tool_call><function=web.live_value>"
        "<parameter=query>gold price in EUR</parameter>"
        "</function></tool_call>"
    )
    assert resolution.state == "parsed"
    assert resolution.calls[0].intent == "web.live_value"
    assert resolution.calls[0].arguments == {"query": "gold price in EUR"}


def test_gemma_tool_code_fence_parses() -> None:
    resolution = _resolve('```tool_code\nweb.live_value(query="ETH in USD")\n```')
    assert resolution.state == "parsed"
    assert resolution.calls[0].intent == "web.live_value"
    assert resolution.calls[0].arguments == {"query": "ETH in USD"}


def test_gemma_print_wrapped_call_parses() -> None:
    resolution = _resolve('```tool_code\nprint(web.live_value(query="gold"))\n```')
    assert resolution.state == "parsed"
    assert resolution.calls[0].arguments == {"query": "gold"}


def test_provider_encoded_name_resolves_to_runtime_intent() -> None:
    native = next(d.name for d in TOOLS if d.intent == "web.live_value")
    resolution = _resolve(
        f'<tool_call>{{"name": "{native}", "arguments": {{"query": "gold"}}}}</tool_call>'
    )
    assert resolution.state == "parsed"
    assert resolution.calls[0].intent == "web.live_value"


# --- parallel calls ---------------------------------------------------------------------------


def test_parallel_hermes_blocks_all_survive_in_order() -> None:
    resolution = _resolve(
        '<tool_call>{"name": "web.live_value", "arguments": {"query": "gold price"}}</tool_call>\n'
        '<tool_call>{"name": "web.live_value", "arguments": {"query": "ETH price"}}</tool_call>'
    )
    assert resolution.state == "parsed"
    assert [c.arguments["query"] for c in resolution.calls] == ["gold price", "ETH price"]


def test_gemma_list_of_calls_all_survive() -> None:
    resolution = _resolve(
        '```tool_code\n[web.live_value(query="gold"), sandbox.run_command(command="pwd")]\n```'
    )
    assert resolution.state == "parsed"
    assert [c.intent for c in resolution.calls] == ["web.live_value", "sandbox.run_command"]


def test_identical_stuttered_blocks_deduplicate_to_one_call() -> None:
    """A model that stutters the same block twice expressed ONE intent; executing it twice
    would be a duplicated side effect, and rejecting it would lose a valid intent."""
    block = '<tool_call>{"name": "web.live_value", "arguments": {"query": "gold"}}</tool_call>'
    resolution = _resolve(block + "\n" + block)
    assert resolution.state == "parsed"
    assert len(resolution.calls) == 1


# --- the single bounded repair ----------------------------------------------------------------


def test_trailing_comma_arguments_are_repaired() -> None:
    resolution = _resolve(
        '<tool_call>{"name": "web.live_value", "arguments": {"query": "gold price",}}</tool_call>'
    )
    assert resolution.state == "repaired"
    assert resolution.repair_applied
    assert resolution.calls[0].arguments == {"query": "gold price"}


def test_python_literals_and_single_quotes_are_repaired() -> None:
    resolution = _resolve(
        "<tool_call>{'name': 'machine.read_file', 'arguments': {'path': 'x.py', 'max_lines': None}}</tool_call>"
    )
    assert resolution.state == "repaired"
    assert resolution.calls[0].intent == "machine.read_file"
    assert resolution.calls[0].arguments["path"] == "x.py"


def test_schema_typed_scalar_coercion_is_a_repair_not_an_invention() -> None:
    # max_lines is integer-typed; the model quoted it. The value is present — coercion is
    # deterministic and does not invent anything.
    resolution = _resolve(
        '<tool_call>{"name": "machine.read_file", "arguments": {"path": "x.py", "max_lines": "120"}}</tool_call>'
    )
    assert resolution.state == "repaired"
    assert resolution.calls[0].arguments == {"path": "x.py", "max_lines": 120}


def test_single_string_encoded_call_is_repaired_by_one_unwrap() -> None:
    """A JSON-string-encoded call (one extra json.dumps around the object) is a real local-model
    defect and needs exactly one unwrap — inside the single repair budget."""
    import json as _json

    call = {"name": "web.live_value", "arguments": {"query": "gold"}}
    body = _json.dumps(_json.dumps(call))  # the block body decodes to a STRING holding the call
    resolution = _resolve(f"<tool_call>{body}</tool_call>")
    assert resolution.state == "repaired"
    assert resolution.calls[0].arguments == {"query": "gold"}


def test_repair_is_bounded_to_exactly_one_attempt() -> None:
    """A doubly-string-encoded call needs two unwrap passes; the bound allows one. This input
    MUST stay rejected — an implementation that loops repairs until success would accept it."""
    import json as _json

    assert MAX_REPAIR_ATTEMPTS == 1
    call = {"name": "web.live_value", "arguments": {"query": "gold"}}
    body = _json.dumps(_json.dumps(_json.dumps(call)))
    resolution = _resolve(f"<tool_call>{body}</tool_call>")
    assert resolution.state == "rejected"
    assert resolution.calls == ()


def test_truncated_json_is_not_completed() -> None:
    """Truncation means the argument VALUE is incomplete; closing the braces would fabricate a
    value the model never finished writing."""
    resolution = _resolve('<tool_call>{"name": "web.live_value", "arguments": {"query": "gol</tool_call>')
    assert resolution.state == "rejected"
    assert resolution.rejection_kind == "malformed_arguments"
    assert resolution.calls == ()


# --- typed rejections: never invent, never execute ambiguity ----------------------------------


def test_missing_name_is_rejected_with_a_typed_kind() -> None:
    resolution = _resolve('<tool_call>{"arguments": {"query": "gold"}}</tool_call>')
    assert resolution.state == "rejected"
    assert resolution.rejection_kind == "missing_name"
    assert resolution.calls == ()


def test_unknown_tool_name_is_rejected() -> None:
    resolution = _resolve('<tool_call>{"name": "filesystem.delete_everything", "arguments": {}}</tool_call>')
    assert resolution.state == "rejected"
    assert resolution.rejection_kind == "unknown_tool_name"


def test_missing_required_argument_is_never_invented() -> None:
    resolution = _resolve('<tool_call>{"name": "web.live_value", "arguments": {}}</tool_call>')
    assert resolution.state == "rejected"
    assert resolution.rejection_kind in {"malformed_arguments", "schema_validation_failed"}
    assert resolution.calls == ()


def test_wrapper_name_conflicting_with_body_name_is_ambiguous() -> None:
    resolution = _resolve(
        '<function=sandbox.run_command>{"name": "web.live_value", "arguments": {"query": "gold"}}</function>'
    )
    assert resolution.state == "rejected"
    assert resolution.rejection_kind == "ambiguous_tool_call"
    assert resolution.calls == ()


def test_duplicate_native_call_id_is_rejected() -> None:
    native = next(d.name for d in TOOLS if d.intent == "web.live_value")
    raw = [
        {"id": "call_1", "type": "function", "function": {"name": native, "arguments": '{"query": "gold"}'}},
        {"id": "call_1", "type": "function", "function": {"name": native, "arguments": '{"query": "ETH"}'}},
    ]
    resolution = resolve_tool_calls(content="", raw_native_calls=raw, definitions=TOOLS)
    assert resolution.state == "rejected"
    assert resolution.rejection_kind == "duplicate_tool_call"


def test_a_fabricated_tool_result_is_not_a_call() -> None:
    """A model narrating a result it never received must not have that narration executed."""
    resolution = _resolve(
        '<function_results>{"name": "web.live_value", "arguments": {"query": "gold"}}</function_results>'
    )
    assert resolution.state == "none"
    assert resolution.calls == ()


def test_ordinary_prose_is_not_a_call() -> None:
    resolution = _resolve("Gold is a metal; ETH is a cryptocurrency. Ask me anything else.")
    assert resolution.state == "none"
    assert resolution.calls == ()


def test_prose_mentioning_a_tool_call_tag_is_not_a_call() -> None:
    resolution = _resolve("The <tool_call> wrapper is how some models emit calls.")
    assert resolution.state == "none"


# --- native-envelope repair -------------------------------------------------------------------


def test_malformed_native_arguments_are_repaired_once() -> None:
    native = next(d.name for d in TOOLS if d.intent == "web.live_value")
    raw = [
        {
            "id": "call_7",
            "type": "function",
            "function": {"name": native, "arguments": '{"query": "gold price",}'},
        }
    ]
    resolution = resolve_tool_calls(content="", raw_native_calls=raw, definitions=TOOLS)
    assert resolution.state == "repaired"
    assert resolution.calls[0].arguments == {"query": "gold price"}
    assert resolution.calls[0].call_id == "call_7", "repair must not re-mint an existing call id"


def test_native_envelope_missing_name_stays_rejected() -> None:
    raw = [{"id": "call_9", "type": "function", "function": {"arguments": '{"query": "gold"}'}}]
    resolution = resolve_tool_calls(content="", raw_native_calls=raw, definitions=TOOLS)
    assert resolution.state == "rejected"
    assert resolution.rejection_kind in {"missing_name", "unknown_tool_name"}


# --- idempotency ------------------------------------------------------------------------------


def test_resolving_the_same_text_twice_mints_identical_call_ids() -> None:
    text = (
        '<tool_call>{"name": "web.live_value", "arguments": {"query": "gold"}}</tool_call>\n'
        '<tool_call>{"name": "web.live_value", "arguments": {"query": "ETH"}}</tool_call>'
    )
    first = _resolve(text)
    second = _resolve(text)
    assert [c.call_id for c in first.calls] == [c.call_id for c in second.calls]
    assert all(c.call_id for c in first.calls), "every recovered call carries a stable id"
    assert len({c.call_id for c in first.calls}) == len(first.calls)


def test_repair_preserves_the_original_call_id() -> None:
    native = next(d.name for d in TOOLS if d.intent == "web.live_value")
    raw = [{"id": "call_keep", "function": {"name": native, "arguments": "{'query': 'gold'}"}}]
    resolution = resolve_tool_calls(content="", raw_native_calls=raw, definitions=TOOLS)
    assert resolution.state == "repaired"
    assert resolution.calls[0].call_id == "call_keep"


# --- markup is authoritative over narration ---------------------------------------------------


def test_explicit_markup_wins_over_bare_json_narration() -> None:
    resolution = _resolve(
        '<tool_call>{"name": "web.live_value", "arguments": {"query": "gold"}}</tool_call>\n'
        'That corresponds to {"name": "sandbox.run_command", "arguments": {"command": "rm -rf /"}} in other syntaxes.'
    )
    assert resolution.state == "parsed"
    assert [c.intent for c in resolution.calls] == ["web.live_value"]


def test_valid_native_envelope_wins_over_content() -> None:
    native = next(d.name for d in TOOLS if d.intent == "web.live_value")
    raw = [{"id": "n1", "type": "function", "function": {"name": native, "arguments": '{"query": "gold"}'}}]
    resolution = resolve_tool_calls(
        content='<tool_call>{"name": "sandbox.run_command", "arguments": {"command": "pwd"}}</tool_call>',
        raw_native_calls=raw,
        definitions=TOOLS,
    )
    assert resolution.state == "parsed"
    assert resolution.dialect == "native"
    assert [c.intent for c in resolution.calls] == ["web.live_value"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
