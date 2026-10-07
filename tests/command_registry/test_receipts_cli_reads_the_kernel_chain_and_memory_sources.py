"""The operator CLI reads the memory kernel's receipts: `receipts verify` checks the kernel chain, and
`memory source` opens the verbatim source behind a remembered line.

Before this, the kernel receipt chain (core.evidence_kernel.receipts) was written on every turn but never read:
`receipts verify` checked only the honesty chain, so a tampered or truncated kernel ledger passed, and the source
materializer (core.context_retrieval.materialize_source_evidence) had no caller outside tests. Every value below is
written for this file.
"""
from __future__ import annotations

import json

import pytest

from core.command_registry.execute import ExecutionContext, execute_command


@pytest.fixture()
def home(tmp_path, monkeypatch):
    from core.runtime_paths import configure_runtime_home

    profile = tmp_path / "profile"
    profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    monkeypatch.setenv("VOOL_EVIDENCE_KERNEL", "1")
    configure_runtime_home(profile)
    from storage.migrations import run_migrations

    run_migrations()
    yield profile
    configure_runtime_home(None)


def _issue(session: str, n: int) -> None:
    from core.evidence_kernel.receipts import issue

    for i in range(n):
        issue(kind="vool.memory.turn.v1", session_id=session, subject_type="memory_receipt",
              subject={"turn": i, "session": session}, status="recorded", commit=True)


def _verify(**inp):
    return execute_command("receipts.verify", inp, context=ExecutionContext(projection="cli"))


def test_verify_reports_the_kernel_chain(home):
    _issue("chat-a", 2)
    _issue("chat-b", 1)
    env = _verify()
    assert env.ok, env.to_json()
    kernel = env.data["kernel_chain"]
    assert kernel["ledgers"] == 2 and kernel["envelopes"] == 3 and kernel["broken"] == [], kernel


def test_verify_one_session_reads_only_its_ledger(home):
    _issue("chat-a", 2)
    _issue("chat-b", 1)
    env = _verify(session_id="chat-a")
    assert env.ok and env.data["kernel_chain"]["ledgers"] == 1 and env.data["kernel_chain"]["envelopes"] == 2, env.to_json()


@pytest.mark.parametrize("tamper", ["edit", "truncate"])
def test_a_tampered_or_truncated_kernel_ledger_fails_verify(home, tamper):
    from core.evidence_kernel.receipts import ledger_path

    _issue("chat-a", 3)
    path = ledger_path("vool.memory.turn.v1", "chat-a")
    lines = path.read_text(encoding="utf-8").splitlines()
    if tamper == "edit":
        row = json.loads(lines[1])
        row["status"] = "forged"
        lines[1] = json.dumps(row)
    else:
        lines = lines[:-1]                                  # the head file still names the deleted last envelope
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = _verify()
    assert not env.ok, env.to_json()
    assert "kernel" in env.summary.lower(), env.summary


def _stored_occurrence(home) -> str:
    import core.context_retrieval as cr
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace("chat-src", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="chat-src")
    cr.store_turn("chat-src", "The boat club meets at the north jetty on Thursdays.", "Noted.", access_policy=policy,
                  source_context={"chat_id": "chat-src", "runtime_home": str(home)})
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=str(home))
    try:
        rows = mem._conn.execute(
            "SELECT occurrence_id FROM source_occurrences WHERE chat_scope = ? AND role = 'user'", ("chat-src",)
        ).fetchall()
    finally:
        close = getattr(mem, "close", None)
        if callable(close):
            close()
    assert rows
    return str(rows[0][0])


def test_memory_source_opens_the_verbatim_source(home):
    occurrence_id = _stored_occurrence(home)
    env = execute_command("memory.source", {"occurrence_id": occurrence_id, "query": "boat club"},
                          context=ExecutionContext(projection="cli"))
    assert env.ok, env.to_json()
    assert env.data["complete"] is True and "north jetty" in env.data["body"], env.data


def test_memory_source_of_an_unknown_id_says_so_and_invents_nothing(home):
    env = execute_command("memory.source", {"occurrence_id": "no-such-occurrence"}, context=ExecutionContext(projection="cli"))
    assert not env.ok, env.to_json()
    assert "occurrence_not_found" in json.dumps(env.to_json()), env.to_json()
