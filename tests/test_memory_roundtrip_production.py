"""Production finalized-turn memory round-trip tests.

These tests deliberately enter through ``append_conversation_event`` rather
than pre-seeding ``store_turn``.  That is the production persistence seam used
by model turns and fast paths, so the tests prove write, scope, retrieval, and
exact-recall behavior together.
"""
from __future__ import annotations

import pytest


def _source_context(home, chat_id: str) -> dict[str, object]:
    return {
        "surface": "api",
        "platform": "api",
        "chat_id": chat_id,
        "runtime_home": str(home),
        "allow_remote_fetch": False,
    }


@pytest.fixture
def isolated_memory_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    return tmp_path


def _activate(chat_id: str):
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat_id, grant_current_receipts=False)
    return resolve_memory_access_policy(chat_id=chat_id)


def test_finalized_turn_persists_and_recalls_exact_identifier(isolated_memory_home, monkeypatch):
    from core import context_retrieval as retrieval
    from core.fact_extractor import stable_text_embedding
    from core.persistent_memory import append_conversation_event

    monkeypatch.setattr(retrieval, "embed", stable_text_embedding)
    chat_id = "roundtrip:identifier"
    policy = _activate(chat_id)
    context = _source_context(isolated_memory_home, chat_id)

    append_conversation_event(
        session_id=chat_id,
        user_input="Please remember this exact identifier: HX-77412.",
        assistant_output="Locked in.",
        source_context=context,
        access_policy=policy,
    )

    with retrieval._open_memory_for_runtime(str(isolated_memory_home)) as memory:
        hits = memory.node_search_hybrid(
            "stored identifier",
            stable_text_embedding("stored identifier"),
            session_id=chat_id,
            min_score=0.0,
        )
    assert any("HX-77412" in node.content for node, _score in hits)

    transcript = [{"role": "user", "content": "What stored identifier should I remember?"}]
    retrieved = retrieval.inject_retrieved(
        chat_id,
        "What stored identifier should I remember?",
        transcript,
        access_policy=policy,
        source_context=context,
    )
    telemetry = retrieval.get_last_retrieval_telemetry()
    assert telemetry["memory_store_status"] == "stored"
    assert "HX-77412" in " ".join(item["content"] for item in retrieved)
    assert retrieval.capsule_exact_response(
        "What stored identifier should I remember?",
        telemetry,
        session_id=chat_id,
    ) == "HX-77412"


def test_finalized_turn_preserves_multiword_code_without_prefix_truncation(
    isolated_memory_home,
    monkeypatch,
):
    from core import context_retrieval as retrieval
    from core.fact_extractor import stable_text_embedding
    from core.persistent_memory import append_conversation_event

    monkeypatch.setattr(retrieval, "embed", stable_text_embedding)
    chat_id = "roundtrip:code"
    policy = _activate(chat_id)
    context = _source_context(isolated_memory_home, chat_id)
    query = "What stored code should I remember?"

    append_conversation_event(
        session_id=chat_id,
        user_input="Please remember this exact code: BAY 13 EAST.",
        assistant_output="Locked in.",
        source_context=context,
        access_policy=policy,
    )

    retrieved = retrieval.inject_retrieved(
        chat_id,
        query,
        [{"role": "user", "content": query}],
        access_policy=policy,
        source_context=context,
    )
    telemetry = retrieval.get_last_retrieval_telemetry()
    assert "BAY 13 EAST" in " ".join(item["content"] for item in retrieved)
    assert retrieval.capsule_exact_response(query, telemetry, session_id=chat_id) == "BAY 13 EAST"
    assert not retrieval.validate_capsule_exact_response(
        "BAY",
        query,
        session_id=chat_id,
        expected_value="BAY 13 EAST",
    )
    assert "BAY\n" not in str(telemetry.get("selected_facts") or [])


def test_finalized_turn_memory_isolated_from_a_new_chat(isolated_memory_home, monkeypatch):
    from core import context_retrieval as retrieval
    from core.fact_extractor import stable_text_embedding
    from core.persistent_memory import append_conversation_event

    monkeypatch.setattr(retrieval, "embed", stable_text_embedding)
    source_chat = "roundtrip:source"
    target_chat = "roundtrip:target"
    source_policy = _activate(source_chat)
    target_policy = _activate(target_chat)
    source_context = _source_context(isolated_memory_home, source_chat)
    target_context = _source_context(isolated_memory_home, target_chat)

    append_conversation_event(
        session_id=source_chat,
        user_input="Please remember this exact identifier: PRIVATE-CHAT-9911.",
        assistant_output="Locked in.",
        source_context=source_context,
        access_policy=source_policy,
    )

    query = "What stored identifier should I remember?"
    retrieved = retrieval.inject_retrieved(
        target_chat,
        query,
        [{"role": "user", "content": query}],
        access_policy=target_policy,
        source_context=target_context,
    )
    assert "PRIVATE-CHAT-9911" not in " ".join(item["content"] for item in retrieved)
