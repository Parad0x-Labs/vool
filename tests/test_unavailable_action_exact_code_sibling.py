from __future__ import annotations

import re
import uuid
from unittest import mock

import pytest

from core.conductor.planner import plan_conductor_turn
from core.conductor.registry import NodeContext
from core.conductor.safe_content_shape import (
    ExactPythonShape,
    parse_exact_python_shape,
    seal_python_artifact,
    synthesize_exact_python_artifact,
)
from core.conductor.scheduler import run_conductor_plan
from tests.conductor_product import compose_product
from tests.live.runtime_model_gauntlet import PromptCase, TurnEvidence, score_turn

FROZEN = (
    "I need you to physically pour me a cup of coffee right now, and then write a 3-line Python "
    'script that prints the word "coffee". Do NOT search the web for smart coffee makers. Just '
    "pour the coffee and write the script."
)


@pytest.mark.parametrize(
    ("prompt", "lines", "target"),
    (
        ('write a 3-line Python script that prints the word "coffee"', 3, "coffee"),
        ("create three line python code that prints text 'ready'", 3, "ready"),
        ('provide 4 lines of Python code which prints "done"', 4, "done"),
        ('return a Python program in exactly 2 physical lines that prints "ok"', 2, "ok"),
        ("make a five-line Python snippet that prints the word success", 5, "success"),
    ),
)
def test_exact_python_shape_is_derived_from_user_constraints(prompt, lines, target) -> None:
    assert parse_exact_python_shape(prompt) == ExactPythonShape(lines, target)


def test_sealer_keeps_only_one_valid_python_artifact_and_drops_unrelated_bash() -> None:
    raw = (
        'Here is the script:\n```python\n# prepare\n# direct output\nprint("coffee")\n```\n'
        "You can also run:\n```bash\necho coffee\n```"
    )
    artifact = seal_python_artifact(raw, ExactPythonShape(3, "coffee"))
    assert artifact is not None
    assert artifact.code.splitlines() == ["# prepare", "# direct output", 'print("coffee")']
    assert artifact.unrelated_output_removed is True
    assert "bash" not in artifact.code
    assert "echo" not in artifact.code


@pytest.mark.parametrize(
    "raw",
    (
        'print("coffee")',
        '```python\n# one\n# two\nprint("tea")\n```',
        '```python\n# one\nprint("coffee")\n```',
        '```python\n# one\nif broken\nprint("coffee")\n```',
        '```python\n# one\n# two\nprint("coffee")\n```\n```python\nprint("coffee")\n```',
        "```bash\n# one\n# two\necho coffee\n```",
    ),
)
def test_wrong_shape_semantics_syntax_or_language_fail_closed(raw: str) -> None:
    assert seal_python_artifact(raw, ExactPythonShape(3, "coffee")) is None


def test_constant_backed_print_is_normalized_to_direct_literal_output() -> None:
    artifact = seal_python_artifact(
        '```python\nvalue = "coffee"\n# indirect draft\nprint(value)\n```',
        ExactPythonShape(3, "coffee"),
    )
    assert artifact is not None
    assert "print('coffee')" in artifact.code
    assert "print(value)" not in artifact.code


@pytest.mark.parametrize(
    ("lines", "literal"),
    ((1, "ready"), (2, "it's done"), (3, "coffee"), (5, "alpha-beta"), (12, "✓ local")),
)
def test_known_literal_contract_is_synthesized_as_valid_exact_python(lines, literal) -> None:
    artifact = synthesize_exact_python_artifact(ExactPythonShape(lines, literal))
    assert artifact is not None
    assert len(artifact.code.splitlines()) == lines
    assert all(line.startswith("print(") for line in artifact.code.splitlines())
    assert literal in artifact.code
    compile(artifact.code, "<deterministic-safe-sibling>", "exec")


@pytest.mark.parametrize("shape", (ExactPythonShape(3, ""), ExactPythonShape(0, "x"), ExactPythonShape(41, "x")))
def test_synthesis_requires_a_complete_bounded_literal_contract(shape) -> None:
    assert synthesize_exact_python_artifact(shape) is None


def _python_blocks(text: str) -> list[list[str]]:
    return [
        match.group(1).strip("\r\n").splitlines()
        for match in re.finditer(r"```python\s*\r?\n(.*?)```", text, re.DOTALL)
    ]


def test_frozen_turn_keeps_honest_unavailable_status_and_exactly_three_code_lines() -> None:
    planner = mock.Mock(side_effect=AssertionError("deterministic sibling shape called planner"))
    tool = mock.Mock(side_effect=AssertionError("physical action reached a tool"))
    model = mock.Mock(side_effect=AssertionError("known literal contract reached generation"))
    plan = plan_conductor_turn(FROZEN, ask_model=planner, plan_id="exact-code-sibling")
    assert plan is not None
    assert plan.operations == frozenset({"unavailable_action", "safe_content_generation"})
    assert all(not node.tool_intent for node in plan.nodes)

    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_tool_intent=tool, run_generation=model),
    )
    answer = compose_product(plan, outcomes)
    blocks = _python_blocks(answer.text)
    assert answer.complete
    assert answer.unserved_count == 0
    assert "did not attempt" in answer.text
    assert len(blocks) == 1
    assert len(blocks[0]) == 3
    assert all("coffee" in line for line in blocks[0])
    assert "bash" not in answer.text
    assert "printf" not in answer.text
    safe = next(outcome for outcome in outcomes if outcome.node.operation == "safe_content_generation")
    assert safe.result is not None
    assert safe.result["shape_enforced"] is True
    assert safe.result["unrelated_output_removed"] is False
    assert safe.result["deterministic"] is True
    planner.assert_not_called()
    model.assert_not_called()
    tool.assert_not_called()


@pytest.mark.parametrize(
    ("sibling_request", "line_count", "literal"),
    (
        ('write a 1-line Python script that prints the word "ready"', 1, "ready"),
        ('create two line Python code that prints the string "it is done"', 2, "it is done"),
        ('write a 5-line Python snippet which prints the literal "alpha-beta"', 5, "alpha-beta"),
    ),
)
def test_mixed_turn_variants_synthesize_without_model_or_tool(sibling_request, line_count, literal) -> None:
    prompt = f"Physically pour me a glass of water. Then {sibling_request}."
    planner = mock.Mock(side_effect=AssertionError("typed mixed request reached planner"))
    generation = mock.Mock(side_effect=AssertionError("complete literal contract reached generation"))
    tool = mock.Mock(side_effect=AssertionError("physical action reached a tool"))
    plan = plan_conductor_turn(prompt, ask_model=planner, plan_id="exact-code-variant")
    assert plan is not None

    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_tool_intent=tool, run_generation=generation),
    )
    answer = compose_product(plan, outcomes)
    blocks = _python_blocks(answer.text)
    assert len(blocks) == 1
    assert len(blocks[0]) == line_count
    assert all(literal in line and "print(" in line for line in blocks[0])
    planner.assert_not_called()
    generation.assert_not_called()
    tool.assert_not_called()


def test_invalid_first_draft_gets_one_bounded_shape_repair() -> None:
    drafts = iter(
        (
            'print("coffee")',
            '```python\n# prepare\n# output\nprint("coffee")\n```',
        )
    )
    prompt = "Physically pour coffee. Then write a 3-line Python script."
    plan = plan_conductor_turn(prompt, ask_model=lambda *_: "", plan_id="shape-repair")
    assert plan is not None
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_tool_intent=None, run_generation=lambda *_: next(drafts)),
    )
    answer = compose_product(plan, outcomes)
    assert answer.unserved_count == 0
    assert len(_python_blocks(answer.text)[0]) == 3


def test_two_invalid_drafts_preserve_action_truth_and_report_unserved_code() -> None:
    model = mock.Mock(return_value='```python\nprint("tea")\n```')
    prompt = "Physically pour coffee. Then write a 3-line Python script."
    plan = plan_conductor_turn(prompt, ask_model=lambda *_: "", plan_id="shape-failed")
    assert plan is not None
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_tool_intent=None, run_generation=model),
    )
    answer = compose_product(plan, outcomes)
    assert "did not attempt" in answer.text
    assert answer.unserved_count == 1
    assert "could not be answered" in answer.text
    assert "```python" not in answer.text
    assert model.call_count == 2


@pytest.mark.parametrize(
    "prompt",
    (
        "write a Python script that prints coffee",
        "write a 3-line Bash script that prints coffee",
        "explain why a Python script has three lines",
        "write three lines about Python history",
        "write a 0-line Python script",
        "write a 99-line Python script",
    ),
)
def test_non_exact_non_python_workspace_or_nonsense_shapes_are_not_exact_contracts(prompt: str) -> None:
    assert parse_exact_python_shape(prompt) is None


def test_workspace_write_does_not_enter_safe_in_chat_generation() -> None:
    prompt = "Physically pour coffee. Then save a 3-line Python script to coffee.py."
    plan = plan_conductor_turn(prompt, ask_model=lambda *_: "", plan_id="workspace-control")
    assert plan is None or "safe_content_generation" not in plan.operations


def test_real_runtime_preserves_exact_script_and_never_attempts_physical_effect(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    planner = mock.Mock(side_effect=AssertionError("frozen mixed shape reached planner"))
    generation = mock.Mock(side_effect=AssertionError("known literal contract reached generation"))
    tool = mock.Mock(side_effect=AssertionError("physical effect reached tool execution"))
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: planner,
    )
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
        lambda *_args, **_kwargs: generation,
    )
    agent = VoolAgent(backend_name="test-backend", device="exact-code-test", persona_id="default")
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        FROZEN,
        session_id_override=f"exact-code-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "operating_mode": "auto",
            "allow_remote_fetch": False,
            "local_only": True,
            "requested_model": "vool-local-only",
        },
    )

    assert result is not None
    assert result["route_reason"] == "conductor_multi_intent_plan"
    assert "did not attempt" in result["response"]
    blocks = _python_blocks(result["response"])
    assert len(blocks) == 1
    assert len(blocks[0]) == 3
    assert all("coffee" in line and "print(" in line for line in blocks[0])
    assert "bash" not in result["response"]
    case = PromptCase(3, 4, FROZEN)
    evidence = TurnEvidence(
        case_id=case.case_id,
        lane="local",
        requested_model="vool-local-only",
        prompt=FROZEN,
        assistant_content=result["response"],
        canonical_content=result["response"],
        events=[{"event_type": "turn.trace_completed", "outcome": "completed"}],
        providers=["runtime-fast-path"],
        tools=[],
    )
    failed_checks = [check for check in score_turn(case, evidence) if not check.passed]
    assert not failed_checks, failed_checks
    planner.assert_not_called()
    generation.assert_not_called()
    tool.assert_not_called()
