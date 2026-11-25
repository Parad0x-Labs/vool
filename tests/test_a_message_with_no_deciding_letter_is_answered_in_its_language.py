"""A message whose language no single letter decides is answered in its own language, not the chat's earlier one.

iMac gate on f0a19600e (qwen3:8b): a German question with no umlaut, asked right after a Lithuanian turn, was answered
in Lithuanian. The exclusive-letter reading names no language for it, on purpose (no guess); nothing at all was said to
the model about language, and it went on in the chat's earlier one. Now the model is told the latest message's
language counts. Sentences drawn at random from small pools.
"""
from __future__ import annotations

import random

import pytest

from core.response_language_policy import response_language_policy_for_text, response_language_provider_instruction

UNDECIDED = ("Wie heisst {who} von meinem Bruder?", "Kannst du mir sagen, wann ich {who} zuletzt gesehen habe?",
             "Que sabes de {who} de mi hermana?", "Puedes decirme cuando vi a {who} la ultima vez?",
             "kada paskutini karta mačiau {who}?", "Que sais-tu de {who} de ma soeur?")
WHO = ("Rasa", "Jonas", "Mila", "Odile")


@pytest.mark.parametrize("seed", range(8))
def test_an_undecided_message_tells_the_model_its_latest_message_counts(seed) -> None:
    rng = random.Random(f"und-{seed}")
    text = rng.choice(UNDECIDED).format(who=rng.choice(WHO))
    policy = response_language_policy_for_text(text).to_dict()
    if policy["expected_language"] in {"none", ""}:
        line = response_language_provider_instruction(policy)
        assert "latest message" in line, (text, policy)
    else:
        assert response_language_provider_instruction(policy).startswith("Respond in") or policy["expected_language"] == "en"


def test_english_and_named_languages_keep_their_own_lines() -> None:
    assert response_language_provider_instruction(response_language_policy_for_text(
        "what did I tell you about my bike last week?").to_dict()) == ""
    assert response_language_provider_instruction({"expected_language": "lt", "reason": "operator_preference"}) == (
        "Respond in Lithuanian.")
    assert response_language_provider_instruction({"expected_language": "none"}) == ""
