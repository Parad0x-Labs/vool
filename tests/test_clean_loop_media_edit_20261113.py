"""A video or audio edit is not the clean loop's turn: the media tools and the media-studio skill live in the normal
lane, and the loop only reads. Once "first ten" stopped reading as a pointer into a list (5b20ca27), the loop took
"cut the boring first ten seconds out of my holiday video" and media-studio never reached the model (CI #683)."""
from __future__ import annotations

import pytest

from core.clean_loop import route

ENV = {route.FLAG_ENV: "1"}


@pytest.mark.parametrize("text", [
    "cut the boring first ten seconds out of my holiday video",
    "can you trim my clip from the beach",
    "could you mute the audio on my birthday video?",
    "make the song louder",
])
def test_a_media_edit_stays_with_the_normal_lane(text):
    got = route.decide(text, source_context={}, session_id="media-edit", resolve_brain=lambda: object(), env=ENV)
    assert (got.admit, got.reason) == (False, "media_edit_turn")


@pytest.mark.parametrize("text", [
    "whats the longest video in my downloads",
    "which recording did i cut last week",
    "cut the price column out of my budget sheet",
])
def test_a_question_about_media_or_a_non_media_cut_is_no_media_edit(text):
    assert route.is_media_edit(text) is False
