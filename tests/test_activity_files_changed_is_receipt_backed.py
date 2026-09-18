"""Guard: Activity's "Files changed" may only be claimed by a canonical mutation receipt.

LIVE DEFECT this pins. The Activity panel reported:

    FILES CHANGED (24)

while the children underneath it read, in full:

    event: tool_selected
    tool: workspace.write_file
    result: Running workspace.write_file.

The category was built by matching the tool NAME against a needle list (`write`, `edit`, `patch`,
...) and counting the matching tool calls. A selected tool is an intention. A running tool is an
intention in flight. A denied or failed write changed nothing at all. None of them are a file on
disk being different than it was, which is the only thing "Files changed" can honestly mean.

The runtime already emits the real evidence: exactly one canonical receipt per mutation attempt
(``core/runtime_execution_tools.py::_emit_mutation_activity_event``) carrying the affected path in
``canonical_target`` alongside the outcome. "Files changed" is now built from those receipts and
counts UNIQUE PATHS.

These tests EXECUTE the browser projection against forged event streams rather than grepping its
source, because the defect was never a missing word -- it was a real function returning a number
that no evidence supported.
"""

from __future__ import annotations

import json

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node, script

HTML = render_vool_chat_html()

# One allowed, completed write of a file that did not exist. This is the ONLY shape in this module
# that is entitled to produce a changed file.
_GENUINE_RECEIPT = {
    "seq": 900,
    "event_type": "workspace_mutation_completed",
    "canonical_target": "src/real.py",
    "tool_intent": "workspace.write_file",
    "permission_decision": "allowed",
    "result_state": "executed",
    "ok": True,
    "action": "created",
    "before_hash": "",
    "after_hash": "b" * 64,
    "diff_summary": "+real content",
    "error_class": "",
}


def _drive(events: list[dict], *, extra: str = "") -> dict:
    """Build the real Activity tree from `events` inside the real page script."""
    return run_node(
        DOM
        + script()
        + """
const EVENTS = """
        + json.dumps(events)
        + """;
const tree = buildActivityTree(EVENTS);
const category = (key) => {
  const cat = tree.categories.find((c) => c.key === key);
  return cat ? { label: cat.label, count: cat.items.length, titles: cat.items.map((i) => (i.display ? i.display.title : i.tool)) } : null;
};
const payload = {
  changes: category('changes'),
  edits: category('edits'),
  runtime: category('runtime'),
  rollup: activityRollupText(tree),
  worklog: activityWorkLogSummary(tree),
  totalActions: tree.totalActions,
  categoryKeys: tree.categories.map((c) => c.key),
  labels: tree.categories.map((c) => c.label),
  treeText: activityTreeText(tree),
};
"""
        + extra
        + """
out(payload);
"""
    )


def _changed_paths(result: dict) -> list[str]:
    return sorted((result["changes"] or {}).get("titles", []))


# --------------------------------------------------------------------------------------------
# The core invariant, in the exact shapes that produced the live "(24)".
# --------------------------------------------------------------------------------------------


def test_twenty_four_selected_write_tools_change_no_files() -> None:
    """The reproduction. 24 `tool_selected` rows for a mutation tool, and nothing else."""
    events = [
        {
            "seq": i,
            "event_type": "tool_selected",
            "tool_name": "workspace.write_file",
            "message": "Running workspace.write_file.",
            "tool_args": f"path=src/f{i}.py",
        }
        for i in range(1, 25)
    ]
    result = _drive(events)
    assert result["errors"] == []

    assert result["changes"] is None, (
        "24 selected-but-unexecuted write tools produced a Files changed category: "
        f"{result['changes']}"
    )
    assert "Files changed" not in result["labels"]
    # The calls are not hidden -- they are reported as what they are.
    assert result["edits"] == {
        "label": "Edit tool calls",
        "count": 24,
        "titles": ["Editing"] * 24,
    }
    assert result["rollup"] == "Made 24 edit tool calls"
    assert "changed" not in result["rollup"].lower(), (
        f"the rollup still claims a change: {result['rollup']}"
    )


def test_a_started_tool_is_not_a_changed_file() -> None:
    """`tool_selected` opens a call and `tool_executed` closes it. Neither, on its own, is a change."""
    result = _drive(
        [
            {"seq": 1, "event_type": "tool_selected", "tool_name": "workspace.write_file", "message": "Running workspace.write_file."},
            {"seq": 2, "event_type": "tool_started", "tool_name": "workspace.write_file", "message": "started"},
            {"seq": 3, "event_type": "tool_executed", "tool_name": "workspace.write_file", "message": "wrote src/a.py", "status": "ok"},
        ]
    )
    assert result["errors"] == []
    # A tool that RAN to completion still is not the evidence. `tool_executed` reports that the
    # dispatcher returned; the mutation receipt reports what happened to the file.
    assert result["changes"] is None, (
        "a completed tool call was counted as a changed file with no mutation receipt behind it: "
        f"{result['changes']}"
    )


def test_a_permission_request_changes_no_files() -> None:
    result = _drive(
        [
            {"seq": 1, "event_type": "task_pending_approval", "message": "workspace.write_file wants to write src/a.py"},
            {"seq": 2, "event_type": "tool_selected", "tool_name": "workspace.write_file", "message": "Running workspace.write_file."},
        ]
    )
    assert result["errors"] == []
    assert result["changes"] is None


def test_a_failed_or_denied_mutation_receipt_changes_no_files() -> None:
    """The receipt exists and names a real path -- but it says the operation did not happen."""
    result = _drive(
        [
            {
                "seq": 1, "event_type": "workspace_mutation_failed", "canonical_target": "src/denied.py",
                "tool_intent": "workspace.write_file", "permission_decision": "denied",
                "result_state": "permission_denied", "ok": False, "error_class": "permission_denied",
            },
            {
                "seq": 2, "event_type": "workspace_mutation_failed", "canonical_target": "src/boom.py",
                "tool_intent": "workspace.replace_in_file", "permission_decision": "allowed",
                "result_state": "ambiguous_match", "ok": False, "error_class": "ambiguous_match",
            },
            {
                "seq": 3, "event_type": "workspace_mutation_rollback_conflict", "canonical_target": "src/stale.py",
                "tool_intent": "workspace.rollback_last_change", "ok": False,
                "error_class": "stale_revert_conflict", "conflict_reason": "stale_revert_conflict",
            },
        ]
    )
    assert result["errors"] == []
    assert result["changes"] is None, f"a failed mutation was counted as a change: {result['changes']}"
    # It must remain VISIBLE -- suppressing a failed write would be its own lie.
    assert result["runtime"] is not None and result["runtime"]["count"] == 3
    assert sorted(result["runtime"]["titles"]) == [
        "File change failed", "File change failed", "Rollback conflict",
    ]


def test_each_veto_alone_is_enough_to_reject_a_receipt() -> None:
    """Every clause of the success test is load-bearing on its own.

    Each event below is a completion-typed receipt with a real path that differs from the genuine
    one in exactly ONE field. If any single veto were dropped, that row would manufacture a file.
    """
    variants = {
        "not_ok": {"ok": False},
        "denied": {"permission_decision": "denied"},
        "carries_error_class": {"error_class": "write_failed"},
        "no_path": {"canonical_target": ""},
        "whitespace_path": {"canonical_target": "   "},
    }
    events = []
    for index, (name, override) in enumerate(sorted(variants.items()), start=1):
        event = dict(_GENUINE_RECEIPT)
        event.update({"seq": index, "canonical_target": f"src/{name}.py"})
        event.update(override)
        events.append(event)

    result = _drive(events)
    assert result["errors"] == []
    assert result["changes"] is None, (
        "a receipt failing one veto still manufactured a changed file: " f"{result['changes']}"
    )


def test_one_genuine_receipt_among_the_noise_is_still_reported() -> None:
    """The control. The guard must reject forgeries WITHOUT suppressing the real thing -- otherwise
    every test above would also pass on a projection that simply never reports a change."""
    events = [
        {"seq": 1, "event_type": "tool_selected", "tool_name": "workspace.write_file", "message": "Running workspace.write_file."},
        {"seq": 2, "event_type": "workspace_mutation_failed", "canonical_target": "src/nope.py", "ok": False, "error_class": "denied"},
        dict(_GENUINE_RECEIPT),
    ]
    result = _drive(events)
    assert result["errors"] == []
    assert _changed_paths(result) == ["src/real.py"]
    assert result["changes"]["label"] == "Files changed"
    assert "changed 1 file" in result["rollup"].lower()


# --------------------------------------------------------------------------------------------
# Counting: unique paths, never operations.
# --------------------------------------------------------------------------------------------


def test_repeated_writes_to_one_path_are_one_changed_file() -> None:
    events = [
        dict(_GENUINE_RECEIPT, seq=seq, action="updated", after_hash=str(seq) * 64)
        for seq in range(1, 8)
    ]
    result = _drive(events)
    assert result["errors"] == []
    assert result["changes"]["count"] == 1, (
        "7 writes to one path inflated the file count to " f"{result['changes']['count']}"
    )
    assert _changed_paths(result) == ["src/real.py"]
    assert result["rollup"] == "Changed 1 file"
    # The repetition is not swallowed -- it is stated, in the word that describes it.
    assert "7 operations" in result["treeText"], (
        "repeated writes collapsed to one row without saying how many operations produced it"
    )


def test_a_multi_path_receipt_reports_each_path_it_actually_names() -> None:
    """A rollback or cross-file diff legitimately reports several paths in one receipt -- but only
    the ones it names."""
    result = _drive(
        [
            dict(
                _GENUINE_RECEIPT, seq=1, canonical_target="src/a.py, src/b.py, src/a.py",
                tool_intent="workspace.apply_unified_diff", action="updated",
            )
        ]
    )
    assert result["errors"] == []
    # Three entries, two distinct paths.
    assert _changed_paths(result) == ["src/a.py", "src/b.py"]
    assert result["rollup"] == "Changed 2 files"


def test_files_changed_is_ordered_first_and_counted_in_the_work_log() -> None:
    result = _drive(
        [
            {"seq": 1, "event_type": "tool_selected", "tool_name": "workspace.read_file"},
            dict(_GENUINE_RECEIPT, seq=2),
        ]
    )
    assert result["errors"] == []
    assert result["categoryKeys"][0] == "changes", (
        f"proven changes are not the first thing shown: {result['categoryKeys']}"
    )
    assert result["totalActions"] == 2
    assert result["worklog"] == "Work log · 2 actions"


def test_a_change_row_discloses_the_receipt_it_rests_on() -> None:
    result = _drive([dict(_GENUINE_RECEIPT, seq=1)])
    assert result["errors"] == []
    text = result["treeText"]
    for evidence in ("workspace.write_file", "created", "allowed", "b" * 64, "+real content", "workspace_mutation_completed"):
        assert evidence in text, f"the change row hides its own evidence: {evidence!r} missing"


# --------------------------------------------------------------------------------------------
# Source-level guard: the needle list may never label itself "Files changed" again.
# --------------------------------------------------------------------------------------------


def test_the_tool_name_needle_list_no_longer_claims_files_changed() -> None:
    assert "'edits', 'Files changed'" not in HTML, (
        "the tool-NAME category is labelled 'Files changed' again -- that is the original defect"
    )
    assert "'edits', 'Edit tool calls'" in HTML
    assert "const ACTIVITY_MUTATION_SUCCESS_TYPES" in HTML
    assert "workspace_mutation_completed: 1" in HTML
    assert "workspace_mutation_rollback_completed: 1" in HTML
