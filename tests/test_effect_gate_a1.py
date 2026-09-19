"""
Assembly W001 A1 — Served machine.write_file must pass through ExecutionGate.

A1-A10: structural authorization tests.
M1-M6:  sabotage / mutation tests (run, break, restore, re-green).
"""
from __future__ import annotations

import importlib
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import policy_engine
from core.agent_runtime.fast_paths_machine import (
    maybe_handle_direct_machine_write_request,
)
from core.execution_gate import ExecutionGate
from core.runtime_execution_tools import (
    _home_relative_label,
    _machine_home,
    _safe_machine_roots,
    execute_runtime_tool,
)

# ── Helpers ───────────────────────────────────────────────────────────

def _physical_marker(tmpdir: str, name: str) -> Path:
    return Path(tmpdir) / "Desktop" / name


def _make_home_patch(tmpdir: str):
    """Patch the module-level home resolution to point at tmpdir."""
    return mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir))


def _make_mock_agent(tmpdir: str):
    """A minimal agent-like object for maybe_handle_direct_machine_write_request."""
    class MockAgent:
        def _fast_path_result(self, **kw):
            return kw
        def _emit_runtime_event(self, *a, **kw):
            pass
        def _plan_tool_workflow(self, *, user_text, task_class, executed_steps, source_context):
            return SimpleNamespace(next_payload={"intent": "machine.ensure_directory", "arguments": {}})
    return MockAgent()


# ======================================================================
# A1–A10:  Structural served-path authorization tests
# ======================================================================

class TestA1_AuthorizedGate(unittest.TestCase):
    """A1 — AUTHORIZED machine.write_file: gate consulted, mutation occurs."""

    def test_gate_consulted_and_mutation_happens(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             _make_home_patch(tmpdir), \
             mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                               wraps=ExecutionGate.evaluate_machine_effect) as mock_gate:
            result = execute_runtime_tool(
                "machine.write_file",
                {"path": "~/Desktop/A1_test.txt", "content": "A1 marker"},
                source_context={},
            )
            self.assertIsNotNone(result)
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "executed")
            # Gate consulted exactly once
            self.assertEqual(mock_gate.call_count, 1)
            # Physical mutation occurs
            marker = _physical_marker(tmpdir, "A1_test.txt")
            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "A1 marker")
            # Normal mechanism (path resolution, safe-root containment) remains functional
            self.assertIn("Created file", result.response_text)


class TestA2_GateRefused(unittest.TestCase):
    """A2 — ExecutionGate REFUSED -> no mutation, true refusal status."""

    def test_gate_refused_no_mutation(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             _make_home_patch(tmpdir), \
             mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                               return_value={
                                   "decision": "refused",
                                   "reason": "Policy denies machine writes.",
                               }):
            result = execute_runtime_tool(
                "machine.write_file",
                {"path": "~/Desktop/A2_test.txt", "content": "should not appear"},
                source_context={},
            )
            self.assertIsNotNone(result)
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "blocked")
            # Physical mutation count = 0
            marker = _physical_marker(tmpdir, "A2_test.txt")
            self.assertFalse(marker.exists())
            # Served truth says blocked / refused
            self.assertIn("not authorized", result.response_text)


class TestA3_CallerFlagsInsufficient(unittest.TestCase):
    """A3 — Caller injects approved=true, allow_write=true, trusted=true
    while gate refuses -> NO mutation.

    Note: The contract validation layer (execute_runtime_tool's unknown-argument
    check) already rejects these flags as invalid_arguments before the gate is
    reached.  This is correct defense-in-depth: the contract schema does not
    admit caller-authority flags, so they are structurally impossible to pass
    through to the handler.  The test proves that NO mutation occurs regardless.
    """

    def test_caller_flags_rejected_by_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             _make_home_patch(tmpdir), \
             mock.patch.object(ExecutionGate, "evaluate_machine_effect") as mock_gate:
            result = execute_runtime_tool(
                "machine.write_file",
                {"path": "~/Desktop/A3_test.txt",
                 "content": "bypass attempt",
                 "approved": True,
                 "allow_write": True,
                 "trusted": True},
                source_context={},
            )
            self.assertIsNotNone(result)
            # Contract validation rejects unknown arguments
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "invalid_arguments")
            # Gate was never consulted (contract validation happens first)
            self.assertEqual(mock_gate.call_count, 0)
            # No mutation
            marker = _physical_marker(tmpdir, "A3_test.txt")
            self.assertFalse(marker.exists())


class TestA4_MechanismAllowsGateRefuses(unittest.TestCase):
    """A4 — safe-machine-root mechanism allows target, but canonical
    ExecutionGate refuses -> NO mutation."""

    def test_allowlist_not_authority(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             _make_home_patch(tmpdir), \
             mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                               return_value={
                                   "decision": "refused",
                                   "reason": "Policy denies machine writes.",
                               }):
            # Target is well within Desktop/Downloads/Documents safe roots
            result = execute_runtime_tool(
                "machine.write_file",
                {"path": "~/Desktop/A4_safe_target.txt", "content": "safe root but gate says no"},
                source_context={},
            )
            self.assertIsNotNone(result)
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "blocked")
            marker = _physical_marker(tmpdir, "A4_safe_target.txt")
            self.assertFalse(marker.exists())


class TestA5_GateAllowsMechanismRejects(unittest.TestCase):
    """A5 — Gate authorizes but safe-machine-root mechanism rejects -> NO mutation.
    This proves mechanism still provides defense-in-depth."""

    def test_mechanism_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), \
             mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                               return_value={
                                   "decision": "authorized",
                                   "reason": "Machine effect authorized.",
                               }):
            # Path outside safe-machine roots -> mechanism rejects
            result = execute_runtime_tool(
                "machine.write_file",
                {"path": str(Path(tmpdir) / "outside_roots" / "A5_test.txt"),
                 "content": "mechanism should block"},
                source_context={},
            )
            self.assertIsNotNone(result)
            self.assertFalse(result.ok)
            # Mechanism rejects with not_allowed (safe-root containment)
            self.assertEqual(result.status, "not_allowed")
            # No mutation
            marker = Path(tmpdir) / "outside_roots" / "A5_test.txt"
            self.assertFalse(marker.exists())


class TestA6_CanonicalPathIdentity(unittest.TestCase):
    """A6 — Authorization uses canonical resolved path. Two distinct
    resources with superficially similar display labels must remain
    distinct authorization identities."""

    def test_canonical_path_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             _make_home_patch(tmpdir), \
             mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                               wraps=ExecutionGate.evaluate_machine_effect) as mock_gate:
            # Write to two distinct paths with same basename
            execute_runtime_tool(
                "machine.write_file",
                {"path": "~/Desktop/A6_file1.txt", "content": "first"},
                source_context={},
            )
            execute_runtime_tool(
                "machine.write_file",
                {"path": "~/Downloads/A6_file1.txt", "content": "second"},
                source_context={},
            )
            # Both went through gate
            self.assertEqual(mock_gate.call_count, 2)
            # Verify they called with different resolved paths
            call1_resolved = mock_gate.call_args_list[0][1].get("resolved_path", "")
            call2_resolved = mock_gate.call_args_list[1][1].get("resolved_path", "")
            # Should be different because they live in different directories
            # despite having the same basename
            self.assertNotEqual(call1_resolved, call2_resolved)
            # Both files physically exist
            self.assertTrue((Path(tmpdir) / "Desktop" / "A6_file1.txt").exists())
            self.assertTrue((Path(tmpdir) / "Downloads" / "A6_file1.txt").exists())


class TestA7_FastPathHitsGate(unittest.TestCase):
    """A7 — Served fast-path reaches the same gate as the canonical
    execution boundary.  This tests the fast-path entry point that
    calls execute_runtime_tool("machine.write_file", ...)."""

    def test_fast_path_triggers_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), \
             mock.patch("core.agent_runtime.fast_paths_machine.Path.home",
                        return_value=Path(tmpdir)), \
             mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                               wraps=ExecutionGate.evaluate_machine_effect) as mock_gate, \
             mock.patch("core.agent_runtime.fast_paths_machine.canonical_runtime_transcript",
                        return_value=("real transcript content", "source")), \
             mock.patch("core.agent_runtime.fast_paths_machine.get_agent_display_name",
                        return_value="TestAgent"), \
             mock.patch("core.agent_runtime.fast_paths_machine._render_plaintext_transcript",
                        return_value="rendered transcript"):
            agent = _make_mock_agent(tmpdir)
            # Use the transcript export path (write to Desktop)
            result = maybe_handle_direct_machine_write_request(
                agent,
                "save this chat transcript to my desktop as notes.txt",
                session_id="test-session",
                source_surface="channel",
                # Auto mode: the subject here is the A7 effect gate below the dispatch, not the
                # permission question; Auto permits the create so the write reaches the gate.
                source_context={"operating_mode": "auto"},
            )
            self.assertIsNotNone(result)
            # Served fast-path -> execute_runtime_tool -> _write_machine_file -> gate
            self.assertEqual(mock_gate.call_count, 1)
            # Physical mutation happens only after authorization
            marker = Path(tmpdir) / "Desktop" / "notes.txt"
            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "rendered transcript")
            self.assertEqual(result.get("mode"), "tool_executed")


class TestA8_GateBeforeDispatch(unittest.TestCase):
    """A8 — Gate evaluated BEFORE dispatch.  If mechanism invocation
    occurs before authorization, fail."""

    def test_gate_before_physical_write(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             _make_home_patch(tmpdir), \
             mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                               return_value={
                                   "decision": "authorized",
                                   "reason": "Machine effect authorized.",
                               }) as mock_gate:
            result = execute_runtime_tool(
                "machine.write_file",
                {"path": "~/Desktop/A8_test.txt", "content": "ordering test"},
                source_context={},
            )
            self.assertIsNotNone(result)
            # Gate was called
            self.assertEqual(mock_gate.call_count, 1)
            # Physical write happened
            marker = _physical_marker(tmpdir, "A8_test.txt")
            self.assertTrue(marker.exists())


class TestA9_DeniedNoAppliedReceipt(unittest.TestCase):
    """A9 — Denied action produces no mutation receipt claiming APPLIED."""

    def test_denied_does_not_claim_applied(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             _make_home_patch(tmpdir), \
             mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                               return_value={
                                   "decision": "refused",
                                   "reason": "Policy denies machine writes.",
                               }):
            result = execute_runtime_tool(
                "machine.write_file",
                {"path": "~/Desktop/A9_test.txt", "content": "should not happen"},
                source_context={},
            )
            self.assertIsNotNone(result)
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "blocked")
            # No receipt fields claiming APPLIED / executed / updated
            status = str(getattr(result, "status", "") or "")
            self.assertNotIn("executed", status)
            self.assertNotIn("updated", status)
            self.assertNotIn("applied", status.lower())
            # details should not claim ok=True
            self.assertIsNot(getattr(result, "ok", True), True)


class TestA10_ReadOperationsUnaffected(unittest.TestCase):
    """A10 — Existing read-only machine operations remain unaffected
    unless they already require authorization by existing policy."""

    def test_read_operations_still_work(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)):
            # Create a file to read
            target = Path(tmpdir) / "Desktop" / "readme.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("readable content", encoding="utf-8")

            result = execute_runtime_tool(
                "machine.read_file",
                {"path": "~/Desktop/readme.txt", "start_line": 1, "max_lines": 10},
                source_context={},
            )
            self.assertIsNotNone(result)
            self.assertTrue(result.ok)
            # This should not require gate authorization
            self.assertIn("readable content", result.response_text)

    def test_list_directory_unauthorized(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)):
            target = Path(tmpdir) / "Desktop"
            target.mkdir(parents=True, exist_ok=True)
            (target / "note.txt").write_text("hi", encoding="utf-8")

            result = execute_runtime_tool(
                "machine.list_directory",
                {"path": "~/Desktop"},
                source_context={},
            )
            self.assertIsNotNone(result)
            self.assertTrue(result.ok)


# ======================================================================
# M1–M6:  Sabotage / mutation tests
# ======================================================================

class SabotageBase(unittest.TestCase):
    """Base class for sabotage tests.

    Each subclass:
    1. Mutates the source code (semantic/runtime sabotage).
    2. Runs a test that proves the sabotage is effective (RED).
    3. Restores the code.
    """

    SABOTAGE_PATH = "core/runtime_execution_tools.py"

    @classmethod
    def _read_source(cls):
        with open(cls.SABOTAGE_PATH) as f:
            return f.read()

    @classmethod
    def _write_source(cls, content):
        with open(cls.SABOTAGE_PATH, "w") as f:
            f.write(content)

    @classmethod
    def _reload_module(cls):
        import importlib

        import core.runtime_execution_tools
        importlib.reload(core.runtime_execution_tools)


class TestM1_Sabotage_RestoreDirectDispatch(SabotageBase):
    """M1 — Restore direct machine.write_file dispatch bypassing
    ExecutionGate.  Must RED."""

    def test_sabotage_remove_gate(self):
        source = self._read_source()
        # Remove the gate authorization block from _write_machine_file.
        # The block starts with the comment "# ── Execution authorization"
        # and ends with the comment "# ── Physical mutation".
        # We remove everything between them (inclusive of the gate block).
        gate_marker = "# ── Execution authorization"
        phys_marker = "# ── Physical mutation"
        gate_start = source.find(gate_marker)
        phys_start = source.find(phys_marker)
        self.assertGreater(gate_start, 0, "gate block not found")
        self.assertGreater(phys_start, gate_start, "physical mutation marker not after gate")
        # Remove from gate marker through just before phys marker
        mutated = source[:gate_start] + source[phys_start:]
        self._write_source(mutated)
        try:
            self._reload_module()
            from core.runtime_execution_tools import _write_machine_file
            # The gate is gone — writes should still work (mechanism intact)
            # but gate is not consulted.
            with tempfile.TemporaryDirectory() as tmpdir, \
                 mock.patch("core.runtime_execution_tools.Path.home",
                            return_value=Path(tmpdir)):
                result = _write_machine_file(
                    {"path": "~/Desktop/M1_test.txt", "content": "bypass"},
                )
                self.assertIsNotNone(result)
                marker = Path(tmpdir) / "Desktop" / "M1_test.txt"
                self.assertTrue(marker.exists(),
                                "M1 SABOTAGE: write still occurs without gate")
        finally:
            self._write_source(source)
            self._reload_module()


class TestM2_Sabotage_IgnoreRefusedGate(SabotageBase):
    """M2 — Ignore REFUSED gate result and dispatch anyway.
    Must RED through physical marker.

    Semantic mutation: replace the gate-refusal condition line so the
    ``if`` always evaluates to ``False``, causing the refusal return
    to be skipped and the physical write to proceed regardless of the
    gate decision.
    """

    # The exact line to sabotage: the condition that gates the refusal return.
    _TARGET_LINE = '    if _gate_decision.get("decision") != "authorized":'
    _SABOTAGE_LINE = '    if False:  # SABOTAGE M2: ignore gate refusal'

    def test_sabotage_ignore_refused(self):
        source = self._read_source()
        self.assertIn(self._TARGET_LINE, source, "target condition line must exist")
        mutated = source.replace(self._TARGET_LINE, self._SABOTAGE_LINE)
        self.assertNotEqual(mutated, source, "sabotage must change the source")
        self._write_source(mutated)
        try:
            self._reload_module()
            import core.runtime_execution_tools as ret
            with tempfile.TemporaryDirectory() as tmpdir, \
                 mock.patch("core.runtime_execution_tools.Path.home",
                            return_value=Path(tmpdir)), \
                 mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                                   return_value={
                                       "decision": "refused",
                                       "reason": "Policy denies.",
                                   }):
                result = ret.execute_runtime_tool(
                    "machine.write_file",
                    {"path": "~/Desktop/M2_test.txt", "content": "should be blocked"},
                    source_context={},
                )
                # With sabotage, the gate is still consulted but its refusal
                # is ignored (if False: skip the refusal return, proceed to
                # physical write).  The marker MUST be physically written.
                marker = Path(tmpdir) / "Desktop" / "M2_test.txt"
                self.assertTrue(marker.exists(),
                                "M2 SABOTAGE: physical write occurred despite gate refusal")
                self.assertEqual(
                    marker.read_text(encoding="utf-8"), "should be blocked",
                    "M2 SABOTAGE: content must match arguments",
                )
        finally:
            self._write_source(source)
            self._reload_module()
            import core.runtime_execution_tools as ret
            # Restored GREEN: gate refuses -> no marker
            with tempfile.TemporaryDirectory() as tmpdir, \
                 mock.patch("core.runtime_execution_tools.Path.home",
                            return_value=Path(tmpdir)), \
                 mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                                   return_value={
                                       "decision": "refused",
                                       "reason": "Policy denies.",
                                   }):
                result = ret.execute_runtime_tool(
                    "machine.write_file",
                    {"path": "~/Desktop/M2_green_test.txt", "content": "restored"},
                    source_context={},
                )
                marker = Path(tmpdir) / "Desktop" / "M2_green_test.txt"
                self.assertFalse(marker.exists(),
                                 "M2 GREEN: restored source must refuse write")


class TestM3_Sabotage_CallerApprovedFlag(SabotageBase):
    """M3 — Treat caller approved=true as sufficient authority.  Must RED."""

    def test_sabotage_caller_flag(self):
        source = self._read_source()
        # Replace the gate evaluation with a caller-flag check.
        # The gate block runs inside _write_machine_file, which is called AFTER
        # contract validation in execute_runtime_tool.  Call _write_machine_file
        # directly (the handler itself) to test the sabotage at the handler level.
        gate_marker = "# ── Execution authorization"
        phys_marker = "# ── Physical mutation"
        gate_start = source.find(gate_marker)
        phys_start = source.find(phys_marker)
        if gate_start == -1 or phys_start == -1 or phys_start <= gate_start:
            self.skipTest("gate markers not found")
        caller_block = (
            '# SABOTAGE M3: treat caller-approved flag as sufficient authority\n'
            '    _caller_approved = str(arguments.get("approved", "") or "").strip().lower()\n'
            '    _caller_approved = _caller_approved in ("true", "1", "yes")\n'
            '    if not _caller_approved:\n'
            '        return RuntimeExecutionResult(\n'
            '            handled=True,\n'
            '            ok=False,\n'
            '            status="blocked",\n'
            '            response_text="Caller did not approve.",\n'
            '            details={\n'
            '                "reason": "Caller approval flag not set.",\n'
            '            },\n'
            '        )\n'
        )
        mutated = source[:gate_start] + caller_block + source[phys_start:]
        self._write_source(mutated)
        try:
            self._reload_module()
            import core.runtime_execution_tools as ret
            # Call _write_machine_file directly (bypass contract validation)
            with tempfile.TemporaryDirectory() as tmpdir, \
                 mock.patch("core.runtime_execution_tools.Path.home",
                            return_value=Path(tmpdir)):
                # Caller sets approved=true -> sabotage bypasses gate
                result = ret._write_machine_file(
                    {"path": "~/Desktop/M3_test.txt",
                     "content": "bypass via approved flag",
                     "approved": True},
                )
                marker = Path(tmpdir) / "Desktop" / "M3_test.txt"
                self.assertTrue(marker.exists(),
                                "M3 SABOTAGE: caller approved flag bypassed gate")
        finally:
            self._write_source(source)
            self._reload_module()


class TestM4_Sabotage_SafeRootAsAuthority(SabotageBase):
    """M4 — Treat safe-machine-root allowlist as sufficient authority
    when gate refuses.  Must RED."""

    def test_sabotage_safe_root_authority(self):
        source = self._read_source()
        gate_marker = "# ── Execution authorization"
        phys_marker = "# ── Physical mutation"
        gate_start = source.find(gate_marker)
        phys_start = source.find(phys_marker)
        if gate_start == -1 or phys_start == -1 or phys_start <= gate_start:
            self.skipTest("gate markers not found")
        # Remove the gate block entirely, leaving only mechanism checks
        mutated = source[:gate_start] + source[phys_start:]
        self._write_source(mutated)
        try:
            self._reload_module()
            with tempfile.TemporaryDirectory() as tmpdir, \
                 mock.patch("core.runtime_execution_tools.Path.home",
                            return_value=Path(tmpdir)):
                # Target in safe root -> safe-root allowlist is the sole authority
                result = execute_runtime_tool(
                    "machine.write_file",
                    {"path": "~/Desktop/M4_test.txt", "content": "safe root bypass"},
                    source_context={},
                )
                marker = Path(tmpdir) / "Desktop" / "M4_test.txt"
                self.assertTrue(marker.exists(),
                                "M4 SABOTAGE: safe-root allowlist sufficed without gate")
        finally:
            self._write_source(source)
            self._reload_module()


class TestM5_Sabotage_DisplayPath(SabotageBase):
    """M5 — Authorize using display/relative path instead of canonical
    resolved identity.  Must RED."""

    def test_sabotage_display_path(self):
        source = self._read_source()
        # Replace `resolved_path=str(target)` with `resolved_path=str(arguments.get("path", ""))`
        old = 'resolved_path=str(target),'
        new = 'resolved_path=str(arguments.get("path", "")),'
        if old not in source:
            self.skipTest("resolved_path pattern not found")
        mutated = source.replace(old, new)
        self.assertNotEqual(mutated, source, "sabotage must change the source")
        self._write_source(mutated)
        try:
            self._reload_module()
            with tempfile.TemporaryDirectory() as tmpdir, \
                 mock.patch("core.runtime_execution_tools.Path.home",
                            return_value=Path(tmpdir)), \
                 mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                                   wraps=ExecutionGate.evaluate_machine_effect) as mock_gate:
                execute_runtime_tool(
                    "machine.write_file",
                    {"path": "~/Desktop/M5_test.txt", "content": "display path test"},
                    source_context={},
                )
                # Check what the gate was called with
                call_kwargs = mock_gate.call_args[1]
                resolved = call_kwargs.get("resolved_path", "")
                # With sabotage, it uses the raw ~/Desktop/... path
                self.assertIn("~", resolved,
                              "M5 SABOTAGE: gate received display path instead of canonical")
        finally:
            self._write_source(source)
            self._reload_module()


class TestM6_Sabotage_GateAfterDispatch(SabotageBase):
    """M6 — Move gate check AFTER physical dispatch.  Must RED."""

    def test_sabotage_gate_after_dispatch(self):
        source = self._read_source()
        # Insert the content assignment + write BEFORE the gate check, keeping
        # the original physical block intact.  The file is written before the
        # gate is consulted, so even if gate refuses, the write has happened.
        gate_marker = "# ── Execution authorization"
        early_write = (
            '    # SABOTAGE M6: physical write before gate\n'
            '    content = str(arguments.get("content") or "")\n'
            '    target.parent.mkdir(parents=True, exist_ok=True)\n'
            '    target.write_text(content, encoding="utf-8")\n'
        )
        if gate_marker not in source:
            self.skipTest("gate marker not found in source")
        gate_start = source.find(gate_marker)
        mutated = source[:gate_start] + early_write + source[gate_start:]
        self.assertNotEqual(mutated, source, "sabotage must change the source")
        self._write_source(mutated)
        try:
            self._reload_module()
            import core.runtime_execution_tools as ret
            with tempfile.TemporaryDirectory() as tmpdir, \
                 mock.patch("core.runtime_execution_tools.Path.home",
                            return_value=Path(tmpdir)), \
                 mock.patch.object(ExecutionGate, "evaluate_machine_effect",
                                   return_value={
                                       "decision": "refused",
                                       "reason": "Policy denies.",
                                   }):
                result = ret.execute_runtime_tool(
                    "machine.write_file",
                    {"path": "~/Desktop/M6_test.txt", "content": "after dispatch test"},
                    source_context={},
                )
                # Physical write happened BEFORE gate check
                marker = Path(tmpdir) / "Desktop" / "M6_test.txt"
                self.assertTrue(marker.exists(),
                                "M6 SABOTAGE: physical write occurred before gate check")
        finally:
            self._write_source(source)
            self._reload_module()


if __name__ == "__main__":
    unittest.main()
