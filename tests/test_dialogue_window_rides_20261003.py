"""Imported dialogue: the reply rides with its hit, and a short turn is delivered whole.

An imported conversation between two named people stores every person's turn as a USER record whose
body opens with the speaker's label ("Ines: ..."). The general neighbour law lets only a correction
or an ASSISTANT answer ride after a hit, so in such a transcript the turn that answers a question
never reaches the capsule:

    Ines:  ... I met my new neighbour Tobiah yesterday!          <- the hit (names the subject)
    Marek: How did you two meet?
    Ines:  It happened at the pottery class on the pier.          <- the answer, no shared word

and the question that names what an anaphoric hit is about stays out too:

    Marek: That's a lovely brass lantern! Where did you find it?  <- names the subject
    Ines:  My grandfather gave it to me - it reminds me why I keep sailing.   <- the hit

Laws (core/context_retrieval.py, neighbour ride loop + _dialogue_ride):
* a turn up to two after a DELIVERED speaker-labelled hit rides when it replies (its speaker
  differs from the turn just before it) and carries unseen content; the turn just before the hit
  rides as context when another speaker said it and it names an asked term the hit lacks;
* at most two dialogue rides per hit; same exchange only (a session boundary is adjacency, not a
  reply); a bare backchannel ("Wow, nice!") never rides; unlabelled user/assistant chats keep the
  general law (no user-record ride);
* a short (<= 512 chars) speaker-labelled turn is delivered whole: its question sentences and the
  image caption after them belong to the turn (F6); hedged turns keep the narrow window.

All names, places and sentences are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from tests.test_envelope_provenance_not_content_20261003 import _items
from tests.test_question_date_time_leg_20261002 import (
    _hash_backend,
    _ingest,
    _profile,
)
from tests.test_time_leg_follows_allowance_20261003 import _wide_capsule

UTC = timezone.utc


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _session(stamp: str, header: str, turns: list[str]) -> list[tuple[float, str]]:
    return [(_ts(stamp), f"Session date: {header}\n{turn}") for turn in turns]


_INES_SMALL_TALK = [
    "The harbour wall got a fresh coat of paint this week.",
    "My cousin is visiting from the north next month.",
    "I repotted the fern on the balcony, it was root-bound.",
    "The library extended its opening hours on Thursdays.",
    "I finished the crossword in record time this morning.",
    "Our street is getting new lamps before the winter.",
    "I tried a lemon tart recipe and it came out runny.",
    "The ferry was late again because of the fog.",
    "I signed up for the choir at the community hall.",
    "My bike chain snapped halfway up the hill.",
    "The museum has an exhibit on old maps until spring.",
    "I bought a second-hand desk for the study.",
]
_MAREK_SMALL_TALK = [
    "The bakery on the corner finally reopened after the flood.",
    "I switched my commute to the early tram.",
    "My brother started a podcast about chess openings.",
    "The gym raised its fees again, which is annoying.",
    "I watched a documentary about deep sea vents.",
    "Our office moved to the fourth floor last week.",
    "I planted tomatoes, though it may be too late.",
    "The neighbour's dog barks at every delivery van.",
    "I fixed the leaking tap in the kitchen myself.",
    "The farmers market now opens at seven on Saturdays.",
    "I learned to make dumplings from a video.",
    "The power went out twice during the storm.",
]


def _filler(stamp: str, header: str) -> list[tuple[float, str]]:
    turns: list[str] = []
    for ines, marek in zip(_INES_SMALL_TALK, _MAREK_SMALL_TALK):
        turns += [f"Ines: {ines}", f"Marek: {marek}"]
    return _session(stamp, header, turns)


def _capsule(profile, chat, question):
    capsule, _telemetry = _wide_capsule(profile, chat, question, target_tokens=2048)
    return capsule


# ───────────────────────── F7: the reply rides with its hit ──────────────

_NEIGHBOUR = _filler("2026-03-02T10:00:00", "10:00 am on 2 March, 2026") + _session(
    "2026-03-09T18:00:00", "6:00 pm on 9 March, 2026", [
        "Marek: The robot kit sounds like a fun puzzle. Keep at it!",
        "Ines: Thanks! Oh, by the way, I met my new neighbour Tobiah yesterday!",
        "Marek: How did you two meet?",
        "Ines: It happened at the pottery class on the pier.",
        "Marek: Did you two talk about it much?",
        "Ines: We talked about glazes for an hour.",
    ]) + _filler("2026-03-20T10:00:00", "10:00 am on 20 March, 2026")


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "Where did Ines meet her new neighbour Tobiah?",
    "How did Ines and Tobiah meet?",
    "where did ines meet tobiah",
    "Ines met her neighbour Tobiah where?",
    "Tobiah and Ines met at which spot?",
])
def test_reply_after_question_rides_with_the_hit(tmp_path, question):
    profile = _profile(tmp_path)
    _ingest(profile, "nb", _NEIGHBOUR)
    capsule = _capsule(profile, "nb", question)
    assert "Tobiah" in capsule, capsule
    # the answer turn, two after the hit, shares no word with the ask
    assert "pottery class on the pier" in capsule, capsule
    # it travels with its own speaker label (who said it stays visible)
    answer = [item for item in _items(capsule) if "pottery class" in item]
    assert answer and "Ines:" in answer[0], capsule


_LANTERN = _filler("2026-05-01T10:00:00", "10:00 am on 1 May, 2026") + _session(
    "2026-05-03T19:00:00", "7:00 pm on 3 May, 2026", [
        "Marek: The lantern parade by the river was packed this year.",
        "Ines: I hung paper lanterns along the fence for the party.",
        "Marek: My lantern for the camping trip needs new batteries.",
        "Ines: The old lantern shop on the quay is closing down.",
    ]) + _session(
    "2026-05-08T19:00:00", "7:00 pm on 8 May, 2026", [
        "Ines: Look at this, isn't it stunning?\n[shared image: a photo of a brass lantern on a shelf]",
        "Marek: That's a lovely brass lantern! Where did you find it?",
        "Ines: My grandfather gave it to me after the regatta, and honestly it is a good reminder of "
        "why I keep sailing through the rough seasons.",
        "Marek: Keep sailing, then. It suits you.",
    ]) + _filler("2026-05-15T10:00:00", "10:00 am on 15 May, 2026")


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "What was the lantern Ines received meant to remind her of?",
    "what does the lantern remind ines of",
    "Ines's lantern from her grandfather reminds her of what?",
])
def test_question_naming_the_subject_rides_as_context_before_an_anaphoric_hit(tmp_path, question):
    profile = _profile(tmp_path)
    _ingest(profile, "lantern", _LANTERN)
    capsule = _capsule(profile, "lantern", question)
    assert "reminder of why I keep sailing" in capsule, capsule
    # the antecedent that names the lantern now sits with the anaphoric answer
    assert "lovely brass lantern! Where did you find it?" in capsule, capsule


_CATS = _filler("2026-06-01T10:00:00", "10:00 am on 1 June, 2026") + _session(
    "2026-06-12T20:00:00", "8:00 pm on 12 June, 2026", [
        "Marek: Your two cats are adorable. How did you get them?",
        "Ines: I got Pepper, the first of my cats, from my aunt when she left for Lisbon.",
        "Marek: That was kind of you. And what about the little grey one?",
        "Ines: Juniper came from the rescue on Kell Street, the volunteers there spent ages showing "
        "me how to settle a nervous animal into a new flat.",
        "Marek: They both look so content now.",
    ]) + _filler("2026-06-20T10:00:00", "10:00 am on 20 June, 2026") + _filler(
    "2026-06-27T10:00:00", "10:00 am on 27 June, 2026")


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "Where did Ines get her cats?",
    "how did ines get her cats",
    "Where are Ines's cats from?",
])
def test_same_speaker_answer_after_the_other_speakers_follow_up_rides(tmp_path, question):
    profile = _profile(tmp_path)
    _ingest(profile, "cats", _CATS)
    capsule = _capsule(profile, "cats", question)
    assert "from my aunt when she left for Lisbon" in capsule, capsule
    assert "rescue on Kell Street" in capsule, capsule


# ───────────────────────── F7 controls ───────────────────────────────────

_CROSS_SESSION = _filler("2026-07-01T10:00:00", "10:00 am on 1 July, 2026") + _session(
    "2026-07-09T18:00:00", "6:00 pm on 9 July, 2026", [
        "Marek: Busy week?",
        "Ines: I met my new neighbour Tobiah at the station yesterday! Have you met him yet?",
    ]) + _session(
    "2026-08-20T18:00:00", "6:00 pm on 20 August, 2026", [
        "Marek: The orchard by the canal sells plum jam on Sundays now.",
        "Ines: Lovely, see you there.",
    ]) + _filler("2026-08-28T10:00:00", "10:00 am on 28 August, 2026")


@pytest.mark.usefixtures("_hash_backend")
def test_a_turn_across_a_session_boundary_is_not_a_reply(tmp_path):
    profile = _profile(tmp_path)
    _ingest(profile, "xs", _CROSS_SESSION)
    capsule = _capsule(profile, "xs", "Where did Ines meet her neighbour Tobiah?")
    assert "Tobiah" in capsule, capsule
    # the next turn opens a later session: adjacency, not a reply
    assert "plum jam" not in capsule, capsule


_MONOLOGUE = _filler("2026-07-01T10:00:00", "10:00 am on 1 July, 2026") + _session(
    "2026-07-09T18:00:00", "6:00 pm on 9 July, 2026", [
        "Ines: I met my new neighbour Tobiah at the station yesterday! Isn't that funny?",
        "Ines: Also the plumber finally fixed the boiler in the cellar.",
        "Ines: And my sister bought a dinghy painted mustard yellow.",
    ]) + _filler("2026-07-28T10:00:00", "10:00 am on 28 July, 2026")


@pytest.mark.usefixtures("_hash_backend")
def test_the_same_speakers_next_turn_is_not_a_reply(tmp_path):
    profile = _profile(tmp_path)
    _ingest(profile, "mono", _MONOLOGUE)
    capsule = _capsule(profile, "mono", "Where did Ines meet her neighbour Tobiah?")
    assert "Tobiah" in capsule, capsule
    # the speaker's own next turn does not reply to anything
    assert "boiler in the cellar" not in capsule, capsule


def _backchannel(reply: str) -> list[tuple[float, str]]:
    return _filler("2026-07-01T10:00:00", "10:00 am on 1 July, 2026") + _session(
        "2026-07-09T18:00:00", "6:00 pm on 9 July, 2026", [
            "Ines: I met my new neighbour Tobiah at the station yesterday! Can you believe it?",
            f"Marek: {reply}",
            "Ines: Haha yeah, thanks!",
        ]) + _filler("2026-07-28T10:00:00", "10:00 am on 28 July, 2026")


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("reply", ["Wow, nice!", "Same here!", "Oh wow, that's awesome!", "Haha, yes, totally."])
def test_a_backchannel_reply_never_rides(tmp_path, reply):
    profile = _profile(tmp_path)
    _ingest(profile, "bc", _backchannel(reply))
    capsule = _capsule(profile, "bc", "Where did Ines meet her neighbour Tobiah?")
    assert "Tobiah" in capsule, capsule
    assert f"Marek: {reply}" not in capsule, capsule
    assert "Haha yeah" not in capsule, capsule


_NAME_ONLY = _filler("2026-07-01T10:00:00", "10:00 am on 1 July, 2026") + _session(
    "2026-07-09T18:00:00", "6:00 pm on 9 July, 2026", [
        "Ines: I met my new neighbour Tobiah at the station yesterday.",
        "Marek: Good for you.",
    ]) + _session(
    "2026-07-15T18:00:00", "6:00 pm on 15 July, 2026", [
        "Ines: I bought a lamp. Like it?",
        "Marek: It looks sturdy, the oak base is gorgeous.",
    ]) + _filler("2026-07-28T10:00:00", "10:00 am on 28 July, 2026")


@pytest.mark.usefixtures("_hash_backend")
def test_a_hit_tied_to_the_ask_only_by_its_speaker_name_anchors_no_ride(tmp_path):
    profile = _profile(tmp_path)
    _ingest(profile, "name", _NAME_ONLY)
    capsule = _capsule(profile, "name", "Where did Ines meet her neighbour Tobiah?")
    assert "Tobiah" in capsule, capsule
    # the lamp turn reached the capsule on the name "Ines" alone; its reply
    # is not about the ask
    assert "I bought a lamp. Like it?" in capsule, capsule
    assert "oak base is gorgeous" not in capsule, capsule


_NO_QUESTION = _filler("2026-07-01T10:00:00", "10:00 am on 1 July, 2026") + _session(
    "2026-07-09T18:00:00", "6:00 pm on 9 July, 2026", [
        "Ines: I met my new neighbour Tobiah at the station yesterday.",
        "Marek: The orchard by the canal sells plum jam on Sundays now.",
        "Ines: My sister bought a dinghy painted mustard yellow.",
    ]) + _filler("2026-07-28T10:00:00", "10:00 am on 28 July, 2026")


@pytest.mark.usefixtures("_hash_backend")
def test_a_turn_that_answers_no_question_does_not_ride(tmp_path):
    profile = _profile(tmp_path)
    _ingest(profile, "nq", _NO_QUESTION)
    capsule = _capsule(profile, "nq", "Where did Ines meet her neighbour Tobiah?")
    assert "Tobiah" in capsule, capsule
    # adjacency without the answer-after-question shape is not a reply
    assert "plum jam" not in capsule, capsule


_CAPPED = _filler("2026-07-01T10:00:00", "10:00 am on 1 July, 2026") + _session(
    "2026-07-09T18:00:00", "6:00 pm on 9 July, 2026", [
        "Marek: I saw a telescope on your balcony, is it new?",
        "Ines: I assembled it last night. Want to look through it?",
        "Marek: Sure, I would love to see the rings of Saturn through it. Is Friday good?",
        "Ines: Come by on Friday after the choir rehearsal then.",
    ]) + _filler("2026-07-28T10:00:00", "10:00 am on 28 July, 2026")


def _dialogue_rides_per_hit(telemetry) -> dict[str, list[str]]:
    per_hit: dict[str, list[str]] = {}
    for ref in telemetry.get("evidence_refs") or []:
        hit = ref.get("dialogue_reply_to") or ref.get("dialogue_context_for")
        if ref.get("delivered") and hit:
            per_hit.setdefault(str(hit), []).append(str((ref.get("span") or {}).get("text") or ""))
    return per_hit


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("cap", [1, 2])
def test_dialogue_rides_per_hit_are_capped(tmp_path, monkeypatch, cap):
    monkeypatch.setattr(cr, "_DIALOGUE_RIDES_PER_HIT", cap)
    profile = _profile(tmp_path)
    _ingest(profile, "cap", _CAPPED)
    capsule, telemetry = _wide_capsule(profile, "cap", "When did Ines assemble her telescope?",
                                       target_tokens=2048)
    assert "I assembled it last night" in capsule, capsule
    per_hit = _dialogue_rides_per_hit(telemetry)
    assert per_hit, telemetry.get("evidence_refs")
    assert all(len(rides) <= cap for rides in per_hit.values()), per_hit
    rides = [text for texts in per_hit.values() for text in texts]
    # replies ride in spoken order: the first reply always, the second only within the cap
    assert any("rings of Saturn" in text for text in rides), per_hit
    assert any("after the choir rehearsal" in text for text in rides) == (cap >= 2), per_hit


_UNLABELLED = [
    (_ts("2026-07-09T18:00:00"), "I met my new neighbour Tobiah at the station yesterday!"),
    (_ts("2026-07-09T18:01:00"), "The plumber finally fixed the boiler in the cellar."),
    (_ts("2026-07-09T18:02:00"), "My sister bought a dinghy painted mustard yellow."),
]


@pytest.mark.usefixtures("_hash_backend")
def test_unlabelled_user_records_keep_the_general_neighbour_law(tmp_path):
    profile = _profile(tmp_path)
    _ingest(profile, "plain", _UNLABELLED)
    capsule = _capsule(profile, "plain", "Where did I meet my neighbour Tobiah?")
    assert "Tobiah" in capsule, capsule
    assert "boiler in the cellar" not in capsule, capsule
    assert "mustard yellow" not in capsule, capsule


class _Turn:
    def __init__(self, body, *, role="user", statement_at=1.0e9, integrity="verified"):
        self.body = body
        self.role = role
        self.statement_at = statement_at
        self.body_integrity = integrity
        self.speaker = ""
        self.status = "active"


@pytest.mark.parametrize("body,speaker", [
    ("Session date: 6:00 pm on 9 July, 2026\nIness: It happened at the pottery class.", "Iness"),
    ("Marek Vale: How did you two meet?", "Marek Vale"),
    ("Session date: 2026-07-09\nOrla: Yes.", "Orla"),
])
def test_dialogue_turn_speaker_reads_the_single_reported_label(body, speaker):
    assert cr._dialogue_turn_speaker(_Turn(body)) == speaker


@pytest.mark.parametrize("turn", [
    _Turn("It happened at the pottery class."),                         # no label
    _Turn("Ines: It happened at the pottery class.", role="assistant"),  # assistant output
    _Turn("User: where did we meet?"),                                   # role scaffold
    _Turn("Note: the boiler is fixed."),                                 # register label
    _Turn("Ines: hi\nMarek: hello"),                                     # two labels
    _Turn("Ines: It happened at the pottery class.", integrity="unverified"),
])
def test_records_that_are_not_dialogue_turns(turn):
    assert cr._dialogue_turn_speaker(turn) == ""


def test_same_exchange_is_bounded_by_statement_time():
    first = _Turn("Ines: a", statement_at=1_000_000.0)
    assert cr._same_dialogue_exchange(first, _Turn("Marek: b", statement_at=1_000_060.0))
    assert not cr._same_dialogue_exchange(first, _Turn("Marek: b", statement_at=1_000_000.0 + 40 * 86400))
    assert cr._same_dialogue_exchange(first, _Turn("Marek: b", statement_at=None))


# ───────────────────────── F6: short turns delivered whole ───────────────

_HOBBY = _filler("2026-04-01T10:00:00", "10:00 am on 1 April, 2026") + _session(
    "2026-04-11T16:00:00", "4:00 pm on 11 April, 2026", [
        "Marek: How was the gallery opening?",
        "Oskar: Hey Marek, it was great meeting those painters! Also, check out this project - "
        "I love restoring it to unwind. What about you? Any hobbies that help you relax?\n"
        "[shared image: a photo of a dented teal motorbike in a garage]",
        "Marek: Gardening, mostly.",
    ]) + _filler("2026-04-20T10:00:00", "10:00 am on 20 April, 2026")


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "What project does Oskar restore to unwind?",
    "what does oskar restore to unwind",
    "Which project helps Oskar unwind?",
])
def test_short_turn_with_a_question_is_delivered_whole_with_its_caption(tmp_path, question):
    profile = _profile(tmp_path)
    _ingest(profile, "hobby", _HOBBY)
    capsule = _capsule(profile, "hobby", question)
    assert "restoring it to unwind" in capsule, capsule
    # the question sentence and the caption after it stay with the turn
    assert "Any hobbies that help you relax?" in capsule, capsule
    assert "dented teal motorbike" in capsule, capsule


_SUPERMARKET = _filler("2026-04-01T10:00:00", "10:00 am on 1 April, 2026") + _session(
    "2026-04-11T16:00:00", "4:00 pm on 11 April, 2026", [
        "Oskar: Thanks, Marek! Btw you know what? I went to the hardware store again and, "
        "predictably, had trouble with the paint mixer. It keeps jamming on the lids.",
    ]) + _filler("2026-04-20T10:00:00", "10:00 am on 20 April, 2026")


@pytest.mark.usefixtures("_hash_backend")
def test_comma_clause_is_not_cut_out_of_a_short_turn(tmp_path):
    profile = _profile(tmp_path)
    _ingest(profile, "store", _SUPERMARKET)
    capsule = _capsule(profile, "store", "What trouble did Oskar have at the hardware store?")
    assert "had trouble with the paint mixer" in capsule, capsule
    assert "It keeps jamming on the lids." in capsule, capsule
    assert "I went to the hardware store again and, predictably, had trouble" in capsule, capsule


def test_complete_window_allows_question_form_only_for_a_dialogue_turn():
    turn = _Turn("Session date: 4:00 pm on 11 April, 2026\nOskar: I restore it to unwind. "
                 "What about you?\n[shared image: a photo of a teal motorbike]")
    window = cr._complete_source_window(turn, "I restore it to unwind.", max_chars=4000)
    assert window is not None and window["text"] == turn.body
    # an unlabelled record keeps the question-masking law
    plain = _Turn("I restore it to unwind. What about you?")
    assert cr._complete_source_window(plain, "I restore it to unwind.", max_chars=4000) is None
    # a hedged dialogue turn keeps the narrow window (hedge law unchanged)
    hedged = _Turn("Oskar: Maybe I could sell the motorbike. What do you think?")
    assert cr._complete_source_window(hedged, "Maybe I could sell the motorbike.", max_chars=4000) is None
    # a long dialogue turn keeps the bounded window
    long_turn = _Turn("Oskar: " + "I restore old bikes in the shed. " * 20 + "What about you?")
    assert cr._complete_source_window(long_turn, "I restore old bikes in the shed.", max_chars=4000) is None
