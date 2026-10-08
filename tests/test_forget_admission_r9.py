"""Durable admission distinguishes refused starts from accepted barriers."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.test_forget_durable_authority_r7 import _child, _command, _read_child_line


def test_unarmed_failed_admission_preserves_source_and_readability(tmp_path, monkeypatch):
    from core.memory import revocation as authority
    from core.vool_memory import VoolMemory

    home = tmp_path / "runtime"
    memory = VoolMemory(runtime_home=home)
    try:
        source = memory.occurrence_store(
            chat_scope="durable-authority", role="user", authority="observed-user-statement",
            body="The locker code is MINT84.",
        )
        before = [tuple(row) for row in memory._conn.execute("SELECT * FROM source_occurrences")]
        before_ledger = (home / ".forget-authority" / "ledger.json").read_bytes()

        def denied(path, payload):
            raise OSError("admission storage unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(authority, "_durable_write", denied)
            with pytest.raises(authority.ForgetStartError) as failure:
                authority.begin("durable-authority", "MINT84", home)
            assert failure.value.started is False
            assert failure.value.operation_id == ""
        assert [tuple(row) for row in memory._conn.execute("SELECT * FROM source_occurrences")] == before
        assert (home / ".forget-authority" / "ledger.json").read_bytes() == before_ledger
        assert memory.occurrence_get(source.occurrence_id).body == "The locker code is MINT84."
        assert _child(home, "reader")["state"] == "readable"
    finally:
        memory.close()


def test_persisted_admission_reaches_running_reader_and_restart(tmp_path):
    from core.memory import revocation as authority

    home = tmp_path / "runtime"
    authority.prepare(home, allow_initialize=True, arm=False)
    process = subprocess.Popen(
        _command(home, "watch"), text=True, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert _read_child_line(process)["state"] == "readable"
        operation = authority.begin("durable-authority", "MINT84", home)
        process.stdin.write("read\n")
        process.stdin.flush()
        assert _read_child_line(process)["state"] == "withheld"
        assert _child(home, "reader")["state"] == "withheld"
        authority.complete(operation, home)
        process.stdin.write("read\n")
        process.stdin.flush()
        state = _read_child_line(process)
        assert state["state"] == "readable"
        assert "mint84" in {token.lower() for token in state["tokens"]}
    finally:
        process.communicate("exit\n", timeout=20)
    assert process.returncode == 0
    assert "mint84" in {token.lower() for token in _child(home, "reader")["tokens"]}


def test_ordinary_reader_termination_does_not_invent_a_forget(tmp_path):
    from core.memory import revocation as authority

    home = tmp_path / "runtime"
    worker = """
import os, sys
from core.vool_memory import VoolMemory
memory = VoolMemory(runtime_home=sys.argv[1])
memory.occurrence_store(chat_scope='durable-authority', role='user', authority='observed-user-statement', body='The bicycle frame is blue.')
os._exit(17)
"""
    result = subprocess.run([sys.executable, "-c", worker, str(home)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 17, result.stderr
    assert authority.snapshot("durable-authority", home) == ()
    assert _child(home, "reader")["state"] == "readable"


def test_repeated_erasure_completes_earlier_identical_barrier_only(tmp_path):
    from core.memory import revocation as authority

    home = tmp_path / "runtime"
    authority.prepare(home, allow_initialize=True, arm=False)
    earlier = authority.begin("durable-authority", "MINT84", home)
    other = authority.begin("another-chat", "MINT84", home)
    current = authority.begin("durable-authority", "MINT84", home)
    authority.complete(current, home)
    rows = json.loads((home / ".forget-authority" / "ledger.json").read_text())["operations"]
    assert rows[earlier]["status"] == rows[current]["status"] == "complete"
    assert rows[other]["status"] == "pending"


def test_refusal_protocol_does_not_swallow_writer_execution_failure(tmp_path):
    from core.memory import revocation as authority

    home = tmp_path / "runtime"

    @authority.fenced_writer(on_refused=None)
    def writer(payload, runtime_home):
        raise RuntimeError("writer execution failed")

    with pytest.raises(RuntimeError, match="writer execution failed"):
        writer("a harmless row", home)


def test_runtime_reader_checks_default_owners_independent_barrier(tmp_path, monkeypatch):
    from core import runtime_paths
    from core.memory import revocation as authority
    from core.vool_memory import VoolMemory
    from tests.test_question_date_time_leg_20261002 import _profile

    default_home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", default_home)
    assert runtime_paths.active_vool_home() == default_home
    runtime_home = tmp_path / "selected-runtime"
    for home in (default_home, runtime_home):
        with VoolMemory(runtime_home=home):
            pass
    assert authority.authorize_payload("The locker code is MINT84.", "durable-authority", runtime_home)
    authority.begin("durable-authority", "MINT84", default_home)
    assert not authority.authorize_payload("The locker code is MINT84.", "durable-authority", runtime_home)


def test_failed_initial_authority_read_never_starts_or_changes_memory(tmp_path, monkeypatch):
    from core.memory import revocation as authority
    from core.vool_memory import VoolMemory

    home = tmp_path / "runtime"
    with VoolMemory(runtime_home=home) as memory:
        source = memory.occurrence_store(
            chat_scope="durable-authority", role="user", authority="observed-user-statement",
            body="The locker code is MINT84.",
        )
        before = [tuple(row) for row in memory._conn.execute("SELECT * FROM source_occurrences")]
        ledger = home / ".forget-authority" / "ledger.json"
        before_ledger = ledger.read_bytes()
        writes = []

        def unreadable(directory):
            raise OSError("authority read unavailable before publication")

        def unexpected_write(path, payload):
            writes.append(path)
            raise AssertionError("no write may precede authority read")

        with monkeypatch.context() as patch:
            patch.setattr(authority, "_read", unreadable)
            patch.setattr(authority, "_durable_write", unexpected_write)
            with pytest.raises(authority.ForgetStartError) as failure:
                authority.begin("durable-authority", "MINT84", home)
            assert failure.value.started is False
            assert failure.value.operation_id == ""
            assert writes == []
        assert ledger.read_bytes() == before_ledger
        assert [tuple(row) for row in memory._conn.execute("SELECT * FROM source_occurrences")] == before
        assert memory.occurrence_get(source.occurrence_id).body == "The locker code is MINT84."
        assert _child(home, "reader")["state"] == "readable"


def test_owner_revocation_snapshot_preserves_executor_reads_and_order(tmp_path, monkeypatch):
    from core import vool_memory

    with vool_memory.VoolMemory(runtime_home=tmp_path / "runtime") as memory:
        with monkeypatch.context() as patch:
            patch.setattr(vool_memory.time, "time", lambda: 10.0)
            memory.record_revocation("ZULU9", chat_id="owner-snapshot")
            patch.setattr(vool_memory.time, "time", lambda: 20.0)
            memory.record_revocation("ALPHA8", chat_id="owner-snapshot")
        expected = ("zulu9", "alpha8")
        assert memory.revoked_tokens("owner-snapshot") == expected
        assert memory.revoked_tokens("") == ()
        assert memory.revoked_tokens(None) == ()
        assert memory.revoked_tokens("owner-snapshot", include_pending=True) == expected

        operation = memory.begin_erasure("owner-snapshot", "ZULU9")
        assert memory.revoked_tokens("owner-snapshot") == expected
        assert memory.revoked_tokens("owner-snapshot", strict=False) == expected
        with pytest.raises(RuntimeError, match="incomplete"):
            memory.revoked_tokens("owner-snapshot", include_pending=True)
        memory.complete_erasure(operation)
        assert memory.revoked_tokens("owner-snapshot", include_pending=True) == expected


@pytest.mark.parametrize("unscoped", [None, ""], ids=["none", "empty"])
def test_owner_revocation_snapshot_withholds_unscoped_pending_erasure(tmp_path, unscoped):
    from core.vool_memory import VoolMemory

    with VoolMemory(runtime_home=tmp_path / "runtime") as memory:
        memory.record_revocation("MINT84", chat_id="pending-chat")
        memory.record_revocation("BLUE62", chat_id="healthy-chat")
        operation = memory.begin_erasure("pending-chat", "MINT84")
        assert memory.revoked_tokens("healthy-chat", include_pending=True) == ("blue62",)
        with pytest.raises(RuntimeError, match="incomplete"):
            memory.revoked_tokens(unscoped, include_pending=True)
        memory.complete_erasure(operation)
        assert set(memory.revoked_tokens(unscoped, include_pending=True)) == {"mint84", "blue62"}


def test_common_reader_gate_withholds_when_owner_revocation_read_fails(tmp_path, monkeypatch):
    import sqlite3

    from core.memory import revocation as authority
    from core.vool_memory import VoolMemory

    home = tmp_path / "runtime"
    with VoolMemory(runtime_home=home) as memory:
        assert authority.authorize_payload("The bicycle is blue.", "owner-read-error", home, owner_memory=memory)
        failures = []

        def unavailable(scope, **kwargs):
            failures.append(scope)
            raise sqlite3.OperationalError("owner revocation snapshot unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(memory, "revoked_tokens", unavailable)
            assert not authority.authorize_payload("The bicycle is blue.", "owner-read-error", home, owner_memory=memory)
        assert failures == ["owner-read-error"]
        assert authority.authorize_payload("The bicycle is blue.", "owner-read-error", home, owner_memory=memory)
