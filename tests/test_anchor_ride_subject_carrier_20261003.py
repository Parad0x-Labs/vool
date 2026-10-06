"""Anchor rides go to carriers that state the asked subject.

After anchor carriers were routed through the stored occurrence, the lane's 2-ride cap was spent on
carriers that bind nothing (independent verifier, 2026-10-03, a dev question lost its gold turn): a
turn that names the asked person only as its ADDRESSEE ("Hey Ines! I started a running club ..."), a
bare reply ("Ines: No, not yet."), and a turn whose only tie to the question is its speaker label.

Laws: an addressed (vocative) name is not a subject; reply particles (no / not yet / nope / maybe /
sorry ...) are acknowledgment register; a span whose own words share no content term with the
question takes no ride; carriers that bind more of the missing anchors and of the question's
content terms ride first.

All names, places and sentences are synthetic.
"""

from __future__ import annotations

import pytest

import core.context_retrieval as cr
from tests.test_question_date_time_leg_20261002 import (  # noqa: F401
    _hash_backend,
    _ingest,
    _profile,
)
from tests.test_time_leg_follows_allowance_20261003 import _ts, _wide_capsule


# ───────────────────────── anchor rides bind the asked subject ────────────

@pytest.mark.parametrize("span", [
    "Ines: No, not yet.",
    "Nope.",
    "Maybe, sorry!",
    "Ines: Not yet, sorry!",
    "Ines: Well, probably not.",
    "Wren: Hey Ines!",
])
def test_bare_replies_are_acknowledgments(span):
    assert cr._span_is_bare_acknowledgment(span)


@pytest.mark.parametrize("span", [
    "Ines: No, not yet. The van still needs a new clutch.",
    "Ines: We're not engaged yet but we've been together for four years.",
    "Ines: Not yet, the ferry was cancelled.",
])
def test_negated_content_is_not_an_acknowledgment(span):
    assert not cr._span_is_bare_acknowledgment(span)


@pytest.mark.parametrize("text,name", [
    ("Wren: Hey Ines! I started a running club with Pia.", "Ines"),
    ("Thanks, Ines! The club meets on Sundays.", "Ines"),
    ("Hi Ines, long time no see.", "Ines"),
    ("That's amazing, Ines!", "Ines"),
    ("How was the trip, Ines?", "Ines"),
    ("Good morning Ines! Coffee?", "Ines"),
])
def test_addressed_name_is_masked(text, name):
    masked = cr._addressee_masked(text)
    assert len(masked) == len(text)
    assert name not in masked
    assert not cr._query_terms_in_text(
        cr._anchor_terms_view(text), {name.lower()}, cr._stemmed_token_set(cr._anchor_terms_view(text)))


@pytest.mark.parametrize("text", [
    "Ines bought a kiln last spring.",
    "I went to the fair with my sister, Ines.",
    "Wren: Ines and I started a running club.",
    "Hey, Ines started a running club!",
    "Ines: We moved to Galway.",
])
def test_subject_names_are_not_masked(text):
    assert cr._addressee_masked(text) == text


# end to end: the question names Ines; delivered lines carry Ines only as a
# speaker label, so the anchor lane must bind "ines"; competing carriers
# (addressee-only, bare reply, speaker-label-only small talk) recorded BEFORE
# the carrier that states the asked subject must not spend the 2-ride cap.
_DATING_LOG = [
    ("2024-01-05T10:00:00", "Session date: 10:00 am on 5 January, 2024\n"
     "Wren: Hey Ines! I started a running club with Pia - great people, every Sunday!"),
    ("2024-01-06T10:00:00", "Session date: 10:00 am on 6 January, 2024\n"
     "Ines: No, not yet. I want to settle the plan first. Can't wait to start, though!"),
    ("2024-01-07T10:00:00", "Session date: 10:00 am on 7 January, 2024\n"
     "Ines: Life's been so hectic since we last talked."),
    ("2024-01-08T10:00:00", "Session date: 10:00 am on 8 January, 2024\n"
     "Wren: Hi Ines! We haven't caught up in ages!"),
    ("2024-01-09T10:00:00", "Session date: 10:00 am on 9 January, 2024\n"
     "Ines: We're not engaged yet but we've been together for four years."),
    ("2024-01-10T10:00:00", "Session date: 10:00 am on 10 January, 2024\n"
     "Ines: Here's me and my partner at the harbour festival last year - such fun!"),
    ("2024-01-11T10:00:00", "Session date: 10:00 am on 11 January, 2024\n"
     "Wren: Pia brought her dog to the club, it ran the whole loop."),
]


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "Which year did Ines and her partner start dating? Give an approximate year from the session date.",
    "Which year did Ines and her partner start dating?",
    "roughly what year did ines and her partner get together",
])
def test_anchor_ride_reaches_the_subject_carrier(tmp_path, question):
    profile = _profile(tmp_path)
    _ingest(profile, "dating", [(_ts(t), text) for t, text in _DATING_LOG])
    capsule, _tel = _wide_capsule(profile, "dating", question, target_tokens=8192)
    assert "together for four years" in capsule, capsule


# ───────────────────────── label-only carriers, stems and ride order ───────
#
# Dev replay of the first version of these laws (parent vs HEAD, 250 dev
# questions) showed three more shapes: a reaction-then-name addressee ("That
# sounds cool, Ines."), question terms that only match a function word by stem
# ("use" ~ "us", "Ines" ~ "in"), and a total-overlap ride order that let
# answer-option words outrank the carrier stating the subject. A carrier whose
# own words bind the anchor through an attached caption must keep riding.

@pytest.mark.parametrize("text", [
    "Wren: That sounds cool, Ines. Stories can teach us a lot.",
    "Right, Ines, the van is fixed.",
    "Wow, thanks, Ines!",
])
def test_reaction_then_name_is_the_addressee(text):
    assert "Ines" not in cr._addressee_masked(text)


@pytest.mark.parametrize("span", ["Orla: Aww, bummer!", "Oops, sorry!", "Ugh, darn."])
def test_bare_interjections_are_acknowledgments(span):
    assert cr._span_is_bare_acknowledgment(span)


@pytest.mark.parametrize("text,terms,expected", [
    ("they can teach us and bring us together", {"use"}, set()),
    ("we haven't caught up in ages", {"ines"}, set()),
    ("because it rained", {"use"}, set()),
    ("You'll find it, just keep trying new things.", {"finding"}, {"finding"}),
    ("Ines: we've been together for four years", {"year", "ines"}, {"year", "ines"}),
])
def test_content_word_hits_ignore_function_word_stems(text, terms, expected):
    assert cr._content_word_hits(text, terms) == expected


def test_missing_anchor_count_reads_the_anchor_view():
    missing = {"ines", "dating"}
    assert cr._anchor_missing_bound(
        "Session date: 8 January, 2024\nWren: Hi Ines! We haven't caught up in ages!", missing) == 0
    assert cr._anchor_missing_bound(
        "Session date: 8 January, 2024\nIdris: Ines has been dating Bram since the spring.", missing) == 2
    assert cr._anchor_missing_bound("Ines: Long week, finally home.", missing) == 1


_CONTENT = {"ines", "partner", "year", "dating", "approximate", "use"}
_MISSING = {"ines", "dating", "approximate"}


@pytest.mark.parametrize("body,span,expected", [
    # bound by the label alone and the span says nothing asked about
    ("Ines: Life's been so hectic since we last talked.",
     "Ines: Life's been so hectic since we last talked.", False),
    # an addressed name and a stem collision ("us" ~ "use") bind nothing
    ("Wren: That sounds cool, Ines. Stories can teach us and bring us together.",
     "Stories can teach us and bring us together.", False),
    # bound by the label alone, but the span states the asked subject
    ("Ines: We're not engaged yet but we've been together for four years.",
     "Ines: We're not engaged yet but we've been together for four years.", True),
    # the record's own words bind the anchor (here an attached caption): rides
    ("Idris: I only paint in the shed.\n[shared image: a photo of Ines dating on a pier]",
     "Idris: I only paint in the shed.", True),
])
def test_label_only_carrier_must_state_the_subject(body, span, expected):
    assert cr._anchor_span_states_subject(body, span, _MISSING, _CONTENT) is expected


# end to end: two label-only small-talk carriers recorded before the carrier
# that states the subject must not spend the 2-ride cap
_SMALLTALK_LOG = [
    ("2024-02-01T10:00:00", "Session date: 10:00 am on 1 February, 2024\n"
     "Ines: Here's me and my partner at the harbour festival last year - such fun!"),
    ("2024-02-02T10:00:00", "Session date: 10:00 am on 2 February, 2024\n"
     "Ines: Life's been so hectic since we last talked."),
    ("2024-02-03T10:00:00", "Session date: 10:00 am on 3 February, 2024\n"
     "Ines: Long week at the studio, I'm finally home."),
    ("2024-02-04T10:00:00", "Session date: 10:00 am on 4 February, 2024\n"
     "Ines: The bakery on the corner closed down, so sad."),
    ("2024-02-05T10:00:00", "Session date: 10:00 am on 5 February, 2024\n"
     "Ines: We're not engaged yet but we've been together for four years."),
    ("2024-02-06T10:00:00", "Session date: 10:00 am on 6 February, 2024\n"
     "Wren: Pia brought her dog to the club, it ran the whole loop."),
]


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "Which year did Ines and her partner start dating? Give an approximate year from the session date.",
    "Which year did Ines and her partner start dating?",
])
def test_label_only_small_talk_does_not_spend_the_ride_cap(tmp_path, question):
    profile = _profile(tmp_path)
    _ingest(profile, "smalltalk", [(_ts(t), text) for t, text in _SMALLTALK_LOG])
    capsule, _tel = _wide_capsule(profile, "smalltalk", question, target_tokens=8192)
    assert "together for four years" in capsule, capsule
