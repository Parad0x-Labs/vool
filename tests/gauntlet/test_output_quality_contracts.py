"""Gauntlet — category 6: output-quality contracts (deterministic).

Two things, both no-live-model:

  1. response_control.py (grep-confirmed COMPLETELY UNTESTED): the "reply exactly:
     X" contract that overrides model output verbatim, and its guard rails (single
     line, <=240 chars, quote-stripping).
  2. Honesty-limitation pins on the deterministic scorers the live lane will reuse
     (core.llm_eval.metrics). The happy paths are covered by
     tests/benchmarks/test_research_accuracy_scores.py and
     test_context_contamination_scores.py; here we pin the BLIND SPOTS a live
     wiring must know — most importantly that citation_validity scores 1.0 on ANY
     http URL, so a fabricated-looking link scores "citation-valid" unless paired
     with forbidden_terms.

There is no JSON-mode / structured-output contract in the runtime (see the README
missing-capabilities list); exact-target echo is the only output-mode control, so
that is what this pins.
"""
from __future__ import annotations

import pytest

from core.llm_eval.metrics import score_context_scenario, score_research_response
from core.web.api.response_control import apply_exact_response_control, exact_response_target

pytestmark = [pytest.mark.gauntlet]


# ---------------------------------------------------------------------------
# exact_response_target — extraction + guard rails
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,expected", [
    ("reply exactly: HELLO", "HELLO"),
    ("respond with exactly PONG", "PONG"),
    ("Reply with exactly this word and nothing else: laapitytio", "laapitytio"),
    ("Respond with exactly the following token and nothing else: QX-7", "QX-7"),
    ("output exactly `xyzzy-42`", "xyzzy-42"),
    ('return exactly "quoted value"', "quoted value"),
])
def test_exact_response_target_extracts_the_target(prompt, expected):
    assert exact_response_target(prompt) == expected


def test_exact_response_target_refuses_an_ambiguous_delimited_payload():
    # `say exactly this marker: PING and nothing else` carries an internal colon, so the
    # payload's literal boundary is ambiguous (is it "this marker: PING", "PING", or the cue
    # residue?). The typed parser is the one interpretation authority and refuses to bind;
    # the legacy rewriter must not re-extract its own target beside that refusal -- serving
    # cue residue here is the measured defect the refusal was built for.
    assert exact_response_target("say exactly this marker: PING and nothing else") == ""


def test_exact_response_target_refuses_a_multiline_colon_payload():
    # Multiline colon payloads are another of the typed parser's documented refusal classes:
    # a newline in the payload makes the literal's extent unknowable, and the old
    # collapse-to-one-line behaviour invented bytes the user never delimited. A single-line
    # literal binds verbatim (its own bytes, whitespace included); the 240-char cap stays
    # the guard rail for that binding.
    assert exact_response_target("reply exactly: line one\nline two") == ""
    assert exact_response_target("reply exactly: " + "x" * 241) == ""


def test_exact_response_target_rejects_over_240_chars():
    assert exact_response_target("reply exactly: " + "x" * 241) == ""


@pytest.mark.parametrize(
    "prompt",
    [
        "Reply with exactly two lines. No bullets, no title, no explanation. "
        "Line 1: red Line 2: blue",
        "Reply with exactly six words about stable routing.",
        "Reply with exactly one sentence about the moon.",
        "Reply with exactly three bullets and nothing else.",
    ],
)
def test_exact_literal_compatibility_binder_never_steals_shape_contracts(prompt):
    assert exact_response_target(prompt) == ""


def test_shape_contract_is_not_overwritten_after_it_was_validated() -> None:
    result = {"response": "red\nblue"}

    out = apply_exact_response_control(
        result,
        "Reply with exactly two lines. No bullets, no title, no explanation. "
        "Line 1: red Line 2: blue",
    )

    assert out == result


@pytest.mark.parametrize("prompt", [
    "what is the weather today?",
    "tell me a joke",
    "summarize this document",
    "",
])
def test_exact_response_target_is_empty_for_a_normal_message(prompt):
    assert exact_response_target(prompt) == ""


# ---------------------------------------------------------------------------
# apply_exact_response_control — override behaviour
# ---------------------------------------------------------------------------

def test_exact_control_overrides_a_wrong_model_response():
    result = {"response": "here is a chatty paragraph the model produced"}
    out = apply_exact_response_control(result, "reply exactly: PONG")
    assert out["response"] == "PONG"
    assert out["response_control"]["mode"] == "exact_target"
    assert out["response_control"]["target"] == "PONG"


def test_exact_control_does_not_promote_the_directive_to_the_target() -> None:
    result = {"response": "this word and nothing else: laapitytio"}
    out = apply_exact_response_control(
        result,
        "Reply with exactly this word and nothing else: laapitytio",
    )

    assert out["response"] == "laapitytio"
    assert out["response_control"]["original_response_excerpt"] == result["response"]


def test_exact_control_is_a_noop_when_response_already_matches():
    result = {"response": "PONG"}
    out = apply_exact_response_control(result, "reply exactly: PONG")
    assert out == result
    assert "response_control" not in out  # unchanged, no override metadata added


def test_exact_control_is_a_noop_without_an_exact_directive():
    result = {"response": "a normal helpful answer"}
    out = apply_exact_response_control(result, "what is 2 + 2?")
    assert out == result


# ---------------------------------------------------------------------------
# Scorer contracts (happy path sanity, reused by the live lane)
# ---------------------------------------------------------------------------

def test_context_scorer_marks_clean_answer_correct():
    r = score_context_scenario(
        scenario_id="s", response_text="Use postgres on port 8096.",
        expected_terms=["postgres", "8096"], forbidden_terms=["mysql"],
    )
    assert r["status"] == "correct" and r["score"] == 1.0


def test_context_scorer_marks_stale_answer_contaminated():
    r = score_context_scenario(
        scenario_id="s", response_text="Actually use mysql now.",
        expected_terms=["postgres"], forbidden_terms=["mysql"],
    )
    assert r["status"] == "contaminated"
    assert r["stale_hits"] == ["mysql"]


def test_research_scorer_requires_honesty_when_it_must_refuse():
    honest = score_research_response(
        scenario_id="r", response_text="I can't verify that from the evidence available.",
        expected_sources=[], must_refuse=True,
    )
    assert honest["status"] == "correct"

    dishonest = score_research_response(
        scenario_id="r", response_text="The answer is definitely 42.",
        expected_sources=[], must_refuse=True,
    )
    assert dishonest["status"] == "failed"


def test_research_scorer_forbidden_term_is_contamination():
    r = score_research_response(
        scenario_id="r", response_text="Per the docs, use the deprecated_api call.",
        expected_sources=["docs"], forbidden_terms=["deprecated_api"],
    )
    assert r["status"] == "contaminated"


# ---------------------------------------------------------------------------
# Scorer BLIND SPOT — pinned so the live wiring compensates
# ---------------------------------------------------------------------------

def test_research_scorer_citation_validity_is_fooled_by_any_url_blind_spot():
    """LIMITATION PIN. citation_validity scores 1.0 on ANY http(s) URL, so a
    fabricated-looking link counts as a valid citation. Any live wiring of this
    scorer MUST pair it with forbidden_terms / source allow-lists to catch a bluff.
    """
    r = score_research_response(
        scenario_id="bluff",
        response_text="According to https://totally-made-up-source.example/x the answer is Z.",
        expected_sources=[],
        must_refuse=False,
    )
    assert r["citation_validity"] == 1.0  # a fabricated URL is scored citation-valid
