"""pa_beta_gate -- correction 3 (F1): an Apple Notes mutation keeps ONE original-request identity.

The replay lookup, the reservation, the bridge invocation and the receipt all derive from the request AS ASKED: kind,
title, the account/folder the request named (usually none) and the payload. The note a resolution finds is recorded
beside that identity as provenance (``target``). A replay after the title is reused in another folder or account
answers from its own record; an unresolved replay is not re-sent; a record an earlier build keyed by its resolved
scope is recovered, or the request is refused when the note it meant cannot be determined; a genuinely new request
(a new task) resolves afresh, and still refuses an ambiguous title.

LABELLED: synthetic stateful Notes runner (``FakeNotes``, speaking the real script protocol) standing in for
osascript; spawned fresh interpreters for the restart probe; pre-correction-3 journal records written field-for-field
in the layout build f2378f6d wrote (compared with that build's own output by correction-3/guard/legacy_layout_probe.py).
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import secrets
from pathlib import Path

import pytest

from ._pc_calendar_rig import prepare_home
from .test_pc_notes_identity import FakeNotes

pytestmark = [pytest.mark.pa_beta]

RENAMES = "set name of theNote to"
APPENDS = "make new paragraph"
DELETES = "delete theNote"


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


def _operations() -> list[dict]:
    from core.operator import notes

    return list(json.loads(notes._apple_note_effects_journal().read_text(encoding="utf-8"))["operations"].values())


def _count(scripts, needle: str) -> int:
    return len([script for script in scripts if needle in script])


def test_original_unscoped_rename_replay_answers_from_its_record(home, monkeypatch):
    """ORIGINAL (the review's F1 data): an unscoped rename of Draft (Work/iCloud) to Ready, then another Draft appears
    in Personal/On My Mac; the SAME request replayed answers from its record and renames nothing. The record's
    identity is the request as asked; the resolved note is its provenance."""
    from core.operator import notes

    fake = FakeNotes({"a": {"title": "Draft", "folder": "Work", "account": "iCloud", "body": "a"}}).install(monkeypatch)
    args = dict(kind="rename", title="Draft", new_title="Ready", task_id="same-task", session_id="same-session")
    first = notes.deliver_apple_note_mutation(**args)
    assert first["ok"] and first["note_id"] == "a" and fake.notes["a"]["title"] == "Ready", first

    fake.notes["b"] = {"title": "Draft", "folder": "Personal", "account": "On My Mac", "body": "b"}
    replay = notes.deliver_apple_note_mutation(**args)
    assert fake.notes["b"]["title"] == "Draft", f"replay renamed a different scoped note: {replay}"
    assert replay["ok"] and replay.get("replayed") is True and replay["note_id"] == "a", replay
    assert replay["operation_id"] == first["operation_id"]
    assert _count(fake.scripts, RENAMES) == 1

    [op] = _operations()
    assert (op["effect_kind"], op["note_title"], op["account"], op["folder"]) == ("rename", "Draft", "", "")
    assert op["target"] == {"note_id": "a", "title": "Draft", "account": "iCloud", "folder": "Work"}
    assert op["content_key"] == notes._mutation_request_key(kind="rename", title="Draft", payload="Ready", account="", folder="")
    assert op["effect_key"] == notes._mutation_effect_key(kind="rename", target=op["target"], payload="Ready")
    assert notes._operation_id_derives(op["op_id"], session_id="same-session", task_id="same-task", content_key=op["content_key"])


def test_novel_partial_scope_append_replay_is_not_retargeted_to_another_account(home, monkeypatch):
    """NOVEL: an append that names only the folder. The original Standup (Work/iCloud) is then renamed by the user and
    a new Standup appears in Work of On My Mac; the same request replayed appends nothing anywhere."""
    from core.operator import notes

    fake = FakeNotes({"n1": {"title": "Standup", "folder": "Work", "account": "iCloud", "body": "monday"}}).install(monkeypatch)
    args = dict(kind="append", title="Standup", payload="migrated repo", folder="Work",
                task_id="append-task", session_id="append-session")
    first = notes.deliver_apple_note_mutation(**args)
    assert first["ok"] and fake.notes["n1"]["body"] == "monday\nmigrated repo", first

    fake.notes["n1"]["title"] = "Standup (archived)"
    fake.notes["n2"] = {"title": "Standup", "folder": "Work", "account": "On My Mac", "body": "tuesday"}
    replay = notes.deliver_apple_note_mutation(**args)
    assert fake.notes["n2"]["body"] == "tuesday", f"replay appended to a different account's note: {replay}"
    assert replay["ok"] and replay.get("replayed") is True and replay["note_id"] == "n1", replay
    assert fake.notes["n1"]["body"] == "monday\nmigrated repo" and _count(fake.scripts, APPENDS) == 1
    [op] = _operations()
    assert (op["folder"], op["account"]) == ("Work", "") and op["target"]["account"] == "iCloud"


def test_novel_unscoped_delete_replay_after_title_reuse_deletes_nothing(home, monkeypatch):
    """NOVEL: an unscoped delete of 'Scratch pad'; a new 'Scratch pad' then appears in Archive/On My Mac; the same
    request replayed deletes nothing and answers with the note it originally deleted."""
    from core.operator import notes

    fake = FakeNotes({"d1": {"title": "Scratch pad", "folder": "Notes", "account": "iCloud", "body": "old"}}).install(monkeypatch)
    args = dict(kind="delete", title="Scratch pad", task_id="delete-task", session_id="delete-session")
    first = notes.deliver_apple_note_mutation(**args)
    assert first["ok"] and "d1" not in fake.notes, first

    fake.notes["d2"] = {"title": "Scratch pad", "folder": "Archive", "account": "On My Mac", "body": "keep me"}
    replay = notes.deliver_apple_note_mutation(**args)
    assert "d2" in fake.notes and fake.notes["d2"]["body"] == "keep me", f"replay deleted a different note: {replay}"
    assert replay["ok"] and replay.get("replayed") is True and replay["note_id"] == "d1", replay
    assert _count(fake.scripts, DELETES) == 1


def test_unresolved_unscoped_append_replay_is_never_resent(home, monkeypatch):
    """UNRESOLVED: an unscoped append whose reply is lost stays unresolved. After the original note is renamed and the
    title reappears in another account, the same request replayed is answered as unresolved and sent nowhere."""
    from core.operator import notes

    fake = FakeNotes({"u1": {"title": "Ops log", "folder": "Work", "account": "iCloud", "body": "start"}}).install(monkeypatch)
    fake.timeout_on_append = True
    args = dict(kind="append", title="Ops log", payload="rotated keys", task_id="unresolved-task", session_id="unresolved-session")
    first = notes.deliver_apple_note_mutation(**args)
    assert not first["ok"] and first["delivery_state"] == "unresolved", first

    fake.timeout_on_append = False
    fake.notes["u1"]["title"] = "Ops log (old)"
    fake.notes["u2"] = {"title": "Ops log", "folder": "Personal", "account": "On My Mac", "body": "other"}
    replay = notes.deliver_apple_note_mutation(**args)
    assert fake.notes["u2"]["body"] == "other", f"an unresolved request was re-sent to another note: {replay}"
    assert not replay["ok"] and replay.get("replayed") is True and replay["delivery_state"] == "unresolved", replay
    assert _count(fake.scripts, APPENDS) == 1


def _child_mutation(state_file: str, request: dict, answer_file: str) -> None:
    """One request in a fresh interpreter over the SAME workspace. The synthetic Notes state is read from disk and
    written back, so nothing carries over between interpreters but files."""
    from core.operator import apple_notes
    from core.operator import notes as notes_module
    from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes as ChildNotes

    saved = json.loads(Path(state_file).read_text(encoding="utf-8"))
    fake = ChildNotes(saved["notes"])
    fake.scripts = list(saved["scripts"])
    apple_notes.run_notes_bridge = lambda script, *, runner=None, purpose="the Notes request": fake._run(script)
    answer = notes_module.deliver_apple_note_mutation(**request)
    Path(state_file).write_text(json.dumps({"notes": fake.notes, "scripts": fake.scripts}), encoding="utf-8")
    Path(answer_file).write_text(json.dumps({"pid": os.getpid(), **answer}, default=str), encoding="utf-8")


def _in_fresh_interpreter(tmp_path, name: str, request: dict) -> dict:
    ctx = multiprocessing.get_context("spawn")
    answer_file = tmp_path / f"answer-{name}.json"
    child = ctx.Process(target=_child_mutation, args=(str(tmp_path / "notes-state.json"), request, str(answer_file)))
    child.start()
    child.join(120)
    assert child.exitcode == 0, (name, child.exitcode)
    return json.loads(answer_file.read_text(encoding="utf-8"))


def test_restart_replay_in_a_fresh_interpreter_keeps_original_custody(home, tmp_path):
    """RESTART: the rename is delivered by one interpreter; a DIFFERENT fresh interpreter replays the same request after
    the title is reused in another account and renames nothing."""
    state = tmp_path / "notes-state.json"
    state.write_text(json.dumps({"notes": {"r1": {"title": "Q3 retro", "folder": "Team", "account": "iCloud", "body": ""}},
                                 "scripts": []}), encoding="utf-8")
    request = dict(kind="rename", title="Q3 retro", new_title="Q3 retro (done)", task_id="restart-task", session_id="restart-session")
    first = _in_fresh_interpreter(tmp_path, "deliver", request)
    assert first["ok"] and first["note_id"] == "r1", first

    saved = json.loads(state.read_text(encoding="utf-8"))
    saved["notes"]["r2"] = {"title": "Q3 retro", "folder": "Personal", "account": "On My Mac", "body": ""}
    state.write_text(json.dumps(saved), encoding="utf-8")
    replay = _in_fresh_interpreter(tmp_path, "replay", request)
    after = json.loads(state.read_text(encoding="utf-8"))
    assert replay["pid"] != first["pid"]
    assert after["notes"]["r2"]["title"] == "Q3 retro", f"the restarted replay renamed a different note: {replay}"
    assert replay["ok"] and replay.get("replayed") is True and replay["note_id"] == "r1", replay
    assert _count(after["scripts"], RENAMES) == 1


def test_new_request_resolves_afresh_and_an_ambiguous_new_request_still_refuses(home, monkeypatch):
    """CONTROL: a genuinely new request (a new task in the same chat) is its own operation and renames the note that now
    carries the title, without anyone typing a folder; with two same-titled candidates a new request refuses naming both."""
    from core.operator import notes

    fake = FakeNotes({"a": {"title": "Draft", "folder": "Work", "account": "iCloud", "body": ""}}).install(monkeypatch)
    request = dict(kind="rename", title="Draft", new_title="Ready", session_id="new-request-session")
    assert notes.deliver_apple_note_mutation(**request, task_id="turn-1")["ok"]

    fake.notes["b"] = {"title": "Draft", "folder": "Personal", "account": "On My Mac", "body": ""}
    fresh = notes.deliver_apple_note_mutation(**request, task_id="turn-2")
    assert fresh["ok"] and fresh.get("replayed") is not True and fresh["note_id"] == "b", fresh
    assert fake.notes["b"]["title"] == "Ready" and _count(fake.scripts, RENAMES) == 2

    fake.notes["c"] = {"title": "Draft", "folder": "Work", "account": "iCloud", "body": ""}
    fake.notes["d"] = {"title": "Draft", "folder": "Personal", "account": "On My Mac", "body": ""}
    ambiguous = notes.deliver_apple_note_mutation(**request, task_id="turn-3")
    assert not ambiguous["ok"] and ambiguous["reason"] == "ambiguous", ambiguous
    assert _count(fake.scripts, RENAMES) == 2 and fake.notes["c"]["title"] == fake.notes["d"]["title"] == "Draft"


def _f2378f6d_mutation_record(*, kind: str, target: dict, payload: str, session_id: str, task_id: str) -> dict:
    """A confirmed mutation record in the layout build f2378f6d wrote: keyed by the RESOLVED title and scope, with the
    scope the request named not recorded at all."""
    from core.operator.effect_lifecycle import owner_provenance

    content_key = hashlib.sha256("\0".join([f"{kind}:{target['title']}", payload, target["account"], target["folder"]]).encode("utf-8")).hexdigest()
    op_id = "op-" + hashlib.sha256("\0".join(["apple_notes.create", session_id, task_id, content_key]).encode("utf-8")).hexdigest()[:40]
    return {"op_id": op_id, "content_key": content_key, "state": "confirmed", "attempt": secrets.token_hex(16),
            "session_id": session_id, "task_id": task_id, "effect_kind": kind, "note_id": target["note_id"],
            "note_title": target["title"], "account": target["account"], "folder": target["folder"],
            "reserved_at": "2026-09-15T04:00:00.000000+00:00", "owner": owner_provenance(),
            "completed_at": "2026-09-15T04:00:01.000000+00:00", "reason": "", "detail": "", "note_reference": target["note_id"]}


def test_pre_correction3_records_are_recovered_or_refused_never_retargeted(home, monkeypatch):
    """MIGRATION: a record the f2378f6d build keyed by its resolved scope answers this unscoped request's replay with no
    bridge call; a journal holding TWO such records of one request (the review's retarget, already suffered) cannot say
    which note the request meant, so the replay is refused and nothing is sent; a request that NAMES one of those scopes
    still replays exactly its own record."""
    from core.operator import notes

    fake = FakeNotes({"c": {"title": "Draft", "folder": "Shared", "account": "iCloud", "body": ""}}).install(monkeypatch)
    work = {"note_id": "a", "title": "Draft", "account": "iCloud", "folder": "Work"}
    personal = {"note_id": "b", "title": "Draft", "account": "On My Mac", "folder": "Personal"}
    single = _f2378f6d_mutation_record(kind="rename", target=work, payload="Ready", session_id="old-s1", task_id="old-t1")
    split = [_f2378f6d_mutation_record(kind="rename", target=target, payload="Ready", session_id="old-s2", task_id="old-t2")
             for target in (work, personal)]
    journal = {"schema": notes._JOURNAL_SCHEMA, "operations": {op["op_id"]: op for op in [single, *split]}, "quarantined": []}
    notes._save_apple_note_effects(notes._journal_document(journal))

    recovered = notes.deliver_apple_note_mutation(kind="rename", title="Draft", new_title="Ready", task_id="old-t1", session_id="old-s1")
    assert recovered["ok"] and recovered.get("replayed") is True and recovered["note_id"] == "a", recovered
    assert recovered["identity_recovered_from"] == "resolved_scope_record" and recovered["operation_id"] == single["op_id"]

    refused = notes.deliver_apple_note_mutation(kind="rename", title="Draft", new_title="Ready", task_id="old-t2", session_id="old-s2")
    assert not refused["ok"] and refused["reason"] == "request_identity_undetermined", refused
    assert refused["delivery_state"] == "not_dispatched" and "Nothing was sent to Notes" in refused["detail"], refused

    named = notes.deliver_apple_note_mutation(kind="rename", title="Draft", new_title="Ready", folder="Personal", account="On My Mac",
                                              task_id="old-t2", session_id="old-s2")
    assert named["ok"] and named.get("replayed") is True and named["note_id"] == "b", named
    assert named["operation_id"] == split[1]["op_id"]

    assert fake.notes["c"]["title"] == "Draft" and _count(fake.scripts, RENAMES) == 0, "nothing was renamed on any path"
    assert not list(notes.notes_root().glob(".apple-notes-effects.quarantine-*")), "the older records were read, not quarantined"
