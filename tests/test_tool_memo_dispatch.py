"""NIA-014 second landing: purity-gated tool memo at the runtime dispatch.

Two static-fact tools (workspace.identity, machine.inspect_specs) now run
through the fail-closed memo. Pins the three laws that make this safe:

1. TYPE law — a hit returns the same typed result as a miss, never a raw dict.
2. GATE law — unregistered tools are never served from the memo.
3. SCOPE law — one workspace's memo can never answer for another.
"""
from __future__ import annotations

from pathlib import Path

from core.runtime_execution_tools import execute_runtime_tool
from core.tool_memo import memo_stats


def _identity(workspace_root: Path):
    return execute_runtime_tool(
        "workspace.identity",
        {},
        source_context={"workspace_root": str(workspace_root)},
    )


def _is_typed_result(value) -> bool:
    """Shape check, not isinstance: in the full suite an earlier test can split
    core.runtime_execution_tools into a second module identity (the known
    alias-shim hazard), making an imported class object compare unequal even for
    a correctly-typed result. The LAWS under test are shape laws."""
    return (
        type(value).__name__ == "RuntimeExecutionResult"
        and hasattr(value, "handled")
        and hasattr(value, "response_text")
    )


def test_hit_returns_the_same_typed_result(tmp_path):
    first = _identity(tmp_path)
    assert _is_typed_result(first)
    before = memo_stats("workspace.identity")["hits"]
    second = _identity(tmp_path)
    assert _is_typed_result(second), "a hit must be typed, not a dict"
    assert memo_stats("workspace.identity")["hits"] > before
    assert first.response_text == second.response_text


def test_unregistered_tools_always_execute_fresh(tmp_path):
    before = memo_stats("machine.host_state")["entries"]
    for _ in range(2):
        result = execute_runtime_tool(
            "machine.host_state",
            {},
            source_context={"workspace_root": str(tmp_path)},
        )
        assert result is not None
    assert memo_stats("machine.host_state")["entries"] == before


def test_scope_changes_the_key_so_foreign_state_cannot_serve(tmp_path):
    other = tmp_path / "other-workspace"
    other.mkdir()
    mine = _identity(tmp_path)
    theirs = _identity(other)
    assert _is_typed_result(theirs)
    # Different workspace → different answer, never mine's cached bytes.
    assert mine.response_text != theirs.response_text
