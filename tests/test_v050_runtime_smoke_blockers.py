"""Regressions for the v0.5.0 runtime smoke blockers.

Each test names the QA id it holds down. Every one of them is built on the shape that actually
failed, not on a friendly input: a three-part request under a one-clause length constraint, a
correction typed in frustration, a title that is one word short, an answer that is two characters
long. The measured behaviour before the fixes is quoted in each docstring so a future reader can
tell a real regression from a rewrite.

QA-050-016  a local multi-intent request was answered with the literal string ``1.``
QA-050-017  that string was accepted as a successful answer, with no warning and no retry
QA-050-018  ``what is that? this is not what i have asked`` was classified research/summarization
QA-050-019  which sent a correction down a heavyweight, paid-fallback escalation chain
QA-050-020  a requested five-word title came back with four words and was accepted
QA-050-021  a pinned cloud model reproduced ``1.`` exactly, so the truncation was the runtime's
QA-050-009/011/022  severe latency with no per-attempt timing anywhere in the trace
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from core.agent_runtime.response import _validate_final_chat_output
from core.incomplete_answer import (
    answer_looks_incomplete,
    inspect_answer_completeness,
)
from core.memory_first_router import (
    MemoryFirstRouter,
    _emit_attempt_chain_timing,
    _record_attempt_timing,
    summarize_attempt_timings,
)
from core.response_constraints import (
    check_response_constraint,
    enforce_response_constraint,
    parse_response_constraint,
)
from core.task_router import (
    classify,
    looks_like_conversational_correction,
    model_execution_profile,
)
from storage.model_provider_manifest import ModelProviderManifest

# The turn that produced `1.`: three unrelated requests, with a length attached to the first one.
_MULTI_INTENT_TURN = (
    "Do three things: explain photosynthesis in one sentence, list two colors, "
    "and name a fruit."
)
# A model's honest answer to it. Every part is present; only the runtime lost them.
_MULTI_PART_DRAFT = (
    "1. Plants turn light into sugar.\n"
    "2. Blue and green.\n"
    "3. Mango."
)


def _one_sentence_constraint():
    constraint = parse_response_constraint("Confirm it in one short sentence.")
    assert constraint is not None and constraint.max_sentences == 1
    return constraint


# ---------------------------------------------------------------------------------------------
# QA-050-016 / QA-050-021 -- the truncation itself
# ---------------------------------------------------------------------------------------------


def test_a_sentence_bound_never_trims_a_numbered_answer_to_its_first_marker() -> None:
    """Measured before the fix: this exact call returned ``1.`` and reported compliant=True.

    The sentence splitter read the enumeration marker as a whole sentence, so "the first sentence"
    of a numbered answer was two characters long.
    """
    application = enforce_response_constraint(_MULTI_PART_DRAFT, _one_sentence_constraint())

    assert application.text.strip() != "1."
    assert not application.text.strip().rstrip(".").isdigit()
    assert "Plants turn light into sugar" in application.text


def test_a_bare_enumeration_marker_is_not_a_compliant_one_sentence_answer() -> None:
    """QA-050-017 at the shape checker: ``1.`` used to satisfy a one-sentence constraint."""
    check = check_response_constraint("1.", _one_sentence_constraint())

    assert not check.compliant
    assert check.sentence_count == 0


@pytest.mark.parametrize(
    "draft",
    [
        "1.",
        "1.\n2.\n3.",
        "- \n- \n- ",
        "  1.  ",
    ],
)
def test_no_degenerate_draft_survives_the_shape_check(draft: str) -> None:
    """Every pathological enumeration shape must fail, not just the one that was measured."""
    assert not check_response_constraint(draft, _one_sentence_constraint()).compliant


def test_a_length_asked_of_one_clause_does_not_bound_a_three_part_answer() -> None:
    """QA-050-016's root: "in one sentence" belonged to one of three requests.

    Adopting it as the shape of the whole reply is what made the enforcement destructive in the
    first place. The user's own words still reach the model; the runtime just stops enforcing a
    bound it cannot correctly scope.
    """
    assert parse_response_constraint(_MULTI_INTENT_TURN) is None


def test_a_whole_turn_shape_instruction_is_still_adopted() -> None:
    """The control for the test above: a bare instruction addressed to the reply still binds."""
    constraint = parse_response_constraint("Answer in one word: is it ready?")

    assert constraint is not None
    assert constraint.exact_words == 1


def _manifest(*, local: bool) -> ModelProviderManifest:
    manifest = ModelProviderManifest(
        provider_name="smoke-test",
        model_name="qwen3:14b" if local else "nemotron-pinned",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="test",
        license_reference="test",
        weight_location="external",
        runtime_dependency="test",
        capabilities=["summarize"],
        runtime_config={
            "base_url": "http://127.0.0.1:11434" if local else "https://example.invalid",
        },
        metadata={
            "deployment_class": "local" if local else "remote",
            "cost_class": "free_local" if local else "remote_unknown",
        },
    )
    # `core.final_answer_authorship` refuses an uncertified loopback model BEFORE its adapter
    # is built, so an uncertified probe never reaches the lane this file names and the test
    # would assert the authorship fence instead. Certifying is what an operator does; it does
    # not soften the authority. Same remedy the authorship lane applies to its own probe in
    # tests/test_v050_fastpath_authority_and_call_accounting.py.
    from tests._authorship_certification import certify_for_authorship

    certify_for_authorship(manifest)
    return manifest


def _constrained_request() -> ModelRequest:
    constraint = _one_sentence_constraint()
    return ModelRequest(
        task_kind="conversation",
        prompt=_MULTI_INTENT_TURN,
        messages=[{"role": "user", "content": _MULTI_INTENT_TURN}],
        metadata={
            "response_constraint": constraint.to_dict(),
            "defer_stream_until_verified": True,
        },
    )


@pytest.mark.parametrize("local", [True, False])
def test_no_provider_lane_can_ship_a_bare_marker_as_the_answer(local: bool) -> None:
    """QA-050-021: the pinned cloud model returned the same two characters as the local one.

    That is the tell that the truncation was the runtime's, so the regression is driven through the
    same router seam on BOTH lanes with the same draft. Whatever each lane does about the violation
    -- the local lane gets one bounded repair, the cloud lane falls back -- neither may hand the
    user a bare enumeration marker.
    """
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text=_MULTI_PART_DRAFT, usage={"output_tokens": 20}),
        ModelResponse(output_text="Plants turn light into sugar.", usage={"output_tokens": 6}),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(local=local),
            request=_constrained_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id=f"smoke-{local}"),
            source_context=None,
        )

    assert error is None
    assert response is not None
    answer = str(response.output_text or "").strip()
    assert answer != "1."
    assert not answer.rstrip(".").isdigit()
    assert not answer_looks_incomplete(answer)


# ---------------------------------------------------------------------------------------------
# QA-050-017 -- an incomplete answer may not be reported as a finished one
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("1.", "enumeration_without_content"),
        ("1.\n2.\n3.", "enumeration_without_content"),
        ("", "empty_answer"),
        ("​​", "empty_answer"),
        ("1. Paris is the capital.\n2.", "unfinished_enumeration"),
        ("1. Paris\n2.\n3. Berlin", "empty_enumeration_item"),
        ("Here are three options:\n- alpha\n- beta", "fewer_items_than_announced"),
        ("It depends on the season, the weather and the", "dangling_tail"),
    ],
)
def test_a_cut_off_answer_is_named_as_one(text: str, reason: str) -> None:
    result = inspect_answer_completeness(text)

    assert result.incomplete
    assert reason in result.reasons


@pytest.mark.parametrize(
    "text",
    [
        "Ready.",
        "New York",
        "42",
        "1. Plants turn light into sugar.\n2. Blue and green.\n3. Mango.",
        "- alpha\n- beta\n- gamma",
        # A marker whose item is written on the next line is a complete list, not a blank bullet.
        "1.\n   Paris is the capital of France.\n2.\n   Berlin is the capital of Germany.",
        "Ask about the season, the weather and the rest.",
    ],
)
def test_a_short_or_unusual_answer_is_not_called_incomplete(text: str) -> None:
    """The detector fails open. A false positive here costs the user a correct answer."""
    assert not answer_looks_incomplete(text)


def test_the_final_backstop_refuses_to_ship_a_bare_marker() -> None:
    """Measured before the fix: `1.` and `""` both left this function unchanged.

    `inspect_ordinary_chat_output` returns allowed=True for both, so nothing downstream of the
    model had an opinion about an answer with no content in it.
    """
    source_context: dict[str, object] = {}

    final = _validate_final_chat_output("1.", source_context=source_context)

    assert final.strip() != "1."
    assert "cut off" in final.lower()
    completeness = source_context["response_control"]["final_ui"]["answer_completeness"]
    assert completeness["incomplete"] is True
    assert completeness["degenerate"] is True


@pytest.mark.parametrize("blank", ["", "   ", "​​", "﻿"])
def test_the_final_backstop_refuses_to_ship_nothing(blank: str) -> None:
    """A blank answer is the same defect wearing less punctuation, zero-width characters included.

    `str.strip()` does not remove Cf/Cc characters, so a reply of two zero-width spaces used to
    reach the user as a visibly empty message that every check called fine.
    """
    final = _validate_final_chat_output(blank, source_context={})

    assert final.strip()
    assert "cut off" in final.lower()


def test_a_partial_answer_keeps_its_content_and_says_it_is_partial() -> None:
    """Most of an answer is worth keeping; passing it off as finished is not."""
    source_context: dict[str, object] = {}

    final = _validate_final_chat_output(
        "1. Plants turn light into sugar.\n2.",
        source_context=source_context,
    )

    assert "Plants turn light into sugar" in final
    assert "Incomplete" in final
    assert source_context["response_control"]["final_ui"]["answer_completeness"]["incomplete"] is True


def test_a_complete_answer_passes_the_backstop_untouched() -> None:
    source_context: dict[str, object] = {}
    answer = "1. Plants turn light into sugar.\n2. Blue and green.\n3. Mango."

    assert _validate_final_chat_output(answer, source_context=source_context) == answer
    assert source_context["response_control"]["final_ui"]["answer_completeness"]["incomplete"] is False


# ---------------------------------------------------------------------------------------------
# QA-050-018 / QA-050-019 -- a correction is a conversational turn
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "turn",
    [
        "what is that? this is not what i have asked",
        "that is not what i asked for",
        "no, that's wrong",
        "you misunderstood me",
        "what is that?",
        "this is not what i wanted",
    ],
)
def test_a_correction_stays_in_conversation(turn: str) -> None:
    """QA-050-018: the bare substring "what is" routed the first of these to research."""
    assert looks_like_conversational_correction(turn)
    assert classify(turn, {"chat_surface": True})["task_class"] == "chat_conversation"


def test_a_correction_does_not_buy_a_heavyweight_paid_lane() -> None:
    """QA-050-019: research maps to queen + allow_paid_fallback, which is what ran the chain."""
    task_class = classify(
        "what is that? this is not what i have asked",
        {"chat_surface": True},
    )["task_class"]
    profile = model_execution_profile(task_class, chat_surface=True)

    assert profile["provider_role"] == "auto"
    assert profile["allow_paid_fallback"] is False
    assert profile["task_kind"] != "summarization"


@pytest.mark.parametrize(
    "turn",
    [
        # A real lookup keeps its lane.
        "search for the latest ollama release",
        "look up who founded solana on x",
        # A technical complaint is a technical request, however unhappily phrased.
        "why is this code wrong?",
        "the test output is wrong, fix it",
        # Long enough to be carrying its own content.
        "that is not what i asked, i wanted a full comparison of the two deployment "
        "strategies with their tradeoffs and costs",
    ],
)
def test_the_correction_recognizer_does_not_swallow_other_turns(turn: str) -> None:
    assert not looks_like_conversational_correction(turn)


# ---------------------------------------------------------------------------------------------
# QA-050-020 -- an exact word count is validated, whatever noun it is attached to
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("turn", "expected"),
    [
        ("give me a five-word title for this", 5),
        ("give me a 5 word title for this", 5),
        ("write a five word headline", 5),
        ("give me a twelve-word summary of the file", 12),
        ("Give me a three-word mood.", 3),
    ],
)
def test_an_attributive_word_count_is_read_as_the_length_it_states(turn: str, expected: int) -> None:
    """Measured before the fix: the five-word forms parsed to no constraint at all."""
    constraint = parse_response_constraint(turn)

    assert constraint is not None
    assert constraint.exact_words == expected


def test_a_four_word_title_fails_a_five_word_request() -> None:
    """QA-050-020 exactly: the answer that was accepted is now a violation the runtime repairs."""
    constraint = parse_response_constraint("give me a five-word title for this")
    assert constraint is not None

    short = check_response_constraint("Bright Morning Coffee Ritual", constraint)
    exact = check_response_constraint("Bright Morning Coffee Ritual Notes", constraint)

    assert not short.compliant
    assert short.word_count == 4
    assert "exact_words" in short.violations
    assert exact.compliant


@pytest.mark.parametrize(
    "turn",
    [
        "a five-word title is short",
        "i think a five-word title works better than a long one",
    ],
)
def test_a_sentence_about_a_length_does_not_bound_the_reply(turn: str) -> None:
    assert parse_response_constraint(turn) is None


# ---------------------------------------------------------------------------------------------
# QA-050-009 / 011 / 022 -- the fallback chain says what it cost
# ---------------------------------------------------------------------------------------------


def test_every_attempt_records_its_duration_and_the_ceiling_it_ran_under() -> None:
    timings: list[dict[str, object]] = []
    heavy = SimpleNamespace(provider_id="ollama:qwen3:14b", model_name="qwen3:14b")
    pinned = SimpleNamespace(provider_id="openrouter:nemotron", model_name="nemotron")

    _record_attempt_timing(
        timings,
        manifest=heavy,
        seconds=61.25,
        outcome="failed",
        error="read timeout",
        timeout_seconds=60.0,
    )
    _record_attempt_timing(
        timings,
        manifest=pinned,
        seconds=8.4,
        outcome="answered",
        timeout_seconds=180.0,
    )

    assert [entry["outcome"] for entry in timings] == ["failed", "answered"]
    assert timings[0]["seconds"] == 61.25
    assert timings[0]["timeout_seconds"] == 60.0
    assert timings[0]["error"] == "read timeout"
    assert summarize_attempt_timings(timings) == {
        "attempts": 2,
        "total_seconds": 69.65,
        "slowest_provider_id": "ollama:qwen3:14b",
        "slowest_seconds": 61.25,
    }


def test_a_multi_candidate_chain_emits_its_total_and_names_the_slowest() -> None:
    """The event the smoke run needed and did not have."""
    timings: list[dict[str, object]] = []
    for provider, seconds in (("qwen3:14b", 61.0), ("nemotron", 45.0), ("qwen3:8b", 12.0)):
        _record_attempt_timing(
            timings,
            manifest=SimpleNamespace(provider_id=provider, model_name=provider),
            seconds=seconds,
            outcome="failed",
            timeout_seconds=180.0,
        )

    with mock.patch("core.memory_first_router.emit_runtime_event") as emit:
        _emit_attempt_chain_timing(
            {"runtime_session_id": "smoke"},
            attempt_timings=timings,
            fallback_budget_seconds=180.0,
            outcome="all_ranked_providers_failed",
        )

    assert emit.call_count == 1
    details = emit.call_args.kwargs["details"]
    assert details["attempts"] == 3
    assert details["total_seconds"] == 118.0
    assert details["slowest_provider_id"] == "qwen3:14b"
    assert details["fallback_budget_seconds"] == 180.0
    assert len(details["attempt_timings"]) == 3


def test_a_single_attempt_turn_does_not_emit_a_chain_event() -> None:
    """One call is not a chain; its duration rides on the decision instead."""
    timings: list[dict[str, object]] = []
    _record_attempt_timing(
        timings,
        manifest=SimpleNamespace(provider_id="qwen3:8b", model_name="qwen3:8b"),
        seconds=3.0,
        outcome="answered",
    )

    with mock.patch("core.memory_first_router.emit_runtime_event") as emit:
        _emit_attempt_chain_timing(
            {"runtime_session_id": "smoke"},
            attempt_timings=timings,
            fallback_budget_seconds=60.0,
            outcome="answered",
        )

    assert emit.call_count == 0
