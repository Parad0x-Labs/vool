"""A relayed record keeps its stated speaker on every fragment the reader receives.

Contract: when a stored record carries exactly ONE unquoted source label on its
first substantive line ("Tomas: ..."), every delivered fragment of that record
shows the label, whatever the fragment's grammatical person (first person,
imperative, subject-less clause, third person) and whichever delivery path
selected it. Without the label a fragment reads as the user's own statement.

The label stays reported provenance: it never becomes the user's identity, and
ambiguous, quoted or multi-label sources still receive no label (see
tests/test_source_prefix_binding.py).
"""
from __future__ import annotations

import re

import pytest

from tests.test_overnight_source_structure import _recall, _store, source_env  # noqa: F401

_ENTRY = re.compile(r"^- (?P<head>[^:\n]*?)(?: \((?:stated|recorded)[^)]*\))?: (?P<body>.*)$", re.S)


def _entries(capsule: str) -> list[str]:
    out: list[str] = []
    for line in capsule.splitlines():
        if line.startswith("- "):
            out.append(line)
        elif out and not line.startswith("</retrieved_context>"):
            out[-1] += "\n" + line
    return out


def _visible_speaker(entry: str, speakers: tuple[str, ...]) -> str | None:
    """The speaker a reader can see on a delivered line: the reported-prefix
    annotation, or an inline 'Name:' label at the start of the utterance."""
    annotated = re.search(r'reported source prefix "([A-Za-z ]+):"', entry)
    if annotated:
        return annotated.group(1)
    match = _ENTRY.match(entry)
    body = match.group("body") if match else entry
    body = re.sub(r"^Session date:[^\n]*\n", "", body)
    inline = re.match(r"^([A-Z][A-Za-z]+):\s", body)
    return inline.group(1) if inline and inline.group(1) in speakers else None


def _utterance_text(entry: str) -> str:
    match = _ENTRY.match(entry)
    body = match.group("body") if match else entry
    body = re.sub(r"\s*\((?:stated|recorded)[^)]*\)\s*$", "", body)
    body = re.sub(r"^Session date:[^\n]*\n?", "", body)
    return re.sub(r"^[A-Z][A-Za-z]+:\s", "", body).strip()


def _assert_every_line_bound(capsule: str, records: list[str], speakers: tuple[str, ...]) -> int:
    """Every delivered line whose text is a fragment of exactly one stored,
    single-label record shows that record's label. Returns the lines checked."""
    checked = 0
    for entry in _entries(capsule):
        text = _utterance_text(entry)
        if len(text) < 8:
            continue
        owners = [re.search(r"\n([A-Z][A-Za-z]+): ", r).group(1) for r in records if text in r]
        if len(owners) != 1:
            continue
        checked += 1
        assert _visible_speaker(entry, speakers) == owners[0], (
            f"speaker lost or wrong on delivered line:\n{entry}\n---\n{capsule}")
    return checked


# Short records (complete-source delivery): regression cover for the inline label.
SHORT_SPEAKERS = ("Priya", "Tomas")
SHORT_RECORDS = [
    "Session date: 4:15 pm on 2 March, 2021\nPriya: Hi Tomas! Busy week? I repainted the hallway on Sunday.",
    "Session date: 4:15 pm on 2 March, 2021\nTomas: Pretty good. I rode my bike up to the old lighthouse on Saturday! The view was huge.",
    "Session date: 4:15 pm on 2 March, 2021\nPriya: That sounds lovely! My hallway is now a pale sage green.",
    "Session date: 4:15 pm on 2 March, 2021\nTomas: My neighbour Ines borrowed the canoe for the lake trip. The wind stayed low all afternoon.",
]
SHORT_ASKS = [
    ("What did Priya ride up to the old lighthouse?", "bike", "Tomas"),
    ("Who borrowed the canoe for the lake trip?", "canoe", "Tomas"),
    ("What colour is Priya's hallway now?", "sage green", "Priya"),
]


@pytest.mark.parametrize("ask,needle,owner", SHORT_ASKS)
def test_relayed_fragment_shows_its_stated_speaker(source_env, ask, needle, owner):
    chat = "relay-short-" + needle.split()[0]
    for record in SHORT_RECORDS:
        _store(source_env, chat, record, "", stated=1614701700)
    capsule = _recall(source_env, chat, ask)
    print("CAPSULE[" + ask + "]\n" + capsule)
    carrying = [e for e in _entries(capsule) if needle in e.lower()]
    assert carrying, capsule
    for entry in carrying:
        assert _visible_speaker(entry, SHORT_SPEAKERS) == owner, (
            f"speaker lost or wrong on delivered line:\n{entry}\n---\n{capsule}")


def test_reported_speaker_never_becomes_user_identity(source_env):
    for record in SHORT_RECORDS:
        _store(source_env, "relay-identity", record, "", stated=1614701700)
    capsule = _recall(source_env, "relay-identity", "What is my name?")
    assert not re.search(r"user(?:'s)? name is (?:Priya|Tomas)", capsule, re.I), capsule
    assert "user said: Tomas" not in capsule.replace("[", ""), capsule


# Records longer than _COMPLETE_SOURCE_MAX_CHARS (512) are delivered as fragments.
# The middle fragments below are an exclamation with no pronoun and an
# imperative; both were said by the labelled speaker.
_PAD = (" The rest of the call drifted across the rain forecast, a delayed train, a new bakery two streets "
        "over, last night's chess final, a programme about glaciers, the cost of bus tickets, and plenty "
        "of other small talk that has no bearing on anything asked later in this record.")
LONG_SPEAKERS = ("Lena", "Marco")
LONG_RECORDS = [
    "Session date: 10:05 am on 14 June, 2021\nMarco: Received a letter about a weekend job at a community radio station."
    + _PAD + " Hosting the late show there seems like great fun." + _PAD,
    "Session date: 10:05 am on 14 June, 2021\nLena: Hosting a late radio show sounds fantastic!"
    + _PAD + " Remember to send the station a demo tape before Thursday, the slots fill quickly." + _PAD,
    "Session date: 10:05 am on 14 June, 2021\nLena: Lately I have been baking sourdough on weekends."
    + _PAD + _PAD,
]
LONG_ASKS = [
    ("What kind of job was Lena offered at the community radio station?", "weekend job", "Marco"),
    ("What does hosting a late radio show sound like to Lena?", "sounds fantastic", "Lena"),
    ("Who should send the demo tape before Thursday?", "demo tape", "Lena"),
]


@pytest.mark.parametrize("ask,needle,owner", LONG_ASKS)
def test_fragment_of_long_relayed_record_shows_its_stated_speaker(source_env, ask, needle, owner):
    chat = "relay-long-" + needle.split()[0]
    for record in LONG_RECORDS:
        assert len(record) > 512
        _store(source_env, chat, record, "", stated=1623665100)
    capsule = _recall(source_env, chat, ask)
    print("CAPSULE[" + ask + "]\n" + capsule)
    assert any(needle in e.lower() for e in _entries(capsule)), capsule
    assert _assert_every_line_bound(capsule, LONG_RECORDS, LONG_SPEAKERS) >= 1, capsule


# Unit-level family on the binder itself: the label binds by record, not by the
# fragment's grammatical person. Every fragment below was said by "Tomas".
def _occurrence(body: str, integrity: str = "verified", role: str = "user"):
    from types import SimpleNamespace
    return SimpleNamespace(body=body, body_integrity=integrity, role=role, occurrence_id="occ-1",
                           authority="", chat_scope="family", statement_at=None, recorded_at=None)


def _bind(body: str, fragment: str, **kwargs):
    from core.context_retrieval import _reported_source_prefix_receipt
    start = body.index(fragment)
    return _reported_source_prefix_receipt(_occurrence(body, **kwargs), start, start + len(fragment), fragment)


BOUND_FRAGMENTS = [
    # imperative, subject-less, third person, question, exclamation
    "Remember to send the station a demo tape before Thursday.",
    "Don't forget to post the forms by Monday.",
    "Ines borrowed the canoe for the lake trip.",
    "Sounds like a wonderful plan!",
    "Was the ferry late again?",
    "Their garden won second prize at the fair.",
    "Bought new tyres for the van yesterday.",
    # sloppy, user-typed
    "gotta call the vet tmrw",
    "remeber to feed teh cat before 6",
    "ferry late again??",
    "new tyres van yesterday",
    "NO WAY that happened lol",
    # first person: bound before and after the change
    "I tuned the piano on Sunday.",
]


@pytest.mark.parametrize("fragment", BOUND_FRAGMENTS)
@pytest.mark.parametrize("header", ["", "Session date: 4:15 pm on 2 March, 2021\n"])
def test_single_label_binds_every_grammatical_person(fragment, header):
    body = header + "Tomas: Long week here. " + fragment + " Anyway, talk soon."
    receipt = _bind(body, fragment)
    assert receipt is not None, f"fragment lost its speaker: {fragment!r}"
    assert receipt["reported_source_prefix"]["text"] == "Tomas:"
    span = receipt["span"]
    assert body[span["start"]:span["end"]] == fragment
    prefix = receipt["reported_source_prefix"]
    assert body[prefix["start"]:prefix["end"]] == "Tomas:"


@pytest.mark.parametrize("body,fragment", [
    # two speakers: ambiguous, no label
    ("Tomas: Long week here. Was the ferry late again?\nPriya: It was on time.", "Was the ferry late again?"),
    # no label at all
    ("Long week here. Was the ferry late again? Anyway, talk soon.", "Was the ferry late again?"),
    # double-quoted relay inside the labelled line
    ('Tomas: She wrote "Was the ferry late again?" on the card.', "Was the ferry late again?"),
    # single-quoted relay inside the labelled line
    ("Tomas: She wrote 'Was the ferry late again?' on the card.", "Was the ferry late again?"),
    # label not on the first substantive line
    ("The card said this.\nTomas: Was the ferry late again?", "Was the ferry late again?"),
])
def test_ambiguous_or_quoted_or_late_label_still_binds_nothing(body, fragment):
    assert _bind(body, fragment) is None


def test_fragment_overlapping_the_label_or_unverified_source_binds_nothing():
    body = "Tomas: Was the ferry late again? Anyway, talk soon."
    assert _bind(body, "Tomas: Was the ferry") is None
    assert _bind(body, "Was the ferry late again?", integrity="legacy-unverified") is None
    assert _bind(body, "Was the ferry late again?", role="unknown") is None
