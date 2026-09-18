"""pa_beta_gate — the Google Calendar and Microsoft Graph adapters, protocol-level.

Each adapter executes its REAL wire protocol (v3 REST / Graph v1.0 JSON) against a
disposable local service speaking that provider's documented API shape, through the real
VOOL transport. This proves the wire, the identity/version discipline and the failure
truth — it does NOT prove a live Google/Microsoft account (that connection/consent gate
is stated in the delivery, with the exact OAuth binding requirement).

The EventKit native adapter is exercised to its honest states here too: its packaging
gate (pyobjc binding) and its typed permission posture, without ever pretending a Mac
event store is connected when it is not.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from ._json_api_service import start_json_calendar_fixture

pytestmark = [pytest.mark.pa_beta]

G_CAL = "work@fixture.test"
H_CAL = "AAMkAGIyM2Fj"


@pytest.fixture
def google_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    server, state, base = start_json_calendar_fixture(dialect="google", calendars={G_CAL: "Work", "personal@fixture.test": "Personal"})
    monkeypatch.setenv("VOOL_CALENDAR_PROVIDER", "google")
    monkeypatch.setenv("VOOL_CALENDAR_URL", base)
    monkeypatch.setenv("VOOL_CALENDAR_ID", G_CAL)
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "10")
    # Fixed clock: this suite seeds events on fixed dates and speaks in day words ("Tuesday
    # afternoon"), so it must not depend on which weekday the real clock happens to be on.
    from datetime import datetime, timezone as _tz

    from core import local_operator_actions

    _fixed = datetime(2026, 9, 14, 12, 0, tzinfo=_tz.utc)  # Monday: "Tuesday" is 2026-09-15
    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: _fixed)
    yield {"server": server, "state": state, "url": base}
    server.shutdown()
    server.server_close()


@pytest.fixture
def graph_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    server, state, base = start_json_calendar_fixture(dialect="graph", calendars={H_CAL: "Work", "AAMkAApersonal=": "Personal"})
    monkeypatch.setenv("VOOL_CALENDAR_PROVIDER", "graph")
    monkeypatch.setenv("VOOL_CALENDAR_URL", base)
    monkeypatch.setenv("VOOL_CALENDAR_ID", H_CAL)
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "10")
    from datetime import datetime, timezone as _tz

    from core import local_operator_actions

    _fixed = datetime(2026, 9, 14, 12, 0, tzinfo=_tz.utc)  # Monday: day words resolve like the seeded dates
    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: _fixed)
    yield {"server": server, "state": state, "url": base}
    server.shutdown()
    server.server_close()


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed: {text!r}"
    return intent, dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


# ---------------------------------------------------------------------------
# Google Calendar: adapter protocol through the operator vertical
# ---------------------------------------------------------------------------


def test_google_original_journey(google_env):
    session = "google-orig"
    state = google_env["state"]
    state.seed_event(G_CAL, "busy@fixture", summary="Design review",
                     start=datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc), minutes=60)
    _intent, check = _run("check Tuesday afternoon for a free 30-minute slot", session_id=session)
    assert check.ok, check.response_text
    assert "Design review" in check.response_text
    options = check.details["options"]
    assert options
    for option in options:
        start = datetime.fromisoformat(option["start_utc"])
        assert not (start < datetime(2026, 9, 15, 14, tzinfo=timezone.utc) and datetime(2026, 9, 15, 13, tzinfo=timezone.utc) < start + timedelta(minutes=30))

    _intent, proposal = _run('option 1, propose "Sprint planning"', session_id=session)
    assert proposal.status == "approval_required", proposal.response_text
    _intent, created = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert created.ok, created.response_text
    uid = created.details["uid"]
    assert uid in state.snapshot()[G_CAL]
    assert state.snapshot()[G_CAL][uid]["summary"] == "Sprint planning"

    _intent, move = _run('move the "Sprint planning" event to Friday at 11:00 Europe/Berlin', session_id=session)
    assert move.status == "approval_required"
    _intent, moved = _run(f"approve calendar {move.details['action_id']}", session_id=session)
    assert moved.ok, moved.response_text

    _intent, cancel = _run('cancel the "Sprint planning" event', session_id=session)
    assert cancel.status == "approval_required"
    _intent, cancelled = _run(f"approve calendar {cancel.details['action_id']}", session_id=session)
    assert cancelled.ok and cancelled.details["verified_gone"] is True
    assert uid not in state.snapshot()[G_CAL]


def test_google_pagination_recurring_and_stale_etag(google_env):
    session = "google-matrix"
    state = google_env["state"]
    # Three events with page_size=2: the range read MUST page to completeness.
    for index in range(3):
        state.seed_event(G_CAL, f"series_{index}", summary=f"Weekly {index}",
                         start=datetime(2026, 9, 15, 13 + index, tzinfo=timezone.utc), minutes=30,
                         recurring_series="rec-series")
    # A recurring-instance identity: seriesId_instant.
    _intent, check = _run("check Tuesday afternoon for a free 30-minute slot", session_id=session)
    assert check.ok
    events = check.details["events"]
    assert len(events) == 3, f"pagination must read every page: {events}"
    from core.kas.adapters.google_calendar import splits_recurring_instance

    for event in events:
        assert splits_recurring_instance(event["uid"]) is not None, f"instance id carries its series: {event['uid']}"

    # Stale If-Match: external edit after the user saw v1.
    from core.effect_gateway import named_background_effect_scope
    from core.kas.contract import CalendarRefusedError
    from core.operator.calendar_provider import build_provider_adapter, load_provider_config

    _intent, proposal = _run('propose "Guarded" on 2026-09-15 19:00 Europe/Berlin for 30m', session_id=session)
    _intent, created = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    uid = created.details["uid"]
    config = load_provider_config()
    with named_background_effect_scope("test-google-external-edit"):
        adapter = build_provider_adapter(config)
        current = adapter.get_event(G_CAL, uid)
        from core.kas.contract import CalEvent

        adapter.update_event(G_CAL, CalEvent(
            provider_id="google", uid=uid, calendar_id=G_CAL, etag=current.etag,
            summary="Guarded (edited externally)", start_utc=current.start_utc,
            end_utc=current.end_utc, tz_name=current.tz_name))
    _intent, move = _run('move the "Guarded" event to Friday at 10:00 Europe/Berlin', session_id=session)
    assert move.status == "approval_required"
    _intent, refused = _run(f"approve calendar {move.details['action_id']}", session_id=session)
    assert not refused.ok and refused.status == "stale_etag", refused.response_text
    assert state.snapshot()[G_CAL][uid]["summary"] == "Guarded (edited externally)"
    del CalendarRefusedError


def test_google_auth_rate_limit_and_unknown_timeout(google_env, monkeypatch):
    session = "google-failures"
    state = google_env["state"]
    state.require_bearer = "google-fixture-secret"
    _intent, unauthorized = _run("list my calendars", session_id=session)
    assert unauthorized.status == "provider_refused"
    assert unauthorized.details["reason"] in {"authorization_expired", "http_403"}
    assert "unauthorized" in unauthorized.response_text
    state.require_bearer = ""

    state.rate_limit_after = 1
    _intent, limited = _run("list my calendars", session_id=session)
    assert limited.details["reason"] == "rate_limited"
    state.rate_limit_after = 0

    state.hang_seconds = 3.0
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "1")
    try:
        _intent, proposal = _run('propose "Uncertain" on 2026-09-15 16:00 Europe/Berlin for 30m', session_id=session)
        _intent, unknown = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert unknown.status == "outcome_unproven", unknown.response_text
    finally:
        state.hang_seconds = 0.0


# ---------------------------------------------------------------------------
# Microsoft Graph: the same vertical through a different provider
# ---------------------------------------------------------------------------


def test_graph_novel_journey_and_matrix(graph_env, monkeypatch):
    session = "graph-novel"
    state = graph_env["state"]
    from zoneinfo import ZoneInfo

    from core import local_operator_actions

    fixed = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)  # Saturday; Thursday = 2026-09-17
    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: fixed.astimezone(ZoneInfo("UTC")))
    state.seed_event(H_CAL, "existing@fixture", summary="Tenant town hall",
                     start=datetime(2026, 9, 17, 9, 30, tzinfo=timezone.utc), minutes=60)
    _intent, check = _run("check Thursday morning for a free 15-minute slot", session_id=session)
    assert check.ok, check.response_text
    assert "Tenant town hall" in check.response_text
    options = check.details["options"]
    assert options and all(
        not (datetime.fromisoformat(o["start_utc"]) < datetime(2026, 9, 17, 10, 30, tzinfo=timezone.utc)
             and datetime(2026, 9, 17, 9, 30, tzinfo=timezone.utc) < datetime.fromisoformat(o["start_utc"]) + timedelta(minutes=15))
        for o in options)

    _intent, proposal = _run('option 2, propose "Client prep"', session_id=session)
    assert proposal.status == "approval_required"
    _intent, created = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert created.ok, created.response_text
    uid = created.details["uid"]
    assert uid in state.snapshot()[H_CAL]

    # Provider binding: an approval staged against this Graph account must not execute
    # against a different account.
    other_server, _other, other_base = start_json_calendar_fixture(dialect="graph", calendars={H_CAL: "Work"})
    try:
        from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

        monkeypatch_env = {"VOOL_CALENDAR_PROVIDER": "graph", "VOOL_CALENDAR_URL": other_base, "VOOL_CALENDAR_ID": H_CAL}
        import os

        saved = {k: os.environ.get(k) for k in monkeypatch_env}
        os.environ.update(monkeypatch_env)
        try:
            _intent2, rename = _run('rename the "Client prep" event to "Client kickoff"', session_id=session)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        # Approve with the ORIGINAL account active: the staged update names the other
        # account, so the approval is a typed binding refusal — never executes there.
        refused_intent = parse_operator_action_intent(f"approve calendar {rename.details['action_id']}")
        refused = dispatch_operator_action(refused_intent, task_id="t", session_id=session)
        assert refused.status == "provider_changed", refused.response_text
        assert "re-propose against the current account" in refused.response_text
    finally:
        other_server.shutdown()
        other_server.server_close()
    assert uid in state.snapshot()[H_CAL], "original account untouched"

    _intent, cancel = _run('cancel the "Client prep" event', session_id=session)
    _intent, cancelled = _run(f"approve calendar {cancel.details['action_id']}", session_id=session)
    assert cancelled.ok and uid not in state.snapshot()[H_CAL]


def test_graph_auth_and_rate_limit(graph_env):
    session = "graph-failures"
    state = graph_env["state"]
    state.require_bearer = "graph-fixture-secret"
    _intent, unauthorized = _run("list my calendars", session_id=session)
    assert unauthorized.status == "provider_refused"
    assert "unauthorized" in unauthorized.response_text
    state.require_bearer = ""
    state.rate_limit_after = 1
    _intent, limited = _run("list my calendars", session_id=session)
    assert limited.details["reason"] == "rate_limited"
    state.rate_limit_after = 0


def test_cross_provider_identity_is_not_confused(google_env, graph_env, monkeypatch):
    """The same workflow through Google then Graph keeps identities per provider."""
    session = "cross-provider"
    import os

    # The provider fixtures stack env vars; pin GOOGLE as the active provider first.
    saved = (os.environ.get("VOOL_CALENDAR_PROVIDER"), os.environ.get("VOOL_CALENDAR_URL"), os.environ.get("VOOL_CALENDAR_ID"))
    os.environ["VOOL_CALENDAR_PROVIDER"] = "google"
    os.environ["VOOL_CALENDAR_URL"] = google_env["url"]
    os.environ["VOOL_CALENDAR_ID"] = G_CAL
    google_state = google_env["state"]
    _intent, gp = _run('propose "Shared title" on 2026-09-15 16:00 Europe/Berlin for 30m', session_id=session)
    assert gp.status == "approval_required", gp.response_text
    _intent, gc = _run(f"approve calendar {gp.details['action_id']}", session_id=session)
    assert gc.ok
    google_uid = gc.details["uid"]

    # Switch the ACTIVE provider to Graph (a different provider and account, same
    # session): the workflow rebinds, and no Google identity leaks into the Graph store.
    os.environ["VOOL_CALENDAR_PROVIDER"] = "graph"
    os.environ["VOOL_CALENDAR_URL"] = graph_env["url"]
    os.environ["VOOL_CALENDAR_ID"] = H_CAL
    graph_state = graph_env["state"]
    try:
        _intent, check = _run("list my calendars", session_id=session)
        assert check.ok, check.response_text
        assert google_uid not in graph_state.snapshot().get(H_CAL, {}), "google identity never appears in the graph store"
    finally:
        os.environ["VOOL_CALENDAR_PROVIDER"], os.environ["VOOL_CALENDAR_URL"], os.environ["VOOL_CALENDAR_ID"] = saved
    assert google_uid in google_state.snapshot()[G_CAL]


# ---------------------------------------------------------------------------
# EventKit: honest native states, no fabricated store
# ---------------------------------------------------------------------------


def test_eventkit_states_its_packaging_gate_honestly():
    from core.kas.adapters.eventkit_mac import EventKitCalendarAdapter, eventkit_availability

    availability = eventkit_availability()
    try:
        import EventKit  # noqa: F401
        import Foundation  # noqa: F401

        binding = True
    except Exception:
        binding = False
    if not binding:
        assert availability["state"] == "unavailable"
        assert availability["reason"] == "os_binding_unavailable"
        assert "pyobjc EventKit binding" in availability["detail"]
        # The adapter REFUSES to construct without the binding: no fake store.
        with pytest.raises(Exception) as unavailable:
            EventKitCalendarAdapter(transport=None, config=None)
        assert "EventKit" in str(unavailable.value) or "binding" in str(unavailable.value)
    else:  # pragma: no cover - only on a macOS host with pyobjc
        assert availability["state"] == "binding_available"


def test_eventkit_permission_denial_is_a_recoverable_typed_state():
    """The authorization gate is real logic testable without the binding: a denied
    state answers a typed refusal naming the System Settings pane, never a bypass."""
    from core.kas.adapters.eventkit_mac import EventKitStore, _load_eventkit

    try:
        _load_eventkit()
    except Exception as exc:
        pytest.skip(f"pyobjc EventKit binding unavailable here ({exc}); the packaging gate test above covers the honest state")
    store = EventKitStore()
    state = store.authorization()
    if "authorized" in state.lower():
        pytest.skip("this host has granted Calendars access; the denied-state path cannot be exercised without changing OS permissions")
    from core.kas.contract import CalendarRefusedError

    with pytest.raises(CalendarRefusedError) as refused:
        store.require_authorized()
    assert refused.value.reason == "os_permission_denied"
    assert "System Settings" in refused.value.detail
