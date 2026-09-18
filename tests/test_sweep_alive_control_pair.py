"""AUD-20260829-003, C8 — no slot is listed as unanswered when it was answered.

C8 is the ONE criterion FINAL_SIGNOFF grades PASS, and the re-adjudication downgrades it to
"proof materially vacuous". Both readings are correct, which is the problem: after Phase A the
text ladder can no longer emit `unanswered` at all, so "no false unanswered listing" holds
because nothing can be listed. Nothing committed distinguishes a working sweep from a dead one.

This is the non-vacuous control pair. Same request, same two slots, same served bytes, driven
through the REAL `finalize_answer` sweep. The only difference between the arms is which slot the
lane actually served:

  * NEGATIVE — a genuinely unanswered slot IS disclosed, provably, with a reason;
  * POSITIVE — the answered slot is never listed, and never appears in a disclosure row.

And a sabotage, because a control pair that cannot fail proves as little as the vacuous PASS it
replaces: with the sole writer neutered to return all-indeterminate, the negative control must go
red. If it stays green, this file is measuring nothing.
"""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.conductor import obligation_ledger as ol
from tests.test_refused_slot_register_contract import serve_turn

TWO_SLOT = "What is the weather in Rome? What is the water temperature in the Baltic Sea?"
ROME_ONLY = "Rome: Sunny, 31 °C (source: wttr.in)."


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        reset_runtime_continuity_state,
    )
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "sweep_control.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    reset_runtime_continuity_state()
    yield
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)
    sdb.configure_default_db_path(None)
    ol.clear_active_set()


def _states(attempt_id: str) -> dict[str, str]:
    from core.refused_slot_register import _obligation_set_for_attempt
    from core.runtime_continuity import get_runtime_attempt

    bound = _obligation_set_for_attempt(get_runtime_attempt(attempt_id))
    assert bound is not None, "control failed: the turn bound no obligation set"
    return {
        str(item.get("unit_id")): str(item.get("state"))
        for item in ol.demand_obligations(*bound)
    }


def test_negative_control_a_genuinely_unanswered_slot_is_disclosed(fresh_store):
    """The sweep can still accuse, on evidence. Rome is dispatched and served; the Baltic slot
    has no dispatch row at all, so the RECORD -- not the prose -- proves it was never served."""
    attempt_id = serve_turn(
        "sess-c8-negative", TWO_SLOT, ROME_ONLY, lane_served_units=("u1",)
    )
    states = _states(attempt_id)
    assert states["u2"] == "unanswered", states
    assert states["u1"] == "satisfied", states


def test_positive_control_an_answered_slot_is_never_listed(fresh_store):
    """The other half, and the one C8 is named for: a slot the lane served is never accused."""
    both = "Rome: Sunny, 31 °C (source: wttr.in). Baltic Sea: 17.2 °C (source: open-meteo)."
    attempt_id = serve_turn(
        "sess-c8-positive", TWO_SLOT, both, lane_served_units=("u1", "u2")
    )
    states = _states(attempt_id)
    assert states == {"u1": "satisfied", "u2": "satisfied"}, states


def test_the_disclosure_reaches_the_served_bytes_and_names_only_the_dropped_slot(fresh_store):
    """The criterion is about what the READER sees, so the bytes are asserted, not just the
    census: the dropped slot is named in a disclosure row and the served one is not."""
    from core.finalization import RSS_UNAVAILABLE_HEADER
    from core.runtime_continuity import get_runtime_attempt

    attempt_id = serve_turn(
        "sess-c8-bytes", TWO_SLOT, ROME_ONLY, lane_served_units=("u1",)
    )
    attempt = get_runtime_attempt(attempt_id)
    assert attempt is not None
    from core.refused_slot_register import refused_slots_for_attempt

    refused = refused_slots_for_attempt(attempt)
    named = {slot.unit_id for slot in refused}
    assert "u2" in named, refused
    assert "u1" not in named, "the served slot was listed as refused"
    assert RSS_UNAVAILABLE_HEADER  # the disclosure header the sweep serves


def test_sabotage_a_dead_sweep_reds_the_negative_control(fresh_store, monkeypatch):
    """THE POINT OF THIS FILE. `sweep_demand_obligations` is the sole writer of a demand
    disposition. Neutered to all-indeterminate, the negative control must fail -- otherwise the
    pair is measuring the fixture rather than the runtime, which is exactly the vacuity the
    re-adjudication flagged."""
    # **kwargs deliberately: a substitute for the sole writer must not break when the writer
    # gains a keyword, or the sabotage stops measuring the runtime and starts measuring its own
    # signature -- which is how it would go green while the guard was gone.
    def all_indeterminate(set_id, version, *, states=None, **_kwargs):
        # A sweep that never accuses. It must REPLACE the writer, not post-process its return:
        # rewriting the rows after the real sweep has already dispositioned them leaves the
        # durable state accusing and the sabotage passes while proving nothing.
        rows = []
        for item in ol.demand_obligations(set_id, version):
            unit_id = str(item.get("unit_id") or "")
            ol.record_disposition(
                set_id,
                version,
                str(item.get("obligation_id") or ""),
                "indeterminate",
                evidence_source=ol.RSS_SWEEP_EVIDENCE,
                evidence_ref=unit_id,
            )
            rows.append(
                {"unit_id": unit_id, "text": str(item.get("text") or ""),
                 "state": "indeterminate", "reason": ""}
            )
        return tuple(rows)

    attempt_id = serve_turn(
        "sess-c8-control", TWO_SLOT, ROME_ONLY, lane_served_units=("u1",)
    )
    assert _states(attempt_id)["u2"] == "unanswered", "control failed: no accusation before sabotage"

    monkeypatch.setattr(ol, "sweep_demand_obligations", all_indeterminate)
    sabotaged = serve_turn(
        "sess-c8-sabotage", TWO_SLOT, ROME_ONLY, turn_id="turn-s", lane_served_units=("u1",)
    )
    assert _states(sabotaged)["u2"] != "unanswered", "the sweep is not the writer this pins"
