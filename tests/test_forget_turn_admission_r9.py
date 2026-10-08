"""The real turn door admits a forget before any memory-bearing side effect."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile


@pytest.fixture(autouse=True)
def _isolated_admission_database(tmp_path, runtime_storage_reset):
    from storage.db import active_default_db_path, configure_default_db_path
    from storage.migrations import run_migrations

    previous = active_default_db_path()
    configure_default_db_path(tmp_path / "turn-admission.db")
    try:
        run_migrations()
        yield
    finally:
        configure_default_db_path(previous)


def _carrier_state(home):
    from storage.db import get_connection

    memory = {}
    for path in (home / "data" / "memory").glob("*.db"):
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            memory[path.name] = list(connection.iterdump())
    files = {path.name: path.read_bytes() for path in (home / "data").iterdir()
             if path.suffix in {".jsonl", ".json", ".md"}
             and path.name in {"memory_entries.jsonl", "conversation_log.jsonl", "session_summaries.jsonl",
                               "chat_session_meta.json", "user_heuristics.jsonl", "operator_dense_profile.json", "MEMORY.md"}}
    connection = get_connection()
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = {}
        for name in ("dialogue_turns", "dialogue_sessions", "dialogue_topic_archives", "active_mission_slots",
                     "runtime_checkpoints", "operator_profile_items", "operator_profile_history", "operator_profile_state"):
            if name in tables:
                rows[name] = sorted((tuple(row) for row in connection.execute(f'SELECT * FROM "{name}"')), key=repr)
    finally:
        connection.close()
    return memory, files, rows


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("user_text", ["Forget MINT84.", "Forget my name. Forget MINT84."])
@pytest.mark.parametrize("failure_point", ["write", "lock", "directory"])
def test_run_once_unstarted_forget_preserves_all_memory_carriers(tmp_path, monkeypatch, user_text, failure_point):
    from core import operator_profile
    from core.agent_runtime.agent import VoolAgent
    from core.honesty_receipt import list_honesty_receipts
    from core.memory import revocation
    from core.memory.command_result import verified_forget_outcome

    home = _profile(tmp_path)
    chat = "r9-unstarted-turn"
    _ingest(home, chat, [(1760000000, "My locker code is MINT84.")])
    saved = operator_profile.remember("owner_local", "preferred_name", "Alex", session_id=chat, turn_id="seed")
    assert saved.kind == "saved"
    agent = VoolAgent(backend_name="test-backend", device="admission-fixture", persona_id="default")
    context = {"chat_id": chat, "runtime_home": str(home), "client_turn_id": "admission-failure"}
    before = _carrier_state(home)
    denied = []

    def no_barrier(path, payload):
        denied.append(path.name)
        raise OSError("durable admission unavailable")

    with monkeypatch.context() as patch:
        if failure_point == "write":
            patch.setattr(revocation, "_durable_write", no_barrier)
        elif failure_point == "lock":
            original_flock = revocation.fcntl.flock

            def deny_first_lock(fd, operation):
                if operation == revocation.fcntl.LOCK_EX and not denied:
                    denied.append("lock")
                    raise OSError("admission lock unavailable")
                return original_flock(fd, operation)

            patch.setattr(revocation.fcntl, "flock", deny_first_lock)
        else:
            from pathlib import Path

            original_mkdir = Path.mkdir

            def deny_first_directory(path, *args, **kwargs):
                if path == home / ".forget-authority" and not denied:
                    denied.append("directory")
                    raise OSError("admission directory unavailable")
                return original_mkdir(path, *args, **kwargs)

            patch.setattr(Path, "mkdir", deny_first_directory)
        result = agent.run_once(user_text, session_id_override=chat, source_context=context)
    assert denied
    outcome = verified_forget_outcome(result, user_input=user_text, session_id=chat, source_context=context)
    assert outcome is not None and outcome.status == "not_started", result
    assert "Nothing was forgotten" in result["response"] and "try again" in result["response"]
    assert _carrier_state(home) == before
    assert operator_profile.resolve("owner_local", "preferred_name", session_id=chat).value_text == "Alex"
    receipt = list_honesty_receipts(chat)[-1]
    assert receipt["claimed_actions"] == []
    assert "not_started" in receipt["verdict_detail"]
    assert result["receipt_write_status"] in {"written", "already_exists"}


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("user_text", ["Forget MINT84.", "Forget my name. Forget MINT84."])
def test_run_once_completed_forget_retires_only_its_admission(tmp_path, user_text):
    from core import operator_profile
    from core.agent_runtime.agent import VoolAgent
    from core.memory import revocation
    from core.memory.command_result import verified_forget_outcome

    home = _profile(tmp_path)
    chat = "r9-completed-turn"
    _ingest(home, chat, [(1760000000, "My locker code is MINT84.")])
    operator_profile.remember("owner_local", "preferred_name", "Alex", session_id=chat, turn_id="seed")
    agent = VoolAgent(backend_name="test-backend", device="admission-fixture", persona_id="default")
    context = {"chat_id": chat, "runtime_home": str(home), "client_turn_id": "admission-success"}
    result = agent.run_once(user_text, session_id_override=chat, source_context=context)
    outcome = verified_forget_outcome(result, user_input=user_text, session_id=chat, source_context=context)
    assert outcome is not None and outcome.status == "applied", result
    rows = json.loads((home / ".forget-authority" / "ledger.json").read_text())["operations"]
    admissions = [row for row in rows.values() if row.get("kind") == "admission"]
    assert admissions and all(row["status"] == "complete" for row in admissions)
    tokens = revocation.snapshot(chat, home)
    assert "mint84" in tokens
    assert all(row["token"] not in tokens for row in admissions)
    assert not revocation.authorize_payload("My locker code is MINT84.", chat, home)
    assert revocation.authorize_payload("The bicycle is blue.", chat, home)


@pytest.mark.usefixtures("_hash_backend")
def test_admission_cannot_complete_after_only_one_of_two_forgets(tmp_path):
    from core.memory import revocation
    from core.persistent_memory import _finish_forget_turn_admission, _preadmit_forget_turn, maybe_handle_memory_command
    from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

    home = _profile(tmp_path)
    chat = "r9-two-demands"
    text = "Forget MINT84. Forget BLUE62."
    _ingest(home, chat, [(1760000000, "My locker code is MINT84."), (1760000010, "The archive label is BLUE62.")])
    context = {"chat_id": chat, "runtime_home": str(home)}
    context[TURN_REQUEST_KEY] = TurnRequest.from_ingress(
        user_text=text, source_context=context, request_id="req:r9-two-demands", turn_id="r9-two-demands", session_id=chat,
    )
    assert _preadmit_forget_turn(text, session_id=chat, source_context=context) == (True, None)
    _, first = maybe_handle_memory_command("Forget MINT84.", session_id=chat, source_context=context)
    _finish_forget_turn_admission({"response": first}, user_text=text, session_id=chat, source_context=context)
    with pytest.raises(RuntimeError):
        revocation.snapshot(chat, home)
    _, second = maybe_handle_memory_command("Forget BLUE62.", session_id=chat, source_context=context)
    _finish_forget_turn_admission({"response": second}, user_text=text, session_id=chat, source_context=context)
    assert {"mint84", "blue62"} <= set(revocation.snapshot(chat, home))


@pytest.mark.usefixtures("_hash_backend")
def test_new_turn_after_restart_can_complete_identical_abandoned_admission(tmp_path):
    from core.agent_runtime.agent import VoolAgent
    from core.memory import revocation

    home = _profile(tmp_path)
    chat = "r9-retry-admission"
    _ingest(home, chat, [(1760000000, "My locker code is MINT84.")])
    worker = """
import sys
from core.persistent_memory import _preadmit_forget_turn
from core.turn_contract import TURN_REQUEST_KEY, TurnRequest
home, chat = sys.argv[1:]
context = {'chat_id': chat, 'runtime_home': home}
context[TURN_REQUEST_KEY] = TurnRequest.from_ingress(user_text='Forget MINT84.', source_context=context, request_id='req:abandoned', turn_id='abandoned', session_id=chat)
assert _preadmit_forget_turn('Forget MINT84.', session_id=chat, source_context=context) == (True, None)
"""
    child = subprocess.run([sys.executable, "-c", worker, str(home), chat], capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr
    with pytest.raises(RuntimeError):
        revocation.snapshot(chat, home)
    agent = VoolAgent(backend_name="test-backend", device="admission-fixture", persona_id="default")
    result = agent.run_once("Forget MINT84.", session_id_override=chat,
                            source_context={"chat_id": chat, "runtime_home": str(home), "client_turn_id": "retry-new-turn"})
    assert result["response"].startswith("Forget applied"), result
    assert "mint84" in revocation.snapshot(chat, home)


@pytest.mark.usefixtures("_hash_backend")
def test_cancelled_forget_does_not_leave_a_barrier_for_the_next_turn(tmp_path):
    from core.agent_runtime.agent import VoolAgent
    from core.memory import revocation
    from core.persistent_memory import _preadmit_forget_turn
    from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

    home = _profile(tmp_path)
    chat = "r9-cancelled-admission"
    text = "Forget MINT84. WAIT, STOP. Instead, tell me a joke."
    _ingest(home, chat, [(1760000000, "My locker code is MINT84."), (1760000010, "The archive label is BLUE62.")])
    context = {"chat_id": chat, "runtime_home": str(home)}
    context[TURN_REQUEST_KEY] = TurnRequest.from_ingress(
        user_text=text, source_context=context, request_id="req:cancelled", turn_id="cancelled", session_id=chat,
    )
    before = (home / ".forget-authority" / "ledger.json").read_bytes()
    assert _preadmit_forget_turn(text, session_id=chat, source_context=context) == (False, None)
    assert (home / ".forget-authority" / "ledger.json").read_bytes() == before
    agent = VoolAgent(backend_name="test-backend", device="admission-fixture", persona_id="default")
    result = agent.run_once("Forget BLUE62.", session_id_override=chat,
                            source_context={"chat_id": chat, "runtime_home": str(home), "client_turn_id": "next-after-cancel"})
    assert result["response"].startswith("Forget applied"), result
    assert "mint84" not in revocation.snapshot(chat, home)
    assert revocation.authorize_payload("My locker code is MINT84.", chat, home)
