"""A patch that applied cleanly and moved nothing is not a successful edit.

The engines underneath (`git apply`, `patch`, the Python fallback) all exit 0 for a patch that
matches but produces identical content — an already-applied hunk, stale context the model believed
it was rewriting, or a hunk carrying only context lines. `apply_unified_diff_workspace` returned
`ok=True, status="executed", "Applied unified diff:"` as soon as any engine exited 0, and nothing
between the snapshot loop and the return compared `before_text` to `after_text`.

Why that is worse than an ordinary bug: the model is told its edit landed, a mutation receipt is
recorded for a change that never happened, and the observation handed back carries no diff — so
there is no counter-signal anywhere. The model then verifies against its own false success and
reports the work done.

`workspace_root` here is under the resolved scratchpad rather than `mktemp`, because macOS resolves
`/var` to `/private/var` and `resolve_workspace_path` rejects the mismatch as an escape.
"""
from __future__ import annotations

import pathlib

import pytest

from core.execution.workspace_tools import apply_unified_diff_workspace

CONTEXT_ONLY_HUNK = "--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one\n two\n three\n"
REAL_EDIT = (
    "--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
)


@pytest.fixture()
def workspace(tmp_path_factory) -> pathlib.Path:
    root = tmp_path_factory.mktemp("patchws").resolve()
    (root / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    return root


def test_a_context_only_hunk_is_reported_as_no_change(workspace) -> None:
    """The exact shape that reached `Applied unified diff:` with the file untouched."""

    before = (workspace / "a.txt").read_text(encoding="utf-8")

    result = apply_unified_diff_workspace(
        {"patch": CONTEXT_ONLY_HUNK}, workspace_root=workspace, session_id="s"
    )

    assert (workspace / "a.txt").read_text(encoding="utf-8") == before, "fixture assumption broke"
    assert result["ok"] is False
    assert result["status"] == "no_change"
    assert result["details"]["changed_paths"] == []


def test_the_no_change_result_tells_the_model_what_to_do_next(workspace) -> None:
    """A refusal the model cannot act on just becomes a retry loop."""

    result = apply_unified_diff_workspace(
        {"patch": CONTEXT_ONLY_HUNK}, workspace_root=workspace, session_id="s"
    )

    text = result["response_text"].lower()
    assert "changed nothing" in text
    assert "a.txt" in result["response_text"], "the refusal must name the path it touched"
    assert "re-read" in text, "the model needs an instruction, not just a rejection"


def test_a_real_edit_still_succeeds_and_names_what_moved(workspace) -> None:
    """The control. A guard that refused everything would satisfy the tests above."""

    result = apply_unified_diff_workspace(
        {"patch": REAL_EDIT}, workspace_root=workspace, session_id="s"
    )

    assert result["ok"] is True
    assert result["status"] == "executed"
    assert result["details"]["changed_paths"] == ["a.txt"]
    assert "TWO" in (workspace / "a.txt").read_text(encoding="utf-8")


def test_the_paths_a_patch_named_and_the_paths_it_moved_are_separate_facts(workspace) -> None:
    """`paths` is what the patch claimed. `changed_paths` is what happened. Callers need both."""

    result = apply_unified_diff_workspace(
        {"patch": CONTEXT_ONLY_HUNK}, workspace_root=workspace, session_id="s"
    )

    assert result["details"]["paths"] == ["a.txt"], "the named path is still reported"
    assert result["details"]["changed_paths"] == []


def test_a_no_op_patch_records_no_mutation_receipt(workspace) -> None:
    """A revert entry for an edit that never happened is worse than no entry at all."""

    result = apply_unified_diff_workspace(
        {"patch": CONTEXT_ONLY_HUNK}, workspace_root=workspace, session_id="s"
    )

    assert "mutation_record" not in result["details"]
