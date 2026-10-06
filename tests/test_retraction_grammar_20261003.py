"""A reminder or a description is not a retraction (core/temporal_selection.py).

The closed retraction class matched "forget the/that/it", "ignore ...", "cancel that" anywhere in a
record. In an imported conversation a REMINDER ("Don't forget the spare key under the flowerpot")
or a DESCRIPTION ("a walk helps me forget the commute") then withdrew every earlier record of the
same speaker in its slot - the speaker's own name joins all their turns into one slot - so the
answer turn never reached the capsule (measured, c1-recall zero-spend replay of 200 paid dev
questions: 14 gold evidence turns withdrawn this way, several at lexical rank 1-2).

Law: negation reverses the act (a negated marker withdraws nothing), and the speech verbs (forget /
ignore / disregard) withdraw speech only as a directive to the listener: clause-initial (a speaker
label's colon opens the clause), optionally after discourse words or politeness, or inside a
request frame that hands the verb to the listener ("could you ...", "I'd like you to ...", "you
should ...", "let's ..."). Genuine retractions ("Scratch that ...", "Forget the flowerpot, the key is
in the shed", "Kindly disregard that code") keep withdrawing. "cancel that / cancel it" reports a
withdrawn plan however it is phrased ("we had to cancel it"), so it obeys the negation rule alone.
The merge's revision marker (core/context_retrieval.py::_span_is_revision) follows the same law.

All sentences are invented for this contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from core.temporal_selection import (
    AsOfIntent,
    apply_temporal_selection,
    carries_undo_marker,
    retraction_marker,
)
from tests.test_question_date_time_leg_20261002 import _hash_backend  # noqa: F401

UTC = timezone.utc
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


_LIVE_RETRACTIONS = [
    # original shape and clean paraphrases
    "Forget the flowerpot, the spare key is in the shed now.",
    "Scratch that about the flowerpot, the spare key is with Lena.",
    "Oh, ignore what I said about the flowerpot, the spare key moved.",
    "Please disregard the flowerpot bit, the key is in the shed.",
    "You can forget the flowerpot, the spare key sits in the shed.",
    "Let's cancel that plan, the key stays with me.",
    "On second thought, the key goes in the shed.",
    "Never mind the flowerpot, the key is in my coat.",
    # sloppy / typed variants
    "ok so forget that flowerpot thing, key is in the shed",
    "pls ignore that, key's in the shed",
    "wait, forget what i said about the pot",
    "nah cancel that the key is w me",
    "scratch that lol key in shed",
]

_NOT_RETRACTIONS = [
    # reminders: negation keeps the thing
    "Don't forget the spare key under the flowerpot.",
    "Do not forget the key we keep under the flowerpot.",
    "dont forget the spare key lol",
    "plus don't forget the umbrella later",
    "Never forget the spare key, okay?",
    "You can't ignore the spare key thing forever.",
    "I couldn't ignore that noise all night.",
    # descriptions: a subject or governing verb before the verb
    "A walk by the river helps me forget the key drama.",
    "I always forget the name of that cafe.",
    "they told me to ignore it but I didn't",
]


@pytest.mark.parametrize("text", _LIVE_RETRACTIONS)
def test_directive_retraction_is_still_a_retraction(text):
    assert retraction_marker(text) is not None, text
    assert carries_undo_marker(text), text


@pytest.mark.parametrize("text", _NOT_RETRACTIONS)
def test_reminder_or_description_is_not_a_retraction(text):
    assert retraction_marker(text) is None, text


def test_a_live_marker_after_a_reminder_in_the_same_record_still_counts():
    # adversarial near-miss: the reminder clause is inert, the directive after it is live
    text = "Don't forget the spare key: actually, scratch that, it's in the shed."
    match = retraction_marker(text)
    assert match is not None and match.group(0).lower().startswith("scratch"), match


@pytest.mark.parametrize("text, revises", [
    ("Don't forget the receipts in the glovebox.", False),
    ("A long walk helps me forget the commute.", False),
    ("don't scratch that record please", False),
    ("Forget the earlier time, it's 4 pm now.", True),
    ("Scratch that, the ferry leaves at nine.", True),
    ("Correction: the ferry leaves at nine.", True),
])
def test_merge_revision_marker_follows_the_same_grammar(text, revises):
    assert cr._span_is_revision(text) is revises, text


def _row(key: str, body: str, at: str, speaker: str = "Halvard") -> dict:
    return {"key": key, "body": body, "role": "user", "statement_at": _ts(at),
            "speaker": speaker, "seq": None}


def _key_rows(phrase: str) -> list[dict]:
    return [
        _row("key", "Halvard: The spare key lives under the blue flowerpot by the back door.",
             "2026-02-11T10:00:00"),
        _row("tap", "Halvard: I fixed the leaking tap in the kitchen.", "2026-03-12T10:00:00"),
        _row("later", f"Halvard: Have fun at the market! {phrase}", "2026-04-13T10:00:00"),
    ]


@pytest.mark.parametrize("past_only", [True, False])
@pytest.mark.parametrize("phrase", _NOT_RETRACTIONS[:9])
def test_a_reminder_never_withdraws_the_speakers_earlier_records(phrase, past_only):
    verdicts = apply_temporal_selection(
        _key_rows(phrase), intent=AsOfIntent(), past_only=past_only,
        question="Where does Halvard keep the spare key?", now_utc=NOW)
    assert verdicts["key"].eligible, (phrase, verdicts["key"])
    assert verdicts["key"].reason not in {"withdrawn", "superseded-by-correction"}
    assert verdicts["tap"].eligible, (phrase, verdicts["tap"])


@pytest.mark.parametrize("past_only", [True, False])
@pytest.mark.parametrize("phrase", _LIVE_RETRACTIONS[:5] + _LIVE_RETRACTIONS[8:11])
def test_a_directive_retraction_still_withdraws_the_record_it_names(phrase, past_only):
    # negative control: the old lane (withdrawal) is kept for genuine retractions
    verdicts = apply_temporal_selection(
        _key_rows(phrase), intent=AsOfIntent(), past_only=past_only,
        question="Where does Halvard keep the spare key?", now_utc=NOW)
    assert not verdicts["key"].eligible, (phrase, verdicts["key"])




# ── request frames, cancellations and the speaker label ──────────────────────

_REQUEST_FORM_RETRACTIONS = [
    # clean: a request, permission or advice frame hands the verb to the listener
    "Kindly disregard the storage unit code from Monday.",
    "I'd like you to forget the storage unit code I gave you.",
    "You should forget that storage unit code, it was a typo.",
    "Could you ignore what I said about the storage unit? The code changed.",
    "I want you to ignore what I told you about the storage unit.",
    "You might want to ignore that, the unit code moved on.",
    "Please could you just forget the old unit code.",
    "I need you to disregard my note about the trailer hitch.",
    "Would you disregard that? The trailer hitch is fine after all.",
    # sloppy / typed
    "can u just forget the storage code thing? its wrong",
    "pls can you disregard that unit code",
    "u can ignore that unit code lol",
    "id like u to forget the unit code i sent",
    "you should ignore that, wrong unit code",
    "could you disregard my last msg about the hitch",
]

_REQUEST_FRAME_CONTROLS = [
    # a question about a habit, a rhetorical question, a negated request, negated advice,
    # a conditional, "ever", reported speech and the speaker's own intent describe or remind
    "Do you ever forget the storage unit code?",
    "How could you forget the storage unit code?",
    "Could you not forget the unit key this time?",
    "You shouldn't ignore that beeping from the hitch.",
    "If you can ignore the noise, the flat is lovely.",
    "Would you ever ignore it?",
    "I'll just ignore that email from the landlord.",
    "Why would you disregard the forecast?",
]


@pytest.mark.parametrize("text", _REQUEST_FORM_RETRACTIONS)
def test_a_request_form_retraction_is_still_a_retraction(text):
    assert retraction_marker(text) is not None, text
    assert carries_undo_marker(text), text


@pytest.mark.parametrize("text", _REQUEST_FRAME_CONTROLS)
def test_a_frame_that_does_not_address_a_request_is_no_retraction(text):
    assert retraction_marker(text) is None, text


@pytest.mark.parametrize("text, live", [
    # a plan reported cancelled is withdrawn however it is phrased
    ("We had to cancel it, the kiln broke.", True),
    ("I'll cancel it, the pottery night clashes with work.", True),
    ("Cancel that workshop, the kiln is cracked.", True),
    ("ok cancel that pottery thing, kiln is cracked", True),
    # negation keeps the plan
    ("Don't cancel it, the kiln is fixed.", False),
    ("We never cancel it, the class runs rain or shine.", False),
    ("dont cancel that, kiln works again", False),
])
def test_a_reported_cancellation_obeys_the_negation_rule_alone(text, live):
    assert (retraction_marker(text) is not None) is live, text


_LABELLED_DIRECTIVES = [
    # the speaker label's colon opens the directive's clause
    "Halvard: Forget the flowerpot, the spare key is in the shed now.",
    "Halvard: Ignore what I said about the flowerpot, the key moved.",
    "Halvard: Disregard that, the spare key is with me.",
    "Halvard: Could you ignore that? The spare key moved to the shed.",
    "Halvard: pls forget the flowerpot thing, key's in the shed",
]


@pytest.mark.parametrize("text", _LABELLED_DIRECTIVES)
def test_a_speaker_label_opens_the_directive_clause(text):
    assert retraction_marker(text) is not None, text


@pytest.mark.parametrize("text", [
    "Halvard: Don't forget the spare key under the flowerpot.",
    "Halvard: Gardening helps me forget the key drama.",
    "Halvard: My sister told me to ignore it.",
])
def test_a_speaker_label_never_makes_a_description_a_directive(text):
    assert retraction_marker(text) is None, text


@pytest.mark.parametrize("past_only", [True, False])
@pytest.mark.parametrize("text", _LABELLED_DIRECTIVES)
def test_a_labelled_directive_withdraws_the_record_it_names(text, past_only):
    rows = [*_key_rows("x")[:2], _row("later", text, "2026-04-13T10:00:00")]
    verdicts = apply_temporal_selection(
        rows, intent=AsOfIntent(), past_only=past_only,
        question="Where does Halvard keep the spare key?", now_utc=NOW)
    assert not verdicts["key"].eligible, (text, verdicts["key"])


# ── end to end: an ordinary chat capsule ─────────────────────────────────────

_UNIT = ("My storage unit code is 5906.", "What is my storage unit code?", "5906")
_POTTERY = ("The pottery workshop is on Saturday at 10 am.", "When is the pottery workshop?",
            "10 am")


def _ordinary_chat_capsule(tmp_path, first: str, later: str, question: str) -> str:
    from tests.test_question_date_time_leg_20261002 import _capsule, _ingest, _profile

    profile = _profile(tmp_path)
    _ingest(profile, "plainchat", [
        (_ts("2026-02-03T09:00:00"), first),
        (_ts("2026-02-20T09:00:00"), "The shed roof needs new tar paper."),
        (_ts("2026-03-05T09:00:00"), later),
    ])
    capsule, _telemetry = _capsule(profile, "plainchat", question)
    return capsule


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("fact, later, withdrawn", [
    # request-form retractions withdraw the value they name
    (_UNIT, "Kindly disregard the storage unit code from Monday.", True),
    (_UNIT, "I'd like you to forget the storage unit code I gave you.", True),
    (_UNIT, "You should forget that storage unit code, it was a typo.", True),
    (_UNIT, "Could you ignore what I said about the storage unit? The code changed.", True),
    (_UNIT, "can u just forget the storage code thing? its wrong", True),
    (_UNIT, "Please could you just forget the old storage unit code.", True),
    (_UNIT, "u can ignore that storage unit code lol", True),
    (_UNIT, "Forget the storage unit code I gave you, it is wrong.", True),
    # a plan reported cancelled is withdrawn
    (_POTTERY, "We had to cancel it, the pottery workshop kiln broke.", True),
    (_POTTERY, "I'll cancel it, the pottery workshop clashes with work.", True),
    # negative controls: a reminder, a question about a habit and a negated cancellation keep it
    (_UNIT, "Don't forget the storage unit code.", False),
    (_UNIT, "Do you ever forget the storage unit code?", False),
    (_POTTERY, "Don't cancel it, the pottery workshop kiln is fixed.", False),
])
def test_an_ordinary_chat_capsule_follows_the_retraction_grammar(tmp_path, fact, later,
                                                                   withdrawn):
    first, question, value = fact
    capsule = _ordinary_chat_capsule(tmp_path, first, later, question)
    assert (value not in capsule) is withdrawn, (later, capsule)
