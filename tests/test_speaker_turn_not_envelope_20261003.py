"""A speaker's dated turn is content, never a record's envelope.

The envelope mask treated every metadata-only line among a record's first lines as envelope, so a
speaker's own short dated statement ("Ines: The recital is 9 June, 2024.") was blanked and the record
refused as envelope-only (independent verifier, 2026-10-03; parent delivered it).

Law: the envelope is only the LEADING run of provenance lines (session/date headers before the first
speaker-labelled or content line). A speaker-labelled turn is never envelope, never masked, and is an
assertion; provenance role labels ("Date:", "Timestamp:", "Session date:") stay envelope.

All names, places and sentences are synthetic.
"""

from __future__ import annotations

import pytest

import core.context_retrieval as cr
from tests.test_question_date_time_leg_20261002 import (
    _hash_backend,
    _ingest,
    _profile,
)
from tests.test_time_leg_follows_allowance_20261003 import _ts, _wide_capsule

# ───────────────────────── the envelope is the leading provenance run ─────

_ENV = "Session date: 1:00 pm on 3 May, 2024"


@pytest.mark.parametrize("turn", [
    "Ines: The recital is 9 June, 2024.",
    "Ines: My birthday is 12 July 2024.",
    "Ines: 9 June, 2024.",
    "Bram Okafor: The audit is on 2024-06-09.",
    "Ines: Moved in on 4 March 2024 at 10:00.",
])
def test_speaker_dated_turn_is_never_envelope(turn):
    body = f"{_ENV}\n{turn}"
    assert cr._is_speaker_turn_line(turn)
    assert not cr._is_envelope_line(turn)
    assert cr._envelope_line_ranges(body) == [(0, len(_ENV))]
    masked = cr._envelope_masked(body)
    assert masked[len(_ENV) + 1:] == turn
    assert not masked[:len(_ENV)].strip()
    assert cr._assertion_sentences(turn), turn
    # the delivered-window envelope view keeps the turn, by offsets and by text
    assert cr._envelope_free_slice(body, 0, len(body), body).strip() == turn
    assert cr._envelope_free_slice("", 0, 0, body).strip() == turn


@pytest.mark.parametrize("label_line", [
    "Date: 2024-05-03",
    "Timestamp: 2024-05-03 09:30",
    "Session date: 2024/05/03",
    "Logged on 2024-03-01 09:30",
    "Please remember: Session date: 3 May 2024",
])
def test_provenance_role_labels_stay_envelope(label_line):
    assert cr._is_envelope_line(label_line)
    assert not cr._assertion_sentences(label_line)
    body = f"{label_line}\nIdris: the kiln is fixed."
    assert cr._envelope_line_ranges(body) == [(0, len(label_line))]


def test_envelope_is_only_the_leading_run():
    # two stacked headers are both envelope
    body = "Session date: 3 May 2024\nLogged on 2024-05-03 09:30\nIdris: the kiln is fixed."
    ranges = cr._envelope_line_ranges(body)
    assert len(ranges) == 2
    assert cr._envelope_masked(body).strip() == "Idris: the kiln is fixed."
    # a dated line AFTER the first content line is never envelope
    late = "Idris: the kiln is fixed.\n2024-05-03"
    assert cr._envelope_line_ranges(late) == []
    assert cr._envelope_masked(late) == late
    # a speaker turn first: nothing is envelope, even a header-shaped line after it
    first = "Ines: The recital is 9 June, 2024.\nSession date: 3 May 2024"
    assert cr._envelope_line_ranges(first) == []


_LOG = [
    ("2024-05-01T09:00:00", "Session date: 9:00 am on 1 May, 2024\nWren: Ines, when is the recital going to be?"),
    ("2024-05-03T13:00:00", "Session date: 1:00 pm on 3 May, 2024\nInes: The recital is 9 June, 2024."),
    ("2024-05-04T13:00:00", "Session date: 1:00 pm on 4 May, 2024\nInes: My birthday is 12 July 2024."),
    ("2024-05-05T09:00:00", "Session date: 9:00 am on 5 May, 2024\nWren: Picked up new reeds at the music shop."),
]


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question,answer", [
    ("When is Ines's recital?", "9 June, 2024"),
    ("When is Ines's recital? Answer with the session date.", "9 June, 2024"),
    ("when is the recital ines mentioned", "9 June, 2024"),
    ("When is Ines's birthday?", "12 July 2024"),
    ("What date is Ines's birthday, going by the chat date?", "12 July 2024"),
])
def test_speaker_dated_answer_line_is_delivered(tmp_path, question, answer):
    profile = _profile(tmp_path)
    _ingest(profile, "home", [(_ts(t), text) for t, text in _LOG])
    capsule, _tel = _wide_capsule(profile, "home", question, target_tokens=8192)
    assert answer in capsule, capsule
