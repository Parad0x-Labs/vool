"""A truncated file listing must say so in its body, loudly enough to block absence claims.

Measured 2026-07-31 on a 682-file repository during a live audit: `workspace.list_files` capped at
200 paths, the walk is alphabetical, so the cut landed mid-alphabet and hid all 275 files under
`tests/`. The auditing model then wrote "**No tests discovered** | Workspace-wide" as a P1 finding --
a false claim manufactured by the tool's own ceiling. The `truncated` boolean lived in `details`,
which the model never read; the body said only that the scan hit "the configured budget".

Three properties hold it down now:
  * the ceiling is 5000, chosen so essentially every real project lists in full (a path is ~43
    bytes; the walk has already run in full before any cap applies, so a low cap saves no I/O);
  * the walk runs past the reply's cap, so the reply knows the TRUE total and how many it dropped;
  * a truncated body states, in the body, that it is an alphabetical prefix that cannot support any
    conclusion of absence.
"""
from __future__ import annotations

from pathlib import Path

from core.runtime_execution_tools import execute_runtime_tool


def _tree(root: Path, *, before: int, tests: int) -> None:
    for i in range(before):
        (root / f"aaa_{i:04d}.py").write_text("x")
    (root / "tests").mkdir()
    for i in range(tests):
        (root / "tests" / f"test_{i}.py").write_text("def test(): pass")


def test_a_partial_file_read_says_so_and_names_the_total(tmp_path) -> None:
    """The same defect on the CONTENT axis, which shipped alongside the listing bug.

    `read_text().splitlines()` pulls the whole file in before the slice, so the old 160-line
    default saved no I/O -- it only hid the size. A 160-line file and a 4,620-line file produced
    byte-identical envelopes (`status: executed`, no `total_lines`, no `truncated`), so a model
    that had seen 3.5% of a module could not tell, and would describe the file by what it was
    never shown to contain.
    """

    target = tmp_path / "big.py"
    target.write_text("\n".join(f"line_{i} = {i}" for i in range(1000)), encoding="utf-8")

    partial = execute_runtime_tool(
        "workspace.read_file",
        {"path": "big.py", "max_lines": 160},
        source_context={"workspace": str(tmp_path)},
    )
    assert partial.status == "truncated"
    assert partial.details["line_count"] == 160
    assert partial.details["total_lines"] == 1000
    assert partial.details["truncated"] is True
    # In the body, with a way to continue -- not a boolean nobody reads.
    assert "1000 lines in total" in partial.response_text
    assert "cannot support any conclusion" in partial.response_text
    assert "Re-read from line 161" in partial.response_text

    whole = execute_runtime_tool(
        "workspace.read_file",
        {"path": "big.py"},
        source_context={"workspace": str(tmp_path)},
    )
    # The default must read an ordinary file WHOLE; 43% of this repo exceeded the old 160.
    assert whole.status == "executed"
    assert whole.details["truncated"] is False
    assert whole.details["line_count"] == 1000
    assert "cannot support any conclusion" not in whole.response_text


def test_a_verbatim_read_still_discloses_truncation(tmp_path) -> None:
    """Verbatim is what the audit uses, so a silent partial read there is the worst case."""

    target = tmp_path / "big.py"
    target.write_text("\n".join(f"line_{i}" for i in range(500)), encoding="utf-8")
    result = execute_runtime_tool(
        "workspace.read_file",
        {"path": "big.py", "max_lines": 50, "verbatim": True},
        source_context={"workspace": str(tmp_path)},
    )
    assert result.status == "truncated"
    assert result.details["total_lines"] == 500
    assert "450 not shown" in result.response_text or "51-500 not shown" in result.response_text


def test_a_truncated_listing_names_the_drop_and_forbids_absence_claims(tmp_path) -> None:
    _tree(tmp_path, before=300, tests=5)
    result = execute_runtime_tool(
        "workspace.list_files", {"limit": 200}, source_context={"workspace": str(tmp_path)}
    )
    assert result.status == "truncated"
    # The reply knows the true total even though it listed a prefix.
    assert result.details["total"] == 305
    assert result.details["dropped"] == 105
    # The warning is in the BODY the model reads, not buried in details.
    assert "cannot support any conclusion" in result.response_text
    assert "105 more file(s)" in result.response_text


def test_a_real_project_sized_tree_lists_in_full_by_default(tmp_path) -> None:
    """682 files was enough to manufacture a false P1. The default reply must carry a tree that
    size whole -- the ceiling exists for pathological trees, not ordinary repositories."""
    _tree(tmp_path, before=675, tests=7)
    result = execute_runtime_tool(
        "workspace.list_files", {"limit": 5000}, source_context={"workspace": str(tmp_path)}
    )
    assert result.status == "executed"
    assert result.details["count"] == 682
    assert "tests/test_0.py" in result.response_text
    assert "cannot support any conclusion" not in result.response_text


def test_the_ceiling_still_binds_on_a_pathological_request(tmp_path) -> None:
    """limit is caller-supplied; a runaway value must clamp to the ceiling, not honour itself."""
    (tmp_path / "one.py").write_text("x")
    result = execute_runtime_tool(
        "workspace.list_files", {"limit": 10_000_000}, source_context={"workspace": str(tmp_path)}
    )
    assert result.ok
    assert result.details["count"] == 1
