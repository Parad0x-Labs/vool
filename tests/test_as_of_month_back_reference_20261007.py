"""A later-stated turn that dates itself back onto the asked period is as-of eligible (core/temporal_selection.py).

"Back in March I planted the raspberry canes along the south fence", stated on 12 May, answers "What did I plant
along the south fence in March 2025?": the record's own words date its content to March, so the as-of law must not
read it as a future statement. At ec0f46b6 it reached the reader through the whole-turn lane only (the record
section refused it: state time was the statement day), and the narrowed lane admission refuses it too. A turn
that names a DIFFERENT month, a hedged one, or a negated one ("not back in March") stays out; a later turn that
does not date itself back stays out. Every record and question below was written for this file.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from tests.test_question_date_time_leg_20261002 import _capsule, _hash_backend, _ingest, _profile, _ts  # noqa: F401

QUESTION = "What did I plant along the south fence of the allotment in March 2025?"
STORY = "Back in March I planted the new raspberry canes along the south fence of the allotment, the variety is Glen Ample."
LONG = (STORY + " It took most of a weekend: I dug a trench, mixed in two bags of manure and the leaf mould from last "
        "autumn, and set the canes about forty centimetres apart with the wire supports at each end. My neighbour lent "
        "me his long-handled spade, which made the trench much easier, and we shared a flask of tea while the rain held "
        "off. I watered them in well and mulched with straw, and tied the first labels on with green twine so I would "
        "remember which row was which when they fruit in the summer, the row nearest the shed.")


def _records(capsule: str) -> str:
    """The distilled record section: before the whole-turn lane and before the receipts packet. The packet is the
    compiler's (it does not face the as-of law today: a May turn's typed event shows for a March ask with the
    receipts switch on, at ec0f46b6 as here; logged as a separate gap), the lane is the lane admission's."""
    return capsule.split(cr._TURN_LANE_HEADER, 1)[0].split("Evidence receipts (", 1)[0]


def _serve(tmp_path, story: str):
    profile = _profile(tmp_path)
    _ingest(profile, "allot", [
        ("2025-02-10T09:00:00", "The allotment plot number is fourteen."),
        ("2025-05-12T09:00:00", story),
        ("2025-05-12T09:30:00", "The water butt by the shed is leaking again."),
    ])
    return _capsule(profile, "allot", QUESTION)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("story", [
    STORY,
    LONG,
    "Last March I planted the new raspberry canes along the south fence of the allotment.",
    "In early March I planted the new raspberry canes along the south fence of the allotment.",
    "On March 3rd I planted the new raspberry canes along the south fence of the allotment.",
    "Earlier this year, in March, I planted the new raspberry canes along the south fence of the allotment.",
    "i planted the raspberry canes along the south fence back in march, forgot to say",
], ids=["back_in", "back_in_long", "last_march", "early_march", "march_3rd", "earlier_this_year", "sloppy"])
def test_a_later_turn_dated_back_onto_the_asked_month_reaches_the_record_section(tmp_path, story):
    capsule, _telemetry = _serve(tmp_path, story)
    assert "raspberry" in _records(capsule), capsule


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("story", [
    "Back in April I planted the new raspberry canes along the south fence of the allotment.",     # other month
    "Maybe back in March I planted the raspberry canes along the south fence, I am not sure.",    # hedged
    "It was not back in March that I planted the raspberry canes along the south fence, it was May.",  # negated
    "I planted the new raspberry canes along the south fence of the allotment this morning.",     # no back-reference
], ids=["other_month", "hedged", "negated", "no_back_reference"])
def test_a_later_turn_that_does_not_date_itself_onto_the_month_stays_out(tmp_path, story):
    capsule, _telemetry = _serve(tmp_path, story)
    assert "raspberry" not in _records(capsule), capsule


def test_self_dated_day_reads_month_and_explicit_back_references():
    from core.temporal_selection import self_dated_day

    stated = _ts("2025-05-12T09:00:00")
    assert self_dated_day("Back in March I planted the canes.", stated).isoformat() == "2025-03-01"
    assert self_dated_day("Last March I planted the canes.", stated).isoformat() == "2025-03-01"
    assert self_dated_day("In early March I planted the canes.", stated).isoformat() == "2025-03-01"
    assert self_dated_day("On March 3rd I planted the canes.", stated).isoformat() == "2025-03-03"
    assert self_dated_day("Back in November I planted the canes.", stated).isoformat() == "2024-11-01"
    assert self_dated_day("Yesterday I planted the canes.", stated).isoformat() == "2025-05-11"
    for body in ("Maybe back in March I planted the canes, not sure.",
                 "It was not back in March that I planted them.",
                 "I never planted anything in March.",
                 "I planted the canes this morning.",
                 "From March the water rate rises."):   # a forward declaration dates nothing back
        assert self_dated_day(body, stated) is None, body
