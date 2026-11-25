"""A pasted email or message is the one thing an ask works on, never a list of asks.

Measured on the installed app (Mac gate 16 on 0680a8678): "What does this boil down to?" over a pasted email was
split by the conductor into four requests, and its header lines and body came back as "I couldn't do this part".
The email is read by its shape in any language: header lines (label, colon, value) with an address, a blank line,
a body. All names and addresses here are invented.
"""
from __future__ import annotations

import pytest

from core.memory.admission import holds_a_pasted_message

_EMAILS = [
    "What does this boil down to?\n\nFrom: Harbour Kayaks <shop@example.net>\nSubject: Your order\n\n"
    "Your paddle has shipped and the invoice is attached.",
    "Fasse das bitte kurz zusammen:\n\nVon: Stadtwerke Kiel <service@example.de>\nBetreff: Zählerstand\n"
    "Datum: 3. März\n\nBitte melden Sie Ihren Zählerstand bis Freitag.",
    "Von: Ola <ola@example.pl>\nTemat: Sobota\n\nCzy możemy przenieść spotkanie na niedzielę?",
    "kas čia parašyta?\n\nNuo: Biblioteka <info@example.lt>\nTema: Knyga\n\nJūsų knyga laukia skyriuje iki penktadienio.",
]
_NOT_EMAILS = [
    "Save this contact:\nName: Ines Halloran\nEmail: ines@example.org",
    "Name: Ines Halloran\nEmail: ines@example.org\nPhone: +44 7700 900123",
    "What is 3:15 in minutes? And note: the bus leaves at 4:20.",
    "Two things: buy milk.\nAlso: call the plumber.\n\nThanks!",
    "Remember this: my sister's email is mira@example.com",
]


@pytest.mark.parametrize("text", _EMAILS)
def test_a_pasted_email_in_any_language_is_seen(text):
    assert holds_a_pasted_message(text)


@pytest.mark.parametrize("text", _NOT_EMAILS)
def test_a_contact_card_or_plain_asks_are_not_an_email(text):
    assert not holds_a_pasted_message(text)


def test_the_conductor_leaves_a_pasted_email_whole(monkeypatch):
    from core.agent_runtime import agent as agent_module

    called = []
    monkeypatch.setattr(agent_module, "_skill_keeps_turn_whole", lambda *a, **k: False)
    import core.conductor as conductor

    monkeypatch.setattr(conductor, "plan_conductor_turn", lambda *a, **k: called.append(a) or None)
    runtime = agent_module.VoolAgent.__new__(agent_module.VoolAgent)
    assert runtime._maybe_answer_conductor_turn(session_id="paste-chat", source_context={},
                                                effective_input=_EMAILS[0], raw_input=_EMAILS[0]) is None
    assert called == []
