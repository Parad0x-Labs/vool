"""With nothing connected, an owner's mail or calendar ask in any language is answered plainly.

Measured 2026-10-30 on qwen3:8b (dev mc-07): "ar gavau šiandien laišką iš banko?" (Lithuanian:
did I get a letter from the bank today?) reached the model with no email account connected, the
English-only mailbox recognizer missed it, and the reply claimed "no letter from the bank today" --
a check that never ran. The owner's mail and calendar reads in Lithuanian, French, Spanish,
German, Russian and Polish (and Portuguese, Italian) now get the same plain "not connected"
answer English gets, before any model. How-to and drafting asks, calendar writes, remote turns
and a connected or unknown account keep their own lanes.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import (
    calendar_not_connected_reply,
    email_not_connected_reply,
    owner_source_ask_in_other_language,
)

OWNER = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
         "runtime_session_id": "s-mail", "turn_id": "t-mail"}
REMOTE = {**OWNER, "_owner_local": False}

MAIL_READS = [
    "ar gavau šiandien laišką iš banko?",                 # lt (dev mc-07, regression anchor)
    "ar turiu naujų laiškų pašte?",                        # lt
    "est-ce que j'ai reçu un courriel de ma banque ?",     # fr
    "il y a des nouveaux mails dans ma boîte de réception ?",  # fr
    "¿tengo correos nuevos del banco?",                    # es
    "¿me llegó algún email de mi casero?",                 # es
    "habe ich heute eine E-Mail von der Bank bekommen?",   # de
    "was ist neu in meinem Posteingang?",                  # de
    "мне пришло письмо из банка?",                          # ru
    "есть что-то новое у меня на почте?",                   # ru
    "czy dostałem maila z banku?",                         # pl
    "co nowego w mojej skrzynce?",                         # pl
]
CALENDAR_READS = [
    "ką turiu kalendoriuje rytoj?",                        # lt
    "qu'est-ce que j'ai dans mon calendrier demain ?",     # fr
    "¿tengo algo en el calendario el viernes?",            # es
    "welche Termine habe ich diese Woche?",                # de
    "что у меня в календаре на завтра?",                    # ru
    "jakie mam spotkania w tym tygodniu?",                 # pl
]
NOT_A_SOURCE_ASK = [
    "comment écrire un courriel formel ?",                 # how-to (knowledge)
    "¿cómo se escribe una carta de queja?",
    "wie schreibe ich eine höfliche E-Mail?",
    "как написать письмо начальнику?",
    "écris un mail poli à mon propriétaire",               # drafting, not a mailbox read
    "napisz maila do mojego szefa",
    "quelle est la capitale de l'Australie ?",             # plain knowledge
    "¿cuánto tiempo se hierve un huevo?",
    "wie spät ist es in Tokio?",
]
CALENDAR_WRITES = [
    "ajoute un rendez-vous chez le dentiste mardi à 15h dans mon calendrier",
    "dodaj spotkanie do mojego kalendarza w sobotę",
    "trag mir einen Termin am Freitag ein",
]


@pytest.fixture
def nothing_connected(monkeypatch):
    monkeypatch.setattr("core.action_claim_gate._mail_connected", lambda: False)
    monkeypatch.setattr("core.action_claim_gate._calendar_connected", lambda: False)


@pytest.mark.parametrize("text", MAIL_READS)
def test_a_mail_ask_in_another_language_says_mail_is_not_connected(text: str, nothing_connected) -> None:
    reply = email_not_connected_reply(text, dict(OWNER))
    assert reply, text
    assert "connect" in reply.lower() or "connecté" in reply.lower() or "conectad" in reply.lower() or "verbunden" in reply.lower()
    assert calendar_not_connected_reply(text, dict(OWNER)) is None
    assert email_not_connected_reply(text, dict(REMOTE)) is None


@pytest.mark.parametrize("text", CALENDAR_READS)
def test_a_calendar_read_in_another_language_says_no_calendar_is_connected(text: str, nothing_connected) -> None:
    reply = calendar_not_connected_reply(text, dict(OWNER))
    assert reply, text
    assert email_not_connected_reply(text, dict(OWNER)) is None
    assert calendar_not_connected_reply(text, dict(REMOTE)) is None


@pytest.mark.parametrize("text", [*NOT_A_SOURCE_ASK, *CALENDAR_WRITES])
def test_knowledge_drafting_and_calendar_writes_keep_their_lanes(text: str, nothing_connected) -> None:
    assert owner_source_ask_in_other_language(text) == "" or text in CALENDAR_WRITES
    assert email_not_connected_reply(text, dict(OWNER)) is None, text
    assert calendar_not_connected_reply(text, dict(OWNER)) is None, text


def test_a_connected_or_unknown_source_is_not_answered_from_a_fact(monkeypatch) -> None:
    for state in (True, None):
        monkeypatch.setattr("core.action_claim_gate._mail_connected", lambda s=state: s)
        monkeypatch.setattr("core.action_claim_gate._calendar_connected", lambda s=state: s)
        assert email_not_connected_reply(MAIL_READS[0], dict(OWNER)) is None
        assert calendar_not_connected_reply(CALENDAR_READS[0], dict(OWNER)) is None
