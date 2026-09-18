"""SCALPEL fixture repair, failure class I, 2026-08-06: the final-answer contract requires the
chat report to state measured wall time and the largest context component, runtime-measured, never
asked of the model. Before this fix neither field rendered anywhere in chat.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from core.agent_runtime.stepped_audit import run_stepped_audit

TARGET = "codec.py"
CODEC = (
    "class Codec:\n"
    "    def decompress(self, blob):\n"
    "        if blob.startswith(b'RPT1'):\n"
    "            blob = blob[4:]\n"
    "        out = bytearray()\n"
    "        while blob:\n"
    "            try:\n"
    "                pid = blob[0]\n"
    "            except Exception:\n"
    "                break\n"
    "            out.extend(blob[:pid])\n"
    "            blob = blob[pid:]\n"
    "        return bytes(out)\n"
)
MANIFEST = SimpleNamespace(provider_id="test:pinned", model_name="test-model")
FINDING = (
    '{"title": "Malformed record silently truncates the output", "file": "codec.py", '
    '"line_start": 9, "line_end": 9, "cited_line_text": "                break", '
    '"failure_scenario": "A malformed trailing byte makes the bare except break the decode loop."}'
)
CHALLENGE_UNCERTAIN = '{"verdict": "uncertain", "reason": "The window alone does not settle it."}'


class _ScriptedRouter:
    def __init__(self, replies):
        self.replies = list(replies)

    def _requested_model_manifest(self, _context):
        return MANIFEST

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        text = self.replies.pop(0)
        return (
            None,
            SimpleNamespace(
                output_text=text,
                usage={"prompt_tokens": 60, "completion_tokens": 20},
                provider_id=manifest.provider_id,
                model_name=manifest.model_name,
                model_call_id="call-1",
                response_id="resp-1",
            ),
            None,
        )


def _drive():
    agent = SimpleNamespace(
        memory_router=_ScriptedRouter([FINDING, CHALLENGE_UNCERTAIN]),
        _execute_tool_intent=lambda *a, **k: SimpleNamespace(
            ok=True, handled=True, response_text="", details={}, status="executed"
        ),
        hive_activity_tracker=None,
        public_hive_bridge=None,
    )
    context = {
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {
            "all_paths": (TARGET,),
            "inspected_paths": (TARGET,),
            "sources": {TARGET: CODEC},
            "workspace_root": "/tmp/telemetry-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }
    return run_stepped_audit(
        agent,
        task=SimpleNamespace(task_id="task-telemetry"),
        effective_input="Audit " + TARGET + " for the single highest-risk bug. Do not modify anything.",
        source_context=context,
        session_id="telemetry-1",
    )


def test_the_chat_report_states_measured_wall_time_and_largest_context() -> None:
    decision = _drive()
    report = str(decision.output_text)

    wall_time_match = re.search(r"Measured wall time: (\d+\.\d+)s", report)
    assert wall_time_match, f"no measured wall time line in report:\n{report}"
    assert float(wall_time_match.group(1)) >= 0.0

    assert re.search(r"Largest context component: (~[\d,]+ tokens|not available)", report), (
        f"no largest-context-component line in report:\n{report}"
    )

    # Runtime-measured timing lives in Activity too, and must agree with what chat states.
    stepped = dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})
    timing = stepped.get("timing") or {}
    assert abs(timing.get("total", -1) - float(wall_time_match.group(1))) < 0.5
