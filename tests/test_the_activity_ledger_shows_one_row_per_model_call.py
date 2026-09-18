"""One physical model call must render as ONE running row and ONE answered row.

The router deliberately records two event shapes per lane-wrapped call: `model_lane_*` (the lane
timeline) and dotted `model.call_*` (the per-adapter call receipt consumed by turn_trace /
turn_failure_stage). Both are real, distinct records -- but the chat page's LEDGER_MAP keyed both
shapes to the identical "Model running"/"Model answered" labels, so every wrapped call rendered
twice (observed live 2026-08-14, session openclaw:ed890df3 seqs 63-66).

The fix is decided at the SOURCE: `_invoke_manifest` stamps `lane_receipted=True` onto the dotted
started/completed events exactly when its caller wraps the call in lane receipts, and the renderer
honours that per-event field -- no pairing heuristics, nothing dropped from the ledger itself.
Unwrapped calls (classifier, race, mux) carry no flag and keep rendering; `model.call_failed` is
never skipped.

These tests drive the REAL emission path (`MemoryFirstRouter._invoke_manifest` against a real
manifest registry, with only the network adapter faked) into a real on-disk ledger, then run the
REAL `ledgerRow` renderer (extracted from core/vool_chat_page.py, executed under node) over the
recorded events.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters.base_adapter import ModelRequest, ModelResponse
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    list_runtime_session_events,
    reset_runtime_continuity_state,
)

PAGE = Path("core/vool_chat_page.py")
NODE = shutil.which("node")


def _render_ledger_rows(events: list[dict]) -> list[dict]:
    """Run the REAL ledgerRow over `events` under node, returning the non-null rows."""
    src = PAGE.read_text(encoding="utf-8")
    start = src.index("const LEDGER_SKIP")
    end = src.index("function ledgerRanNoTool")
    body = src[start:end]
    script = (
        # Minimal stand-ins for page-level helpers ledgerRow reaches only on non-model rows.
        "function esc(s){return String(s);}\n"
        "function panelRow(cls, icon, title, sub){return JSON.stringify({cls, title, sub});}\n"
        "function activityCategoryFor(){return ['tools','Tool calls'];}\n"
        + body
        + f"\nconst events = {json.dumps(events)};\n"
        + "const rows = events.map(ledgerRow).filter(Boolean);\n"
        + "process.stdout.write(JSON.stringify(rows));\n"
    )
    proc = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class _RouterCase(unittest.TestCase):
    """A real router + registry with only the adapter faked, writing a real event ledger."""

    def setUp(self) -> None:
        from core.model_registry import ModelRegistry
        from storage.db import get_connection
        from storage.migrations import run_migrations

        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM model_provider_manifests")
            conn.commit()
        finally:
            conn.close()
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "ledger.db"
        run_migrations(db_path=db_path)
        configure_runtime_continuity_db_path(str(db_path))
        reset_runtime_continuity_state()
        self.registry = ModelRegistry()
        self.manifest = self.registry.register_manifest(
            {
                "provider_name": "local-qwen-http", "model_name": "qwen-local", "source_type": "http",
                "adapter_type": "local_qwen_provider", "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied", "weights_bundled": False, "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["summarize", "structured_json", "tool_intent"],
                "runtime_config": {"base_url": "http://127.0.0.1:1234"}, "enabled": True,
                "metadata": {"orchestration_role": "drone"},
            }
        )

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _invoke(self, *, session_id: str, lane_receipted: bool) -> None:
        from core.memory_first_router import MemoryFirstRouter
        from core.task_router import create_task_record

        router = MemoryFirstRouter(self.registry)
        task = create_task_record("summarize the release notes")
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"ok": true}',
            provider_id=self.manifest.provider_id,
            model_name=self.manifest.model_name,
            output_mode="tool_intent",
        )
        with mock.patch.object(self.registry, "build_adapter", return_value=adapter):
            kwargs = {"lane_receipted": True} if lane_receipted else {}
            _adapter, response, error = router._invoke_manifest(
                manifest=self.manifest,
                request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
                output_mode="tool_intent",
                task=task,
                source_context={"session_id": session_id, "_owner_local": True},
                **kwargs,
            )
        self.assertIsNone(error)
        self.assertIsNotNone(response)

    def test_a_lane_wrapped_call_renders_one_running_and_one_answered_row(self) -> None:
        from core.memory_first_router import _emit_model_routing_event

        session_id = "s-wrapped"
        source_context = {"session_id": session_id, "_owner_local": True}
        # The REAL lane receipts the sequential fallback loop emits around each attempt, through
        # the real emitter function, followed by the REAL adapter-call emission path.
        _emit_model_routing_event(
            source_context, "model_lane_started", "Using local-qwen-http.",
            provider_id=self.manifest.provider_id, model_id=self.manifest.model_name, phase="running",
        )
        self._invoke(session_id=session_id, lane_receipted=True)
        _emit_model_routing_event(
            source_context, "model_lane_completed", "local-qwen-http completed.",
            provider_id=self.manifest.provider_id, model_id=self.manifest.model_name, phase="completed",
        )

        events = list_runtime_session_events(session_id)
        started = [e for e in events if e["event_type"] == "model.call_started"]
        completed = [e for e in events if e["event_type"] == "model.call_completed"]
        self.assertEqual(len(started), 1, events)
        self.assertEqual(len(completed), 1, events)
        # The stamp is on the recorded event itself -- the source decided, not the renderer.
        self.assertIs(started[0].get("lane_receipted"), True, started[0])
        self.assertIs(completed[0].get("lane_receipted"), True, completed[0])

        if NODE is None:
            self.skipTest("node is not installed")
        rows = _render_ledger_rows(events)
        running = [r for r in rows if r["title"].startswith("Model running")]
        answered = [r for r in rows if r["title"].startswith("Model answered")]
        self.assertEqual(len(running), 1, rows)
        self.assertEqual(len(answered), 1, rows)

    def test_an_unwrapped_call_still_renders(self) -> None:
        # Negative control -- the classifier pattern: a dotted-only call with NO lane wrapper must
        # keep its rows, or classifier calls vanish from the Activity panel entirely.
        session_id = "s-classifier"
        self._invoke(session_id=session_id, lane_receipted=False)
        events = list_runtime_session_events(session_id)
        started = [e for e in events if e["event_type"] == "model.call_started"]
        completed = [e for e in events if e["event_type"] == "model.call_completed"]
        self.assertEqual(len(started), 1, events)
        self.assertEqual(len(completed), 1, events)
        self.assertNotIn("lane_receipted", started[0], started[0])
        self.assertNotIn("lane_receipted", completed[0], completed[0])

        if NODE is None:
            self.skipTest("node is not installed")
        rows = _render_ledger_rows(events)
        self.assertEqual(len([r for r in rows if r["title"].startswith("Model running")]), 1, rows)
        self.assertEqual(len([r for r in rows if r["title"].startswith("Model answered")]), 1, rows)


class FailureRowsAreNeverCollapsedTests(unittest.TestCase):
    """model.call_failed carries the failure reason (e.g. the resource governor's
    model_load_gated_low_memory sentence) -- it must render even when lane-wrapped."""

    def test_a_flagged_failure_still_renders_with_its_reason(self) -> None:
        if NODE is None:
            self.skipTest("node is not installed")
        rows = _render_ledger_rows(
            [
                {
                    "event_type": "model.call_failed",
                    "message": "Model call failed with ollama-local.",
                    "model_id": "qwen3:8b",
                    "provider_id": "ollama-local:qwen3:8b",
                    "reason": "model_load_gated_low_memory",
                    "lane_receipted": True,
                },
            ]
        )
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("Model call failed", rows[0]["title"])
        self.assertIn("model_load_gated_low_memory", rows[0]["sub"])


if __name__ == "__main__":
    unittest.main()
