"""C18 final correction — a command artifact is code CONTEXT, never action AUTHORITY.

REPO_COMMAND_ANCHOR (added in 943de55a) let ANY pasted pytest/test-file command classify as
debugging and seat ``code.task.open`` — even when the operator explicitly asks for explanation,
comparison or summarization and forbids execution. Measured RED at f9f75dad: all four cases
below classified ``debugging`` and seated the code-task control plane.

Root law pinned here:
  - A command or test artifact proves code CONTEXT, not operator authorization to repair or
    execute. Code-context evidence and typed action demand are SEPARATE.
  - No English or translated phrase regex was added; the anchor is no longer, by itself,
    debugging or code.task.open authority anywhere.
  - Where no existing language-neutral typed execution-demand authority can prove the request,
    the turn FAILS CLOSED: multilingual code-task execution stays PARTIAL for the C12 owner.

Positive controls prove genuine bounded repair demands still route and seat, so the five-language
green was not preserved by weakening action-demand truth.
"""
from __future__ import annotations

import pytest

from core.task_router import classify
from core.tool_demand_signals import resolve_demand_signals

#: Review cases: code context present, execution demand absent or explicitly forbidden.
CONTEXT_ONLY_CASES = (
    "Explain this command: python -m pytest -q test_calc.py",
    "Do not run this; what does python -m pytest -q test_calc.py mean?",
    "Here is a log line: pytest test_calc.py failed. Summarize it only.",
    "Compare pytest test_a.py with unittest conceptually; do not change files.",
)


@pytest.mark.parametrize("text", CONTEXT_ONLY_CASES)
def test_command_context_is_not_an_execution_demand_for_classification(text: str) -> None:
    result = classify(text)

    assert result.get("task_class") != "debugging", (text, result.get("task_class"))


@pytest.mark.parametrize("text", CONTEXT_ONLY_CASES)
def test_command_context_does_not_seat_the_code_task_control_plane(text: str) -> None:
    signals = resolve_demand_signals(text)

    assert "code.task.open" not in signals.explicit_intents, text
    assert "code" not in signals.required_families, text


# ---------------------------------------------------------------------------
# Positive controls: genuine typed action demand still routes and seats
# ---------------------------------------------------------------------------


def test_genuine_repair_demand_still_classifies_debugging_and_seats_the_plane() -> None:
    demand = (
        "Find why this test fails (python -m pytest -q), repair the root cause "
        "and show me the exact diff."
    )

    assert classify(demand).get("task_class") == "debugging"
    signals = resolve_demand_signals(demand)
    assert "code.task.open" in signals.explicit_intents or "code" in signals.required_families


def test_bounded_repair_predicate_keeps_its_mutation_plus_verification_meaning() -> None:
    from core.task_router import looks_like_bounded_repo_repair_request

    # The command anchor is code CONTEXT only: it is not, alone, bounded-repair authority in
    # the classifier or the offer seat, and the orchestration predicate ignores it entirely.
    anchored = "Explain this command: python -m pytest -q test_calc.py"
    assert classify(anchored).get("task_class") != "debugging"
    signals = resolve_demand_signals(anchored)
    assert "code.task.open" not in signals.explicit_intents
    assert "code" not in signals.required_families

    # A real bounded repair demand — mutation plus verification — still routes.
    assert looks_like_bounded_repo_repair_request(
        "replace the broken helper and run tests"
    ) is True
