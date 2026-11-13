"""A mission slot keeps the exact constraint, never a secret said in the same line.

The slot table stores the source line next to each slot. Before this, a line such as "keep the cap at
0.037 SOL, my card PIN is 4821" was kept word for word, PIN included, while every memory store refused it.
"""
from __future__ import annotations

import uuid

import pytest

from core.active_mission import (
    ActiveMissionSlot,
    capture_active_mission_slots,
    current_active_mission_slots,
    upsert_active_mission_slots,
)

pytestmark = [pytest.mark.pa_beta]


def _sid():
    return f"openclaw:secret-slot:{uuid.uuid4().hex}"


def _stored_rows(sid):
    from storage.db import get_connection

    conn = get_connection()
    try:
        return [tuple(row) for row in conn.execute(
            "SELECT slot_name, slot_value_raw, source_text FROM active_mission_slots WHERE session_id = ?", (sid,)
        ).fetchall()]
    finally:
        conn.close()


def test_the_source_line_is_kept_masked_and_the_constraint_exact():
    sid = _sid()
    said = "Active mission: keep launch cap exactly 0.037 SOL, and my card PIN is 4821."
    assert capture_active_mission_slots(sid, said) >= 1
    slots = current_active_mission_slots(sid)
    assert {s["slot_name"]: s["value"] for s in slots}.get("spend_cap") == "0.037 SOL", slots
    rows = _stored_rows(sid)
    assert rows and not any("4821" in repr(row) for row in rows), rows


def test_a_slot_whose_value_is_the_secret_is_not_kept():
    sid = _sid()
    written = upsert_active_mission_slots(
        sid,
        [ActiveMissionSlot("wallet_prefix", "4821"), ActiveMissionSlot("target_os", "Windows")],
        source_text="My card PIN is 4821, target OS is Windows.",
    )
    slots = current_active_mission_slots(sid)
    assert written == 1, slots
    assert [(s["slot_name"], s["value"]) for s in slots] == [("target_os", "Windows")], slots
    assert "4821" not in repr(_stored_rows(sid))
