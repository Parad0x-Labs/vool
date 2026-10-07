"""No plain SHA-256 of user text leaves the memory store; occurrence ids are random and opaque.

The kernel's receipt ledgers (files beside the store) carried plain SHA-256 digests of every stored sentence, packet
line, question and reply. Anyone holding a ledger could confirm a guessed sentence ("my locker code is 4417") by
hashing it. Every user-text digest in a ledger is now an HMAC under this install's local key, created once and
owner-only, so the same text digests differently on another install.
"""
from __future__ import annotations

import calendar
import hashlib
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

SENTENCES = ["My gym locker code is 4417, I keep forgetting it.", "I paid 45 dollars for the lamp and 30 dollars for the rug."]
QUESTION = "How much did I spend on the lamp and the rug together?"
REPLY = "$75 total: lamp $45 and rug $30."


def _epoch(y, m, d):
    return float(calendar.timegm(datetime(y, m, d, 9, 0, tzinfo=timezone.utc).timetuple()))


def _install(root: Path, monkeypatch) -> Path:
    import core.embedding_service as embedding_service

    profile = root / "profile"; profile.mkdir(parents=True)
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("VOOL_CONTEXT_CAPSULE_V2", "VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_HOP",
                 "VOOL_EVIDENCE_VERIFY", "VOOL_EVIDENCE_KERNEL"):
        monkeypatch.setenv(name, "1")
    configure_runtime_home(profile)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    return profile


def _use(profile: Path, chat: str, monkeypatch) -> str:
    import core.bootstrap_context as bootstrap_context
    from core.unsourced_current_claim import inspect_unsourced_current_claim

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for i, text in enumerate(SENTENCES):
        cr.store_turn(chat, text, "Noted.", access_policy=policy,
                      source_context={"chat_id": chat, "runtime_home": str(profile), "statement_at": _epoch(2025, 4, 1 + i)})
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(chat, QUESTION, [{"role": "user", "content": QUESTION}], access_policy=policy,
                              source_context={"chat_id": chat, "runtime_home": str(profile)})
    block = "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))
    monkeypatch.setattr(bootstrap_context, "admitted_capsule_evidence_text", lambda *_a, **_k: block)
    inspect_unsourced_current_claim(answer=REPLY, requires_current=True, user_turn_text=QUESTION,
                                    source_context={"chat_id": chat}, session_id=chat)
    return block


def _ledger_text(profile: Path) -> str:
    root = profile / "data" / "receipts_v2"
    return "\n".join(p.read_text() for p in root.rglob("*.jsonl"))


def test_no_ledger_carries_a_plain_sha256_of_user_text(tmp_path, monkeypatch):
    profile = _install(tmp_path / "a", monkeypatch)
    block = _use(profile, "chat-ledger", monkeypatch)
    ledgers = _ledger_text(profile)
    assert ledgers, "the kernel wrote no ledger"
    candidates = set(SENTENCES) | {QUESTION, REPLY} | {line for line in block.splitlines() if line.strip()}
    plain = {hashlib.sha256(text.encode("utf-8")).hexdigest() for text in candidates}
    found = set(re.findall(r"[0-9a-f]{64}", ledgers))
    assert not (plain & found), "a ledger carries a plain SHA-256 of user text"
    assert "k1:" in ledgers
    key = profile / "data" / "receipts_v2" / ".digest_key"
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600


def test_the_same_text_digests_differently_on_another_install(tmp_path, monkeypatch):
    from core.evidence_kernel.receipts import keyed_digest

    _install(tmp_path / "a", monkeypatch)
    first = keyed_digest(SENTENCES[0])
    assert keyed_digest(SENTENCES[0]) == first  # stable inside one install
    _install(tmp_path / "b", monkeypatch)
    assert keyed_digest(SENTENCES[0]) != first


def test_occurrence_ids_are_random_and_not_derived_from_the_text(tmp_path, monkeypatch):
    profile = _install(tmp_path / "a", monkeypatch)
    chat = "chat-ids"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    ids = []
    for day in (1, 2):
        out = cr.store_turn(chat, SENTENCES[0], "", access_policy=policy,
                            source_context={"chat_id": chat, "runtime_home": str(profile), "statement_at": _epoch(2025, 5, day)})
        ids.extend(out["occurrence_ids"])
    assert len(ids) == 2 and ids[0] != ids[1]
    assert all(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", i) for i in ids), ids
    assert hashlib.sha256(SENTENCES[0].encode()).hexdigest()[:8] not in "".join(ids)
