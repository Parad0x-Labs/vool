"""pa_beta_gate -- revision-5 NOVEL cases for native Apple Notes delivery operations.

Different data from the independent review's originals, at the same failure classes:
reservation before the native boundary, cross-process ownership, a child process that dies
after the native effect, a lost receipt, damaged/unsupported/unreadable journals, the
revision-4 journal migration, rollback compatibility, and one explicit new attempt after a
definite refusal.

LABELLED: every native call goes through ``apple_notes.create_apple_note`` with an injected
runner double. Nothing here runs osascript or touches the Notes app.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.operator import apple_notes, notes
from core.operator.models import OperatorActionIntent

_SESSION = "novel-notes-session"
_JOURNAL = ".apple-notes-effects.json"


def _intent(text: str) -> OperatorActionIntent:
    return OperatorActionIntent(kind="save_note", raw_text=text)


def _save(text: str, *, task: str, session: str = _SESSION):
    return notes.handle_save_note(
        _intent(text),
        task_id=task,
        session_id=session,
        evaluate_local_action_fn=lambda *a, **k: SimpleNamespace(mode="execute"),
        audit_log_fn=lambda *a, **k: None,
    )


def _journal_path(root: Path) -> Path:
    return root / "notes" / _JOURNAL


def _journal(root: Path) -> dict:
    return json.loads(_journal_path(root).read_text(encoding="utf-8"))


def _content_key(title: str, body: str, account: str = "", folder: str = "") -> str:
    return hashlib.sha256("\0".join([title, body, account, folder]).encode("utf-8")).hexdigest()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(root))
    return root


class Bridge:
    """Injected runner double behind the real bridge's command building and error mapping."""

    def __init__(self, monkeypatch, answers):
        self.calls: list[list[str]] = []
        self.answers = list(answers)
        real = apple_notes.create_apple_note
        monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kw: real(**kw, runner=self.run))

    def run(self, command, **kwargs):
        self.calls.append(list(command))
        kind, value = self.answers.pop(0) if self.answers else ("ok", "")
        if kind == "ok":
            return SimpleNamespace(returncode=0, stdout=value or f"note id x-coredata://novel/p{len(self.calls)}", stderr="")
        if kind == "denied":
            return SimpleNamespace(returncode=1, stdout="", stderr="execution error: Not authorized to send Apple events to Notes. (-1743)")
        if kind == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 5))
        raise AssertionError(f"unscripted bridge answer {kind!r}")


# ---------------------------------------------------------------------------------------------
# Definite refusal -> explicit new request; ambiguity stays blocking; fallbacks never multiply
# ---------------------------------------------------------------------------------------------


def test_denied_then_new_request_after_consent_creates_once_in_the_named_folder(workspace, monkeypatch):
    bridge = Bridge(monkeypatch, [("denied", ""), ("ok", "note id x-coredata://novel/p77")])
    text = 'save a note in the Suppliers folder of Apple Notes titled "Quote comparison" with: three vendors, lowest bid wins'
    first = _save(text, task="turn-before-consent")
    assert not first.ok and first.status == "os_permission_denied", first.response_text
    assert "a new request makes one new attempt" in first.response_text

    replay = _save(text, task="turn-before-consent")
    assert replay.status == "os_permission_denied" and len(bridge.calls) == 1, "a replayed request is never retried automatically"
    assert replay.details["note_path"] == first.details["note_path"], "one fallback per operation, reused on replay"

    second = _save(text, task="turn-after-consent")
    assert second.ok and second.details["destination"] == "apple_notes", second.response_text
    assert second.details["note_reference"] == "note id x-coredata://novel/p77"
    assert len(bridge.calls) == 2
    assert 'folder "Suppliers" of default account' in bridge.calls[1][2]
    assert len(list((workspace / "notes").glob("*.md"))) == 1
    states = sorted(op["state"] for op in _journal(workspace)["operations"].values())
    assert states == ["confirmed", "refused"]


def test_timeout_is_not_denial_and_blocks_identical_content_but_not_an_independent_note(workspace, monkeypatch):
    bridge = Bridge(monkeypatch, [("timeout", ""), ("ok", "")])
    text = 'save a note to Apple Notes titled "Kiln firing log" with: cone 6, 1222C peak, slow cool'
    first = _save(text, task="kiln-turn-1")
    assert not first.ok and first.status == "delivery_unknown", first.response_text
    assert "not established" in first.response_text
    identical = _save(text, task="kiln-turn-2")
    assert identical.status == "earlier_attempt_unresolved" and len(bridge.calls) == 1, identical.response_text
    independent = _save('save a note to Apple Notes titled "Glaze inventory" with: celadon running low', task="kiln-turn-3")
    assert independent.ok and len(bridge.calls) == 2, independent.response_text


# ---------------------------------------------------------------------------------------------
# Cross-process ownership, restart and lost receipts
# ---------------------------------------------------------------------------------------------


def _patch_child_bridge(runner):
    from core.operator import apple_notes as bridge_module

    real = bridge_module.create_apple_note
    bridge_module.create_apple_note = lambda **kw: real(**kw, runner=runner)


def _child_save(title: str, body: str, task: str, session: str):
    from core.operator import notes as notes_module

    return notes_module.handle_save_note(
        _intent(f'save a note to Apple Notes titled "{title}" with: {body}'),
        task_id=task, session_id=session,
        evaluate_local_action_fn=lambda *a, **k: SimpleNamespace(mode="execute"),
        audit_log_fn=lambda *a, **k: None,
    )


def _crash_after_native_call(call_log: str):
    def runner(command, **kwargs):
        with open(call_log, "a", encoding="utf-8") as handle:
            handle.write("child-native-create\n")
        os._exit(17)  # dies after the native effect, before its outcome can be recorded

    _patch_child_bridge(runner)
    _child_save("Night inventory", "after-hours count of spare filters", "restart-turn", "restart-session")


def _deliver_while_held(call_log: str, entered, release):
    def runner(command, **kwargs):
        with open(call_log, "a", encoding="utf-8") as handle:
            handle.write("child-native-create\n")
        entered.set()
        if not release.wait(10):
            raise RuntimeError("synchronization timeout")
        return SimpleNamespace(returncode=0, stdout="note id x-coredata://child/p1", stderr="")

    _patch_child_bridge(runner)
    result = _child_save("Freezer audit", "compressor cycles logged", "overlap-turn", "overlap-session")
    if not result.ok:
        raise SystemExit(3)


def _deliver_at_barrier(call_log: str, barrier, title: str, body: str, task: str):
    def runner(command, **kwargs):
        barrier.wait(10)
        with open(call_log, "a", encoding="utf-8") as handle:
            handle.write(f"{title}\n")
        return SimpleNamespace(returncode=0, stdout=f"note id x-coredata://{task}/p1", stderr="")

    _patch_child_bridge(runner)
    result = _child_save(title, body, task, "parallel-session")
    if not result.ok:
        raise SystemExit(4)


def test_child_process_dying_after_the_native_effect_is_never_resent(workspace, monkeypatch, tmp_path):
    call_log = tmp_path / "native-calls.txt"
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_crash_after_native_call, args=(str(call_log),))
    child.start()
    child.join(20)
    assert child.exitcode == 17, child.exitcode
    ops = _journal(workspace)["operations"]
    assert [op["state"] for op in ops.values()] == ["dispatching"], "the reservation was durable before the native call"

    bridge = Bridge(monkeypatch, [])
    text = 'save a note to Apple Notes titled "Night inventory" with: after-hours count of spare filters'
    replay = _save(text, task="restart-turn", session="restart-session")
    assert not replay.ok and replay.status == "owner_lost_during_dispatch", replay.response_text
    assert "not attempted again" in replay.response_text
    fresh = _save(text, task="restart-new-turn", session="restart-session")
    assert fresh.status == "earlier_attempt_unresolved", fresh.response_text
    assert bridge.calls == [] and call_log.read_text().splitlines() == ["child-native-create"]
    assert [op["state"] for op in _journal(workspace)["operations"].values()] == ["ambiguous"]


def test_live_owner_in_another_process_is_never_overlapped(workspace, monkeypatch, tmp_path):
    call_log = tmp_path / "native-calls.txt"
    ctx = multiprocessing.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_deliver_while_held, args=(str(call_log), entered, release))
    child.start()
    bridge = Bridge(monkeypatch, [])
    text = 'save a note to Apple Notes titled "Freezer audit" with: compressor cycles logged'
    try:
        assert entered.wait(20), f"child never reached the native call (exit={child.exitcode})"
        overlapping = _save(text, task="overlap-turn", session="overlap-session")
        assert overlapping.status == "delivery_in_progress", overlapping.response_text
        assert "note_path" not in overlapping.details, "no fallback copy while the owner is still delivering"
        different_request = _save(text, task="overlap-other-turn", session="overlap-session")
        assert different_request.status == "delivery_in_progress", different_request.response_text
    finally:
        release.set()
        child.join(20)
    assert child.exitcode == 0, child.exitcode
    receipt = _save(text, task="overlap-turn", session="overlap-session")
    assert receipt.ok and receipt.details["replayed"] and receipt.details["note_reference"] == "note id x-coredata://child/p1"
    assert bridge.calls == [] and call_log.read_text().splitlines() == ["child-native-create"]
    assert not list((workspace / "notes").glob("*.md"))


def test_two_processes_delivering_different_notes_lose_no_update(workspace, tmp_path):
    call_log = tmp_path / "native-calls.txt"
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    children = [
        ctx.Process(target=_deliver_at_barrier, args=(str(call_log), barrier, "Pump station A", "seal leak at flange 3", "pump-a")),
        ctx.Process(target=_deliver_at_barrier, args=(str(call_log), barrier, "Pump station B", "vibration within tolerance", "pump-b")),
    ]
    for child in children:
        child.start()
    for child in children:
        child.join(30)
    assert [child.exitcode for child in children] == [0, 0]
    assert sorted(call_log.read_text().splitlines()) == ["Pump station A", "Pump station B"]
    journal = _journal(workspace)
    confirmed = {op["title"]: op["note_reference"] for op in journal["operations"].values() if op["state"] == "confirmed"}
    assert confirmed == {"Pump station A": "note id x-coredata://pump-a/p1", "Pump station B": "note id x-coredata://pump-b/p1"}
    for title, body in (("Pump station A", "seal leak at flange 3"), ("Pump station B", "vibration within tolerance")):
        assert journal[_content_key(title, body)]["status"] == "executed"


def test_lost_receipt_keeps_the_reservation_so_nothing_is_resent(workspace, monkeypatch):
    bridge = Bridge(monkeypatch, [("ok", "note id x-coredata://novel/p9")])
    real_save = notes._save_apple_note_effects
    writes = []

    def fail_outcome_write(document):
        writes.append(document)
        if len(writes) == 2:
            raise OSError("synthetic disk full while recording the outcome")
        return real_save(document)

    monkeypatch.setattr(notes, "_save_apple_note_effects", fail_outcome_write)
    text = 'save a note to Apple Notes titled "Boiler service" with: pressure relief valve replaced'
    first = _save(text, task="boiler-1")
    assert first.ok and first.details["receipt_unrecorded"] is True, first.response_text
    assert "could not be saved" in first.response_text
    assert _journal(workspace)["operations"][first.details["operation_id"]]["state"] == "dispatching"
    replay = _save(text, task="boiler-1")
    assert not replay.ok and replay.status == "owner_lost_during_dispatch" and len(bridge.calls) == 1, replay.response_text
    fresh = _save(text, task="boiler-2")
    assert fresh.status == "earlier_attempt_unresolved" and len(bridge.calls) == 1


# ---------------------------------------------------------------------------------------------
# Damaged, unsupported and unreadable journals: never read as an empty map
# ---------------------------------------------------------------------------------------------


def test_undecodable_journal_is_quarantined_byte_for_byte_and_pauses_delivery(workspace, monkeypatch):
    bridge = Bridge(monkeypatch, [("ok", "")])
    journal = _journal_path(workspace)
    journal.parent.mkdir(parents=True)
    damaged = b'{"schema": "vool.apple_notes_delivery_journal.v2", "operations": {"op-trunc'
    journal.write_bytes(damaged)
    text = 'save a note to Apple Notes titled "Sensor calibration" with: offset +0.4 at 21C'
    first = _save(text, task="calibration-1")
    assert not first.ok and first.status == "effect_journal_quarantined" and not bridge.calls, first.response_text
    preserved = list((workspace / "notes").glob(".apple-notes-effects.quarantine-*"))
    assert len(preserved) == 1 and preserved[0].read_bytes() == damaged and not journal.exists()
    second = _save(text, task="calibration-2")
    assert second.status == "effect_journal_quarantined" and not bridge.calls, "a quarantined journal is never an empty map"
    preserved[0].unlink()  # the owner's explicit resume step, after checking Apple Notes
    third = _save(text, task="calibration-3")
    assert third.ok and len(bridge.calls) == 1, third.response_text


@pytest.mark.parametrize("payload", [
    [1, 2, 3],
    {"schema": "vool.apple_notes_delivery_journal.v2", "operations": {"op-a": {"op_id": "op-b", "state": "confirmed", "content_key": "0" * 64}}},
    {"deadbeef": {"status": "executed"}},
    {"schema": "vool.apple_notes_delivery_journal.v2", "operations": {}, "quarantined": "not-a-list"},
])
def test_structurally_invalid_journals_are_quarantined(workspace, monkeypatch, payload):
    bridge = Bridge(monkeypatch, [("ok", "")])
    journal = _journal_path(workspace)
    journal.parent.mkdir(parents=True)
    raw = json.dumps(payload).encode("utf-8")
    journal.write_bytes(raw)
    result = _save('save a note to Apple Notes titled "Tide table" with: high water 06:12', task="tide-1")
    assert result.status == "effect_journal_quarantined" and not bridge.calls, result.response_text
    preserved = list((workspace / "notes").glob(".apple-notes-effects.quarantine-*"))
    assert len(preserved) == 1 and preserved[0].read_bytes() == raw


def test_unknown_future_schema_is_refused_and_left_untouched(workspace, monkeypatch):
    bridge = Bridge(monkeypatch, [("ok", "")])
    journal = _journal_path(workspace)
    journal.parent.mkdir(parents=True)
    raw = json.dumps({"schema": "vool.apple_notes_delivery_journal.v9", "operations": {"future": {}}}).encode("utf-8")
    journal.write_bytes(raw)
    result = _save('save a note to Apple Notes titled "Tide table" with: low water 12:40', task="tide-2")
    assert result.status == "effect_journal_version_unsupported" and not bridge.calls, result.response_text
    assert journal.read_bytes() == raw
    assert not list((workspace / "notes").glob(".apple-notes-effects.quarantine-*"))


def test_quarantine_that_cannot_move_the_journal_fails_closed(workspace, monkeypatch):
    bridge = Bridge(monkeypatch, [("ok", "")])
    journal = _journal_path(workspace)
    journal.parent.mkdir(parents=True)
    damaged = b"\x00\x01 not json"
    journal.write_bytes(damaged)
    real_replace = os.replace

    def refuse_journal_move(src, dst, *args, **kwargs):
        if str(src).endswith(_JOURNAL):
            raise PermissionError("synthetic: the journal cannot be moved aside")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", refuse_journal_move)
    result = _save('save a note to Apple Notes titled "Hive check" with: queen seen, brood pattern good', task="hive-1")
    assert result.status == "effect_journal_unwritable" and not bridge.calls, result.response_text
    assert journal.read_bytes() == damaged


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="file permissions do not bind root")
def test_unreadable_journal_refuses_without_touching_it(workspace, monkeypatch):
    bridge = Bridge(monkeypatch, [("ok", "")])
    journal = _journal_path(workspace)
    journal.parent.mkdir(parents=True)
    raw = json.dumps({"schema": "vool.apple_notes_delivery_journal.v2", "operations": {}}).encode("utf-8")
    journal.write_bytes(raw)
    journal.chmod(0)
    try:
        result = _save('save a note to Apple Notes titled "Seed order" with: tomatoes, basil', task="seed-1")
    finally:
        journal.chmod(0o600)
    assert result.status == "effect_journal_unreadable" and not bridge.calls, result.response_text
    assert journal.read_bytes() == raw


# ---------------------------------------------------------------------------------------------
# Migration from, and rollback to, the revision-4 journal
# ---------------------------------------------------------------------------------------------


def test_revision4_journal_is_migrated_by_meaning(workspace, monkeypatch):
    bridge = Bridge(monkeypatch, [("ok", "note id x-coredata://novel/pA"), ("ok", "note id x-coredata://novel/pC")])
    delivered = ("Ferry timetable", "summer schedule posted")
    unresolved = ("Harbour fees", "berth 4 invoice")
    denied = ("Crew roster", "weekend shifts")
    legacy = {
        _content_key(*delivered): {"status": "executed", "note_reference": "note id x-coredata://legacy/p1", "title": delivered[0]},
        _content_key(*unresolved): {"status": "outcome_unproven", "reason": "delivery_unknown", "detail": "timed out"},
        _content_key(*denied): {"status": "failed", "reason": "os_permission_denied", "detail": "Automation refused"},
    }
    journal = _journal_path(workspace)
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps(legacy), encoding="utf-8")

    again = _save(f'save a note to Apple Notes titled "{delivered[0]}" with: {delivered[1]}', task="migrated-1")
    assert again.ok and "An earlier, separate request had already created" in again.response_text, again.response_text
    refused = _save(f'save a note to Apple Notes titled "{unresolved[0]}" with: {unresolved[1]}', task="migrated-2")
    assert refused.status == "earlier_attempt_unresolved", refused.response_text
    retried = _save(f'save a note to Apple Notes titled "{denied[0]}" with: {denied[1]}', task="migrated-3")
    assert retried.ok, retried.response_text
    assert len(bridge.calls) == 2

    migrated = _journal(workspace)
    assert migrated["schema"] == "vool.apple_notes_delivery_journal.v2"
    assert {migrated[_content_key(*pair)]["status"] for pair in (delivered, denied)} == {"executed"}
    assert migrated[_content_key(*unresolved)]["status"] == "outcome_unproven"
    assert all(migrated[_content_key(*pair)]["_v5_index"] for pair in (delivered, unresolved, denied))


def _revision4_delivery(effects: dict, *, title: str, body: str, account: str, folder: str, bridge_create) -> dict:
    """The decision logic of revision-4 ``_deliver_apple_note_effect`` (bc6417bd,
    core/operator/notes.py lines 542-568), copied verbatim apart from injecting the journal dict
    and the bridge, so a rollback read of a revision-5 journal is executed, not described."""
    effect_key = hashlib.sha256("\0".join([title, body, account, folder]).encode("utf-8")).hexdigest()
    prior = effects.get(effect_key)
    if prior is not None:
        status = str(prior.get("status") or "")
        if status == "executed":
            return {"ok": True, "reason": "", "detail": "", "note_reference": str(prior.get("note_reference") or ""), "destination": "apple_notes", "replayed": True}
        if status in {"outcome_unproven", "effect_unrecorded", "executing", "pending"}:
            reason = str(prior.get("reason") or "delivery_unknown")
            return {"ok": False, "reason": reason, "detail": str(prior.get("detail") or "the earlier attempt's outcome is unproven; it was not re-attempted"), "destination": "apple_notes", "replayed": True}
        if status == "failed":
            return {"ok": False, "reason": str(prior.get("reason") or "osascript_failed"), "detail": str(prior.get("detail") or ""), "destination": "apple_notes", "replayed": True}
    outcome = bridge_create(title=title, body=body, account=account, folder=folder)
    reason = str(outcome.get("reason") or "")
    if outcome.get("ok"):
        effects[effect_key] = {"status": "executed", "note_reference": outcome.get("note_reference", ""), "title": title}
    else:
        effects[effect_key] = {"status": "outcome_unproven" if reason in {"delivery_unknown", "unverified"} else "failed", "reason": reason, "detail": outcome.get("detail", "")}
    return outcome


def test_rollback_reader_never_resends_confirmed_or_unresolved_notes(workspace, monkeypatch):
    bridge = Bridge(monkeypatch, [("ok", "note id x-coredata://novel/pX"), ("timeout", ""), ("ok", "note id x-coredata://rollback/pZ")])
    confirmed = ("Vaccination record", "rabies booster due March")
    ambiguous = ("Vet invoice", "consultation and bloods")
    assert _save(f'save a note to Apple Notes titled "{confirmed[0]}" with: {confirmed[1]}', task="pre-rollback-1").ok
    assert _save(f'save a note to Apple Notes titled "{ambiguous[0]}" with: {ambiguous[1]}', task="pre-rollback-2").status == "delivery_unknown"
    document = _journal(workspace)

    replayed = _revision4_delivery(document, title=confirmed[0], body=confirmed[1], account="", folder="", bridge_create=apple_notes.create_apple_note)
    held = _revision4_delivery(document, title=ambiguous[0], body=ambiguous[1], account="", folder="", bridge_create=apple_notes.create_apple_note)
    assert replayed["ok"] and replayed["replayed"] and not held["ok"] and held["replayed"]
    assert len(bridge.calls) == 2, "the older reader re-sent nothing this build knows about"

    created_by_old_build = _revision4_delivery(document, title="Kennel booking", body="two nights in October", account="", folder="", bridge_create=apple_notes.create_apple_note)
    assert created_by_old_build["ok"] and len(bridge.calls) == 3
    _journal_path(workspace).write_text(json.dumps(document, sort_keys=True, indent=2), encoding="utf-8")

    resumed = notes._load_apple_note_effects()  # roll forward again: the older build's write is imported, not quarantined
    imported = [op for op in resumed["operations"].values() if op.get("legacy")]
    assert [(op["title"], op["state"]) for op in imported] == [("Kennel booking", "confirmed")]
    assert sorted(op["state"] for op in resumed["operations"].values() if not op.get("legacy")) == ["ambiguous", "confirmed"]


def test_workspace_notes_listing_never_exposes_delivery_bookkeeping(workspace, monkeypatch):
    Bridge(monkeypatch, [("denied", "")])
    _save('save a note to Apple Notes titled "Garden plan" with: raised beds by the fence', task="garden-1")
    notes.handle_save_note(_intent("save a note with agenda: compost turning schedule"), task_id="garden-2", session_id=_SESSION,
                           evaluate_local_action_fn=lambda *a, **k: SimpleNamespace(mode="execute"), audit_log_fn=lambda *a, **k: None)
    listed = sorted(row["title"] for row in notes.search_notes(query=""))
    assert listed == ["Agenda", "Garden plan"]
    hidden = {path.name for path in (workspace / "notes").iterdir() if path.name.startswith(".")}
    assert {".apple-notes-effects.json", ".apple-notes-effects.lock", ".apple-notes-effects.owners"} <= hidden


def test_an_attempt_whose_record_was_judged_elsewhere_cannot_overwrite_it(workspace, monkeypatch):
    """Fencing: while this worker is inside the bridge, another writer has already judged its
    reservation (different attempt token, recorded ambiguous). The late outcome must not
    overwrite that newer record, and the answer admits its receipt was not recorded."""
    journal = _journal_path(workspace)

    def foreign_judgement(command, **kwargs):
        document = json.loads(journal.read_text(encoding="utf-8"))
        (op,) = document["operations"].values()
        op.update(state="ambiguous", attempt="foreign-attempt", reason="owner_lost_during_dispatch")
        journal.write_text(json.dumps(document), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="note id x-coredata://late/p1", stderr="")

    real = apple_notes.create_apple_note
    monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kw: real(**kw, runner=foreign_judgement))
    late = _save('save a note to Apple Notes titled "Lab freezer alarm" with: minus 62C at 03:10', task="freezer-1")
    assert late.ok and late.details["receipt_unrecorded"] is True, late.response_text
    (op,) = _journal(workspace)["operations"].values()
    assert (op["state"], op["attempt"]) == ("ambiguous", "foreign-attempt"), "a stale attempt overwrote a newer record"
