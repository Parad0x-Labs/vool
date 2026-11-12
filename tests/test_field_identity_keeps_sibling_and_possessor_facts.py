"""A later statement about one field never retires another field (core/temporal_selection.py).

Memory review 2026-10-05 (D1/E1), still true on the v0.8 line at 247124ed (probe, 2026-11-12):
slot identity is a bag of words, so a later statement whose words are contained in an earlier one
replaced it even when it named another field.

* Possessor: "I changed my wifi password." retired "My sister's wifi password is ...": the
  sister's record was marked superseded and its distilled line lost its value.
* Sibling field: "My gate code is 2222." and "I changed my gate code." retired "My spare gate code
  is 1111.".

Law (see _names_another_field):

* a statement about the speaker's own field ("my wifi password") and one about the same field of
  another possessor ("my sister's wifi password") are two fields, unless the later one names that
  possessor;
* a CONTRASTIVE modifier - a closed class whose meaning presupposes a sibling of the same head
  ("spare", "backup", "guest", "work", "second") - makes a narrower field that a statement without
  it never speaks for. Any other modifier is description: a first mention is often fuller than the
  next ("We booked the island ferry for noon." / "Cancel that, the ferry is full." is one ferry),
  so those keep superseding and withdrawing as before;
* version words ("new", "current", "old") never make another field.

The law applies to value supersession, to value-less corrections ("I changed my gate code") and to
retractions alike, and it never makes a record ineligible: it only keeps one current.

All sentences are invented for this contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.temporal_selection import AsOfIntent, _names_another_field, apply_temporal_selection
from tests.test_question_date_time_leg_20261002 import _hash_backend  # fixture

UTC = timezone.utc
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _row(key: str, body: str, day: int) -> dict:
    return {"key": key, "body": body, "role": "user", "statement_at": _ts(f"2026-10-{day:02d}T09:00:00"),
            "speaker": "", "seq": None}


def _verdicts(rows: list[dict], question: str, *, past_only: bool = False):
    return apply_temporal_selection(rows, intent=AsOfIntent(), past_only=past_only, question=question,
                                    now_utc=NOW)


@pytest.mark.parametrize("past_only", [True, False])
@pytest.mark.parametrize("later", [
    "My gate code is 2222.",
    "I changed my gate code.",
    "Correction: my gate code is 3333.",
    "Never mind my gate code, I moved out.",
])
def test_the_plain_field_never_retires_the_spare_one(later, past_only):
    verdicts = _verdicts([_row("spare", "My spare gate code is 1111.", 1), _row("later", later, 3)],
                         "What is my spare gate code?", past_only=past_only)
    assert verdicts["spare"].eligible, (later, verdicts["spare"])


@pytest.mark.parametrize("past_only", [True, False])
@pytest.mark.parametrize("later", [
    "I changed my wifi password.",
    "My wifi password is fig-12 now.",
    "Scratch that wifi password, it was wrong.",
])
def test_the_speakers_own_field_never_retires_another_possessors(later, past_only):
    verdicts = _verdicts([_row("sister", "My sister's wifi password is plum-77.", 1), _row("later", later, 3)],
                         "What is my sister's wifi password?", past_only=past_only)
    assert verdicts["sister"].eligible, (later, verdicts["sister"])


def test_another_possessors_field_never_retires_the_speakers_own():
    verdicts = _verdicts([_row("mine", "My locker number is 12.", 1),
                          _row("sister", "My sister's locker number is 41.", 3)],
                         "What is my locker number?")
    assert verdicts["mine"].eligible, verdicts["mine"]
    assert verdicts["sister"].eligible, verdicts["sister"]


@pytest.mark.parametrize("later, field", [
    ("I changed my spare gate code.", "spare"),
    ("My spare gate code is 4444 now.", "spare"),
    ("Never mind the spare gate code.", "spare"),
    ("I changed my sister's wifi password for her.", "sister"),
])
def test_a_statement_that_names_the_same_field_still_retires_it(later, field):
    rows = [_row("spare", "My spare gate code is 1111.", 1),
            _row("sister", "My sister's wifi password is plum-77.", 1),
            _row("later", later, 3)]
    verdicts = _verdicts(rows, "What is my spare gate code and my sister's wifi password?")
    assert not verdicts[field].eligible, (later, verdicts[field])


def test_a_retraction_of_the_spare_field_keeps_the_plain_one():
    rows = [_row("main", "My gate code is 2222.", 1), _row("spare", "My spare gate code is 1111.", 2),
            _row("later", "Never mind the spare gate code, the spare remote broke.", 3)]
    verdicts = _verdicts(rows, "What are my gate codes?")
    assert not verdicts["spare"].eligible, verdicts["spare"]
    assert verdicts["main"].eligible, verdicts["main"]


@pytest.mark.parametrize("earlier, later", [
    # version words say which version of ONE field
    ("My gate code is 1111.", "My new gate code is 2222."),
    ("My gate code is 1111.", "My current gate code is 2222."),
    # a descriptive modifier is not identity: the later, shorter mention is the same thing
    ("The island ferry leaves at 9:15.", "The ferry leaves at 9:40 now."),
    ("My blue padlock code is 7462.", "My padlock code is 1190 now."),
    # the same possessor named on both sides
    ("My sister's wifi password is plum-77.", "My sister's wifi password is fig-12 now."),
])
def test_one_field_still_supersedes_as_before(earlier, later):
    assert not _names_another_field(earlier, later), (earlier, later)
    verdicts = _verdicts([_row("old", earlier, 1), _row("new", later, 3)], "What is it now?")
    assert not verdicts["old"].eligible, (earlier, later, verdicts["old"])
    assert verdicts["new"].eligible, (earlier, later, verdicts["new"])


# ── end to end: the distilled lane of an ordinary chat capsule ───────────────────────────────

def _capsule(tmp_path, turns: list[tuple[str, str]], question: str) -> str:
    from tests.test_question_date_time_leg_20261002 import _capsule as build
    from tests.test_question_date_time_leg_20261002 import _ingest, _profile

    profile = _profile(tmp_path)
    _ingest(profile, "fieldchat", [(_ts(at), text) for at, text in turns])
    capsule, _telemetry = build(profile, "fieldchat", question)
    return capsule


def _distilled(capsule: str) -> str:
    """The distilled lane: the current facts, before the whole-turn evidence lane."""
    return capsule.split("Evidence turns", 1)[0]


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("turns, question, kept", [
    ([("2026-10-01T09:00:00", "My spare gate code is 1111."),
      ("2026-10-02T09:00:00", "My gate code is 2222."),
      ("2026-10-03T09:00:00", "I changed my gate code yesterday.")],
     "What is my spare gate code?", "My spare gate code is 1111."),
    ([("2026-10-01T09:00:00", "My sister's locker number is 41."),
      ("2026-10-03T09:00:00", "I changed my locker number.")],
     "What is my sister's locker number?", "My sister's locker number is 41."),
    ([("2026-10-01T09:00:00", "My work desk number is 318."),
      ("2026-10-03T09:00:00", "My desk number is 207 now.")],
     "What is my work desk number?", "My work desk number is 318."),
])
def test_the_other_field_stays_a_current_fact_in_the_capsule(tmp_path, turns, question, kept):
    capsule = _capsule(tmp_path, turns, question)
    assert kept in _distilled(capsule), capsule


@pytest.mark.usefixtures("_hash_backend")
def test_the_same_field_still_leaves_the_current_facts(tmp_path):
    capsule = _capsule(tmp_path, [("2026-10-01T09:00:00", "My locker number is 12."),
                                  ("2026-10-03T09:00:00", "I changed my locker number to 19.")],
                       "What is my locker number?")
    assert "My locker number is 12." not in _distilled(capsule), capsule
    assert "19" in _distilled(capsule), capsule
