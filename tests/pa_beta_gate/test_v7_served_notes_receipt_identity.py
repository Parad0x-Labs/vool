"""pa_beta_gate -- revision-7 served receipts: a resumed Apple Notes request answers only from its own whole receipt.

Real agent turns (``VoolAgent.run_once``) through the production conversational path, in ONE session per test. A
Notes save has no approval pause in this runtime: the local-action gate evaluates it as a non-destructive, explicitly
requested action and the operator lane dispatches it. The served path that reaches the replay owner is the runtime's
own checkpoint resume. The save is delivered, the turn stops before its answer is assembled, the runtime files the
checkpoint as interrupted, and the user's "resume" re-runs the stored request under the SAME task, which is the same
delivery operation. Between the stop and the resume, an earlier writer's damage is applied to that operation's
receipt: the review's shapes with the review's data (original), and identity swaps with new data (novel).

MODEL STAND-IN (labelled): the deterministic stand-in the served workflow tests use. SYNTHETIC FAULT (labelled): one
RuntimeError raised right after the operator step returned. NATIVE DOUBLE (labelled): every Notes call goes through a
runner double behind ``apple_notes.create_apple_note``, never osascript. Every test counts native dispatches, reads the
journal, the checkpoint and the preserved bytes, and keeps a valid Notes flow beside the refusals.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.operator import notes
from tests.pa_beta_gate.test_served_calendar_notes_workflows import (
    _turn,
    served_env,
)
from tests.pa_beta_gate.test_v6_served_journal_semantics import _Bridge
from tests.pa_beta_gate.test_v6_served_outcome_recovery import _note_texts

_REVIEW_BODY = "retain the delivery receipt"


class _StopAfterOperatorStep:
    """SYNTHETIC FAULT: when armed, the next turn raises right after the operator lane returned its result."""

    def __init__(self, monkeypatch, agent):
        self.armed = False
        self.fired = 0
        real = agent._action_workflow_summary

        def summary(*args, **kwargs):
            if self.armed:
                self.armed = False
                self.fired += 1
                raise RuntimeError("synthetic stop after the operator step")
            return real(*args, **kwargs)

        monkeypatch.setattr(agent, "_action_workflow_summary", summary)


def _journal_path(workspace) -> Path:
    return Path(workspace) / "notes" / ".apple-notes-effects.json"


def _operation_titled(workspace, title: str) -> dict:
    operations = json.loads(_journal_path(workspace).read_text(encoding="utf-8"))["operations"].values()
    [op] = [op for op in operations if op.get("title") == title]
    return op


def _delivered_then_stopped(harness, workspace, fault, text: str, title: str) -> dict:
    """The save is delivered, the turn stops before its answer, and the runtime leaves a resumable checkpoint."""
    from core.runtime_continuity import latest_resumable_checkpoint

    fault.armed = True
    with pytest.raises(RuntimeError, match="synthetic stop after the operator step"):
        _turn(harness, text, workspace)
    op = _operation_titled(workspace, title)
    checkpoint = latest_resumable_checkpoint(op["session_id"])
    assert op["state"] == "confirmed" and checkpoint and checkpoint["status"] == "interrupted", (op, checkpoint)
    assert checkpoint["task_id"] == op["task_id"], "the interrupted checkpoint holds the task whose delivery was recorded"
    return op


def _damage(workspace, title: str, change) -> bytes:
    """An earlier writer's damage to one recorded receipt; returns the exact bytes left on disk."""
    path = _journal_path(workspace)
    document = json.loads(path.read_text(encoding="utf-8"))
    [op] = [op for op in document["operations"].values() if op.get("title") == title]
    change(op)
    raw = json.dumps(document, sort_keys=True, indent=2).encode("utf-8")
    path.write_bytes(raw)
    return raw


def _preserved(workspace) -> list[bytes]:
    return [path.read_bytes() for path in sorted((Path(workspace) / "notes").glob(".apple-notes-effects.quarantine-*"))]


def _owner_checks_notes_and_resumes_delivery(workspace) -> None:
    for path in (Path(workspace) / "notes").glob(".apple-notes-effects.quarantine-*"):
        path.unlink()  # the owner's documented resume step, after checking Apple Notes


def test_served_resume_with_the_review_data_answers_only_from_a_whole_receipt(request, monkeypatch):
    """ORIGINAL data served: the review's titles, body and receipt damage, reached through checkpoint resume."""
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    bridge = _Bridge(monkeypatch)
    fault = _StopAfterOperatorStep(monkeypatch, harness.agent)

    # Valid flow: the review's control receipt replays through the resume; nothing is sent twice.
    _delivered_then_stopped(harness, workspace, fault, f'save a note to Apple Notes titled "Valid inspection" with: {_REVIEW_BODY}', "Valid inspection")
    assert bridge.calls == 1
    resumed = _turn(harness, "resume", workspace)
    assert resumed.startswith("Note saved in Apple Notes: 'Valid inspection'") and "answered from its recorded receipt" in resumed, resumed
    assert bridge.calls == 1

    # Review finding 1: the receipt later carries the legacy flag and a whitespace reference.
    _delivered_then_stopped(harness, workspace, fault, f'save a note to Apple Notes titled "Harbour inspection" with: {_REVIEW_BODY}', "Harbour inspection")
    assert bridge.calls == 2
    raw = _damage(workspace, "Harbour inspection", lambda op: op.update(legacy=True, note_reference="   "))
    refused = _turn(harness, "resume", workspace)
    assert "destination is NOT resolved (effect_journal_quarantined)" in refused and "flagged legacy" in refused, refused
    assert "Note saved in Apple Notes" not in refused and bridge.calls == 2 and _preserved(workspace) == [raw]
    assert any(_REVIEW_BODY in text for text in _note_texts(workspace)), "the labelled workspace fallback was written"

    # Review finding 2, after the owner's check: the receipt records content that is not the request's.
    _owner_checks_notes_and_resumes_delivery(workspace)
    _delivered_then_stopped(harness, workspace, fault, f'save a note to Apple Notes titled "Warehouse key register" with: {_REVIEW_BODY}', "Warehouse key register")
    assert bridge.calls == 3
    raw = _damage(workspace, "Warehouse key register", lambda op: op.update(content_key="a" * 64))
    refused = _turn(harness, "resume", workspace)
    assert "destination is NOT resolved (effect_journal_quarantined)" in refused and "does not derive its id" in refused, refused
    assert "Note saved in Apple Notes" not in refused and bridge.calls == 3 and _preserved(workspace) == [raw]
    assert fault.fired == 3


def test_served_resume_with_novel_identity_swaps_refuses_and_keeps_a_valid_flow(request, monkeypatch):
    """NOVEL data served: a destination swap on a receipt without request identity (seen only at replay selection),
    a task swap (seen on load), and an older-shape receipt of the same request that still replays."""
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    bridge = _Bridge(monkeypatch)
    fault = _StopAfterOperatorStep(monkeypatch, harness.agent)

    title, body = "Cold store log", "freezer 2 held at minus 21 overnight"
    op = _delivered_then_stopped(harness, workspace, fault, f'save a note in the Maintenance folder of Apple Notes titled "{title}" with: {body}', title)
    assert op["folder"] == "Maintenance" and bridge.calls == 1

    def claim_another_folder(record):
        record.update(content_key=notes._apple_note_effect_key(title=title, body=body, account="", folder="Personal"), folder="Personal")
        del record["session_id"], record["task_id"]

    raw = _damage(workspace, title, claim_another_folder)
    refused = _turn(harness, "resume", workspace)
    assert "destination is NOT resolved (effect_journal_quarantined)" in refused and "different content than this request" in refused, refused
    assert "Note saved in Apple Notes" not in refused and bridge.calls == 1 and _preserved(workspace) == [raw]

    _owner_checks_notes_and_resumes_delivery(workspace)
    spares = "Cold store spares"
    _delivered_then_stopped(harness, workspace, fault, f'save a note to Apple Notes titled "{spares}" with: order two door gaskets', spares)
    assert bridge.calls == 2
    raw = _damage(workspace, spares, lambda record: record.update(task_id="another-turn-of-this-chat"))
    refused = _turn(harness, "resume", workspace)
    assert "destination is NOT resolved (effect_journal_quarantined)" in refused and "does not derive its id" in refused, refused
    assert "Note saved in Apple Notes" not in refused and bridge.calls == 2 and _preserved(workspace) == [raw]

    # Valid flow after the owner's check: an older record shape of this same request still replays, sending nothing.
    _owner_checks_notes_and_resumes_delivery(workspace)
    service = "Compressor service"
    _delivered_then_stopped(harness, workspace, fault, f'save a note to Apple Notes titled "{service}" with: replace the start relay on unit 4', service)
    assert bridge.calls == 3
    _damage(workspace, service, lambda record: [record.pop("session_id"), record.pop("task_id")])
    resumed = _turn(harness, "resume", workspace)
    assert resumed.startswith(f"Note saved in Apple Notes: '{service}'") and "answered from its recorded receipt" in resumed, resumed
    assert bridge.calls == 3 and fault.fired == 3
