"""Taking back one's own words withdraws them (core/temporal_selection.py).

Probe of the v0.8 line at 247124ed, 2026-11-12: the closed retraction class had no form for a
speaker who takes back their own words by naming the saying. "I take back what I said about the
dentist appointment" and "Forget I said that about my cat's name, it was wrong" left the earlier
record eligible as current (selection verdicts eligible-coexists / slot-winner), and the chat
capsule carried the withdrawn value next to the withdrawal, so the model was free to answer it.

Law, added to the closed class with its own speaker rule:

* "forget I said / forget I (ever) mentioned / forget I told you" and "ignore / forget my last
  message" are speech verbs: the directive law of the class applies unchanged (clause-initial,
  after discourse words, or inside a request frame; never negated).
* "strike that" is "scratch that": a directive whose "that" points back at what was said.
* "take that back", "take back what I said", "take back my last message" and "I misspoke" un-say
  the SPEAKER's own words, so they are never handed to the listener: live for a first-person
  speaker in the present ("I take that back", "I'm taking that back"), a first-person intention
  or permission frame ("let me take back what I said", "I want to take that back"), and, only for
  the forms that name the speaker's own speech, a bare imperative ("take back what I said").
  "Take that back!" and "you should take that back" ask the listener to un-say THEIR words;
  "he told me to take that back" reports; "if it breaks, I take it back" describes a routine; and
  "take it back to the store" / "taking that back tomorrow" is the errand of returning a thing.
  "I take it back" is live only in the present simple: "I'll take it back" and "I'm taking it
  back" are errands.
* The words of these markers name the act, never the subject, so they never tie the withdrawal
  to an unrelated record ("back", "message", "said").

All sentences are invented for this contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.temporal_selection import (
    AsOfIntent,
    _retraction_act_words,
    apply_temporal_selection,
    carries_undo_marker,
    retraction_marker,
)
from tests.test_question_date_time_leg_20261002 import _hash_backend  # noqa: F401 (fixture)
from tests.test_retraction_grammar_20261003 import _ordinary_chat_capsule

UTC = timezone.utc
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


_TAKE_BACKS = [
    # the probe's wordings
    "I take back what I said about the dentist appointment.",
    "Forget I said that about my cat's name, it was wrong.",
    # clean paraphrases
    "Forget I ever mentioned the dentist appointment.",
    "Ignore my last message about the dentist appointment.",
    "I take that back, the dentist appointment is not on Friday.",
    "Let me take back what I said about the dentist appointment.",
    "I said Friday but I take that back.",
    "Take back what I said about the dentist appointment.",
    "Strike that, I have no dentist appointment.",
    "Sorry, I misspoke about the dentist appointment.",
    "I take back my last message about the dentist.",
    "I take back that last bit about the dentist appointment.",
    "I want to take back what I told you about the dentist appointment.",
    "Actually I totally take it back about the dentist appointment.",
    "I'm taking that back, the dentist appointment is off.",
    # sloppy / typed
    "ok i take that back lol",
    "nvm ignore my last msg",
    "forget i said anything about the dentist",
    "lemme take back what i said abt the dentist",
    "i misspoke, no dentist appt",
]

_NOT_TAKE_BACKS = [
    # a demand that the LISTENER un-say their words
    "Take that back!",
    "You should take that back.",
    "You'd better take back what you said about my cat.",
    # reported speech, a routine, advice and a hedge
    "He told me to take that back.",
    "If it breaks, I take it back.",
    "I would take that back if I were you.",
    "I think I misspoke.",
    # negation keeps the words
    "I won't take back what I said.",
    "I'm not taking that back.",
    "I don't take it back, the dentist is on Friday.",
    "Don't forget I said that.",
    # the errand of returning a thing
    "I'll take it back to the store tomorrow.",
    "I'm taking it back.",
    "I'm taking that back to the shop.",
    "I'll take that back tomorrow.",
    "I can take it back if you want.",
    "I need to take back what I bought.",
    "I take back that sweater every year.",
    # an ordinary object or a description
    "Lightning might strike that tree.",
    "I forget I said that sometimes.",
    # taking back a question un-says no statement
    "Forget I asked, I found the receipt.",
]


@pytest.mark.parametrize("text", _TAKE_BACKS)
def test_taking_back_ones_own_words_is_a_retraction(text):
    assert retraction_marker(text) is not None, text
    assert carries_undo_marker(text), text


@pytest.mark.parametrize("text", _NOT_TAKE_BACKS)
def test_a_demand_a_report_or_an_errand_is_no_retraction(text):
    assert retraction_marker(text) is None, text


@pytest.mark.parametrize("text, act", [
    ("I take back what I said about the dentist appointment.", {"take", "back"}),
    ("Ignore my last message about the gym.", {"ignore", "last", "message"}),
    ("Forget I ever mentioned the dentist.", {"forget", "ever", "mentioned"}),
    ("Strike that, the dentist is on Thursday.", {"strike"}),
])
def test_the_words_of_the_act_are_never_subject_identity(text, act):
    words = _retraction_act_words(text)
    assert act <= words, (text, words)
    assert not words & {"dentist", "appointment", "gym", "thursday"}, (text, words)


def _row(key: str, body: str, at: str, role: str = "user") -> dict:
    return {"key": key, "body": body, "role": role, "statement_at": _ts(at), "speaker": "",
            "seq": None}


def _dentist_rows(later: str, *, later_role: str = "user") -> list[dict]:
    return [
        _row("fact", "My dentist appointment is on Friday at 3pm.", "2026-10-01T09:00:00"),
        _row("roof", "The shed roof needs new tar paper.", "2026-10-02T09:00:00"),
        _row("later", later, "2026-10-03T09:00:00", role=later_role),
    ]


_DENTIST_TAKE_BACKS = [text for text in _TAKE_BACKS if "dentist" in text]


@pytest.mark.parametrize("past_only", [True, False])
@pytest.mark.parametrize("later", _DENTIST_TAKE_BACKS)
def test_taking_back_withdraws_the_record_it_names_and_nothing_else(later, past_only):
    verdicts = apply_temporal_selection(
        _dentist_rows(later), intent=AsOfIntent(), past_only=past_only,
        question="When is my dentist appointment?", now_utc=NOW)
    assert not verdicts["fact"].eligible, (later, verdicts["fact"])
    assert verdicts["roof"].eligible, (later, verdicts["roof"])


@pytest.mark.parametrize("later", [
    "You should take back what you said about the dentist appointment.",
    "He told me to take that back about the dentist appointment.",
    "I won't take back what I said about the dentist appointment.",
    "I'll take the dentist appointment card back to the desk tomorrow.",
])
def test_a_demand_a_report_or_an_errand_keeps_the_record(later):
    verdicts = apply_temporal_selection(
        _dentist_rows(later), intent=AsOfIntent(), past_only=False,
        question="When is my dentist appointment?", now_utc=NOW)
    assert verdicts["fact"].eligible, (later, verdicts["fact"])


def test_the_assistant_taking_back_its_words_never_withdraws_the_users_record():
    later = "I take back what I said about the dentist appointment, I misread your message."
    verdicts = apply_temporal_selection(
        _dentist_rows(later, later_role="assistant"), intent=AsOfIntent(), past_only=False,
        question="When is my dentist appointment?", now_utc=NOW)
    assert verdicts["fact"].eligible, verdicts["fact"]


_DENTIST = ("My dentist appointment is on Friday at 3pm.", "When is my dentist appointment?", "3pm")
_CAT = ("My cat's name is Biscuit.", "What is my cat's name?", "Biscuit")


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("fact, later, withdrawn", [
    (_DENTIST, "I take back what I said about the dentist appointment.", True),
    (_CAT, "Forget I said that about my cat's name, it was wrong.", True),
    (_DENTIST, "Forget I ever mentioned the dentist appointment.", True),
    (_DENTIST, "Let me take back what I said about the dentist appointment.", True),
    (_DENTIST, "Sorry, I misspoke about the dentist appointment.", True),
    (_CAT, "Strike that cat's name, I never had a cat.", True),
    # controls: the listener is asked, a report, a negation
    (_DENTIST, "You should take back what you said about the dentist appointment.", False),
    (_DENTIST, "He told me to take that back about the dentist appointment.", False),
    (_CAT, "I won't take back what I said about my cat's name.", False),
])
def test_an_ordinary_chat_capsule_follows_the_take_back_law(tmp_path, fact, later, withdrawn):
    first, question, value = fact
    capsule = _ordinary_chat_capsule(tmp_path, first, later, question)
    assert (value not in capsule) is withdrawn, (later, capsule)


# ── the forget command: its stored target is a description, not a retraction ────────────────

@pytest.mark.parametrize("command", [
    "Forget the exact identifier I mentioned in this chat, but keep the budget note.",
    "Forget the exact identifier I said in this chat, but keep the budget note.",
    "Forget the exact identifier I told you in this chat, but keep the budget note.",
    "Forget the exact identifier I asked you to remember in this chat, but keep the budget note.",
    "Forget I said anything about the identifier in this chat, keep the budget note.",
])
def test_a_forget_commands_stored_target_never_withdraws_a_kept_memory(tmp_path, monkeypatch, command):
    """The forget authority withdraws by subject from the targets a forget command stored, and the
    command parser stores relative clauses cut from the request ("i mentioned in this chat"). Read
    as a retraction of the speaker's own saying, such a fragment withdrew every memory of the chat
    through "chat", including the budget note the person asked to keep (measured on this branch
    before the fix; plain 247124ed never read "forget I ..." as a retraction)."""
    from core import runtime_paths
    from core.context_namespace import ensure_chat_namespace
    from core.persistent_memory import maybe_handle_memory_command, summarize_memory

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    chat = "take-back-forget-command"
    ensure_chat_namespace(chat)
    assert maybe_handle_memory_command(
        "Remember this exact identifier for this chat: INSTALL-MEM-E91D-7319.", session_id=chat)[0]
    assert maybe_handle_memory_command("Keep this exact budget note in this chat: 0.091 SOL.", session_id=chat)[0]

    handled, reply = maybe_handle_memory_command(command, session_id=chat)

    assert handled, reply
    remembered = "\n".join(summarize_memory(chat_id=chat, limit=20))
    assert "0.091 SOL" in remembered, (command, reply, remembered)
    if reply.startswith("Nothing was removed"):
        # what the reply says is what happened: nothing was withdrawn behind it either
        assert "INSTALL-MEM-E91D-7319" in remembered, (command, reply, remembered)

