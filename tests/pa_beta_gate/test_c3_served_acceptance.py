"""pa_beta_gate -- correction 3 SERVED acceptance: the three repaired contracts through the runtime's own agent turns.

Real agent turns (``VoolAgent.run_once``) through the production conversational path -- served ingress, routing,
operator dispatch, approvals and checkpoint resume -- one session per flow:

* Notes (F1): an UNSCOPED and a SCOPED Apple Notes rename are delivered, the turn stops before its answer, the title is
  reused elsewhere, and the user's "resume" re-runs the SAME request: it answers from the original record and renames
  nothing. A genuinely new message afterwards (a new task) acts on the note that now carries the title.
* CalDAV (F2, F3): a named occurrence of a series on the strict loopback CalDAV service is moved on approval and read back
  at its new time, with the master and its other occurrences persisting in the ONE series resource. A cancellation of
  another occurrence is approved against a provider that acknowledges the write without applying it: unproven, never
  verified; approving again sends nothing; after the provider applies it, a later approval verifies it without writing.

LABELLED: MODEL STAND-IN (the deterministic stand-in the served workflow tests use); SYNTHETIC NOTES RUNNER (``FakeNotes``
behind ``apple_notes.run_notes_bridge``, never osascript); SYNTHETIC FAULTS (one RuntimeError right after the operator
step; a provider write acknowledged without effect); loopback strict CalDAV fixture. In-process served ingress -- not HTTP,
browser, native window or a live provider.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes
from tests.pa_beta_gate.test_served_calendar_notes_workflows import (  # noqa: F401 -- served_env is a fixture, requested by name
    VILNIUS_CAL,
    _approval_id,
    _turn,
    served_env,
)
from tests.pa_beta_gate.test_v7_served_notes_receipt_identity import _StopAfterOperatorStep

from . import _caldav_service

pytestmark = [pytest.mark.pa_beta]

CRLF = "\r\n"
RENAMES = "set name of theNote to"


def _mutation_operations(workspace) -> list[dict]:
    path = Path(workspace) / "notes" / ".apple-notes-effects.json"
    return [op for op in json.loads(path.read_text(encoding="utf-8"))["operations"].values() if op.get("effect_kind")]


def _renamed_then_stopped(harness, workspace, fault, text: str) -> dict:
    """The rename is delivered, the turn stops before its answer, and the runtime leaves a resumable checkpoint."""
    from core.runtime_continuity import latest_resumable_checkpoint

    fault.armed = True
    with pytest.raises(RuntimeError, match="synthetic stop after the operator step"):
        _turn(harness, text, workspace)
    op = max(_mutation_operations(workspace), key=lambda row: str(row.get("reserved_at") or ""))
    checkpoint = latest_resumable_checkpoint(op["session_id"])
    assert op["state"] == "confirmed" and checkpoint and checkpoint["status"] == "interrupted", (op, checkpoint)
    assert checkpoint["task_id"] == op["task_id"], "the interrupted checkpoint holds the task whose rename was recorded"
    return op


def test_served_unscoped_and_scoped_note_replays_keep_their_original_custody(request, monkeypatch):
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    fake = FakeNotes({
        "a": {"title": "Draft", "folder": "Work", "account": "iCloud", "body": "a"},
        "p": {"title": "Plan", "folder": "Work", "account": "iCloud", "body": "p"},
    }).install(monkeypatch)
    fault = _StopAfterOperatorStep(monkeypatch, harness.agent)

    # UNSCOPED -- the review's F1 data, reached through the served resume.
    op = _renamed_then_stopped(harness, workspace, fault, 'rename my Apple note "Draft" to "Ready"')
    assert (op["account"], op["folder"], op["target"]["note_id"], op["target"]["folder"]) == ("", "", "a", "Work"), op
    assert fake.notes["a"]["title"] == "Ready"
    fake.notes["b"] = {"title": "Draft", "folder": "Personal", "account": "On My Mac", "body": "b"}
    resumed = _turn(harness, "resume", workspace)
    assert fake.notes["b"]["title"] == "Draft", f"the resumed request renamed a different note: {resumed!r}"
    assert 'Renamed the Apple note "Draft" to "Ready"' in resumed, resumed
    assert len([script for script in fake.scripts if RENAMES in script]) == 1

    # SCOPED -- novel data: the account is named (served chat routes any "... folder ..." wording to the workspace-folder
    # lane before the Notes owner -- recorded as a separate open routing gap); the title is then reused in another
    # folder of that same account, the partial-scope retarget case.
    op = _renamed_then_stopped(harness, workspace, fault, 'rename my Apple note "Plan" in the iCloud account to "Plan v2"')
    assert (op["account"], op["folder"], op["target"]["note_id"], op["target"]["folder"]) == ("iCloud", "", "p", "Work"), op
    fake.notes["q"] = {"title": "Plan", "folder": "Personal", "account": "iCloud", "body": "q"}
    resumed = _turn(harness, "resume", workspace)
    assert fake.notes["q"]["title"] == "Plan", f"the resumed scoped request renamed a different note: {resumed!r}"
    assert len([script for script in fake.scripts if RENAMES in script]) == 2 and fault.fired == 2

    # A genuinely new message is its own request and acts on the note that now carries the title.
    fresh = _turn(harness, 'rename my Apple note "Draft" to "Ready"', workspace)
    assert fake.notes["b"]["title"] == "Ready", fresh
    assert len([script for script in fake.scripts if RENAMES in script]) == 3


SERIES_MASTER = CRLF.join([
    "BEGIN:VEVENT", "UID:retro-served@f", "DTSTAMP:20260901T000000Z",
    "DTSTART:20260915T090000Z", "DTEND:20260915T100000Z", "SUMMARY:Retro",
    "ATTENDEE;CN=Ana;RSVP=TRUE:mailto:ana@example.org", "X-TEAM:platform",
    "RRULE:FREQ=WEEKLY;BYDAY=TU", "END:VEVENT",
])


def test_served_occurrence_move_and_accepted_cancellation_report_what_the_provider_holds(request, monkeypatch):
    env = request.getfixturevalue("served_env")
    harness, state, workspace = env["harness"], env["state"], env["workspace"]
    writes: list[str] = []
    real_do_put = _caldav_service._Handler.do_PUT

    def counted(handler):
        writes.append(unquote(urlparse(handler.path).path))
        return real_do_put(handler)

    monkeypatch.setattr(_caldav_service._Handler, "do_PUT", counted)
    state.put_event(VILNIUS_CAL, "retro-served@f", CRLF.join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Fixture//CalDAV//EN",
                                                               SERIES_MASTER, "END:VCALENDAR"]) + CRLF)

    # MOVE: the 2026-09-22 occurrence, two days later, on approval.
    proposal = _turn(harness, 'move the "Retro" event to 2026-09-24 12:00 Europe/Berlin (the 2026-09-22 occurrence)', workspace)
    moved = _turn(harness, f"approve calendar {_approval_id(proposal)}", workspace)
    assert "Event updated on the provider and verified: the 2026-09-22 occurrence of 'Retro'" in moved, moved
    assert writes == [f"{VILNIUS_CAL}retro-served@f.ics"], writes
    stored = state.snapshot()[VILNIUS_CAL]
    assert list(stored) == ["retro-served@f"], stored.keys()
    assert stored["retro-served@f"]["ics"].count("UID:retro-served@f") == 2 and SERIES_MASTER in stored["retro-served@f"]["ics"]
    week = _turn(harness, "agenda from 2026-09-21 to 2026-09-27", workspace)
    assert "Retro: 2026-09-24 12:00" in week and "2026-09-22 12:00" not in week, week
    siblings = _turn(harness, "agenda from 2026-09-28 to 2026-10-04", workspace)
    assert "Retro: 2026-09-29 12:00" in siblings, siblings

    # CANCEL: the 2026-09-29 occurrence, against a provider that acknowledges the write without applying it.
    real_put_event = state.put_event

    def accept_without_change(calendar, name, ics):
        if "STATUS:CANCELLED" in ics:
            return '"accepted-no-effect"'
        return real_put_event(calendar, name, ics)

    monkeypatch.setattr(state, "put_event", accept_without_change)
    staged = _turn(harness, 'cancel the "Retro" event on 2026-09-29', workspace)
    assert "the 2026-09-29 occurrence of 'Retro'" in staged and "the series and its other occurrences stay" in staged, staged
    action_id = _approval_id(staged)
    unproven = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "still serves the 2026-09-29 occurrence as scheduled" in unproven, unproven
    assert "cancelled on the provider and verified" not in unproven, unproven
    still = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "not verifiably cancelled" in still and "NOT sent again" in still, still
    assert len(writes) == 2, writes  # the move, and the one acknowledged cancellation; nothing re-sent

    # The provider applies the accepted cancellation later; a later approval verifies it and writes nothing.
    monkeypatch.setattr(state, "put_event", real_put_event)
    current = state.snapshot()[VILNIUS_CAL]["retro-served@f"]["ics"]
    applied = CRLF.join(["BEGIN:VEVENT", "UID:retro-served@f", "DTSTAMP:20260910T120000Z", "RECURRENCE-ID:20260929T090000Z",
                         "DTSTART:20260929T090000Z", "DTEND:20260929T100000Z", "SUMMARY:Retro", "STATUS:CANCELLED", "END:VEVENT"])
    state.put_event(VILNIUS_CAL, "retro-served@f", current.replace("END:VCALENDAR", applied + CRLF + "END:VCALENDAR"))
    verified = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "Occurrence cancelled on the provider and verified" in verified and "nothing was sent twice" in verified, verified
    assert len(writes) == 2, writes
    after = _turn(harness, "agenda from 2026-09-28 to 2026-10-04", workspace)
    assert "Retro:" not in after, after
    later = _turn(harness, "agenda from 2026-10-05 to 2026-10-11", workspace)
    assert "Retro: 2026-10-06 12:00" in later, later
    assert SERIES_MASTER in state.snapshot()[VILNIUS_CAL]["retro-served@f"]["ics"], "the series master persists as stored"
