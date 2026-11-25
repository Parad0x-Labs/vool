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


def test_the_kind_of_task_is_read_from_the_persons_own_words():
    """Mac gate 17 on f04d8289c: the pasted email's words made "Can you sum this up for me?" integration work, and the
    summary came with a plan's step lines glued on."""
    from core.task_router import classify

    asked = "Can you sum this up for me?"
    pasted = (f"{asked}\n\nFrom: Riverside Builders <office@example.net>\nSubject: Meeting about the sync\n\n"
              "We will install the new boiler after the meeting and email you the invoice.")
    assert classify(pasted)["task_class"] == classify(asked)["task_class"]


_ASKED_IN_TWO_PARTS = ("Kannst du mir sagen, worum es hier geht?\n\nFrom: Marina Lotte <desk@example.it>\nSubject: Booking\n\n"
                       "Your mooring at berth 12 is confirmed; the deposit is due by 20 May.")


@pytest.mark.parametrize("text", _EMAILS[:2] + _EMAILS[3:] + [_ASKED_IN_TWO_PARTS])
def test_the_words_in_front_of_a_pasted_email_are_the_ask(text):
    """Mac gate 18 on bb5351e0e: a mail under a short ask was read whole by the tool gate."""
    from core.agent_runtime.turn_planner import pasted_material_head
    from core.memory.admission import text_before_a_pasted_message

    assert pasted_material_head(text) == text_before_a_pasted_message(text).strip(" \t\r\n:-")


def test_an_email_pasted_with_no_ask_in_front_has_no_head():
    from core.agent_runtime.turn_planner import pasted_material_head

    assert pasted_material_head(_EMAILS[2]) == ""


def test_the_tool_judge_reads_the_ask_with_the_email_shown_as_material(monkeypatch):
    """Mac gate 18 on bb5351e0e: a German mail pasted under "Could you give me the gist of this?" was judged "read a
    file", and the turn searched the Desktop for its subject line. The judge is asked about the person's own words,
    with the paste shown as text already in the message."""
    from core import intent_arbiter, plugin_ask
    from core.request_trust import OWNER_LOCAL_KEY

    seen = []
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask",
                        lambda message, choices, **kw: seen.append((message, kw.get("material", ""))) or ())
    plugin_ask.forget()
    try:
        text = _EMAILS[3]
        plugin_ask.judge_turn(text, source_context={OWNER_LOCAL_KEY: True, "surface": "openclaw"}, session_id="p")
    finally:
        plugin_ask.forget()
    assert seen, "the tool judge was not asked"
    message, material = seen[0]
    assert message == "kas čia parašyta?"
    assert material.startswith("Nuo: Biblioteka") and "penktadienio" in material
    prompt = intent_arbiter._plugin_ask_prompt(message, "", ["Read a file"], material)
    assert "Pasted text:" in prompt and prompt.rstrip().endswith('{"answer": [0]} for none of them')
    assert intent_arbiter._plugin_ask_prompt(message, "", ["Read a file"]).count("Pasted text:") == 0


def test_the_ask_is_found_in_the_persons_own_lines_when_the_lanes_text_is_folded():
    """Mac gate19 on c77cf9f90: the lanes read the turn with its line breaks folded into spaces, so the pasted mail was
    not found and its address made the turn integration work, answered as a plan with step lines glued on."""
    from core.agent_runtime.turn_planner import pasted_material_head_of_turn
    from core.task_router import classify

    raw = ("Streść mi to krótko.\n\nOd: Przychodnia Zdrowie <rejestracja@example.pl>\nTemat: Wizyta\n\n"
           "Przypominamy o wizycie u okulisty w czwartek o 10:30. Prosimy zabrać skierowanie.")
    folded = " ".join(raw.split())
    head = pasted_material_head_of_turn(folded, raw)
    assert head == "Streść mi to krótko."
    assert classify(head)["task_class"] != "integration_orchestration"
    # A turn the intake narrowed to other words keeps its own reading.
    assert pasted_material_head_of_turn("Something else entirely.", raw) == ""


def test_an_address_alone_is_not_the_word_email():
    from core.task_router import classify

    assert classify("what does mira@example.com stand for")["task_class"] != "integration_orchestration"
    assert classify("send an email to my landlord")["task_class"] == "integration_orchestration"


def test_a_pasted_mail_gives_no_order_of_its_own(monkeypatch):
    """Mac gate20 on 1d605955a: a French ask to sum up a pasted club mail ran a calendar availability check, read from
    the mail's own words (no tool was picked), and failed."""
    from core import plugin_ask
    from core.agent_runtime.turn_dispatch import _order_left_by_a_pasted_message
    from core.operator.parser import parse_operator_action_intent

    mail = ("Riassumi questa mail per favore.\n\nFrom: Town Library <desk@example.org>\nSubject: New hours\n\n"
            "The library opens on Monday at 9. Readers get a free bookmark and the new desks can be tried for the first time.")
    read = parse_operator_action_intent(" ".join(mail.split()))
    assert read is not None   # the mail's own words read as an order: the case this guards
    plugin_ask.forget()
    assert _order_left_by_a_pasted_message(read, mail, parse_operator_action_intent) is None
    # The same order typed without a pasted mail is the person's own.
    plain = "Am I free on Saturday at 10?"
    assert _order_left_by_a_pasted_message("kept", plain, parse_operator_action_intent) == "kept"
    # A tool the judge picked for the person's words keeps the mail as the order's material.
    monkeypatch.setattr(plugin_ask, "judged_builtins", lambda text: ("calendar",))
    assert _order_left_by_a_pasted_message(read, mail, parse_operator_action_intent) is read


@pytest.mark.parametrize("ask", [
    "Mohl bys mi shrnout, co v tom píšou?",
    "Kas šiame laiške parašyta? Trumpai.",
    "Resume-me isto em duas frases, por favor.",
])
def test_a_pasted_mail_does_not_pick_the_reply_language(ask):
    """Mac gate20 on 1d605955a: an ask in Polish over a pasted English mail was answered in English. The mail's
    words decided the language; only the person's own lines may."""
    from core.response_language_policy import response_language_policy_for_text

    mail = ("\n\nFrom: Garden Centre <shop@example.com>\nSubject: Your order is ready\n\n"
            "Your order of two lemon trees and a bag of soil is ready for pickup. We are open every day from 8 "
            "until 6 and the parking is at the back of the building.")
    assert response_language_policy_for_text(ask + mail).expected_language != "en"
    # The same mail under an English ask is still answered in English.
    assert response_language_policy_for_text("What is this about, in short?" + mail).expected_language == "en"


@pytest.mark.parametrize("ask", ["Puoi riassumere questo messaggio?", "Kun je dit kort samenvatten?"])
def test_a_plain_letter_ask_over_a_pasted_mail_names_whose_language_counts(ask):
    """iMac gate21 on 006e4a888: an Italian and a Dutch ask with plain a-z letters over a pasted English mail went to the
    model with no language line, and the reply came back in the mail's English."""
    from core.response_language_policy import response_language_policy_for_text, response_language_provider_instruction

    text = (ask + "\n\nFrom: Hill Cycles <shop@example.com>\nSubject: Repair done\n\n"
            "Your bike is ready. The new chain and brake pads came to 61 euros; pick it up any weekday before 5.")
    line = response_language_provider_instruction(
        response_language_policy_for_text(text).to_dict(), earlier_user_texts=[], latest_text=text)
    assert "own words in front of the pasted text" in line
    # The same ask with nothing pasted adds no line in an English chat: the prompt stays byte for byte.
    assert response_language_provider_instruction(
        response_language_policy_for_text(ask).to_dict(), earlier_user_texts=[], latest_text=ask) == ""
