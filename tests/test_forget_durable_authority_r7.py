"""An unavailable forget barrier stays closed across real process boundaries."""
from __future__ import annotations

import importlib
import json
import os
import selectors
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _authority():
    return importlib.import_module("core.memory.revocation")


_WORKER = """
import json
import os
import sys
from core.memory import revocation as authority

home, mode = sys.argv[1:]
chat = 'durable-authority'

if mode == 'unarmed-failure':
    original_open = authority.Path.open
    def deny_lease(path, *args, **kwargs):
        if path.name.startswith('lease-') and args and 'x' in args[0]:
            raise OSError('new lease cannot be persisted')
        return original_open(path, *args, **kwargs)
    authority.Path.open = deny_lease
    try:
        authority.begin(chat, 'MINT84', home)
    except RuntimeError:
        print(json.dumps({'state': 'withheld'}), flush=True)
        sys.exit(0)
    raise AssertionError('unrecorded forget unexpectedly completed')

def read():
    try:
        tokens = authority.snapshot(chat, home)
        return {'state': 'readable', 'tokens': list(tokens)}
    except RuntimeError:
        return {'state': 'withheld'}

try:
    authority.prepare(home, allow_initialize=mode in {'initialize', 'crash'})
except RuntimeError:
    print(json.dumps({'state': 'withheld'}), flush=True)
    sys.exit(0)

if mode == 'crash':
    os._exit(17)
if mode == 'initialize':
    operation = authority.begin(chat, 'MINT84', home)
    authority.complete(operation, home)
print(json.dumps(read()), flush=True)
if mode == 'watch':
    for command in sys.stdin:
        if command.strip() == 'exit':
            break
        print(json.dumps(read()), flush=True)
"""


def _command(home, mode):
    return [sys.executable, "-c", _WORKER, str(home), mode]


def _child(home, mode):
    result = subprocess.run(_command(home, mode), text=True, capture_output=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _read_child_line(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(timeout=20), "authority reader did not respond"
    line = process.stdout.readline()
    assert line, "authority reader exited before reporting a state"
    return json.loads(line)


def test_pending_barrier_blocks_then_completed_token_survives_healthy_restart(tmp_path):
    authority = _authority()
    home = tmp_path / "runtime"
    authority.prepare(home, allow_initialize=True)
    operation = authority.begin("durable-authority", "MINT84", home)
    with pytest.raises(RuntimeError):
        authority.snapshot("durable-authority", home)
    authority.complete(operation, home)
    assert "mint84" in {token.lower() for token in authority.snapshot("durable-authority", home)}
    state = _child(home, "reader")
    assert state["state"] == "readable"
    assert "mint84" in {token.lower() for token in state["tokens"]}


def test_normal_writer_exit_preserves_durable_revocation_without_orphaning_readers(tmp_path):
    home = tmp_path / "runtime"
    state = _child(home, "initialize")
    assert state["state"] == "readable"
    after_restart = _child(home, "reader")
    assert after_restart["state"] == "readable"
    assert "mint84" in {token.lower() for token in after_restart["tokens"]}


def test_ungraceful_exit_leaves_a_barrier_readers_cannot_autoheal(tmp_path):
    home = tmp_path / "runtime"
    result = subprocess.run(_command(home, "crash"), text=True, capture_output=True, timeout=20, check=False)
    assert result.returncode == 17, result.stderr
    assert _child(home, "reader")["state"] == "withheld"
    authority = _authority()
    with pytest.raises(RuntimeError):
        authority.prepare(home, allow_initialize=True)
    with pytest.raises(RuntimeError):
        authority.snapshot("durable-authority", home)


def test_total_barrier_write_failure_withholds_in_current_and_restarted_reader(tmp_path, monkeypatch):
    authority = _authority()
    home = tmp_path / "runtime"
    authority.prepare(home, allow_initialize=True)
    assert authority.snapshot("durable-authority", home) == ()
    denied = []

    def no_writes(path, payload):
        denied.append(str(path))
        raise OSError("durable storage unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(authority, "_durable_write", no_writes)
        with pytest.raises(RuntimeError):
            authority.begin("durable-authority", "MINT84", home)
        assert denied
        with pytest.raises(RuntimeError):
            authority.snapshot("durable-authority", home)
    # The failed write cannot be persisted later by a successful read or by
    # constructing a new process with healthy disk access.
    with pytest.raises(RuntimeError):
        authority.snapshot("durable-authority", home)
    assert _child(home, "reader")["state"] == "withheld"


def test_already_running_reader_detects_another_process_poison_without_restart(tmp_path):
    authority = _authority()
    home = tmp_path / "runtime"
    authority.prepare(home, allow_initialize=True)
    process = subprocess.Popen(
        _command(home, "watch"), text=True, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        assert _read_child_line(process)["state"] == "readable"
        authority.poison(home)
        process.stdin.write("read\n")
        process.stdin.flush()
        assert _read_child_line(process)["state"] == "withheld"
    finally:
        process.communicate("exit\n", timeout=20)
    assert process.returncode == 0
    assert _child(home, "reader")["state"] == "withheld"


def test_missing_authority_beside_existing_owner_store_is_never_fresh_initialization(tmp_path):
    home = tmp_path / "runtime"
    db_path = home / "data" / "memory" / "vool_memory.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE source_occurrences (body TEXT)")
        connection.execute("INSERT INTO source_occurrences VALUES ('My locker code is MINT-84.')")
    authority = _authority()
    with pytest.raises(RuntimeError):
        authority.prepare(home, allow_initialize=True)
    with pytest.raises(RuntimeError):
        authority.snapshot("durable-authority", home)
    assert _child(home, "reader")["state"] == "withheld"
    with sqlite3.connect(Path(db_path)) as connection:
        assert connection.execute("SELECT body FROM source_occurrences").fetchone()[0] == "My locker code is MINT-84."


def test_structured_only_history_cannot_reinitialize_missing_authority(tmp_path):
    home = tmp_path / "runtime"
    entries = home / "data" / "memory_entries.jsonl"
    entries.parent.mkdir(parents=True)
    original = json.dumps({"id": "retained-row", "text": "My locker code is MINT-84.",
                           "status": "active", "session_id": "durable-authority"}) + "\n"
    entries.write_text(original)
    authority = _authority()
    with pytest.raises(RuntimeError):
        authority.prepare(home, allow_initialize=True)
    assert _child(home, "reader")["state"] == "withheld"
    assert entries.read_text() == original


def test_new_process_without_a_persistable_lease_cannot_leave_existing_reader_open(tmp_path):
    authority = _authority()
    home = tmp_path / "runtime"
    authority.prepare(home, allow_initialize=True)
    assert authority.snapshot("durable-authority", home) == ()
    assert _child(home, "unarmed-failure")["state"] == "withheld"
    # The command was refused, but owner law still requires every reader to
    # retain that refusal after the failing process has exited.
    with pytest.raises(RuntimeError):
        authority.snapshot("durable-authority", home)
    assert _child(home, "reader")["state"] == "withheld"


@pytest.mark.parametrize(("barrier_chat", "complete"), [
    ("durable-authority", False), ("durable-authority", True), ("", True),
])
def test_ordinary_occurrence_writer_refuses_pending_completed_and_global_revocations(tmp_path, barrier_chat, complete):
    from core.vool_memory import VoolMemory

    authority = _authority()
    home = tmp_path / "runtime"
    authority.prepare(home, allow_initialize=True)
    mem = VoolMemory(runtime_home=home)
    try:
        operation = authority.begin(barrier_chat, "MINT84", home)
        if complete:
            authority.complete(operation, home)
        with pytest.raises(RuntimeError):
            mem.occurrence_store(chat_scope="durable-authority", role="user", authority="observed-user-statement", body="My locker code is MINT84.")
        preserved = mem.occurrence_store(chat_scope="durable-authority", role="user", authority="observed-user-statement", body="The bicycle frame is blue.")
        rows = mem._conn.execute("SELECT occurrence_id, body FROM source_occurrences").fetchall()
        assert [(row[0], row[1]) for row in rows] == [(preserved.occurrence_id, "The bicycle frame is blue.")]
    finally:
        mem.close()


def test_reader_cannot_treat_missing_authority_as_a_clean_new_runtime(tmp_path):
    authority = _authority()
    home = tmp_path / "runtime"
    with pytest.raises(RuntimeError):
        authority.snapshot("durable-authority", home)
    assert not (home / ".forget-authority" / "ledger.json").exists()
