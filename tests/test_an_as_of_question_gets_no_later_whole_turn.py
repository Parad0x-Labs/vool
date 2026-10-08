"""An as-of question never receives a turn whose content became true after its as-of day, on any carrier.

The whole-turn lane ("Evidence turns") ranks every turn a search leg returns. It handed a later record back whole
("And to 38 marks from October." for "as of mid August, what did the day pass cost?") after the capsule's temporal
selection had refused it as future-relative-to-as-of. The lane now applies the same as-of law
(core.temporal_selection.stated_after_as_of). A later-stated turn whose own words date it back onto the asked
period still rides. All names, places and amounts are synthetic.
"""
from __future__ import annotations

import pytest

from tests.test_anchor_carrier_stored_record_20261003 import _as_of_capsule
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile
from tests.test_time_leg_follows_allowance_20261003 import _ts

_RENT = [
    ("2026-02-03T10:00:00", "Session date: 10:00 am on 3 February, 2026\nOrrin: The allotment rent at Wexcombe is 41 crowns a season."),
    ("2026-11-06T10:00:00", "Session date: 10:00 am on 6 November, 2026\nOrrin: Wexcombe allotment rent goes up to 47 crowns from November."),
]


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "As of early September 2026, what was the allotment rent at Wexcombe?",
    "as of september 2026 how much was the wexcombe allotment rent",
])
def test_a_later_turn_never_reaches_an_as_of_question(tmp_path, question):
    profile = _profile(tmp_path)
    _ingest(profile, "allotment", [(_ts(t), text) for t, text in _RENT])
    capsule = _as_of_capsule(profile, "allotment", question, "2026-09-05T00:00:00")
    assert "41 crowns" in capsule, capsule
    assert "47 crowns" not in capsule, capsule


@pytest.mark.usefixtures("_hash_backend")
def test_the_same_turns_without_an_as_of_date_still_ride(tmp_path):
    from tests.test_anchor_carrier_stored_record_20261003 import cr

    profile = _profile(tmp_path)
    _ingest(profile, "allotment-now", [(_ts(t), text) for t, text in _RENT])
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    question = "What is the allotment rent at Wexcombe?"
    ensure_chat_namespace("allotment-now", grant_current_receipts=False)
    messages = cr.inject_retrieved(
        "allotment-now", question, [{"role": "user", "content": question}],
        access_policy=resolve_memory_access_policy(chat_id="allotment-now"),
        source_context={"chat_id": "allotment-now", "runtime_home": str(profile)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    capsule = "\n".join(str(m.get("content") or "") for m in messages if "<retrieved_context>" in str(m.get("content") or ""))
    assert "47 crowns" in capsule, capsule


def test_a_later_turn_that_dates_itself_back_is_not_after_the_as_of_day():
    from datetime import datetime, timezone

    from core.temporal_selection import TemporalCandidate, stated_after_as_of

    as_of_end = datetime(2026, 9, 5, 23, 59, 59, tzinfo=timezone.utc)
    november = datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp()
    retold = TemporalCandidate(key="r", body="Back in August the rent was 41 crowns.", statement_at=november)
    later = TemporalCandidate(key="l", body="The rent goes up to 47 crowns from November.", statement_at=november)
    undated = TemporalCandidate(key="u", body="The rent is 41 crowns.")
    assert not stated_after_as_of(retold, as_of_end)
    assert stated_after_as_of(later, as_of_end)
    assert not stated_after_as_of(undated, as_of_end)


def _turn(statement_day, body):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    stated = datetime.fromisoformat(statement_day).replace(tzinfo=timezone.utc).timestamp()
    return SimpleNamespace(occurrence_id=body[:8], body=body, role="user", statement_at=stated, event_at=None,
                           recorded_at=stated)


@pytest.mark.parametrize("question", [
    "What am I baking for the Wexcombe open day on December 12?",
    "What did the Wexcombe allotment rent go up to in 2026?",
])
def test_a_date_the_question_only_names_is_not_an_as_of_day(question):
    # A named date is what the question asks about. Read as an as-of day, "December 12" resolved to the most recent
    # past 12 December and refused every turn stated after it, the answer included.
    from tests.test_anchor_carrier_stored_record_20261003 import cr

    units = [[_turn("2026-02-03", "I am baking Lisbon tarts for the open day.")],
             [_turn("2026-11-06", "Wexcombe allotment rent goes up to 47 crowns from November.")]]
    assert cr._whole_turn_units_known_by_as_of(units, question, None) == units


def test_the_same_turns_are_filtered_for_an_as_of_question():
    from tests.test_anchor_carrier_stored_record_20261003 import cr

    early = [_turn("2026-02-03", "The allotment rent at Wexcombe is 41 crowns a season.")]
    late = [_turn("2026-11-06", "Wexcombe allotment rent goes up to 47 crowns from November.")]
    assert cr._whole_turn_units_known_by_as_of([early, late], "As of 5 September 2026, what was the rent?", None) == [early]
    assert cr._whole_turn_units_known_by_as_of([early, late], "What was the rent?", "2026-09-05") == [early]


def test_an_unreadable_statement_time_keeps_the_turn():
    from types import SimpleNamespace

    from tests.test_anchor_carrier_stored_record_20261003 import cr

    odd = SimpleNamespace(occurrence_id="o", body="The rent is 41 crowns.", role="user",
                          statement_at="last Tuesday-ish", event_at=None, recorded_at=object())
    units = [[odd]]
    assert cr._whole_turn_units_known_by_as_of(units, "As of September 2026, what was the rent?", None) == units


@pytest.mark.usefixtures("_hash_backend")
def test_a_turn_stated_before_the_day_that_declares_a_later_payment_still_rides(tmp_path):
    # The contract reads "I'll pay ... on 15 June" as future to a 1 June as-of day, but the turn was stated before
    # that day: the lane law keeps a future-declared turn, and only a turn stated after the day is refused.
    profile = _profile(tmp_path)
    _ingest(profile, "boat", [(_ts(t), text) for t, text in [
        ("2026-05-02T10:00:00", "Session date: 2 May, 2026\nOrrin: I spent 120 marks on new sails for the boat."),
        ("2026-05-20T10:00:00", "Session date: 20 May, 2026\nOrrin: I'll pay the 300 marks mooring fee for the boat on 15 June."),
        ("2026-06-20T10:00:00", "Session date: 20 June, 2026\nOrrin: I spent 80 marks on a new boat cover."),
    ]])
    capsule = _as_of_capsule(profile, "boat", "As of 1 June 2026, how much had I spent on the boat?", "2026-06-01T00:00:00")
    assert "120 marks" in capsule, capsule
    assert "300 marks" in capsule, capsule
    assert "80 marks" not in capsule, capsule
