"""pa_beta_gate -- revision-6: an Apple Notes delivery receipt counts only when its record is whole.

The independent review of revision 5 (originals in ``test_calendar_v5_independent_review.py``) showed a v2
journal whose confirmed operation had no note_reference, or a mapping in its place, replay as a delivered
note: journal validation checked structure only and the reference was rebuilt with str(). The cases here
are other semantic damage at the same class -- in v2 operations and in revision-4 entries -- plus the
controls that must keep working: a whole confirmed receipt replays without sending, a proven refusal
permits one new request, revision-4 entries (including a receipt without a reference) import by meaning,
a failed move-aside fails closed with the bytes in place, and a quarantine keeps what a rollback reader
needs so it never re-sends the damaged contents.

LABELLED: the native bridge is a runner double behind ``apple_notes.create_apple_note``; nothing runs
osascript or touches the Notes app.
"""
from __future__ import annotations

import json
import os

import pytest

from core.operator import notes
from tests.pa_beta_gate.test_v5_notes_delivery_operations import _revision4_delivery
from tests.pa_beta_gate.test_v6_native_notes_outcome_certainty import _SESSION, ScriptedBridge, _journal_file, _save

_TITLE, _BODY = "Pressure vessel log", "valve 3 lifted at 10.2 bar"
_TEXT = f'save a note to Apple Notes titled "{_TITLE}" with: {_BODY}'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(root))
    return root


def _key(title=_TITLE, body=_BODY):
    return notes._apple_note_effect_key(title=title, body=body, account="", folder="")


def _op(*, task, state, content=(_TITLE, _BODY), session=_SESSION, **fields):
    key = _key(*content)
    op_id = notes._apple_note_operation_id(session_id=session, task_id=task, content_key=key)
    return op_id, {"op_id": op_id, "content_key": key, "state": state, **fields}


def _write(root, document):
    path = _journal_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document).encode("utf-8")
    path.write_bytes(raw)
    return raw


def _v2(*ops):
    return {"schema": notes._JOURNAL_SCHEMA, "operations": dict(ops)}


def _preserved(root):
    return [path.read_bytes() for path in sorted((root / "notes").glob(".apple-notes-effects.quarantine-*"))]


# ---------------------------------------------------------------------------------------------
# Semantic damage: quarantined byte for byte, never an answer, delivery paused
# ---------------------------------------------------------------------------------------------

_DAMAGED = [
    pytest.param("confirmed", {"note_reference": ""}, "note_reference", id="confirmed-empty-reference"),
    pytest.param("confirmed", {"note_reference": ["note id x-coredata://v6/p1"]}, "note_reference", id="confirmed-reference-in-a-list"),
    pytest.param("confirmed", {"note_reference": 17}, "note_reference", id="confirmed-numeric-reference"),
    pytest.param("dispatching", {}, "attempt", id="reservation-without-attempt"),
    pytest.param("dispatching", {"attempt": {"token": "a1"}}, "attempt", id="reservation-attempt-not-text"),
    pytest.param("refused", {"reason": {"code": -1743}}, "reason", id="refusal-reason-not-text"),
    pytest.param("ambiguous", {"owner": "pid 4242"}, "owner", id="owner-not-an-object"),
    pytest.param("confirmed", {"note_reference": "note id x-coredata://v6/p2", "legacy": "yes"}, "legacy", id="legacy-flag-not-true-or-false"),
    pytest.param("confirmed", {"note_reference": "note id x-coredata://v6/p3", "title": ["Pressure vessel log"]}, "title", id="title-not-text"),
]


@pytest.mark.parametrize(("state", "fields", "field"), _DAMAGED)
def test_semantically_damaged_operation_is_quarantined_and_never_answers(workspace, monkeypatch, state, fields, field):
    raw = _write(workspace, _v2(_op(task="vessel-original", state=state, **fields)))
    bridge = ScriptedBridge(monkeypatch, [])
    replay = _save(_TEXT, task="vessel-original")
    assert not replay.ok and replay.status == "effect_journal_quarantined" and bridge.calls == 0, replay.response_text
    assert field in replay.response_text, "the answer names what is damaged"
    assert _preserved(workspace) == [raw]
    fresh = _save(_TEXT, task="vessel-new-request")
    assert fresh.status == "effect_journal_quarantined" and bridge.calls == 0, "delivery stays paused while the damaged receipt is preserved"


def test_damaged_revision4_reference_or_reason_is_quarantined_not_rebuilt_as_text(workspace, monkeypatch):
    bridge = ScriptedBridge(monkeypatch, [])
    raw = _write(workspace, {_key(): {"status": "executed", "note_reference": {"id": "p4"}, "title": _TITLE}})
    assert _save(_TEXT, task="after-upgrade").status == "effect_journal_quarantined" and bridge.calls == 0
    assert _preserved(workspace) == [raw]


def test_damaged_revision4_refusal_reason_is_quarantined(workspace, monkeypatch):
    bridge = ScriptedBridge(monkeypatch, [])
    raw = _write(workspace, {_key(): {"status": "failed", "reason": ["os_permission_denied"], "detail": "Automation refused"}})
    assert _save(_TEXT, task="after-upgrade").status == "effect_journal_quarantined" and bridge.calls == 0
    assert _preserved(workspace) == [raw]


def test_damaged_receipt_that_cannot_be_moved_aside_fails_closed_with_its_bytes_in_place(workspace, monkeypatch):
    raw = _write(workspace, _v2(_op(task="vessel-original", state="confirmed")))
    real_replace = os.replace

    def refuse_journal_move(src, dst, *args, **kwargs):
        if str(src).endswith(".apple-notes-effects.json"):
            raise PermissionError("synthetic: the journal cannot be moved aside")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", refuse_journal_move)
    bridge = ScriptedBridge(monkeypatch, [])
    result = _save(_TEXT, task="vessel-original")
    assert result.status == "effect_journal_unwritable" and bridge.calls == 0, result.response_text
    assert _journal_file(workspace).read_bytes() == raw


@pytest.mark.parametrize("truncated", [pytest.param(False, id="decodable-bytes"), pytest.param(True, id="truncated-bytes")])
def test_quarantine_leaves_a_rollback_reader_nothing_to_resend(workspace, monkeypatch, truncated):
    other_title, other_body = "Relief valve spares", "order two 3/4 inch seats"
    damaged = _v2(
        _op(task="vessel-original", state="confirmed"),
        _op(task="spares-original", state="dispatching", content=(other_title, other_body)),
    )
    raw = _write(workspace, damaged)
    if truncated:
        raw = raw[:-12]
        _journal_file(workspace).write_bytes(raw)
    bridge = ScriptedBridge(monkeypatch, [])
    assert _save(_TEXT, task="vessel-original").status == "effect_journal_quarantined" and bridge.calls == 0
    assert _preserved(workspace) == [raw]

    rollback_view = json.loads(_journal_file(workspace).read_text(encoding="utf-8"))
    for title, body in ((_TITLE, _BODY), (other_title, other_body)):
        held = _revision4_delivery(rollback_view, title=title, body=body, account="", folder="", bridge_create=lambda **kw: {"ok": True, "note_reference": "resent"})
        assert not held["ok"] and held["replayed"], f"a rollback reader would re-send {title!r}"

    for path in (workspace / "notes").glob(".apple-notes-effects.quarantine-*"):
        path.unlink()  # the owner's explicit resume step, after checking Apple Notes
    resumed = _save(_TEXT, task="vessel-after-owner-check")
    assert resumed.ok and bridge.calls == 1, resumed.response_text


# ---------------------------------------------------------------------------------------------
# Controls: whole receipts, proven refusals and revision-4 entries keep their meaning
# ---------------------------------------------------------------------------------------------


def test_whole_confirmed_receipt_replays_its_reference_without_sending(workspace, monkeypatch):
    _write(workspace, _v2(_op(task="vessel-original", state="confirmed", note_reference="note id x-coredata://v6/p9", title=_TITLE,
                              completed_at="2026-09-14T09:00:00+00:00", attempt="a" * 32, owner={"pid": 1})))
    bridge = ScriptedBridge(monkeypatch, [])
    replay = _save(_TEXT, task="vessel-original")
    assert replay.ok and replay.details["note_reference"] == "note id x-coredata://v6/p9" and bridge.calls == 0, replay.response_text


def test_whole_refusal_record_permits_exactly_one_new_request(workspace, monkeypatch):
    _write(workspace, _v2(_op(task="vessel-denied", state="refused", reason="os_permission_denied", attempt="b" * 32,
                              detail="macOS refused Automation access to Notes. No note was created.", completed_at="2026-09-14T09:00:00+00:00")))
    bridge = ScriptedBridge(monkeypatch, [])
    assert _save(_TEXT, task="vessel-denied").status == "os_permission_denied" and bridge.calls == 0
    after_consent = _save(_TEXT, task="vessel-after-consent")
    assert after_consent.ok and bridge.calls == 1, after_consent.response_text


@pytest.mark.parametrize("entry", [
    pytest.param({"status": "executed", "title": _TITLE}, id="v4-receipt-without-reference"),
    pytest.param({"status": "executed", "note_reference": "note id x-coredata://legacy/p4", "title": _TITLE}, id="v4-receipt-with-reference"),
    pytest.param({"status": "failed", "reason": "os_permission_denied", "detail": "Automation refused"}, id="v4-proven-refusal"),
])
def test_revision4_entries_import_by_meaning_and_survive_the_next_load(workspace, monkeypatch, entry):
    _write(workspace, {_key(): entry})
    bridge = ScriptedBridge(monkeypatch, [])
    unrelated = _save('save a note to Apple Notes titled "Boiler room keys" with: spare set in cabinet B', task="unrelated")
    assert unrelated.ok, unrelated.response_text  # this save writes the imported entry back as a v2 legacy operation
    again = _save(_TEXT, task="after-upgrade")
    assert again.ok and bridge.calls == 2 and not _preserved(workspace), again.response_text
