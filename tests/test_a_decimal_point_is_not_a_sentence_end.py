"""Trimming a long chat answer must not rewrite the text it keeps.

Measured live on the served surface (`route=ordinary_plain_text_chat`, `ollama-local:qwen2.5:7b`)::

    1. Toyota Corolla: ~186 mph (300 km/h), 1. 8L to 2. 4L
    2. Volkswagen Golf: ~155 mph (250 km/h), 1. 0L to 2. 0L

Every engine size mangled. The same prompt sent straight to `qwen2.5:7b` returns `1.8L to 2.0L`
intact, so the model did not write it that way -- the runtime did.

`constrain_ordinary_chat_output` trims a normal-chat overrun. It collected "sentences" with
`_SENTENCE_RE = [^.!?]+(?:[.!?]+|$)`, stripped each one, and rejoined them with `" "`. That regex
ends a sentence at any `.`, so `1.8` was two sentences, and the strip-then-rejoin inserted a space
that was never in the text. The same rejoin flattened newlines, so a numbered list came back as one
paragraph.

The repair cuts the ORIGINAL string at an offset instead of re-assembling it, and a terminator no
longer counts when it is a decimal point or a list marker. Nothing is rebuilt, so nothing can be
rewritten.

Scope: this fixes CORRUPTION, not the length. The word cap still truncates -- that is a separate
defect (an output ceiling set below what an enumerated request needs), and the truncation is now
reported by `inspect_answer_completeness` rather than shipped as a finished answer.
"""

from __future__ import annotations

import re

import pytest

from core.ordinary_chat_response_guard import constrain_ordinary_chat_output

_CARS = (
    "Here are the top 5 best-selling cars:\n\n"
    "1. Toyota Corolla: ~186 mph (300 km/h), 1.8L to 2.4L\n"
    "2. Volkswagen Golf: ~155 mph (250 km/h), 1.0L to 2.0L\n"
    "3. Honda Civic: ~130 mph (210 km/h), 1.0L to 2.4L\n"
    "4. Ford Focus: ~150 mph (240 km/h), 1.5L to 2.0L\n"
    "5. Hyundai Elantra: ~128 mph (206 km/h), 1.6L to 2.0L"
)


# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproduction
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("max_words", (20, 30, 40, 55))
def test_a_decimal_is_never_split_by_the_trimmer(max_words: int) -> None:
    out = constrain_ordinary_chat_output(_CARS, {"max_words": max_words})

    assert "1. 8L" not in out
    assert "2. 0L" not in out
    assert "1. 0L" not in out


def test_whatever_survives_the_trim_is_verbatim_from_the_source() -> None:
    """The strongest form of the invariant: the output is a PREFIX of the input.

    A trimmer may end early. It may not alter a character of what it keeps -- and a prefix check
    catches every rewrite at once, including ones nobody thought to name.
    """

    for max_words in range(5, 60, 3):
        out = constrain_ordinary_chat_output(_CARS, {"max_words": max_words})
        assert _CARS.startswith(out.rstrip()), (max_words, out[-60:])


# ---------------------------------------------------------------------------------------------
# CLEAN -- other decimal-bearing content, none of it cars
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        "The rate moved from 3.25 percent to 4.75 percent over the year in steady increments.",
        "Version 2.14.3 replaced 2.13.9 after the regression was found in the parser module.",
        "The sample measured 0.001 molar against a 0.05 molar control in every replicate run.",
        "Latitude 51.5074 and longitude 0.1278 place it within the boundary of the old city.",
        "It weighs 1.5 kg empty and 2.75 kg once the reservoir has been completely filled up.",
    ),
)
def test_decimals_in_ordinary_prose_are_untouched(text: str) -> None:
    out = constrain_ordinary_chat_output(text, {"max_words": 8})

    assert text.startswith(out.rstrip())
    assert not re.search(r"\d\.\s+\d", out), out


def test_a_numbered_list_keeps_its_line_breaks() -> None:
    """The rejoin flattened structure; cutting an offset cannot."""

    out = constrain_ordinary_chat_output(_CARS, {"max_words": 40})

    assert "\n" in out
    assert out.count("\n") >= 3


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- the trimming behaviour itself must survive
# ---------------------------------------------------------------------------------------------


def test_prose_is_still_trimmed_at_a_real_sentence_end() -> None:
    prose = (
        "The first point is simple. The second point is longer and adds detail. "
        "The third trails off here"
    )

    assert constrain_ordinary_chat_output(prose, {"max_words": 12}) == "The first point is simple."


def test_an_answer_under_the_cap_is_returned_unchanged() -> None:
    assert constrain_ordinary_chat_output(_CARS, {"max_words": 500}) == _CARS


def test_no_cap_means_no_trimming() -> None:
    assert constrain_ordinary_chat_output(_CARS, {}) == _CARS
    assert constrain_ordinary_chat_output(_CARS, None) == _CARS


def test_an_empty_answer_stays_empty() -> None:
    assert constrain_ordinary_chat_output("", {"max_words": 10}) == ""
    assert constrain_ordinary_chat_output("   ", {"max_words": 10}) == ""


def test_a_long_answer_is_actually_shortened() -> None:
    """The guard must not be bought by disabling it."""

    prose = " ".join(f"word{i}." for i in range(200))
    out = constrain_ordinary_chat_output(prose, {"max_words": 20})

    assert len(out) < len(prose)


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL
# ---------------------------------------------------------------------------------------------


def test_a_list_marker_is_not_a_sentence_end() -> None:
    """A cut landing on a bare "2." is what left the live answer ending on a lone marker.

    No prose sentence anywhere in this input, deliberately: with one present, the cut lands on the
    real terminator and the marker exemption is never consulted. Written that way at first, and a
    sabotage run removing the exemption stayed green -- the test was not reaching the guard.
    """

    listed = "1. alpha\n2. beta\n3. gamma\n4. delta"
    out = constrain_ordinary_chat_output(listed, {"max_words": 4})

    assert not out.rstrip().endswith("2.")
    assert not out.rstrip().endswith("3.")
    assert "beta" in out


def test_a_cut_never_lands_on_a_decimal_point() -> None:
    """The other exemption, exercised.

    An answer ending "The engine is 1." reads as a finished sentence and is a severed number. The
    cut must fall back rather than stop inside the value -- there must be no prose terminator here,
    or the decimal is never the last candidate and the exemption goes untested.
    """

    text = "The engine is 1.8 litres and the car weighs a lot more than that"
    out = constrain_ordinary_chat_output(text, {"max_words": 6})

    assert not out.rstrip().endswith("1.")
    assert "1.8" in out


def test_a_sentence_ending_in_a_number_still_terminates() -> None:
    """Only a decimal point is exempt. "...costs 42." really is the end of a sentence.

    Excluding every period after a digit would be the easy over-correction, and it would stop the
    trimmer finding any sentence end in numeric prose.
    """

    text = "The total is 42. The next paragraph begins here and runs on for a while longer"

    assert constrain_ordinary_chat_output(text, {"max_words": 10}) == "The total is 42."


def test_an_ellipsis_and_multi_terminator_run_still_work() -> None:
    text = "He paused... Then he spoke again at some length about the weather and the roads"

    assert constrain_ordinary_chat_output(text, {"max_words": 6}) == "He paused..."


def test_a_decimal_at_the_very_end_is_not_treated_as_a_terminator() -> None:
    """No trailing digit after the point means it is not a decimal -- but there is nothing after
    it either, so the guard must not fabricate a sentence end mid-number."""

    out = constrain_ordinary_chat_output("The engine is 1.8", {"max_words": 3})

    assert "1.8" in out or out == "The engine is"
