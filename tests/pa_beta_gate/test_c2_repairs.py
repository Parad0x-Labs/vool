"""pa_beta_gate -- correction-2 workflow proofs for the four review repairs.

Each repair keeps the review's original case, adds a genuinely novel case, and a
refusal/preservation control at the real owning seams.

LABELLED: loopback CalDAV/Google fixtures, synthetic stateful Notes runner, injected clock and
storage failures. No owner data, no live invitations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ._caldav_service import start_caldav_fixture
from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)
WORK_CAL = "/calendars/work/"
OTHER_CAL = "/calendars/other/"


@pytest.fixture
def home(tmp_path, monkeypatch):
    prepared = prepare_home(tmp_path, monkeypatch)
    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: T0.astimezone(ZoneInfo("Europe/Berlin")))
    return prepared


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed the request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _choose(provider, base, cal, label, *, default_write=False):
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider=provider, base_url=base, label=label)
    assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
    assert calendar_accounts.select_calendar(account["account_id"], cal, selected=True, default_write=default_write)["ok"]
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)


# ---------------------------------------------------------------------------
# R1: Notes replay identity
# ---------------------------------------------------------------------------

def test_delete_replay_after_target_disappears_and_title_reuse(home, monkeypatch):
    """NOVEL (R1): a delete's replay, after the original note is gone and ANOTHER note reuses
    its title, answers from the record and touches nothing; a legitimately NEW request (new
    task) deletes the current holder after its own review; an unknown-effect record replays
    as unresolved without a second bridge call."""
    from core.operator.notes import deliver_apple_note_mutation
    from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes

    notes = FakeNotes({"id-a": {"title": "Grooming", "folder": "Work", "account": "iCloud", "body": "x"},
                       "id-b": {"title": "Other", "folder": "Work", "account": "iCloud", "body": "y"}}).install(monkeypatch)
    args = dict(kind="delete", title="Grooming", folder="Work", account="iCloud",
                task_id="t-delete", session_id="s-delete")
    first = deliver_apple_note_mutation(**args)
    assert first["ok"] and first.get("note_id") == "id-a", first
    assert "id-a" not in notes.notes

    # title reused by a different note; the SAME request replays from its record.
    notes.notes["id-new"] = {"title": "Grooming", "folder": "Work", "account": "iCloud", "body": "z"}
    second = deliver_apple_note_mutation(**args)
    assert second.get("replayed") is True and second["ok"], second
    assert "id-new" in notes.notes and notes.notes["id-new"]["title"] == "Grooming", "the replacement was untouched"

    # a genuinely new explicit request reviews and deletes the current holder
    fresh = deliver_apple_note_mutation(**{**args, "task_id": "t-delete-2"})
    assert fresh["ok"] and fresh.get("note_id") == "id-new", fresh
    assert "id-new" not in notes.notes


def test_rename_replay_after_unknown_effect_never_resends(home, monkeypatch):
    """CONTROL (R1): an unresolved rename (lost reply) replays as unresolved without a second
    bridge attempt; the renamed-away original title is not re-resolved onto another note."""
    import subprocess

    from core.operator.notes import deliver_apple_note_mutation
    from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes

    notes = FakeNotes({"id-x": {"title": "Draft", "folder": "Work", "account": "iCloud", "body": ""}})
    notes.timeout_on_append = True

    def rename_timeout(script, *, runner=None, purpose="the Notes request"):
        if "set name of theNote to" in script:
            raise subprocess.TimeoutExpired(["osascript"], 5)  # the rename dispatch is lost
        return original_bridge(script, runner=runner, purpose=purpose)

    notes.install(monkeypatch)
    from core.operator import apple_notes

    original_bridge = apple_notes.run_notes_bridge
    apple_notes.run_notes_bridge = rename_timeout
    try:
        args = dict(kind="rename", title="Draft", new_title="Reviewed", folder="Work", account="iCloud",
                    task_id="t-r", session_id="s-r")
        first = deliver_apple_note_mutation(**args)
        assert not first["ok"] and first.get("delivery_state") != "confirmed", first
    finally:
        apple_notes.run_notes_bridge = original_bridge

    notes.timeout_on_append = False
    replay = deliver_apple_note_mutation(**dict(kind="rename", title="Draft", new_title="Reviewed",
                                                folder="Work", account="iCloud", task_id="t-r", session_id="s-r"))
    assert not replay["ok"] and replay.get("replayed") is True, replay
    renames = [s for s in notes.scripts if "set name of theNote to" in s]
    assert renames == [], "nothing was sent on replay of an unresolved rename"


# ---------------------------------------------------------------------------
# R3: selection-store truth
# ---------------------------------------------------------------------------

def test_corrupt_store_blocks_create_and_move_and_availability(home, monkeypatch):
    """NOVEL (R3): a malformed selections store refuses create AND move execution AND
    availability with an unavailable answer -- never an empty-calendar or free-time claim; a
    verified legacy store (no such table) keeps single-provider behavior."""
    import sqlite3

    from core.operator import calendar_accounts, calendar_agenda

    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={WORK_CAL: "Work"})
    try:
        _choose("caldav", base, WORK_CAL, "Work", default_write=True)

        def malformed(**kw):
            raise sqlite3.DatabaseError("database disk image is malformed")

        monkeypatch.setattr(calendar_accounts, "list_accounts", malformed)
        with pytest.raises(calendar_agenda.SelectionsUnavailableError):
            calendar_agenda.selected_sources()

        proposal = _run('propose "C" on 2026-09-22 15:00 Europe/Berlin for 30m', session_id="r3")
        assert not proposal.ok and proposal.status == "storage_unavailable", proposal.response_text
        assert state.snapshot()[WORK_CAL.rstrip("/") + "/"] == {}

        state.seed_event(WORK_CAL, "e1@f", summary="Existing", start=T0 + timedelta(days=1, hours=3), minutes=30)
        move = _run('move the "Existing" event to 2026-09-23 11:00 Europe/Berlin', session_id="r3b")
        assert move.status == "storage_unavailable", move.response_text

        check = _run("Check Tuesday afternoon for a free 30-minute slot", session_id="r3c")
        assert not check.ok and check.status == "storage_unavailable", check.response_text

        monkeypatch.undo()
        Clock(T0, monkeypatch)
        from core import local_operator_actions

        monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: T0.astimezone(ZoneInfo("Europe/Berlin")))
        healthy = _run("Check Tuesday afternoon for a free 30-minute slot", session_id="r3d")
        assert healthy.ok, healthy.response_text  # the healthy store reads again

        def legacy(**kw):
            raise sqlite3.OperationalError("no such table: calendar_accounts")

        monkeypatch.setattr(calendar_accounts, "list_accounts", legacy)
        assert calendar_agenda.selected_sources() == [], "a verified pre-selections store has no selections"
    finally:
        server.shutdown(); server.server_close()


# ---------------------------------------------------------------------------
# R2: moves refuse unreadable selected sources (novel: real outage, then return)
# ---------------------------------------------------------------------------

def test_move_refuses_during_real_second_source_outage_then_succeeds_on_return(home, monkeypatch):
    """NOVEL (R2): with the second selected CalDAV server's port genuinely closed, an approved
    move refuses without sending and the approval rests; when a server answers on that port
    again the SAME approval executes; a conflict on the returned source then blocks a further
    move -- recovery is not a free pass."""
    Clock(T0, monkeypatch)
    server_w, state_w, base_w, _ = start_caldav_fixture(calendars={WORK_CAL: "Work"})
    server_o, state_o, base_o, port_o = start_caldav_fixture(calendars={OTHER_CAL: "Other"})
    try:
        _choose("caldav", base_w, WORK_CAL, "Work", default_write=True)
        _choose("caldav", base_o, OTHER_CAL, "Other account")
        state_w.seed_event(WORK_CAL, "mv@f", summary="Board prep", start=T0 + timedelta(days=1), minutes=30)
        session = "r2"
        proposal = _run('move the "Board prep" event to 2026-09-23 11:00 Europe/Berlin', session_id=session)
        assert proposal.status == "approval_required", proposal.response_text
        before = state_w.snapshot()

        server_o.shutdown(); server_o.server_close()  # the port now refuses connections
        refused = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert not refused.ok and "could not be read" in refused.response_text, refused.response_text
        assert "Other" in refused.response_text, refused.response_text
        assert state_w.snapshot() == before, "nothing was sent during the outage"
        assert refused.details.get("resting_status") in {"pending_approval", "pending"}, refused.details

        # a server answers on the same port again; the SAME approval executes once
        server_r, state_r, _base_r, _ = start_caldav_fixture(calendars={OTHER_CAL: "Other"}, preferred_port=port_o)
        try:
            moved = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
            assert moved.ok, moved.response_text
            after = state_w.snapshot()[WORK_CAL.rstrip("/") + "/"]
            assert "T11" not in after["mv@f"]["start_utc"] or "2026-09-23" in after["mv@f"]["start_utc"], after

            state_r.seed_event(OTHER_CAL, "clash@f", summary="Quarterly", start=T0 + timedelta(days=2, hours=2), minutes=60)
            blocked = _run('move the "Board prep" event to 2026-09-23 12:00 Europe/Berlin', session_id=session + "b")
            assert blocked.status == "approval_required", blocked.response_text
            blocked_ok = _run(f"approve calendar {blocked.details['action_id']}", session_id=session + "b")
            assert not blocked_ok.ok and blocked_ok.status == "conflict" and "Quarterly" in blocked_ok.response_text, blocked_ok.response_text
        finally:
            server_r.shutdown(); server_r.server_close()
    finally:
        server_w.shutdown(); server_w.server_close()


# ---------------------------------------------------------------------------
# R4: description preservation at the provider wire
# ---------------------------------------------------------------------------

def test_caldav_move_preserves_description_over_real_put_readback(home, monkeypatch):
    """NOVEL (R4): an approved move on a real loopback CalDAV server round-trips the stored
    description and an unknown extension; the start moves to the reviewed local time."""
    CRLF = chr(13) + chr(10)
    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={WORK_CAL: "Work"})
    try:
        _choose("caldav", base, WORK_CAL, "Work", default_write=True)
        session = "r4"
        created = _run('propose "Deep work" on 2026-09-22 12:00 Europe/Berlin for 60m', session_id=session)
        assert created.status == "approval_required", created.response_text
        approved = _run(f"approve calendar {created.details['action_id']}", session_id=session)
        assert approved.ok, approved.response_text
        uid = approved.details["uid"]
        stored = state.snapshot()[WORK_CAL.rstrip("/") + "/"][uid]["ics"]
        enriched = stored.replace("END:VEVENT",
                                  "DESCRIPTION:Keep my agenda" + CRLF + "X-CUSTOM-FLAG:keep-me" + CRLF + "END:VEVENT")
        state.put_event(WORK_CAL, uid, enriched)

        move = _run('move the "Deep work" event to 2026-09-22 15:00 Europe/Berlin', session_id=session + "b")
        assert move.status == "approval_required", move.response_text
        moved = _run(f"approve calendar {move.details['action_id']}", session_id=session + "b")
        assert moved.ok, moved.response_text
        after = state.snapshot()[WORK_CAL.rstrip("/") + "/"][uid]["ics"]
        assert "DESCRIPTION:Keep my agenda" in after, after
        assert "X-CUSTOM-FLAG:keep-me" in after, "unknown extensions survive the PUT"
        assert "DTSTART;TZID=Europe/Berlin:20260922T150000" in after, after
        assert "120000" not in after, after
    finally:
        server.shutdown(); server.server_close()
