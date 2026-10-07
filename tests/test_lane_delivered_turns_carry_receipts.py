"""Every turn the whole-turn lane renders carries a delivered evidence receipt.

The lane has its own budget (the free window), so it can deliver a turn while the final pack keeps no distilled line.
The receipts recorded only the distilled lines, so the reader got "The ceramics society meets at Alder Hall ..." and
every receipt said nothing was delivered: an answer from the lane traced to no evidence. A lane-rendered turn now
gets its own row (delivered, lane "whole_turn", the exact lane line), and a turn the lane did not render gets none.
"""
from __future__ import annotations

from dataclasses import replace

from core import context_retrieval as cr
from tests.test_complete_source_evidence_units import fresh_profile  # noqa: F401
from tests.test_recall_evidence_merge_law_20260929 import _capsule, _live

FACT = "The ceramics society meets at Alder Hall on Wednesdays. The organiser is Imani and the fee is 32 euros."


def _drop_final_pack(monkeypatch):
    real_pack = cr.pack_context

    def drop_final_pack(*args, **kwargs):
        packed = real_pack(*args, **kwargs)
        return replace(packed, blocks=(), render_block="", tokens_used=0, chosen=0,
                       dropped_budget=packed.dropped_budget + packed.chosen)

    monkeypatch.setattr(cr, "pack_context", drop_final_pack)


def _lane_rows(telemetry):
    return [r for r in telemetry.get("evidence_refs") or [] if r.get("lane") == "whole_turn"]


def test_a_turn_only_the_lane_delivered_has_a_delivered_receipt(fresh_profile, monkeypatch):
    _live(fresh_profile, "final-pack", [("user", FACT)])
    _drop_final_pack(monkeypatch)
    capsule = _capsule(fresh_profile, "final-pack", "Where does the ceramics society meet?")
    lane = capsule.split(cr._TURN_LANE_HEADER, 1)[1] if cr._TURN_LANE_HEADER in capsule else ""
    assert "Alder Hall" in lane, capsule
    telemetry = cr.get_last_retrieval_telemetry()
    rows = _lane_rows(telemetry)
    assert len(rows) == 1, telemetry.get("evidence_refs")
    row = rows[0]
    assert row["delivered"] is True and row["role"] == "user" and row["occurrence_id"]
    assert row["line"] in lane.splitlines(), (row["line"], lane)
    admitted = cr.admitted_evidence_records(telemetry)
    assert [r.line for r in admitted] == [row["line"]]
    assert telemetry["whole_turn_lines"] == len(rows)


def test_a_turn_the_lane_did_not_render_has_no_lane_receipt(fresh_profile):
    # Without the pack override the distilled section carries the fact, so the lane does not repeat it.
    _live(fresh_profile, "final-pack", [("user", FACT)])
    capsule = _capsule(fresh_profile, "final-pack", "Where does the ceramics society meet?")
    telemetry = cr.get_last_retrieval_telemetry()
    lane = capsule.split(cr._TURN_LANE_HEADER, 1)[1] if cr._TURN_LANE_HEADER in capsule else ""
    rows = _lane_rows(telemetry)
    assert len(rows) == telemetry.get("whole_turn_lines", 0) == sum(1 for l in lane.splitlines() if l.startswith("- "))
    assert all(r["line"] in lane.splitlines() for r in rows)
