"""pa_beta_gate -- revision-6: a Notes bridge failure is a refusal only when nothing can have reached Notes.

The independent review of revision 5 (originals in ``test_calendar_v5_independent_review.py``) showed a
runner that created the note and then failed to decode its completion, and one terminated after
creating it, both reported as ``osascript_failed`` -- "no note was created" -- after which a fresh
explicit request created a second note. The cases here are new data at the same failure class, plus the
controls that must keep working: a real -1743 denial, a missing executable, an unaddressable app or
folder, a script osascript refuses to compile, same-request replay, and an intentional new request after
a confirmed creation.

LABELLED: every native call goes through ``apple_notes.create_apple_note`` with an injected runner double
that records each call and each note it pretends to create; nothing here runs osascript or touches the
Notes app.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.operator import apple_notes, notes
from core.operator.models import OperatorActionIntent

_SESSION = "v6-notes-outcome"


def _save(text: str, *, task: str):
    return notes.handle_save_note(
        OperatorActionIntent(kind="save_note", raw_text=text), task_id=task, session_id=_SESSION,
        evaluate_local_action_fn=lambda *a, **k: SimpleNamespace(mode="execute"), audit_log_fn=lambda *a, **k: None,
    )


def _journal_file(root: Path) -> Path:
    return root / "notes" / ".apple-notes-effects.json"


def _operations(root: Path) -> list[dict]:
    path = _journal_file(root)
    return list(json.loads(path.read_text(encoding="utf-8"))["operations"].values()) if path.exists() else []


def _completed(returncode: int, stderr: str = "", stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stderr=stderr, stdout=stdout)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(root))
    return root


class ScriptedBridge:
    """Runner double: each scripted answer runs once. ``created`` counts notes the double made."""

    def __init__(self, monkeypatch, answers):
        self.calls = 0
        self.created = 0
        self.answers = list(answers)
        real = apple_notes.create_apple_note
        monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kw: real(**kw, runner=self.run))

    def run(self, command, **kwargs):
        assert command[:2] == ["/usr/bin/osascript", "-e"], command
        self.calls += 1
        if self.answers:
            effect, outcome = self.answers.pop(0)
        else:
            effect, outcome = "create", _completed(0, stdout=f"note id x-coredata://v6/p{self.calls}")
        if effect == "create":
            self.created += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


# ---------------------------------------------------------------------------------------------
# A failure after the bridge started is not evidence that nothing was created
# ---------------------------------------------------------------------------------------------

_AFTER_LAUNCH = [
    pytest.param(_completed(1, "execution error: Notes got an error: AppleEvent timed out. (-1712)"), "apple_event_timeout", id="apple-event-timeout"),
    pytest.param(_completed(-15), "bridge_terminated", id="terminated-by-sigterm"),
    pytest.param(_completed(1, "execution error: Notes got an error: An error of type -10000 has occurred. (-10000)"), "bridge_outcome_unknown", id="unrecognised-exit"),
    pytest.param(OSError(5, "Input/output error"), "bridge_outcome_unknown", id="completion-read-error"),
    pytest.param(BrokenPipeError(32, "Broken pipe"), "bridge_outcome_unknown", id="broken-pipe"),
]


@pytest.mark.parametrize(("failure", "reason"), _AFTER_LAUNCH)
def test_failure_after_the_bridge_started_stays_unresolved_and_blocks_a_new_request(workspace, monkeypatch, failure, reason):
    bridge = ScriptedBridge(monkeypatch, [("create", failure)])
    text = 'save a note to Apple Notes titled "Hydrant flush schedule" with: north loop every second Tuesday'
    first = _save(text, task="hydrant-original")
    assert not first.ok and first.status == reason and first.details["delivery_state"] == "unresolved", first.response_text
    assert "no note was created" not in first.response_text.lower(), first.response_text
    assert [op["state"] for op in _operations(workspace)] == ["ambiguous"]

    follow_up = _save(text, task="hydrant-explicit-follow-up")
    assert follow_up.status == "earlier_attempt_unresolved", follow_up.response_text
    replay = _save(text, task="hydrant-original")
    assert replay.status == reason and replay.details["delivery_state"] == "unresolved", replay.response_text
    assert (bridge.calls, bridge.created) == (1, 1), "exactly one native attempt; nothing was sent again"


def test_launch_error_that_names_another_file_is_not_proof_the_script_never_ran(workspace, monkeypatch):
    bridge = ScriptedBridge(monkeypatch, [("create", PermissionError(13, "Permission denied", "/private/tmp/notes-bridge-pipe"))])
    text = 'save a note to Apple Notes titled "Boiler room keys" with: spare set moved to cabinet B'
    first = _save(text, task="keys-original")
    assert first.status == "bridge_outcome_unknown" and first.details["delivery_state"] == "unresolved", first.response_text
    assert _save(text, task="keys-follow-up").status == "earlier_attempt_unresolved"
    assert bridge.calls == 1


# ---------------------------------------------------------------------------------------------
# Proven refusals keep their recovery: one new attempt on a new explicit request
# ---------------------------------------------------------------------------------------------

_PROVEN_REFUSALS = [
    pytest.param(_completed(1, "execution error: Not authorized to send Apple events to Notes. (-1743)"), "os_permission_denied", id="automation-denied"),
    pytest.param(_completed(1, "execution error: Application can’t be found. (-2700)"), "notes_app_unavailable", id="app-not-found-typographic"),
    pytest.param(_completed(1, 'execution error: Notes got an error: Can’t get folder "Field reports" of default account. (-1728)'), "notes_app_unavailable", id="folder-not-found-typographic"),
    pytest.param(_completed(1, "syntax error: Expected end of line but found identifier. (-2741)"), "notes_script_rejected", id="script-not-compiled"),
    pytest.param(FileNotFoundError(2, "No such file or directory", "/usr/bin/osascript"), "osascript_unavailable", id="executable-missing"),
]


@pytest.mark.parametrize(("refusal", "reason"), _PROVEN_REFUSALS)
def test_proven_refusal_permits_exactly_one_new_attempt(workspace, monkeypatch, refusal, reason):
    bridge = ScriptedBridge(monkeypatch, [("none", refusal), ("create", _completed(0, stdout="note id x-coredata://v6/p-after-repair"))])
    text = 'save a note to Apple Notes titled "Generator fuel log" with: tank two at 40 percent'
    first = _save(text, task="fuel-before-repair")
    assert not first.ok and first.status == reason and first.details["delivery_state"] == "refused", first.response_text
    assert "no note was created" in first.response_text.lower(), first.response_text
    replay = _save(text, task="fuel-before-repair")
    assert replay.status == reason and bridge.calls == 1, "a replay never retries by itself"
    second = _save(text, task="fuel-after-repair")
    assert second.ok and second.details["note_reference"] == "note id x-coredata://v6/p-after-repair", second.response_text
    assert (bridge.calls, bridge.created) == (2, 1)
    assert sorted(op["state"] for op in _operations(workspace)) == ["confirmed", "refused"]


def test_intentional_new_request_after_a_confirmed_note_creates_its_own(workspace, monkeypatch):
    bridge = ScriptedBridge(monkeypatch, [])
    text = 'save a note to Apple Notes titled "Crane inspection" with: hook latch replaced'
    assert _save(text, task="crane-first").ok
    again = _save(text, task="crane-second-on-purpose")
    assert again.ok and "An earlier, separate request had already created" in again.response_text, again.response_text
    assert (bridge.calls, bridge.created) == (2, 2)


def test_default_runner_sends_nothing_when_osascript_is_not_executable(monkeypatch):
    import subprocess

    def launched(*args, **kwargs):
        raise AssertionError("the bridge must not launch anything when osascript is not executable")

    monkeypatch.setattr(apple_notes, "_executable", lambda path: False)
    monkeypatch.setattr(subprocess, "run", launched)
    outcome = apple_notes.create_apple_note(title="Harbour pilot roster", body="night shift swaps")
    assert (outcome["ok"], outcome["reason"]) == (False, "osascript_unavailable")
    assert "nothing was sent" in outcome["detail"]


@pytest.mark.parametrize(("completion", "reason", "proven_unsent"), [
    (_completed(1, "Not authorized to send Apple events to Notes. (-1743)"), "os_permission_denied", True),
    (_completed(1, "Notes got an error: AppleEvent timed out. (-1712)"), "apple_event_timeout", False),
    (_completed(-9), "bridge_terminated", False),
    (_completed(1, ""), "bridge_outcome_unknown", False),
    (_completed(0, stdout=""), "unverified", False),
    (SimpleNamespace(stdout="note id x-coredata://v6/p9", stderr=""), "bridge_outcome_unknown", False),
])
def test_bridge_level_classification_matches_the_delivery_contract(completion, reason, proven_unsent):
    outcome = apple_notes.create_apple_note(title="Fence line survey", body="posts 14-22 leaning", runner=lambda *a, **k: completion)
    assert (outcome["ok"], outcome["reason"]) == (False, reason)
    assert ("no note was created" in outcome["detail"].lower()) is proven_unsent, outcome["detail"]
    assert (reason in notes._KNOWN_UNSENT_REASONS) is proven_unsent


# ---------------------------------------------------------------------------------------------
# Records written by earlier builds: a refusal counts only with unsent evidence
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("layout", "reason", "held_back"), [
    pytest.param("v2-operation", "osascript_failed", True, id="v2-refusal-without-evidence"),
    pytest.param("v4-entry", "osascript_failed", True, id="v4-failure-without-evidence"),
    pytest.param("v2-operation", "os_permission_denied", False, id="v2-proven-denial"),
    pytest.param("v4-entry", "os_permission_denied", False, id="v4-proven-denial"),
])
def test_recorded_refusal_counts_only_with_unsent_evidence(workspace, monkeypatch, layout, reason, held_back):
    title, body = "Tide gauge service", "sensor swapped at pier 3"
    key = notes._apple_note_effect_key(title=title, body=body, account="", folder="")
    if layout == "v2-operation":
        op_id = notes._apple_note_operation_id(session_id=_SESSION, task_id="earlier-build-task", content_key=key)
        document = {"schema": notes._JOURNAL_SCHEMA, "operations": {op_id: {
            "op_id": op_id, "content_key": key, "state": "refused", "reason": reason, "title": title,
            "detail": "the Notes bridge failed (exit 1); no note was created.", "completed_at": "2026-09-13T10:00:00+00:00"}}}
    else:
        document = {key: {"status": "failed", "reason": reason, "detail": "the Notes bridge failed; no note was created."}}
    _journal_file(workspace).parent.mkdir(parents=True)
    _journal_file(workspace).write_text(json.dumps(document), encoding="utf-8")
    bridge = ScriptedBridge(monkeypatch, [])
    result = _save(f'save a note to Apple Notes titled "{title}" with: {body}', task="after-upgrade")
    if held_back:
        assert result.status == "earlier_attempt_unresolved" and bridge.calls == 0, result.response_text
    else:
        assert result.ok and bridge.calls == 1, result.response_text


def test_reinterpreted_refusal_keeps_what_the_earlier_build_recorded():
    key, op_id = "a" * 64, "op-" + "b" * 40
    data = {"schema": notes._JOURNAL_SCHEMA, "operations": {op_id: {
        "op_id": op_id, "content_key": key, "state": "refused", "reason": "osascript_failed", "detail": "exit 1; no note was created."}}}
    op = notes._normalize_journal(data)["operations"][op_id]
    assert (op["state"], op["reclassified_from"], op["recorded_detail"]) == ("ambiguous", "refused", "exit 1; no note was created.")
    assert "not established" in op["detail"]
