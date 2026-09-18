"""SCALPEL fixture repair, failure class Activity/7, 2026-08-06: trimming the chat response must
not delete audit evidence.

The exact gap: `details.stepped_audit` (candidate ids, fingerprints, lifecycle states, challenge
verdicts/reasons, proof command/output, per-stage timing) lived only on the in-memory
`ModelExecutionDecision` object returned by `run_stepped_audit` -- once the HTTP response was sent,
none of it was recoverable. Only a thin per-call `{purpose, tokens, result}` slice reached the
durable `runtime_session_events` store via `_emit_call_ledger`.

These drive the real `run_stepped_audit` and read back the REAL durable store afterward
(`list_runtime_session_events`, the same reader `core/turn_trace.py` uses) -- not the returned
decision object, which was never in question.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.agent_runtime.stepped_audit import run_stepped_audit
from core.runtime_continuity import list_runtime_session_events

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
FINDING = json.dumps(
    {
        "title": "Malformed record silently truncates the output",
        "file": TARGET,
        "line_start": 9,
        "line_end": 9,
        "cited_line_text": "                break",
        "failure_scenario": "AKIAABCDEFGHIJKLMNOP appears here as a planted fake secret to prove "
        "redaction -- a malformed trailing byte makes the bare except break the decode loop, so "
        "decompress returns a truncated prefix instead of raising.",
    }
)
CHALLENGE_UNCERTAIN = json.dumps(
    {"verdict": "uncertain", "reason": "The window alone does not settle it either way."}
)


class _ScriptedRouter:
    def __init__(self, replies):
        self.replies = list(replies)

    def _requested_model_manifest(self, _context):
        return MANIFEST

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        if not self.replies:
            return (None, None, "script_exhausted")
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


def _drive(session_id: str):
    agent = SimpleNamespace(
        memory_router=_ScriptedRouter([FINDING, CHALLENGE_UNCERTAIN]),
        _execute_tool_intent=lambda *a, **k: SimpleNamespace(
            ok=True, handled=True, response_text="", details={}, status="executed"
        ),
        hive_activity_tracker=None,
        public_hive_bridge=None,
    )
    context = {
        # Mirrors the real production stamp -- `core/web/api/service.py:2941` sets this on every
        # real turn's `source_context` before `run_stepped_audit` is ever called; without it
        # `emit_runtime_event` has no session to key the durable write on and silently no-ops.
        "runtime_session_id": session_id,
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {
            "all_paths": (TARGET,),
            "inspected_paths": (TARGET,),
            "sources": {TARGET: CODEC},
            "workspace_root": "/tmp/durable-activity-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }
    decision = run_stepped_audit(
        agent,
        task=SimpleNamespace(task_id="task-durable"),
        effective_input="Audit " + TARGET + " for the single highest-risk bug. Do not modify anything.",
        source_context=context,
        session_id=session_id,
    )
    return decision


def test_the_full_candidate_detail_reaches_the_durable_event_store() -> None:
    session_id = "durable-activity-1"
    decision = _drive(session_id)
    in_memory = dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})
    assert in_memory.get("screened_out"), "fixture must produce at least one screened candidate"

    events = list_runtime_session_events(session_id, limit=200)
    detail_events = [e for e in events if e.get("event_type") == "audit_candidate_detail"]
    assert detail_events, "no audit_candidate_detail event reached the durable store"
    persisted = detail_events[-1]

    # The exact fields the chat response no longer carries, recoverable from the durable store --
    # not the in-memory decision object, which was never in question.
    assert persisted.get("terminal_state") == in_memory.get("terminal_state")
    persisted_screened = persisted.get("screened_out") or []
    assert persisted_screened, "screened candidates did not survive to the durable record"
    assert persisted_screened[0].get("id"), "candidate id did not survive to the durable record"
    assert persisted_screened[0].get("claim_signature"), "fingerprint did not survive to the durable record"
    assert persisted_screened[0].get("verdict") in {"refuted", "supported", "uncertain"}
    assert "reason" in persisted_screened[0]

    timing = persisted.get("timing") or {}
    for stage in ("total", "context_collection", "nomination", "challenge", "proof", "rendering"):
        assert stage in timing, f"timing missing stage {stage!r}: {timing}"
        assert isinstance(timing[stage], (int, float))
        assert timing[stage] >= 0
    assert timing["total"] > 0
    assert timing["nomination"] > 0, "a real nomination call ran and must show non-zero time"
    assert timing["proof"] == 0.0, "a read-only turn never attempted proof"


def test_a_planted_secret_in_candidate_text_is_redacted_before_it_reaches_storage() -> None:
    session_id = "durable-activity-redaction-1"
    _drive(session_id)

    events = list_runtime_session_events(session_id, limit=200)
    detail_events = [e for e in events if e.get("event_type") == "audit_candidate_detail"]
    assert detail_events
    encoded = json.dumps(detail_events[-1])

    assert "AKIAABCDEFGHIJKLMNOP" not in encoded, (
        "a planted fake secret in candidate text reached the durable store unredacted"
    )
