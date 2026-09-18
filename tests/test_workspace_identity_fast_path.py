"""A question about WHICH folder is answered with the folder, not with its contents.

Measured 2026-07-29 by driving the real chat API across 9 phrasings x 4 workspaces (a non-git
project with a space in its path, a large git repo, a small JS project, and an empty folder):
**4 of 36 passed**. Four phrasings were claimed by the folder-overview reader and answered with an
8-, 14- or 46-line directory listing; five fell through to the model, took 3-60s, and never named
the folder at all.

A detector for this already existed — `_asks_which_workspace` in `core/execution/planner.py` — but
its only production caller is the WORKFLOW planner, which an ordinary chat turn never reaches. The
test that covered it imported the detector directly and asserted it returned True, which proved the
regex worked and nothing whatever about the route. That is why the failure survived being "fixed".

Two orderings had to be established, and the second was only found by measuring:

1. Ahead of the workspace-read handler and the overview reader in the front door — all three match
   a demonstrative plus "folder", and the overview reader is the nearest thing that fires.
2. Ahead of the overview dispatch inside `_execute_arbitrated_family`. The intent arbiter dispatches
   to a family directly, so the front door's ordering does not apply there. With the fix only in the
   front door the score went 4/36 -> 27/36, and the nine survivors were exactly the three phrasings
   the arbiter classified as folder-overview. With both, 36/36.

After the change: identity 36/36, overview 12/12, greeting 8/8 — the neighbouring intents still
claim their own questions.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_utility import (
    _WORKSPACE_ABOUT_RE,
    _WORKSPACE_IDENTITY_RE,
    maybe_handle_workspace_identity_request,
)
from core.execution.planner import _asks_which_workspace

# Verbatim from the driven matrix, including the owner's own no-apostrophe and imperative forms.
IDENTITY_PHRASINGS = (
    "what folder is in use for this workspace?",
    "do you see what folder our workspace is set on?",
    "tell me which folder this chat is bound to",
    "show me the workspace folder",
    "confirm the workspace path",
    "whats the current workspace",
    "am i in the right workspace?",
    "which project am i working in",
    "name the folder you are scoped to",
    "where am i",
    "what directory are we in",
    "whats my workspace",
)

# Each of these belongs to another handler and must survive untouched.
NOT_IDENTITY = (
    "what is this project about",
    "explain this codebase",
    "give me an overview of this folder",
    "whats in this repo",
    "describe the project",
    "walk me through this directory",
    "summarise what this code does",
    "find where tool intent is wired in this workspace",
    "list the files in this folder",
    "read app-landing/index.html",
    "create a folder called notes",
    "what is in this directory",
)


class _Agent:
    def _fast_path_result(self, **kwargs):
        return {"reason": kwargs.get("reason"), "response": kwargs.get("response")}


def _handle(text: str, workspace: str):
    return maybe_handle_workspace_identity_request(
        _Agent(),
        text,
        session_id="s",
        source_surface="api",
        source_context={"workspace": workspace, "project_id": "p", "surface": "api"},
    )


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    return str(tmp_path)


# --------------------------------------------------------------------------------------
# Claims the question, in every wording it was actually asked
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("phrasing", IDENTITY_PHRASINGS)
def test_the_folder_question_is_answered_with_the_folder(phrasing, workspace) -> None:
    result = _handle(phrasing, workspace)

    assert result is not None, f"unclaimed, so it falls to the model: {phrasing!r}"
    assert result["reason"] == "workspace_identity_fast_path"
    assert workspace in result["response"], "the answer must name the folder that was asked about"


@pytest.mark.parametrize("phrasing", IDENTITY_PHRASINGS)
def test_the_answer_is_one_line_not_a_directory_listing(phrasing, workspace) -> None:
    """The original complaint: a one-line question answered with a 46-line dump."""

    body = _handle(phrasing, workspace)["response"]
    assert len([line for line in body.splitlines() if line.strip()]) <= 2, body


# --------------------------------------------------------------------------------------
# Does not steal a neighbouring intent
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("phrasing", NOT_IDENTITY)
def test_another_intent_is_left_to_its_own_handler(phrasing, workspace) -> None:
    assert _handle(phrasing, workspace) is None, f"identity stole {phrasing!r}"


def test_about_and_which_are_distinguished_by_sense_not_by_ordering() -> None:
    """"what is this project about" and "which project am i working in" both say "project"."""

    assert _WORKSPACE_ABOUT_RE.search("what is this project about")
    assert not _WORKSPACE_ABOUT_RE.search("which project am i working in")
    assert _WORKSPACE_IDENTITY_RE.search("which project am i working in")


def test_current_chat_recall_does_not_become_a_workspace_identity_request(workspace) -> None:
    """A fictional project named in chat is not the active on-disk workspace."""

    prompt = "What fictional project signal did I just name?"
    assert _asks_which_workspace(prompt) is False
    assert _handle(prompt, workspace) is None


# --------------------------------------------------------------------------------------
# The two orderings, both of which had to be measured to be found
# --------------------------------------------------------------------------------------


def test_identity_runs_before_the_read_and_overview_handlers_in_the_front_door() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "turn_frontdoor.py"
    ).read_text(encoding="utf-8")

    identity = source.index("agent._maybe_handle_workspace_identity_request(")
    read = source.index("agent._maybe_handle_direct_workspace_runtime_request(")
    overview = source.index("agent._maybe_handle_folder_overview_request(")
    assert identity < read < overview, (
        "a metadata question must be claimed before the handlers that read the folder"
    )


def test_the_arbiter_dispatch_checks_identity_before_the_overview_family() -> None:
    """The arbiter bypasses the front door's ordering — 9 of the 36 cells failed on exactly this."""

    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "turn_frontdoor.py"
    ).read_text(encoding="utf-8")
    dispatch = source[source.index("if family == FAMILY_FOLDER_OVERVIEW:"):]
    identity = dispatch.index("_maybe_handle_workspace_identity_request(")
    overview = dispatch.index("_maybe_handle_folder_overview_request(")
    assert identity < overview


# --------------------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------------------


def test_a_non_chat_surface_is_not_claimed(workspace) -> None:
    assert maybe_handle_workspace_identity_request(
        _Agent(), "what folder is in use for this workspace?",
        session_id="s", source_surface="cli", source_context={"workspace": workspace},
    ) is None


def test_a_long_paragraph_that_merely_mentions_a_folder_is_not_claimed(workspace) -> None:
    """A short metadata question is the subject; a paragraph is something else entirely."""

    essay = (
        "so what folder is in use here, and once you know, could you go through each of the "
        "source files and write me a summary of the architecture, then propose a refactor plan "
        "with the tradeoffs spelled out for each option you considered along the way please"
    )
    assert _handle(essay, workspace) is None
