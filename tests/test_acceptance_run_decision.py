"""The acceptance runner's release decision refuses every run that is not a full pass.

An outside review found that run_acceptance.py exited 0 with FAIL/BLOCKED/ERROR rows in its table
(only an early stop made it exit 1), that apply-review always exited 0, that a case the run never
reached simply vanished from the table, and that a failed clean-up left a passing case passing.
Each of those must now refuse the run; all planned cases passing must still accept it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("run_acceptance_decision", REPO_ROOT / "ops" / "acceptance" / "run_acceptance.py")
assert _SPEC and _SPEC.loader
acc = importlib.util.module_from_spec(_SPEC)
sys.modules["run_acceptance_decision"] = acc
_SPEC.loader.exec_module(acc)

PLANNED = [{"case": f"C{i}", "scenario": f"S{i}", "lane": "email"} for i in range(1, 4)]


def _row(case: str, status: str, turn: object = 1, graded: bool = True) -> dict:
    return {"case": case, "scenario": "S", "lane": "email", "turn": turn, "graded": graded, "status": status}


def _all_pass() -> list[dict]:
    return [_row("C1", "setup", graded=False), _row("C1", "PASS", 2), _row("C2", "PASS"), _row("C3", "PASS")]


def test_every_planned_case_passing_is_accepted() -> None:
    assert acc.run_accepted(_all_pass(), PLANNED) is True


@pytest.mark.parametrize("status", ["FAIL", "ERROR", "BLOCKED", "REVIEW"])
def test_one_non_pass_row_refuses_the_run(status: str) -> None:
    rows = [*_all_pass(), _row("C2", status, 2)]
    assert acc.run_accepted(rows, PLANNED) is False


def test_a_case_never_reached_refuses_the_run_and_is_shown_as_not_run(tmp_path: Path) -> None:
    rows = [r for r in _all_pass() if r["case"] != "C3"]
    assert acc.run_accepted(rows, PLANNED) is False
    table = acc.write_table(tmp_path, rows, {"planned": PLANNED})
    assert "NOT ACCEPTED" in table
    assert "| C3 | 0 | NOT RUN |" in table
    assert "NOT RUN 1" in table


def test_an_early_stop_or_an_empty_plan_refuses_the_run() -> None:
    assert acc.run_accepted(_all_pass(), PLANNED, note="first 3 answers broken") is False
    assert acc.run_accepted(_all_pass(), []) is False


def test_apply_review_reports_the_decision_and_an_unreviewed_row_keeps_it_refused(tmp_path: Path) -> None:
    rows = [*_all_pass(), dict(_row("C2", "REVIEW", 2), right="PASS", plain="PASS")]
    (tmp_path / "raw.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / "source.json").write_text(json.dumps({"planned": PLANNED}))
    _table, accepted = acc.apply_review(tmp_path)
    assert accepted is False
    (tmp_path / "review.json").write_text(json.dumps({"C2.t2": {"verdict": "pass", "reason": "answered the ask"}}))
    _table, accepted = acc.apply_review(tmp_path)
    assert accepted is True
    assert acc.main(["--apply-review", str(tmp_path)]) == 0
    (tmp_path / "review.json").write_text(json.dumps({"C2.t2": {"verdict": "fail", "reason": "wrong sender"}}))
    assert acc.main(["--apply-review", str(tmp_path)]) == 1


class _Page:
    def __init__(self, delete_ok: bool) -> None:
        self.n = 0
        self.delete_ok = delete_ok

    def new_chat(self) -> str:
        self.n += 1
        return f"chat-{self.n}"

    def current_chat(self) -> str:
        return f"chat-{self.n}"

    def screenshot(self, _name: str) -> str:
        return ""

    def ui_checks(self, *_args: object) -> list:
        return []

    def delete_chat(self, _chat: str) -> bool:
        return self.delete_ok


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    def flush(self) -> None:
        pass


def _case() -> object:
    steps = [{"chat": "A", "grade": False}, {"chat": "A", "grade": False, "action": "restart_daemon"}]
    return acc.Case(case_id="C9", scenario={"id": "S9", "lane": "email", "steps": steps}, phrasing=0, values={})


def test_rows_reached_before_an_early_stop_stay_in_the_run_and_a_failed_clean_up_is_an_error() -> None:
    sink: list[dict] = [_row("C8", "PASS")]
    with pytest.raises(acc.EarlyStop):
        acc.run_case(_case(), _Page(delete_ok=False), None, Path("."), False, acc.Gate(), _Log(), sink=sink)
    c9 = [r for r in sink if r["case"] == "C9"]
    assert [r["turn"] for r in c9] == [1, "cleanup"]
    assert c9[-1]["status"] == "ERROR"
    assert acc.case_status(c9) == "ERROR"


def test_a_clean_case_adds_no_clean_up_row() -> None:
    sink: list[dict] = []
    with pytest.raises(acc.EarlyStop):
        acc.run_case(_case(), _Page(delete_ok=True), None, Path("."), False, acc.Gate(), _Log(), sink=sink)
    assert [r["turn"] for r in sink] == [1]
