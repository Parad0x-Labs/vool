"""A record's envelope is provenance, never content.

Transcript records arrive with a leading envelope line ("Session date: 4:15 pm on 20 April, 2023",
"Logged on 2024-03-01 09:30"). The capsule restates that date as the record's provenance, so the
envelope's own words assert nothing. Measured on the fork-v5 wiring audit (dev questions only): a
question whose wording shares the envelope's role words ("... answer with the session date", "...
the date of the chat") matched EVERY record through its envelope - date-question capsules carried
envelope-only items ("- user said: Session date: 10:49 am on 29 October, 2023 (stated: 2023-10-29)")
and envelope-anchored windows, and the answer line sat near the end.

Law: a window or fragment made only of a record's envelope is never delivered, on any lane, and
envelope lines never count toward lexical query-term coverage or ranking (clause windows and their
short-clause extension, distiller chunk ranking and echo, evidence-span coverage, time-leg overlap).
Stored text, delivered text and provenance are unchanged.

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


# ───────────────────────── envelopes and questions ─────────────────────────

ENVELOPES = [
    "Session date: 4:15 pm on 20 April, 2023",
    "Session date: 2023/04/20",
    "Logged on 2023-04-20 09:15",
    "Please remember: Session date: 20 April 2023",
    "Session date: 9:02 am on April 20, 2023",
]

#: questions that share the envelope's role words (session / date / logged /
#: chat / conversation) on top of their subject: original shape, paraphrases
#: and sloppy typed variants
ECHO_QUESTIONS = [
    "When did Orla sail to Inishmore? Answer with the session date.",
    "When did Orla sail to Inishmore? Give the approximate date from the conversation session.",
    "Using the date of the session, when did Orla go out to Inishmore?",
    "What date did Orla sail the boat to Inishmore, going by when it was logged?",
    "Orla's Inishmore crossing: which date? use the chat session date",
    "when did orla sail inishmore?? use the session date pls",
    "orla inishmore trip date - from the logged session",
    "When did Orla sail to Inishmore? Answer with an approximate DATE from the SESSION.",
    "orla went inishmore when, give date of conversation",
    "When did Orla sail to Inishmore? date of the chat is fine",
]

ANSWER = "Orla: We finally sailed the yawl out to Inishmore and anchored off Kilronan."
FILLER = "Orla: Thanks, talk soon!"


def _record(envelope: str, line: str) -> str:
    return envelope + "\n" + line


def _envelope_only(body: str, window: dict) -> bool:
    # the record's first line is its envelope: a window lying inside it
    # (the whole line or a fragment of it) carries nothing else
    envelope_end = len(body.split("\n", 1)[0])
    return int(window["start"]) < envelope_end and int(window["end"]) <= envelope_end + 1


@pytest.mark.parametrize("envelope", ENVELOPES)
@pytest.mark.parametrize("question", ECHO_QUESTIONS)
def test_no_clause_window_is_made_of_the_envelope(envelope, question):
    for line in (ANSWER, FILLER):
        body = _record(envelope, line)
        windows = cr._evidence_clause_windows(question, body)
        assert not [w for w in windows if _envelope_only(body, w)], (envelope, line, windows)


@pytest.mark.parametrize("envelope", ENVELOPES)
@pytest.mark.parametrize("question", ECHO_QUESTIONS)
def test_answer_window_still_found_beside_the_envelope(envelope, question):
    body = _record(envelope, ANSWER)
    windows = cr._evidence_clause_windows(question, body)
    assert any("Inishmore" in str(w["text"]) for w in windows), windows


@pytest.mark.parametrize("envelope", ENVELOPES)
@pytest.mark.parametrize("question", ECHO_QUESTIONS[:5])
def test_distiller_never_ranks_the_envelope_line(envelope, question):
    contents = [_record(envelope, FILLER), _record(envelope, ANSWER)]
    ranked = cr._ranked_recall_chunks(question, contents)
    envelope_texts = {" ".join(envelope.split())}
    assert not [r for r in ranked if " ".join(str(r[2]).split()) in envelope_texts], ranked
    assert any("Inishmore" in str(r[2]) for r in ranked), ranked


def test_envelope_role_words_are_not_coverage_but_content_words_are():
    body = _record("Session date: 4:15 pm on 20 April, 2023", ANSWER)
    masked = cr._envelope_masked(body)
    assert len(masked) == len(body)
    assert "Session" not in masked and "2023" not in masked
    assert masked.endswith(ANSWER)


# negative controls: dated declarations and content that merely mentions a date are content
@pytest.mark.parametrize("body,question,expected", [
    ("On June 12, 2025, the orchard inspection is in the east annex.",
     "What date is the orchard inspection?", "orchard inspection"),
    ("Orla: We set the date for the regatta: the 14th of May.",
     "What date did Orla set for the regatta?", "regatta"),
    ("Session date: 2023/04/20\nOrla: The session at the harbour office ran late, the date for "
     "the hearing moved to Friday.", "What date did the hearing move to?", "hearing moved"),
])
def test_dated_declarations_and_date_content_still_window(body, question, expected):
    windows = cr._evidence_clause_windows(question, body)
    assert any(expected in str(w["text"]) for w in windows), windows


def test_near_miss_envelope_with_an_assertion_after_the_date_is_content():
    # adversarial near-miss: reads like an envelope, but asserts something after its date
    body = "Session date: 20 April, 2023 moved to the harbour office annex."
    assert not cr._is_metadata_only_line(body)
    windows = cr._evidence_clause_windows("Where did the session move to?", body)
    assert any("harbour office" in str(w["text"]) for w in windows), windows


# ───────────────────────── end to end through the capsule ─────────────────

_VOYAGE_LOG = [
    ("2023-03-02T14:00:00", "Session date: 2:00 pm on 2 March, 2023\nOrla: Thanks, talk soon!"),
    ("2023-03-09T10:00:00", "Session date: 10:00 am on 9 March, 2023\nDeclan: Morning! Hope the weather holds."),
    ("2023-03-16T18:30:00", "Session date: 6:30 pm on 16 March, 2023\nOrla: The varnish on the tiller is finally dry."),
    ("2023-03-23T09:10:00", "Session date: 9:10 am on 23 March, 2023\nDeclan: Bye for now, take care."),
    ("2023-04-20T16:15:00", "Session date: 4:15 pm on 20 April, 2023\n" + ANSWER),
    ("2023-04-27T08:45:00", "Session date: 8:45 am on 27 April, 2023\nDeclan: Haha nice one, cheers."),
    ("2023-05-04T19:20:00", "Session date: 7:20 pm on 4 May, 2023\nOrla: Busy week with the boatyard paperwork."),
    ("2023-05-11T12:05:00", "Session date: 12:05 pm on 11 May, 2023\nDeclan: Sounds good to me."),
]


def _items(capsule: str) -> list[str]:
    items: list[str] = []
    for line in capsule.splitlines():
        if line.startswith("- "):
            items.append(line)
        elif items and not line.startswith("</retrieved_context>"):
            items[-1] += "\n" + line
    return items


def _payload(item: str) -> str:
    import re

    text = re.sub(r"^- [^:\n]{0,160}?\bsaid\b(?: \[[^\]]*\])?(?: \([^)]*\))?: ?", "", item, count=1)
    text = cr._PROVENANCE_SUFFIX_RE.sub("", text)
    return "\n".join(line for line in text.splitlines()
                     if not (line.strip() and cr._is_metadata_only_line(line)))


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", ECHO_QUESTIONS)
def test_capsule_carries_no_envelope_only_item(tmp_path, question):
    profile = _profile(tmp_path)
    _ingest(profile, "voyage", [(_ts(t), text) for t, text in _VOYAGE_LOG])
    capsule, _tel = _wide_capsule(profile, "voyage", question, target_tokens=8192)
    assert "Inishmore" in capsule, capsule
    empty = [item for item in _items(capsule) if not any(ch.isalpha() for ch in _payload(item))]
    assert not empty, capsule
    # Declan's records share no word with any of these questions except
    # through their envelope ("Session date"): an envelope match is not a
    # reason to deliver a record
    assert "Declan" not in capsule, capsule


@pytest.mark.usefixtures("_hash_backend")
def test_enveloped_answer_keeps_its_date_as_provenance(tmp_path):
    profile = _profile(tmp_path)
    _ingest(profile, "voyage", [(_ts(t), text) for t, text in _VOYAGE_LOG])
    capsule, _tel = _wide_capsule(profile, "voyage", ECHO_QUESTIONS[0], target_tokens=8192)
    line = next(item for item in _items(capsule) if "Inishmore" in item)
    assert "2023-04-20" in line or "20 April, 2023" in line, line
