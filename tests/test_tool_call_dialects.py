"""A tool call in a foreign dialect is still a tool call.

Measured 2026-07-31 on `laguna-s-2.1:free` against a real repository file. The model emitted a
correct intent in the Qwen/Hermes XML dialect:

    </think><tool_call>read<arg_key>path</arg_key><arg_value>api/apache/...</arg_value></tool_call>

`core/model_output_guard.py` classifies those tags as tokenizer junk and strips the whole block
(`_TOOL_BLOCK_RE`), so the call was deleted before anything read it and the turn ended at
"the model returned an invalid tool payload with no intent name".

The second-order failure is the one that matters. On the retry, the model — now holding no working
tools, still under instruction to read a file — FABRICATED the read: a `<function_results>` block
containing an invented Python module (`def liquefy_apache_repetition(apache_data: Dict[str, Any])`)
that exists nowhere in the repository, presented as tool output. The real file is a zstd compressor
built around a `LiquefyApacheRepetitionV1` class. VOOL refused it, correctly, but by then the cause
was two steps upstream: a model that cannot reach a tool invents the call, then invents the result.

Recognising the dialect is therefore a correctness fix, not a convenience.
"""
from __future__ import annotations

import pytest

from core.tool_call_dialects import looks_like_fabricated_tool_result, parse_text_tool_call

# Verbatim from the user's transcript, including the stray `</think>` the model leaked.
LAGUNA_VERBATIM = (
    "I'll read the file first.</think><tool_call>read<arg_key>path</arg_key>"
    "<arg_value>api/apache/liquefy_apache_repetition_v1.py</arg_value></tool_call>"
)


def test_the_exact_call_that_was_thrown_away_now_parses() -> None:
    parsed = parse_text_tool_call(LAGUNA_VERBATIM)

    assert parsed is not None, "the live failure case still yields nothing"
    assert parsed["intent"] == "read"
    assert parsed["arguments"] == {"path": "api/apache/liquefy_apache_repetition_v1.py"}


def test_a_json_bodied_block_parses() -> None:
    parsed = parse_text_tool_call(
        '<tool_call>{"name": "workspace.read_file", "arguments": {"path": "x.py"}}</tool_call>'
    )
    assert parsed == {"intent": "workspace.read_file", "arguments": {"path": "x.py"}}


def test_an_attribute_named_function_parses() -> None:
    parsed = parse_text_tool_call('<function=workspace.list_files>{"path": "."}</function>')
    assert parsed == {"intent": "workspace.list_files", "arguments": {"path": "."}}


def test_untyped_arguments_are_coerced_only_where_unambiguous() -> None:
    """The dialect carries no types; the contract layer downstream validates against a real schema.

    An integer argument must not fail validation purely for having arrived quoted -- but anything
    that is not plainly an int or a bool stays the string it was, so nothing is invented.
    """

    parsed = parse_text_tool_call(
        "<tool_call>workspace.read_file"
        "<arg_key>path</arg_key><arg_value>x.py</arg_value>"
        "<arg_key>max_lines</arg_key><arg_value>200</arg_value>"
        "<arg_key>verbatim</arg_key><arg_value>true</arg_value>"
        "</tool_call>"
    )
    assert parsed is not None
    assert parsed["arguments"] == {"path": "x.py", "max_lines": 200, "verbatim": True}


@pytest.mark.parametrize(
    "text",
    [
        "just a normal answer with no tags at all",
        "I could mention a <tool_call> in prose without meaning it",
        "here is some HTML: <function> is a word people write",
        "",
    ],
)
def test_prose_is_never_turned_into_an_execution(text: str) -> None:
    """None means 'no foreign call here', not 'this failed'.

    A false positive would be far worse than the bug being fixed: it would execute something the
    model never asked for. An unpaired or unnamed tag yields nothing.
    """

    assert parse_text_tool_call(text) is None


def test_an_unknown_intent_is_returned_not_resolved() -> None:
    """This module does not decide what is executable.

    It hands back a candidate; the existing contract layer resolves the name against registered
    intents and validates arguments, so a bogus name still fails exactly where it fails today.
    """

    parsed = parse_text_tool_call("<tool_call>not.a.real.intent<arg_key>a</arg_key><arg_value>b</arg_value></tool_call>")
    assert parsed == {"intent": "not.a.real.intent", "arguments": {"a": "b"}}


def test_a_fabricated_tool_result_is_distinguishable_from_a_malformed_call() -> None:
    """The two failures must not share a message.

    A dialect gap is fixable and worth retrying. A model narrating a result it never received is
    fabrication, and must never reach the user as evidence.
    """

    fabricated = (
        '<function_results>File: api/apache/liquefy_apache_repetition_v1.py</function_results>'
        '<result>{"path": "...", "content": ["import json", "import logging"]}</result>'
    )
    assert looks_like_fabricated_tool_result(fabricated) is True
    assert looks_like_fabricated_tool_result(LAGUNA_VERBATIM) is False
    assert looks_like_fabricated_tool_result("an ordinary answer") is False


# --------------------------------------------------------------------------------------
# Wiring: the parser is reached by the real normalizer, not just callable in isolation
# --------------------------------------------------------------------------------------


def test_the_real_normalizer_reaches_the_foreign_dialect() -> None:
    """`normalize_payload` is the seam every tool caller funnels through.

    It used to `json.loads` the model's text and return {} on failure, which is what made an
    XML-dialect model look tool-less. JSON is still attempted first, so this cannot change the
    behaviour of any model that already worked.
    """

    from core.execution.receipts import normalize_payload

    assert normalize_payload(LAGUNA_VERBATIM) == {
        "intent": "read",
        "arguments": {"path": "api/apache/liquefy_apache_repetition_v1.py"},
    }


def test_the_json_dialect_is_untouched() -> None:
    from core.execution.receipts import normalize_payload

    assert normalize_payload('{"intent": "workspace.read_file", "arguments": {"path": "x.py"}}') == {
        "intent": "workspace.read_file",
        "arguments": {"path": "x.py"},
    }
    assert normalize_payload({"intent": "workspace.list_files", "arguments": {}}) == {
        "intent": "workspace.list_files",
        "arguments": {},
    }


def test_prose_still_normalizes_to_nothing() -> None:
    """The refusal path must survive. Text that is not a call must not become one."""

    from core.execution.receipts import normalize_payload

    assert normalize_payload("this is just an ordinary answer") == {}
    assert normalize_payload("") == {}
