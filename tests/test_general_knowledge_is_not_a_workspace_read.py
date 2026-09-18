"""Guard: naming a well-known filename in a general question must not become a file read.

A QA drive against the live daemon (commit 3fa1a68) sent these verbatim, one fresh session each,
and both came back in 0s -- a deterministic fast path, not a model wobble:

    "explain how a setup.py works"
        -> "File `setup.py` does not exist."
    "whats the difference between requirements.txt and pyproject.toml?"
        -> "File `requirements.txt` does not exist."

Neither is a request to read a file. Both are Python-packaging questions, and the reply is a true
statement about a path the operator never asked about, which reads as a statement about the topic
they did ask about.

A third phrasing from the same drive, also 0s, hit a different gate:

    "is it a good idea to create a Makefile for this project?"
        -> a full directory listing of the workspace

`_direct_workspace_read_request` makes the FILE REFERENCE ITSELF the read signal on purpose: the
gate that required one of five literal read verbs let everything else fall through to a model that
INVENTED file contents. That inversion is not weakened here. The stand-down added alongside it is
keyed on explanation/comparison CONSTRUCTIONS, and an explicit read verb suppresses it -- so the
half of this file that pins the read behaviour matters as much as the half that pins the fix.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.build_request_intent import is_advice_question, is_deliberation
from core.agent_runtime.fast_paths_utility import (
    _asks_about_a_kind_of_file,
    _direct_workspace_read_request,
    maybe_handle_direct_workspace_runtime_request,
    maybe_handle_folder_overview_request,
)

# The three verbatim phrasings from the drive.
EXPLAINS_A_KIND_OF_FILE = "explain how a setup.py works"
COMPARES_TWO_KINDS_OF_FILE = "whats the difference between requirements.txt and pyproject.toml?"
ASKS_WHETHER_TO_ADD_A_FILE = "is it a good idea to create a Makefile for this project?"


@pytest.fixture()
def workspace(tmp_path):
    """A real bound workspace, so a wrong verdict produces the real wrong ANSWER, not a bool."""

    (tmp_path / "notes.txt").write_text("hello from notes\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Zebra Ledger\nA Rust CLI.\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def agent():
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def _context(workspace) -> dict[str, object]:
    return {"surface": "channel", "workspace": str(workspace), "project_id": "regression"}


def _reply(result) -> str:
    if result is None:
        return ""
    return str((result or {}).get("response") or "")


def _read_lane(agent, text, workspace):
    return maybe_handle_direct_workspace_runtime_request(
        agent, text, session_id="kind-of-file", source_surface="channel", source_context=_context(workspace)
    )


def _overview_lane(agent, text, workspace):
    return maybe_handle_folder_overview_request(
        agent, text, session_id="kind-of-file", source_surface="channel", source_context=_context(workspace)
    )


def _front_door(agent, text, workspace):
    """Drive the real turn front door, so a fix that never gets REACHED cannot pass.

    `{"result": None}` means no fast path claimed the turn and it goes on to the model -- which is
    the whole point: a general-knowledge question should be answered, not routed to a file reader.
    """

    with mock.patch.object(agent, "_startup_sequence_fast_path", return_value=None), mock.patch(
        "core.agent_runtime.agent.maybe_handle_preference_command", return_value=(False, "")
    ), mock.patch.object(agent, "_maybe_handle_credit_command", return_value=None), mock.patch.object(
        agent, "_maybe_handle_workspace_audit_request", return_value=None
    ):
        return agent._handle_turn_frontdoor(
            raw_user_input=text,
            effective_input=text,
            normalized_input=text,
            source_surface="channel",
            session_id="kind-of-file-frontdoor",
            source_context=dict(_context(workspace), _owner_local=True),
            persona=mock.sentinel.persona,
            interpreted=SimpleNamespace(understanding_confidence=0.8),
        )


# --------------------------------------------------------------------------------------------
# The reported failures, at the front door.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrasing",
    [EXPLAINS_A_KIND_OF_FILE, COMPARES_TWO_KINDS_OF_FILE, ASKS_WHETHER_TO_ADD_A_FILE],
)
def test_general_knowledge_question_reaches_the_model_not_a_fast_path(agent, workspace, phrasing):
    assert _front_door(agent, phrasing, workspace) == {"result": None}, (
        f"{phrasing!r} was claimed by a fast path; it is a general question, not a workspace request"
    )


@pytest.mark.parametrize("phrasing", [EXPLAINS_A_KIND_OF_FILE, COMPARES_TWO_KINDS_OF_FILE])
def test_a_named_kind_of_file_is_not_answered_with_does_not_exist(agent, workspace, phrasing):
    """The exact reported wrong answer, asserted as text -- a decline alone would not catch it."""

    reply = _reply(_read_lane(agent, phrasing, workspace))
    assert "does not exist" not in reply.lower(), f"{phrasing!r} -> {reply!r}"
    assert _read_lane(agent, phrasing, workspace) is None


def test_asking_whether_to_add_a_file_is_not_answered_with_a_directory_listing(agent, workspace):
    reply = _reply(_overview_lane(agent, ASKS_WHETHER_TO_ADD_A_FILE, workspace))
    assert "notes.txt" not in reply and "README.md" not in reply, (
        f"an advice question was answered with the folder's contents: {reply!r}"
    )
    assert _overview_lane(agent, ASKS_WHETHER_TO_ADD_A_FILE, workspace) is None


# --------------------------------------------------------------------------------------------
# The same constructions in phrasings the drive never sent.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrasing",
    [
        # explanation of a KIND of file
        "explain how a setup.py works",
        "what is a pyproject.toml",
        "how does a tsconfig.json work",
        "why would someone use a requirements.txt",
        "when should i use a setup.py instead",
        "what does a package.json do",
        "whats the point of a conftest.py",
        # comparison of two kinds
        "whats the difference between requirements.txt and pyproject.toml?",
        "difference between package.json and package-lock.json",
        "requirements.txt vs pyproject.toml",
        "compare setup.py and pyproject.toml",
        "pros and cons of a setup.cfg",
        "how do setup.py and pyproject.toml differ",
        # generic plural
        "explain what pyproject.toml files are for",
        "why do people commit .prettierrc.json files",
    ],
)
def test_questions_about_a_kind_of_file_stand_down(agent, workspace, phrasing):
    assert _direct_workspace_read_request(phrasing) is None, f"{phrasing!r} bound to workspace.read_file"
    assert "does not exist" not in _reply(_read_lane(agent, phrasing, workspace)).lower()


@pytest.mark.parametrize(
    "phrasing",
    [
        "is it a good idea to create a Makefile for this project?",
        "is it worth adding a linter to this project",
        "would it be better to split this repo",
        "should i use docker for this project",
        "does it make sense to add tests to this codebase",
        "any advice on how to structure this project",
        "pros and cons of a monorepo for this project",
    ],
)
def test_advice_questions_about_the_project_are_not_folder_listings(agent, workspace, phrasing):
    reply = _reply(_overview_lane(agent, phrasing, workspace))
    assert "notes.txt" not in reply, f"{phrasing!r} was answered with the folder's contents: {reply!r}"


# --------------------------------------------------------------------------------------------
# What must NOT change: the inverted gate, and the overview reader.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrasing",
    [
        "what is in notes.txt",
        "print the contents of notes.txt",
        "cat notes.txt",
        "read notes.txt",
        "open notes.txt",
        "show me notes.txt",
        "inspect notes.txt",
        "what does notes.txt say?",
        "display notes.txt",
        "output notes.txt",
        "view notes.txt",
        "look at notes.txt",
        "check notes.txt",
        "pls just give me notes.txt",
    ],
)
def test_a_named_workspace_file_still_reads_the_real_bytes(agent, workspace, phrasing):
    """Not just "binds" -- the file's OWN contents come back, from the file on disk."""

    assert _direct_workspace_read_request(phrasing)["path"] == "notes.txt"
    assert "hello from notes" in _reply(_read_lane(agent, phrasing, workspace)), phrasing


def test_reads_that_name_two_files_are_not_mistaken_for_a_comparison(agent, workspace):
    """An explicit read verb suppresses the stand-down; "and" between two files is not a veto."""

    assert _direct_workspace_read_request("read notes.txt and README.md")["path"] == "notes.txt"
    assert "hello from notes" in _reply(_read_lane(agent, "read notes.txt and README.md", workspace))


def test_an_explanation_frame_aimed_at_a_real_file_still_reads_it(agent, workspace):
    """"explain how X works IN worker.py" names no kind of file -- it is a read of worker.py."""

    for phrasing in (
        "explain README.md to me",
        "explain how the retry logic works in src/calc.py",
        "what does src/calc.py contain?",
    ):
        assert _direct_workspace_read_request(phrasing) is not None, phrasing
    assert "Zebra Ledger" in _reply(_read_lane(agent, "explain README.md to me", workspace))
    assert "def add" in _reply(_read_lane(agent, "what does src/calc.py contain?", workspace))


@pytest.mark.parametrize(
    "phrasing",
    [
        "delete notes.txt",
        "create notes.txt with hello",
        "write notes.txt",
        "rename notes.txt to old.txt",
        "append a line to notes.txt",
        "update config.json",
        "move notes.txt into archive",
        "overwrite notes.txt",
    ],
)
def test_mutation_intent_is_still_not_a_read(phrasing):
    assert _direct_workspace_read_request(phrasing) is None, f"{phrasing!r} treated as a read"


@pytest.mark.parametrize(
    "phrasing",
    [
        "what is this project about",
        "explain this codebase",
        "what's in the folder",
        "check this folder and tell me what it is",
        # These reach the loose demonstrative arm, the one the advice veto guards. They must
        # survive it: a narrower veto than `is_deliberation` exists precisely for them.
        "analyse the local folder we are in",
        "explain the local folder we are in",
        "hey lets audit thsi folder?",
    ],
)
def test_folder_overview_still_reads_the_real_folder(agent, workspace, phrasing):
    reply = _reply(_overview_lane(agent, phrasing, workspace))
    assert "notes.txt" in reply and "README.md" in reply, f"{phrasing!r} -> {reply!r}"


# --------------------------------------------------------------------------------------------
# The two detectors, on the boundary each one draws.
# --------------------------------------------------------------------------------------------


def test_read_verbs_suppress_the_kind_of_file_stand_down():
    assert _asks_about_a_kind_of_file("what is the difference between a.txt and b.txt") is True
    assert _asks_about_a_kind_of_file("print the difference between a.txt and b.txt") is False
    assert _asks_about_a_kind_of_file("explain how a setup.py works") is True
    assert _asks_about_a_kind_of_file("explain how setup.py in src works") is False


def test_advice_question_is_the_narrow_subset_of_deliberation():
    """The veto used by the folder-overview arm must not grow into the full deliberation list."""

    assert is_advice_question(ASKS_WHETHER_TO_ADD_A_FILE) is True
    for descriptive in (
        "explain the local folder we are in",
        "what should i look at in this repo",
        "tell me about this project",
        "what is this project about",
    ):
        assert is_advice_question(descriptive) is False, descriptive
        assert is_deliberation(descriptive) is True, f"{descriptive!r} must stay deliberation"


def test_a_product_named_like_a_file_is_not_a_read_when_no_such_file_exists(tmp_path) -> None:
    """SERVED (c5463c47, rig s54): "What is the latest stable version of Node.js?" was answered
    "There is no file at `Node.js`" by the workspace fast path. A proper-noun stem on a code
    extension, no read verb, no such file under the root: not a read. With the file present, or
    with a read verb, the read stays."""
    from core.agent_runtime.fast_paths_utility import _names_only_absent_proper_noun_products

    reads = [{"path": "Node.js"}]
    assert _names_only_absent_proper_noun_products(
        "What is the latest stable version of Node.js?", reads, str(tmp_path)
    )
    (tmp_path / "Node.js").write_text("module.exports = 1;\n")
    assert not _names_only_absent_proper_noun_products(
        "What is the latest stable version of Node.js?", reads, str(tmp_path)
    )
    (tmp_path / "Node.js").unlink()
    assert not _names_only_absent_proper_noun_products("read Node.js", reads, str(tmp_path))
    assert not _names_only_absent_proper_noun_products(
        "what is in notes.txt", [{"path": "notes.txt"}], str(tmp_path)
    ), "a lowercase missing file keeps the existing true 'does not exist' answer"
