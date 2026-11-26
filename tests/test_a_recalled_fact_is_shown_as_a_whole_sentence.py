"""A saved fact said back is closed as a sentence, so the person's own words never read as an answer cut off.

iMac gate 43 on the installed 7eaa8608c: the fact "my sister Ilse turns 40 on the 9th of April, please remember that"
came back "You told me: ... please remember that (Incomplete: the last sentence ends mid-clause. Ask again for the
rest.)", in a new chat, after a chat delete and after a restart."""
from __future__ import annotations

from core.incomplete_answer import inspect_answer_completeness
from core.persistent_memory import _said_to_its_end


def test_a_fact_ending_on_a_word_a_sentence_cannot_end_on_is_closed_and_reads_complete():
    fact = "my nephew Tomas starts school on the 2nd of September, keep that"
    shown = _said_to_its_end(fact)
    assert shown == fact + "."
    assert not inspect_answer_completeness(f"You told me: {shown}").incomplete


def test_a_fact_already_closed_is_left_as_said():
    for fact in ("the gate code is 4417.", "is the van booked?", "the bins go out on Tuesdays!", "she said “yes”"):
        assert _said_to_its_end(fact) == fact.rstrip()


def test_every_language_is_closed_the_same_way():
    assert _said_to_its_end("mano dukra gimė gegužės 3, atsimink tai") == "mano dukra gimė gegužės 3, atsimink tai."
    assert _said_to_its_end("我的生日是五月三日。") == "我的生日是五月三日。"
