"""The acceptance library loads, and the grader catches the wording a person reported.

These guard the acceptance runner itself (docs/acceptance/STANDARD.md): a library file that does
not load, or a grader that lets "provided context" through, would make every acceptance table
wrong without anyone noticing.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("run_acceptance", REPO_ROOT / "ops" / "acceptance" / "run_acceptance.py")
assert _SPEC and _SPEC.loader
acc = importlib.util.module_from_spec(_SPEC)
sys.modules["run_acceptance"] = acc
_SPEC.loader.exec_module(acc)


def test_library_loads_and_every_lane_is_present() -> None:
    scenarios, hashes = acc.load_library()
    lanes = {s["lane"] for s in scenarios}
    assert {"memory", "chat_context", "email", "pdf", "reminders_calendar", "browser_skills",
            "sidebar_interrupt_updates"} <= lanes
    assert len(hashes) >= 7
    cases = acc.expand(scenarios, seed=1)
    # Fresh values per case: two cases of the same scenario do not share a generated code.
    codes = [c.values["code"] for c in cases if c.scenario["id"] == "MEM-CROSS-02"]
    assert len(codes) >= 3 and len(set(codes)) == len(codes)


def test_library_refuses_a_single_phrasing_scenario(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text(
        "lane: x\nscenarios:\n  - id: X-1\n    steps:\n      - say: hi\n        expect: {any: ['hi']}\n",
        encoding="utf-8",
    )
    with pytest.raises(acc.LibraryError, match="phrasings"):
        acc.load_library(tmp_path)


def test_reported_answer_fails_right_and_plain() -> None:
    step = {"expect": {"all": ["${code}"]}}
    v = acc.grade_turn(step, "Your test hotel booking code is not listed in the provided context.", 4.0,
                       {"code": "HT-48213"})
    assert v.right == "FAIL" and v.plain == "FAIL" and v.status == "FAIL"


def test_code_written_with_a_space_still_counts_as_right() -> None:
    step = {"expect": {"all": ["${code}"]}}
    v = acc.grade_turn(step, "It's HT 48213, from your trip chat.", 4.0, {"code": "HT-48213"})
    assert v.status == "PASS"


def test_asking_back_a_clear_question_is_not_helpful() -> None:
    step = {"expect": {"all": ["(?i)sunrise bakery"]}}
    v = acc.grade_turn(step, "Do you mean Bakery A, B or C?", 3.0, {})
    assert v.helpful == "FAIL"


def test_slow_answer_fails_helpful_and_review_waits_for_a_person() -> None:
    step = {"expect": {"any": ["\\d"], "max_seconds": 60}}
    assert acc.grade_turn(step, "You have 10 unread emails.", 400.0, {}).helpful == "FAIL"
    step = {"expect": {"any": ["\\d"], "review": "number matches the mailbox"}}
    assert acc.grade_turn(step, "You have 10 unread emails.", 5.0, {}).status == "REVIEW"
