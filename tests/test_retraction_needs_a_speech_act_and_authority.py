"""A retraction is a speaker taking back their OWN earlier claim
(core/temporal_selection.py).

External review of the frozen code, 2026-10-06:

1. A figurative "scratch that" read as a retraction. The closed class matched
   "scratch that" anywhere in a record and only negation switched it off, so
   "a cold swim at the lido would scratch that itch" withdrew the speaker's
   earlier claims of its slot. Law: "scratch that" is a correction speech act
   only as a directive (clause-initial after discourse words, or inside a
   request frame) whose "that" stands alone or determines a word for something
   said, planned or chosen ("scratch that last bit", "scratch that plan").
   Otherwise the verb takes an ordinary object. A cancel that asks the
   listener to perform a service for the speaker ("cancel that order for me")
   un-says nothing either.

2. The withdrawal chain had no authority check. A slot pairs user statements
   with the assistant lines about them, and any later retraction in the slot
   withdrew any earlier record: an assistant "never mind" withdrew the user's
   own claim, and a user's "scratch that" withdrew what the assistant stated.
   Law: only the party that said a record withdraws or restores it, and two
   attributed speakers never take back each other's statements.

3. Lexical overlap as subject identity. Law: a live retraction's own act words
   ("never mind", "on second thought", "cancel that", "take ... down") are
   never subject identity, so they no longer join it to, or tie it to, a
   record that shares nothing else. The speaker-name and value ties remain;
   they are pinned as strict xfails at the end of this file (the reason is on
   each marker).

All sentences, names and domains are invented for this contract.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from core.temporal_selection import (
    AsOfIntent,
    TemporalCandidate,
    _withdrawn_in_chain,
    apply_temporal_selection,
    carries_undo_marker,
    retraction_marker,
)
from tests.test_question_date_time_leg_20261002 import _hash_backend  # noqa: F401

UTC = timezone.utc
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _cand(key: str, body: str, at: str, *, role: str = "user",
          speaker: str = "") -> TemporalCandidate:
    return TemporalCandidate(key=key, body=body, role=role, statement_at=_ts(at),
                             speaker=speaker)


def _select(cands, question: str, *, past_only: bool = False):
    return apply_temporal_selection(cands, intent=AsOfIntent(), past_only=past_only,
                                    question=question, now_utc=NOW)


# ── 1. a figurative "scratch that" is an ordinary verb phrase ─────────────────

# The review quoted inflected forms; the closed class needs the bare verb, so
# these never matched it. They stay assertions.
_REPORTED_WORDING = [
    "rock climbing scratches that same itch for me",
    "it scratched that itch",
]

_FIGURATIVE = [
    # clean paraphrases: a subject or a governing verb comes first
    "Swimming at the lido helps me scratch that itch for open water.",
    "A cold swim at the lido would scratch that itch too.",
    "Laps at the lido are how I scratch that itch.",
    "I joined a rowing crew mainly to scratch that itch.",
    # the pronoun names the itch, not a statement: only the clause law decides
    "I crave open water and only the lido can scratch that.",
    "A dawn swim is the one thing that will scratch that for me.",
    # clean paraphrases: a directive whose object is a thing, not a statement
    "Scratch that same itch with laps at the lido, it beats the gym.",
    "You should scratch that open-water itch at the lido more often.",
    "Let's scratch that travel itch this summer with a night train.",
    # sloppy / typed
    "lido swim def scratch that itch lol",
    "need the lido to scratch that itch tbh",
    "SCRATCH THAT ITCH AT THE LIDO!!",
    "gotta scratch that swim itch at lido",
    "pottery wheel on sundays scratch that itch 4 me",
    "laps at the lido always scratch that for me",
    "Scratch that itch 4 me, my back is killing me.",
]

_LIVE_SCRATCH = [
    # the review's must-keep shapes and clean paraphrases
    "Scratch that, I'm moving in June.",
    "Actually, scratch that — make it Tuesday.",
    "Scratch that last bit about the lido laps.",
    "No wait, scratch that plan, we're staying home.",
    "You can scratch that, the lido closed for repairs.",
    "Scratch that about the rowing crew, I never signed up.",
    "Hmm, scratch that entirely.",
    # sloppy / typed
    "scratch that im moving in june",
    "ok wait scratch that its tuesday not monday",
    "nvm scratch that lol",
    "SCRATCH THAT the party is off",
    "scratch that 9:40 pickup, its 10:15",
    "scratch that meeting got moved",
    "i mean scratch that, its tuesday",
    "omg scratch that the venue changed",
    "my bad scratch that last part",
]


@pytest.mark.parametrize("text", _REPORTED_WORDING + _FIGURATIVE)
def test_a_figurative_scratch_that_is_no_retraction(text):
    assert retraction_marker(text) is None, text
    assert not carries_undo_marker(text), text


@pytest.mark.parametrize("text", _LIVE_SCRATCH)
def test_a_scratch_that_correction_is_still_a_retraction(text):
    match = retraction_marker(text)
    assert match is not None and match.group(0).lower().startswith("scratch"), text
    assert carries_undo_marker(text), text


@pytest.mark.parametrize("text, live", [
    # negative controls: the other arms of the class keep their own grammar
    ("never mind what I said about the gym", True),
    ("On second thought, the ferry is too slow.", True),
    ("Kindly disregard my note about the kiln.", True),
    ("don't scratch that record please", False),
    ("Never forget the spare oars.", False),
])
def test_the_other_retraction_arms_are_unchanged(text, live):
    assert (retraction_marker(text) is not None) is live, text


def test_a_live_scratch_after_a_figurative_one_in_the_same_record_counts():
    # adversarial near-miss: the idiom is inert, the correction after it is live
    text = "Laps at the lido used to scratch that itch. Actually, scratch that, I quit in May."
    match = retraction_marker(text)
    assert match is not None and match.start() == text.index("scratch that,"), match


@pytest.mark.parametrize("text, revises", [
    ("A cold swim at the lido would scratch that itch too.", False),
    ("Scratch that same itch with laps at the lido.", False),
    ("Actually, scratch that — make it Tuesday.", True),
    ("Scratch that last bit, the lido is shut.", True),
])
def test_the_merge_revision_marker_follows_the_same_grammar(text, revises):
    assert cr._span_is_revision(text) is revises, text


# ── a cancel that asks for a service is no retraction ────────────────────────

_CANCEL_SERVICE_REQUESTS = [
    "Cancel that order for me.",
    "Could you cancel it for me, the seller never replied.",
    "Please cancel that dinner booking for us.",
    "I need you to cancel that subscription for me.",
    "can u cancel that for me pls",
    "cancel it for me asap",
]

_CANCEL_STILL_WITHDRAWS = [
    # a plan reported cancelled, and a cancel with no benefactive
    "We had to cancel it, the kiln broke.",
    "Cancel that workshop, the kiln is cracked.",
    "I'll cancel it for us, the forecast is grim.",
    "ok cancel that pottery thing, kiln is cracked",
    "Let's cancel that plan, the lido is shut.",
]


@pytest.mark.parametrize("text", _CANCEL_SERVICE_REQUESTS)
def test_a_cancel_requested_as_a_service_is_no_retraction(text):
    assert retraction_marker(text) is None, text


@pytest.mark.parametrize("text", _CANCEL_STILL_WITHDRAWS)
def test_a_reported_or_plain_cancel_still_withdraws(text):
    assert retraction_marker(text) is not None, text


@pytest.mark.parametrize("text", ["Don't cancel it for me.", "dont cancel that order for me"])
def test_a_negated_cancel_stays_inert(text):
    assert retraction_marker(text) is None, text


def test_a_retraction_after_a_service_request_in_the_same_record_counts():
    text = "Cancel that order for me. No, scratch that, I'll keep it."
    match = retraction_marker(text)
    assert match is not None and match.group(0).lower() == "scratch that", match


# ── the selection contract: a figurative scratch withdraws nothing ───────────

_LIDO_CLAIM = "I swim laps at the Harbour Lido before work."
_LIDO_Q = "Where do I swim laps?"

# every member shares subject vocabulary with the claim, so a marker reading
# would join its slot and withdraw it
_FIGURATIVE_ABOUT_THE_LIDO = [
    "Swimming at the lido helps me scratch that itch for open water.",
    "A cold swim at the lido would scratch that itch too.",
    "Laps at the lido are how I scratch that itch.",
    "Scratch that same itch with laps at the lido, it beats the gym.",
    "You should scratch that open-water itch at the lido more often.",
    "lido swim def scratch that itch lol",
    "need the lido to scratch that itch tbh",
    "SCRATCH THAT ITCH AT THE LIDO!!",
    "gotta scratch that swim itch at lido",
    "laps before work scratch that itch",
    "I crave open water and only the lido can scratch that.",
    "laps at the lido always scratch that for me",
]

_RETRACTIONS_ABOUT_THE_LIDO = [
    "Scratch that, I swim at the river now.",
    "Actually, scratch that — the lido laps are off until spring.",
    "Scratch that last bit about the lido.",
    "You can scratch that, the lido closed for repairs.",
    "never mind what I said about the lido",
    "ok wait scratch that, lido is closed",
    "nvm scratch that lol lido laps r off",
    "SCRATCH THAT the lido swim is off",
    "scratch that lido plan",
]


@pytest.mark.parametrize("past_only", [False, True])
@pytest.mark.parametrize("later", _FIGURATIVE_ABOUT_THE_LIDO)
def test_a_figurative_scratch_never_withdraws_the_speakers_claim(later, past_only):
    verdicts = _select([
        _cand("claim", _LIDO_CLAIM, "2026-03-02T07:00:00"),
        _cand("later", later, "2026-04-02T07:00:00"),
    ], _LIDO_Q, past_only=past_only)
    assert verdicts["claim"].eligible, (later, verdicts["claim"])
    assert verdicts["claim"].superseded_by is None, (later, verdicts["claim"])


@pytest.mark.parametrize("past_only", [False, True])
@pytest.mark.parametrize("later", _RETRACTIONS_ABOUT_THE_LIDO)
def test_a_real_retraction_still_withdraws_the_claim_it_names(later, past_only):
    # negative control: the old lane (withdrawal) is kept for real corrections
    verdicts = _select([
        _cand("claim", _LIDO_CLAIM, "2026-03-02T07:00:00"),
        _cand("later", later, "2026-04-02T07:00:00"),
    ], _LIDO_Q, past_only=past_only)
    assert not verdicts["claim"].eligible, (later, verdicts["claim"])
    assert verdicts["claim"].superseded_by == "later", (later, verdicts["claim"])


@pytest.mark.parametrize("past_only", [False, True])
def test_the_figurative_line_in_a_record_never_withdraws_but_its_correction_does(past_only):
    verdicts = _select([
        _cand("claim", _LIDO_CLAIM, "2026-03-02T07:00:00"),
        _cand("later", "Laps at the lido used to scratch that itch. Actually, scratch that, "
                       "I quit the lido in May.", "2026-04-02T07:00:00"),
    ], _LIDO_Q, past_only=past_only)
    assert not verdicts["claim"].eligible, verdicts["claim"]


@pytest.mark.parametrize("past_only", [False, True])
def test_a_later_turn_takes_back_what_the_idiom_did_not(past_only):
    # cross-turn: the idiom comes first, the correction a turn later; the claim
    # is withdrawn by the correction, never by the idiom
    verdicts = _select([
        _cand("claim", _LIDO_CLAIM, "2026-03-02T07:00:00"),
        _cand("idiom", "A cold swim at the lido would scratch that itch too.",
              "2026-03-20T07:00:00"),
        _cand("undo", "Scratch that, I quit the lido.", "2026-04-02T07:00:00"),
    ], _LIDO_Q, past_only=past_only)
    assert not verdicts["claim"].eligible, verdicts["claim"]
    assert verdicts["claim"].superseded_by == "undo", verdicts["claim"]


# ── 2. authority: a speaker takes back only their own statement ──────────────

_PADDLE = "The spare kayak paddle hangs in the boathouse loft."
_PADDLE_LATER = "The boathouse loft key is with Petra."

_ASSISTANT_TAKEBACKS = [
    "Scratch that, the boathouse loft photo I described was from another club.",
    "Never mind my note about the boathouse loft, I mixed up two messages.",
    "Please disregard what I said about the boathouse loft earlier.",
    "I retract my summary of the boathouse loft, it was wrong.",
    "on second thought ignore my boathouse loft tip",
]


@pytest.mark.parametrize("takeback", _ASSISTANT_TAKEBACKS)
def test_an_assistant_retraction_never_withdraws_what_the_user_stated(takeback):
    assert retraction_marker(takeback) is not None, takeback  # precondition: a live marker
    verdicts = _select([
        _cand("user_claim", _PADDLE, "2026-05-01T09:00:00"),
        _cand("assistant_undo", takeback, "2026-05-01T09:00:30", role="assistant"),
        _cand("user_later", _PADDLE_LATER, "2026-05-03T09:00:00"),
    ], "Where is the spare kayak paddle?")
    assert verdicts["user_claim"].slot == verdicts["assistant_undo"].slot  # one slot
    assert verdicts["user_claim"].eligible, (takeback, verdicts["user_claim"])
    assert verdicts["user_claim"].reason != "withdrawn"
    assert verdicts["user_claim"].superseded_by != "assistant_undo"


_STUDIO_NOTE = "The glassblowing studio runs its evening session on the north furnace."

_USER_TAKEBACKS = [
    "Scratch that, we are not going to the glassblowing studio.",
    "Never mind the glassblowing studio, we cancelled.",
    "forget what we said about the glassblowing studio",
    "Actually, scratch that — no glassblowing studio this week.",
    "pls ignore that glassblowing studio thing",
]


@pytest.mark.parametrize("takeback", _USER_TAKEBACKS)
def test_a_user_retraction_withdraws_the_users_claim_but_not_the_assistants(takeback):
    verdicts = _select([
        _cand("user_plan", "We booked the glassblowing studio for Friday.", "2026-06-01T10:00:00"),
        _cand("assistant_note", _STUDIO_NOTE, "2026-06-01T10:00:30", role="assistant"),
        _cand("user_undo", takeback, "2026-06-02T10:00:00"),
        _cand("user_later", "The glassblowing studio sells gift cards.", "2026-06-05T10:00:00"),
    ], "What do I know about the glassblowing studio?")
    # the user takes back their own plan ...
    assert not verdicts["user_plan"].eligible, (takeback, verdicts["user_plan"])
    assert verdicts["user_plan"].superseded_by == "user_undo"
    # ... and never what the assistant stated
    assert verdicts["assistant_note"].eligible, (takeback, verdicts["assistant_note"])
    assert verdicts["assistant_note"].reason != "withdrawn"


@pytest.mark.parametrize("takeback", [
    "Never mind the harbour shuttle, that timetable was outdated.",
    "Scratch that about the harbour shuttle, the timetable was old.",
    "Please disregard the harbour shuttle timetable I quoted.",
    "I retract the harbour shuttle detail, the page was outdated.",
])
def test_an_assistant_still_withdraws_its_own_claim(takeback):
    verdicts = _select([
        _cand("assistant_claim", "The timetable I found lists a harbour shuttle from the ferry pier.",
              "2026-07-01T08:00:00", role="assistant"),
        _cand("assistant_undo", takeback, "2026-07-01T08:01:00", role="assistant"),
        _cand("user_later", "The harbour shuttle stop is by the fish market.", "2026-07-02T08:00:00"),
    ], "Is there a harbour shuttle?")
    assert not verdicts["assistant_claim"].eligible, (takeback, verdicts["assistant_claim"])
    assert verdicts["assistant_claim"].reason == "withdrawn"
    assert verdicts["assistant_claim"].superseded_by == "assistant_undo"


def test_two_attributed_speakers_never_take_back_each_others_statements():
    ines = _cand("ines", "Ines: The rowing club meets at the boathouse.", "2026-08-01T18:00:00",
                 speaker="Ines")
    bruno = _cand("bruno", "Bruno: Scratch that, the rowing club meets at the pier.",
                  "2026-08-02T18:00:00", speaker="Bruno")
    # the chain law itself refuses a cross-speaker withdrawal ...
    assert _withdrawn_in_chain(0, [0, 1], [ines, bruno]) is None
    # ... while the same speaker's retraction withdraws (control)
    ines_undo = replace(bruno, key="ines_undo", speaker="Ines",
                        body="Ines: Scratch that, the rowing club meets at the pier.")
    assert _withdrawn_in_chain(0, [0, 1], [ines, ines_undo]) == 1
    # the public path never even shares a slot across two attributed speakers
    verdicts = _select([ines, bruno], "Where does the rowing club meet?")
    assert verdicts["ines"].eligible, verdicts["ines"]


def _locker_rows(restorer_role: str) -> list[TemporalCandidate]:
    return [
        _cand("code", "The locker combination is 4417.", "2026-09-01T12:00:00"),
        _cand("undo", "Scratch that, forget the locker combination.", "2026-09-02T12:00:00"),
        _cand("revive", "No wait, the first reading was right: the locker combination is 4417.",
              "2026-09-02T12:00:30", role=restorer_role),
        _cand("later", "The locker combination sticker peeled off.", "2026-09-04T12:00:00"),
    ]


def test_an_assistant_restoration_never_revives_what_the_user_took_back():
    verdicts = _select(_locker_rows("assistant"), "What is the locker combination?")
    assert not verdicts["code"].eligible, verdicts["code"]
    assert verdicts["code"].reason == "withdrawn" and verdicts["code"].superseded_by == "undo"


def test_the_users_own_restoration_still_revives_their_claim():
    verdicts = _select(_locker_rows("user"), "What is the locker combination?")
    assert verdicts["code"].eligible, verdicts["code"]


# ── end to end: an ordinary chat capsule ──────────────────────────────────────

_LOCKER = ("My bike locker code is 7316.", "What is my bike locker code?", "7316")
_GLAZE = ("The glaze workshop is on Sunday at 11 am.", "When is the glaze workshop?", "11 am")


def _contract_part(capsule: str) -> str:
    """The capsule records the temporal contract lets ride. The whole-turn lane
    appended after them (core/context_retrieval.py _TURN_LANE_HEADER) delivers
    whole turns of both speakers whatever their temporal verdict, so a withdrawn
    record reappears there; this contract is about the part above it."""
    return capsule.split(cr._TURN_LANE_HEADER)[0]


def _chat_capsule(tmp_path, first: str, later: str, question: str) -> str:
    from tests.test_question_date_time_leg_20261002 import _capsule, _ingest, _profile

    profile = _profile(tmp_path)
    _ingest(profile, "plainchat", [
        (_ts("2026-02-03T09:00:00"), first),
        (_ts("2026-02-20T09:00:00"), "The hallway lamp needs a new bulb."),
        (_ts("2026-03-05T09:00:00"), later),
    ])
    capsule, _telemetry = _capsule(profile, "plainchat", question)
    return capsule


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("fact, later, withdrawn", [
    # the idiom keeps the claim it shares words with
    (_LOCKER, "Fixing up the bike locker helps me scratch that itch for tinkering.", False),
    (_GLAZE, "A glaze workshop each month would scratch that creative itch.", False),
    # a cancel requested as a service keeps the claim
    (_GLAZE, "Cancel that glaze workshop booking for me.", False),
    # real corrections still withdraw the claim they name
    (_LOCKER, "Scratch that, the bike locker code is not what I sent.", True),
    (_GLAZE, "Actually, scratch that — the glaze workshop is off.", True),
])
def test_an_ordinary_chat_capsule_reads_the_idiom_as_an_assertion(tmp_path, fact, later,
                                                                  withdrawn):
    first, question, value = fact
    capsule = _chat_capsule(tmp_path, first, later, question)
    assert first in capsule, capsule  # precondition: the claim was retrieved
    assert (value not in _contract_part(capsule)) is withdrawn, (later, capsule)


# ── 3a. a retraction's act words are not what it is about ────────────────────

# Each pair shares nothing with the retraction but the retraction's own act
# words ("never mind", "on second thought", "forget what I said", "cancel
# that", "take ... down"). Before the act-word law the retraction joined the
# claim's slot through them and withdrew it.
_ACT_WORD_ONLY = [
    # clean
    ("I never skip breakfast on workdays.",
     "Never mind the bread maker, I went back to the old oven."),
    ("My second cousin lives in Gdansk.", "On second thought, the canoe trip is off."),
    ("I always forget my umbrella on Fridays.",
     "Forget what I said about the canoe, we sold it."),
    ("The library cancelled its late fees.", "Cancel that, the ferry is full."),
    ("I take the tram down to the harbour on Sundays.", "Take that photo down, it is blurry."),
    ("Pull the blinds down before noon, the sun fades the couch.",
     "Pull that post down, the venue is wrong."),
    ("My second thought was the bakery on Elm.", "On second thought, the canoe trip is off."),
    # sloppy / typed
    ("i never skip breakfast tbh", "never mind bread maker lol"),
    ("my second cousin lives in gdansk", "on second thought canoe trip off"),
    ("i always forget my umbrella", "forget what i said abt the canoe"),
    ("library cancelled late fees", "cancel that ferry is full"),
    ("i take the tram down to the harbour", "take that pic down its blurry"),
]

# negative controls: a shared SUBJECT still lets the same retractions withdraw
_SUBJECT_SHARED = [
    ("The bread maker lives on the top shelf.",
     "Never mind the bread maker, I went back to the old oven."),
    ("We are taking the canoe trip on Saturday.", "On second thought, the canoe trip is off."),
    ("We booked the island ferry for noon.", "Cancel that, the ferry is full."),
    # the removal arm: its object ("paste") names what is taken back
    ("Pasting my packing list here: tent, stove, headlamp.",
     "Take that paste down, it has my address in it."),
    # adversarial near-miss: shares the act word AND the subject
    ("I never got the bread maker to work.",
     "Never mind the bread maker, I went back to the old oven."),
]


@pytest.mark.parametrize("past_only", [False, True])
@pytest.mark.parametrize("claim, takeback", _ACT_WORD_ONLY)
def test_a_retraction_never_withdraws_a_record_sharing_only_its_act_words(claim, takeback,
                                                                          past_only):
    assert retraction_marker(takeback) is not None, takeback  # precondition: a live marker
    assert retraction_marker(claim) is None, claim
    verdicts = _select([
        _cand("claim", claim, "2026-05-01T09:00:00"),
        _cand("undo", takeback, "2026-05-03T09:00:00"),
    ], "What did I say?", past_only=past_only)
    assert verdicts["claim"].eligible, (claim, takeback, verdicts["claim"])
    assert verdicts["claim"].superseded_by is None


@pytest.mark.parametrize("past_only", [False, True])
@pytest.mark.parametrize("claim, takeback", _SUBJECT_SHARED)
def test_a_retraction_still_withdraws_the_record_whose_subject_it_names(claim, takeback,
                                                                        past_only):
    verdicts = _select([
        _cand("claim", claim, "2026-05-01T09:00:00"),
        _cand("undo", takeback, "2026-05-03T09:00:00"),
    ], "What did I say?", past_only=past_only)
    assert not verdicts["claim"].eligible, (claim, takeback, verdicts["claim"])
    assert verdicts["claim"].superseded_by == "undo"


def test_an_act_word_never_ties_a_withdrawal_across_a_bridge_record():
    # The claim shares a slot with the retraction only through a bridge record
    # about the same subject as the retraction; its one word in common with the
    # retraction is the act word "never". The chain law must not tie them.
    verdicts = _select([
        _cand("habit", "I never skip breakfast on workdays.", "2026-05-01T09:00:00"),
        _cand("bridge", "I skip breakfast when the bread maker jams.", "2026-05-02T09:00:00"),
        _cand("undo", "Never mind the bread maker, I went back to the old oven.",
              "2026-05-03T09:00:00"),
    ], "Do I skip breakfast on workdays?")
    assert verdicts["habit"].slot == verdicts["undo"].slot  # precondition: one slot
    assert verdicts["habit"].eligible, verdicts["habit"]
    assert not verdicts["bridge"].eligible, verdicts["bridge"]  # control: names the subject


# ── 3b. lexical overlap as subject identity: known boundary ──────────────────

_SUBJECT_IDENTITY_REASON = (
    "known boundary, left for a repair that resolves what a retraction names: a live "
    "retraction still joins every earlier chain it shares a speaker-name token or a value "
    "with (core/temporal_selection.py _build_slots_with_ties) and, as its slot's winner, "
    "bears on every slot-mate it is directly tied to (_marker_sentence_bears_on). Measured "
    "2026-10-06: dropping the speaker name from slot identity fails four cases of "
    "tests/test_retraction_grammar_20261003.py ('wait, forget what i said about the pot' "
    "and 'pls ignore that, key's in the shed', current and past) that reach their target "
    "only through that name, and three packing cases of "
    "tests/test_dialogue_recall_supplement_20261003.py; value ties also carry measured "
    "restorations ('No wait, the first reading was right: it is 55-014')")


@pytest.mark.xfail(strict=True, reason=_SUBJECT_IDENTITY_REASON)
def test_a_retraction_never_withdraws_the_same_speakers_unrelated_record():
    verdicts = _select([
        _cand("bike", "Ines: The spare bike lives in the cellar.", "2026-05-01T09:00:00",
              speaker="Ines"),
        _cand("violin", "Ines: My sister teaches violin in Porto.", "2026-05-02T09:00:00",
              speaker="Ines"),
        _cand("undo", "Ines: Never mind the cellar, the spare bike is at the office.",
              "2026-05-03T09:00:00", speaker="Ines"),
    ], "What does Ines's sister teach?")
    assert verdicts["violin"].eligible, verdicts["violin"]


@pytest.mark.xfail(strict=True, reason=_SUBJECT_IDENTITY_REASON)
def test_a_retraction_never_withdraws_a_record_sharing_only_a_value():
    verdicts = _select([
        _cand("ages", "My parents are 61 and 64.", "2026-05-01T09:00:00"),
        _cand("undo", "Scratch that, the paint tin holds 64 litres.", "2026-05-03T09:00:00"),
    ], "How old are my parents?")
    assert verdicts["ages"].eligible, verdicts["ages"]
