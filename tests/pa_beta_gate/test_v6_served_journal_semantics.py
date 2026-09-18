"""pa_beta_gate -- revision-6 served recovery for contract 4: a damaged delivery receipt never answers.

Real agent turns (``VoolAgent.run_once``) through the production conversational path. MODEL STAND-IN
(labelled): the deterministic stand-in the served workflow tests use. The workspace holds an Apple Notes
delivery journal an earlier writer left semantically damaged; the native bridge is a runner double behind
``apple_notes.create_apple_note`` and never runs osascript. Every test counts native calls, reads the
preserved bytes and the workspace fallback notes, and follows the owner's documented resume step.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.operator import notes
from tests.pa_beta_gate.test_served_calendar_notes_workflows import (  # noqa: F401 -- served_env is a fixture, requested via getfixturevalue
    _turn,
    served_env,
)
from tests.pa_beta_gate.test_v6_served_outcome_recovery import _note_texts


class _Bridge:
    """LABELLED native double: every call creates a note and reports its reference."""

    def __init__(self, monkeypatch):
        from core.operator import apple_notes

        self.calls = 0
        real = apple_notes.create_apple_note
        monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kw: real(**kw, runner=self.run))

    def run(self, command, **kwargs):
        self.calls += 1
        return SimpleNamespace(returncode=0, stdout=f"note id x-coredata://served-journal/p{self.calls}", stderr="")


def _seed_journal(workspace, title: str, body: str, **op_fields) -> bytes:
    """A journal an earlier request's writer left behind for this content (its own session and task)."""
    key = notes._apple_note_effect_key(title=title, body=body, account="", folder="")
    op_id = notes._apple_note_operation_id(session_id="earlier-session", task_id="earlier-task", content_key=key)
    path = Path(workspace) / "notes" / ".apple-notes-effects.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"schema": notes._JOURNAL_SCHEMA, "operations": {op_id: {"op_id": op_id, "content_key": key, **op_fields}}}).encode("utf-8")
    path.write_bytes(raw)
    return raw


def _preserved(workspace) -> list[bytes]:
    return [path.read_bytes() for path in sorted((Path(workspace) / "notes").glob(".apple-notes-effects.quarantine-*"))]


def test_served_confirmed_receipt_without_reference_is_quarantined_until_the_owner_resumes(request, monkeypatch):
    """ORIGINAL data served: the review's confirmed operation with no note_reference."""
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    title, body = "Damaged receipt note_reference", "retain the delivery receipt"
    raw = _seed_journal(workspace, title, body, state="confirmed")
    bridge = _Bridge(monkeypatch)
    text = f'save a note to Apple Notes titled "{title}" with: {body}'

    first = _turn(harness, text, workspace)
    assert first.startswith(f"Note saved: '{title}' at "), first
    assert "destination is NOT resolved (effect_journal_quarantined)" in first and "note_reference" in first, first
    assert bridge.calls == 0 and _preserved(workspace) == [raw]

    again = _turn(harness, text, workspace)
    assert "destination is NOT resolved (effect_journal_quarantined)" in again and bridge.calls == 0, again

    for path in (Path(workspace) / "notes").glob(".apple-notes-effects.quarantine-*"):
        path.unlink()  # the owner's documented resume step, after checking Apple Notes
    resumed = _turn(harness, text, workspace)
    assert resumed.startswith(f"Note saved in Apple Notes: '{title}'"), resumed
    assert bridge.calls == 1


def test_served_reservation_without_its_attempt_token_is_quarantined_not_taken_over(request, monkeypatch):
    """NOVEL data served: an in-flight reservation that lost the attempt token fencing its outcome."""
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    title, body = "Kiln firing schedule", "cone 6 on Thursday night"
    raw = _seed_journal(workspace, title, body, state="dispatching", reserved_at="2026-09-14T08:00:00+00:00")
    bridge = _Bridge(monkeypatch)

    answer = _turn(harness, f'save a note to Apple Notes titled "{title}" with: {body}', workspace)
    assert "destination is NOT resolved (effect_journal_quarantined)" in answer and "attempt" in answer, answer
    assert bridge.calls == 0 and _preserved(workspace) == [raw]
    assert any(body in text for text in _note_texts(workspace)), "the workspace fallback note was still written"
