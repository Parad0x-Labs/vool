"""pa_beta_gate -- Apple Notes scoped identity and durable mutation custody (correction repair 2).

Stable account/folder/note identity, a structured listing that preserves commas/newlines/Unicode,
ambiguity refusal, revalidation before mutation, and journal custody so an unresolved append is
never automatically re-attempted. The bridge is exercised through a stateful synthetic Notes model
that speaks the real script protocol (id/name/container addressing, unit/record separators).

LABELLED: synthetic runner standing in for osascript; the generated scripts are additionally
compile-checked against the installed Notes dictionary via osacompile (no notes are opened).
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from ._pc_calendar_rig import prepare_home

pytestmark = [pytest.mark.pa_beta]


class FakeNotes:
    """A stateful Notes model: parses the real scripts the bridge builds and answers them."""

    def __init__(self, notes=None):
        # id -> {title, folder, account, body}
        self.notes = notes or {}
        self.scripts: list[str] = []
        self.timeout_on_append = False

    def install(self, monkeypatch):
        from core.operator import apple_notes

        def fake_bridge(script, *, runner=None, purpose="the Notes request"):
            return self._run(script)

        monkeypatch.setattr(apple_notes, "run_notes_bridge", fake_bridge)
        return self

    def _note_line(self, note_id, note):
        return "\x1f".join([note_id, note["title"], note["folder"], note["account"]]) + "\x1e"

    def _run(self, script):
        self.scripts.append(script)
        import subprocess

        if "make new paragraph" in script and self.timeout_on_append:
            raise subprocess.TimeoutExpired(["osascript"], 5)
        completion = type("C", (), {})
        completed = completion()
        if "repeat with theNote in notes" in script:
            completed.returncode, completed.stderr = 0, ""
            completed.stdout = "".join(self._note_line(i, n) for i, n in self.notes.items())
            return self._ok(completed)
        for needle in ("return body of theNote", "return name of theNote", "make new paragraph",
                       "set name of theNote", "delete theNote"):
            if needle in script:
                note_id = script.split('whose id is "')[1].split('"')[0] if 'whose id is "' in script else None
                if note_id is None and 'whose name is "' in script:
                    wanted = script.split('whose name is "')[1].split('"')[0]
                    matches = [i for i, n in self.notes.items() if n["title"] == wanted]
                    note_id = matches[0] if len(matches) == 1 else None
                if note_id is None or note_id not in self.notes:
                    completed.returncode, completed.stderr = 1, 'error: Can\'t get note. (-1728)'
                    completed.stdout = ""
                    return self._classified(completed)
                if needle == "return body of theNote":
                    completed.returncode, completed.stderr, completed.stdout = 0, "", self.notes[note_id]["body"]
                    return self._ok(completed)
                if needle == "return name of theNote":
                    completed.returncode, completed.stderr, completed.stdout = 0, "", self.notes[note_id]["title"]
                    return self._ok(completed)
                if needle == "make new paragraph":
                    text = script.split('with data "')[1].rsplit('"', 1)[0]
                    self.notes[note_id]["body"] += "\n" + text
                if needle == "set name of theNote":
                    new_title = script.split('set name of theNote to "')[1].rsplit('"', 1)[0]
                    self.notes[note_id]["title"] = new_title
                if needle == "delete theNote":
                    del self.notes[note_id]
                completed.returncode, completed.stderr, completed.stdout = 0, "", "ok"
                return self._ok(completed)
        completed.returncode, completed.stderr, completed.stdout = 1, "error: unhandled script", ""
        return self._classified(completed)

    @staticmethod
    def _ok(completed):
        return {"ok": True, "reason": "", "detail": "", "output": str(completed.stdout or "")}

    @staticmethod
    def _classified(completed):
        from core.operator.apple_notes import _classify

        reason, detail = _classify(str(completed.stderr or ""), int(completed.returncode))
        return {"ok": False, "reason": reason or "bridge_outcome_unknown", "detail": detail}


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed a notes request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _default_notes():
    return {
        "x-coredata://Note/p1": {"title": "Plans, Q4", "folder": "Work", "account": "iCloud", "body": "<div>roadmap</div>"},
        "x-coredata://Note/p2": {"title": "Plans, Q4", "folder": "Personal", "account": "On My Mac", "body": "<div>home</div>"},
        "x-coredata://Note/p3": {"title": "Budget néwpaper–列", "folder": "Work", "account": "iCloud", "body": "<div>numbers</div>"},
    }


def test_structured_listing_preserves_commas_and_unicode(home, monkeypatch):
    """ORIGINAL (probe): 'Plans, Q4' and 'Budget' stay two titles, not three; records carry stable
    id/folder/account; a newline inside a title survives as part of the title."""
    notes = FakeNotes({
        "id-1": {"title": "Plans, Q4", "folder": "Work", "account": "iCloud", "body": ""},
        "id-2": {"title": "Budget\nsummary", "folder": "Work", "account": "iCloud", "body": ""},
    }).install(monkeypatch)
    listed = _run("show my Apple Notes", session_id="identity")
    assert listed.ok, listed.response_text
    titles = [row["title"] for row in listed.details["notes"]]
    assert titles == ["Plans, Q4", "Budget\nsummary"], titles
    assert listed.details["notes"][0]["id"] == "id-1"
    assert "Plans, Q4" in listed.response_text and "Budget" in listed.response_text


def test_same_title_in_two_folders_is_refused_until_scoped(home, monkeypatch):
    """NOVEL: 'Plans, Q4' exists in Work/iCloud and Personal/On My Mac; an unscoped append refuses
    naming both candidates and sends nothing; scoped to the Work folder it appends to exactly that
    note; a folder with no such title is not found."""
    notes = FakeNotes(_default_notes()).install(monkeypatch)
    session = "ambig"
    refused = _run('append to my Apple note "Plans, Q4" with "checked pricing"', session_id=session)
    assert not refused.ok and "2 notes share that title" in refused.response_text, refused.response_text
    assert "Work" in refused.response_text and "Personal" in refused.response_text, refused.response_text
    assert not any("make new paragraph" in s for s in notes.scripts), "nothing was sent while ambiguous"

    scoped = _run('append to my Apple note "Plans, Q4" in the Work folder with "checked pricing"', session_id=session)
    assert scoped.ok, scoped.response_text
    assert "checked pricing" in notes.notes["x-coredata://Note/p1"]["body"]
    assert "checked pricing" not in notes.notes["x-coredata://Note/p2"]["body"]
    assert notes.notes["x-coredata://Note/p2"]["body"] == "<div>home</div>", "the other same-titled note is untouched"

    wrong_folder = _run('append to my Apple note "Plans, Q4" in the Gym folder with "x"', session_id=session + "b")
    assert not wrong_folder.ok and "no note" in wrong_folder.response_text.lower(), wrong_folder.response_text


def test_unresolved_append_is_not_automatically_retried(home, monkeypatch):
    """ORIGINAL (probe, now at the custody boundary): two identical append requests after lost
    replies invoke the bridge exactly once; the second answers unresolved from the journal; after
    the model recovers, a DIFFERENT payload makes its own single new attempt."""
    notes = FakeNotes(_default_notes()).install(monkeypatch)
    notes.timeout_on_append = True
    session = "custody"
    first = _run('append to my Apple note "Budget néwpaper–列" with "line one"', session_id=session)
    assert not first.ok and first.details.get("delivery_state") != "confirmed", first.response_text
    second = _run('append to my Apple note "Budget néwpaper–列" with "line one"', session_id=session)
    assert not second.ok and "unresolved" in str(second.details.get("delivery_state", "")).lower() + second.response_text.lower(), second.response_text
    append_attempts = [s for s in notes.scripts if "make new paragraph" in s]
    assert len(append_attempts) == 1, "the bridge ran once, not twice"

    notes.timeout_on_append = False
    different = _run('append to my Apple note "Budget néwpaper–列" with "line two"', session_id=session)
    assert different.ok, different.response_text
    append_attempts_after = [s for s in notes.scripts if "make new paragraph" in s]
    assert len(append_attempts_after) == 2, "a different payload is its own one attempt"
    assert "line two" in notes.notes["x-coredata://Note/p3"]["body"]


def test_renaming_between_review_and_send_refuses(home, monkeypatch):
    """CONTROL: a resolved note whose CURRENT name no longer matches the reviewed title refuses
    with target_changed and the mutation script is never built; the same id with the same name
    passes revalidation; a missing id is not found."""
    from core.operator import apple_notes

    FakeNotes(_default_notes()).install(monkeypatch)
    ok_check = apple_notes.revalidate_note(note_id="x-coredata://Note/p3", expected_title="Budget néwpaper–列")
    assert ok_check["ok"], ok_check

    renamed = dict(_default_notes())
    renamed["x-coredata://Note/p3"] = {**renamed["x-coredata://Note/p3"], "title": "Renamed by someone else"}
    FakeNotes(renamed).install(monkeypatch)
    changed = apple_notes.revalidate_note(note_id="x-coredata://Note/p3", expected_title="Budget néwpaper–列")
    assert not changed["ok"] and changed["reason"] == "target_changed", changed
    assert "now called" in changed["detail"], changed

    missing = apple_notes.revalidate_note(note_id="x-coredata://Note/gone", expected_title="Anything")
    assert not missing["ok"] and missing["reason"] == "not_found", missing


def test_generated_scripts_compile_against_the_installed_notes_dictionary():
    """The scripts this build generates must compile against the REAL Notes scripting dictionary
    (osacompile resolves application terminology; it does not run the scripts or open notes)."""
    import shutil
    import subprocess as sp

    pytest.importorskip("core.operator.apple_notes")
    if not shutil.which("osacompile"):
        pytest.skip("osacompile is not available on this machine")
    from core.operator.apple_notes import (
        build_append_note_script,
        build_delete_note_script,
        build_list_notes_script,
        build_note_name_script,
        build_read_note_script,
        build_rename_note_script,
    )

    scripts = {
        "list": build_list_notes_script(),
        "read": build_read_note_script(note_id="x-coredata://Note/p1"),
        "name": build_note_name_script(note_id="x-coredata://Note/p1"),
        "append": build_append_note_script(note_id="x-coredata://Note/p1", text='line with "quotes" and, commas'),
        "rename": build_rename_note_script(note_id="x-coredata://Note/p1", new_title="New name"),
        "delete": build_delete_note_script(note_id="x-coredata://Note/p1"),
    }
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, script in scripts.items():
            out = Path(tmp) / f"{name}.scpt"
            try:
                sp.run(["osacompile", "-o", str(out), "-e", script], capture_output=True, text=True, timeout=30, check=True)
            except sp.CalledProcessError as exc:
                failures.append((name, (exc.stderr or "").strip()[:200]))
    assert not failures, failures
