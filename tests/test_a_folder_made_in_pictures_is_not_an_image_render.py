"""What a request verb makes is its own object: a folder made in Pictures is a folder, not a picture.

Benchmark on 21d4d4046 (local qwen3:8b): "make a folder in Pictures" went to image generation and was answered "Image
generation isn't set up yet". Wordings here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core.execution.constants import image_generation_intent


@pytest.mark.parametrize("text", [
    "make a folder in Pictures called Trips",
    "create a new folder named Beach inside my Pictures folder",
    "make a folder for pictures from the wedding",
    "create a subfolder under Pictures titled 2026",
])
def test_a_folder_ask_that_names_pictures_is_not_a_render(text):
    assert image_generation_intent(text) is None


@pytest.mark.parametrize(("text", "prompt"), [
    ("make me a poster of a fox in the snow", "a fox in the snow"),
    ("create a high-res image of a harbour at dawn", "a harbour at dawn"),
    ("generate 2 pictures of a red bicycle", "a red bicycle"),
    ("make me a black and white photo of a mill", "a mill"),
])
def test_a_picture_asked_for_is_still_a_render(text, prompt):
    assert image_generation_intent(text) == prompt
