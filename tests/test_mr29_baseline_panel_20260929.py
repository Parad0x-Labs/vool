"""MR29 VALIDATION — frozen-baseline panel (role V, memory-recovery-swarm-20260929).

Representative reproduction of the CONFIRMED baseline failures at foundation
e821457d0836d408e1823eb974dcc1089f67a523, on the real write/read seams with
correct conftest/profile placement (per-case disposable VOOL_HOME, migrations
run, deterministic hash-BoW embedding backend, capsule v2 enabled for read).

Panel intent (NOT fresh acceptance — these cases ride the same seams as the
lead's repro1 and the mission synthesis; fresh acceptance is authored
separately and frozen before candidate execution):

  bp01/bp02  assistant-only evidence loss (live finalization seam:
             append_conversation_event -> store_turn deletes assistant_text)
  bp03       short high-importance fact killed by the 30-char length gate
  bp04       ordinary statement killed by the 0.35 importance gate
  bp05       computed importance never passed to node_store (tag vs column)
  bp09       terse correction: current value must be retrievable
  bp12       chat deletion must cover every recall surface (semantic store
             AND transcript hydration), CONTRACT mr29/1 §1.5

  Controls expected GREEN at base: bp06 (no quote auto-promotion),
  bp07 (foreign chat cannot read), bp08 (no-answer abstention),
  bp11 (stored fact survives reopen).

Every case prints the measured capsule/row content into the failure message so
the first-run record preserves the actual observed behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home


@pytest.fixture()
def fresh_profile(tmp_path, monkeypatch):
    """Per-case disposable profile: VOOL_HOME asserted under pytest basetemp."""
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    assert str(home) in str(Path(home).resolve())
    # Deterministic offline embedding backend (hash-BoW), labeled as such.
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    yield str(home)
    configure_runtime_home(None)


def _policy(chat: str):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    return resolve_memory_access_policy(chat_id=chat)


def _finalize(home: str, chat: str, user_input: str, assistant_output: str) -> None:
    """The production finalized-turn seam (chat_surface/turn_reasoning owner)."""
    from core.persistent_memory import append_conversation_event

    append_conversation_event(
        session_id=chat,
        user_input=user_input,
        assistant_output=assistant_output,
        source_context={"surface": "api", "platform": "api", "chat_id": chat,
                        "runtime_home": home},
        access_policy=_policy(chat),
    )


def _store(home: str, chat: str, text: str):
    return cr.store_turn(
        chat, text, "Noted.", access_policy=_policy(chat),
        source_context={"chat_id": chat, "runtime_home": home},
    )


def _capsule(home: str, chat: str, query: str) -> str:
    out = cr.inject_retrieved(
        chat, query, [{"role": "user", "content": query}],
        access_policy=_policy(chat),
        source_context={"chat_id": chat, "runtime_home": home},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    return next(
        (str(m.get("content") or "") for m in out
         if m.get("role") == "system" and "<retrieved_context>" in str(m.get("content") or "")),
        "",
    )


def _node_rows(home: str, chat: str) -> list[dict]:
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=home)
    try:
        scope_tag = f"session:v2:{__import__('hashlib').sha256(chat.encode()).hexdigest()}"
        rows = []
        for row in mem._conn.execute(
            "SELECT content, base_importance, tags FROM memory_nodes ORDER BY timestamp"
        ):
            tags = json.loads(row["tags"]) if row["tags"] else []
            if scope_tag in [str(t) for t in tags]:
                rows.append({"content": row["content"],
                             "base_importance": row["base_importance"],
                             "tags": [str(t) for t in tags]})
        return rows
    finally:
        mem.close()


# ---- assistant-evidence loss (lead repro1 R1 seam) --------------------------

def test_bp01_assistant_only_statement_retrievable_after_reopen(fresh_profile):
    chat = "bp-clinic"
    _finalize(
        fresh_profile, chat,
        "Where did you say the clinic moved?",
        "The clinic moved to 12 rue des Lilas on March 3rd; "
        "the old site on avenue Calmette closed for good.",
    )
    ctx = _capsule(fresh_profile, chat, "What is the clinic's new address?")
    assert "rue des Lilas" in ctx, {"capsule": ctx, "nodes": _node_rows(fresh_profile, chat)}


def test_bp02_assistant_answer_extends_user_turn(fresh_profile):
    chat = "bp-wifi"
    _finalize(
        fresh_profile, chat,
        "What's the wifi password at the workshop?",
        "The workshop wifi password is driftwood-harbor-42.",
    )
    ctx = _capsule(fresh_profile, chat, "wifi password workshop")
    assert "driftwood-harbor-42" in ctx, {"capsule": ctx, "nodes": _node_rows(fresh_profile, chat)}


# ---- admission gates (lead repro1 R2/R3 seams) ------------------------------

def test_bp03_short_fact_below_length_gate(fresh_profile):
    chat = "bp-pin"
    status = _store(fresh_profile, chat, "My PIN is 4920.")
    ctx = _capsule(fresh_profile, chat, "What is my PIN?")
    assert "4920" in ctx, {"store_status": status, "capsule": ctx,
                           "nodes": _node_rows(fresh_profile, chat)}


def test_bp04_ordinary_statement_importance_gate(fresh_profile):
    chat = "bp-bakery"
    status = _store(fresh_profile, chat,
                    "The bakery on Fifth stopped making rye bread.")
    ctx = _capsule(fresh_profile, chat, "Does the Fifth bakery still make rye bread?")
    assert "stopped making rye" in ctx or "no longer" in ctx.lower(), {
        "store_status": status, "capsule": ctx, "nodes": _node_rows(fresh_profile, chat)}


# ---- ranking input propagation (lead repro1 R4 seam) ------------------------

def test_bp05_declared_importance_reaches_node_column(fresh_profile):
    chat = "bp-passport"
    status = _store(
        fresh_profile, chat,
        "Remember this: my sister's passport number is 849302.",
    )
    assert status["status"] == "stored", status
    rows = _node_rows(fresh_profile, chat)
    assert rows, "no session-scoped node rows"
    declared = [float(t.split(":", 1)[1]) for r in rows for t in r["tags"]
                if t.startswith("importance:")]
    assert declared, rows
    for row, want in zip(rows, declared, strict=False):
        assert abs(row["base_importance"] - want) < 1e-6, {
            "row": row, "declared_in_tag": want,
            "note": "store_turn computes importance into the tag but omits the "
                    "node_store importance argument (synthesis finding 6)"}


# ---- quoted third-party text (control: no auto-promotion at base) -----------

def test_bp06_quoted_third_party_not_promoted_to_user_profile(fresh_profile):
    chat = "bp-dentist"
    _finalize(
        fresh_profile, chat,
        "My dentist said: 'floss twice daily and skip the mouthwash'.",
        "Noted — I'll remember that advice.",
    )
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=fresh_profile)
    try:
        blocks = [dict(r) for r in mem._conn.execute(
            "SELECT block_name, content FROM memory_blocks")]
    finally:
        mem.close()
    assert not any("floss" in str(b.get("content") or "").lower() for b in blocks), {
        "blocks": blocks,
        "note": "quoted text must not auto-promote to user profile blocks"}


# ---- scope controls ----------------------------------------------------------

def test_bp07_foreign_chat_cannot_read(fresh_profile):
    chat_a = "bp-scope-a"
    chat_b = "bp-scope-b"
    _store(fresh_profile, chat_a, "Remember: the boathouse code is 5541.")
    ctx = _capsule(fresh_profile, chat_b, "What is the boathouse code?")
    assert "5541" not in ctx, {"capsule": ctx}


def test_bp08_unrelated_question_yields_empty_capsule(fresh_profile):
    chat = "bp-abstain"
    _store(fresh_profile, chat,
           "Remember: the boathouse code is 5541.")
    ctx = _capsule(fresh_profile, chat, "Who waters the orchids?")
    assert ctx == "", {"capsule": ctx}


# ---- correction semantics ----------------------------------------------------

def test_bp09_terse_correction_current_value_retrievable(fresh_profile):
    chat = "bp-office"
    _store(fresh_profile, chat, "Remember: my office is on floor 9.")
    _store(fresh_profile, chat, "Update: my office moved to floor 11.")
    ctx = _capsule(fresh_profile, chat, "Which floor is my office on now?")
    assert "floor 11" in ctx, {"capsule": ctx, "nodes": _node_rows(fresh_profile, chat)}


# ---- roundtrip control -------------------------------------------------------

def test_bp11_stored_fact_survives_fresh_reopen(fresh_profile):
    chat = "bp-roundtrip"
    _store(fresh_profile, chat,
           "Remember this: my sister's passport number is 849302.")
    rows = _node_rows(fresh_profile, chat)  # fresh VoolMemory open on disk
    assert any("849302" in r["content"] for r in rows), rows


# ---- deletion coverage (CONTRACT mr29/1 §1.5) --------------------------------

def test_bp12_chat_deletion_covers_all_recall_surfaces(fresh_profile):
    chat = "bp-forget"
    token = "marlin-key-77"
    _finalize(
        fresh_profile, chat,
        f"Remember: the storage unit key is {token}.",
        f"Got it — the storage unit key is {token}.",
    )
    # semantic store DOES serve it before deletion
    pre = _capsule(fresh_profile, chat, "What is the storage unit key?")
    assert token in pre, {"pre_delete_capsule": pre,
                          "note": "deletion case needs the fact to exist first"}

    from core.context_retrieval import forget_session_memory

    forget_session_memory(chat, token, access_policy=_policy(chat),
                          source_context={"chat_id": chat,
                                          "runtime_home": fresh_profile})
    post = _capsule(fresh_profile, chat, "What is the storage unit key?")
    assert token not in post, {"post_delete_capsule": post}

    # transcript hydration must not re-serve the deleted body either
    from core.persistent_memory import augment_history_from_session_log

    hydrated = augment_history_from_session_log(
        [], session_id=chat, user_text="What is the storage unit key?")
    assert not any(token in str(m.get("content") or "") for m in hydrated), {
        "hydrated": hydrated,
        "note": "CONTRACT mr29/1 §1.5: deletion must cover source bodies and any "
                "cache that can re-serve them; conversation_log.jsonl keeps the body"}
