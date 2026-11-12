"""A size glued onto a model family name, in any spelling users type, still reads as heavy.

The user-text size filter dropped names written without a separator ("family70b", "family_32b",
"Family2.5_72B") and dropped "can my box run 70b?" because an address lead word sat two words back.
The rule is now structural: a family name glued straight onto the size counts (the name must end in
a letter/underscore/dot so the LAST digit run is the size), and an explicit request verb directly
attached to the size beats an address/unit lead word. Addresses and street numbers stay plain.
"""
from __future__ import annotations

import pytest

from core.local_inference_autopilot import _explicit_heavy_requested, _requested_heavy_marker


@pytest.mark.parametrize(
    "text",
    [
        "use mixtral_141b for the summary",
        "switch to granite27b",
        "try deepseek_v334b please",
        "use Granite3.3_78B",
        "load phi-4_28b tonight",
        "can my laptop run 27b?",
        "please serve 405b",
        "deploy gemma_65b now",
        "can the workstation run 122b?",
    ],
)
def test_glued_and_verb_attached_model_names_are_heavy(text):
    assert _explicit_heavy_requested(user_text=text, source_context={}) is True
    assert _requested_heavy_marker(user_text=text, source_context={}) is not None


@pytest.mark.parametrize(
    "text",
    [
        "use mixtral_141b for the summary",
        "switch to granite27b",
        "try deepseek_v334b please",
        "use Granite3.3_78B",
        "load phi-4_28b tonight",
    ],
)
def test_the_last_digit_run_is_the_size(text):
    marker = _requested_heavy_marker(user_text=text, source_context={})
    assert marker in {"141b", "27b", "334b", "78b", "28b"}, marker


@pytest.mark.parametrize(
    "text",
    [
        "I live in Unit 31b, third floor.",
        "Please use gate 24b for deliveries.",
        "deliver the crate to 1180b Harbour Road",
        "the annex is at pier 40b, dock side",
        "send the spare part to box 55b care of the porter",
    ],
)
def test_addresses_and_unit_numbers_stay_plain(text):
    assert _explicit_heavy_requested(user_text=text, source_context={}) is False
    assert _requested_heavy_marker(user_text=text, source_context={}) is None


def test_a_verb_attached_beats_an_address_lead_word():
    # "box" is an address lead word, but "run" is attached to the size: a request.
    assert _explicit_heavy_requested(user_text="Can my box run 70b?", source_context={}) is True
    assert _requested_heavy_marker(user_text="Can my box run 70b?", source_context={}) == "70b"
    # an address word with no request verb attached still reads as a place
    assert _explicit_heavy_requested(user_text="leave it in box 70b", source_context={}) is False
