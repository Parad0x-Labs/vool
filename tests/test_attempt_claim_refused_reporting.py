"""Regression: a refused execution claim must report, then abort — never crash reporting.

H-1/INV-2 makes the RUNNING claim a CAS whose refusal aborts the executor, surfaced as
the typed `AttemptClaimRefused` plus a durable `attempt_claim_refused` event. The
reporting path itself once crashed there: it guarded the event write with
`contextlib.suppress` without importing `contextlib`, so the executor died on a
`NameError` instead of the typed refusal and the event never landed. This file pins the
whole path: typed refusal out, event recorded, nothing secondary escapes, task not run.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_turn_attempt_chain import _Harness


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-claim-refused-{request.node.name[:40]}")
    try:
        yield h
    finally:
        h.close()


def _session_events(session_id: str) -> list[dict]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM runtime_session_events WHERE session_id = ? ORDER BY seq ASC",
            (str(session_id),),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def test_claim_refusal_reports_event_and_aborts_without_secondary_error(harness: _Harness) -> None:
    """The claim CAS loses -> typed refusal + recorded event, executor aborts cleanly."""
    from core.runtime_continuity import (
        AttemptClaimRefused,
        create_runtime_attempt,
        update_runtime_attempt,
    )

    agent = harness.agent
    session_id = harness.session_id
    minted = create_runtime_attempt(
        session_id=session_id,
        original_request="weather in Tallinn",
        origin_user_turn_id=f"{session_id}-turn",
    )
    attempt_id = str(minted["attempt_id"])
    # Force the refusal deterministically through the CAS contract itself: the row goes
    # terminal before the executor claims it, and a terminal row absorbs the PLANNED
    # rewrite (update_runtime_attempt refuses resurrection), so the claim to RUNNING
    # loses with rowcount 0 -- the same typed refusal a second executor winning the live
    # slot produces.
    update_runtime_attempt(attempt_id, lifecycle_state="SUCCEEDED", completed=True)

    executed: list[str] = []
    with pytest.raises(AttemptClaimRefused) as refused:
        agent._record_live_data_plan_subtasks(
            runtime_attempt_id=attempt_id,
            plan_id="pln-claim-refused-regression",
            plan=SimpleNamespace(subtasks=[]),
            client_turn_id=f"{session_id}-turn",
        )
        # Unreachable while the executor aborts: whatever would run the plan's tasks
        # sits behind this call.
        executed.append("plan-executed")

    assert executed == [], "the underlying task executed despite the refused claim"
    assert not isinstance(refused.value, NameError), (
        f"the reporting path escaped as {type(refused.value).__name__}, not the typed refusal"
    )
    assert "claim refused" in str(refused.value)

    refusal_events = [
        event
        for event in _session_events(session_id)
        if event["event_type"] == "attempt_claim_refused"
    ]
    assert refusal_events, (
        f"no attempt_claim_refused event was recorded for session {session_id}"
    )
    import json

    details = json.loads(str(refusal_events[-1]["details_json"] or "{}"))
    assert details.get("attempt_id") == attempt_id
    assert details.get("status") == "claim_refused"
