"""Tool-required gate (Checkpoint 2): a this-machine FACT question must be tool-backed or
refused with an honest blocker -- never answered with a fabricated value.

Live incidents this pins: "820 GB free" on a ~232 GB drive, invented "Documents/Programs"
as the largest folders with fake GB, and "what is my screen resolution?" answered with CPU
specs. The classifier decides *is this tool-required*; the runtime gate refuses to let the
model answer a tool-required local fact when no tool ran.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apps.vool_agent import VoolAgent
from core.execution.constants import local_fact_capability_required
from core.execution.planner import _extract_machine_specs_request
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    reset_runtime_continuity_state,
)
from storage.migrations import run_migrations


class TestLocalFactClassifier:
    def test_machine_fact_questions_are_tool_required(self) -> None:
        assert local_fact_capability_required("what is my screen resolution?") == "the display"
        assert local_fact_capability_required("what are the biggest 5 folders on C drive?") == "the largest files or folders"
        assert local_fact_capability_required("how much free space is on my drive?") == "your drives"
        assert local_fact_capability_required("how much storage is left") == "your drives"
        assert local_fact_capability_required("what processes are running on this pc?") == "running processes"
        assert local_fact_capability_required("how much RAM does my machine have?") == "this machine's hardware"

    def test_general_knowledge_is_not_gated(self) -> None:
        # No this-machine framing -> a definitional/general question, answerable by the model.
        assert local_fact_capability_required("what is a hard drive?") is None
        assert local_fact_capability_required("what is the resolution of a 4k monitor?") is None
        assert local_fact_capability_required("how does disk defragmentation work?") is None

    def test_advisory_is_not_gated(self) -> None:
        assert local_fact_capability_required("how should I organise my folders?") is None
        assert local_fact_capability_required("what should I do about my disk being full?") is None

    def test_mutations_are_not_gated_here(self) -> None:
        # Mutations go to the operator action lane, not the read gate.
        assert local_fact_capability_required("delete my temp folder") is None
        assert local_fact_capability_required("free up space on my drive") is None

    def test_non_questions_and_empty(self) -> None:
        assert local_fact_capability_required("") is None
        assert local_fact_capability_required("my screen is nice") is None


class TestSpecsNoLongerClaimsDisplay:
    def test_display_questions_do_not_route_to_specs(self) -> None:
        assert _extract_machine_specs_request("what is my screen resolution?") is None
        assert _extract_machine_specs_request("what is my display resolution?") is None
        assert _extract_machine_specs_request("what monitor do i have?") is None

    def test_real_specs_questions_still_route_to_specs(self) -> None:
        assert _extract_machine_specs_request("what cpu does this machine have?") == {}
        assert _extract_machine_specs_request("how much ram do i have?") == {}
        assert _extract_machine_specs_request("tell me my gpu") == {}
        assert _extract_machine_specs_request("what are this pc's specs?") == {}


class TestGateBlocksFabricationEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db = Path(self._tmp.name) / "gate.db"
        run_migrations(db_path=db)
        configure_runtime_continuity_db_path(str(db))
        reset_runtime_continuity_state()
        self.agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        self.agent.start()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _run(self, text: str) -> dict:
        return self.agent.run_once(
            text,
            session_id_override="openclaw:gate-e2e",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    def test_screen_resolution_returns_display_answer_not_cpu_specs(self) -> None:
        import re

        result = self._run("what is my screen resolution?")
        resp = str(result.get("response") or "")
        # Never a CPU/OS spec dump (the original bug).
        self.assertNotIn("Chip:", resp)
        self.assertNotIn("Machine specs for this host", resp)
        # Either a real resolution (Windows, display tool ran) or an honest platform/blocker
        # message (non-Windows CI) -- never a fabricated value.
        got_resolution = bool(re.search(r"\d{3,4}\s*x\s*\d{3,4}", resp))
        honest = any(marker in resp for marker in ("only available on Windows", "couldn't read the display", "won't guess"))
        self.assertTrue(got_resolution or honest, resp[:200])

    def test_largest_folders_returns_measured_scan_not_fabricated_list(self) -> None:
        # find_largest is wired now: a real measured scan, not a fabricated list. Patch the
        # measurement so the test is fast + deterministic (no real 20s C:\ walk).
        import core.machine_diagnostics as md

        md_largest = lambda root, **kw: {  # noqa: E731
            "root": root,
            "complete": True,
            "entries": [
                {"name": "pagefile.sys", "path": root + "pagefile.sys", "size_bytes": 8 * (1024 ** 3), "size_gb": 8.0, "kind": "file", "complete": True, "delete_safety": "system-managed (do not delete)"},
                {"name": "Projects", "path": root + "Projects", "size_bytes": 3 * (1024 ** 3), "size_gb": 3.0, "kind": "folder", "complete": True, "delete_safety": "review before deleting"},
            ],
        }
        with mock.patch.object(md, "largest_entries", md_largest), mock.patch(
            "core.runtime_execution_tools._is_windows_platform", return_value=True
        ):
            result = self._run("what are the biggest 5 folders on C drive?")
        resp = str(result.get("response") or "")
        # Grounded measured output with a delete-safety class -- never a fabricated guess. A
        # "biggest FOLDERS" query lists folders only: pagefile.sys (a file) is filtered out.
        self.assertIn("Largest folders on", resp)
        self.assertIn("Projects", resp)
        self.assertIn("3.0 GB", resp)
        self.assertIn("review before deleting", resp)
        self.assertNotIn("pagefile.sys", resp)
        self.assertIn("won't delete anything without your explicit go-ahead", resp)

    def test_nounless_storage_question_refuses_before_model_when_tool_does_not_run(self) -> None:
        with (
            mock.patch("core.agent_runtime.fast_paths_machine.machine_diagnostics_intent", return_value=None),
            mock.patch.object(self.agent, "_maybe_execute_model_tool_intent", return_value=None),
            mock.patch.object(
                self.agent,
                "_model_routing_profile",
                side_effect=AssertionError("local machine fact reached model routing"),
            ),
        ):
            result = self._run("how much storage is left")
        response = str(result.get("response") or "")
        self.assertIn("won't guess", response)
        self.assertEqual(result.get("mode"), "tool_failed")
