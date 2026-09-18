"""pa_beta_gate — the notes vertical: real workspace files, linked receipts, isolation.

A note is a real Markdown file under the ACTIVE workspace's notes/ directory. The tests
drive the served operator seam (parse + wired dispatch), verify the file bytes on disk,
the note <-> provider-event link, project isolation across workspace roots, and the
action-to-proposal seam that turns a named note action into an approval-gated calendar
proposal backed by the disposable CalDAV service.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ._caldav_service import start_caldav_fixture

pytestmark = [pytest.mark.pa_beta]

VILNIUS_CAL = "/calendars/vilnius/"


@pytest.fixture
def notes_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    from core.user_preferences import save_user_timezone

    assert save_user_timezone("Europe/Berlin")

    server, state, base_url, _port = start_caldav_fixture(calendars={VILNIUS_CAL: "Vilnius"})
    monkeypatch.setenv("VOOL_CALENDAR_PROVIDER", "caldav")
    monkeypatch.setenv("VOOL_CALENDAR_URL", base_url)
    monkeypatch.setenv("VOOL_CALENDAR_ID", VILNIUS_CAL)
    yield {"server": server, "state": state, "workspace": workspace}
    server.shutdown()
    server.server_close()


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"operator parser missed a notes request: {text!r}"
    return intent, dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def test_save_read_search_update_round_trip(notes_env):
    session = "notes-rt"
    _intent, saved = _run("save a note with agenda: packaging and release checks", session_id=session)
    assert saved.ok, saved.response_text
    path = Path(saved.details["note_path"])
    assert path.is_file()
    body = path.read_text(encoding="utf-8")
    assert "packaging and release checks" in body, "the note's body is the file's own content"
    assert "session: " + session in body, "the creating session is recorded"

    _intent, shown = _run('show the note "Agenda"', session_id=session)
    assert shown.ok, shown.response_text
    assert "packaging and release checks" in shown.response_text

    _intent, found = _run("find my notes about packaging", session_id=session)
    assert found.ok
    assert found.details["count"] == 1
    assert "packaging" in found.response_text

    from core.operator.notes import update_note

    outcome = update_note(title="Agenda", append="release sign-off checklist")
    assert outcome["ok"]
    assert "release sign-off checklist" in Path(outcome["note_path"]).read_text(encoding="utf-8")

    # Deterministic search reports what is there: no matches is no matches.
    _intent, none = _run("find my notes about zeppelin logistics", session_id=session)
    assert none.ok
    assert none.details["count"] == 0


def test_note_requires_body_not_a_guess(notes_env):
    _intent, empty = _run("save a note", session_id="notes-empty")
    assert not empty.ok
    assert empty.status == "invalid_request"
    assert "need its content" in empty.response_text


def test_project_isolation_across_workspace_roots(notes_env, monkeypatch, tmp_path):
    session = "notes-isolation"
    _intent, saved = _run('save a note titled "Secret project plan" with: phase two details', session_id=session)
    assert saved.ok

    other_root = tmp_path / "other-workspace"
    other_root.mkdir()
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(other_root))
    _intent, none_visible = _run("find my notes about phase two", session_id=session)
    assert none_visible.ok
    assert none_visible.details["count"] == 0, "another project's workspace is another note universe"

    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(notes_env["workspace"]))
    _intent, visible = _run("find my notes about phase two", session_id=session)
    assert visible.details["count"] == 1


def test_named_note_action_becomes_a_linked_calendar_proposal(notes_env, monkeypatch):
    """The mission's action-to-proposal seam: note first, proposal from the NAMED action,
    link written only after the provider event verifiably exists."""
    session = "notes-action-link"
    state = notes_env["state"]

    _intent, saved = _run(
        'save a note titled "Kickoff follow-ups" with: [action] book the venue call Friday at 15:00 Europe/Berlin',
        session_id=session,
    )
    assert saved.ok, saved.response_text
    assert saved.details["actions_found"], "an explicit [action] line is recognized"

    fixed = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    from zoneinfo import ZoneInfo

    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: fixed.astimezone(ZoneInfo("Europe/Berlin")))

    _intent, proposal = _run(
        'schedule the action "book the venue call" from my note on Thursday at 10:00 Europe/Berlin',
        session_id=session,
    )
    assert proposal.status == "approval_required", proposal.response_text
    assert proposal.details["title"], "the action's words are the title, not an invented one"

    _intent, created = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert created.ok, created.response_text
    uid = created.details["uid"]
    assert uid in state.snapshot()[VILNIUS_CAL]

    # The link is evidence, not hope: the note carries the receipt of the verified event.
    note_path = Path(saved.details["note_path"])
    text = note_path.read_text(encoding="utf-8")
    assert f"linked_event_uid: {uid}" in text
    assert created.response_text.count(str(note_path)) == 1


def test_named_action_refusals_are_typed(notes_env):
    session = "notes-action-refusals"
    _intent, saved = _run('save a note titled "Clean" with: nothing actionable here', session_id=session)
    assert saved.ok

    _intent, missing = _run('schedule the action "does not exist anywhere" from my note', session_id=session)
    assert not missing.ok
    assert "can't find that action" in missing.response_text

    _run('save a note titled "Untimed" with: [action] an action with no clock anywhere', session_id=session)
    _intent, untimed = _run('schedule the action "an action with no clock" from my note', session_id=session)
    assert not untimed.ok
    assert "no time" in untimed.response_text


def test_apple_notes_destination_attempts_the_native_route_then_falls_back_honestly(notes_env, monkeypatch):
    """Asking for Apple Notes drives the native route. TESTS NEVER RUN THE REAL BRIDGE
    (it would mutate the owner's Notes app): a LABELLED runner double answers with the
    typed consent-denied state, the note is saved as a clearly-labelled workspace note,
    and nothing masquerades as an Apple Notes note. The real-bridge run is the opt-in
    gate below."""
    from core.operator import apple_notes as bridge

    def denied_double(*, title, body, account="", folder="", runner=None):
        return {"ok": False, "reason": "os_permission_denied", "detail": "macOS refused Automation access to Notes (test double)."}

    monkeypatch.setattr(bridge, "create_apple_note", denied_double)
    session = "notes-dest"
    _intent, saved = _run("save a note to Apple Notes with agenda: venue walkthrough", session_id=session)
    # The requested destination is DENIED: an honest partial, not a success. The
    # labelled workspace fallback artifact still exists.
    assert not saved.ok and saved.status == "os_permission_denied"
    assert "destination is NOT resolved" in saved.response_text
    assert "NOT an Apple Notes note" in saved.response_text
    path = Path(saved.details["note_path"])
    assert path.is_file() and "venue walkthrough" in path.read_text(encoding="utf-8")


def test_apple_notes_live_bridge_is_an_explicit_opt_in_gate(notes_env, monkeypatch):
    """OPT-IN only (set VOOL_APPLE_NOTES_LIVE=1 with the owner's consent): runs the
    REAL AppleScript bridge, verifies the typed success answer, and deletes the note it
    created. Skipped otherwise — tests must not mutate the owner's Notes app."""
    import os

    if str(os.environ.get("VOOL_APPLE_NOTES_LIVE") or "") != "1":
        pytest.skip("live Apple Notes bridge is an explicit owner-assisted gate (VOOL_APPLE_NOTES_LIVE=1)")
    from core.operator.apple_notes import create_apple_note

    outcome = create_apple_note(title="VOOL bridge verification", body="temporary test note - will be deleted immediately")
    if not outcome.get("ok") and outcome.get("reason") == "os_permission_denied":
        pytest.skip(f"Automation consent not granted on this host: {outcome.get('detail')}")
    assert outcome["ok"], outcome
    reference = outcome["note_reference"]
    import re as _re

    match = _re.search(r"p(\d+)$", reference)
    assert match, reference
    import subprocess

    subprocess.run(["/usr/bin/osascript", "-e", f'tell application "Notes" to delete (first note whose id is "{reference}")'], capture_output=True, timeout=20)
    note_exists = subprocess.run(["/usr/bin/osascript", "-e", f'tell application "Notes" to (count of (notes whose id is "{reference}"))'], capture_output=True, text=True, timeout=20)
    assert note_exists.stdout.strip() in {"0", ""}


def test_apple_notes_bridge_command_and_typed_errors():
    """LABELLED BOUNDARY TEST (method-shaped runner double — not a native run):
    the exact AppleScript, AppleScript escaping, and the OS failure mapping."""
    from core.operator.apple_notes import build_create_note_script, create_apple_note

    script = build_create_note_script(title="Kickoff", body='Say "hi"', account="iCloud", folder="Work")
    assert 'tell application "Notes"' in script
    assert 'name:"Kickoff"' in script
    assert 'Say \\"hi\\"' in script
    assert 'folder "Work" of account "iCloud"' in script

    class Completed:
        def __init__(self, stderr="", returncode=0, stdout="note id x"):
            self.stderr, self.returncode, self.stdout = stderr, returncode, stdout

    denied = create_apple_note(title="t", body="b", runner=lambda *a, **k: Completed(stderr="execution error: Not authorized to send Apple events. (-1743)", returncode=1))
    assert denied["ok"] is False and denied["reason"] == "os_permission_denied"
    assert "Automation" in denied["detail"]

    ok_run = create_apple_note(title="t", body="b", runner=lambda *a, **k: Completed())
    assert ok_run["ok"] is True and ok_run["note_reference"] == "note id x"

    empty = create_apple_note(title="t", body="b", runner=lambda *a, **k: Completed(stdout="", returncode=0))
    assert empty["ok"] is False and empty["reason"] == "unverified"
