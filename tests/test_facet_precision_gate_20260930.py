"""Fresh-wording acceptance for the facet-precision (presupposition-failure)
gate, 2026-09-30. New domains/wordings vs every prior corpus: an ask whose
ATTRIBUTE the chat never asserted must not pack its subject-tied records
(clean abstention); every protected class must keep serving.
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

pytestmark = pytest.mark.usefixtures("fresh_profile")


def _turn(home, chat, user, assistant="Noted."):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(
        chat, user, assistant, access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home})


def _capsule(home, chat, question, as_of=None):
    policy = resolve_memory_access_policy(chat_id=chat)
    src = {"chat_id": chat, "runtime_home": home}
    if as_of:
        src["question_as_of"] = as_of
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy, source_context=src)
    return "\n".join(
        str(m.get("content") or "") for m in out
        if "retrieved_context" in str(m.get("content")))


# ── suppression direction ────────────────────────────────────────────────

def test_absent_measure_ask_abstains_cleanly(fresh_profile):
    _turn(fresh_profile, "g1-sauna",
          "The sauna stove bench is cedar.", "Cedar bench, noted.")
    block = _capsule(fresh_profile, "g1-sauna",
                     "What is the sauna stove thermostat set to?")
    assert "cedar" not in block.lower()


def test_absent_actor_ask_abstains_cleanly(fresh_profile):
    # the asked ACTION word is absent from the record (only "rewoven"
    # describes it), so the actor ask has two absent discriminators
    _turn(fresh_profile, "g1-lift",
          "The chairlift cable was rewoven with a woven core.",
          "Woven core cable, noted.")
    block = _capsule(fresh_profile, "g1-lift", "Who installed the chairlift cable?")
    assert "woven" not in block.lower()


def test_asked_action_present_serves_fail_open(fresh_profile):
    """Fail-open control: when the record DOES attest the asked action word
    ("replaced"), only the actor is unstated and the record serves."""
    _turn(fresh_profile, "g1-lift2",
          "The chairlift cable was replaced with a woven core.",
          "Woven core cable, noted.")
    block = _capsule(fresh_profile, "g1-lift2",
                     "Who replaced the chairlift cable?")
    assert "woven" in block.lower()


def test_absent_qualifier_ask_abstains_cleanly(fresh_profile):
    _turn(fresh_profile, "g1-apiary",
          "The beginner course fee is 60 crowns.", "Beginner fee 60 crowns, noted.")
    block = _capsule(fresh_profile, "g1-apiary",
                     "What is the advanced winter course fee?")
    assert "60" not in block


# ── protected classes keep serving ───────────────────────────────────────

def test_live_reading_under_current_ask_serves(fresh_profile):
    _turn(fresh_profile, "g2-baro",
          "Now the greenhouse hygrometer reads 71 percent.",
          "71 percent right now, noted.")
    block = _capsule(fresh_profile, "g2-baro",
                     "What is the greenhouse humidity at the moment?")
    assert "71" in block


def test_shared_attribute_short_fact_serves(fresh_profile):
    _turn(fresh_profile, "g2-press",
          "The cider press burst disc is rated 8 bar.",
          "8 bar burst disc, noted.")
    block = _capsule(fresh_profile, "g2-press",
                     "What is the burst disc rating right now?")
    assert "8 bar" in block


def test_correction_of_stored_value_serves(fresh_profile):
    _turn(fresh_profile, "g2-lock",
          "The boathouse padlock code is 4-4-0.",
          "440, noted.")
    _turn(fresh_profile, "g2-lock",
          "Correction: the padlock code is 7-7-2.",
          "772, noted.")
    block = _capsule(fresh_profile, "g2-lock", "What is the padlock code?")
    assert "7-7-2" in block


def test_assistant_paraphrase_answer_serves(fresh_profile):
    _turn(fresh_profile, "g2-compass",
          "Which hand-bearing compass should I keep at the helm?",
          "Keep the Plastimo Iris 50 in the helm drawer - it floats and reads head-up.")
    block = _capsule(fresh_profile, "g2-compass",
                     "What hand-bearing compass did you recommend?")
    assert "Iris 50" in block


def test_multi_facet_and_asof_shapes_keep_operands(fresh_profile):
    _turn(fresh_profile, "g2-mill",
          "The northern weir gauged 3.1 metres.",
          "Northern weir 3.1 m, noted.")
    _turn(fresh_profile, "g2-mill",
          "The southern weir gauged 2.4.",
          "Southern weir 2.4 m, noted.")
    block = _capsule(fresh_profile, "g2-mill",
                     "What do the two weirs gauge?")
    assert "3.1" in block and "2.4" in block


def test_history_ask_keeps_its_own_laws(fresh_profile):
    _turn(fresh_profile, "g2-toll",
          "The toll was 4 guilders before the spring revision.",
          "4 guilders before spring, noted.")
    _turn(fresh_profile, "g2-toll",
          "It rose to 6 guilders in the spring revision.",
          "6 guilders from spring, noted.")
    block = _capsule(fresh_profile, "g2-toll",
                     "What was the toll before the spring revision?")
    assert "4 guilders" in block
