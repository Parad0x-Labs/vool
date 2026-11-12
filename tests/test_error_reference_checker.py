"""The deterministic reference and coverage checker is itself a gate: it must pass on this
tree, and its output must be stable across runs (same source, same report: no timestamps,
no environment-dependent ordering)."""
from __future__ import annotations

from tools.check_error_reference import main, run_checks


def test_the_error_reference_checks_pass_on_this_tree() -> None:
    problems, _report = run_checks()
    assert problems == [], "error reference drift: " + "; ".join(problems)


def test_the_checker_is_deterministic() -> None:
    first_problems, first_report = run_checks()
    second_problems, second_report = run_checks()
    assert first_problems == second_problems
    assert first_report == second_report, "the checker's report must be stable"


def test_the_command_exits_zero_and_says_so(capsys) -> None:
    assert main([]) == 0
    assert "error reference and coverage checks passed" in capsys.readouterr().out
