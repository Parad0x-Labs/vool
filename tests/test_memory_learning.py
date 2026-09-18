from __future__ import annotations

import pytest

from core.context_namespace import ensure_chat_namespace
from core.memory.entries import (
    add_memory_fact,
    resolve_memory_access_policy,
)
from core.memory.files import (
    conversation_log_path,
    memory_entries_path,
    memory_path,
    operator_dense_profile_path,
    session_summaries_path,
    user_heuristics_path,
)
from core.memory.learning import (
    load_operator_dense_profile,
    normalized_history,
    refresh_operator_dense_profile,
    update_session_summary,
)


def setup_function() -> None:
    for path in (
        memory_path(),
        conversation_log_path(),
        memory_entries_path(),
        session_summaries_path(),
        user_heuristics_path(),
        operator_dense_profile_path(),
    ):
        if path.exists():
            path.unlink()


def test_normalized_history_drops_invalid_items() -> None:
    history = normalized_history(
        [
            {"role": "user", "content": " keep this concise "},
            {"role": "assistant", "content": " ok "},
            {"role": "tool", "content": "ignored"},
            {"role": "user", "content": ""},
            "bad",
        ]
    )

    assert history == [
        {"role": "user", "content": "keep this concise"},
        {"role": "assistant", "content": "ok"},
    ]


def test_refresh_operator_dense_profile_excludes_chat_facts_and_summaries() -> None:
    ensure_chat_namespace("dense-a")
    add_memory_fact("Operator prefers concise answers.", session_id="dense-a")
    conversation_log_path().write_text(
        '{"session_id":"dense-a","user":"build telegram bot","assistant":"ok"}\n'
        '{"session_id":"dense-a","user":"keep answers concise","assistant":"stored"}\n',
        encoding="utf-8",
    )

    update_session_summary(
        session_id="dense-a",
        user_input="keep answers concise",
        assistant_output="stored",
    )

    profile = refresh_operator_dense_profile(session_id="dense-a")

    assert profile["share_scope"] == "local_only"
    assert "LOCAL_ONLY" in list(profile.get("policy_tags") or [])
    assert load_operator_dense_profile()["last_session_id"] == "dense-a"
    assert "Facts:" not in str(profile.get("dense_summary") or "")
    assert profile["recent_session_summaries"] == []
    assert profile["memory_facts"] == []


def test_session_summary_omits_keyword_topic_salad() -> None:
    # A session summary must NOT surface conversational filler/typos as "Session topics" — the
    # verbatim "Recent asks" is the truthful signal. Regression for the confabulated recap.
    ensure_chat_namespace("s1")
    conversation_log_path().write_text(
        '{"session_id":"s1","user":"hey","assistant":"hi"}\n'
        '{"session_id":"s1","user":"waht are the top folders pls","assistant":"listed them"}\n'
        '{"session_id":"s1","user":"check my local machine","assistant":"done"}\n',
        encoding="utf-8",
    )
    update_session_summary(session_id="s1", user_input="check my local machine", assistant_output="done")
    stored = session_summaries_path().read_text(encoding="utf-8")
    assert "Recent asks:" in stored          # the truthful signal stays
    assert "Session topics:" not in stored   # the keyword-token salad ("hey, pls, waht") is gone


def test_background_session_summary_does_not_create_profile_grant() -> None:
    session_id = "remote-summary-no-profile-grant"
    ensure_chat_namespace(session_id)
    conversation_log_path().write_text(
        (
            '{"session_id":"remote-summary-no-profile-grant",'
            '"user":"remember this chat only","assistant":"ok"}\n'
        ),
        encoding="utf-8",
    )

    update_session_summary(
        session_id=session_id,
        user_input="remember this chat only",
        assistant_output="ok",
    )

    policy = resolve_memory_access_policy(chat_id=session_id)
    assert policy.allow_user_profile_context is False


def test_session_summary_cannot_create_a_chat_namespace() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        update_session_summary(
            session_id="missing-summary-namespace",
            user_input="must not create a chat",
            assistant_output="blocked",
        )
