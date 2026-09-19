"""The capability gate: declarations must be truthful, every local-mutating builtin has one, and
a mutation-capable tool without a recorder declaration fails closed -- at registration for
plugin/MCP contracts, at dispatch for anything else."""
from __future__ import annotations

import pytest

from core.blackbox.coverage.capability import CapabilityError, MutationCapability, validated
from core.blackbox.coverage.registry import (
    capability_for,
    mutation_coverage_decision,
    uncovered_local_mutating_builtins,
)
from tests.blackbox_coverage._ctx import ctx


def _cap(**overrides) -> MutationCapability:
    base = dict(
        tool="demo.writer",
        scope="workspace",
        effect_class="reversible",
        snapshot_strategy="declared_paths",
        receipt_lifecycle="intent_then_terminal",
        rollback_support="exact",
        recorder="blackbox.coverage_declared",
    )
    base.update(overrides)
    return MutationCapability(**base)


class TestDeclarationTruth:
    def test_reversible_requires_preimages(self):
        with pytest.raises(CapabilityError):
            validated(_cap(snapshot_strategy="receipt_only"))

    def test_external_can_never_be_rollback_capable(self):
        with pytest.raises(CapabilityError):
            validated(_cap(scope="external", effect_class="reversible"))
        with pytest.raises(CapabilityError):
            validated(
                _cap(
                    scope="external",
                    effect_class="irreversible",
                    snapshot_strategy="workspace_scan",
                    receipt_lifecycle="terminal_only",
                    rollback_support="exact",
                )
            )

    def test_external_receipt_shape_is_valid(self):
        cap = validated(
            _cap(
                scope="external",
                effect_class="irreversible",
                snapshot_strategy="receipt_only",
                receipt_lifecycle="terminal_only",
                rollback_support="none",
                recorder="effect.receipt",
            )
        )
        assert cap.rollback_support == "none"

    def test_no_whole_machine_snapshots(self):
        with pytest.raises(CapabilityError):
            validated(_cap(scope="machine", snapshot_strategy="workspace_scan"))

    def test_irreversible_local_claims_no_preimages(self):
        with pytest.raises(CapabilityError):
            validated(_cap(effect_class="irreversible", rollback_support="none"))
        validated(
            _cap(
                scope="machine",
                effect_class="irreversible",
                snapshot_strategy="postimage_only",
                receipt_lifecycle="terminal_only",
                rollback_support="none",
                recorder="blackbox.coverage_post",
            )
        )

    def test_recorder_is_mandatory(self):
        with pytest.raises(CapabilityError):
            validated(_cap(recorder=""))


class TestBuiltinAudit:
    def test_every_local_mutating_builtin_declares(self):
        assert uncovered_local_mutating_builtins() == []

    def test_shell_declares_scan_reversible(self):
        decision = mutation_coverage_decision("sandbox.run_command")
        assert decision.mutation_capable and decision.covered
        assert decision.capability.snapshot_strategy == "workspace_scan"
        assert decision.capability.rollback_support == "exact"

    def test_read_only_tools_are_not_gated(self):
        decision = mutation_coverage_decision("workspace.read_file")
        assert not decision.mutation_capable and decision.covered


class TestRegistrationGate:
    def _contract(self, *, mutation, intent="demo.writer"):
        from core.runtime_tool_contracts import RuntimeToolContract

        return RuntimeToolContract(
            intent=intent,
            description="demo writer",
            tool_surface="plugin",
            capability_id="demo.writer",
            capability_claim="writes",
            supported=True,
            unsupported_reason="",
            input_schema={"path": "str"},
            output_schema={"ok": "bool"},
            side_effect_class="workspace_write",
            approval_requirement="manual",
            timeout_policy="60s",
            retry_policy="none",
            artifact_emission="none",
            error_contract="typed",
            handler="plugin",
            source="plugin:demo",
            mutation=mutation,
        )

    def test_mutating_plugin_without_declaration_is_refused(self):
        from core.tool_registry import ToolRegistrationError, register

        with pytest.raises(ToolRegistrationError, match="must declare its Blackbox coverage"):
            register(self._contract(mutation=None))

    def test_mutating_plugin_with_false_rollback_claim_is_refused(self):
        from core.tool_registry import ToolRegistrationError, register

        lie = dict(
            tool="demo.writer",
            scope="external",
            effect_class="reversible",
            snapshot_strategy="workspace_scan",
            receipt_lifecycle="intent_then_terminal",
            rollback_support="exact",
            recorder="blackbox.coverage_scan",
        )
        with pytest.raises(ToolRegistrationError, match="never be called rollback-capable"):
            register(self._contract(mutation=lie))

    def test_valid_declaration_registers_and_seats(self):
        from core.tool_registry import register

        register(
            self._contract(
                mutation=dict(
                    tool="demo.writer",
                    scope="workspace",
                    effect_class="reversible",
                    snapshot_strategy="declared_paths",
                    receipt_lifecycle="intent_then_terminal",
                    rollback_support="exact",
                    recorder="blackbox.coverage_declared",
                )
            )
        )
        assert capability_for("demo.writer") is not None

    def test_read_only_plugin_needs_no_declaration(self):
        from core.runtime_tool_contracts import RuntimeToolContract
        from core.tool_registry import register

        register(
            RuntimeToolContract(
                intent="demo.reader",
                description="demo reader",
                tool_surface="plugin",
                capability_id="demo.reader",
                capability_claim="reads",
                supported=True,
                unsupported_reason="",
                input_schema={},
                output_schema={"ok": "bool"},
                side_effect_class="read_only",
                approval_requirement="none",
                timeout_policy="60s",
                retry_policy="none",
                artifact_emission="none",
                error_contract="typed",
                handler="plugin",
                source="plugin:demo",
            )
        )
        decision = mutation_coverage_decision("demo.reader")
        assert decision.covered and not decision.mutation_capable


class TestDispatchGate:
    def test_undeclared_mutating_tool_is_refused_at_dispatch(self, workspace, store_dir):
        """The coding-assistant shape: a REGISTERED mutating contract whose declaration went
        missing (or was never seated) cannot run -- typed refusal, executed=False."""
        from core.blackbox.coverage.recorder import recorded_capability_mutation
        from core.runtime_execution_tools import RuntimeExecutionResult

        result = recorded_capability_mutation(
            "demo.undeclared_writer",
            {"path": str(workspace / "x.txt")},
            handler=lambda: RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="wrote", details={}),
            source_context=ctx(workspace),
            workspace_root=workspace,
        )
        assert result.ok is False
        assert result.status == "blackbox_coverage_required"
        assert result.details["executed"] is False
        assert (workspace / "x.txt").exists() is False


class TestExternalTruth:
    def test_external_effects_are_irreversible_with_own_receipts(self):
        decision = mutation_coverage_decision("operator.schedule_calendar_event")
        assert decision.mutation_capable and decision.covered
        cap = decision.capability
        assert cap.scope == "external"
        assert cap.effect_class == "irreversible"
        assert cap.rollback_support == "none"
        assert cap.snapshot_strategy == "receipt_only"

    def test_no_local_mutating_builtin_claims_rollback_it_cannot_have(self):
        from core.blackbox.coverage.registry import registered_capabilities

        for tool, cap in registered_capabilities().items():
            if cap.scope == "external":
                assert cap.effect_class == "irreversible" and cap.rollback_support == "none", tool
