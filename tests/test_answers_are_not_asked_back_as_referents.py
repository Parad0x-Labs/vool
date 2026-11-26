"""An ambiguity verdict whose "referents" are answers to the question, not meanings of a name in it, asks nothing.

iMac gate34 on 571e05675 (qwen3:8b): "who composed The Four Seasons?" was judged ambiguous between "Antonio Vivaldi" and
"Giuseppe Tartini", and the person was asked which one they meant. Referents of an ambiguous name carry the name.
"""
from __future__ import annotations

from core.entity_ambiguity import AmbiguityVerdict, referents_read_the_question


def _verdict(*referents: str) -> AmbiguityVerdict:
    return AmbiguityVerdict(ambiguous=True, referents=tuple(referents), clarification="Which one do you mean?")


def test_answers_are_not_referents() -> None:
    assert not referents_read_the_question(_verdict("Ludwig van Beethoven", "Franz Schubert"),
                                           "who wrote the Moonlight Sonata?")
    assert not referents_read_the_question(_verdict("Gustave Eiffel", "Maurice Koechlin"),
                                           "kas suprojektavo Eifelio bokštą?")


def test_meanings_of_a_name_are_referents() -> None:
    assert referents_read_the_question(_verdict("Portland, Oregon", "Portland, Maine"), "how big is Portland?")
    assert referents_read_the_question(_verdict("Mercury (planet)", "Mercury (element)", "Freddie Mercury"),
                                       "tell me about mercury")
    assert referents_read_the_question(_verdict("Córdoba, Spain", "Córdoba, Argentina"), "kiek gyventojų Cordoboje? cordoba")


def test_one_referent_or_none_keeps_the_ask_as_it_was() -> None:
    assert referents_read_the_question(_verdict("Someone"), "who is it?")
    assert referents_read_the_question(_verdict(), "which one?")
