"""A provider reply that only narrates which model/provider served the turn answers no part.

Unindexed prose to a multi-part request is coverage-unknown (tests/test_multipart_answer_authority.py);
runtime model-selection narration is not prose that answers anything, so it is a structural omission
of every requested part. Regression: tests/test_vool_runtime_contracts.py::
test_incomplete_multi_part_provider_text_never_becomes_the_final_answer.
"""

from __future__ import annotations

import pytest

from core import plain_task_routing as routing
from core.ordinary_chat_response_guard import inspect_ordinary_chat_output, ordinary_chat_output_policy

PROMPTS = (
    "Explain ocean blue. Calculate 39 × 24. Give 7-word title.",
    "Explain why fog forms. Calculate 7 times 8. Give a short title.",
    "what is a comet? calc 12 x 11",
    "Describe how tides work and give a haiku about the moon.",
)

NARRATION_ONLY = (
    # original measured wording
    "nvidia/nemotron-3-ultra-550b-a55b:free was the only model this turn was allowed to use",
    # clean paraphrases
    "Only one model, acme/zephyr-9b, was permitted for this turn.",
    "This turn was restricted to the provider local-lab:orca-13b.",
    "The router allowed exactly one model this turn: kestrel-mini.",
    "For this turn the selected model was granite-3b and no fallback was tried.",
    "No other provider was eligible this turn, so heron-7b replied alone.",
    # sloppy / user-typed shapes
    "ONLY MODEL THIS TURN WAS ALLOWED TO USE falcon-40b",
    "model this turn = mistral-small (only one allowed)",
    "this turn model was pinned to llama3 sorry",
    "provider for this turn was openrouter only that one",
    "Selected model for this turn: wren-3b. No other provider was ranked this turn.",
)


@pytest.mark.parametrize("prompt", PROMPTS)
@pytest.mark.parametrize("reply", NARRATION_ONLY)
def test_runtime_selection_narration_answers_no_requested_part(prompt, reply):
    assert routing.ordinary_plain_request_count(prompt) >= 2
    assert routing.ordinary_multi_part_answer_status(prompt, reply) == "missing_requested_parts"
    assert not routing.ordinary_multi_part_answer_complete(prompt, reply)
    policy = ordinary_chat_output_policy(prompt_profile="chat_minimal", output_mode="plain_text", user_text=prompt)
    check = inspect_ordinary_chat_output(reply, policy, current_user_text=prompt)
    assert not check.allowed
    assert check.reasons == ("missing_requested_parts",)
    # The display backstop, which keeps only the server-derived part count, agrees.
    assert not inspect_ordinary_chat_output(reply, policy, current_user_text="").allowed


INDEPENDENT = "Explain why fog forms. Calculate 7 times 8. Give a short title."


@pytest.mark.parametrize("reply", [
    # an unindexed fluent partial stays coverage-unknown (candidate contract)
    "Water vapor condenses as air cools. Seven times eight equals fifty-six.",
    # the word "model" inside a real answer
    "A cooling model explains fog: moist air drops below its dew point. 7 x 8 = 56. Title: Morning Mist.",
    # a real answer that also mentions the serving model keeps its answer sentences
    "Fog forms when moist air cools to its dew point. 7 x 8 = 56. Title: Morning Mist. This turn used the model heron-7b.",
])
def test_answers_that_mention_a_model_stay_coverage_unknown(reply):
    assert routing.ordinary_multi_part_answer_status(INDEPENDENT, reply) == "unindexed_semantic_coverage_unknown"
    policy = ordinary_chat_output_policy(prompt_profile="chat_minimal", output_mode="plain_text", user_text=INDEPENDENT)
    assert inspect_ordinary_chat_output(reply, policy, current_user_text=INDEPENDENT).allowed


def test_turn_of_the_century_idiom_is_not_runtime_narration():
    prompt = "Explain the Model T. Calculate 3 times 4."
    reply = "The Model T changed travel at this turn of the century, and 3 times 4 is 12."
    assert not routing.answer_is_only_runtime_selection_narration(reply)
    assert routing.ordinary_multi_part_answer_status(prompt, reply) == "unindexed_semantic_coverage_unknown"


def test_single_part_requests_are_outside_the_multipart_verdict():
    reply = "nvidia/nemotron-3-ultra-550b-a55b:free was the only model this turn was allowed to use"
    assert routing.ordinary_multi_part_answer_status("Explain why fog forms.", reply) == "not_applicable"
