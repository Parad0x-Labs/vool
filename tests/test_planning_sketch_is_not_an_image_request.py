"""Sketching a plan is planning, not image generation.

v14's clause-based image intent counts "sketch" as a request verb, so on the port "help me sketch a bot plan" asked for
image-generation approval (main's tests/test_vool_local_first_memory_and_personalization.py caught it). A planning noun
is not a render subject; a drawable subject still is.
"""
from __future__ import annotations

import pytest

from core.execution.constants import image_generation_intent


@pytest.mark.parametrize(
    "text",
    [
        "help me sketch a bot plan",
        "sketch an outline for the launch post",
        "can you sketch a rough roadmap for the migration",
        "sketch a strategy for paying off the card",
        "draw up a checklist for the move",
    ],
)
def test_a_planning_sketch_is_not_an_image_request(text: str) -> None:
    assert image_generation_intent(text) is None


@pytest.mark.parametrize(
    "text,subject",
    [
        ("sketch a cat on a skateboard", "cat"),
        ("please draw a lighthouse at dusk", "lighthouse"),
    ],
)
def test_a_drawable_subject_is_still_an_image_request(text: str, subject: str) -> None:
    prompt = image_generation_intent(text)
    assert prompt is not None and subject in prompt
