"""A continued chat is asked about its own last exchange, and that exchange answers.

Chat import run 3 (2026-11-12): a chat brought in from another app, whose last reply was "We
settled it: the tide table first, then the release ledger.", was asked "What did we settle last
time?" and answered "I don't have that saved, and I looked through your other chats and projects"
-- the recall lane read only the owner's own statements, never the reply the decision was in, and
searched every other chat instead. The same held for a chat continued here, imported or not.
"""
from __future__ import annotations

import types

import pytest

import core.agent_runtime.cross_chat_recall as recall

SID = "openclaw:" + "c" * 20
EXCHANGE = [
    {"role": "user", "content": "Plan the harbor release with me."},
    {"role": "assistant", "content": "We settled it: the tide table first, then the release ledger."},
]


@pytest.fixture
def searched(monkeypatch):
    """The owner's own local chat with nothing saved; records every search of the other chats."""
    monkeypatch.setattr(recall, "_owner_local", lambda _ctx: True)
    monkeypatch.setattr(recall, "_private", lambda *_a: False)
    monkeypatch.setattr(recall, "_memory_rows", lambda *_a: ([], []))
    calls: list[str] = []

    def search(text, **_kwargs):
        calls.append(text)
        return types.SimpleNamespace(ok=True, hits=[], chats_searched=3, projects_searched=1)

    monkeypatch.setattr("core.chat_housekeeping.search_chats", search)
    return calls


def _asked(question: str, *history: dict) -> dict:
    return {"conversation_history": [*history, {"role": "user", "content": question}]}


@pytest.mark.parametrize("question", [
    "What did we settle last time?", "what did we decide?", "what did we finally settle on?",
    "Where did we leave off?", "where were we?",
])
def test_a_question_about_this_chats_own_exchange_is_answered_by_the_chat(searched, question):
    assert recall.cross_chat_recall(question, session_id=SID, source_context=_asked(question, *EXCHANGE)) is None
    assert searched == []


def test_a_decision_about_something_this_chat_discussed_stays_with_the_chat(searched):
    question = "What did we agree on for the ledger?"
    assert recall.cross_chat_recall(question, session_id=SID, source_context=_asked(question, *EXCHANGE)) is None
    assert searched == []


def test_a_decision_about_something_this_chat_never_discussed_is_looked_for_elsewhere(searched):
    question = "What did we decide about the boat name?"
    found = recall.cross_chat_recall(question, session_id=SID, source_context=_asked(question, *EXCHANGE))
    assert found is not None and not found.found and searched == [question]


def test_in_a_new_chat_the_same_question_still_looks_through_the_other_chats(searched):
    question = "What did we settle last time?"
    found = recall.cross_chat_recall(question, session_id=SID, source_context=_asked(question))
    assert found is not None and not found.found and searched == [question]


def test_vools_own_replies_never_hold_the_owners_own_record(searched):
    """Only a question about what WE worked out reads VOOL's replies: "what's my locker code?" after a
    reply that mentions lockers and codes is still the owner's own record, looked for elsewhere."""
    question = "What's my locker code?"
    history = (
        {"role": "user", "content": "Where can I leave my bag at the gym?"},
        {"role": "assistant", "content": "The gym has lockers by the entrance; each one takes a four-digit code."},
    )
    found = recall.cross_chat_recall(question, session_id=SID, source_context=_asked(question, *history))
    assert found is not None and searched


# --- the model reads the whole exchange, not just the last one -----------------------------------
# Chat import run 4 (2026-11-12): with the recall lane fixed, "What did we settle last time?", asked
# one exchange after the imported decision, reached the model with only that last exchange ("Open
# notes.txt." and its reply), so the decision was not in front of it.

@pytest.mark.parametrize("question", [
    "What did we settle last time?", "what did we decide?", "Which one did we pick?", "Where did we leave off?",
    "where were we?", "ok, what did we agree on for the ledger?",
])
def test_a_question_about_this_conversation_continues_it(question):
    from core.context_history_authority import asks_about_this_conversation

    assert asks_about_this_conversation(question)


@pytest.mark.parametrize("text", [
    "We decided to go with the tide table.", "Plan the harbor release with me.", '"what did we decide?" is a line in my script',
    "What did I name the boat?", "When do we fly out?",
])
def test_a_statement_a_quote_or_a_fact_question_is_not_one(text):
    from core.context_history_authority import asks_about_this_conversation

    assert not asks_about_this_conversation(text)


def test_what_we_settled_carries_the_earlier_exchange_to_the_model():
    from types import SimpleNamespace
    from uuid import uuid4

    from core.context_namespace import ensure_chat_namespace
    from core.human_input_adapter import adapt_user_input
    from core.persistent_memory import append_conversation_event
    from core.prompt_normalizer import normalize_prompt

    session_id = f"openclaw:continued:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    adapt_user_input("Open notes.txt.", session_id=session_id)
    append_conversation_event(session_id=session_id, user_input="Open notes.txt.",
                              assistant_output="File notes.txt: Next: label the amber crates.", source_context={})
    question = "What did we settle last time?"
    interpretation = adapt_user_input(question, session_id=session_id)
    assert interpretation.is_continuation is True

    class _NoContext:
        report = SimpleNamespace(to_dict=lambda: {})

        @staticmethod
        def assembled_context(*, prompt_profile: str = "default") -> str:
            return ""

    request = normalize_prompt(
        task=SimpleNamespace(task_id="continued", task_summary=question),
        classification={"task_class": "chat_conversation", "risk_flags": []},
        interpretation=interpretation,
        context_result=_NoContext(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="continued",
        surface="api",
        source_context={
            "surface": "api", "platform": "api", "runtime_session_id": session_id, "chat_id": session_id,
            # The server's carrier for a continued imported chat: the imported exchange, then this chat's own.
            "conversation_history": [
                *EXCHANGE,
                {"role": "user", "content": "Open notes.txt."},
                {"role": "assistant", "content": "File notes.txt: Next: label the amber crates."},
                {"role": "user", "content": question},
            ],
        },
    )
    contents = [message["content"] for message in request.as_openai_messages()]
    assert EXCHANGE[1]["content"] in contents
    assert request.metadata["chat_truth_prompt"]["history_expansion"] == "bounded_broad"
