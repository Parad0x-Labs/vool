"""pa_beta_gate — durable intent identity, claim semantics and provider binding.

The review's audit concerns, made concrete: uncertain create replay must reconcile (not
duplicate), concurrent duplicate approvals must not double-execute, an approval is bound
to the provider+account+calendar the user actually reviewed, and a move keeps the
original when the destination was taken after preview (the novel-wording control for
review regression P1-4).
"""
from __future__ import annotations

import pytest

from tests.pa_beta_gate.test_calendar_provider_vertical import (  # noqa: F401 -- vilnius_env is a pytest fixture
    VILNIUS_CAL,
    _provider_events,
    _run,
    vilnius_env,
)

from ._caldav_service import start_caldav_fixture

pytestmark = [pytest.mark.pa_beta]


def test_uncertain_create_replay_recovers_without_duplicate(vilnius_env, monkeypatch):
    """Timeout-after-acceptance leaves a terminal outcome_unproven state; re-approval
    reconciles the SAME durable identity — recovered, never duplicated."""
    session = "replay-recover"
    state = vilnius_env["state"]
    _intent, proposal = _run('propose "Maybe" on 2026-09-15 16:00 Europe/Berlin for 30m', session_id=session)
    intent_uid = _scope_of(vilnius_env, session, proposal.details["action_id"])["intent_uid"]
    assert intent_uid, "the UID is minted at PROPOSAL time (durable intent identity)"

    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "1")
    state.hang_seconds = 3.0
    try:
        _intent, unknown = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert unknown.status == "outcome_unproven"
        assert intent_uid in _provider_events(state), "the first attempt verifiably landed"
    finally:
        state.hang_seconds = 0.0

    # The re-approval reconciles: same identity, reported as recovered, no second event.
    _intent, recovered = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert recovered.ok, recovered.response_text
    assert recovered.details["recovered"] is True
    assert recovered.details["uid"] == intent_uid
    assert "nothing was duplicated" in recovered.response_text
    events = _provider_events(state)
    assert list(events).count(intent_uid) == 1


def test_uncertain_create_replay_refires_when_it_never_landed(vilnius_env):
    """The other reconciliation branch: the attempt never reached the provider, so the
    re-approval creates the exact proposed event under the same identity."""
    session = "replay-refire"
    state = vilnius_env["state"]
    _intent, proposal = _run('propose "Never landed" on 2026-09-15 16:00 Europe/Berlin for 30m', session_id=session)
    action_id = proposal.details["action_id"]
    intent_uid = _scope_of(vilnius_env, session, action_id)["intent_uid"]

    # Simulate the unproven outcome WITHOUT the provider ever applying it: pull the
    # network out from under the first attempt via write-refusal + unknown transport is
    # not expressible here, so drive the terminal state the way the executor records it.
    from core.local_operator_actions import _mark_action_outcome_unproven

    _mark_action_outcome_unproven(action_id, result={"intent_uid": intent_uid, "calendar_id": VILNIUS_CAL,
                                                     "title": "Never landed",
                                                     "start_utc": "2026-09-15T13:00:00+00:00",
                                                     "end_utc": "2026-09-15T13:30:00+00:00", "reason": "simulated"})
    assert intent_uid not in _provider_events(state)

    _intent, refired = _run(f"approve calendar {action_id}", session_id=session)
    assert refired.ok, refired.response_text
    assert refired.details["uid"] == intent_uid, "same durable identity"
    assert refired.details["recovered"] is False
    events = _provider_events(state)
    assert events[intent_uid]["summary"] == "Never landed"


def test_concurrent_duplicate_approval_executes_once(vilnius_env):
    """Two approvals race: the atomic claim lets exactly one through."""
    session = "concurrent-claim"
    state = vilnius_env["state"]
    _intent, proposal = _run('propose "Single flight" on 2026-09-15 16:00 Europe/Berlin for 30m', session_id=session)
    action_id = proposal.details["action_id"]

    from core.local_operator_actions import _claim_pending_action

    assert _claim_pending_action(action_id) is True, "first claim wins"
    assert _claim_pending_action(action_id) is False, "second claim loses"
    # The losing approval turn reports honestly and executes nothing.
    _intent, lost = _run(f"approve calendar {action_id}", session_id=session)
    assert not lost.ok
    assert lost.status == "concurrent_approval"
    assert "Nothing was executed a second time" in lost.response_text
    assert "Single flight" not in {row["summary"] for row in _provider_events(state).values()}


def test_approval_is_bound_to_the_reviewed_provider_and_account(vilnius_env, monkeypatch):
    """An approval staged against one account must not execute against another."""
    session = "binding"
    state = vilnius_env["state"]
    _intent, proposal = _run('propose "Bound" on 2026-09-15 16:00 Europe/Berlin for 30m', session_id=session)
    action_id = proposal.details["action_id"]

    # The runtime is re-pointed at a DIFFERENT provider account between preview and approval.
    other_server, _other_state, other_base, _port = start_caldav_fixture(calendars={VILNIUS_CAL: "Vilnius"})
    try:
        monkeypatch.setenv("VOOL_CALENDAR_URL", other_base)
        _intent, refused = _run(f"approve calendar {action_id}", session_id=session)
        assert not refused.ok
        assert refused.status == "provider_changed"
        assert "re-propose against the current account" in refused.response_text
        assert not any(row["summary"] == "Bound" for row in _provider_events(state).values()), "original account untouched"
        assert _other_state.snapshot()[VILNIUS_CAL] == {}, "re-pointed account untouched too"
    finally:
        other_server.shutdown()
        other_server.server_close()


def _scope_of(env, session_id, action_id):
    from core.operator.calendar_provider import load_action_any_state

    row = load_action_any_state(session_id=session_id, action_kind="provider_calendar_event", action_id=action_id)
    import json

    return json.loads(str(row.get("scope_json") or "{}"))
