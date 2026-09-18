"""Served acceptance for the P0 chat-lifecycle blocker (checkpoint D, 2026-09-03).

Recorded defect: a successful ``POST /api/mode`` ``op=resolve_approval`` poisoned every
later ``/api/chat`` turn on the daemon — any session, mutation or plain — with
``sqlite3.OperationalError: disk I/O error`` at ``storage/db.py`` ``_make_connection``; a
409 did not poison; the daemon recovered only on process restart. Root cause and the
storage-side contract repair live in ``storage/db.py`` + ``tests/test_storage_db_wal_generation.py``.

This file proves the OPERATOR-facing law on the real served door: approve one prompted
mutation, then keep chatting — same session, a different session, a third session, and a
whole second daemon generation. No turn may die with ``no_answer_terminal``/``stream_error``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import ServedDaemon
from tests.test_vool_database_chat_journey import (  # noqa: F401
    ORDERS_DDL,
    ORDERS_SEED,
    SequencedToolProvider,
    _chat,
    _chat_tool_loop,
    _find_approval_id,
    _seed_stub_model,
    journey,
)

pytestmark = [pytest.mark.served]

CREATE_ARGS = {"name": "orders", "schema_sql": ORDERS_DDL, "seed_sql": ORDERS_SEED}


def _resolve_approval(daemon: ServedDaemon, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = Request(
        f"{daemon.base_url}/api/mode",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _assert_turn_answered(turn: dict[str, Any], *, label: str) -> None:
    final = turn.get("final", {})
    terminal = final.get("vool_terminal")
    if isinstance(terminal, dict):
        assert terminal.get("status") != "no_answer_terminal", f"{label} died: {json.dumps(final)[:400]}"
    commit = final.get("vool_response_commit")
    assert isinstance(commit, dict), f"{label} produced no response commit: {json.dumps(final)[:400]}"
    assert commit.get("status") == "answer_present", f"{label} not answered: {json.dumps(commit)[:300]}"


def test_after_an_approved_mutation_chat_keeps_answer_new_sessions_and_generations(
    journey,  # noqa: F811
) -> None:
    daemon: ServedDaemon = journey["daemon"]
    home: Path = journey["home"]
    env: dict[str, str] = journey["env"]

    with SequencedToolProvider() as provider:
        _seed_stub_model(home, provider)
        script = [lambda offer: provider.call_for("vool-database.db.create", CREATE_ARGS, offer=offer)]
        turn = _chat_tool_loop(
            daemon,
            provider,
            "answer from the orders database the exact created receipt, no guessing, by calling vool-database.db.create.",
            script,
            session="wal-lifecycle-approve",
        )
        approval_id = _find_approval_id(turn, home)

        status, body = _resolve_approval(
            daemon,
            {
                "op": "resolve_approval",
                "approval_id": approval_id,
                "decision": "allow",
                "session_id": "wal-lifecycle-approve",
            },
        )
        assert status == 200, body

        # THE RECORDED BLOCKER: every later turn on this daemon died with
        # disk I/O error at storage/db.py. Same session first, then unrelated
        # and brand-new sessions — all must answer.
        retry = _chat(daemon, "one sentence: is the orders database created?", session="wal-lifecycle-approve")
        _assert_turn_answered(retry, label="post-approval same-session turn")
        other = _chat(daemon, "one sentence about the weather, no tools", session="wal-lifecycle-other-session")
        _assert_turn_answered(other, label="post-approval unrelated-session turn")
        third = _chat(daemon, "say ready, no tools", session="wal-lifecycle-third-session")
        _assert_turn_answered(third, label="post-approval third-session turn")

    # A whole second daemon generation on the same home keeps answering.
    daemon.stop()
    restarted = ServedDaemon(home, env_extra=env)
    restarted.start(timeout=240)
    try:
        gen2 = _chat(restarted, "one sentence: are you there?", session="wal-lifecycle-gen2")
        _assert_turn_answered(gen2, label="second-generation turn")
        gen2b = _chat(restarted, "and one more sentence, no tools", session="wal-lifecycle-gen2-b")
        _assert_turn_answered(gen2b, label="second-generation follow-up turn")
    finally:
        restarted.stop()
