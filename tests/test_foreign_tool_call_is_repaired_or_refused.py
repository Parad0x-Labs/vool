"""A tool call the runtime does not speak must be repaired or refused. It must never be rendered.

Measured on the guard before this change, with the exact live failure:

    in   <tool>\\n{"name": "web.search", "arguments": {"query": "price of sol"}}\\n</tool>
    out  <tool>\\n\\n</tool>              <- the JSON went, the wrapper stayed
    in   <tool>{"name": "web.search", "arguments": {"query": "price of sol"}}</tool>
    out  unchanged, markers == []       <- not detected at all

Three separate holes, in sequence:

* `_TOOL_TAG_NAMES` listed `tool_call|tool_code|function_call|function_calls` and not the bare
  `tool`, `tools`, `invoke` or `tool_use` wrappers that models actually emit, so no rule matched
  the envelope;
* the one-line form escaped even the JSON scanner, because `_LINE_START_BRACE_RE` requires the `{`
  to open a line;
* the emptiness probe (`re.search(r"[A-Za-z0-9]", s)`) ran BEFORE the tag-stripping rules, so the
  letters of the word `tool` inside the surviving `<tool></tool>` answered "yes, real content
  remains" and the turn was delivered instead of failed.

The tags are ambiguous enough that stripping them on sight would be wrong — a document about XML
may legitimately contain `<tool>` — so they only count as an envelope when the tag carries a tool
call: a JSON body, or an attribute on the tag itself. That signature is what separates a wrapper
from a word, and the prose fixtures below are what hold the line.
"""
from __future__ import annotations

import pytest

from core.model_output_guard import foreign_markers, scrub_foreign_markers

LEAKED_ENVELOPES = (
    '<tool>\n{"name": "web.search", "arguments": {"query": "price of sol"}}\n</tool>',
    '<tool>{"name":"web.search","arguments":{"query":"price of sol"}}</tool>',
    '<tools>\n{"name": "web.search", "arguments": {"query": "x"}}\n</tools>',
    '<tool_use>{"name": "workspace.read_file", "arguments": {"path": "a.py"}}</tool_use>',
    '<toolcall>{"name": "web.search", "arguments": {}}</toolcall>',
    '<invoke name="web.search">\n{"query": "x"}\n</invoke>',
    '<tool_call>\n{"name": "web.search", "arguments": {"query": "x"}}\n</tool_call>',
)

# A wrapper's letters are not content. These must survive untouched — the tags above are ordinary
# English words and the guard must not eat a sentence that merely mentions them.
ORDINARY_PROSE = (
    "The tool I would use is a search engine.",
    "In XML you write <tool> to open a tool element and </tool> to close it.",
    "Use `<tool>` as the wrapper tag name in your schema.",
    "The config {\"a\": 1} goes in settings.",
    "Invoke the script with `python -m tools.build`.",
    "My toolkit is small: ripgrep, fd, and jq.",
    "The <tools> section of that spec lists every capability.",
)


@pytest.mark.parametrize("leaked", LEAKED_ENVELOPES)
def test_a_leaked_envelope_is_detected(leaked: str) -> None:
    """`foreign_markers` is the boolean that decides whether the guard runs at all."""

    assert foreign_markers(leaked), f"{leaked!r} was invisible to the guard"


@pytest.mark.parametrize("leaked", LEAKED_ENVELOPES)
def test_a_reply_that_is_only_a_tool_call_collapses_to_nothing(leaked: str) -> None:
    """Empty is the signal the contract turns into a failed turn and a failover."""

    assert scrub_foreign_markers(leaked) == ""


def test_the_wrapper_is_removed_with_its_body_not_left_behind() -> None:
    """The measured symptom: the JSON went and `<tool></tool>` was shown to the operator."""

    scrubbed = scrub_foreign_markers(
        'Here is the price.\n<tool>{"name":"web.search","arguments":{"query":"sol"}}</tool>'
    )
    assert scrubbed == "Here is the price."
    assert "<tool" not in scrubbed and "</tool" not in scrubbed


# A leaked call arrives bulleted, quoted or emphasised at least as often as bare, and the line
# anchor was `^[ \t]*\{` — none of these matched it. Found by measuring the guard rather than
# reading it. Widening the anchor is safe because the anchor was never the filter: `_tool_call_name`
# is, and it requires a tool-name key plus an arguments dict.
DECORATED_LEAKS = (
    '- {"name": "web.search", "arguments": {"query": "x"}}',
    '**{"tool": "bash", "arguments": {"cmd": "ls"}}**',
    '> {"name": "web.search", "arguments": {"query": "x"}}',
)

# The residue cases. The emptiness probe is what decides these: without it the punctuation left
# behind by a removed call is delivered to the operator AS their answer.
CALL_WITH_PUNCTUATION_RESIDUE = (
    '{"name": "web.search", "arguments": {"query": "x"}}\n\n---\n',
    '{"name": "web.search", "arguments": {"query": "x"}}\n:::\n',
    '{"tool": "bash", "arguments": {"cmd": "ls"}}\n\n> ',
)

# Ordinary data in a bullet list or in bold is not a tool call, and the widened anchor must not
# start eating it.
DECORATED_PROSE = (
    '- {"a": 1} is the default config',
    'The list:\n- {"name": "Alice"}\n- {"name": "Bob"}',
    '**{"status": "ok"}** means it worked',
    '- {"port": 8080, "host": "localhost"}',
)


@pytest.mark.parametrize("leaked", DECORATED_LEAKS)
def test_a_bulleted_or_emphasised_call_is_still_a_call(leaked: str) -> None:
    assert scrub_foreign_markers(leaked) == ""


@pytest.mark.parametrize("leaked", CALL_WITH_PUNCTUATION_RESIDUE)
def test_punctuation_left_behind_is_not_an_answer(leaked: str) -> None:
    """The emptiness probe, and the reason it must run AFTER the tag-stripping rules.

    Running it before meant the letters of `<tool>` counted as surviving content. Removing it
    altogether means `---` or `>` is handed to the operator as their reply.
    """

    assert scrub_foreign_markers(leaked) == ""


@pytest.mark.parametrize("prose", DECORATED_PROSE)
def test_ordinary_data_in_a_bullet_list_is_untouched(prose: str) -> None:
    assert scrub_foreign_markers(prose) == prose.strip()


@pytest.mark.parametrize("prose", ORDINARY_PROSE)
def test_ordinary_prose_is_untouched(prose: str) -> None:
    assert foreign_markers(prose) == [], prose
    assert scrub_foreign_markers(prose) == prose


def test_the_marker_does_not_carry_the_leaked_arguments() -> None:
    """Markers are logged. A query, a path or a payload must not ride along into the log line."""

    markers = foreign_markers(
        '<tool>{"name":"workspace.read_file","arguments":{"path":"/Users/me/secrets.env"}}</tool>'
    )
    assert markers
    assert not any("secrets.env" in marker for marker in markers)


# --------------------------------------------------------------------------------------
# Refused, not delivered
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("leaked", LEAKED_ENVELOPES)
def test_an_unrepairable_tool_shape_fails_the_contract(leaked: str) -> None:
    """A failed contract is what makes routing fall through to the next candidate.

    Before, the contract saw `<tool></tool>` as surviving content, returned ok=True with a small
    confidence penalty, and the operator was shown the empty wrapper as their answer.
    """

    from core.model_output_contracts import validate_contract

    result = validate_contract("plain_text", leaked)

    assert result.ok is False
    assert result.error == "foreign_tool_syntax"
    assert result.normalized_text == ""
    assert "foreign_tool_markers_only" in result.warnings


def test_a_real_answer_with_a_tool_call_appended_survives_annotated() -> None:
    """Scrubbing must not throw away an answer that happens to trail a stray call."""

    from core.model_output_contracts import validate_contract

    result = validate_contract(
        "plain_text",
        'SOL is around $150.\n<tool>{"name":"web.search","arguments":{"query":"sol"}}</tool>',
    )
    assert result.ok is True
    assert result.normalized_text == "SOL is around $150."
    assert "foreign_tool_markers_stripped" in result.warnings
    assert result.confidence_penalty > 0


# --------------------------------------------------------------------------------------
# Repaired
# --------------------------------------------------------------------------------------


class TestRepairReachesEveryLane:
    """The repair parser worked and was gated off every lane but one.

    `_repaired_tool_call_from_payload` reads a prose tool call and returns a dispatchable VOOL
    intent. It was gated behind `output_mode == "tool_intent"` AND a non-empty `request.tools`, so
    on any other turn a readable call was handed to the user as prose instead of being dispatched.
    """

    def test_output_mode_is_no_longer_the_only_way_to_reach_the_repair(self) -> None:
        """It was the wrong discriminator in both directions.

        Dropping it outright would have been unsafe, and a deliberately opposing test caught that:
        on a chat turn a reply can be prose that merely CONTAINS JSON — "Here you go: {"intent":
        ...}" answers "echo that", and converting it into a dispatched call throws the answer away.
        What separates the two cases is not the requested mode but whether any ANSWER survives.
        """

        import inspect

        from adapters import openai_compatible_adapter

        source = inspect.getsource(openai_compatible_adapter.OpenAICompatibleAdapter)
        assert 'if offered and request.output_mode == "tool_intent":' not in source
        assert 'or not scrub_foreign_markers(output_text).strip()' in source

    def test_prose_that_merely_contains_a_call_is_left_alone(self) -> None:
        """The exact fixture from tests/test_cloud_tool_dialect.py, asserted here too.

        Two tests now hold this line from opposite directions, so widening the repair cannot quietly
        start eating answers.
        """

        from core.model_output_guard import scrub_foreign_markers

        prose = 'Here you go: {"intent": "machine.list_directory", "arguments": {"path": "/x"}}'
        assert scrub_foreign_markers(prose).strip(), "an answer survives, so this must not repair"

        only_a_call = '<tool>{"name":"web.search","arguments":{"query":"sol"}}</tool>'
        assert not scrub_foreign_markers(only_a_call).strip(), "no answer survives, so repair it"

    def test_the_offered_set_is_still_the_gate(self) -> None:
        """`_tool_call_text_from_content` validates the name against the offered set, and an empty
        set cannot reject anything — so a turn that offered no tools must not repair at all."""

        import inspect

        from adapters import openai_compatible_adapter

        source = inspect.getsource(openai_compatible_adapter.OpenAICompatibleAdapter)
        position = source.index("_repaired_tool_call_from_payload(data, definitions=offered)")
        assert "if offered and (" in source[position - 900:position]

    def test_a_repaired_call_is_not_labelled_plain_text(self) -> None:
        """Otherwise the contract validates the repaired JSON as prose and shows it."""

        import inspect

        from adapters import openai_compatible_adapter

        source = inspect.getsource(openai_compatible_adapter.OpenAICompatibleAdapter)
        assert 'response_output_mode = "tool_intent"' in source
        assert "output_mode=response_output_mode," in source

    def test_the_router_validates_against_the_response_mode(self) -> None:
        """The adapter's flip means nothing if every validator reads the request's mode."""

        import inspect

        from core import memory_first_router

        source = inspect.getsource(memory_first_router)
        honoured = source.count("output_mode=_effective_output_mode(")
        assert honoured == 3, f"only {honoured} of 3 validation sites honour the response mode"

    def test_the_override_is_an_upgrade_only(self) -> None:
        """`ModelResponse.output_mode` defaults to `plain_text`.

        Reading the field directly meant any adapter or test double that never set it silently
        DOWNGRADED a genuine tool_intent turn — two model-execution tests failed immediately, with a
        tool_intent reply validated as prose and failing the contract.
        """

        from types import SimpleNamespace

        from core.memory_first_router import _effective_output_mode

        unset = SimpleNamespace(output_mode="plain_text")
        assert _effective_output_mode(unset, "tool_intent") == "tool_intent"
        assert _effective_output_mode(unset, "plain_text") == "plain_text"
        assert _effective_output_mode(unset, "summary_block") == "summary_block"

        repaired = SimpleNamespace(output_mode="tool_intent")
        assert _effective_output_mode(repaired, "plain_text") == "tool_intent"
        assert _effective_output_mode(repaired, "tool_intent") == "tool_intent"

        assert _effective_output_mode(SimpleNamespace(), "plain_text") == "plain_text"
