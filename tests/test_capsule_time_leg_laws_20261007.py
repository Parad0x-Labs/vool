"""Leg-level laws for the occurrence time leg (2026-10-07).

Two laws, each asserted at the leg itself (the ``evidence_time_leg``
telemetry and ``relative_reference_day``), not at the rendered capsule, so a
lane that later renders the same record cannot mask or fake them:

1. a hedged back-reference dates nothing: ``relative_reference_day`` yields
   None for "maybe yesterday ..., not sure", so a speculative record is never
   anchored onto the asked day;
2. the as-of law inside the leg: a record stated after the named day whose
   own words do not date it back onto that day is not a leg record, even
   though the leg read it from the day window.

Each test fails on a tree without the law (measured on the ec0f46b6 export).
"""

from __future__ import annotations

import pytest

from tests.test_question_date_time_leg_20261002 import (  # noqa: F401  (fixture)
    _capsule,
    _hash_backend,
    _ingest,
    _profile,
    _ts,
)


def relative_reference_day(body, statement_at):
    from core.temporal_selection import relative_reference_day as fn

    return fn(body, statement_at)


@pytest.mark.parametrize(
    "body",
    [
        "Maybe yesterday the swarm went to the old chestnut, I am not sure.",
        "I think the kiln cracked a shelf three days ago, possibly.",
        "Perhaps the day before yesterday the queen was laying again?",
        "yesterday we might have tied up at the north quay, not certain",
        "Tentative: two days ago the forestay bent.",
        # "could be" / "I guess" sit outside the shared hedge class (the
        # correction law's closed class); widening it is not this law's job
    ],
)
def test_hedged_back_reference_dates_nothing(body: str) -> None:
    assert relative_reference_day(body, _ts("2024-04-12T09:00:00")) is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Yesterday the swarm went to the old chestnut.", "2024-04-11"),
        ("Three days ago the kiln cracked a shelf, no doubt about it.", "2024-04-09"),
        # a month in a date slot is not modality
        ("Yesterday I booked the hall for the show in May.", "2024-04-11"),
    ],
)
def test_plain_back_reference_still_dates(body: str, expected: str) -> None:
    assert relative_reference_day(body, _ts("2024-04-12T09:00:00")).isoformat() == expected


@pytest.mark.usefixtures("_hash_backend")
def test_hedged_next_day_record_is_not_a_leg_anchor(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest(profile, "bee-yard", [
        ("2025-05-02T09:00:00", "The bee yard hive count is twelve colonies."),
        ("2025-06-11T09:00:00", "Maybe yesterday the swarm went to the old chestnut, I am not sure."),
        ("2025-06-11T09:30:00", "Smoker fuel is running low."),
    ])
    _capsule, telemetry = _capsule_(profile, "bee-yard",
                                    "Where did the swarm from the bee yard go on June 10, 2025?")
    leg = telemetry.get("evidence_time_leg")
    assert leg is not None
    assert leg["anchored_records"] == 0, leg
    assert leg["records"] == 0, leg


@pytest.mark.usefixtures("_hash_backend")
def test_plain_next_day_record_is_a_leg_anchor(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest(profile, "bee-yard", [
        ("2025-05-02T09:00:00", "The bee yard hive count is twelve colonies."),
        ("2025-06-11T09:00:00", "Yesterday the swarm went to the old chestnut."),
    ])
    _capsule, telemetry = _capsule_(profile, "bee-yard",
                                    "Where did the swarm from the bee yard go on June 10, 2025?")
    leg = telemetry.get("evidence_time_leg")
    assert leg is not None
    assert leg["anchored_records"] == 1, leg


@pytest.mark.usefixtures("_hash_backend")
def test_window_record_stated_after_the_day_is_not_a_leg_record(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest(profile, "studio", [
        ("2023-02-01T10:00:00", "The studio glaze for the mugs is celadon."),
        ("2023-03-09T10:00:00", "Correction: the studio glaze for the mugs is now tenmoku."),
    ])
    _capsule, telemetry = _capsule_(profile, "studio",
                                    "What was the studio glaze for the mugs on March 8, 2023?")
    leg = telemetry.get("evidence_time_leg")
    assert leg is not None
    assert leg["records"] == 0, leg


@pytest.mark.usefixtures("_hash_backend")
def test_window_record_dated_back_onto_the_day_is_a_leg_record(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest(profile, "studio", [
        ("2023-02-01T10:00:00", "The studio glaze for the mugs is celadon."),
        ("2023-03-09T10:00:00", "Yesterday I switched the studio glaze for the mugs to tenmoku."),
    ])
    _capsule, telemetry = _capsule_(profile, "studio",
                                    "What was the studio glaze for the mugs on March 8, 2023?")
    leg = telemetry.get("evidence_time_leg")
    assert leg is not None
    assert leg["records"] == 1 and leg["anchored_records"] == 1, leg


def _capsule_(profile, chat, question):
    return _capsule(profile, chat, question)
