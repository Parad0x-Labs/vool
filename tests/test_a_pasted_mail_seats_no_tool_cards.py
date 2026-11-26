"""A pasted mail or message is material: the tool cards are read against the person's own words in front of it.

iMac gate25 on cbcb090a4 (qwen3:8b): a Dutch "what is this mail about" over a clinic's mail ("your cleaning is booked
for Tuesday") was offered the calendar and process tools by the card router's reading of the whole paste, and the
model proposed a calendar event instead of saying what the mail is about. Names and addresses are invented.
"""
from __future__ import annotations

from core import plugin_ask, tool_card_router
from core.tool_card_router import CardMatch

MAIL = ("Hvad handler denne besked om?\n\nFrom: Fjord Physio <desk@example.org>\nSubject: Session confirmed\n\n"
        "Your physiotherapy session is booked for Thursday at 08:45. Bring comfortable clothes.")
ORDER = ("Zet dit in mijn agenda alsjeblieft.\n\nFrom: Fjord Physio <desk@example.org>\nSubject: Session confirmed\n\n"
         "Your physiotherapy session is booked for Thursday at 08:45.")


def _cards_by_meaning(monkeypatch):
    read = []

    def match(text, **_k):
        read.append(text)
        if "booked" in text or "agenda" in text:
            return (CardMatch("operator.propose_calendar_event", 0.8, "book me a call", "operator", True),)
        return ()

    monkeypatch.setattr(tool_card_router, "match_cards", match)
    monkeypatch.setattr(tool_card_router, "card_routing_enabled", lambda: True)
    monkeypatch.setattr(tool_card_router, "_usable", lambda intent: True)
    plugin_ask.forget()
    return read


def test_the_mails_words_seat_no_card(monkeypatch):
    read = _cards_by_meaning(monkeypatch)
    assert tool_card_router.routed_intents(MAIL, owner_local=True) == ()
    assert read and all("booked" not in t for t in read), read


def test_folded_lines_keep_the_reading_the_turn_judge_made(monkeypatch):
    read = _cards_by_meaning(monkeypatch)
    plugin_ask._record_paste(MAIL, plugin_ask.own_words_over_a_paste(MAIL) or "")
    assert tool_card_router.routed_intents(" ".join(MAIL.split()), owner_local=True) == ()
    assert all("booked" not in t for t in read), read


def test_the_persons_own_order_over_a_mail_still_seats_its_card(monkeypatch):
    _cards_by_meaning(monkeypatch)
    got = tool_card_router.routed_intents(ORDER, owner_local=True)
    assert [m.intent for m in got] == ["operator.propose_calendar_event"], got


def test_a_message_with_no_paste_is_read_whole(monkeypatch):
    read = _cards_by_meaning(monkeypatch)
    tool_card_router.routed_intents("my dentist visit is booked, can you add it", owner_local=True)
    assert read == ["my dentist visit is booked, can you add it"], read


ASK_ONLY = ("Qu'est-ce que dit ce courriel ?\n\nFrom: Harbour Swim Club <desk@example.org>\nSubject: Renewal\n\n"
            "Your season pass renews on 3 January; the fee is 52 EUR.")


def test_the_persons_words_over_a_paste_the_judge_picked_nothing_for_seat_no_card(monkeypatch):
    """iMac gate29 on 9ea86d1a4: "De quoi parle ce mail ?" over a gym's mail was judged no tool, and the cards seated
    Contacts by "mail"; the model looked the sender up as a contact instead of saying what the mail is about."""
    def match(text, **_k):
        return (CardMatch("contacts.resolve", 0.7, "what is the email of pierre", "contacts", True),)

    monkeypatch.setattr(tool_card_router, "match_cards", match)
    monkeypatch.setattr(tool_card_router, "card_routing_enabled", lambda: True)
    monkeypatch.setattr(tool_card_router, "_usable", lambda intent: True)
    plugin_ask.forget()
    assert [m.intent for m in tool_card_router.routed_intents(ASK_ONLY, owner_local=True)] == ["contacts.resolve"]
    plugin_ask._record_builtins(ASK_ONLY, ())
    assert tool_card_router.routed_intents(ASK_ONLY, owner_local=True) == ()
    plugin_ask._record_builtins(ASK_ONLY, ("contacts",))
    assert [m.intent for m in tool_card_router.routed_intents(ASK_ONLY, owner_local=True)] == ["contacts.resolve"]
    plugin_ask.forget()
