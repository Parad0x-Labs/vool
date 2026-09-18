"""A working directory outside the workspace is REFUSED before anything runs -- never "outcome unknown".

Measured live 2026-09-07 (packaged app, Manual mode): the model proposed
`sandbox.run_command` with `cwd=/Users`; the operator was asked to approve it and did; the
resumed call then answered "Outcome unknown -- reconciliation required. The change may have
already happened, so retrying it is blocked". Nothing had run: the working-directory check
raised inside the dispatch boundary, and the boundary read the raise as a post-dispatch
exception. A refusal that spawned no process is a refusal, it names the workspace, and it
must be decided before an operator is asked to approve the action.
"""
from __future__ import annotations

from pathlib import Path

from core.effect_reconciliation import EffectOutcomeUnknown, clear_in_flight_effect, set_in_flight_effect
from core.runtime_execution_tools import execute_runtime_tool


def _run(tmp_path: Path, cwd: str):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return execute_runtime_tool(
        "sandbox.run_command",
        {"command": "ls -la", "cwd": cwd},
        source_context={"workspace_root": str(workspace), "workspace": str(workspace)},
    )


def test_a_cwd_outside_the_workspace_is_refused_by_name(tmp_path: Path) -> None:
    result = _run(tmp_path, "/Users")
    assert result is not None and result.handled and not result.ok
    assert result.status == "cwd_outside_workspace", (result.status, result.response_text)
    assert "/Users" in result.response_text and "workspace" in result.response_text.lower()
    assert result.details.get("executed") is False


def test_the_refusal_stays_a_refusal_with_an_effect_claim_in_flight(tmp_path: Path) -> None:
    """The A6 boundary turns a raise into UNKNOWN only after dispatch; a refusal before it must not raise."""
    token = set_in_flight_effect({"tool_name": "sandbox.run_command", "logical_effect_id": "le-test", "effect_instance_id": "ei-test"})
    try:
        try:
            result = _run(tmp_path, "/Users")
        except EffectOutcomeUnknown as exc:  # pragma: no cover - the defect
            raise AssertionError(f"a refusal before dispatch was classified as an unknown outcome: {exc}")
    finally:
        clear_in_flight_effect(token)
    assert result.status == "cwd_outside_workspace", result.status
    assert "unknown" not in result.response_text.lower()
