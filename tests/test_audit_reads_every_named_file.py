"""An audit asked about several named files reads every one of them, not only the last.

From the live agent-team comparison, 2026-10-07. Asked, verbatim, "Review the three Python files in this
folder: pricing.py, stock.py and orders.py. For each file, find the single most important bug …", VOOL's audit
reported "Opened 1 of 3 source files in this pass". `audit_target_in` keeps only the LAST filename a request
names, and the three files do not import or mention each other, so the named-file scope was `orders.py` alone.
Its answer then withdrew the other two files ("I did not actually open `pricing.py`") because nothing had
read them. Every file the operator names is part of the scope, in the order named.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.agent_runtime.workspace_audit import audit_target_in, audit_targets_in, run_workspace_audit

FIXTURE = Path(__file__).with_name("fixtures") / "multi_file_review"
REQUEST = (
    "Review the three Python files in this folder: pricing.py, stock.py and orders.py. For each file, find the "
    "single most important bug and report it as `file:line — what is wrong — the fix` (one line per file). Read "
    "the code; do not change any file. Split the work across sub-agents if you can (one file each)."
)


@pytest.fixture()
def shop(tmp_path: Path) -> Path:
    for path in FIXTURE.glob("*.py"):
        shutil.copy(path, tmp_path / path.name)
    return tmp_path


def test_every_named_file_is_a_target_in_the_order_named():
    assert audit_targets_in(REQUEST) == ("pricing.py", "stock.py", "orders.py")
    assert audit_target_in(REQUEST) == "orders.py"  # the single-target reading is unchanged
    assert audit_targets_in("audit app-landing/index.html please") == ("app-landing/index.html",)
    assert audit_targets_in("audit my project") == ()


def test_an_audit_naming_three_unrelated_files_reads_all_three(shop: Path):
    from core.runtime_execution_tools import execute_runtime_tool

    reads: list[str] = []

    def spy(intent, arguments=None, source_context=None, **kwargs):
        if intent == "workspace.read_file" and (arguments or {}).get("start_line") == 1 and (arguments or {}).get("verbatim"):
            reads.append(str((arguments or {}).get("path") or ""))
        return execute_runtime_tool(intent, arguments or {}, source_context=source_context)

    ctx = {"workspace": str(shop)}
    report, steps = run_workspace_audit(
        str(shop), source_context=ctx, execute_tool=spy, emit=lambda *a, **k: None,
        target_path=audit_target_in(REQUEST), extra_target_paths=audit_targets_in(REQUEST),
    )
    sourced = [s["summary"].split(":", 1)[0] for s in steps if str(s.get("label", "")).startswith("read source ")]
    for name in ("pricing.py", "stock.py", "orders.py"):
        assert name in sourced, (name, sourced)
