"""pa_beta_gate -- Notes that stay useful: recoverable archive/restore, and Apple Notes
list/read/append/rename/delete through the Automation bridge (product rows 23-26).

Workspace notes run for real against the isolated workspace. Apple Notes runs through the real
bridge code with a synthetic runner standing in for osascript (the review guard forbids real
AppleScript); permission, timeout, locked and missing-note classifications are asserted as typed
answers, and deletion requires an explicit confirmation turn.

LABELLED: isolated home/workspace; synthetic injected Apple Notes runner.
"""
from __future__ import annotations

import uuid

import pytest

from ._pc_calendar_rig import prepare_home

pytestmark = [pytest.mark.pa_beta]


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed a notes request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def test_workspace_note_archive_is_recoverable_and_reviewed(home):
    """ORIGINAL: save, archive (out of lists/search), restore unchanged — plus the refusals."""
    session = "notes-archive"
    saved = _run('save a note titled "Kickoff" with agenda: intro and goals', session_id=session)
    assert saved.ok, saved.response_text

    gone = _run('archive the note "Kickoff"', session_id=session)
    assert gone.ok and "not erased" in gone.response_text, gone.response_text

    from core.operator.notes import search_notes

    assert search_notes(query="Kickoff") == [], "an archived note is not search results"
    missing = _run('show the note "Kickoff"', session_id=session)
    assert not missing.ok, missing.response_text

    unknown = _run('archive the note "Never saved"', session_id=session)
    assert not unknown.ok and "no note" in unknown.response_text.lower(), unknown.response_text

    back = _run('restore the note "Kickoff"', session_id=session)
    assert back.ok, back.response_text
    shown = _run('show the note "Kickoff"', session_id=session)
    assert shown.ok and "intro and goals" in shown.response_text, shown.response_text


from .test_pc_notes_identity import FakeNotes  # stateful model speaking the real script protocol


@pytest.fixture
def notes_model(home):
    holder = {}

    def install(model=None):
        holder["model"] = model or FakeNotes({
            "id-standups": {"title": "Standups", "folder": "Work", "account": "iCloud", "body": "<div>migrated repo</div>"},
            "id-reading": {"title": "Reading list", "folder": "Personal", "account": "iCloud", "body": "<div>books</div>"},
        })
        return holder["model"]

    install()
    return holder, install


class _BridgeInstall:
    def __init__(self, install, raw):
        self._install = install
        self.raw = raw

    def __call__(self, model=None):
        return self._install(model)


@pytest.fixture
def bridge(home, monkeypatch):
    holder = {}

    def install(model=None):
        holder["model"] = model if model is not None else FakeNotes({
            "id-standups": {"title": "Standups", "folder": "Work", "account": "iCloud", "body": "<div>migrated repo</div>"},
            "id-reading": {"title": "Reading list", "folder": "Personal", "account": "iCloud", "body": "<div>books</div>"},
        })
        holder["model"].install(monkeypatch)
        return holder["model"]

    def install_raw(runner):
        from core.operator import apple_notes

        monkeypatch.setattr(apple_notes, "run_notes_bridge", runner)

    install()
    return _BridgeInstall(install, install_raw)


def test_apple_notes_list_read_append_and_protections(home, bridge):
    """NOVEL: listing and reading through the bridge; append touches only the end; a locked note
    refuses instead of exposing content; a permission denial is the typed recovery answer; a
    missing note is not found, not a bridge failure."""
    session = "apple-notes"
    model = bridge()
    listed = _run("show my Apple Notes", session_id=session)
    assert listed.ok and "Standups" in listed.response_text and "Reading list" in listed.response_text, listed.response_text

    read = _run('read my Apple note "Standups"', session_id=session)
    assert read.ok and "migrated repo" in read.response_text, read.response_text

    appended = _run('append to my Apple note "Standups" with "checked pricing"', session_id=session)
    assert appended.ok and "left untouched" in appended.response_text, appended.response_text
    assert "checked pricing" in model.notes["id-standups"]["body"]
    assert model.notes["id-reading"]["body"] == "<div>books</div>", "the other note is untouched"

    locked = FakeNotes({"id-locked": {"title": "Standups", "folder": "Work", "account": "iCloud", "body": ""}})
    bridge(locked)
    locked_result = _run('read my Apple note "Standups"', session_id=session + "locked")
    assert not locked_result.ok and "locked" in locked_result.response_text.lower(), locked_result.response_text

    def denied(script, *, runner=None, purpose="the Notes request"):
        from core.operator.apple_notes import _classify

        reason, detail = _classify("execution error: Authorization is required. (-1743)", 1)
        return {"ok": False, "reason": reason, "detail": detail}

    bridge.raw(denied)
    denied_result = _run("show my Apple Notes", session_id=session + "denied")
    assert not denied_result.ok and "Automation access" in denied_result.response_text, denied_result.response_text

    renamed_model = bridge()
    renamed = _run('rename my Apple note "Reading list" to "Books"', session_id=session + "c")
    assert renamed.ok and '"Books"' in renamed.response_text, renamed.response_text
    assert renamed_model.notes["id-reading"]["title"] == "Books"


def test_apple_note_delete_requires_confirmation_and_reports_missing(home, bridge):
    """CONTROL: deletion without confirmation only asks; with it, the resolved target is deleted
    (Recently Deleted retention stated); a note that does not exist is not found, not deleted."""
    session = "apple-delete"
    model = bridge()
    first = _run('delete my Apple note "Standups"', session_id=session)
    assert not first.ok and "confirm" in first.response_text.lower(), first.response_text
    assert not any("delete theNote" in script for script in model.scripts), "nothing reached Notes before confirmation"

    confirmed = _run('yes, delete my Apple note "Standups"', session_id=session)
    assert confirmed.ok and "Recently Deleted" in confirmed.response_text, confirmed.response_text
    assert "id-standups" not in model.notes

    missing = _run('yes, delete my Apple note "Nope"', session_id=session)
    assert not missing.ok and "no note" in missing.response_text.lower(), missing.response_text
