from __future__ import annotations

from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.persistent_memory import (
    append_conversation_event,
    load_operator_dense_profile,
    maybe_handle_memory_command,
    memory_lifecycle_snapshot,
    recent_conversation_events,
    search_session_summaries,
    search_user_heuristics,
    summarize_memory,
)
from core.request_trust import OWNER_LOCAL_KEY


def _policy(chat_id: str):
    ensure_chat_namespace(
        chat_id,
        grant_confirmed_profile=True,
        grant_current_receipts=False,
    )
    return resolve_memory_access_policy(chat_id=chat_id)


def _owner_context(chat_id: str) -> dict[str, object]:
    return {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": chat_id,
        OWNER_LOCAL_KEY: True,
        "conversation_history": [],
    }


def test_memory_commands_remember_and_show_current_contract():
    policy = _policy("openclaw:memory")
    handled, response = maybe_handle_memory_command(
        "remember that I am building a next-gen Telegram bot",
        session_id="openclaw:memory",
        access_policy=policy,
    )
    assert handled is True
    assert "remember" in response.lower()

    handled, response = maybe_handle_memory_command(
        "/memory",
        session_id="openclaw:memory",
        access_policy=policy,
    )
    assert handled is True
    assert "telegram bot" in response.lower()
    assert any(
        "telegram bot" in line.lower()
        for line in summarize_memory(access_policy=policy, limit=8)
    )


def test_append_conversation_event_updates_local_heuristics_and_session_summary():
    policy = _policy("openclaw:heuristics")
    append_conversation_event(
        session_id="openclaw:heuristics",
        user_input="Be brutally honest, concise, use official docs and GitHub repos, and help me build a Telegram bot in Python.",
        assistant_output="Understood.",
        source_context=_owner_context("openclaw:heuristics"),
        access_policy=policy,
    )

    events = recent_conversation_events("openclaw:heuristics", limit=4)
    heuristics = search_user_heuristics(
        "telegram bot github docs python",
        topic_hints=["telegram", "python"],
        limit=8,
        access_policy=policy,
    )
    summaries = search_session_summaries(
        "telegram bot python docs",
        topic_hints=["telegram", "python"],
        limit=3,
        access_policy=policy,
    )

    assert len(events) == 1
    assert any(row["category"] == "response_style" for row in heuristics)
    assert any(row["category"] == "source_preference" for row in heuristics)
    assert any(row["category"] == "preferred_stack" for row in heuristics)
    assert not any(row["category"] == "project_focus" for row in heuristics)
    assert summaries
    assert "telegram bot" in summaries[0]["summary"].lower()


def test_local_first_utility_answer_does_not_need_context_loader(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("local utility answer should not load retrieval context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "what is the day today ?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == "utility_answer"
    assert "today is" in result["response"].lower()


def test_session_summary_recall_handles_light_paraphrase_current_contract():
    policy = _policy("openclaw:semantic-memory")
    append_conversation_event(
        session_id="openclaw:semantic-memory",
        user_input="I am building a next-generation Telegram bot with strong moderation controls.",
        assistant_output="Stored.",
        source_context=_owner_context("openclaw:semantic-memory"),
        access_policy=policy,
    )

    summaries = search_session_summaries(
        "messaging automation with anti abuse controls",
        topic_hints=["messaging"],
        limit=3,
        access_policy=policy,
    )
    assert summaries


def test_future_personalization_can_infer_user_style_from_behavior_without_direct_commands(make_agent):
    agent = make_agent()
    chat_id = "openclaw:future-personalization"
    policy = _policy(chat_id)
    for prompt in (
        "short answer only",
        "again, keep it blunt",
        "no fluff",
        "focus on Python and official docs",
    ):
        append_conversation_event(
            session_id=chat_id,
            user_input=prompt,
            assistant_output="Applied.",
            source_context=_owner_context(chat_id),
            access_policy=policy,
        )

    result = agent.run_once(
        "help me sketch a bot plan",
        session_id_override=chat_id,
        source_context=_owner_context(chat_id),
    )

    assert "official docs first" in result["response"].lower()
    assert len(result["response"].splitlines()) <= 4


def test_dense_profile_does_not_import_prior_chat_project_history(make_agent):
    agent = make_agent()
    prior_chat = "openclaw:prior-companion"
    new_chat = "openclaw:new-companion"
    prior_policy = _policy(prior_chat)
    _policy(new_chat)
    append_conversation_event(
        session_id=prior_chat,
        user_input="We are building a Telegram bot in Python. Use official docs first and keep it blunt.",
        assistant_output="Stored.",
        source_context=_owner_context(prior_chat),
        access_policy=prior_policy,
    )
    append_conversation_event(
        session_id=prior_chat,
        user_input="Next we need moderation controls and an end-to-end smoke run.",
        assistant_output="Stored.",
        source_context=_owner_context(prior_chat),
        access_policy=prior_policy,
    )

    profile = load_operator_dense_profile()
    result = agent.run_once(
        "you know the project, just continue from where we left off",
        session_id_override=new_chat,
        source_context=_owner_context(new_chat),
    )

    lowered = result["response"].lower()
    assert "official_docs_first" in str(profile.get("dense_summary") or "").lower()
    assert "telegram bot build" not in str(profile.get("dense_summary") or "").lower()
    assert "continuing the telegram bot build" not in lowered
    assert "next we need" not in lowered


def test_memory_lifecycle_snapshot_filters_irrelevant_durable_memory_current_contract():
    session_id = "openclaw:memory-lifecycle-filter"
    policy = _policy(session_id)
    handled, _ = maybe_handle_memory_command(
        "remember that project codename is orchid shadow and the stack is python lantern",
        session_id=session_id,
        access_policy=policy,
    )
    assert handled is True
    append_conversation_event(
        session_id=session_id,
        user_input="remember that project codename is orchid shadow and the stack is python lantern",
        assistant_output="Locked in.",
        source_context=_owner_context(session_id),
        access_policy=policy,
    )
    append_conversation_event(
        session_id=session_id,
        user_input="what time is it in Vilnius right now?",
        assistant_output="Current time in Vilnius is 12:34.",
        source_context=_owner_context(session_id),
        access_policy=policy,
    )

    snapshot = memory_lifecycle_snapshot(
        session_id=session_id,
        query_text="what time is it in Vilnius right now?",
        topic_hints=["time", "vilnius"],
        access_policy=policy,
    )

    assert snapshot["recent_conversation_event_count"] >= 2
    assert snapshot["relevant_memory_count"] == 0
    assert snapshot["session_summary_count"] == 0
    assert snapshot["heuristic_count"] == 0
    assert any("what time is it in vilnius right now?" in row["user"].lower() for row in snapshot["recent_turns"])


def test_memory_lifecycle_snapshot_surfaces_relevant_durable_memory_current_contract():
    session_id = "openclaw:memory-lifecycle-relevant"
    policy = _policy(session_id)
    handled, _ = maybe_handle_memory_command(
        "remember that I am building a telegram moderation bot in python",
        session_id=session_id,
        access_policy=policy,
    )
    assert handled is True
    append_conversation_event(
        session_id=session_id,
        user_input="remember that I am building a telegram moderation bot in python",
        assistant_output="Stored.",
        source_context=_owner_context(session_id),
        access_policy=policy,
    )

    snapshot = memory_lifecycle_snapshot(
        session_id=session_id,
        query_text="continue the telegram moderation bot in python",
        topic_hints=["telegram", "python"],
        access_policy=policy,
    )

    assert snapshot["relevant_memory_count"] >= 1
    assert any("telegram moderation bot" in row["text"].lower() for row in snapshot["relevant_memory"])
