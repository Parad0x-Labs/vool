"""Served journey for the effective-invocation contract (revision 3): real daemon, real
``/api/chat``, disposable Git fixture, production task/sandbox/approval/journal/report
paths. THE MODEL IS SCRIPTED (one planned tool call per turn) AND SAYS SO: no live autonomy
is claimed and none could be.

The journey the revision-2 review demanded, in ONE durable task:

* reproduce the real failure, land a deliberately WRONG repair through the production
  propose/approve/mutate door;
* attempt irrelevant green commands -- inline code naming the check file as an argument, a
  harmless sibling script, and the check under ``-O`` (its assertions stripped) -- and watch
  each one refuse completion: they run as evidence, and the obligation stays open;
* re-diagnose, propose the REAL fix, obtain a FRESH approval, land it;
* complete with the actual covering checks (the retained command under its own cwd) and a
  green report whose obligation says the current cumulative covered it.

Every assertion reads the task journal, the disk or the reply the daemon served; a refusal
is proven by the journal's typed state plus the absence of a completed verdict, never by
prose alone.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_code_assistant_served_journey import _call
from tests.test_code_task_served_purpose import MODEL, TurnDrive, _boot_rig, _reply_text

pytestmark = [pytest.mark.served]

CALC_BUGGY = "def total(subtotal, tax):\n    return subtotal - tax\n"
CALC_WRONG = "def total(subtotal, tax):\n    return subtotal * tax\n"
CALC_FIXED = "def total(subtotal, tax):\n    return subtotal + tax\n"
CHECK = "from calc import total\nassert total(40, 8) == 48, total(40, 8)\nprint('calc ok')\n"
HARMLESS = "print('diagnostic only')\n"
FILES = {"calc.py": CALC_BUGGY, "check.py": CHECK, "harmless.py": HARMLESS}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_served_irrelevant_green_cannot_close_a_wrong_repair_then_real_fix_completes(
        tmp_path: Path) -> None:
    rig = _boot_rig(tmp_path, "invocation-r3", FILES)
    try:
        drive = TurnDrive(rig, "served-invocation-r3")
        drive.turn("Fix the failing test in this project and explain the change.",
                   _call("code__task__open",
                         {"objective": "repair calc.total so its check passes"}, "open"))
        files = sorted((rig["store_dir"] / "code_tasks").glob("ct-*.json"))
        assert len(files) == 1, files
        task_id = files[0].stem

        def journal() -> dict[str, Any]:
            return json.loads(
                (rig["store_dir"] / "code_tasks" / f"{task_id}.json").read_text(encoding="utf-8"))

        # 1. The real failure, reproduced through the task plane.
        drive.step(task_id, "repro", "workspace.run_tests", {"command": "python3 check.py"},
                   "reproduce the failure")
        assert journal()["stage"] == "identify"
        assert journal()["reproduced_failure"]["command"].endswith("check.py")
        assert journal()["reproduced_failure"]["cwd"] == ""

        # 2. A competent-but-wrong first fix, approved and landed through the production door.
        drive.turn("identify it", _call("code__task__identify",
                                        {"task_id": task_id, "path": "calc.py", "line": 2,
                                         "reason": "total subtracts the tax"}, "id"))
        drive.step(task_id, "read", "workspace.read_file", {"path": "calc.py"}, "read the owner")
        drive.turn("propose", _call("code__task__propose",
                                    {"task_id": task_id, "proposal_id": "w", "intent": "workspace.write_file",
                                     "arguments": {"path": "calc.py", "content": CALC_WRONG,
                                                   "expected_hash": _sha(CALC_BUGGY)},
                                     "rationale": "Owner calc.py: total combines its operands wrongly."}, "prop"))
        drive.turn("approve", _call("code__task__approve", {"task_id": task_id, "proposal_id": "w"}, "appr"))
        drive.step(task_id, "mut", "workspace.write_file",
                   {"path": "calc.py", "content": CALC_WRONG, "expected_hash": _sha(CALC_BUGGY)},
                   "apply the repair")
        assert (rig["workspace"] / "calc.py").read_text(encoding="utf-8") == CALC_WRONG

        # 3. Irrelevant greens cannot close it. Inline code naming check.py as an ARGUMENT:
        #    it runs, it is journaled as a diagnostic, and it retires nothing.
        inline = drive.step(task_id, "narrow-inline", "workspace.run_tests",
                            {"command": 'python3 -c "print(123)" check.py'},
                            "run the check now")
        assert inline["stage"] == "narrow_test" and inline["narrow"] is None
        entry = inline["verifications"][-1]
        assert entry["obligation_match"] is False and entry["invocation"]["family"] == "python-inline"
        # A harmless sibling executing while check.py rides as program argv: genuine through
        # the obligation's runner, green, and still not covering the obligation.
        sibling = drive.step(task_id, "narrow-sibling", "workspace.run_tests",
                             {"command": "python3 harmless.py check.py"}, "run the check now")
        assert sibling["stage"] == "cumulative"  # a genuine focused check advanced the stage
        assert sibling["verifications"][-1]["covers_obligation"] is False
        # The check under -O strips the acceptance assertion: green, and the cumulative
        # boundary holds the task open.
        optimized = drive.step(task_id, "cumulative-o", "workspace.run_tests",
                               {"command": "python3 -O check.py"}, "run the full check now")
        assert optimized["stage"] == "cumulative"  # refused: not the acceptance condition
        assert optimized["verifications"][-1]["covers_obligation"] is False
        assert "__debug__" in optimized["verifications"][-1]["stale_reason"]
        reply = drive.turn("report it", _call("code__task__report", {"task_id": task_id}, "rep-wrong"))
        assert "completed." not in _reply_text(reply), _reply_text(reply)[:400]
        assert journal()["cumulative"] is None or journal()["cumulative"]["covers_obligation"] is not True
        assert (rig["workspace"] / "calc.py").read_text(encoding="utf-8") == CALC_WRONG

        # 4. Re-diagnose in the SAME durable task: first the retained check's honest red
        #    (the lawful key that reopens review), then a fresh diagnosis.
        retained = drive.step(task_id, "cumulative-red", "workspace.run_tests",
                              {"command": "python3 check.py"}, "run the real full check")
        assert retained["stage"] == "cumulative" and retained["cumulative"]["success"] is False
        drive.turn("what is actually wrong", _call("code__task__identify",
                                                  {"task_id": task_id, "path": "calc.py", "line": 2,
                                                   "reason": "total multiplies where the check requires the sum"},
                                                  "id2"))
        drive.step(task_id, "read2", "workspace.read_file", {"path": "calc.py"}, "re-read the owner")
        drive.turn("propose the real fix", _call("code__task__propose",
                                                 {"task_id": task_id, "proposal_id": "real",
                                                  "intent": "workspace.write_file",
                                                  "arguments": {"path": "calc.py", "content": CALC_FIXED,
                                                                "expected_hash": _sha(CALC_WRONG)},
                                                  "rationale": "Owner calc.py: the check requires the sum."},
                                                 "prop2"))
        drive.turn("approve it", _call("code__task__approve", {"task_id": task_id, "proposal_id": "real"},
                                       "appr2"))
        drive.step(task_id, "mut2", "workspace.write_file",
                   {"path": "calc.py", "content": CALC_FIXED, "expected_hash": _sha(CALC_WRONG)},
                   "apply the real repair")

        # 5. Complete with the ACTUAL covering checks: the retained command, twice, green.
        narrow = drive.step(task_id, "narrow", "workspace.run_tests", {"command": "python3 check.py"},
                            "run the focused check now")
        assert narrow["stage"] == "cumulative" and narrow["narrow"]["success"] is True
        cumulative = drive.step(task_id, "cumulative", "workspace.run_tests", {"command": "python3 check.py"},
                                "run the full check now")
        assert cumulative["stage"] == "inspect_diff", cumulative["stage"]
        assert cumulative["cumulative"]["success"] is True
        assert cumulative["cumulative"]["covers_obligation"] is True
        assert cumulative["cumulative"]["invocation"]["entry"] == "check.py"
        drive.step(task_id, "diff", "workspace.git_diff", {}, "show the diff")
        reply = drive.turn("report", _call("code__task__report", {"task_id": task_id}, "rep"))
        assert "completed." in _reply_text(reply), _reply_text(reply)[:400]
        assert (rig["workspace"] / "calc.py").read_text(encoding="utf-8") == CALC_FIXED
        final = journal()
        assert final["stage"] == "report"
        assert final["narrow"]["success"] is True and final["cumulative"]["success"] is True
        assert final["cumulative"]["covers_obligation"] is True
        # The wrong-repair evidence stayed in the journal as history, never as the verdict.
        assert any(v.get("covers_obligation") is False for v in final["verifications"])
        assert any(v.get("obligation_match") is False for v in final["verifications"])
        assert any(v.get("success") is False for v in final["verifications"])
    finally:
        rig["daemon"].stop()
        rig["provider"].__exit__(None, None, None)
