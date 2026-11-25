"""A reply that passes on what the person's own email, letter or document says was done is no claim of the assistant's.

Installed app 6f5532699 (qwen3:8b): asked to sum up a shop's order email, pasted and in a second chat attached, the
model wrote a correct two-line summary ("Order #... is confirmed. Pickup is scheduled for Saturday ..."). The done
question read the reply alone as the assistant's own finished action, and the person was told "no step ran on this
turn to do it". The reply alone does not say who did it; what the person gave does, so a done reading is asked again
with it beside the reply.

The judge here is a stand-in: what it answers is the test's choice, not a reading of any wording.
All names and values are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter
from core.agent_runtime.action_honesty_validator import emit_turn_honesty_receipt, enforce_final_action_honesty
from core.honesty_receipt import VERDICT_BLOCKED

LETTER = ("Kurz zusammenfassen bitte:\n\nVon: Gartencenter Linde\nBetreff: Lieferung 3307\n\nGuten Tag, Ihre Bestellung "
          "3307 (zwei Hochbeete) ist bezahlt und für Donnerstag zwischen 8 und 12 Uhr zur Lieferung eingeplant.")
SUMMARY = "Bestellung 3307 (zwei Hochbeete) ist bezahlt; die Lieferung ist für Donnerstag 8-12 Uhr eingeplant."


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_HONESTY_RECEIPTS", "1")
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    intent_arbiter.bind_turn_judge({}, None)
    yield
    intent_arbiter.bind_turn_judge({}, None)


def _judge(relay_answer: str, asked: list[str]):
    def judge(prompt: str) -> str:
        asked.append(prompt)
        if prompt.endswith('{"answer": "stated"} or {"answer": "new"}'):
            return '{"answer": "%s"}' % relay_answer
        return '{"answer": "done"}'

    intent_arbiter.bind_turn_judge({}, judge)


def _turn(response: str, user_input: str, context: dict) -> dict:
    guarded = enforce_final_action_honesty(
        {"response": response}, user_input=user_input, session_id="s-relay", source_context=context
    )
    return emit_turn_honesty_receipt(guarded, user_input=user_input, session_id="s-relay", source_context=context)


def test_a_summary_of_the_persons_pasted_letter_stands():
    asked: list[str] = []
    _judge("stated", asked)
    out = _turn(SUMMARY, LETTER, {})
    assert out["response"] == SUMMARY
    assert out["honesty_receipt"]["verdict"] != VERDICT_BLOCKED
    assert len(asked) == 2 and "Gartencenter Linde" in asked[1]


def test_a_claim_the_judge_says_the_assistant_made_is_still_corrected():
    asked: list[str] = []
    _judge("new", asked)
    out = _turn("Ich habe die Lieferung für Donnerstag in deinen Kalender eingetragen.", "trag die Lieferung ein", {})
    assert out["response"] != "Ich habe die Lieferung für Donnerstag in deinen Kalender eingetragen."
    assert out["honesty_receipt"]["verdict"] == VERDICT_BLOCKED


def test_an_attached_letter_is_read_beside_the_reply_and_fetched_pages_are_not():
    asked: list[str] = []
    _judge("stated", asked)
    context = {"external_evidence": [
        {"origin": "chat_attachment", "kind": "text", "text": "Ihre Bestellung 3307 ist bezahlt und eingeplant."},
        {"origin": "web_fetch", "kind": "text", "text": "Seite eines Händlers"},
    ]}
    out = _turn(SUMMARY, "fass den Anhang kurz zusammen", context)
    assert out["response"] == SUMMARY
    assert "Ihre Bestellung 3307 ist bezahlt" in asked[1] and "Seite eines Händlers" not in asked[1]


def test_no_answer_to_who_did_it_leaves_the_done_reading():
    asked: list[str] = []
    _judge("maybe", asked)
    out = _turn(SUMMARY, LETTER, {})
    assert out["response"] != SUMMARY
    assert out["honesty_receipt"]["verdict"] == VERDICT_BLOCKED


@pytest.mark.parametrize(("answer", "read"), [
    ('{"answer": "remind"}', "yes"), ('{"answer": "remember"}', "no"), ('{"answer": "other"}', "no"),
    ('{"answer": "yes"}', None), ("no", None),
])
def test_the_reminder_question_names_a_fact_to_remember_as_an_answer_of_its_own(answer, read):
    """Probe on 3dd1243b9 (qwen3:8b): asked "does it ask to be reminded later?", a fact told for later was a yes 2 of 2
    each, and the installed app made a reminder of a fact the person asked it to keep in mind."""
    asked: list[str] = []
    intent_arbiter.bind_turn_judge({}, lambda prompt: asked.append(prompt) or answer)
    assert intent_arbiter.arbitrate_reminder_ask("mein Kurs ist ab jetzt donnerstags", source_context={}) == read
    assert all(f'"answer": "{kind}"' in asked[0] for kind in ("remind", "remember", "other"))


def test_a_turn_read_as_an_order_for_a_tool_is_never_cleared_by_the_persons_words(monkeypatch):
    """Probe on 3dd1243b9 (qwen3:8b): "text Jonas that dinner is at 8" -> "Sent Jonas a message: dinner is at 8." was
    read as already written in the person's text. On a turn whose ask is an order for a tool, nothing the person wrote
    stands for the step that did not run."""
    from core.agent_runtime import action_honesty_validator as validator

    monkeypatch.setattr(validator, "_ask_judged_for_a_tool", lambda *_a: True)
    asked: list[str] = []
    _judge("stated", asked)
    out = _turn("Nachricht an Jonas gesendet: Abendessen um 8.", "schreib Jonas, dass das Abendessen um 8 ist", {})
    assert out["response"] != "Nachricht an Jonas gesendet: Abendessen um 8."
    assert out["honesty_receipt"]["verdict"] == VERDICT_BLOCKED
    assert not any(p.endswith('{"answer": "stated"} or {"answer": "new"}') for p in asked)
