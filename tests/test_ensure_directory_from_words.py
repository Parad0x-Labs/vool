"""A folder name asked for in idiom words, not junk prose (measured on the audit wording
2026-10-27: "make sure there's a docs folder in garden planner and put a short note in it
about watering" planned workspace.ensure_directory with path `sure` -- `_CREATE_PATH_RE`
read "make sure" as the create verb plus a path named `sure`, and the approval card targeted
a folder called `sure`).

Two owning contracts, fixed where they live:

1. An idiom filler standing after a create verb is never a folder name (the filler set in
   `core.execution.constants._PATH_STOP_WORDS`), so "make sure ...", "make certain ...",
   "make it work", "create it" stop supplying `sure`/`certain`/`it` as the directory.
2. A folder name can stand attached to the folder noun with no create verb in front of it
   (`_ARTICLE_NOUN_PATH_RE`), so "there's a docs folder" extracts `docs`.

Real folder words people ask for -- note, notes, docs -- stay extractable, creation
questions still plan nothing, and the planned directory targets the folder the person
named, never the filler.
"""
from __future__ import annotations

from core.execution.planner import (
    _extract_workspace_bootstrap_path,
    _looks_like_workspace_bootstrap_request,
    plan_tool_workflow,
)

_AUDIT_WORDING = (
    "make sure there's a docs folder in garden planner and put a short note in it about watering"
)


def test_audit_wording_extracts_the_named_folder_not_the_filler() -> None:
    assert _extract_workspace_bootstrap_path(_AUDIT_WORDING) == "docs"
    assert _looks_like_workspace_bootstrap_request(_AUDIT_WORDING) is True


def test_short_audit_wording_extracts_the_named_folder() -> None:
    assert _extract_workspace_bootstrap_path("make sure there's a docs folder in garden planner") == "docs"


def test_fresh_idiom_wordings_extract_the_named_folder() -> None:
    cases = {
        "make certain there's an assets directory": "assets",
        "make sure there is a notes folder in the garden planner project": "notes",
        "can you make sure there's a src folder": "src",
        "make sure we have a docs dir": "docs",
        "make sure it has a build directory": "build",
    }
    for wording, expected in cases.items():
        assert _extract_workspace_bootstrap_path(wording) == expected, wording
        assert _looks_like_workspace_bootstrap_request(wording) is True, wording


def test_filler_after_create_verb_without_a_folder_name_extracts_nothing() -> None:
    for wording in (
        "make sure it works",
        "make it work",
        "create it",
        "make certain that happens",
    ):
        assert _extract_workspace_bootstrap_path(wording) == "", wording


def test_unnamed_folder_ask_extracts_nothing() -> None:
    # A folder noun with no name attached must not hand back prose ("a", "folder", "in").
    assert _extract_workspace_bootstrap_path("make sure there's a folder in garden planner") == ""


def test_explicit_create_wordings_keep_their_paths() -> None:
    cases = {
        "create a folder named garden-notes": "garden-notes",
        "create a folder called assets": "assets",
        "make a notes folder": "notes",
        "create directory src/lib": "src/lib",
        "create folder docs in documents": "docs",
    }
    for wording, expected in cases.items():
        assert _extract_workspace_bootstrap_path(wording) == expected, wording


def test_notes_is_not_a_filler_word() -> None:
    # "note"/"notes" are real folder names people ask for; they must stay extractable.
    assert _extract_workspace_bootstrap_path("make sure there's a notes folder in garden planner") == "notes"


def test_note_request_without_create_marker_extracts_nothing() -> None:
    wording = "put a short note in it about watering"
    assert _extract_workspace_bootstrap_path(wording) == ""
    assert _looks_like_workspace_bootstrap_request(wording) is False


def test_creation_questions_are_not_creates() -> None:
    for wording in (
        "did you create folder or not?",
        "wait so did you created folder or not?",
        "have you created folder finalbot yet?",
    ):
        assert _looks_like_workspace_bootstrap_request(wording) is False, wording


def test_plain_folder_question_is_not_a_create_request() -> None:
    # Asking whether a folder exists plans no directory creation.
    for wording in ("is there a docs folder?", "is there a docs folder in garden planner"):
        assert _looks_like_workspace_bootstrap_request(wording) is False, wording


def test_planner_targets_the_named_folder_never_the_filler(tmp_path) -> None:
    decision = plan_tool_workflow(
        user_text="make sure there's a docs folder in garden planner",
        task_class="unknown",
        executed_steps=[],
        source_context={"workspace": str(tmp_path), "workspace_root": str(tmp_path)},
    )
    assert decision.handled is True
    assert decision.reason == "planned_workspace_directory_bootstrap"
    payload = decision.next_payload or {}
    assert payload["intent"] == "workspace.ensure_directory"
    assert payload["arguments"]["path"] == "docs"
    assert payload["arguments"]["path"] != "sure"


def test_planner_never_plans_a_create_from_a_question(tmp_path) -> None:
    for wording in (
        "did you create folder or not?",
        "is there a docs folder?",
        "make sure it works",
    ):
        decision = plan_tool_workflow(
            user_text=wording,
            task_class="unknown",
            executed_steps=[],
            source_context={"workspace": str(tmp_path), "workspace_root": str(tmp_path)},
        )
        payload = decision.next_payload or {}
        intent = str(payload.get("intent") or "")
        assert not intent.endswith("ensure_directory"), (wording, intent)
