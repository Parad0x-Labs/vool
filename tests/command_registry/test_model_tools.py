"""Model-offerable projection: eligibility, execution parity, and the
operator-only sabotage (an operator-only command must NEVER be offered, and
invoking it through the model lane must refuse at the operator gate).
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module", autouse=True)
def projected():
    from core.command_registry.model_tools import project_model_tools
    from core.command_registry.registry import registry

    registry()
    project_model_tools()


def test_only_explicitly_offerable_commands_are_contracted():
    from core.capability_graph import ensure_registry_bootstrap
    from core.command_registry.registry import registry
    from core.tool_registry import registered_tools

    # The operator.command contracts seed through the production bootstrap, which a sibling
    # pack's registry-reset fixture may have cleared in this process; read what dispatch
    # would see, not a stale pre-bootstrap registry.
    ensure_registry_bootstrap()
    offerable = {s.command_id for s in registry().commands() if s.model_offerable}
    contracted = {
        str(c.intent).removeprefix("operator.command.")
        for c in registered_tools()
        if str(c.intent).startswith("operator.command.")
    }
    assert contracted == offerable
    # every offered command is read-only in this milestone
    for spec in registry().commands():
        if spec.model_offerable:
            assert spec.effects == "read_only", spec.command_id


def test_operator_only_commands_never_offered():
    from core.tool_registry import registered_tools

    intents = [str(c.intent) for c in registered_tools() if str(c.intent).startswith("operator.command.")]
    for forbidden in ("blackbox.rollback", "council.stop", "settings.prefs.set", "models.pin", "update.apply", "approvals.resolve"):
        assert f"operator.command.{forbidden}" not in intents


def test_model_execution_matches_the_one_seam():
    from core.command_registry.execute import ExecutionContext, execute_command
    from core.command_registry.model_tools import execute_model_command

    via_model = execute_model_command("operator.command.blackbox.status", {})
    via_core = execute_command("blackbox.status", context=ExecutionContext(projection="core"))
    assert via_model.ok is True
    assert via_core.ok is True
    # same data truth through the model lane and the core seam
    assert via_model.details["envelope"]["data"]["store"] == via_core.data["store"]


def test_sabotage_flipping_operator_only_to_offerable_still_refuses(monkeypatch):
    """Even if a spec is (sabotaged to be) model_offerable, an OperatorAuthority
    command must refuse execution through the model lane — the gate is at
    dispatch, not at offering."""
    import dataclasses
    import sys

    from core.command_registry.model_tools import execute_model_command

    reg_module = sys.modules["core.command_registry.registry"]
    import core.command_registry.groups.blackbox_group as bb

    original = bb.register

    def sabotaged(reg):
        original(reg)
        reg._commands["blackbox.rollback"] = dataclasses.replace(
            reg.lookup("blackbox.rollback"), model_offerable=True
        )

    monkeypatch.setattr(bb, "register", sabotaged)
    reg_module._REGISTRY = None
    try:
        reg_module.registry()
        result = execute_model_command("operator.command.blackbox.rollback", {"turn_id": "t", "workspace_root": "/tmp"})
        # availability may refuse first (no recorded turns in this env); with a
        # recorded turn the OperatorAuthority refuses because principal=model.
        assert result.ok is False
        assert result.status in {"unavailable", "permission_denied", "denied", "permission_required"}
    finally:
        monkeypatch.undo()
        reg_module._REGISTRY = None


def test_sabotage_offering_a_mutating_command_fails_the_contract_law():
    """The projection law itself: only read-only commands may be model-offerable
    in this milestone — a mutating offerable command fails the registry check."""
    import dataclasses
    import sys

    from core.command_registry.check import check_registry

    reg_module = sys.modules["core.command_registry.registry"]
    import core.command_registry.groups.blackbox_group as bb

    original = bb.register

    def sabotaged(reg):
        original(reg)
        reg._commands["blackbox.rollback"] = dataclasses.replace(
            reg.lookup("blackbox.rollback"), model_offerable=True
        )

    bb.register = sabotaged
    reg_module._REGISTRY = None
    try:
        report = check_registry(reg_module.registry())
        assert any(
            f.command_id == "blackbox.rollback" and "model_offerable" in f.detail
            for f in report.findings
        )
    finally:
        bb.register = original
        reg_module._REGISTRY = None
