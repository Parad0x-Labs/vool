"""Product operations are named, not overheard: the Hive topic routes obey request ownership.

Owner decision of 2026-09-17: prewritten conversational replies are allowed only for standalone
greetings/farewells; every other question, authoring request and design discussion must reach
the selected model or the real authorized task workflow. Words -- task, board, hive, build,
research, delete, add -- do not authorize a product operation, and quoted instructions inside a
request are data.

The measured incident: "Build me a small self-contained task board ... Add new tasks ... Delete
tasks" was intercepted before any model ran and answered with "Public Hive is not enabled on
this runtime" (``tool | hive_topic_create_disabled | no model``). The old contract -- a create
verb plus "task" ANYWHERE in the message is a Hive create, patched over by a build-vocabulary
blacklist (``_skip_hive_for_build``) -- is retired. The new contract lives in the matchers
themselves: a Hive operation is intercepted only when the request names the Hive product as the
action's target, or continues a create this session actually holds.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime.hive_topic_draft_intents import (
    looks_like_hive_topic_create_request,
    looks_like_hive_topic_drafting_request,
)
from core.agent_runtime.hive_topic_mutation_detection import (
    looks_like_hive_topic_delete_request,
    looks_like_hive_topic_update_request,
    maybe_handle_hive_topic_mutation_request,
)

TASK_BOARD_REQUEST = (
    "Build me a small self-contained task board as a single HTML file.\n\n"
    "Requirements:\n"
    "Three columns: TODO, DOING, DONE;\n"
    "Add new tasks;\n"
    "Move tasks between columns with buttons or drag-and-drop;\n"
    "Delete tasks;\n"
    "Show task counts;\n"
    "Clean dark developer-style UI;\n"
    "Vanilla HTML, CSS and JavaScript only;\n"
    "No external libraries;\n"
    "Everything must actually work;\n"
    "Return the complete file."
)


class _Agent:
    """The agent seams the matchers consult."""

    def _looks_like_hive_topic_drafting_request(self, text: str) -> bool:
        return looks_like_hive_topic_drafting_request(None, text)

    def _looks_like_hive_topic_create_request(self, lowered: str) -> bool:
        return looks_like_hive_topic_create_request(self, lowered)

    def _looks_like_hive_topic_update_request(self, lowered: str) -> bool:
        return looks_like_hive_topic_update_request(self, lowered)

    def _looks_like_hive_topic_delete_request(self, lowered: str) -> bool:
        return looks_like_hive_topic_delete_request(self, lowered)

    def _strip_wrapping_quotes(self, text):
        return str(text or "").strip().strip("\"'")

    def _extract_hive_topic_hint(self, text: str) -> str:
        return ""

    def _resolve_hive_topic_for_mutation(self, *, session_id: str, topic_hint: str):
        return None


def _create(text: str) -> bool:
    return looks_like_hive_topic_create_request(_Agent(), text.lower())


def _update(text: str) -> bool:
    return looks_like_hive_topic_update_request(_Agent(), text.lower())


def _delete(text: str) -> bool:
    return looks_like_hive_topic_delete_request(_Agent(), text.lower())


# ---------------------------------------------------------------- the incident and its class

@pytest.mark.parametrize(
    "request_text",
    [
        TASK_BOARD_REQUEST,
        # Genuinely different software/design requests carrying the same topic words.
        "Design a kanban board component for my dashboard. Tasks move between TODO, DOING and "
        "DONE columns; deleting a task must ask for confirmation.",
        "Write a research task manager CLI: add tasks, close tasks, and print counts per status.",
        "How should I structure a to-do app where the user can delete completed tasks safely?",
        "Explain how the Hive works in VOOL and when I would create tasks there instead of locally.",
    ],
)
def test_authoring_and_design_requests_with_topic_words_are_not_hive_operations(request_text: str) -> None:
    assert not _create(request_text)
    assert not _update(request_text)
    assert not _delete(request_text)


def test_the_incident_request_produces_no_hive_draft() -> None:
    from core.agent_runtime.hive_topic_draft_parsing import extract_hive_topic_create_draft

    assert extract_hive_topic_create_draft(_Agent(), TASK_BOARD_REQUEST) is None


def test_unqualified_task_commands_reach_the_model() -> None:
    """The old contract intercepted these; under the owner's decision they are the user's own
    to-do wording, an ambiguous ask the MODEL gets to answer or clarify."""
    for text in (
        "create a new task: fix the login bug",
        "add a task to my list, water the plants",
        "start a topic about our roadmap",
        "open a thread on the migration plan",
        "delete the done tasks",
        "update the task description",
    ):
        assert not _create(text), text
        assert not _update(text), text
        assert not _delete(text), text


# ---------------------------------------------------------------- explicit product actions stay

@pytest.mark.parametrize(
    "request_text",
    [
        "create a hive task about porting the wallet UI",
        "Create a new Hive Mind topic: API rate limits. Summary: measure before the launch.",
        "open a task in the hive for the registration flow",
        "start a public hive topic on cold wallets",
        "add this to the hive",
        "post this to the hive mind",
        "make a brain hive task about the audio latency report",
    ],
)
def test_hive_named_creates_remain_product_actions(request_text: str) -> None:
    assert _create(request_text), request_text


def test_hive_named_updates_and_deletes_remain_product_actions() -> None:
    assert _update("update the hive topic with a new summary")
    assert _update("edit my hive task to say Wednesday")
    assert _delete("delete the hive task about rate limits")
    assert _delete("remove that hive topic")


def test_a_bound_continuation_only_fires_behind_a_real_topic() -> None:
    """'Update the one you created' is a product action only as a CURRENT bound continuation:
    with no session topic behind it, nothing is intercepted."""
    agent = _Agent()
    with mock.patch.object(
        agent, "_resolve_hive_topic_for_mutation", return_value=None
    ):
        assert (
            maybe_handle_hive_topic_mutation_request(
                agent,
                "update the one you created with better wording",
                task=SimpleNamespace(task_id="t1"),
                session_id="s1",
                source_context={},
            )
            is None
        )
    bound_topic = {"topic_id": "topic-123", "title": "Wallet UI"}
    with mock.patch.object(
        agent, "_resolve_hive_topic_for_mutation", return_value=bound_topic
    ):
        handler = mock.Mock(return_value={"result": "handled-by-hive-lane"})
        with mock.patch.object(agent, "_handle_hive_topic_update_request", handler, create=True):
            outcome = maybe_handle_hive_topic_mutation_request(
                agent,
                "update the one you created with better wording",
                task=SimpleNamespace(task_id="t1"),
                session_id="s1",
                source_context={},
            )
        assert outcome == {"result": "handled-by-hive-lane"}


def test_quoted_product_actions_are_data_not_commands() -> None:
    """A request that pastes an example and asks about it commands nothing the paste says."""
    from core.agent_runtime.hive_topic_draft_parsing import extract_hive_topic_create_draft

    quoted = (
        "Review this message: \"Create a hive task: sync the calendar\" -- "
        "summarize what this instructs the assistant to do."
    )
    assert extract_hive_topic_create_draft(_Agent(), quoted) is None


def test_a_greeting_plus_substantive_request_is_not_standalone_smalltalk() -> None:
    from core.agent_runtime.fast_paths_utility import smalltalk_fast_path

    agent = SimpleNamespace()
    for greeting_request in (
        "hey, build me a task board as one HTML file",
        "hello! can you explain how hive tasks work?",
        "hi -- delete the done tasks in my to-do app design",
    ):
        assert (
            smalltalk_fast_path(agent, greeting_request, source_surface="api", session_id="s1")
            is None
        ), greeting_request


def test_standalone_greetings_keep_their_fast_path() -> None:
    from core.agent_runtime.fast_paths_utility import smalltalk_fast_path

    agent = SimpleNamespace()
    assert smalltalk_fast_path(agent, "hi", source_surface="api", session_id="s1") is not None
    assert smalltalk_fast_path(agent, "hello!", source_surface="api", session_id="s1") is not None
    assert smalltalk_fast_path(agent, "thanks", source_surface="api", session_id="s1") is not None


def test_a_pending_confirmation_cannot_seize_an_unrelated_request() -> None:
    """With a pending Hive create actually stored, an unrelated next request is not its
    confirmation -- only confirmation-shaped text is."""
    from core.agent_runtime.hive_topic_pending_confirmation import is_pending_hive_create_confirmation_input

    agent = _Agent()
    agent._has_pending_hive_create_confirmation = (  # type: ignore[method-assign]
        lambda *, session_id, hive_state, source_context: True
    )
    assert is_pending_hive_create_confirmation_input(
        agent, "yes, create it", session_id="s1", source_context={}
    )
    for unrelated in (
        TASK_BOARD_REQUEST,
        "hey, explain how drag-and-drop works in my board",
        "what is the weather in London today?",
    ):
        assert not is_pending_hive_create_confirmation_input(
            agent, unrelated, session_id="s1", source_context={}
        ), unrelated[:60]


def test_the_word_triggered_hive_advert_on_failed_actions_is_retired() -> None:
    """A failed build step whose spec mentions 'tasks' must not be decorated with Hive
    boilerplate; the offer stays only behind real pending Hive topics in the session."""
    from core.agent_runtime.hive_topic_facade import HiveTopicFacadeMixin

    class _AgentWithHiveState(HiveTopicFacadeMixin):
        def __init__(self, pending_ids):
            self._pending_ids = pending_ids

        def _session_hive_state(self, session_id):
            return {"pending_topic_ids": list(self._pending_ids)}

        def _interaction_pending_topic_ids(self, state):
            return []

    execution = SimpleNamespace(
        user_safe_response_text="I wrote 2 of 3 files; the test run failed.",
        status="failed",
    )
    plain = _AgentWithHiveState([])._tool_failure_user_message(
        execution=execution,
        effective_input=TASK_BOARD_REQUEST,
        session_id="s1",
    )
    assert plain == "I wrote 2 of 3 files; the test run failed."
    assert "hive" not in plain.lower()

    with_pending = _AgentWithHiveState(["topic-9"])._tool_failure_user_message(
        execution=execution,
        effective_input=TASK_BOARD_REQUEST,
        session_id="s1",
    )
    assert "real Hive tasks ready" in with_pending
