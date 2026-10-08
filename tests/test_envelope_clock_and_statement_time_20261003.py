"""An envelope's clock time is part of its date, and its full date is a statement time.

"Session date: 10:49 am on 29 October, 2023" kept the clock in the date's role qualifier: the
innermost-label search split the clock at its own colon, so the provenance read "(stated: 10: 49 am
on 29 October, 2023; ...)", and the envelope time parser accepted only ISO text, so it could never
read "29 October, 2023" (fork-v5 wiring audit). Laws: a clock and its connective ("at", "on", a
comma) before the date are removed before the role is read; the envelope time parser reads ISO,
day-first and month-first full dates and refuses month-grain, impossible and non-session dates.

All lines are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr

UTC = timezone.utc


# ───────────────────────── envelope clock and envelope time ───────────────

@pytest.mark.parametrize("line,role,date", [
    ("Session date: 10:49 am on 29 October, 2023", "Session date:", "29 October, 2023"),
    ("Session date: 1:24 pm on 17 September, 2023", "Session date:", "17 September, 2023"),
    ("Session date: 9:05 PM on March 3, 2024", "Session date:", "March 3, 2024"),
    ("session date: 12:35 am on 14 august 2023", "session date:", "14 august 2023"),
    ("Session date: 07:30 a.m., 2 June 2022", "Session date:", "2 June 2022"),
    ("Logged at 10:30 on 2024-03-01", "Logged", "2024-03-01"),
    # controls: no clock - role unchanged
    ("Session date: 2025/05/03 (Sat) 09:00", "Session date:", "2025/05/03"),
    ("Warranty expires on 2026-07-08", "Warranty expires on", "2026-07-08"),
    ("Please remember: Session date: 2025-06-10", "Session date:", "2025-06-10"),
])
def test_clock_before_date_is_not_a_role_word(line, role, date):
    assert cr._source_date_expressions(line + "\nbody") == [(role, date)]


@pytest.mark.parametrize("body,iso", [
    ("Session date: 10:49 am on 29 October, 2023\nCalvo: hi", "2023-10-29"),
    ("Session date: 9:05 PM on March 3, 2024\nx", "2024-03-03"),
    ("Session date: 3rd June 2021\nx", "2021-06-03"),
    ("Session date: 2023/05/21\nx", "2023-05-21"),
    ("Session date: 2023-05-21 08:00\nx", "2023-05-21"),
    ("Session date: Sept 9, 2022\nx", "2022-09-09"),
])
def test_envelope_time_reads_full_dates(body, iso):
    stamp = cr._declared_envelope_time(body)
    assert stamp is not None, body
    assert datetime.fromtimestamp(stamp, tz=UTC).date().isoformat() == iso


@pytest.mark.parametrize("body", [
    "Session date: October 2023\nx",           # month grain names no day
    "Session date: 31 February, 2023\nx",       # impossible day
    "Warranty expires on 29 October, 2023\nx",  # another event's date, not the session's
    "We sailed on 29 October, 2023 to the islands.",  # body date, no envelope
])
def test_envelope_time_refuses_non_statement_dates(body):
    assert cr._declared_envelope_time(body) is None


def test_clock_suffix_no_longer_reaches_the_provenance():
    body = "Session date: 10:49 am on 29 October, 2023\nCalvo: The new mast went up today."
    suffix = cr._provenance_suffix(cr._source_date_expressions(body), "The new mast went up today.", "2023-10-29",
                                   time_kind="stated")
    assert "10: 49" not in suffix and "am on" not in suffix, suffix
    assert "Session date: 29 October, 2023" in suffix, suffix
