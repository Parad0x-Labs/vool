from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.context_namespace import ensure_chat_namespace
from core.memory.entries import (
    add_memory_fact,
    resolve_memory_access_policy,
    search_relevant_memory,
)
from core.memory.files import load_jsonl, memory_entries_path
from core.runtime_paths import configure_runtime_home


@pytest.fixture
def memory_home(tmp_path):
    configure_runtime_home(tmp_path)
    try:
        yield tmp_path
    finally:
        configure_runtime_home(None)


def _rows() -> list[dict[str, object]]:
    return load_jsonl(memory_entries_path())


def test_user_correction_supersedes_lower_authority_fact(memory_home) -> None:
    chat_id = "chat:correction"
    ensure_chat_namespace(chat_id)
    fact_key = "identity:favorite-color"
    assert add_memory_fact(
        "My favorite color is blue.",
        session_id=chat_id,
        fact_key=fact_key,
        authority="confirmed_memory",
    )
    assert add_memory_fact(
        "Actually, my favorite color is green.",
        session_id=chat_id,
        fact_key=fact_key,
        authority="user_correction",
    )

    rows = [row for row in _rows() if row.get("fact_key") == fact_key]
    assert [row["status"] for row in rows] == ["superseded", "active"]
    assert rows[0]["superseded_record_id"] == rows[1]["record_id"]
    selected = search_relevant_memory(
        "favorite color",
        access_policy=resolve_memory_access_policy(chat_id=chat_id),
    )
    assert [row["text"] for row in selected] == [
        "Actually, my favorite color is green."
    ]


def test_equal_authority_conflict_is_disputed_and_withheld(memory_home) -> None:
    chat_id = "chat:dispute"
    ensure_chat_namespace(chat_id)
    fact_key = "location:office"
    assert add_memory_fact(
        "The office is in Seattle.",
        session_id=chat_id,
        fact_key=fact_key,
    )
    assert add_memory_fact(
        "The office is in Vancouver.",
        session_id=chat_id,
        fact_key=fact_key,
    )

    rows = [row for row in _rows() if row.get("fact_key") == fact_key]
    assert [row["status"] for row in rows] == ["disputed", "disputed"]
    assert search_relevant_memory(
        "office location",
        access_policy=resolve_memory_access_policy(chat_id=chat_id),
    ) == []


def test_model_inference_and_expired_fact_never_enter_active_prompt(
    memory_home,
) -> None:
    chat_id = "chat:quarantine"
    ensure_chat_namespace(chat_id)
    assert add_memory_fact(
        "The user probably likes purple.",
        session_id=chat_id,
        fact_key="preference:color",
        authority="model_inference",
        source="assistant_inference",
    )
    assert add_memory_fact(
        "Temporary code is 8142.",
        session_id=chat_id,
        fact_key="temporary:code",
        expires_at=(
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat(),
    )

    rows = _rows()
    assert rows[0]["status"] == "disputed"
    assert search_relevant_memory(
        "purple temporary code",
        access_policy=resolve_memory_access_policy(chat_id=chat_id),
    ) == []
