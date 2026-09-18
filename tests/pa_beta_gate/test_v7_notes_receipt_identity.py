"""pa_beta_gate -- revision-7: an Apple Notes receipt answers only as the record variant it is, for the request it is.

The independent review of revision 6 (originals in ``test_calendar_v6_review.py``) found two receipts that replayed as
delivered notes. A current ``op-`` record carrying ``legacy: true`` skipped the reference requirement. A record under
the requested operation id recorded a different content key. The cases here are new data at the same two boundaries:

* record variants, checked at load: only an imported revision-4 content-only record may be legacy. Its id names its
  content key and the state it was imported in, and it carries no request identity. Any other flagged, legacy-shaped
  or unrecognized id is damage.
* request binding: a current record's recorded session and task, when present, must derive its id from its content
  key (checked at load). The record selected for a replay must match the request's content key and every request
  field it carries (checked at the locked replay selection). Older records carry fewer fields and nothing absent is
  invented.

Controls keep what must keep working: whole receipts with full or older identity replay without sending; revision-4
entries and the legacy records earlier builds persisted keep their meaning through import and reload; an unresolved
sibling still holds back identical content; a new request makes its own attempt; an owner-lost reservation stays
fenced.

LABELLED: the native bridge is a runner double behind ``apple_notes.create_apple_note``; nothing runs osascript or
touches the Notes app.
"""
from __future__ import annotations

import json

import pytest

from core.operator import notes
from tests.pa_beta_gate.test_v6_native_notes_outcome_certainty import _SESSION, ScriptedBridge, _journal_file, _save


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(root))
    return root


def _key(title: str, body: str, account: str = "", folder: str = "") -> str:
    return notes._apple_note_effect_key(title=title, body=body, account=account, folder=folder)


def _request_text(title: str, body: str, *, folder: str = "") -> str:
    if folder:
        return f'save a note in the {folder} folder of Apple Notes titled "{title}" with: {body}'
    return f'save a note to Apple Notes titled "{title}" with: {body}'


def _current(title, body, *, task, session=_SESSION, account="", folder="", state="confirmed", reference="default", **fields):
    """A current operation exactly as this build's reservation and outcome writers leave it."""
    key = _key(title, body, account, folder)
    record = {
        "op_id": notes._apple_note_operation_id(session_id=session, task_id=task, content_key=key), "content_key": key,
        "state": state, "attempt": "c" * 32, "session_id": session, "task_id": task, "title": title, "account": account,
        "folder": folder, "reserved_at": "2026-09-14T08:00:00.000000+00:00", "owner": {"pid": 4242},
        "completed_at": "2026-09-14T08:00:01.000000+00:00", "reason": "", "detail": "",
    }
    if reference == "default":
        reference = f"note id x-coredata://v7/{task}" if state == "confirmed" else None
    if reference is not None:
        record["note_reference"] = reference
    record.update(fields)
    return record


def _legacy(title, body, *, imported_state, **fields):
    """A revision-4 entry as ``_normalize_journal`` imports it and a later save persists it."""
    key = _key(title, body)
    record = {"op_id": f"legacy-{key[:40]}-{imported_state}", "content_key": key, "state": imported_state, "legacy": True,
              "title": title, "note_reference": "", "reason": "", "detail": ""}
    record.update(fields)
    return record


def _without(record, *names):
    return {name: value for name, value in record.items() if name not in names}


def _v2(*records):
    return {"schema": notes._JOURNAL_SCHEMA, "operations": {record["op_id"]: record for record in records}}


def _write(root, document) -> bytes:
    path = _journal_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document).encode("utf-8")
    path.write_bytes(raw)
    return raw


def _preserved(root):
    return [path.read_bytes() for path in sorted((root / "notes").glob(".apple-notes-effects.quarantine-*"))]


# ---------------------------------------------------------------------------------------------
# Record variants: the legacy exemption belongs only to an imported content-only record
# ---------------------------------------------------------------------------------------------

_DOSING = ("Chlorine dosing record", "pump B dosing at 2.5 litres per hour")

_VARIANT_DAMAGE = [
    pytest.param(_current(*_DOSING, task="dosing-original", reference=None, legacy=True), "flagged legacy",
                 id="current-operation-flagged-legacy-without-reference"),
    pytest.param(_current(*_DOSING, task="dosing-original", legacy=True), "flagged legacy",
                 id="current-operation-flagged-legacy-with-its-reference"),
    pytest.param(_legacy(*_DOSING, imported_state="confirmed", op_id=f"legacy-{_key('Chlorine delivery note', 'drum 7 received')[:40]}-confirmed"),
                 "names other content", id="legacy-id-names-other-content"),
    pytest.param(_legacy(*_DOSING, imported_state="refused", state="confirmed", note_reference="note id x-coredata://v7/legacy"),
                 "not the state it was imported in", id="legacy-state-is-not-its-import"),
    pytest.param(_legacy(*_DOSING, imported_state="refused", state="ambiguous", reason="osascript_failed"),
                 "not the state it was imported in", id="legacy-unresolved-without-its-reclassification"),
    pytest.param(_legacy(*_DOSING, imported_state="confirmed", session_id=_SESSION, task_id="dosing-original"),
                 "carrying request identity", id="legacy-record-carrying-session-and-task"),
    pytest.param(_legacy(*_DOSING, imported_state="ambiguous", attempt="d" * 32, owner={"pid": 4242}),
                 "carrying request identity", id="legacy-record-carrying-an-attempt-token"),
    pytest.param(_legacy(*_DOSING, imported_state="confirmed", legacy=False, note_reference="note id x-coredata://v7/legacy-shaped"),
                 "neither a request operation", id="legacy-shaped-id-without-the-flag"),
    pytest.param({**_current(*_DOSING, task="dosing-original"), "op_id": "op-7f3a"}, "neither a request operation",
                 id="operation-id-of-no-known-shape"),
]


@pytest.mark.parametrize(("record", "named"), _VARIANT_DAMAGE)
def test_record_claiming_a_variant_it_is_not_is_quarantined_and_never_answers(workspace, monkeypatch, record, named):
    raw = _write(workspace, _v2(record))
    bridge = ScriptedBridge(monkeypatch, [])
    replay = _save(_request_text(*_DOSING), task="dosing-original")
    assert not replay.ok and replay.status == "effect_journal_quarantined" and bridge.calls == 0, replay.response_text
    assert named in replay.response_text, replay.response_text
    assert _preserved(workspace) == [raw]
    later = _save(_request_text(*_DOSING), task="dosing-after-owner-check")
    assert later.status == "effect_journal_quarantined" and bridge.calls == 0, "delivery stays paused while the damaged record is preserved"


def test_persisted_legacy_records_of_earlier_builds_load_and_keep_their_meaning(workspace, monkeypatch):
    receipt = ("Ferry manifest", "deck cargo 14 vehicles")
    reclassified = ("Ballast report", "tank 3 pumped out")
    proven = ("Crew roster", "weekend shifts from Friday")
    unresolved = ("Harbour fees", "berth 4 invoice")
    revision5_refusal = ("Bilge alarm", "float switch replaced")
    document = _v2(
        _legacy(*receipt, imported_state="confirmed"),  # a revision-4 receipt recorded without a reference
        _legacy(*reclassified, imported_state="refused", state="ambiguous", reclassified_from="refused", reason="osascript_failed",
                recorded_detail="osascript exited 1",
                detail="an earlier build recorded this delivery as refused without evidence that nothing reached Notes;"
                       " whether the note was created is not established."),
        _legacy(*proven, imported_state="refused", reason="os_permission_denied", detail="Automation refused"),
        _legacy(*unresolved, imported_state="ambiguous", reason="delivery_unknown", detail="timed out"),
        _legacy(*revision5_refusal, imported_state="refused", reason="osascript_failed", detail="osascript exited 1"),
    )
    _write(workspace, document)
    bridge = ScriptedBridge(monkeypatch, [])

    assert _save(_request_text(*receipt), task="after-upgrade-receipt").ok and bridge.calls == 1
    held = [_save(_request_text(*pair), task=f"after-upgrade-{index}") for index, pair in enumerate((reclassified, unresolved, revision5_refusal))]
    assert [answer.status for answer in held] == ["earlier_attempt_unresolved"] * 3 and bridge.calls == 1, [answer.response_text for answer in held]
    assert _save(_request_text(*proven), task="after-upgrade-proven").ok and bridge.calls == 2
    assert not _preserved(workspace)

    reloaded = notes._load_apple_note_effects()["operations"]
    legacy = {op_id: op for op_id, op in reloaded.items() if op.get("legacy")}
    assert set(legacy) == set(document["operations"]), "every legacy record survived the saves and the reload"
    assert legacy[f"legacy-{_key(*revision5_refusal)[:40]}-refused"]["state"] == "ambiguous"


def test_revision4_entries_import_persist_and_reload_as_recognized_legacy_records(workspace, monkeypatch):
    receipt = ("Ferry timetable", "winter schedule posted")
    unproven = ("Deck crane check", "hook block greased")
    proven = ("Pilot booking", "arrival confirmed by the agent")
    pending = ("Customs form", "manifest copy for the agent")
    _write(workspace, {
        _key(*receipt): {"status": "executed", "title": receipt[0]},
        _key(*unproven): {"status": "failed", "reason": "osascript_failed", "detail": "osascript exited 1"},
        _key(*proven): {"status": "failed", "reason": "notes_app_unavailable", "detail": "Application can't be found"},
        _key(*pending): {"status": "pending"},
    })
    bridge = ScriptedBridge(monkeypatch, [])
    first = _save(_request_text("Engine room log", "generator 2 on load test"), task="unrelated")
    assert first.ok and bridge.calls == 1, first.response_text  # this save persists the imported entries

    persisted = json.loads(_journal_file(workspace).read_text(encoding="utf-8"))["operations"]
    shapes = {op_id: (op["state"], op.get("reclassified_from", "")) for op_id, op in persisted.items() if op.get("legacy")}
    assert shapes == {
        f"legacy-{_key(*receipt)[:40]}-confirmed": ("confirmed", ""),
        f"legacy-{_key(*unproven)[:40]}-refused": ("ambiguous", "refused"),
        f"legacy-{_key(*proven)[:40]}-refused": ("refused", ""),
        f"legacy-{_key(*pending)[:40]}-ambiguous": ("ambiguous", ""),
    }
    assert _save(_request_text(*unproven), task="after-reload-unproven").status == "earlier_attempt_unresolved"
    assert _save(_request_text(*pending), task="after-reload-pending").status == "earlier_attempt_unresolved"
    assert _save(_request_text(*proven), task="after-reload-proven").ok
    assert _save(_request_text(*receipt), task="after-reload-receipt").ok
    assert bridge.calls == 3 and not _preserved(workspace)


# ---------------------------------------------------------------------------------------------
# Request binding: the selected receipt must be this request's, on load and at selection
# ---------------------------------------------------------------------------------------------

_SLUDGE = ("Sludge tank reading", "level at 62 percent before transfer")
_PUMP = ("Fire pump test", "diesel pump ran 30 minutes at 7 bar")
_SLUDGE_TEXT = _request_text(*_SLUDGE)
_PUMP_TEXT = _request_text(*_PUMP, folder="Maintenance")


def _sludge_receipt(**changes):
    return {**_current(*_SLUDGE, task="sludge-original"), **changes}


def _pump_receipt(**changes):
    return {**_current(*_PUMP, task="pump-original", folder="Maintenance"), **changes}


_IDENTITY = ("session_id", "task_id", "attempt", "owner", "reserved_at", "account", "folder", "title")

_BINDING_DAMAGE = [
    # Recorded request identity exposes the damage on load.
    pytest.param(_sludge_receipt(task_id="sludge-other-turn"), _SLUDGE_TEXT, "sludge-original", "does not derive its id", id="task-swap"),
    pytest.param(_sludge_receipt(session_id="v7-another-chat"), _SLUDGE_TEXT, "sludge-original", "does not derive its id", id="session-swap"),
    pytest.param(_sludge_receipt(content_key=_key("Sludge tank reading", "level at 48 percent after transfer")), _SLUDGE_TEXT,
                 "sludge-original", "does not derive its id", id="content-swap-to-another-note"),
    pytest.param(_pump_receipt(content_key=_key(*_PUMP, folder="Personal"), folder="Personal"), _PUMP_TEXT, "pump-original",
                 "does not derive its id", id="destination-swap"),
    # An older record without request identity: only the replay selection can see it.
    pytest.param(_without(_sludge_receipt(content_key=_key("Sludge tank reading", "level at 48 percent after transfer")), *_IDENTITY),
                 _SLUDGE_TEXT, "sludge-original", "different content than this request", id="content-swap-without-request-identity"),
    pytest.param(_without(_pump_receipt(content_key=_key(*_PUMP, folder="Personal")), *_IDENTITY), _PUMP_TEXT, "pump-original",
                 "different content than this request", id="destination-swap-without-request-identity"),
    pytest.param({**_without(_sludge_receipt(), *_IDENTITY), "title": "Sludge tank reading, starboard"}, _SLUDGE_TEXT, "sludge-original",
                 "different title than this request", id="title-names-another-note-without-request-identity"),
    pytest.param({**_without(_pump_receipt(), *_IDENTITY), "folder": "Personal"}, _PUMP_TEXT, "pump-original",
                 "different folder than this request", id="folder-names-another-destination-without-request-identity"),
]


@pytest.mark.parametrize(("record", "text", "task", "named"), _BINDING_DAMAGE)
def test_receipt_under_the_requested_operation_answers_only_for_this_request(workspace, monkeypatch, record, text, task, named):
    raw = _write(workspace, _v2(record))
    bridge = ScriptedBridge(monkeypatch, [])
    replay = _save(text, task=task)
    assert not replay.ok and replay.status == "effect_journal_quarantined" and bridge.calls == 0, replay.response_text
    assert named in replay.response_text, replay.response_text
    assert _preserved(workspace) == [raw]
    later = _save(text, task=f"{task}-after-owner-check")
    assert later.status == "effect_journal_quarantined" and bridge.calls == 0, "delivery stays paused while the damaged receipt is preserved"


def test_another_requests_record_with_swapped_identity_is_quarantined_before_a_new_request(workspace, monkeypatch):
    raw = _write(workspace, _v2(_sludge_receipt(task_id="sludge-other-turn")))
    bridge = ScriptedBridge(monkeypatch, [])
    fresh = _save(_request_text("Ballast water log", "exchange completed off the coast"), task="ballast-new-request")
    assert fresh.status == "effect_journal_quarantined" and "does not derive its id" in fresh.response_text and bridge.calls == 0, fresh.response_text
    assert _preserved(workspace) == [raw]


def test_whole_receipt_with_full_request_identity_replays_without_sending(workspace, monkeypatch):
    record = _pump_receipt()
    _write(workspace, _v2(record))
    bridge = ScriptedBridge(monkeypatch, [])
    replay = _save(_PUMP_TEXT, task="pump-original")
    assert replay.ok and replay.details["replayed"] and replay.details["note_reference"] == record["note_reference"], replay.response_text
    assert bridge.calls == 0 and not _preserved(workspace)


def test_older_receipt_without_request_identity_still_replays_without_sending(workspace, monkeypatch):
    key = _key(*_SLUDGE)
    op_id = notes._apple_note_operation_id(session_id=_SESSION, task_id="sludge-original", content_key=key)
    _write(workspace, _v2({"op_id": op_id, "content_key": key, "state": "confirmed", "note_reference": "note id x-coredata://v7/older"}))
    bridge = ScriptedBridge(monkeypatch, [])
    replay = _save(_SLUDGE_TEXT, task="sludge-original")
    assert replay.ok and replay.details["note_reference"] == "note id x-coredata://v7/older" and bridge.calls == 0, replay.response_text


def test_new_request_after_a_confirmed_receipt_creates_its_own_note(workspace, monkeypatch):
    _write(workspace, _v2(_sludge_receipt()))
    bridge = ScriptedBridge(monkeypatch, [])
    fresh = _save(_SLUDGE_TEXT, task="sludge-second-request")
    assert fresh.ok and not fresh.details["replayed"] and bridge.calls == 1, fresh.response_text
    assert "An earlier, separate request had already created" in fresh.response_text


def test_unresolved_sibling_still_holds_back_identical_content_and_replays_unresolved(workspace, monkeypatch):
    _write(workspace, _v2(_current(*_SLUDGE, task="sludge-original", state="ambiguous", reason="apple_event_timeout",
                                   detail="Notes got an error: AppleEvent timed out.")))
    bridge = ScriptedBridge(monkeypatch, [])
    held = _save(_SLUDGE_TEXT, task="sludge-second-request")
    assert held.status == "earlier_attempt_unresolved" and bridge.calls == 0, held.response_text
    replay = _save(_SLUDGE_TEXT, task="sludge-original")
    assert replay.status == "apple_event_timeout" and replay.details["delivery_state"] == "unresolved" and bridge.calls == 0, replay.response_text


def test_reservation_whose_owner_is_gone_replays_as_unresolved_and_keeps_its_attempt(workspace, monkeypatch):
    reservation = _without(_current(*_SLUDGE, task="sludge-original", state="dispatching"), "completed_at", "reason", "detail")
    _write(workspace, _v2(reservation))
    bridge = ScriptedBridge(monkeypatch, [])
    replay = _save(_SLUDGE_TEXT, task="sludge-original")
    assert replay.status == "owner_lost_during_dispatch" and bridge.calls == 0, replay.response_text
    [op] = json.loads(_journal_file(workspace).read_text(encoding="utf-8"))["operations"].values()
    assert (op["state"], op["attempt"]) == ("ambiguous", reservation["attempt"])
