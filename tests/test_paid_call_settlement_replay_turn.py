"""Finding #8 on the real owner-pick turn: one paid response is charged once.

The router settles a priced response, then writes its receipt. Before this fix a receipt failure
after the settlement had committed sent the same response down the supplemental-charge path, so
the ledger held the call twice. These tests drive the real routing path with a recorded provider.

NO REAL CALL IS MADE ANYWHERE HERE: the key is a dummy string and ``requests.post`` inside the
adapter is a recorder.
"""
from __future__ import annotations

from tests.test_owner_pick_paid_reservation import (  # fixtures and the turn driver
    _isolated_runtime,
    _run_turn,
    _set_policy,
    anthropic_lane,
    posts,
)


def _rows() -> list[dict]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT model_call_id, actual_usd, reserved_usd, status FROM model_spend_reservations"
            ).fetchall()
        ]
    finally:
        conn.close()


def _owner_turn(anthropic_lane, posts):
    _set_policy(mode="off", daily_cap=25)
    return _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id},
        allow_paid_fallback=False,
        posts=posts,
    )


def test_a_paid_turn_whose_receipt_fails_is_charged_once(anthropic_lane, posts, monkeypatch) -> None:
    from core import paid_call_reservation

    real_receipt = paid_call_reservation._emit_answer_spend_receipt

    def _settled_receipt_fails(event_type, *args, **kwargs):
        if event_type == "paid_call.settled":
            raise RuntimeError("receipt store unavailable")
        return real_receipt(event_type, *args, **kwargs)

    monkeypatch.setattr(paid_call_reservation, "_emit_answer_spend_receipt", _settled_receipt_fails)
    decision = _owner_turn(anthropic_lane, posts)

    assert decision.used_model and posts.posted_to_anthropic
    assert len(posts.urls) == 1, "one physical provider call"
    rows = _rows()
    assert [row["status"] for row in rows] == ["settled"], rows
    assert rows[0]["actual_usd"] > 0
    assert rows[0]["reserved_usd"] == 0

