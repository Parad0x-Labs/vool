"""A message whose language no single letter decides is answered in its own language, not the chat's earlier one.

iMac gate on f0a19600e (qwen3:8b): a German question with no umlaut, asked right after a Lithuanian turn, was answered
in Lithuanian. The exclusive-letter reading names no language for it, on purpose (no guess); nothing at all was said to
the model about language, and it went on in the chat's earlier one. Now the model is told the latest message's
language counts, whenever an earlier message of the chat was in another language: that is the pull. A chat kept in
English sends no extra line ("Why?" after English turns keeps its prompt byte for byte). Sentences drawn at random from
small pools.
"""
from __future__ import annotations

import random

import pytest

from core.response_language_policy import response_language_policy_for_text, response_language_provider_instruction

UNDECIDED = ("Wie heisst {who} von meinem Bruder?", "Kannst du mir sagen, wann ich {who} zuletzt gesehen habe?",
             "Que sabes de {who} de mi hermana?", "Puedes decirme cuando vi a {who} la ultima vez?",
             "kada paskutini karta mačiau {who}?", "Que sais-tu de {who} de ma soeur?")
WHO = ("Rasa", "Jonas", "Mila", "Odile")
EARLIER_OTHER = ("Ką veikė Rūta praėjusį savaitgalį?", "Wie geht's dem Hund meiner Schwester? Er heißt Bruno.",
                 "¿Dónde está la llave del garaje?", "Qu'est-ce que j'ai dit à propos de la fête d'été ?")
EARLIER_ENGLISH = ("What makes database indexes useful?", "Remind me what I said about the garden shed.")


@pytest.mark.parametrize("seed", range(8))
def test_an_undecided_message_tells_the_model_its_latest_message_counts(seed) -> None:
    rng = random.Random(f"und-{seed}")
    text = rng.choice(UNDECIDED).format(who=rng.choice(WHO))
    policy = response_language_policy_for_text(text).to_dict()
    if policy["expected_language"] in {"none", ""}:
        line = response_language_provider_instruction(policy, earlier_user_texts=[rng.choice(EARLIER_OTHER)])
        assert "latest message" in line, (text, policy)
        plain = not any(ch.isalpha() and ord(ch) > 127 for ch in text)
        alone = response_language_provider_instruction(policy, latest_text=text)
        after_english = response_language_provider_instruction(
            policy, earlier_user_texts=list(EARLIER_ENGLISH), latest_text=text)
        if plain:
            assert alone == "" and after_english == "", "no letter of its own and nothing earlier pulls"
        else:
            assert "latest message" in alone and "latest message" in after_english, (text, policy)
    else:
        assert response_language_provider_instruction(policy).startswith("Respond in") or policy["expected_language"] == "en"


def test_english_and_named_languages_keep_their_own_lines() -> None:
    assert response_language_provider_instruction(response_language_policy_for_text(
        "what did I tell you about my bike last week?").to_dict()) == ""
    assert response_language_provider_instruction({"expected_language": "lt", "reason": "operator_preference"}) == (
        "Respond in Lithuanian.")
    assert response_language_provider_instruction({"expected_language": "none"}) == ""


@pytest.mark.parametrize("latest", ["jaki kolor ma czapka mojej żony?", "kada išvažiuoja traukinys į Kauną?",
                                    "Que sais-tu de la tante de Léa ?"])
def test_a_message_with_its_own_non_english_letters_is_answered_in_it_after_english_turns(latest) -> None:
    """iMac gate on 3dd1243b9: a Polish question whose "ą" names no single language, asked after an English turn, was
    answered in English."""
    policy = response_language_policy_for_text(latest).to_dict()
    if policy["expected_language"] in {"none", ""}:
        line = response_language_provider_instruction(policy, earlier_user_texts=list(EARLIER_ENGLISH), latest_text=latest)
        assert "latest message" in line, (latest, policy)


@pytest.mark.parametrize("latest", ["Why?", "Run the tests.", "Explain that more."])
def test_an_english_chat_sends_no_extra_line(latest) -> None:
    policy = response_language_policy_for_text(latest).to_dict()
    assert response_language_provider_instruction(
        policy, earlier_user_texts=list(EARLIER_ENGLISH), latest_text=latest) == ""
