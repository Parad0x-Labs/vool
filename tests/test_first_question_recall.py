"""Order-of-asking recall answers from this chat's own persisted transcript.

The reported failure (2026-10-29 re-audit, settings F01): "what did I ask you first"
repeated the SECOND question. Two causes sat on the same lane — the named-actor guard
read a capitalised first-person "I" as a third-party name, and the deterministic
transcript reader had no ordering ask class at all, so the ask fell to a model reading
a context window with no order semantics.

These tests pin the contract on the fast path the served turn actually runs:
the answer quotes this chat's persisted user messages in order, never another chat,
never a greeting row, never the current ask itself; "before that" resolves against
the last reference our own reply made; an empty chat is said plainly; and the
near-miss shapes (an exam's first question, an introduced "my first question is …",
an ordering of actions, an outside person) keep their ordinary lanes.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.same_chat_recall import same_chat_transcript_recall_fast_path
from core.context_history_authority import is_same_chat_history_recall


@pytest.fixture
def chat_home(tmp_path, monkeypatch):
    """An isolated VOOL_HOME so persisted rows land in the test's own store."""
    from core import runtime_paths

    home = tmp_path / "vool-home"
    home.mkdir(parents=True, exist_ok=True)
    prior = runtime_paths._VOOL_HOME_OVERRIDE
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    try:
        yield home
    finally:
        runtime_paths.configure_runtime_home(prior)


def _persist_turn(session_id: str, user: str, assistant: str) -> None:
    from core.persistent_memory import append_conversation_event

    append_conversation_event(
        session_id=session_id,
        user_input=user,
        assistant_output=assistant,
        source_context={"surface": "api"},
        persist_now=True,
    )


def _recall(ask: str, *, session_id: str, history: list[dict[str, str]] | None = None):
    source_context: dict[str, object] = {"surface": "api"}
    if history is not None:
        source_context["conversation_history"] = history
    return same_chat_transcript_recall_fast_path(
        ask,
        source_surface="api",
        source_context=source_context,
        session_id=session_id,
    )


def _seed_chat(session_id: str, questions: list[str]) -> None:
    for index, question in enumerate(questions, start=1):
        _persist_turn(session_id, question, f"answer {index}")


FIRST_ASKS = (
    "what did I ask you first",
    "What did I ask you first?",
    "what did i ask you first",
    "what was my first question",
    "what's my first question in this chat",
    "what was the first thing I asked you",
    "remind me what I asked first",
)


@pytest.mark.parametrize("ask", FIRST_ASKS)
@pytest.mark.parametrize("count", [1, 2, 5])
def test_first_question_quotes_the_earliest_persisted_user_message(
    chat_home, ask: str, count: int
) -> None:
    questions = [f"question number {i}" for i in range(1, count + 1)]
    _seed_chat("chat-a", questions)

    recalled = _recall(ask, session_id="chat-a")

    assert recalled is not None
    assert recalled.found is True
    assert recalled.response == 'Your first question in this chat was: "question number 1"'
    assert recalled.recall_kind == "first_question"


def test_first_question_never_reads_another_chat(chat_home) -> None:
    _seed_chat("chat-other", ["what is my bank pin", "where did i put my cv"])
    _persist_turn("chat-a", "how big is the biggest file", "12 GB")

    recalled = _recall("what did I ask you first", session_id="chat-a")

    assert recalled is not None
    assert recalled.response == 'Your first question in this chat was: "how big is the biggest file"'


def test_greeting_rows_are_never_the_first_question(chat_home) -> None:
    # A greeting the app wrote with no user side, then an assistant-side greeting row in
    # the visible copy: neither may count, and neither may shift the order.
    _persist_turn("chat-g", "", "Hi! I'm here — what do you need?")
    _persist_turn("chat-g", "hey", "Hello! What can I do for you?")
    _persist_turn("chat-g", "what's the biggest file on my machine", "It's the VM image.")

    recalled = _recall(
        "what did I ask you first",
        session_id="chat-g",
        history=[
            {"role": "assistant", "content": "Hi! I'm here — what do you need?"},
            {"role": "user", "content": "hey"},
            {"role": "assistant", "content": "Hello! What can I do for you?"},
            {"role": "user", "content": "what's the biggest file on my machine"},
            {"role": "assistant", "content": "It's the VM image."},
            {"role": "user", "content": "what did I ask you first"},
        ],
    )

    assert recalled is not None
    assert recalled.response == 'Your first question in this chat was: "hey"'


def test_empty_chat_says_so_plainly(chat_home) -> None:
    recalled = _recall("what did I ask you first", session_id="brand-new-chat")

    assert recalled is not None
    assert recalled.found is False
    assert recalled.response == "This is the first thing you've asked in this chat."


def test_private_chat_answers_from_its_own_visible_transcript_only(chat_home) -> None:
    # A chat private from its first message persists nothing: the visible copy is the
    # only record, and it is this chat's own — another chat's rows must not appear.
    _seed_chat("chat-public", ["what is my bank pin"])

    recalled = _recall(
        "what did I ask you first",
        session_id="chat-private",
        history=[
            {"role": "user", "content": "where did i put my cv"},
            {"role": "assistant", "content": "In Documents."},
            {"role": "user", "content": "what did I ask you first"},
        ],
    )

    assert recalled is not None
    assert recalled.found is True
    assert recalled.response == 'Your first question in this chat was: "where did i put my cv"'


BEFORE_THAT_ASKS = (
    "what did I ask before that",
    "what did i say before that",
    "the one before that?",
    "what about the one before",
    "before that?",
)


@pytest.mark.parametrize("ask", BEFORE_THAT_ASKS)
def test_before_that_names_the_message_before_the_last_reference(chat_home, ask: str) -> None:
    _seed_chat(
        "chat-b",
        [
            "how do I start a fresh chat",
            "what's the biggest file on my machine",
            "whats running on my computer right now",
        ],
    )
    # Our own quote reply is the reference the follow-up resolves against.
    _persist_turn(
        "chat-b",
        "what did I ask you first",
        'Your first question in this chat was: "how do I start a fresh chat"',
    )

    recalled = _recall(ask, session_id="chat-b")

    assert recalled is not None
    # The last referenced message was the FIRST question; before it there was nothing.
    assert recalled.response == "You hadn't asked anything before that in this chat."


def test_before_that_after_a_before_reply_walks_one_further_back(chat_home) -> None:
    _seed_chat(
        "chat-c",
        [
            "how do I start a fresh chat",
            "what's the biggest file on my machine",
            "whats running on my computer right now",
        ],
    )
    _persist_turn(
        "chat-c",
        "what did I ask you first",
        'Your first question in this chat was: "how do I start a fresh chat"',
    )
    _persist_turn(
        "chat-c",
        "what did I ask before that",
        'Before that, you asked: "what\'s the biggest file on my machine"',
    )

    recalled = _recall("what did I ask before that", session_id="chat-c")

    assert recalled is not None
    assert (
        recalled.response
        == 'Before that, you asked: "how do I start a fresh chat"'
    )


def test_before_that_without_a_prior_reference_uses_the_most_recent_message(chat_home) -> None:
    _seed_chat(
        "chat-d",
        [
            "how do I start a fresh chat",
            "what's the biggest file on my machine",
            "whats running on my computer right now",
        ],
    )

    recalled = _recall("what did I ask before that", session_id="chat-d")

    assert recalled is not None
    assert (
        recalled.response
        == 'Before that, you asked: "what\'s the biggest file on my machine"'
    )


def test_before_that_with_a_single_prior_message_says_nothing_came_before(chat_home) -> None:
    _persist_turn("chat-e", "how do I start a fresh chat", "Just say hello.")

    recalled = _recall("what did I ask before that", session_id="chat-e")

    assert recalled is not None
    assert recalled.response == "You hadn't asked anything before that in this chat."


def test_a_retried_ask_is_not_counted_as_its_own_predecessor(chat_home) -> None:
    # A failed attempt whose row carries the same text as the live ask: the live ask
    # must not become "the first question" by trailing-equality.
    _persist_turn("chat-f", "what's the biggest file on my machine", "")
    _persist_turn("chat-f", "what did I ask you first", 'Your first question in this chat was: "what\'s the biggest file on my machine"')
    _persist_turn("chat-f", "what did I ask you first", 'Your first question in this chat was: "what\'s the biggest file on my machine"')

    recalled = _recall("what did I ask you first", session_id="chat-f")

    assert recalled is not None
    assert (
        recalled.response
        == 'Your first question in this chat was: "what\'s the biggest file on my machine"'
    )


NEAR_MISS_ASKS = (
    # an exam's first question is not this chat's
    "what is the first question on the quiz",
    # introducing a question, not recalling one
    "my first question is how do I start a fresh chat",
    # an ordering of actions the person requested, not of their asks
    "what did I ask you to do first",
    # an outside person keeps the ordinary lane
    "what did Astrid ask before",
    "What title did Toni Morrison give her novel earlier?",
)


@pytest.mark.parametrize("ask", NEAR_MISS_ASKS)
def test_near_miss_shapes_are_not_answered_from_the_transcript(chat_home, ask: str) -> None:
    _seed_chat("chat-n", ["what's the biggest file on my machine"])

    assert _recall(ask, session_id="chat-n") is None


@pytest.mark.parametrize(
    "ask",
    (
        "what did I ask you first",
        "what did i ask you first",
        "what was my first question",
        "what was the first thing I asked you",
        "remind me what I asked first",
        "what did I say before that",
        "the one before that?",
        "before that?",
        "what about the one before",
    ),
)
def test_ordering_asks_gain_history_recall_authority(ask: str) -> None:
    assert is_same_chat_history_recall(ask) is True


@pytest.mark.parametrize(
    "ask",
    (
        "what is the first question on the quiz",
        "my first question is how do I start a fresh chat",
        "put the one before the shelf back",
        "the one before that file is the draft",
    ),
)
def test_near_miss_shapes_do_not_gain_history_recall_authority(ask: str) -> None:
    assert is_same_chat_history_recall(ask) is False


def test_capitalised_first_person_is_not_an_outside_person(chat_home) -> None:
    # The exact reported shape: capital "I" must pass the named-actor guard.
    _seed_chat("chat-cap", ["how do I start a fresh chat", "what's the biggest file on my machine"])

    recalled = _recall("What did I ask you first?", session_id="chat-cap")

    assert recalled is not None
    assert (
        recalled.response
        == 'Your first question in this chat was: "how do I start a fresh chat"'
    )


def test_unreadable_store_declines_rather_than_claiming_an_empty_chat(
    chat_home, monkeypatch
) -> None:
    from core.agent_runtime import same_chat_recall

    def _broken_read(_session_id, limit=6, include_artifacts=False):  # noqa: ANN001
        raise OSError("store unreadable")

    monkeypatch.setattr(
        "core.persistent_memory.recent_conversation_events", _broken_read
    )

    assert (
        _recall("what did I ask you first", session_id="chat-unknown") is None
    )
