"""The CP1 command-budget gap — the registry's ONE execution seam now owes its `command` unit.

`execute_command` is the single dispatch path every projection shares (model tool lane, operator
HTTP, legacy HTTP, CLI). These proofs drive it through those projections directly and assert the
authorities' own stores: the budget DB's reservation rows and the handler's call records. A
`budget_refused` fault envelope means the handler never ran.
"""
from __future__ import annotations

import uuid

import pytest

from core.command_registry.envelope import ExitCodes
from core.command_registry.execute import ExecutionContext, execute_command
from core.command_registry.registry import CommandRegistry
from core.command_registry.spec import (
    AuthorityDecision,
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    OperatorAuthority,
)

_CALLS: list[str] = []


def _probe_ok(_input) -> tuple[bool, str]:
    return True, ""


def _handle_write(_input, _ctx):
    _CALLS.append("budgetproof.write")
    from core.command_registry.spec import HandlerOk

    return HandlerOk(summary="wrote", receipts=({"kind": "write"},))


def _handle_read(_input, _ctx):
    _CALLS.append("budgetproof.read")
    from core.command_registry.spec import HandlerOk

    return HandlerOk(summary="read", data={"ok": True})


def _handle_boom(_input, _ctx):
    _CALLS.append("budgetproof.boom")
    raise RuntimeError("handler exploded mid-command")


def _gate_operator_only(_input, ctx):
    if getattr(ctx, "principal", "") == "model":
        return AuthorityDecision(granted=False, reason="operator-only command")
    return AuthorityDecision(granted=True, reason="")


def _scratch_registry() -> CommandRegistry:
    reg = CommandRegistry()
    reg.add_group(GroupSpec(group_id="budgetproof", description="the budget door's own probe group"))
    reg.add(
        CommandSpec(
            command_id="budgetproof.read",
            group="budgetproof",
            description="a read-only probe command",
            handler=Handler("tests.plugin_mcp_budgets.test_command_registry_door:_handle_read"),
            availability=Availability("tests.plugin_mcp_budgets.test_command_registry_door:_probe_ok"),
        )
    )
    reg.add(
        CommandSpec(
            command_id="budgetproof.write",
            group="budgetproof",
            description="a mutating probe command",
            effects="mutating",
            handler=Handler("tests.plugin_mcp_budgets.test_command_registry_door:_handle_write"),
            availability=Availability("tests.plugin_mcp_budgets.test_command_registry_door:_probe_ok"),
        )
    )
    reg.add(
        CommandSpec(
            command_id="budgetproof.operator",
            group="budgetproof",
            description="a mutating command behind the operator authority",
            effects="mutating",
            permission=OperatorAuthority(
                kind="budgetproof.operator",
                verifier="tests.plugin_mcp_budgets.test_command_registry_door:_gate_operator_only",
            ),
            handler=Handler("tests.plugin_mcp_budgets.test_command_registry_door:_handle_write"),
            availability=Availability("tests.plugin_mcp_budgets.test_command_registry_door:_probe_ok"),
        )
    )
    reg.add(
        CommandSpec(
            command_id="budgetproof.boom",
            group="budgetproof",
            description="a mutating command whose handler raises",
            effects="mutating",
            handler=Handler("tests.plugin_mcp_budgets.test_command_registry_door:_handle_boom"),
            availability=Availability("tests.plugin_mcp_budgets.test_command_registry_door:_probe_ok"),
        )
    )
    return reg


@pytest.fixture(autouse=True)
def _clean_calls():
    _CALLS.clear()
    yield
    _CALLS.clear()


def _api_ctx(session: str = "") -> ExecutionContext:
    return ExecutionContext(
        projection="api",
        principal="operator",
        extra={"session_id": session, "workspace_root": "/budgetproof-workspace"},
    )


class TestCommandBudgetDoor:
    def test_exhausted_command_budget_refuses_the_second_run_and_the_handler_never_reruns(
        self, set_budget
    ):
        from core import effect_budget as eb

        set_budget(("command", eb.SCOPE_SESSION, 1))
        session = f"operator:{uuid.uuid4().hex[:12]}"
        reg = _scratch_registry()

        first = execute_command("budgetproof.write", {}, reg=reg, context=_api_ctx(session))
        second = execute_command("budgetproof.write", {}, reg=reg, context=_api_ctx(session))

        assert first.ok is True, first.summary
        assert second.ok is False
        assert second.fault is not None and second.fault.code == "budget_refused"
        assert second.fault.detail["code"] == eb.REFUSAL_BUDGET_EXCEEDED
        assert second.execution.exit_code == ExitCodes.BUDGET_REFUSED
        assert _CALLS == ["budgetproof.write"], "zero handler calls on refusal"
        rows = [row for row in eb.reservation_rows() if row["owner_ref"] == "command_registry.execute_door"]
        assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_CONSUMED

    def test_identity_scoped_direct_leg_counts_per_session(self, set_budget):
        from core import effect_budget as eb

        set_budget(("command", eb.SCOPE_SESSION, 1))
        session_a = f"operator:{uuid.uuid4().hex[:12]}"
        session_b = f"operator:{uuid.uuid4().hex[:12]}"
        reg = _scratch_registry()

        first = execute_command("budgetproof.write", {}, reg=reg, context=_api_ctx(session_a))
        from_other_session = execute_command("budgetproof.write", {}, reg=reg, context=_api_ctx(session_b))
        refused = execute_command("budgetproof.write", {}, reg=reg, context=_api_ctx(session_b))

        assert first.ok and from_other_session.ok, (first.summary, from_other_session.summary)
        assert refused.fault.code == "budget_refused"
        assert _CALLS == ["budgetproof.write", "budgetproof.write"], "the other session ran its own unit"
        rows = [row for row in eb.reservation_rows() if row["owner_ref"] == "command_registry.execute_door"]
        assert len(rows) == 2 and all(row["state"] == eb.RESERVATION_CONSUMED for row in rows)

    def test_read_only_command_never_consumes_the_command_budget(self, set_budget):
        from core import effect_budget as eb

        set_budget(("command", eb.SCOPE_SESSION, 1))
        reg = _scratch_registry()
        for _ in range(3):
            result = execute_command("budgetproof.read", {}, reg=reg, context=_api_ctx())
            assert result.ok, result.summary
        assert eb.reservation_rows() == [], "a read_only command reserves nothing"

    def test_operator_authority_refusal_precedes_the_budget_and_spends_nothing(self, set_budget):
        from core import effect_budget as eb

        set_budget(("command", eb.SCOPE_SESSION, 1))
        reg = _scratch_registry()
        model_ctx = ExecutionContext(projection="model", principal="model", extra={"session_id": "m1"})
        result = execute_command("budgetproof.operator", {}, reg=reg, context=model_ctx)
        assert result.ok is False
        assert result.fault.code in {"permission_denied", "permission_required"}
        assert eb.reservation_rows() == [], "an unauthorized attempt is never charged"

    def test_crashed_handler_stays_charged(self, set_budget):
        from core import effect_budget as eb

        set_budget(("command", eb.SCOPE_SESSION, 2))
        session = f"operator:{uuid.uuid4().hex[:12]}"
        reg = _scratch_registry()
        crashed = execute_command("budgetproof.boom", {}, reg=reg, context=_api_ctx(session))
        after = execute_command("budgetproof.write", {}, reg=reg, context=_api_ctx(session))
        assert crashed.ok is False and crashed.fault.code == "internal"
        assert "exploded" in crashed.summary
        rows = [row for row in eb.reservation_rows() if row["owner_ref"] == "command_registry.execute_door"]
        assert len(rows) == 2 and all(row["state"] == eb.RESERVATION_CONSUMED for row in rows), (
            "the crashed command's unit is spent, never refunded"
        )
        assert after.ok is True, "the second unit was still available for the next command"
        assert _CALLS == ["budgetproof.boom", "budgetproof.write"]

    def test_exhaustion_holds_across_a_process_restart(self, set_budget):
        from core import effect_budget as eb

        set_budget(("command", eb.SCOPE_SESSION, 1))
        session = f"operator:{uuid.uuid4().hex[:12]}"
        reg = _scratch_registry()
        first = execute_command("budgetproof.write", {}, reg=reg, context=_api_ctx(session))

        eb.reset_effect_budget_process_state()  # the "restart": instance state dies, the store holds

        second = execute_command("budgetproof.write", {}, reg=reg, context=_api_ctx(session))
        assert first.ok
        assert second.fault.code == "budget_refused", "budgets hold across restart"
        assert _CALLS == ["budgetproof.write"]

    def test_the_model_lane_executes_through_the_same_budgeted_seam(self, set_budget):
        from core import effect_budget as eb

        set_budget(("command", eb.SCOPE_SESSION, 1))
        session = f"openclaw:{uuid.uuid4().hex[:20]}"
        reg = _scratch_registry()

        from core.command_registry import execute as execute_module
        from core.command_registry.model_tools import execute_model_command

        original = execute_module.registry_default
        execute_module.registry_default = lambda: reg
        try:
            first = execute_model_command(
                "operator.command.budgetproof.write", {}, task_id="t1", session_id=session
            )
            second = execute_model_command(
                "operator.command.budgetproof.write", {}, task_id="t1", session_id=session
            )
        finally:
            execute_module.registry_default = original

        assert first.ok, first.response_text
        assert second.ok is False and second.status == "budget_refused"
        assert second.details["envelope"]["fault"]["code"] == "budget_refused"
        assert _CALLS == ["budgetproof.write"]
        rows = [row for row in eb.reservation_rows() if row["owner_ref"] == "command_registry.execute_door"]
        assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_CONSUMED

    def test_the_unbudgeted_world_stays_an_honest_pass(self):
        reg = _scratch_registry()
        for _ in range(3):
            result = execute_command("budgetproof.write", {}, reg=reg, context=_api_ctx())
            assert result.ok, result.summary
        from core import effect_budget as eb

        assert eb.reservation_rows() == [], "no operator rule: no rows, no events, no refusal"
