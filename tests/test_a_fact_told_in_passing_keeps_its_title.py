"""A fact told in passing is kept whole: the keep step never cuts it at the dot of a title or an initial.

Mac gate 65 on b259d3e55 (packaged copy, qwen3:8b): "my dentist is Dr. Okafor, her clinic is on Linden Street next to
the bakery" was kept as "my dentist is Dr." -- the name and the clinic were lost -- and "who's my dentist again?",
"and where's her clinic?" and "what's my dentist called?" were all answered "my dentist is Dr.". The memory lane read
such a sentence whole since 02ff11496; the keep step, which writes what is told without a "remember" lead, still cut
at every dot. Both now read by one rule (`core.memory.sentences`). Titles, names, places and languages are drawn at
random.
"""
from __future__ import annotations

import random

import pytest

from core.memory import everyday_remember as everyday
from tests.test_keep_receipt_decides_save_claims_v08 import _facts, _fresh, _turn
from tests.test_memory_forget_resurrection_p0 import isolated_home

__all__ = ["_fresh", "isolated_home"]  # fixtures this module uses

TITLES = ("Dr.", "Mr.", "Mrs.", "Ms.", "St.", "Hr.", "Fr.", "J.", "K.")
NAMES = ("Okafor", "Brandt", "Kazlauskas", "Nowak", "Rossi", "Lindqvist")
SHAPES = (
    "my {role} is {title} {name}, her clinic is on {street} next to the {shop}",
    "my {role} is {title} {name} and the practice is on {street}",
    "Mein {role_de} heißt {title} {name} und die Praxis ist in der {street}",
    "Mano {role_lt} yra {title} {name}, kabinetas {street}",
    "mi {role_es} es el {title} {name}, en la {street}",
)


def _told(rng: random.Random) -> tuple[str, str]:
    title, name = rng.choice(TITLES), rng.choice(NAMES)
    text = rng.choice(SHAPES).format(
        role=rng.choice(("dentist", "GP", "physio", "vet")), role_de=rng.choice(("Zahnarzt", "Hausarzt")),
        role_lt=rng.choice(("odontologas", "gydytojas")), role_es=rng.choice(("dentista", "médico")),
        title=title, name=name, street=rng.choice(("Linden Street", "Mill Road", "Lindenstraße", "Gedimino gatvė")),
        shop=rng.choice(("bakery", "pharmacy", "post office")),
    )
    return text, f"{title} {name}"


@pytest.mark.parametrize("seed", range(16))
def test_the_keep_step_reads_a_told_fact_with_a_title_as_one_sentence(seed):
    rng = random.Random(f"told-title-{seed}")
    text, titled = _told(rng)
    read = [*everyday.stated_facts(text), *everyday.unlisted_statements(text)]
    assert read == [text], read
    assert titled in read[0]


@pytest.mark.parametrize("seed", range(6))
def test_a_told_fact_with_a_title_is_kept_whole(isolated_home, seed):
    rng = random.Random(f"kept-title-{seed}")
    title, name = rng.choice(TITLES), rng.choice(NAMES)
    street = rng.choice(("Linden Street", "Mill Road", "Harbour Lane"))
    text = f"my {rng.choice(('dentist', 'GP', 'physio'))} is {title} {name}, her clinic is on {street}"

    _handled, _reply, receipt = _turn(text)

    assert receipt["saved"] and all(f"{title} {name}" in fact and street in fact for fact in receipt["saved"]), receipt
    assert any(f"{title} {name}" in fact and street in fact for fact in _facts()), _facts()


@pytest.mark.parametrize("text, parts", [
    ("my cat is called Pixel. She sleeps on the radiator", 2),
    ("our boiler is in the attic. The key is under the mat!", 2),
    ("my GP is Dr. Weiss. Her surgery opens at eight", 2),
])
def test_a_sentence_that_ends_still_ends(text, parts):
    from core.memory.sentences import SentenceSplitter

    assert len([part for part in SentenceSplitter(marks=".!?").split(text) if part.strip()]) == parts
