"""Recall runs on the question's subject, not on its answer-frame directives.

Measured defect (probe-20 dev set, 2026-10-02): a reader turn that wraps its
question in instructions about HOW to answer ("Answer from the earlier
conversations only. If they do not support an answer, say you do not know.
Give a concise answer. <question>") fed those directive sentences into every
recall leg. Their generic words ranked unrelated records above the one the
question is about (lexical rank 12-103 instead of 0-2), and the facet gate
read them as asked attributes absent from every record, so it dropped the
answer-bearing record as noise.

Contract: ``_answer_frame_free_query`` removes sentences that only instruct the
responder how to reply, when a question survives; content sentences, names,
digits, quotes and option lists are never removed. Every store below is
synthetic (hash embedding lane, zero network).
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


# ── the view itself: semantic family ─────────────────────────────────────

FRAMED = [
    # original reported shape (own wording, not the benchmark's)
    ("Answer only from what we discussed earlier. If our chats do not cover it, "
     "say you do not know. Give a short final answer.\nWhat colour is Oskar's canoe?",
     "What colour is Oskar's canoe?"),
    # clean paraphrases
    ("Respond using only my earlier messages. Which bakery did Lena pick?",
     "Which bakery did Lena pick?"),
    ("You should base your reply on what I told you before. Where does Priya keep the spare key?",
     "Where does Priya keep the spare key?"),
    ("Keep the answer to one sentence. How long is the drive to the cabin?",
     "How long is the drive to the cabin?"),
    ("Please answer briefly. Who fixed the porch light?", "Who fixed the porch light?"),
    ("If you are not certain, tell me you don't know. What breed is the neighbour's dog?",
     "What breed is the neighbour's dog?"),
    ("Provide a concise response. Which train did Amaru miss?", "Which train did Amaru miss?"),
    # sloppy / user-typed
    ("pls answer short. where did i park the van", "where did i park the van"),
    ("if ur not sure say u dont know. what is my bike called?", "what is my bike called?"),
    ("dont guess, answer from what i told u.\nwhich gym did i join?", "which gym did i join?"),
    ("keep it short!! what did the plumber charge", "what did the plumber charge"),
    ("answer in a few words\nwho won the quiz night?", "who won the quiz night?"),
    # trailing directive
    ("When did Farid repaint the shed? Answer with just the month.",
     "When did Farid repaint the shed?"),
    # directive between content and question keeps the content sentence
    ("Answer briefly. My sister moved last spring. When is her birthday?",
     "My sister moved last spring. When is her birthday?"),
]


@pytest.mark.parametrize("turn,subject", FRAMED)
def test_answer_frame_directives_leave_the_recall_subject(turn, subject):
    assert cr._answer_frame_free_query(turn) == subject


UNCHANGED = [
    # content sentences carry the subject: never removed
    "My sister lives near the old mill. When is her birthday?",
    "List all the plants I bought last month. Which one needs shade?",
    # names, digits, quotes and option lists are content-shaped: kept
    "Tell me about the trip to Porto. Where did we stay?",
    "Use the 2022 receipts to answer. What did I pay for the boiler?",
    'Say "welcome back" to the team. What did Mira reply?',
    "Pick the right option: (a) red (b) blue. Which colour did Joaquim choose?",
    # a directive-only turn has no surviving question: unchanged
    "Answer briefly.",
    "Keep it short and say you don't know if unsure.",
    # adversarial near-misses: imperative register, but about the WORLD, not the reply
    "Give the dog his pills at eight. What did the vet say about the dosage?",
    "Keep the receipts in the blue folder. Where did I put the warranty card?",
    "If it rains, the match moves indoors. When is the match?",
    # a single sentence is never split
    "Answer this for me: which day is the bin collection?",
]


@pytest.mark.parametrize("turn", UNCHANGED)
def test_content_and_unframed_turns_are_not_rewritten(turn):
    assert cr._answer_frame_free_query(turn) == turn


# ── end to end through the capsule (falsifies the parent) ─────────────────

def _turn(home, chat, user):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(chat, user, "", access_policy=policy,
                         source_context={"chat_id": chat, "runtime_home": home})


def _capsule(home, chat, question):
    policy = resolve_memory_access_policy(chat_id=chat)
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy, source_context={"chat_id": chat, "runtime_home": home})
    return "\n".join(str(m.get("content") or "") for m in out
                     if "retrieved_context" in str(m.get("content")))


SCENARIOS = [
    (
        "af-kayak",
        "Marisol: Oh the kayak? Everyone calls it Driftwood Polly now.",
        ["Marisol: The boathouse roof leaked again after the storm.",
         "Teodor: We painted the boathouse door green last spring.",
         "Marisol: The paddles hang on the left wall of the boathouse.",
         "Teodor: I prefer early mornings on the lake."],
        "Reply using only what we discussed. Keep it brief, and if you are unsure, "
        "just say you do not know. What does Marisol call her kayak?",
        "What does Marisol call her kayak?",
        "Driftwood Polly",
    ),
    (
        "af-orchard",
        "Benedikt: Oh, the orchard pears are called Copper Bell, odd little things.",
        ["Benedikt: The orchard fence came down in the wind on the north side.",
         "Ayumi: The ladder by the orchard gate needs a new rung.",
         "Benedikt: Bees were busy around the orchard hives all afternoon.",
         "Ayumi: Let's walk the orchard path after lunch."],
        "Answer from our earlier messages only. If they do not cover it, say you do not know. "
        "Give a concise final answer.\nWhat are the orchard pears called?",
        "What are the orchard pears called?",
        "Copper Bell",
    ),
    (
        "af-studio",
        "Wanjiru: Ha, the studio cat? We named him Biscuit Major.",
        ["Wanjiru: The studio heater rattles whenever it switches on.",
         "Tomasz: I swept the studio floor and moved the easels.",
         "Wanjiru: The studio skylight lets in great morning light.",
         "Tomasz: Lunch at the studio was soup again."],
        "pls answer short, dont guess. if ur not sure say u dont know. "
        "what is the studio cat named?",
        "what is the studio cat named?",
        "Biscuit Major",
    ),
]


@pytest.mark.parametrize("chat,decisive,distractors,question,bare,answer", SCENARIOS)
def test_framed_question_recalls_the_subject_record(
        fresh_profile, chat, decisive, distractors, question, bare, answer):
    """The answer-bearing record is retained-only (below index admission), so
    the lexical evidence leg and the facet gate decide whether it rides. With
    the directive sentences steering them it was dropped; on the subject it
    rides."""
    assert _turn(fresh_profile, chat, decisive)["status"] == "retained"
    for line in distractors:
        _turn(fresh_profile, chat, line)
    assert answer in _capsule(fresh_profile, chat, question)


@pytest.mark.parametrize("chat,decisive,distractors,question,bare,answer", SCENARIOS)
def test_unframed_control_question_still_recalls(
        fresh_profile, chat, decisive, distractors, question, bare, answer):
    """Control: the bare question recalled the record before and after."""
    _turn(fresh_profile, chat, decisive)
    for line in distractors:
        _turn(fresh_profile, chat, line)
    assert answer in _capsule(fresh_profile, chat, bare)


def test_absent_attribute_still_abstains_under_a_frame(fresh_profile):
    """Negative control: the facet gate keeps abstaining for an attribute the
    chat never stated, framed or not (the directive removal must not disarm
    presupposition-failure abstention)."""
    _turn(fresh_profile, "af-neg", "The sauna stove bench is cedar.")
    for q in ("What is the sauna stove thermostat set to?",
              "Answer briefly. If you are unsure, say you do not know. "
              "What is the sauna stove thermostat set to?"):
        assert "cedar" not in _capsule(fresh_profile, "af-neg", q).lower()


@pytest.mark.parametrize("chat,decisive,distractors,question,bare,answer", SCENARIOS)
def test_scenario_frames_reduce_to_their_bare_question(
        chat, decisive, distractors, question, bare, answer):
    assert cr._answer_frame_free_query(question) == bare
