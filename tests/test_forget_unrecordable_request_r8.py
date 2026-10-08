"""Unadmitted forgets preserve stores and report that nothing was forgotten."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

_CHILD = r'''
import json
import sys
from core.memory import revocation

home, mode, occurrence_id = sys.argv[1:]
chat = "unrecordable-forget"
if mode == "forget":
    denied = []
    original_open = revocation.Path.open

    def deny_ledger(path, payload):
        denied.append(path.name)
        raise OSError("durable authority writes unavailable")

    def deny_first_lease(path, *args, **kwargs):
        if path.name.startswith("lease-") and args and "x" in args[0]:
            denied.append(path.name)
            raise OSError("first durable lease unavailable")
        return original_open(path, *args, **kwargs)

    revocation._durable_write = deny_ledger
    revocation.Path.open = deny_first_lease
    from core.persistent_memory import maybe_handle_memory_command
    context = {"chat_id": chat, "runtime_home": home,
               "client_turn_id": "unrecordable-request"}
    handled, reply = maybe_handle_memory_command(
        "forget MINT84", session_id=chat, source_context=context,
    )
    from core.memory.command_result import verified_forget_outcome
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty, emit_turn_honesty_receipt
    from core.honesty_receipt import list_honesty_receipts
    result = {"response": reply, "mode": "advice_only"}
    owned = verified_forget_outcome(result, user_input="forget MINT84", session_id=chat, source_context=context)
    result = enforce_final_action_honesty(result, user_input="forget MINT84", session_id=chat, source_context=context)
    result = emit_turn_honesty_receipt(result, user_input="forget MINT84", session_id=chat, source_context=context)
    receipt = list_honesty_receipts(chat)[-1]
    print(json.dumps({"handled": handled, "reply": str(reply), "denied": denied,
                      "status": owned.status if owned else None,
                      "final_reply": str(result["response"]), "receipt": receipt,
                      "receipt_write_status": result.get("receipt_write_status")}), flush=True)
else:
    from core.vool_memory import VoolMemory
    memory = VoolMemory(runtime_home=home)
    try:
        if mode == "seed":
            row = memory.occurrence_store(
                chat_scope=chat, role="user", authority="observed-user-statement",
                body="My locker code is MINT84.",
            )
            print(json.dumps({"occurrence_id": row.occurrence_id}), flush=True)
        else:
            row = memory.occurrence_get(occurrence_id)
            print(json.dumps({"body": row.body if row else None}), flush=True)
    finally:
        memory.close()
'''


def _child(home, mode, occurrence_id=""):
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD, str(home), mode, occurrence_id],
        env={**os.environ, "VOOL_HOME": str(home),
             "VOOL_WORKSPACE_ROOT": str(home / "workspace")},
        text=True, capture_output=True, timeout=20, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _authority_bytes(home):
    return {path.name: path.read_bytes()
            for path in (home / ".forget-authority").iterdir() if path.is_file()}


def _memory_rows(home):
    state = {}
    for path in (home / "data" / "memory").glob("*.db"):
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            state[path.name] = list(connection.iterdump())
    return state


@pytest.mark.parametrize("existing_reader", [False, True])
def test_first_barrier_write_failure_withholds_after_receiving_process_exits(tmp_path, existing_reader):
    from core.vool_memory import VoolMemory

    home = tmp_path / "runtime"
    occurrence_id = _child(home, "seed")["occurrence_id"]
    memory = VoolMemory(runtime_home=home) if existing_reader else None
    try:
        # Healthy startup is a required positive control for this same store.
        assert _child(home, "read", occurrence_id)["body"] == "My locker code is MINT84."
        if memory is not None:
            assert memory.occurrence_get(occurrence_id).body == "My locker code is MINT84."
        before = _authority_bytes(home)
        before_memory = _memory_rows(home)

        failed = _child(home, "forget", occurrence_id)

        assert failed["handled"] and "could not be verified" in failed["reply"]
        assert "Nothing was forgotten" in failed["reply"] and "did not start" in failed["reply"]
        assert "try again" in failed["reply"]
        assert failed["status"] == "not_started"
        assert failed["final_reply"] == failed["reply"]
        assert failed["receipt_write_status"] in {"written", "already_exists"}
        assert failed["receipt"]["claimed_actions"] == []
        assert "not_started" in failed["receipt"]["verdict_detail"]
        assert failed["denied"], "the failure injection never reached a durable write"
        assert _authority_bytes(home) == before, "the injected refusal changed durable authority state"
        assert _memory_rows(home) == before_memory, "an unadmitted forget changed an owner store"
        observations = {"restarted_reader": _child(home, "read", occurrence_id)["body"]}
        if memory is not None:
            row = memory.occurrence_get(occurrence_id)
            observations["already_running_reader"] = row.body if row else None
        print(json.dumps({"failed_forget": failed, "observations": observations}, sort_keys=True))
        # ROUND 9 owner amendment: no admission means no erasure or revocation.
        assert all(value == "My locker code is MINT84." for value in observations.values()), observations
    finally:
        if memory is not None:
            memory.close()
