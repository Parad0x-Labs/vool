"""pa_beta_gate -- revision-7 restart probes: in a fresh process a receipt answers a replay only when it is this request's.

Each probe runs the delivery and the replay in separate spawned interpreters. Nothing carries over between them but
the workspace files. The first child delivers one explicit request through the native double and exits. The parent
may then damage the recorded receipt the way an earlier writer could: the review's shapes and new identity swaps. A
second fresh child replays the same request (same session and task). Every child appends a line to a dispatch log
on each native call and writes its answer to a file, so dispatch counts and answers are read from disk.

LABELLED: the native bridge in every child is a runner double behind ``apple_notes.create_apple_note``; nothing runs
osascript or touches the Notes app.
"""
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.operator import notes

_SESSION = "v7-restart-probe"
_TITLE, _BODY = "Anchor chain inspection", "shackle 4 pin replaced and seized"
_TEXT = f'save a note to Apple Notes titled "{_TITLE}" with: {_BODY}'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(root))
    return root


def _child_request(text: str, task: str, dispatch_log: str, answer_file: str) -> None:
    """One request in a fresh interpreter: deliver or replay it, then write the answer to disk."""
    from core.operator import apple_notes as bridge_module
    from core.operator import notes as notes_module
    from core.operator.models import OperatorActionIntent

    def runner(command, **kwargs):  # LABELLED native double: records the dispatch, never osascript
        with open(dispatch_log, "a", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()} {task}\n")
        return SimpleNamespace(returncode=0, stdout=f"note id x-coredata://v7-restart/p{os.getpid()}", stderr="")

    real = bridge_module.create_apple_note
    bridge_module.create_apple_note = lambda **kw: real(**kw, runner=runner)
    result = notes_module.handle_save_note(
        OperatorActionIntent(kind="save_note", raw_text=text), task_id=task, session_id=_SESSION,
        evaluate_local_action_fn=lambda *a, **k: SimpleNamespace(mode="execute"), audit_log_fn=lambda *a, **k: None,
    )
    answer = {"pid": os.getpid(), "ok": result.ok, "status": result.status, "text": result.response_text, "details": result.details}
    Path(answer_file).write_text(json.dumps(answer, default=str), encoding="utf-8")


def _in_fresh_process(tmp_path, name: str, text: str, task: str) -> dict:
    ctx = multiprocessing.get_context("spawn")
    answer_file = tmp_path / f"answer-{name}.json"
    child = ctx.Process(target=_child_request, args=(text, task, str(tmp_path / "dispatch.log"), str(answer_file)))
    child.start()
    child.join(90)
    assert child.exitcode == 0, (name, child.exitcode)
    return json.loads(answer_file.read_text(encoding="utf-8"))


def _dispatches(tmp_path) -> list[str]:
    path = tmp_path / "dispatch.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _preserved(workspace) -> list[bytes]:
    return [path.read_bytes() for path in sorted((workspace / "notes").glob(".apple-notes-effects.quarantine-*"))]


def test_fresh_process_replays_a_whole_receipt_without_sending(workspace, tmp_path):
    delivered = _in_fresh_process(tmp_path, "deliver", _TEXT, "anchor-original")
    assert delivered["ok"] and len(_dispatches(tmp_path)) == 1, delivered
    replay = _in_fresh_process(tmp_path, "replay", _TEXT, "anchor-original")
    assert replay["pid"] != delivered["pid"]
    assert replay["ok"] and replay["details"]["replayed"], replay
    assert replay["details"]["note_reference"] == delivered["details"]["note_reference"]
    assert len(_dispatches(tmp_path)) == 1, "the fresh process answered from the receipt and sent nothing"


def _flag_legacy_without_reference(op: dict) -> None:
    op["legacy"] = True
    del op["note_reference"]


def _record_content_not_requested(op: dict) -> None:
    op["content_key"] = "a" * 64


def _claim_another_folder_without_request_identity(op: dict) -> None:
    op["content_key"] = notes._apple_note_effect_key(title=_TITLE, body=_BODY, account="", folder="Personal")
    op["folder"] = "Personal"
    del op["session_id"], op["task_id"]


def _name_another_task(op: dict) -> None:
    op["task_id"] = "anchor-another-turn"


_RESTART_DAMAGE = [
    pytest.param(_flag_legacy_without_reference, "flagged legacy", id="review-shape-legacy-flag-without-reference"),
    pytest.param(_record_content_not_requested, "does not derive its id", id="review-shape-content-key-not-requested"),
    pytest.param(_claim_another_folder_without_request_identity, "different content than this request",
                 id="novel-destination-swap-without-request-identity"),
    pytest.param(_name_another_task, "does not derive its id", id="novel-task-swap"),
]


@pytest.mark.parametrize(("damage", "named"), _RESTART_DAMAGE)
def test_fresh_process_refuses_a_receipt_that_is_not_this_request(workspace, tmp_path, damage, named):
    delivered = _in_fresh_process(tmp_path, "deliver", _TEXT, "anchor-original")
    assert delivered["ok"] and len(_dispatches(tmp_path)) == 1, delivered

    path = workspace / "notes" / ".apple-notes-effects.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    [op] = document["operations"].values()
    damage(op)
    raw = json.dumps(document, sort_keys=True, indent=2).encode("utf-8")
    path.write_bytes(raw)

    replay = _in_fresh_process(tmp_path, "replay", _TEXT, "anchor-original")
    assert replay["pid"] != delivered["pid"]
    assert not replay["ok"] and replay["status"] == "effect_journal_quarantined" and named in replay["text"], replay
    assert len(_dispatches(tmp_path)) == 1, "nothing was sent again"
    assert _preserved(workspace) == [raw]

    for preserved in (workspace / "notes").glob(".apple-notes-effects.quarantine-*"):
        preserved.unlink()  # the owner's documented resume step, after checking Apple Notes
    after_check = _in_fresh_process(tmp_path, "after-owner-check", _TEXT, "anchor-after-owner-check")
    assert after_check["ok"] and len(_dispatches(tmp_path)) == 2, after_check
