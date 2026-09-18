"""How the scripted served model reads a code task observation (coding revision 3, harness correction).

``ObservedRepairModel`` (``tests/test_code_task_served_units.py``) decides from the newest ``code.task`` observation
line of its served prompt. The renderer clips a long line, and a clipped code task line leads with its declared fields
-- ``intent``, ``ok``, ``status``, ``stage``, ``pending_repairs``, ``evidence``, ``reason``, ``next``,
``verification_failed`` -- so a failed check's own outcome (``ok: true``, ``status: command_failed`` at ``narrow_test``
or ``cumulative``) survives cuts that remove the task-level ``verification_failed`` flag and the full recovery phrase.

Measured in the review's failing run of the served independent-units journey: captured prompt 009 showed exactly that
line, the model read it as "not failed", kept the validation it owed, ended the turn and ran the same full check again
on the next one -- a second failed cumulative verification for revision 1. The captured lines here are what it read
(``fixtures/code_task_served_observations/independent_units_capture.json``; only the capturing machine's temporary
directory prefix is replaced, by a neutral path of the same length).

A failed check in a different project is then cut at every position that leaves its outcome visible. The controls are
steps that did not run (with and without a standing failure), unrelated tools, passing checks and the reproduction,
whose command fails without being a verification. Every observation is runtime output rendered by the production
renderer; none is written by hand.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.test_code_task_repair_units import _door, _git
from tests.test_code_task_served_units import LABELS_FIXED, PRICING_FIXED, ObservedRepairModel, _facts

CAPTURE = json.loads((Path(__file__).resolve().parent / "fixtures" / "code_task_served_observations"
                      / "independent_units_capture.json").read_text(encoding="utf-8"))
CAPTURED = {record["prompt"].removesuffix(".txt"): record for record in CAPTURE["prompts"]}
CLIPPED = " …[clipped here;"


# ---------------------------------------------------------------------------
# The captured served lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", ["009", "010", "011"])
def test_a_captured_failed_full_check_is_read_as_failed_after_its_flag_was_clipped(prompt) -> None:
    line = CAPTURED[prompt]["newest_line"]
    visible = line[:line.index(CLIPPED)]
    assert '"ok": true, "status": "command_failed", "stage": "cumulative"' in visible
    assert '"returncode": 1, "success": false' in visible
    assert '"verification_failed"' not in visible and "still fails: re-diagnose" not in visible
    assert _facts(line)["failed_verification"] is True, visible[-160:]


def test_the_captured_line_that_kept_its_flag_is_read_as_failed() -> None:
    line = CAPTURED["013"]["newest_line"]
    assert CLIPPED not in line and '"verification_failed": true' in line
    assert _facts(line)["failed_verification"] is True


@pytest.mark.parametrize(("prompt", "what"), [
    ("002", "the reproduction: its command failed and the task moved on to identify"),
    ("003", "a read"),
    ("004", "a proposal"),
    ("005", "an approval"),
    ("006", "a landed write"),
    ("007", "a write refused until the landed repair is validated: nothing ran"),
    ("008", "a passing focused check"),
    ("015", "a passing focused check, clipped late"),
    ("016", "a passing full check"),
])
def test_captured_lines_that_are_not_failed_verifications(prompt, what) -> None:
    assert _facts(CAPTURED[prompt]["newest_line"])["failed_verification"] is False, what


def _served_prompt(record: dict[str, Any]) -> str:
    """What the model reads of a captured prompt: the task it was told was opened, and its newest task line."""
    return "\n".join([*(f"Opened code task {task}" for task in record["opened"][-1:]), record["newest_line"]])


def test_the_captured_journey_settles_its_failed_full_check_and_moves_on_to_the_waiting_repair() -> None:
    # The served journey's model, built as test_served_two_known_independent_repairs_are_validated_between_units builds it.
    model = ObservedRepairModel(
        repro="node suite.js", owner="pricing.js", reads=["pricing.js", "labels.js"],
        repairs=[
            {"pid": "pricing", "path": "pricing.js", "content": PRICING_FIXED,
             "rationale": "Owner pricing.js: total subtracts tax; check_pricing requires the sum."},
            {"pid": "labels", "path": "labels.js", "content": LABELS_FIXED,
             "rationale": "Owner labels.js: slug joins with an underscore; check_labels requires a hyphen."},
        ],
        focused={"pricing": "node check_pricing.js", "labels": "node check_labels.js"}, full="node suite.js",
    )
    replies = [model.reply(_served_prompt(CAPTURED[f"{index:03d}"])) for index in range(1, 10)]
    served = CAPTURE["model_log"]
    reached = served.index("run:cumulative:r1") + 1
    assert model.log[:reached] == served[:reached]  # the replay made the served run's decisions up to the full check
    assert model.log[reached:] == ["check:cumulative->cumulative:failed", "write:labels"], model.log
    arguments = json.loads(replies[-1]["function"]["arguments"])
    assert (arguments["intent"], arguments["arguments"]["path"]) == ("workspace.write_file", "labels.js")


# ---------------------------------------------------------------------------
# A different project, driven in process through the runtime door
# ---------------------------------------------------------------------------

PERCENT = "def share_percent(part, whole):\n    return round(part / whole * 10, 2)\n"
PERCENT_FLOOR = "def share_percent(part, whole):\n    return round(part * 100 // whole, 2)\n"
PERCENT_FIXED = "def share_percent(part, whole):\n    return round(part / whole * 100, 2)\n"
CHECK_PERCENT = ("from metrics.percent import share_percent\n"
                 "assert share_percent(1, 8) == 12.5, f'share_percent(1, 8) must be 12.5, got {share_percent(1, 8)}'\n"
                 "print('percent ok')\n")
CHECK_REPORT = CHECK_PERCENT + "assert share_percent(3, 4) == 75.0, share_percent(3, 4)\nprint('report ok')\n"
REPORT_FILES = {"metrics/__init__.py": "", "metrics/percent.py": PERCENT, "check_percent.py": CHECK_PERCENT,
                "check_report.py": CHECK_REPORT, "REPORT_NOTES.md": "report notes: operator owned\n"}


@pytest.fixture(scope="module")
def report_observations(tmp_path_factory) -> dict[str, dict[str, Any]]:
    """One code task on a percentage report: a floor-division repair fails its focused check, a corrected one passes
    narrow and full. Returns each named step's observation payload as the served boundary hands it to the renderer."""
    from core.agent_runtime.response_policy_tool_history import tool_history_observation_payload
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    base = tmp_path_factory.mktemp("percent-report")
    root = base / "report"
    (root / "metrics").mkdir(parents=True)
    for name, text in REPORT_FILES.items():
        (root / name).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("VOOL_BLACKBOX_DIR", str(base / "blackbox"))
        patch.setenv("VOOL_CODE_TASK_DIR", str(base / "code_tasks"))
        reset_mode_permission_state()
        code_task_runtime().reset()
        try:
            ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": "percent-report",
                   "runtime_session_id": "percent-report", "operating_mode": "auto"}
            task_id = _door("code.task.open", {"objective": "Repair the percentage report"}, ctx).details["task_id"]
            numbers = iter(range(1, 100))

            def step(intent: str, arguments: dict[str, Any]):
                return _door("code.task.step", {"task_id": task_id, "step_id": f"p{next(numbers)}", "intent": intent,
                                                "arguments": arguments}, ctx)

            def task_call(tool: str, **arguments: Any) -> None:
                result = _door(tool, {"task_id": task_id, **arguments}, ctx)
                assert result.ok, (tool, result.status, result.response_text)

            def repair(pid: str, content: str) -> None:
                task_call("code.task.propose", proposal_id=pid, intent="workspace.write_file",
                          arguments={"path": "metrics/percent.py", "content": content},
                          rationale="Owner metrics/percent.py: a share is part over whole times one hundred.")
                task_call("code.task.approve", proposal_id=pid)

            seen = {"reproduction": step("workspace.run_tests", {"command": "python3 check_report.py"})}
            task_call("code.task.identify", path="metrics/percent.py", line=2, reason="share_percent scales by ten")
            assert step("workspace.read_file", {"path": "metrics/percent.py"}).ok
            repair("floor-percent", PERCENT_FLOOR)
            seen["check-before-its-repair-lands"] = step("workspace.run_tests", {"command": "python3 check_percent.py"})
            assert step("workspace.write_file", {"path": "metrics/percent.py", "content": PERCENT_FLOOR}).ok
            seen["search-before-any-check"] = step("workspace.search_text", {"query": "median_value", "path": "."})
            seen["missing-read-before-any-check"] = step("workspace.read_file", {"path": "metrics/absent.py"})
            seen["diff-before-any-check"] = step("workspace.git_diff", {})
            seen["failed-focused-check"] = step("workspace.run_tests", {"command": "python3 check_percent.py"})
            seen["search-while-the-failure-stands"] = step("workspace.search_text", {"query": "median_value", "path": "."})
            seen["uncontracted-step-while-the-failure-stands"] = step("workspace.run_testz",
                                                                      {"command": "python3 check_percent.py"})
            task_call("code.task.identify", path="metrics/percent.py", line=2, reason="floor division drops the fraction")
            assert step("workspace.read_file", {"path": "metrics/percent.py"}).ok
            repair("true-percent", PERCENT_FIXED)
            assert step("workspace.write_file", {"path": "metrics/percent.py", "content": PERCENT_FIXED}).ok
            seen["passing-focused-check"] = step("workspace.run_tests", {"command": "python3 check_percent.py"})
            seen["passing-full-check"] = step("workspace.run_tests", {"command": "python3 check_report.py"})
            journal = json.loads((base / "code_tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            # Exactly the checks that ran were recorded: the refused and unrelated steps recorded no verification.
            assert [(v["stage"], v["success"], v["revision"]) for v in journal["verifications"]] == [
                ("narrow_test", False, 1), ("narrow_test", True, 2), ("cumulative", True, 2)]
            assert (root / "metrics/percent.py").read_text(encoding="utf-8") == PERCENT_FIXED
            return {name: tool_history_observation_payload(execution=result, tool_name="code.task.step")
                    for name, result in seen.items()}
        finally:
            code_task_runtime().reset()
            reset_mode_permission_state()


def _rendered(payload: dict[str, Any], neighbours: int) -> str:
    """The payload's line when the renderer shares its window with `neighbours` bulky results before it."""
    from core.prompt_normalizer import _runtime_tool_observation_message

    crowd = [{"intent": "workspace.read_file", "path": f"module_{index:02d}.py", "ok": True, "status": "executed",
              "response_preview": f"MODULE_{index:02d} " + "x" * 3000} for index in range(neighbours)]
    message = _runtime_tool_observation_message({"runtime_tool_observations": [*crowd, dict(payload)]})
    return message.content.splitlines()[neighbours + 1]


def _every_cut(payload: dict[str, Any], *, after: str) -> Iterator[str]:
    """Every line the renderer can produce for this payload once `after` is visible: the declared fields first, cut and
    said to be cut, then the whole observation in sorted form once it fits."""
    from core.prompt_normalizer import _CLIP_NOTICE_TEMPLATE, _priority_first_json

    bare = {key: value for key, value in payload.items() if key != "observation_priority"}
    ordered = _priority_first_json(bare, tuple(payload["observation_priority"]))
    for share in range(ordered.index(after) + len(after), len(ordered)):
        yield f"- {ordered[:share]}{_CLIP_NOTICE_TEMPLATE.format(dropped=len(ordered) - share)}"
    yield "- " + json.dumps(bare, ensure_ascii=False, sort_keys=True, default=str)


def _report_model() -> ObservedRepairModel:
    return ObservedRepairModel(
        repro="python3 check_report.py", owner="metrics/percent.py", reads=["metrics/percent.py"],
        repairs=[{"pid": "true-percent", "path": "metrics/percent.py", "content": PERCENT_FIXED,
                  "rationale": "Owner metrics/percent.py: a share is part over whole times one hundred."}],
        focused={"true-percent": "python3 check_percent.py"}, full="python3 check_report.py",
    )


def _settled_check(line: str, checked: str, revision: int) -> ObservedRepairModel:
    """A model that owes a validation, has issued the check at `checked`, and settles `line` as that check's outcome."""
    model = _report_model()
    model.checkpoint_owed = True
    model._step("check", "workspace.run_tests", {"command": "python3 check_percent.py"},
                expect=("check", (revision, checked), checked))
    model._settle(_facts(line))
    return model


def test_a_failed_check_in_another_project_is_read_as_failed_wherever_its_outcome_survives(report_observations) -> None:
    payload = report_observations["failed-focused-check"]
    # The smallest share a line gets (31 bulky neighbours, the window's item limit) cuts inside the evidence, before the
    # return code; the task-level flag and the recovery phrase are far behind.
    line = _rendered(payload, 31)
    visible = line[:line.index(CLIPPED)]
    assert '"ok": true, "status": "command_failed", "stage": "narrow_test", "pending_repairs": []' in visible
    assert '"returncode"' not in visible and '"success"' not in visible and '"next"' not in visible, visible
    assert _facts(line)["failed_verification"] is True, visible[-160:]
    misread = [cut for cut in _every_cut(payload, after='"stage": "narrow_test"') if not _facts(cut)["failed_verification"]]
    assert not misread, f"{len(misread)} cuts read as not failed, the shortest ending {misread[0][-200:]!r}"


@pytest.mark.parametrize(("name", "checked", "revision", "outcome"), [
    ("failed-focused-check", "narrow_test", 1, "check:narrow_test->narrow_test:failed"),
    ("passing-focused-check", "narrow_test", 2, "check:narrow_test->cumulative"),
    ("passing-full-check", "cumulative", 2, "check:cumulative->inspect_diff"),
])
def test_a_check_that_ran_settles_by_its_own_outcome(report_observations, name, checked, revision, outcome) -> None:
    payload = report_observations[name]
    assert payload["ok"] is True and payload["executed"] is True
    for line in _every_cut(payload, after=f'"stage": "{payload["stage"]}"'):
        model = _settled_check(line, checked, revision)
        assert model.log == [outcome], (model.log, line[-200:])
        assert model.checkpoint_owed is (payload["stage"] == "cumulative"), (model.log, line[-200:])


@pytest.mark.parametrize("name", ["check-before-its-repair-lands", "uncontracted-step-while-the-failure-stands"])
def test_a_step_that_did_not_run_settles_as_refused_whatever_failure_its_line_carries(report_observations, name) -> None:
    payload = report_observations[name]
    assert payload["ok"] is False and payload["executed"] is False
    status, stage = payload["status"], payload["stage"]
    for line in _every_cut(payload, after=f'"status": "{status}"'):
        model = _settled_check(line, stage, 1)
        assert model.log == [f"check-refused:{status}"] and model.checkpoint_owed, (model.log, line[-200:])


@pytest.mark.parametrize("name", [
    "reproduction", "search-before-any-check", "missing-read-before-any-check", "diff-before-any-check",
    "passing-focused-check", "passing-full-check", "check-before-its-repair-lands",
])
def test_an_observation_that_is_not_a_failed_check_is_never_read_as_one(report_observations, name) -> None:
    payload = report_observations[name]
    if name == "reproduction":
        assert (payload["ok"], payload["status"], payload["stage"]) == (True, "command_failed", "identify")
    misread = [line for line in _every_cut(payload, after='"intent": "code.task.step"')
               if _facts(line)["failed_verification"]]
    assert not misread, misread[0][-240:]


@pytest.mark.parametrize("name", ["search-while-the-failure-stands", "uncontracted-step-while-the-failure-stands"])
def test_a_step_that_is_not_a_check_reads_as_failed_only_where_the_runtime_says_so(report_observations, name) -> None:
    payload = report_observations[name]
    assert payload["verification_failed"] is True and payload["status"] != "command_failed"
    for line in _every_cut(payload, after='"intent": "code.task.step"'):
        said = '"verification_failed": true' in line or "still fails: re-diagnose" in line
        assert _facts(line)["failed_verification"] is said, line[-240:]
