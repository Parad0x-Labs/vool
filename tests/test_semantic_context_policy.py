from __future__ import annotations

import pytest

import core.context_retrieval as context_retrieval
from core.bootstrap_context import canonical_runtime_transcript
from core.context_namespace import (
    ensure_chat_namespace,
    load_chat_namespace,
    set_chat_namespace_state,
)
from core.memory.entries import resolve_memory_access_policy
from storage.dialogue_memory import record_dialogue_turn


class _Node:
    def __init__(self, content: str, *, session_id: str):
        scope_key = context_retrieval._session_scope_key(session_id)
        self.content = content
        self.tags = ["user", f"session:{scope_key}"]
        self.context_description = (
            f"session={scope_key} role=user scope=chat status=active"
        )


class _Memory:
    def __init__(self, hits=None):
        self.hits = list(hits or [])
        self.search_sessions: list[str] = []
        self.stored: list[str] = []
        self.store_records: list[dict[str, object]] = []

    def node_search(
        self,
        _embedding,
        *,
        top_k,
        min_score,
        session_id,
    ):
        del top_k, min_score
        self.search_sessions.append(str(session_id))
        return self.hits

    def node_store(
        self,
        *,
        content,
        keywords,
        tags,
        context_description,
        embedding,
        lineage_request_id="",
    ):
        del keywords, embedding
        self.stored.append(str(content))
        self.store_records.append(
            {
                "content": str(content),
                "tags": list(tags),
                "context_description": str(context_description),
            }
        )

    def close(self):
        return None


def _policy(chat_id: str, *, project_id: str = ""):
    ensure_chat_namespace(
        chat_id,
        project_id=project_id,
        grant_current_receipts=False,
    )
    return resolve_memory_access_policy(chat_id=chat_id)


def _record_turn(chat_id: str, text: str) -> None:
    record_dialogue_turn(
        chat_id,
        raw_input=text,
        normalized_input=text,
        reconstructed_input=text,
        speaker_role="user",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=1.0,
        quality_flags=[],
    )


def test_missing_namespace_never_creates_or_queries_semantic_context(
    monkeypatch,
) -> None:
    chat_id = "semantic-missing-namespace"
    transcript = [{"role": "user", "content": "recall the marker"}]
    opened = False

    def _unexpected_open():
        nonlocal opened
        opened = True
        return _Memory()

    monkeypatch.setattr(
        context_retrieval,
        "_open_memory",
        _unexpected_open,
    )

    assert context_retrieval.inject_retrieved(
        chat_id,
        "marker",
        transcript,
    ) is transcript
    context_retrieval.store_turn(
        chat_id,
        "Please remember unique marker MISSING-4410 for later.",
        "Noted.",
    )
    canonical, source = canonical_runtime_transcript(
        session_id=chat_id,
        source_context={"chat_id": chat_id},
        current_user_text="Hi",
    )

    assert canonical == []
    assert source == "scope_denied"
    assert load_chat_namespace(chat_id) is None
    assert opened is False


def test_forged_policy_or_context_cannot_select_foreign_marker(
    monkeypatch,
) -> None:
    foreign_chat = "semantic-foreign-chat"
    current_chat = "semantic-current-chat"
    foreign_policy = _policy(foreign_chat, project_id="project-foreign")
    current_policy = _policy(current_chat, project_id="project-current")
    memory = _Memory(
        [
            (
                _Node(
                    "Foreign marker must stay private: FOREIGN-9081.",
                    session_id=foreign_chat,
                ),
                0.99,
            ),
            (
                _Node(
                    "Current marker is CURRENT-2714.",
                    session_id=current_chat,
                ),
                0.91,
            ),
        ]
    )
    monkeypatch.setattr(context_retrieval, "_open_memory", lambda: memory)
    monkeypatch.setattr(
        context_retrieval,
        "embed",
        lambda _text: [0.1, 0.2],
    )
    transcript = [{"role": "user", "content": "What is the marker?"}]

    rejected = context_retrieval.inject_retrieved(
        current_chat,
        "marker",
        transcript,
        access_policy=foreign_policy,
    )
    rejected_context = canonical_runtime_transcript(
        session_id=current_chat,
        source_context={
            "chat_id": foreign_chat,
            "_trusted_project_id": "project-foreign",
        },
        current_user_text="marker",
        access_policy=current_policy,
    )
    accepted = context_retrieval.inject_retrieved(
        current_chat,
        "marker",
        transcript,
        access_policy=current_policy,
    )
    blob = "\n".join(item["content"] for item in accepted)

    assert rejected is transcript
    assert rejected_context == ([], "scope_denied")
    assert "CURRENT-2714" in blob
    assert "FOREIGN-9081" not in blob
    assert memory.search_sessions == [current_chat]


def test_store_turn_reloads_active_policy_and_persists_its_project(
    monkeypatch,
) -> None:
    chat_id = "semantic-active-store"
    policy = _policy(chat_id, project_id="project-trusted")
    memory = _Memory()
    monkeypatch.setattr(context_retrieval, "_open_memory", lambda: memory)
    monkeypatch.setattr(
        context_retrieval,
        "embed",
        lambda _text: [0.1, 0.2],
    )

    context_retrieval.store_turn(
        chat_id,
        "Please remember active marker ACTIVE-5834 for later.",
        "Noted.",
        access_policy=policy,
        source_context={
            "chat_id": chat_id,
            "_trusted_project_id": "project-trusted",
            "project_id": "caller-forged-project",
        },
    )

    assert memory.stored == [
        "Please remember active marker ACTIVE-5834 for later."
    ]
    assert "project:project-trusted" in memory.store_records[0]["tags"]
    assert (
        "project=project-trusted"
        in memory.store_records[0]["context_description"]
    )


@pytest.mark.parametrize("state", ["archived", "deleted"])
def test_inactive_namespace_blocks_semantic_reads_and_writes(
    monkeypatch,
    state: str,
) -> None:
    chat_id = f"semantic-{state}-chat"
    stale_policy = _policy(chat_id)
    set_chat_namespace_state(chat_id, state)
    memory = _Memory(
        [
            (
                _Node(
                    f"Inactive marker {state.upper()}-7712.",
                    session_id=chat_id,
                ),
                0.95,
            )
        ]
    )
    monkeypatch.setattr(context_retrieval, "_open_memory", lambda: memory)
    monkeypatch.setattr(
        context_retrieval,
        "embed",
        lambda _text: [0.1, 0.2],
    )
    transcript = [{"role": "user", "content": "recall inactive marker"}]

    result = context_retrieval.inject_retrieved(
        chat_id,
        "inactive marker",
        transcript,
        access_policy=stale_policy,
    )
    context_retrieval.store_turn(
        chat_id,
        "Please remember inactive marker INACTIVE-7712 for later.",
        "Noted.",
        access_policy=stale_policy,
    )

    assert result is transcript
    assert memory.search_sessions == []
    assert memory.stored == []


def test_canonical_transcript_reads_only_the_persisted_policy_chat(
    monkeypatch,
) -> None:
    foreign_chat = "canonical-foreign-chat"
    current_chat = "canonical-current-chat"
    _policy(foreign_chat, project_id="project-foreign")
    current_policy = _policy(current_chat, project_id="project-current")
    _record_turn(foreign_chat, "Foreign transcript marker FOREIGN-TURN-6632.")
    _record_turn(current_chat, "Current transcript marker CURRENT-TURN-1845.")
    monkeypatch.setattr(
        context_retrieval,
        "_open_memory",
        lambda: None,
    )

    transcript, source = canonical_runtime_transcript(
        session_id=current_chat,
        source_context={
            "chat_id": current_chat,
            "_trusted_project_id": "project-current",
            "project_id": "caller-forged-project",
        },
        current_user_text="Continue.",
        access_policy=current_policy,
    )
    blob = "\n".join(item["content"] for item in transcript)

    assert source == "structured_dialogue_memory"
    assert "CURRENT-TURN-1845" in blob
    assert "FOREIGN-TURN-6632" not in blob
