"""Acceptance regressions for `workspace.git_status`, from phrasings driven at the live daemon.

The bound workspace on the test host, `~/.vool_runtime/workspace`, is NOT a git repository --
`git status` there says "fatal: not a git repository". The tool knows that and answers
"`<path>` is not inside a git repository."

Driven on the deployed build 2026-07-30, "is the working tree clean" answered:

    The working tree is clean. No changes staged or unstaged.

That is a fabricated git status for a directory with no git in it, and it is the worst class of
answer this system can produce -- confident, specific, and false. Three more phrasings ("any
uncommitted changes", "do i have anything staged", "whats changed since the last commit") missed
the same gate and burned 60-75 seconds each before returning nothing.

The cause was ordering: the gate demanded the literal word "git", "repo", "branch" or "commit"
before it would look at the status vocabulary underneath it -- so "working tree", "uncommitted",
"untracked" and "unstaged", which mean nothing but git, could never get past it.
"""
from __future__ import annotations

import pytest

from core.execution.planner import plan_tool_workflow


def _intent(text: str) -> str | None:
    decision = plan_tool_workflow(
        user_text=text,
        task_class="unknown",
        executed_steps=[],
        source_context={"surface": "api", "platform": "api", "workspace": "/tmp/vool-ws"},
    )
    if not decision.handled:
        return None
    return (decision.next_payload or {}).get("intent")


@pytest.mark.parametrize(
    "text",
    [
        # the fabrication: answered "The working tree is clean" for a non-repo
        "is the working tree clean",
        # these three reached no lane and burned 60-75s each
        "any uncommitted changes",
        "do i have anything staged",
        "whats changed since the last commit",
        "are there untracked files",
    ],
)
def test_git_vocabulary_reaches_the_git_tool_without_the_word_git(text: str) -> None:
    assert _intent(text) == "workspace.git_status"


@pytest.mark.parametrize(
    "text",
    ["whats the git status", "git status please", "show me git status"],
)
def test_the_phrasings_that_already_worked_still_do(text: str) -> None:
    assert _intent(text) == "workspace.git_status"


@pytest.mark.parametrize(
    "text",
    [
        # "staged" is ordinary English outside git and must not drag a turn into the repo lane
        "we did a staged rollout last week",
        "plan a staged migration for the database",
        # a question ABOUT git is not a question about THIS repo
        "how do i learn git",
    ],
)
def test_ordinary_english_is_not_a_repo_question(text: str) -> None:
    """The widening is only sound if what it must not claim stays unclaimed."""

    assert _intent(text) is None


def test_a_diff_request_still_goes_to_diff_not_status() -> None:
    assert _intent("show me the git diff") == "workspace.git_diff"
