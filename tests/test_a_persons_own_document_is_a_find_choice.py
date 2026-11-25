"""The find choice, as the judge reads it over the full list, says a person's own document is something it finds.

iMac gate on 122dfc5e9 / 56d5fb4a0 (qwen3:8b): "gal gali surasti mano žemės ūkio ataskaitą už 2024 metus?" ("could you
find my agricultural report for 2024?") was judged a web ask; the reading without the web picked nothing and the chat
answered "I can't find your report" with no search run. The find choice spoke only of "a file or folder by its name".
Probe on the iMac: with the kinds of things named, 36 of 45 -> 42 of 45 picks right, nothing that was right moved.
"""
from __future__ import annotations

from core.intent_arbiter import _PLUGIN_ASK_BRIEF_CHARS
from core.plugin_ask import BUILTIN_CHOICES
from core.plugin_identity import prompt_data


def test_the_find_choice_names_a_persons_own_things_within_what_the_judge_reads() -> None:
    description = dict((key, text) for key, text, _seats in BUILTIN_CHOICES)["find"]
    read = prompt_data(description, _PLUGIN_ASK_BRIEF_CHARS).lower()
    assert "this computer" in read
    assert all(kind in read for kind in ("document", "report", "bill", "photo")), read
    assert "file or a folder" in read
    assert len(" ".join(description.split())) <= _PLUGIN_ASK_BRIEF_CHARS, "the judge would read it cut off"
