"""Memory gaps wired in for v0.8 (ledger V-128, tracker row 103, H-037).

- V-128: "update: my sailboat is called noodle now" replaced the saved "my turtle is called Ziggy" on the one word they
  share ("called"); an update replaces only a fact about its own subject.
- Row 103: a forget selected rows by plain substring, so a short word erased unrelated rows ("forget cat" took "my
  category is ..."); a forget's words now start and end where words do.
"""
from __future__ import annotations

import random

import pytest

from core.memory.carriers import forget_needle_in
from core.persistent_memory import maybe_handle_memory_command
from tests.test_memory_asked_in_any_language_v08 import CHAT, OWNER, _entries, _fresh, _policy, home, isolated_home

__all__ = ["_fresh", "home", "isolated_home"]


@pytest.mark.parametrize("seed", range(4))
def test_an_update_replaces_only_a_fact_about_its_own_subject(home, seed):
    rng = random.Random(f"v128-{seed}")
    pet, pet_name = rng.choice([("turtle", "Ziggy"), ("parrot", "Mango"), ("hamster", "Biscuit")])
    thing, new_name = rng.choice([("sailboat", "noodle"), ("canoe", "Pebble"), ("kite", "Zephyr")])
    lead = rng.choice(["update:", "small change:", "quick update -"])
    maybe_handle_memory_command(f"Remember that my {pet} is called {pet_name}.", session_id=CHAT,
                                access_policy=_policy(CHAT), source_context=dict(OWNER))
    maybe_handle_memory_command(f"{lead} my {thing} is called {new_name} now", session_id=CHAT,
                                access_policy=_policy(CHAT), source_context=dict(OWNER))
    assert f"my {pet} is called {pet_name}" in _entries(), _entries()


def test_an_update_about_a_saved_subject_still_replaces_it(home):
    maybe_handle_memory_command("Remember that my canoe is called Pebble.", session_id=CHAT,
                                access_policy=_policy(CHAT), source_context=dict(OWNER))
    maybe_handle_memory_command("update: my canoe is called Driftwood now", session_id=CHAT,
                                access_policy=_policy(CHAT), source_context=dict(OWNER))
    assert "Driftwood" in _entries() and "Pebble" not in _entries().split("replaces")[0], _entries()


@pytest.mark.parametrize(("text", "needle", "carried"), [
    ("my category is A", "cat", False),
    ("the party is on Friday", "art", False),
    ("our carpet is beige", "car", False),
    ("my cat is grey", "cat", True),
    ("my tents are green", "my tent", True),
    ("Ziggy's bowl is blue", "ziggy", True),
    ("mano dviratis mėlynas", "dviratis", True),
    ("the meeting is in room 312", "12", False),
    ("gate code 12", "12", True),
])
def test_a_forget_word_stands_where_a_word_stands(text, needle, carried):
    assert forget_needle_in(text, needle) is carried


@pytest.mark.parametrize("seed", range(4))
def test_no_piece_of_a_forgotten_sentence_is_kept_as_its_salvage(seed):
    """Mac run17 (1bf694c5): after "forget everything about my sledge", the learned line "Remember that my sledge is
    lilac" stayed: the forget of the whole sentence kept its first clause as what survives of the line."""
    from core.vool_memory import _forget_salvage_content

    rng = random.Random(f"salvage-{seed}")
    thing, colour, place = rng.choice(["sledge", "easel", "kayak"]), rng.choice(["lilac", "navy"]), rng.choice(
        ["garage", "loft"])
    said = rng.choice([f"Remember that my {thing} is {colour} and lives in the {place}",
                       f"Note: my {thing} is {colour}, it's kept in the {place}"])
    assert _forget_salvage_content(said, said.lower()) == ""
    # A value forgotten out of a longer line still leaves the line's other facts.
    assert _forget_salvage_content(f"The gate code is 4411 and my {thing} is {colour}.", "4411") == (
        f"my {thing} is {colour}.")
