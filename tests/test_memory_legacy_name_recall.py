"""Stored memory written before the rename must not reach the model as a competing identity.

The name was reported fixed three times and kept coming back, because the persona row, the display
name and the system prompt were all correct -- the wrong name was arriving through *recall*. Prompt
capture (`VOOL_DEBUG_PROMPT=1`) showed three distinct injectors in one outbound payload:

    "Relevant durable memory: **My name**: VOOL; ..."     <- search_relevant_memory
    "Last assistant outcome: I'm VOOL."                    <- a prior reply, quoted back and re-copied
    "Continuity: ... "                                      <- a stored session summary

So these assert at the read points, and they assert on the returned text rather than on a file having
changed -- the earlier version of this fix passed a test that only proved the prompt source was
edited, while the running product still said the old name.
"""
from __future__ import annotations

import json

import pytest

from core.context_namespace import ensure_chat_namespace
from core.memory.entries import (
    add_memory_fact,
    canonical_agent_name_in_memory,
    resolve_memory_access_policy,
    search_relevant_memory,
    search_session_summaries,
    summarize_memory,
)
from core.memory.files import (
    memory_entries_path,
    memory_path,
    session_summaries_path,
)
from core.runtime_paths import configure_runtime_home


@pytest.fixture(autouse=True)
def isolated_memory_home(tmp_path, monkeypatch):
    configure_runtime_home(tmp_path / "runtime-home")
    memory_path().parent.mkdir(parents=True, exist_ok=True)
    for path in (memory_entries_path(), session_summaries_path()):
        if path.exists():
            path.unlink()
    memory_path().write_text(
        "# VOOL Persistent Memory\n\n## Learned Knowledge\n\n- **My name**: VOOL\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("core.onboarding.get_agent_display_name", lambda: "VOOL")
    yield
    configure_runtime_home(None)


def _policy(chat_id: str):
    ensure_chat_namespace(chat_id, grant_current_receipts=False)
    return resolve_memory_access_policy(chat_id=chat_id)


def test_durable_memory_recall_does_not_return_the_legacy_name() -> None:
    policy = _policy("s1")
    assert add_memory_fact(
        "**My name**: VOOL",
        category="identity",
        session_id="s1",
        access_policy=policy,
    )

    hits = search_relevant_memory(
        "what is your name",
        access_policy=policy,
        topic_hints=["name"],
        limit=4,
    )

    assert hits, "the row must still be recalled -- the fix rewrites it, it does not hide it"
    recalled = " ".join(str(row.get("text") or "") for row in hits)
    assert "VOOL" in recalled
    assert "VOOL" not in recalled.upper()


def test_prior_session_summary_cannot_quote_the_legacy_name_back() -> None:
    policy = _policy("prior")
    session_summaries_path().write_text(
        json.dumps(
            {
                "session_id": "prior",
                "summary": "Recent asks: whats your name? Last assistant outcome: I'm VOOL.",
                "created_at": "2026-07-28T00:00:00+00:00",
                "turn_count": 2,
                "scope": "chat",
                "source": "session_summary",
                "status": "active",
                "origin_chat_id": "prior",
                "origin_project_id": "",
                "provenance": {
                    "kind": "derived_session_summary",
                    "origin_chat_id": "prior",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    hits = search_session_summaries(
        "name",
        access_policy=policy,
        limit=2,
    )

    assert hits
    assert "VOOL" not in str(hits[0].get("summary") or "").upper()
    assert "VOOL" in str(hits[0].get("summary") or "")


def test_memory_facts_summary_is_canonicalised() -> None:
    policy = _policy("summary")
    assert add_memory_fact(
        "**My name**: VOOL",
        category="identity",
        session_id="summary",
        access_policy=policy,
    )
    facts = " ".join(summarize_memory(access_policy=policy, limit=8))

    assert "VOOL" not in facts.upper()


@pytest.mark.parametrize(
    "stored",
    [
        "~/Desktop/vool-local-product",
        "project_name: vool_runtime/workspace_render_db0cc086",
        "/tmp/vool_proj_test",
        "logger vool.api emitted a warning",
        "run apps.vool_api_server to start it",
    ],
)
def test_real_paths_and_identifiers_survive_untouched(stored: str) -> None:
    # Rewriting a path would hand the model a location that does not exist, which is a worse failure
    # than the wrong name: it turns a cosmetic bug into a tool call against nothing.
    assert canonical_agent_name_in_memory(stored) == stored


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("**My name**: VOOL", "**My name**: VOOL"),
        ("I'm VOOL.", "I'm VOOL."),
        ("VOOL, your local-first assistant", "VOOL, your local-first assistant"),
        ("connect my Vool to my TG account", "connect my VOOL to my TG account"),
    ],
)
def test_standalone_legacy_name_is_rewritten(stored: str, expected: str) -> None:
    assert canonical_agent_name_in_memory(stored) == expected


def test_a_deliberately_chosen_name_is_never_overwritten(monkeypatch) -> None:
    # If the operator renames the assistant to the legacy default on purpose, that is their choice
    # and the mapping must become a no-op rather than fight them.
    monkeypatch.setattr("core.onboarding.get_agent_display_name", lambda: "VOOL")
    assert canonical_agent_name_in_memory("**My name**: VOOL") == "**My name**: VOOL"
