"""QA-050-026: where a turn's prompt tokens go, and one lossless cut.

Two live turns on 2026-08-12:

    "WHAT IS all?"                  -> cloud nemotron,  1,512 tokens
    "what is meaning of try all?"   -> qwen3:14b,       4,925 tokens

Measured on the base 03b04b39 through the real assembly path (`normalize_prompt` over a real
`TieredContextLoader` result), with the runtime's own estimator:

    turn                                        profile           system  project  user   TOTAL
    "WHAT IS all?"            plain_text        chat_minimal         545      363     3     911
    "what is meaning of try all?" plain_text    chat_minimal         545      363     7     915
    "what is meaning of try all?" tool_intent   chat_operational   4,498      436     7  14,283*

    * including 8,535 tokens of native tool schemas the router attaches after assembly, which
      nothing in the runtime counted at all.

And that tool_intent system prompt by contributor:

    tool_catalog          17,045 chars   4,262 tok   80.6%
    tooling_guidance       2,278 chars     570 tok   10.8%
    runtime_truth            879 chars     220 tok    4.2%
    capability_grounding     468 chars     117 tok    2.2%
    everything else          489 chars     123 tok    2.3%

So a 7-token question cost 14,283 tokens, 12,797 of them (90%) the tool catalog shipped TWICE --
once as the prose dialect in the system prompt and once as native schemas on the wire. **That
duplication is the finding, and it is not fixed here**; the report below only makes it visible and
re-encodes the prose half.

Those figures use the real local policy (78 wired specs, web fallback on). The pytest environment
loads a narrower policy -- 74 specs, web fallback off, so a 3,205-token prose catalog rather than
3,454 -- which is why every guard asserts RELATIVELY, against a share, a ceiling derived from the
measured floor, or the catalog's own current size. A guard hard-coded to 4,262 would pass or fail on
which policy happened to load.

## What was tried and REVERTED, so nobody repeats it

A `core/tiny_turn_policy.py` was built to decline three costs on a turn that "names nothing to
retrieve", measured structurally: no local path, no URL, no digit, no proper noun, no code, no
attachment, no same-turn tool evidence, short enough to have stated no subject. It narrowed the
`research` short-circuit in `should_attempt_tool_intent`, the `allow_paid_fallback` arm in
`model_execution_profile`, and `reasoning_mode` in `_build_request`. On the two symptom turns it
worked: 14,283 tokens became 915.

The full suite refuted it. Two turns in the existing corpus are structurally identical to the
symptom and must NOT be declined:

    "tell me about stoicism"   4 words, no capital, no digit -- and a real retrieval subject.
                               tests/test_a_research_turn_can_reach_a_tool.py pins that a research
                               turn reaches a tool whatever its DOMAIN vocabulary is.
    "audit it"                 2 words, no capital, no digit -- and real work needing reasoning.
                               tests/test_an_audit_turn_must_finish_inside_its_timeout.py pins
                               `think` on for an ordinary turn.

"Short, no capital, no digit" does not mean "no subject": a lowercase common noun IS a retrievable
subject, and a terse imperative IS work. The signal set cannot separate `try all` (a UI string the
operator just read) from `stoicism` (a topic in the world) without a function-word list -- and
`core/execution/planner.py` documents three times over that a word list in that gate is the
recurring defect, not the fix.

What would actually separate them is not a heuristic at all: whether the phrase being asked about
occurs in this chat's own recent transcript or UI state. `try all` does; `stoicism` does not. That
is a grounded check against real conversation state, and it belongs wherever the transcript is
already authoritative -- not in a frontdoor gate guessing from the raw words.

So this file no longer asserts any routing decision. It asserts the MEASUREMENT and the one cut that
needs no authority to be known first.
"""
from __future__ import annotations

import re

import pytest

from core.context_namespace import ensure_chat_namespace
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.prompt_assembly_report import estimate_tokens
from core.prompt_normalizer import _tool_intent_catalog_text, normalize_prompt
from core.prompt_payload_breakdown import (
    ALL_CATEGORIES,
    CATEGORY_ACTIVITY_RECEIPTS,
    CATEGORY_CURRENT_USER_TURN,
    CATEGORY_FILES_ARTIFACTS,
    CATEGORY_TOOL_CATALOG,
    OVERHEAD_CATEGORIES,
    measure_prompt_payload,
)
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextLoader
from core.tool_intent_executor import runtime_tool_specs

# The live symptom turns, verbatim.
_SYMPTOM_TINY_DEFINITIONAL = "what is meaning of try all?"
_SYMPTOM_TINY_FRAGMENT = "WHAT IS all?"

#: The ceiling a short local chat turn's assembled prompt must stay under. Derived, not guessed: the
#: `chat_minimal` profile bottoms out at ~545 tokens of system prompt plus ~365 of bootstrap context,
#: so ~910 is the floor the current assembly can reach for a 3-token question. 1,200 leaves headroom
#: for the runtime-truth facts a bound workspace adds, and no room for a tool catalog (the smallest
#: of which is 3,205 tokens) or an unrelated project/file block.
SHORT_TURN_PROMPT_TOKEN_CEILING = 1200

_SOURCE_CONTEXT = {
    "surface": "openclaw",
    "platform": "openclaw",
    "workspace": "/Users/example/vool-milestone",
    "workspace_binding": "project",
    "project_id": "vool",
    "requested_model": "qwen3:14b",
}


def _assemble(prompt: str, *, output_mode: str, task_kind: str, source_context: dict | None = None):
    """Assemble a real prompt through the real path -- no hand-built message list.

    Building the messages by hand would let this file agree with itself about a payload the
    assembler does not actually produce, which is the exact failure mode the 4,925-token turn hid
    behind for as long as the system prompt was one opaque string.
    """
    persona = load_active_persona("default")
    task = create_task_record(prompt)
    interpretation = HumanInputInterpretation(
        raw_text=prompt,
        normalized_text=prompt,
        reconstructed_text=prompt,
        intent_mode="request",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.5,
        quality_flags=[],
    )
    classification = classify(prompt, {"chat_surface": True})
    session_id = f"payload-{abs(hash(prompt)) % 10**8}-{output_mode}"
    ensure_chat_namespace(session_id, grant_current_receipts=False)
    context_result = TieredContextLoader().load(
        task=task,
        classification=classification,
        interpretation=interpretation,
        persona=persona,
        session_id=session_id,
        total_context_budget=5000,
    )
    return normalize_prompt(
        task=task,
        classification=classification,
        interpretation=interpretation,
        context_result=context_result,
        persona=persona,
        output_mode=output_mode,
        task_kind=task_kind,
        trace_id=task.task_id,
        surface="openclaw",
        source_context=dict(source_context or _SOURCE_CONTEXT),
    )


# --------------------------------------------------------------------------------------------
# The reverted approach, recorded as an executable fact rather than a comment
# --------------------------------------------------------------------------------------------


def test_the_reverted_tiny_turn_policy_is_really_gone() -> None:
    """No dead module, and no half-wired consumer of one.

    A structural-tininess policy that cannot separate "what is meaning of try all?" from "tell me
    about stoicism" must not sit in the tree waiting to be re-wired by someone reading only its
    docstring. If it comes back, it comes back with the grounded transcript check this file's
    header describes.
    """
    with pytest.raises(ModuleNotFoundError):
        __import__("core.tiny_turn_policy")


@pytest.mark.parametrize(
    "turn",
    (
        "tell me about stoicism",
        "audit it",
        _SYMPTOM_TINY_DEFINITIONAL,
    ),
)
def test_no_routing_decision_keys_on_how_short_a_turn_is(turn: str) -> None:
    """The counterexamples and the symptom reach the SAME routing decisions.

    Not a claim that this is ideal -- "what is meaning of try all?" still buys a tool catalog it
    cannot use. It is a claim that this branch introduced no length-keyed divergence, so the three
    turns are indistinguishable to the router exactly as they were on the base.
    """
    from core.execution.planner import should_attempt_tool_intent
    from core.task_router import model_execution_profile

    task_class = str(classify(turn, {"chat_surface": True})["task_class"])
    # Both read the same way for a `research` verdict, whatever the turn's length.
    assert should_attempt_tool_intent(
        turn, task_class="research", source_context=dict(_SOURCE_CONTEXT)
    ) is True
    assert model_execution_profile("research", chat_surface=True)["allow_paid_fallback"] is True
    assert task_class  # the classifier still has an opinion; nothing here overrides it


# --------------------------------------------------------------------------------------------
# Guard: a short local chat turn has a bounded prompt, with no catalog, files or receipts
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("turn", (_SYMPTOM_TINY_FRAGMENT, _SYMPTOM_TINY_DEFINITIONAL))
def test_a_short_local_chat_turn_stays_under_the_prompt_ceiling(turn: str) -> None:
    """Measured through the real assembly, not estimated from a fixture."""
    breakdown = measure_prompt_payload(
        _assemble(turn, output_mode="plain_text", task_kind="normalization_assist")
    )
    assert breakdown.total_tokens() <= SHORT_TURN_PROMPT_TOKEN_CEILING, breakdown.render_table()
    # And the ceiling is a real bound, not one set so high it can never bite: the tool catalog
    # alone is larger than the whole allowance.
    assert estimate_tokens(_tool_intent_catalog_text()) > SHORT_TURN_PROMPT_TOKEN_CEILING


@pytest.mark.parametrize("turn", (_SYMPTOM_TINY_FRAGMENT, _SYMPTOM_TINY_DEFINITIONAL))
def test_a_plain_chat_turn_carries_no_catalog_no_files_and_no_activity_receipts(turn: str) -> None:
    """The three categories that have nothing to do with a question about a word."""
    breakdown = measure_prompt_payload(
        _assemble(turn, output_mode="plain_text", task_kind="normalization_assist")
    )
    for category in (CATEGORY_TOOL_CATALOG, CATEGORY_FILES_ARTIFACTS, CATEGORY_ACTIVITY_RECEIPTS):
        assert breakdown.tokens_for(category) == 0, f"{category}: {breakdown.render_table()}"


def test_overhead_is_reported_separately_from_what_the_operator_typed() -> None:
    """"916 tokens" hides the ratio that matters; 909 of overhead against 7 of question does not."""
    breakdown = measure_prompt_payload(
        _assemble(
            _SYMPTOM_TINY_DEFINITIONAL, output_mode="plain_text", task_kind="normalization_assist"
        )
    )
    typed = breakdown.tokens_for(CATEGORY_CURRENT_USER_TURN)
    assert 0 < typed <= 12
    assert breakdown.overhead_tokens() == breakdown.total_tokens() - typed
    assert breakdown.overhead_tokens() > typed * 10


def test_the_prompt_ceiling_is_derived_from_a_measured_floor() -> None:
    """The threshold is not a round number someone liked: the smallest prompt the current chat
    assembly can produce for a short turn must fit under it, and not by a wide margin."""
    smallest = measure_prompt_payload(
        _assemble(
            _SYMPTOM_TINY_FRAGMENT, output_mode="plain_text", task_kind="normalization_assist"
        )
    ).total_tokens()
    assert smallest < SHORT_TURN_PROMPT_TOKEN_CEILING
    assert smallest * 2 > SHORT_TURN_PROMPT_TOKEN_CEILING


# --------------------------------------------------------------------------------------------
# Guard: the measurement is a measurement
# --------------------------------------------------------------------------------------------


def test_the_breakdown_leaves_no_contributor_unlabelled_on_any_profile() -> None:
    """An unlabelled contributor is the defect this module exists to remove: it is how 17,045
    characters of tool catalog hid inside "the system prompt" for as long as it did."""
    for turn, output_mode, task_kind in (
        (_SYMPTOM_TINY_FRAGMENT, "plain_text", "normalization_assist"),
        (_SYMPTOM_TINY_DEFINITIONAL, "tool_intent", "tool_intent"),
        ("write me a short poem about rain", "plain_text", "normalization_assist"),
        ("Reply with exactly: CLOUD-050-OK", "plain_text", "normalization_assist"),
        ("give me a summary block of this repo", "summary_block", "summarization"),
        ("translate 'good morning' into Lithuanian", "plain_text", "normalization_assist"),
    ):
        breakdown = measure_prompt_payload(
            _assemble(turn, output_mode=output_mode, task_kind=task_kind)
        )
        assert breakdown.unlabelled_segments == (), f"{turn!r}: {breakdown.unlabelled_segments}"
        assert breakdown.total_tokens() > 0


def test_the_breakdown_reconciles_with_the_text_that_was_actually_assembled() -> None:
    """Sum of the labelled parts == the estimate over the whole prompt, to within the separator
    whitespace the labels do not carry. A report that does not reconcile is a second guess."""
    request = _assemble(
        _SYMPTOM_TINY_DEFINITIONAL, output_mode="tool_intent", task_kind="tool_intent"
    )
    breakdown = measure_prompt_payload(request)
    whole = estimate_tokens(
        "".join(str(message.get("content") or "") for message in request.as_openai_messages())
    )
    assert abs(breakdown.total_tokens() - whole) <= len(breakdown.segments)


def test_the_prose_catalog_is_attributed_to_the_tool_catalog_not_to_bootstrap_prose() -> None:
    """The whole point of the instrumentation, pinned directly.

    Added after a sabotage run: relabelling this one segment `system_bootstrap` passed every other
    guard in this file. The native-schema test computes its number as a DIFFERENCE, so a prose
    catalog counted as zero still produced a plausible-looking answer, and the plain-chat tests all
    run on turns where the prose catalog is empty anyway. Nothing was measuring the 3,454 tokens the
    report exists to expose -- the exact class of blind spot that let 17,045 characters hide inside
    "the system prompt" in the first place.
    """
    request = _assemble(
        _SYMPTOM_TINY_DEFINITIONAL, output_mode="tool_intent", task_kind="tool_intent"
    )
    breakdown = measure_prompt_payload(request)
    catalog_tokens = estimate_tokens(_tool_intent_catalog_text())
    assert catalog_tokens > 1000, "catalog unexpectedly small; this guard would pass vacuously"
    assert breakdown.segment_tokens("tool_catalog") == catalog_tokens
    assert breakdown.tokens_for(CATEGORY_TOOL_CATALOG) == catalog_tokens
    # And it dominates: a report that buries it inside the bootstrap total answers no question.
    assert breakdown.largest_category() == (CATEGORY_TOOL_CATALOG, catalog_tokens)


def test_the_catalog_is_counted_in_both_encodings_it_is_actually_sent_in() -> None:
    """The native schemas the router attaches after assembly were counted NOWHERE. They are larger
    than the prose dialect they duplicate, and that duplication is this file's headline finding."""
    from core.cloud_tool_call_contract import build_cloud_tool_definitions

    request = _assemble(
        _SYMPTOM_TINY_DEFINITIONAL, output_mode="tool_intent", task_kind="tool_intent"
    )
    native = build_cloud_tool_definitions(runtime_tool_specs())
    assert native, "no native tool definitions to measure"

    prose_only = measure_prompt_payload(request)
    both = measure_prompt_payload(request, native_tool_payload=native)
    native_tokens = both.tokens_for(CATEGORY_TOOL_CATALOG) - prose_only.tokens_for(
        CATEGORY_TOOL_CATALOG
    )
    assert native_tokens > prose_only.tokens_for(CATEGORY_TOOL_CATALOG)
    assert both.largest_category()[0] == CATEGORY_TOOL_CATALOG
    assert both.segment_tokens("native_tool_schemas") == native_tokens
    # The share is the argument for fixing the duplication, so state it as a number.
    assert both.tokens_for(CATEGORY_TOOL_CATALOG) / both.total_tokens() > 0.8


def test_the_recorded_segments_carry_sizes_and_never_the_prompt_text() -> None:
    """The record travels on `InternalModelRequest.metadata`, which the router copies per ranked
    candidate. Carrying the prose would duplicate a 17KB system prompt once per candidate to report
    its size -- a memory cost paid to report a token cost."""
    request = _assemble(
        _SYMPTOM_TINY_DEFINITIONAL, output_mode="tool_intent", task_kind="tool_intent"
    )
    segments = list(request.metadata.get("prompt_payload_segments") or [])
    assert segments, "no segments recorded"
    for entry in segments:
        assert set(entry) == {"name", "category", "chars", "tokens"}, entry
        assert entry["chars"] > 0 and entry["tokens"] > 0
    # Cheap enough to carry: the whole record is far smaller than the prompt it describes.
    recorded_chars = sum(int(entry["chars"]) for entry in segments)
    assert recorded_chars > 10_000, "sanity: the prompt it describes is large"
    assert len(str(segments)) < recorded_chars // 4


def test_a_hand_built_segment_list_carrying_text_is_still_measured() -> None:
    """A caller that has prose rather than pre-measured sizes -- a test, or a future non-chat
    assembler -- must not be silently reported as zero."""
    from types import SimpleNamespace

    request = SimpleNamespace(
        metadata={
            "prompt_payload_segments": [
                {"name": "hand_built", "category": CATEGORY_TOOL_CATALOG, "text": "x" * 400},
            ]
        },
        messages=[],
    )
    breakdown = measure_prompt_payload(request)
    assert breakdown.tokens_for(CATEGORY_TOOL_CATALOG) == estimate_tokens("x" * 400)


def test_the_payload_categories_are_the_eight_the_report_claims() -> None:
    """Guard against the two constant sets drifting apart -- `core/prompt_normalizer.py` writes
    category names as literals so it need not import the breakdown module at assembly time."""
    import core.prompt_normalizer as prompt_normalizer

    assert len(OVERHEAD_CATEGORIES) == 8
    assert CATEGORY_CURRENT_USER_TURN not in OVERHEAD_CATEGORIES
    for name, value in vars(prompt_normalizer).items():
        if not name.startswith("_PAYLOAD_") or not isinstance(value, str):
            continue
        if name.endswith(("_KEY", "_KEYS")):
            continue
        assert value in ALL_CATEGORIES, f"{name}={value!r} is not a known payload category"


def test_routing_overhead_is_zero_until_a_call_is_actually_measured() -> None:
    """Never inferred. A call this module did not see is not counted, so the report cannot imply
    the answering call was the whole turn."""
    request = _assemble(
        _SYMPTOM_TINY_FRAGMENT, output_mode="plain_text", task_kind="normalization_assist"
    )
    alone = measure_prompt_payload(request)
    assert alone.tokens_for("routing_overhead") == 0
    assert alone.routing_calls_measured == 0

    preflight = measure_prompt_payload(
        _assemble(_SYMPTOM_TINY_FRAGMENT, output_mode="tool_intent", task_kind="tool_intent")
    )
    with_routing = measure_prompt_payload(request, routing_calls=[preflight])
    assert with_routing.routing_calls_measured == 1
    assert with_routing.tokens_for("routing_overhead") == preflight.total_tokens()
    assert with_routing.total_tokens() == alone.total_tokens() + preflight.total_tokens()


def test_the_rendered_table_states_every_category_and_the_overhead():
    """The operator-facing artifact. A category missing from the table is a cost nobody sees."""
    breakdown = measure_prompt_payload(
        _assemble(
            _SYMPTOM_TINY_DEFINITIONAL, output_mode="tool_intent", task_kind="tool_intent"
        )
    )
    table = breakdown.render_table()
    for category in ALL_CATEGORIES:
        assert category in table, category
    assert "TOTAL" in table and "overhead" in table


# --------------------------------------------------------------------------------------------
# Guard: the catalog re-encoding drops nothing
# --------------------------------------------------------------------------------------------

#: Measured on the base 03b04b39 for the live 78-spec catalog, under the real local policy.
_BASELINE_CATALOG_TOKENS = 4262


def test_the_catalog_recoding_is_measurably_smaller() -> None:
    catalog_tokens = estimate_tokens(_tool_intent_catalog_text())
    assert catalog_tokens < _BASELINE_CATALOG_TOKENS * 0.85, (
        f"catalog is {catalog_tokens} tokens; the dict-repr rendering was {_BASELINE_CATALOG_TOKENS}"
    )


def test_the_catalog_recoding_drops_no_intent_no_argument_and_no_detail() -> None:
    """Per spec, per argument, per detail word. A re-encoding that quietly drops
    `(files|folders|both)` or `ceiling 50000` trades prompt tokens for malformed arguments -- the
    `missing_intent` failure the catalog exists to prevent."""
    catalog = _tool_intent_catalog_text()
    specs = runtime_tool_specs()
    assert len(specs) > 40, "catalog unexpectedly small; this guard would pass vacuously"

    type_words = {
        "string", "integer", "number", "boolean", "float", "optional", "object", "dict", "list",
    }
    missing: list[str] = []
    for spec in specs:
        intent = str(spec.get("intent") or "")
        if intent not in catalog:
            missing.append(f"intent {intent}")
        arguments = spec.get("arguments")
        if not isinstance(arguments, dict):
            continue
        for name, value in arguments.items():
            if not re.search(rf"[(,]\s*{re.escape(str(name))}:", catalog):
                missing.append(f"argument {intent}.{name}")
            for word in re.findall(r"[A-Za-z0-9_|.\-]{4,}", str(value or "")):
                if word.lower() in type_words:
                    continue
                if word not in catalog:
                    missing.append(f"detail {intent}.{name} -> {word}")
    assert not missing, missing


def test_every_argument_keeps_a_type_and_an_optionality_marker() -> None:
    """The two facts a model needs to fill an argument, not just its name.

    Matched per CATALOG ENTRY rather than by a global search for the argument name, because the live
    catalog ships two distinct `pay.x402` specs whose `allow_spend` differs -- `'boolean, must be
    true to spend'` in one and `'boolean optional'` in the other. A global first-match search reads
    one spec's rendering against the other's expectation and fails on a duplicate that has nothing
    to do with the re-encoding.
    """
    catalog = _tool_intent_catalog_text()

    def _rendered_argument_lists(intent: str) -> list[str]:
        """Every rendered argument list for `intent`, parsed by paren DEPTH.

        A `[^)]*` regex cannot do this: `machine.find_largest` renders
        `(drive:str?, top:int?, kind:str? (files|folders|both))`, and the enum's own parentheses end
        the match three arguments early.
        """
        found: list[str] = []
        needle = f"- {intent}("
        start = catalog.find(needle)
        while start != -1:
            cursor = start + len(needle)
            depth = 1
            while cursor < len(catalog) and depth:
                depth += {"(": 1, ")": -1}.get(catalog[cursor], 0)
                cursor += 1
            if not depth:
                found.append(catalog[start + len(needle) : cursor - 1])
            start = catalog.find(needle, cursor)
        return found

    assert re.findall(
        r"- ([A-Za-z0-9_.]+)\(", catalog
    ), "no catalog entries parsed; this guard would pass vacuously"

    for spec in runtime_tool_specs():
        arguments = spec.get("arguments")
        if not isinstance(arguments, dict) or not arguments:
            continue
        intent = str(spec.get("intent") or "")
        candidates = _rendered_argument_lists(intent)
        assert candidates, f"{intent} has no rendered catalog entry"

        def _entry_matches(rendered: str, arguments: dict = arguments) -> bool:
            for name, value in arguments.items():
                match = re.search(rf"(?:^|,\s*){re.escape(str(name))}:([a-z]+)(\??)", rendered)
                if not match or match.group(1) not in {"str", "int", "num", "bool", "obj", "list"}:
                    return False
                wants_optional = "optional" in str(value or "").lower()
                if wants_optional != (match.group(2) == "?"):
                    return False
            return True

        assert any(_entry_matches(rendered) for rendered in candidates), (
            f"no rendered {intent} entry carries every argument with its type and optionality: "
            f"{candidates}"
        )


def test_the_catalog_still_names_the_respond_direct_escape_hatch() -> None:
    """A catalog a model cannot decline is a catalog that forces a tool call on a chat turn."""
    catalog = _tool_intent_catalog_text()
    assert "respond.direct" in catalog
    assert "Never invent intent names" in catalog


def test_an_empty_catalog_still_returns_a_usable_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adversarial: no wired tools at all must not produce an empty instruction."""
    import core.prompt_normalizer as prompt_normalizer

    monkeypatch.setattr(prompt_normalizer, "runtime_tool_specs", lambda: [])
    text = _tool_intent_catalog_text()
    assert "respond.direct" in text
    assert "Never invent tool names" in text


def test_a_malformed_spec_does_not_break_the_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adversarial: a plugin shipping junk arguments must still get its intent listed, because a
    renderer that raises here takes every tool down with it."""
    import core.prompt_normalizer as prompt_normalizer

    monkeypatch.setattr(
        prompt_normalizer,
        "runtime_tool_specs",
        lambda: [
            {"intent": "plugin.weird", "description": "d", "arguments": "not-a-dict"},
            {"intent": "plugin.nested", "description": "d", "arguments": {"a": {"deep": 1}}},
            {"intent": "plugin.none", "description": "d", "arguments": {"b": None}},
            {"intent": "plugin.empty", "description": "d", "arguments": {}},
        ],
    )
    text = _tool_intent_catalog_text()
    for intent in ("plugin.weird", "plugin.nested", "plugin.none", "plugin.empty"):
        assert intent in text
