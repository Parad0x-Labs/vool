"""An update replaces a saved fact only when the fact holds what the update is about, not one shared word.

Independent review of the job 19 commits (C6): with "my dentist is dr Okafor on Brill street" saved, "the street party
moved to saturday" overwrote the dentist under its key as "the street party moved to saturday (this replaces: my
dentist is ...)": the two share only "street". The job 19 commits send update-shaped turns to memory ahead of the
clean loop, so more turns reach this. All names, places and dates are invented.
"""
from __future__ import annotations

import pytest

from core.memory import everyday_remember as everyday

SAVED = [
    {"fact": "my dentist is dr Okafor on Brill street"},
    {"fact": "we booked the boat on lake Orrin for 4-7 june"},
    {"fact": "our team standup is at 9:15 every weekday"},
]


def test_one_shared_word_is_not_the_subject():
    assert everyday.updated_entry("the street party moved to saturday", SAVED) is None


@pytest.mark.parametrize(
    ("said", "replaced"),
    [
        ("small update: the boat dates changed to 5-8 june", "we booked the boat on lake Orrin for 4-7 june"),
        ("standup went back to 9:30", "our team standup is at 9:15 every weekday"),
        ("my dentist moved. new one is dr Ilse on Kade road", "my dentist is dr Okafor on Brill street"),
    ],
)
def test_an_update_about_a_saved_subject_still_replaces_it(said, replaced):
    assert (everyday.updated_entry(said, SAVED) or {}).get("fact") == replaced
