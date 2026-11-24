"""A fact the person asks VOOL to keep is never cut at the dot of a title, an initial or an abbreviation.

Fresh-question audit of 02ff11496 (German): "Mein Zahnarzt heißt Dr. Albrecht und die Praxis ist in der Lindenstraße"
was saved cut at "Dr.", and "Wie heißt mein Zahnarzt?" was answered "Albrecht und die Praxis ...". Titles, names,
languages and leads are drawn at random; the turn's judge is its record.
"""
from __future__ import annotations

import random

import pytest

from core.persistent_memory import _SENTENCE_SPLIT_RE, maybe_handle_memory_command
from tests.test_memory_asked_in_any_language_v08 import (
    CHAT,
    OWNER,
    _entries,
    _fresh,
    _judged,
    home,
    isolated_home,
)
from tests.test_memory_forget_resurrection_p0 import _policy

__all__ = ["_fresh", "home", "isolated_home"]  # fixtures this module uses

FACTS = (
    ("de", "Mein Zahnarzt heißt {title} {name} und die Praxis ist in der Lindenstraße"),
    ("lt", "Mano odontologas yra {title} {name}, kabinetas Gedimino prospekte"),
    ("en", "my dentist is {title} {name} and the practice is on Linden Street"),
    ("pl", "Mój dentysta to {title} {name}, gabinet jest na ul. Długiej"),
)


@pytest.mark.parametrize("seed", range(8))
def test_a_kept_fact_with_a_title_is_saved_whole(home, seed):
    rng = random.Random(f"title-{seed}")
    _lang, shape = rng.choice(FACTS)
    title = rng.choice(["Dr.", "Hr.", "Prof.", "Fr.", "Mr.", "dr."])
    name = rng.choice(["Albrecht", "J. Brandt", "Kazlauskas", "A. Nowak"])
    lead = rng.choice(["Merk dir bitte:", "Įsimink:", "Keep this in mind:", "Zapamiętaj:"])
    text = f"{lead} {shape.format(title=title, name=name)}."
    _judged(text, "keep")
    handled, reply = maybe_handle_memory_command(text, session_id=CHAT, access_policy=_policy(CHAT),
                                                 source_context=dict(OWNER))
    assert handled, reply
    saved = _entries()
    assert f"{title} {name}" in saved, saved


@pytest.mark.parametrize("text, parts", [
    ("Mein Zahnarzt heißt Dr. Albrecht. Die Praxis ist in der Lindenstraße.", 2),
    ("My bike is blue. It lives in the shed!", 2),
    ("Ask Hr. Brandt; he has the key", 2),
    ("Wir treffen uns am 5. Mai bei Prof. Kim", 1),
    ("line one\nline two", 2),
])
def test_sentences_are_cut_only_at_their_ends(text, parts):
    assert len([part for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]) == parts
