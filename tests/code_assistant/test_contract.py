"""The canonical proposal contract: allowlist, trust-key forging, confinement -- and the
single-door law. The contract VALIDATES proposals; it executes nothing. Everything that runs
crosses the ONE production door as a contracted ``code.task.*`` call."""
from __future__ import annotations

import ast
import inspect

import pytest

from core.code_assistant.contract import (
    CodeAssistantProposal,
    ContractRefused,
    validate_proposal,
)


def test_intent_outside_the_families_is_refused_before_any_authority():
    with pytest.raises(ContractRefused) as caught:
        validate_proposal(CodeAssistantProposal(intent="wallet.spend", arguments={}))
    assert caught.value.reason == "intent_not_in_code_assistant_scope"


def test_unknown_intent_is_refused_typed():
    with pytest.raises(ContractRefused) as caught:
        validate_proposal(CodeAssistantProposal(intent="totally.made_up", arguments={}))
    assert caught.value.reason == "intent_not_in_code_assistant_scope"


@pytest.mark.parametrize("path", ["../escape.txt", "/etc/passwd", "~/Desktop/x", "a/../../b"])
def test_unconfined_paths_are_refused(path: str):
    with pytest.raises(ContractRefused) as caught:
        validate_proposal(
            CodeAssistantProposal(intent="workspace.write_file", arguments={"path": path, "content": "x"})
        )
    assert caught.value.reason == "unconfined_path"


def test_underscore_argument_cannot_smuggle_a_trust_flag():
    with pytest.raises(ContractRefused) as caught:
        validate_proposal(
            CodeAssistantProposal(
                intent="sandbox.run_command",
                arguments={"command": "ls", "_trusted_local_only": True},
            )
        )
    assert caught.value.reason == "forged_trust_key"


def test_empty_shell_command_is_refused():
    with pytest.raises(ContractRefused) as caught:
        validate_proposal(CodeAssistantProposal(intent="sandbox.run_command", arguments={"command": "  "}))
    assert caught.value.reason == "empty_command"


# ---------------------------------------------------------------- the one execution path


def test_the_contract_module_executes_nothing():
    """The retired second door stays retired: the contract package exposes no execution entry and
    imports no execution boundary. The only executor in the package is the task runtime's dispatch
    seam behind ``core.runtime_execution_tools``."""
    import core.code_assistant.contract as contract_module
    import core.code_assistant.dialects as dialects_module
    import core.code_assistant.review as review_module

    for module in (contract_module, dialects_module, review_module):
        assert not hasattr(module, "execute_proposal")
        assert not hasattr(module, "execute_authorized_runtime_tool")
    for name in ("execute_proposal", "execute_authorized_runtime_tool"):
        imported = set()
        for node in ast_walk(contract_module):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(str(node.module or ""))
                imported |= {alias.name for alias in node.names}
        assert name not in imported, f"{name} is imported by the contract module"
    # ...and the one door is the runtime tool door the served lane crosses.
    import core.runtime_execution_tools as door_module

    assert hasattr(door_module, "execute_runtime_tool")


def ast_walk(module):
    return [node for node in ast.walk(ast.parse(inspect.getsource(module)))]


def test_a_read_step_runs_through_the_production_door_with_its_permission(auto_context):
    """The single-door proof, served shape: a step's receipts carry the boundary's permission
    decision and effect receipts -- evidence the call crossed the door, not a private path."""
    from .conftest import door

    opened = door("code.task.open", {"objective": "read the owner"}, auto_context)
    task_id = opened.details["task_id"]
    step = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "r", "intent": "workspace.read_file", "arguments": {"path": "stats.py"}},
        auto_context,
    )
    assert step.ok and step.details["executed"] is True, step.response_text
    assert step.details["tool_result"].get("hash"), step.details["tool_result"]


def test_review_intent_is_served_by_the_door(auto_context):
    from .conftest import door

    receipt = door("code.review_evidence", {"turn_id": "no-such-turn"}, auto_context)
    assert receipt.status == "not_found"
    assert receipt.details["executed"] is False


def test_browser_smoke_family_is_supported_end_to_end():
    """The browser-smoke family is part of the assistant's contract surface: the intent is in
    the family allowlist, its runtime contract exists and is read-only, and a proposal for it
    validates. (A live render needs a browser runtime; that lane owns its own proofs.)"""
    from core.runtime_tool_contracts import runtime_tool_contract_map

    proposal = CodeAssistantProposal(intent="browser.render", arguments={"url": "https://example.invalid"})
    assert validate_proposal(proposal) == "browser_smoke"
    contract = runtime_tool_contract_map()["browser.render"]
    assert contract.read_only
    # browser.render is dispatched by the browser lane (handler=external_lane); the coding
    # assistant may propose it, the permission boundary still decides it, and this contract
    # never claims to execute it itself.
    assert contract.handler == "external_lane"
