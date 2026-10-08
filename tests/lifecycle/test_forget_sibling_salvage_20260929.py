"""Value-scoped forget keeps sibling facts (q90-post-sealed-recovery-20260929).

Sealed-acceptance class F14-02/F14-07/F14-12: a token forget used to destroy
every sibling fact that shared a carrier with the forgotten value — the
whole occurrence body cleared, the statement-granular node hard-deleted, the
log row and dialogue row dropped. The forget law now salvages token-free
clauses at EVERY carrier: nodes are re-admitted as salvage nodes,
occurrence/log/dialogue bodies keep their token-free clauses, and only a
carrier that cannot shed the token dies whole. Fresh domains throughout.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TREE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(TREE))



@pytest.fixture()
def fresh_profile(tmp_path, monkeypatch):
    from core.runtime_paths import configure_runtime_home

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(home / "workspace"))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(home)
    from core import embedding_service

    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from storage.migrations import run_migrations

    run_migrations()
    yield str(home)
    configure_runtime_home(None)


def _capsule(home: str, chat: str, query: str) -> str:
    import core.context_retrieval as cr
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat, grant_current_receipts=False)
    out = cr.inject_retrieved(
        chat, query, [{"role": "user", "content": query}],
        access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={"chat_id": chat, "runtime_home": home},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    return next(
        (str(m.get("content") or "") for m in out
         if m.get("role") == "system"
         and "<retrieved_context>" in str(m.get("content") or "")), "")


def test_assistant_comma_list_sibling_survives_token_forget(fresh_profile):
    """The F14-02 shape in a fresh domain (harbor chandlery): the assistant's
    comma-listed report is the sibling's only carrier. After forgetting the
    code, the pieces count must still serve and the code must be gone from
    every reachable surface."""
    home = fresh_profile
    chat = "salv-chandlry"
    import core.context_retrieval as cr
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import append_conversation_event

    ensure_chat_namespace(chat, grant_current_receipts=False)
    pol = resolve_memory_access_policy(chat_id=chat)
    append_conversation_event(
        session_id=chat,
        user_input="Log the bosun's order please.",
        assistant_output="Order logged: 11 marine fenders, cone-6 canvas, "
                         "protected run with code TARP-9.",
        source_context={"surface": "api", "platform": "api", "chat_id": chat,
                        "runtime_home": home},
        access_policy=pol)
    removed = cr.forget_session_memory(
        chat, "TARP-9", access_policy=pol,
        source_context={"chat_id": chat, "runtime_home": home})
    assert removed == 0  # no semantic node: assistant text never indexes

    capsule = _capsule(home, chat,
                       "How many marine fenders are in the bosun's order?")
    assert "11" in capsule, capsule
    assert "TARP-9" not in capsule, capsule

    # reachable-store absence (the multi-surface deletion standard)
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=home)
    try:
        bodies = [str(r["body"]) for r in mem._conn.execute(
            "SELECT body FROM source_occurrences").fetchall()]
        nodes = [str(r["content"]) for r in mem._conn.execute(
            "SELECT content FROM memory_nodes").fetchall()]
    finally:
        mem.close()
    assert not any("TARP-9" in b for b in bodies + nodes), bodies + nodes
    assert any("11 marine fenders" in b for b in bodies), bodies
    log_text = (Path(home) / "data" / "conversation_log.jsonl").read_text()
    assert "TARP-9" not in log_text
    from storage.dialogue_memory import recent_dialogue_turns

    turns = recent_dialogue_turns(chat, limit=10)
    joined = " ".join(
        str(t.get("raw_input") or "") + str(t.get("normalized_input") or "")
        for t in turns)
    assert "TARP-9" not in joined, joined


def test_user_joint_statement_sibling_node_salvage(fresh_profile):
    """The F14-07/F14-12 shape in a fresh domain (climbing wall): one user
    statement carries both values; forgetting one keeps the other servable."""
    home = fresh_profile
    chat = "salv-wall"
    import core.context_retrieval as cr
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import append_conversation_event

    ensure_chat_namespace(chat, grant_current_receipts=False)
    pol = resolve_memory_access_policy(chat_id=chat)
    append_conversation_event(
        session_id=chat,
        user_input="My novice lane grade is 4 and my lead wall grade is 6.",
        assistant_output="Novice 4, lead 6, noted.",
        source_context={"surface": "api", "platform": "api", "chat_id": chat,
                        "runtime_home": home},
        access_policy=pol)
    removed = cr.forget_session_memory(
        chat, "4", access_policy=pol,
        source_context={"chat_id": chat, "runtime_home": home})
    assert removed == 1  # the joint statement node carried the token

    capsule = _capsule(home, chat, "What grades do I still have on file?")
    assert "6" in capsule, capsule
    assert "novice" not in capsule.lower(), capsule


def test_salvage_never_leaves_a_subjectless_fragment(fresh_profile):
    """q90-post-sealed-recovery (sealed F10-07 regression): a salvaged
    clause must carry its own subject. Forgetting one subject's phrase used
    to leave its bare predicate fragment (\"numbered P-77 by coincidence.\")
    as an active body that shadowed the intact coincidence-value record.
    Fresh domain: museum audio guide."""
    home = fresh_profile
    chat = "salv-guide"
    import core.context_retrieval as cr
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import append_conversation_event

    ensure_chat_namespace(chat, grant_current_receipts=False)
    pol = resolve_memory_access_policy(chat_id=chat)
    append_conversation_event(
        session_id=chat,
        user_input="The docent's guide channel for today is G-42.",
        assistant_output="Guide channel G-42, noted.",
        source_context={"surface": "api", "platform": "api", "chat_id": chat,
                        "runtime_home": home},
        access_policy=pol)
    append_conversation_event(
        session_id=chat,
        user_input="The caretaker's spare handset is also numbered G-42 by coincidence.",
        assistant_output="Spare handset G-42, noted.",
        source_context={"surface": "api", "platform": "api", "chat_id": chat,
                        "runtime_home": home},
        access_policy=pol)
    cr.forget_session_memory(
        chat, "caretaker's spare handset", access_policy=pol,
        source_context={"chat_id": chat, "runtime_home": home})

    capsule = _capsule(home, chat, "Which guide channel is on record for the docent?")
    assert "G-42" in capsule, capsule
    assert "docent" in capsule.lower(), capsule
    assert "coincidence" not in capsule.lower(), capsule
