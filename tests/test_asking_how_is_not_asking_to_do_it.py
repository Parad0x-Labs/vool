"""A request for a command is knowledge, not an instruction to inspect this machine.

Measured live on ff7f0d65, served surface::

    U: give me the git command to squash the last 3 commits. raw text only, no markdown
    A: `<workspace>` is not inside a git repository.
       route=deterministic:workspace_runtime_fast_path

The planner saw "git" and "commits", returned a `workspace.git_*` intent, and the workspace fast
path executed an inspection of this machine. The answer is true and beside the point: nobody asked
whether the workspace was a git repository. The user wanted a command they could run anywhere.

The distinction pinned here is instructional vs observational, and it is not about git -- the clean
family below spans docker, ffmpeg, ssh and sed, none of which appear in the repair.
"""

from __future__ import annotations

import pytest

from core.instructional_request import asks_for_instructions_not_execution as asks_how

# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproduction
# ---------------------------------------------------------------------------------------------


def test_the_reported_request_is_instructional() -> None:
    assert asks_how("give me the git command to squash the last 3 commits. raw text only, no markdown")


# ---------------------------------------------------------------------------------------------
# CLEAN -- other tools, other phrasings, none of them git
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    (
        "how do I mount a volume in docker",
        "what is the command to re-encode a file with ffmpeg",
        "which command copies a key to a remote host",
        "how to replace text in place with sed",
        "show me the syntax for a tar archive",
        "give me the command to list open ports",
        "whats the best way to rotate a log file",
        "explain how to set up a cron job",
    ),
)
def test_instructional_requests_across_unrelated_tools(request_text: str) -> None:
    assert asks_how(request_text), request_text


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- observational requests must still reach the tool lanes
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    (
        "what is my git status",
        "is my workspace a git repo",
        "what changed in this repository",
        "summarize the git history of this project",
        "read the file core/task_router.py",
        "how much disk do I have left",
        "list the files in my project folder",
    ),
)
def test_observational_requests_are_untouched(request_text: str) -> None:
    assert not asks_how(request_text), request_text


def test_a_plain_question_is_not_instructional() -> None:
    assert not asks_how("what is a hash table")
    assert not asks_how("")
    assert not asks_how("   ")


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL NEAR-MISSES
# ---------------------------------------------------------------------------------------------


def test_an_instructional_phrasing_that_names_THIS_target_is_observational() -> None:
    """The load-bearing exception: a possessive or deictic makes it about this machine.

    "how do I check my repo's status" is phrased as instruction but is asking about a target that
    is right here, and the tool lanes may legitimately answer it. Without this the guard would take
    away real capability rather than a misroute.
    """

    assert not asks_how("how do I see my git status here")
    assert not asks_how("how do I check this repository for changes")
    assert not asks_how("how do I list the files in my workspace")


def test_the_word_how_in_passing_is_not_an_instructional_request() -> None:
    """Anchored openers: a sentence merely containing "how" must not qualify."""

    assert not asks_how("tell me how the merge went")
    assert not asks_how("I don't know how big the repo is")


def test_the_guard_declines_rather_than_answering() -> None:
    """This module only classifies. Deciding what happens instead belongs to the caller.

    Pinned so a future edit does not grow it into a responder -- the ordinary lanes already answer
    instructional questions, and a fixed string here would be the exact failure being repaired.
    """

    import inspect

    from core import instructional_request

    source = inspect.getsource(instructional_request)

    assert "response" not in source.lower().replace("response_text", "")
    # Two classifiers, still no responder: `asks_how_to` is the HOW-question half of the first predicate,
    # exposed for a recognizer that seats an action for "draft a reply saying X" and must still decline
    # "how should I write a polite reminder email?" (core.tool_demand_signals.outgoing_message_intents).
    assert instructional_request.__all__ == ["asks_for_instructions_not_execution", "asks_how_to"]


# ---------------------------------------------------------------------------------------------
# The WIRING -- the predicate being right is not the same as the lane consulting it
# ---------------------------------------------------------------------------------------------


def _fast_path(user_input: str, planner):
    """Drive the real workspace fast path with a stubbed planner."""

    from unittest import mock

    from core.agent_runtime.fast_paths_utility import maybe_handle_direct_workspace_runtime_request

    agent = mock.Mock()
    agent._plan_tool_workflow = planner
    return maybe_handle_direct_workspace_runtime_request(
        agent,
        user_input,
        session_id="wiring-test",
        source_surface="api",
        source_context={"workspace": "/tmp/wiring-test-workspace", "surface": "api"},
    )


def test_the_lane_declines_an_instructional_request_without_planning_it() -> None:
    """Sabotage-proof for the wiring, added because removing the guard left the family green.

    The predicate family above only proved the classifier. This proves the LANE consults it: the
    planner must never be reached for an instructional request, because reaching it is what returned
    a `workspace.git_*` intent and executed an inspection nobody asked for.
    """

    planned: list[str] = []

    def planner(**kwargs):
        planned.append(str(kwargs.get("user_text") or ""))
        raise AssertionError("the planner was reached for an instructional request")

    result = _fast_path("give me the git command to squash the last 3 commits", planner)

    assert result is None
    assert not planned, "the instructional request reached the planner"


def test_the_lane_still_plans_an_observational_request() -> None:
    """Negative control on the wiring: the guard must not close the lane for real work."""

    planned: list[str] = []

    def planner(**kwargs):
        planned.append(str(kwargs.get("user_text") or ""))

        class _Decision:
            next_payload = {"intent": "workspace.git_status", "arguments": {}}

        return _Decision()

    _fast_path("what is my git status", planner)

    assert planned, "an observational request must still reach the planner"
