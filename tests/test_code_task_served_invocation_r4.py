"""Served journey for the revision-4 invocation contract: real daemon, real ``/api/chat``, one
session, one durable task, a disposable Git fixture, production task/sandbox/approval/journal/
report paths. THE MODEL IS SCRIPTED (one planned tool call per turn) AND SAYS SO: no live
autonomy is claimed and none could be.

The journey, on the ``ledger.py`` fixture (a helper-module assertion, a test class, markers):

* reproduce the retained node's real failure; land a deliberately WRONG approved repair;
* misleading greens are refused, each by the journal's typed state: a sibling node, pytest under
  ``-O --assert=plain`` (no assertion executes), the same-spelled file under another cwd, and a
  marker filter selecting a different test;
* a MEANINGFULLY CHANGED command runs: ``-k test_settles -k test_empty`` (the last ``-k`` decides)
  is green and uncovered; its identical repeat is corrected with the retained check as the lawful
  next action; the swapped ``-k test_empty -k test_settles`` executes and fails;
* the RETAINED check's red reopens diagnosis in the same task; the real repair is proposed and
  freshly approved;
* the retained node verifies narrow, the broader ``tests`` directory run provably covers it, and
  the report completes -- with the wrong-repair evidence kept as history.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_code_assistant_served_journey import _call
from tests.test_code_task_invocation_contract_r4 import (
    LEDGER_BUGGY,
    LEDGER_CHECKS,
    LEDGER_FIXED,
    LEDGER_TESTS,
    LEDGER_WRONG,
    PASSING,
)
from tests.test_code_task_served_purpose import TurnDrive, _boot_rig, _reply_text, _seed_pytest_dir

pytestmark = [pytest.mark.served]

FILES = {
    "ledger.py": LEDGER_BUGGY,
    "tests/__init__.py": "",
    "tests/ledger_checks.py": LEDGER_CHECKS,
    "tests/test_ledger.py": LEDGER_TESTS,
    "decoy/tests/test_ledger.py": PASSING,
}
RETAINED = "python3 -m pytest -q tests/test_ledger.py::TestBalance::test_settles"
SUITE = "python3 -m pytest -q tests/test_ledger.py"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_served_misleading_greens_refused_changed_commands_run_real_fix_completes(tmp_path: Path) -> None:
    rig = _boot_rig(tmp_path, "invocation-r4", FILES)
    try:
        drive = TurnDrive(rig, "served-invocation-r4")
        drive.turn("Fix the failing ledger test in this project and explain the change.",
                   _call("code__task__open", {"objective": "repair ledger.balance so its test passes"}, "open"))
        files = sorted((rig["store_dir"] / "code_tasks").glob("ct-*.json"))
        assert len(files) == 1, files
        task_id = files[0].stem

        def journal() -> dict[str, Any]:
            return json.loads((rig["store_dir"] / "code_tasks" / f"{task_id}.json").read_text(encoding="utf-8"))

        # 1. The retained node's real failure (the helper assertion), then a WRONG approved repair.
        task = drive.step(task_id, "repro", "workspace.run_tests", {"command": RETAINED}, "reproduce the failure")
        assert task["stage"] == "identify", task["stage"]
        assert task["reproduced_failure"]["command"].endswith("tests/test_ledger.py::TestBalance::test_settles")
        drive.turn("identify it", _call("code__task__identify",
                                        {"task_id": task_id, "path": "ledger.py", "line": 2,
                                         "reason": "balance adds the debits"}, "id"))
        drive.step(task_id, "read", "workspace.read_file", {"path": "ledger.py"}, "read the owner")
        drive.turn("propose", _call("code__task__propose",
                                    {"task_id": task_id, "proposal_id": "w", "intent": "workspace.write_file",
                                     "arguments": {"path": "ledger.py", "content": LEDGER_WRONG,
                                                   "expected_hash": _sha(LEDGER_BUGGY)},
                                     "rationale": "Owner ledger.py: balance combines its operands wrongly."}, "prop"))
        drive.turn("approve", _call("code__task__approve", {"task_id": task_id, "proposal_id": "w"}, "appr"))
        task = drive.step(task_id, "mut", "workspace.write_file",
                          {"path": "ledger.py", "content": LEDGER_WRONG, "expected_hash": _sha(LEDGER_BUGGY)},
                          "apply the repair")
        assert task["stage"] == "narrow_test"
        assert (rig["workspace"] / "ledger.py").read_text(encoding="utf-8") == LEDGER_WRONG

        # 2. Misleading greens. A sibling node: genuine and green, advances narrow, covers nothing.
        task = drive.step(task_id, "narrow-sibling", "workspace.run_tests",
                          {"command": "python3 -m pytest -q tests/test_ledger.py::TestBalance::test_empty"},
                          "run the focused test now")
        sibling = task["verifications"][-1]
        assert sibling["success"] is True and sibling["covers_obligation"] is False, sibling
        assert task["stage"] == "cumulative"
        # pytest under -O with --assert=plain: every assertion stripped, green, refused.
        task = drive.step(task_id, "full-optimized", "workspace.run_tests",
                          {"command": f"{sys.executable} -O -m pytest -q --assert=plain tests/test_ledger.py"},
                          "run the full suite now")
        optimized = task["verifications"][-1]
        assert optimized["success"] is True and optimized["covers_obligation"] is False, optimized
        assert "-O" in optimized["stale_reason"] and task["stage"] == "cumulative"
        # The same-spelled file under another working directory. The sandboxed interpreter
        # resolves `-m pytest` imports from the process cwd only (harness note in
        # test_code_task_served_purpose._boot_rig), so the rig's importable set is seeded beside
        # the decoy cwd too -- the command itself is unchanged.
        _seed_pytest_dir(rig, "decoy")
        task = drive.step(task_id, "full-decoy", "workspace.run_tests", {"command": SUITE, "cwd": "decoy"},
                          "run the suite from the decoy folder")
        decoy = task["verifications"][-1]
        assert decoy["success"] is True and decoy["covers_obligation"] is False, decoy
        assert task["stage"] == "cumulative"
        # A marker filter that selects a different test.
        task = drive.step(task_id, "full-marker", "workspace.run_tests", {"command": SUITE + " -m smoke"},
                          "run the smoke tests")
        marker = task["verifications"][-1]
        assert marker["success"] is True and marker["covers_obligation"] is False, marker
        assert "filters the suite" in marker["stale_reason"] and task["stage"] == "cumulative"

        # 3. A meaningfully changed command runs; its identical repeat is corrected with the lawful next action.
        last_empty = SUITE + " -k test_settles -k test_empty"
        task = drive.step(task_id, "full-k1", "workspace.run_tests", {"command": last_empty}, "run it filtered")
        assert task["verifications"][-1]["success"] is True and task["stage"] == "cumulative"
        task = drive.step(task_id, "full-k1-again", "workspace.run_tests", {"command": last_empty},
                          "run exactly that again to be sure")
        repeat = task["steps"]["full-k1-again"]
        assert repeat["status"] == "repeated_verification" and repeat["executed"] is False, repeat
        assert "does not cover the retained acceptance obligation" in repeat["reason"], repeat["reason"]
        assert "tests/test_ledger.py::TestBalance::test_settles" in repeat["reason"], repeat["reason"]
        verifications_before = len(task["verifications"])
        task = drive.step(task_id, "full-k2", "workspace.run_tests",
                          {"command": SUITE + " -k test_empty -k test_settles"}, "now swap the filters")
        swapped = task["steps"]["full-k2"]
        assert swapped["status"] != "repeated_verification" and swapped["executed"] is True, swapped
        assert len(task["verifications"]) == verifications_before + 1
        assert task["verifications"][-1]["success"] is False  # the changed selection really ran the failing test
        reply = drive.turn("report it", _call("code__task__report", {"task_id": task_id}, "rep-wrong"))
        assert "completed." not in _reply_text(reply), _reply_text(reply)[:400]
        assert (rig["workspace"] / "ledger.py").read_text(encoding="utf-8") == LEDGER_WRONG

        # 4. The RETAINED check's own red reopens diagnosis in the same durable task.
        task = drive.step(task_id, "full-retained-red", "workspace.run_tests", {"command": RETAINED},
                          "run the original failing test")
        assert task["stage"] == "cumulative" and task["cumulative"]["success"] is False
        assert task["cumulative"]["covers_obligation"] is True
        drive.turn("what is actually wrong", _call("code__task__identify",
                                                  {"task_id": task_id, "path": "ledger.py", "line": 2,
                                                   "reason": "balance multiplies where the test requires the difference"},
                                                  "id2"))
        drive.step(task_id, "read2", "workspace.read_file", {"path": "ledger.py"}, "re-read the owner")
        drive.turn("propose the real fix", _call("code__task__propose",
                                                 {"task_id": task_id, "proposal_id": "real",
                                                  "intent": "workspace.write_file",
                                                  "arguments": {"path": "ledger.py", "content": LEDGER_FIXED,
                                                                "expected_hash": _sha(LEDGER_WRONG)},
                                                  "rationale": "Owner ledger.py: the test requires the difference."},
                                                 "prop2"))
        drive.turn("approve it", _call("code__task__approve", {"task_id": task_id, "proposal_id": "real"}, "appr2"))
        task = drive.step(task_id, "mut2", "workspace.write_file",
                          {"path": "ledger.py", "content": LEDGER_FIXED, "expected_hash": _sha(LEDGER_WRONG)},
                          "apply the real repair")
        assert task["stage"] == "narrow_test"
        proposals = task["proposals"]
        assert proposals["real"]["approved"] and proposals["real"]["consumed_by"] == "mut2"
        assert proposals["w"]["consumed_by"] == "mut"

        # 5. Real narrow coverage, then a LAWFUL BROADER check that provably includes the node.
        task = drive.step(task_id, "narrow", "workspace.run_tests", {"command": RETAINED}, "run the focused test")
        assert task["stage"] == "cumulative" and task["narrow"]["success"] is True
        assert task["narrow"]["covers_obligation"] is True
        task = drive.step(task_id, "cumulative", "workspace.run_tests", {"command": "python3 -m pytest -q tests"},
                          "run the whole test directory")
        assert task["stage"] == "inspect_diff", task["verifications"][-1]
        assert task["cumulative"]["success"] is True and task["cumulative"]["covers_obligation"] is True
        assert task["cumulative"]["invocation"]["operands"] == ["tests"]
        drive.step(task_id, "diff", "workspace.git_diff", {}, "show the diff")
        reply = drive.turn("report", _call("code__task__report", {"task_id": task_id}, "rep"))
        assert "completed." in _reply_text(reply), _reply_text(reply)[:400]
        assert (rig["workspace"] / "ledger.py").read_text(encoding="utf-8") == LEDGER_FIXED
        final = journal()
        assert final["stage"] == "report"
        assert final["narrow"]["success"] is True and final["cumulative"]["covers_obligation"] is True
        # The wrong-repair evidence stayed in the journal as history, never as the verdict.
        history = final["verifications"]
        assert any(v.get("covers_obligation") is False and v.get("success") is True for v in history)
        assert any(v.get("success") is False for v in history)
        assert final["steps"]["full-k1-again"]["status"] == "repeated_verification"
    finally:
        rig["daemon"].stop()
        rig["provider"].__exit__(None, None, None)
