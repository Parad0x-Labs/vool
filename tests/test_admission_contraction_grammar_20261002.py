"""Layer-2 admission grammar is contraction-tolerant.

Measured defect (parent 2686028c): the first-person admission patterns in
``_score_importance`` required whitespace after "I", so a contracted clause
never matched its own grammar: "I've been getting into classic rock." scored
0.20 (rejected) while "I have been getting into classic rock." scored 0.35
(admitted). Most conversational first-person statements are contracted, so
they never became semantic nodes. The contract: a subject contraction is the
same clause as its expanded form and must score the same; contractions that
read as "would" or as filler stay below the admission bar.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (  # noqa: F401
        fresh_profile,
    )
except ImportError:  # pragma: no cover
    fresh_profile = None

T = cr._IMPORTANCE_THRESHOLD

# (contracted as users type it, expanded twin). Each contracted form carries a
# first-person clause the admission grammar is written for.
FAMILY = [
    # original reported wording
    ("I've been getting into classic rock.", "I have been getting into classic rock."),
    # clean paraphrases, different vocabulary and shape
    ("Lately I've been learning to restore old sailboats.",
     "Lately I have been learning to restore old sailboats."),
    ("I'm planning to run a half marathon in the spring.",
     "I am planning to run a half marathon in the spring."),
    ("I'm really looking forward to the pottery workshop.",
     "I am really looking forward to the pottery workshop."),
    ("We've adopted two kittens from the county shelter.",
     "We have adopted two kittens from the county shelter."),
    ("I'd just bought a secondhand cello before the move.",
     "I had just bought a secondhand cello before the move."),
    ("I've finished the first draft of my thesis on tidal energy.",
     "I have finished the first draft of my thesis on tidal energy."),
    ("I'll use the green ledger for the bakery invoices.",
     "I will use the green ledger for the bakery invoices."),
    # sloppy / user-typed variants
    ("i've been practicing calligraphy every nite", "i have been practicing calligraphy every nite"),
    ("ive been volunteering at the food bank on weekends",
     "i have been volunteering at the food bank on weekends"),
    ("im planning to repaint the garage door blue",
     "i am planning to repaint the garage door blue"),
    ("Im really looking forward to the regatta this weekend",
     "I am really looking forward to the regatta this weekend"),
    ("I’ve been studying Portuguese for my trip to Lisbon.",
     "I have been studying Portuguese for my trip to Lisbon."),
    ("I 've been training our dog to herd ducks lol",
     "I have been training our dog to herd ducks lol"),
    ("I’m planning to switch jobs after the audit season ends",
     "I am planning to switch jobs after the audit season ends"),
    ("so yeah we've booked the cabin near the glacier",
     "so yeah we have booked the cabin near the glacier"),
]

# Contracted turns that must stay below the bar: "'d" as "would", filler,
# and an adversarial near-miss whose verb only looks like a participle.
BELOW_BAR = [
    "I'm fine, thanks for asking.",
    "I'll think about it and get back to you.",
    "I'd love to hear more about that sometime!",
    "I'd need a bigger kitchen to even try that one.",
    "Haha I'm not sure, what do you think?",
    "Ill be there around noon maybe",  # "ill" is a word; not expanded
]


@pytest.mark.parametrize("contracted,expanded", FAMILY)
def test_contracted_clause_scores_as_its_expanded_form(contracted, expanded):
    assert cr._score_importance(contracted) == cr._score_importance(expanded), (
        contracted, cr._expand_pronoun_contractions(contracted))


@pytest.mark.parametrize("contracted,expanded", FAMILY)
def test_contracted_first_person_statement_clears_admission(contracted, expanded):
    assert cr._score_importance(expanded) >= T, expanded
    assert cr._score_importance(contracted) >= T, contracted


@pytest.mark.parametrize("text", BELOW_BAR)
def test_would_and_filler_contractions_stay_below_admission(text):
    assert cr._score_importance(text) < T, text


def test_pluperfect_d_is_had_and_otherwise_would():
    expand = cr._expand_pronoun_contractions
    assert expand("I'd been saving for months") == "I had been saving for months"
    assert expand("we'd already ordered the parts") == "we had already ordered the parts"
    assert expand("I'd love to") == "I would love to"
    assert expand("I'd need a hand") == "I would need a hand"


def test_bare_forms_that_are_words_or_names_are_not_expanded():
    expand = cr._expand_pronoun_contractions
    for text in ("We were there.", "Well, it went fine.", "I felt ill.",
                 "The id field is empty.", "Jony Ive designed it.", "IM me later"):
        assert expand(text) == text, text


def test_questions_still_cannot_earn_admission_through_a_contraction():
    # A pure question is not an assertion; expansion must not change that.
    assert cr._score_importance("Have you heard what I've been working on?") < T
    assert cr._score_importance("Am I planning to go, or I'm not?") < T


def _node_count(home: str) -> int:
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=home)
    try:
        return int(mem._conn.execute("SELECT count(*) FROM memory_nodes").fetchone()[0])
    finally:
        mem.close()


def test_store_turn_indexes_a_contracted_statement_as_a_semantic_node(fresh_profile):
    """Authority seam: layer-2 admission (memory_nodes), not only the score."""
    home = fresh_profile
    chat = "contraction-admission"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    ctx = {"chat_id": chat, "runtime_home": home}
    before = _node_count(home)
    cr.store_turn(chat, "I've been getting into restoring vintage typewriters.", "",
                  access_policy=policy, source_context=ctx)
    assert _node_count(home) == before + 1
    # Filler contraction is retained at layer 1 but not indexed.
    cr.store_turn(chat, "I'm fine, thanks for asking, really appreciate it.", "",
                  access_policy=policy, source_context=ctx)
    assert _node_count(home) == before + 1
