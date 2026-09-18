"""An explicitly pinned large model earns the heavy-request policy, whatever it is called.

`explicit_heavy` exists so a consumer can refuse to fall back to a smaller model after a failure --
its own field comment says so. Heaviness was decided by a hand-written list of size strings:

    ("24b", "30b", "32b", "35b", "72b", "heavy")

Measured on c6eed761, that list said a local `qwen3:32b` WAS an explicit heavy request while

    nemotron-3-ultra-550b-a55b:free   -> not heavy
    llama-3.1-405b                    -> not heavy
    deepseek-v3-671b                  -> not heavy
    mixtral-8x22b                     -> not heavy

so a pinned 550B model did not earn the protection a 32B one did, and a smaller fallback could
silently replace it. Backwards, and a list needing an edit for every model release.

These tests pin the PROPERTY -- size decides heaviness -- not the names. The clean family below
deliberately uses models that were never in the old list.
"""

from __future__ import annotations

import pytest

from core.local_inference_autopilot import (
    _explicit_heavy_requested,
    _largest_parameter_size_b,
    _requested_heavy_marker,
)


def _heavy(model: str) -> bool:
    return _explicit_heavy_requested(user_text="", source_context={"requested_model": model})


# ---------------------------------------------------------------------------------------------
# G1 -- the measured gap
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    (
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "llama-3.1-405b",
        "deepseek-v3-671b",
        "mixtral-8x22b",
    ),
)
def test_large_models_the_old_list_missed_are_now_heavy(model: str) -> None:
    assert _heavy(model), model


# ---------------------------------------------------------------------------------------------
# Everything the old list caught must still be caught
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", ("qwen3:24b", "mistral-30b", "qwen3:32b", "cmd-r-35b", "qwen3:72b"))
def test_every_model_the_old_list_caught_is_still_heavy(model: str) -> None:
    """No regression: 24 is the floor precisely because it was that list's smallest member."""

    assert _heavy(model), model


def test_the_explicit_flag_and_the_word_still_work() -> None:
    assert _explicit_heavy_requested(user_text="", source_context={"autopilot_allow_heavy_model": True})
    assert _explicit_heavy_requested(user_text="use a heavy model please", source_context={})


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- small models must not become heavy
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    (
        "qwen3:14b",
        "llama-3.1-8b",
        "qwen3:4b",
        "gemma-2-2b",
        "nvidia/nemotron-3.5-lightning:free",
        "phi-3-mini",
        "",
    ),
)
def test_small_or_unsized_models_are_not_heavy(model: str) -> None:
    assert not _heavy(model), model


def test_a_frontier_name_carrying_no_size_is_not_claimed_by_the_size_test() -> None:
    """Stated limit, not an oversight: the name says nothing about size, so size cannot decide it.

    `autopilot_allow_heavy_model` remains the explicit way to say so for these.
    """

    assert not _heavy("claude-opus-5")
    assert not _heavy("gpt-5")
    assert _explicit_heavy_requested(
        user_text="", source_context={"requested_model": "claude-opus-5", "autopilot_allow_heavy_model": True}
    )


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL -- the parsing itself
# ---------------------------------------------------------------------------------------------


def test_a_mixture_of_experts_name_is_read_as_its_product() -> None:
    """"8x22b" is eight experts of 22B. Reading the bare number put a ~176B model under a 24B floor."""

    assert _largest_parameter_size_b("mixtral-8x22b") == pytest.approx(176.0)
    assert _largest_parameter_size_b("mixtral-8x7b") == pytest.approx(56.0)


def test_an_moe_name_carrying_total_and_active_counts_takes_the_larger() -> None:
    """"550b-a55b" states total and active; the operator picked the model, not one of its halves."""

    assert _largest_parameter_size_b("nemotron-3-ultra-550b-a55b") == pytest.approx(550.0)


def test_a_version_number_is_not_a_parameter_count() -> None:
    """"llama-3.1-405b" must read 405, never 3.1 -- and a bare version must yield nothing."""

    assert _largest_parameter_size_b("llama-3.1-405b") == pytest.approx(405.0)
    assert _largest_parameter_size_b("llama-3.1") == 0.0
    assert _largest_parameter_size_b("qwen-2.5-instruct") == 0.0


def test_a_size_in_the_user_text_counts_as_a_request() -> None:
    """The old behaviour read the prompt too, and that is preserved."""

    assert _explicit_heavy_requested(user_text="use the 70b model for this", source_context={})
    assert not _explicit_heavy_requested(user_text="use the 7b model for this", source_context={})


def test_the_marker_reports_the_size_that_decided_it() -> None:
    """The routing proof needs to name what made the turn heavy."""

    assert _requested_heavy_marker(user_text="", source_context={"requested_model": "llama-3.1-405b"}) == "405b"
    assert _requested_heavy_marker(user_text="", source_context={"requested_model": "mixtral-8x22b"}) == "176b"
    assert _requested_heavy_marker(user_text="", source_context={"requested_model": "qwen3:14b"}) is None


def test_a_bare_letter_b_is_not_a_size() -> None:
    """Guard against the parse firing on prose that merely contains a b-word."""

    assert _largest_parameter_size_b("plan b") == 0.0
    assert _largest_parameter_size_b("model b variant") == 0.0
