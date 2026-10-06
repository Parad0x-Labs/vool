"""A directive-shaped sentence that names the subject stays in the recall query.

Defect (independent verification of ed5ab909, 2026-10-02): the answer-frame
filter removed any sentence that opened with a reply verb and contained one
frame word, even when the rest of the sentence named the subject. "Tell me
what you know about the dentist visit. What did she find?" was reduced to
"What did she find?", so recall ran on a pronoun-only question and delivered
a wrong-subject record or nothing.

Contract: a directive sentence is removed only when every word in it is a
function word or reply-frame vocabulary (the act of replying, its manner and
shape, the responder's knowledge state, where the reply may draw from). Any
other word is subject content and keeps the sentence; a topical preposition
("about", "regarding", "concerning") naming a non-pronoun object keeps it too.
Every store below is synthetic (hash embedding lane, zero network).
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from tests.test_recall_answer_frame_subject_20261002 import _capsule, _turn

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (  # noqa: F401
        fresh_profile,
    )
except ImportError:  # pragma: no cover
    fresh_profile = None


# ── the view: subject-bearing directives are kept ──────────────────────────

SUBJECT_DIRECTIVES = [
    "Tell me what you know about the boiler service. What did the engineer replace?",
    "Tell me what you know about the allotment plan. Which vegetables did we pick?",
    "Just tell me in a few words about the reunion dinner. Which restaurant did we book?",
    "Be brief about my shoulder injury recovery. How long until I could swim?",
    "Give me a short answer on the roof repair. Who did the work?",
    "Answer briefly about the tax refund. When did it arrive?",
    "Keep it short, the piano lessons matter most. Who teaches them?",
    # frame vocabulary as the named topic: the preposition keeps it
    "Tell me what you know about our earlier chats. What did I ask first?",
    "Say what you know regarding the notes. Which one was missing?",
    # sloppy register still carrying a subject
    "pls tell me what u know about the car insurance. when does it renew",
    "tell me wat u know abt the dog walker. when does she come",
    "just tell me short about kayak club. who runs it",
    "be brief abt my dentist apointment. what time is it",
    "Say what u know about the bike lock!! whats the code",
    "answer quick re the parking permit\nwhen does it expire",
]


@pytest.mark.parametrize("turn", SUBJECT_DIRECTIVES)
def test_directive_that_names_the_subject_is_kept(turn):
    assert cr._answer_frame_free_query(turn) == turn


PURE_FRAME = [
    ("Answer with only the prior conversations you were given. You may use only "
     "answers the records support. If the records do not cover it, say you do "
     "not know. Give a concise final answer.\nWhat colour did Ines paint the gate?",
     "What colour did Ines paint the gate?"),
    ("If you are unsure about anything, say you do not know. Where is the ladder?",
     "Where is the ladder?"),
    ("Be brief about it. Who fed the hens?", "Who fed the hens?"),
    ("Keep it short. Tell me about the ferry trip. Where did we sit?",
     "Tell me about the ferry trip. Where did we sit?"),
    ("dont guess or make it up, answer from my notes only. what did i name the goat",
     "what did i name the goat"),
]


@pytest.mark.parametrize("turn,subject", PURE_FRAME)
def test_pure_reply_frame_sentences_are_still_removed(turn, subject):
    assert cr._answer_frame_free_query(turn) == subject


# ── end to end through the capsule (falsifies ed5ab909 / 3d0469cc) ─────────

SCENARIOS = [
    # The bare question shares no stem with the subject record (irregular
    # past tense, a pronoun subject): only the directive sentence ties them.
    (
        "cd-boiler",
        "Nadia: The boiler service went fine, he brought a new ignition electrode along.",
        ["Nadia: He brought flowers for the whole office on Friday.",
         "Nadia: He brought his old guitar to the party.",
         "Nadia: He brought the wrong charger again.",
         "Nadia: He brought soup round when I was ill."],
        "Tell me what you know about the boiler service. What did he bring?",
        "ignition electrode",
    ),
    (
        "cd-allotment",
        "Corwin: For the allotment plan we grew leeks and broad beans in the top bed.",
        ["Corwin: We grew tired of the long commute.",
         "Corwin: The kids grew two inches over the summer.",
         "Corwin: We grew apart from the old neighbours.",
         "Corwin: She grew her hair out after the wedding."],
        "Tell me what you know about the allotment plan. What did we grow?",
        "broad beans",
    ),
    (
        "cd-reunion",
        "Halima: At the reunion dinner we ate at the Saltmarsh Grill by the harbour.",
        ["Halima: We ate at the airport before the night flight.",
         "Halima: We ate at home because of the rain.",
         "Halima: We ate at the Larkspur Cafe after the book club.",
         "Halima: We ate at her sister's place on Sunday."],
        "Just tell me in a few words about the reunion dinner. Where did we eat?",
        "Saltmarsh Grill",
    ),
]


@pytest.mark.parametrize("chat,decisive,distractors,question,answer", SCENARIOS)
def test_subject_in_a_directive_sentence_reaches_recall(
        fresh_profile, chat, decisive, distractors, question, answer):
    """The question alone ("What did he bring?") matches no subject word; the
    distractors match it as well as the record does. Only the directive names the subject. With
    that sentence removed recall ran on the pronoun question and delivered a
    wrong-subject record or nothing; kept, the subject record rides."""
    _turn(fresh_profile, chat, decisive)
    for line in distractors:
        _turn(fresh_profile, chat, line)
    assert answer in _capsule(fresh_profile, chat, question)
