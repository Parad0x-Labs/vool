"""A number the owner's ask names is as telling as a word: the files loop keeps a find whose only shared part is it.

CI on 006e4a888 (test_setup_first_tries_served): a find in another language for a file whose name ends in a long
number fell through, because the word reader drops bare numbers and the ask's other words are its verb and "file",
which no file name holds. Names and numbers are invented.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.clean_loop import hook


def _loop(seen: list[str]) -> SimpleNamespace:
    steps = [{"tool": "find_files", "ok": True, "result_chars": 120, "status": "ok"}]
    return SimpleNamespace(steps=steps, seen=seen, staged="", answer="Found it: " + ", ".join(seen))


@pytest.mark.parametrize("ask, seen", [
    ("Etsi kansio 7302 minulle", ["~/Documents/harbour-7302/plan.txt"]),
    ("Najdi mi složku 51846", ["~/Desktop/receipts_51846"]),
])
def test_a_find_named_by_its_number_keeps_the_file_it_found(ask, seen):
    reason = hook.loop_should_decline(ask, _loop(seen), [], judged=None)
    assert reason != "irrelevant_matches_fell_through", reason


def test_a_find_that_shares_nothing_with_the_ask_still_falls_through():
    reason = hook.loop_should_decline("Etsi kansio 7302 minulle", _loop(["~/Music/band-1188/demo.wav"]), [])
    assert reason == "irrelevant_matches_fell_through", reason
