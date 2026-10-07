"""A read-only review lists the findings that passed the source check, clearly labelled unproven.

sls, 2026-10-07 ("Show in v0.7"): a read-only review used to print "No verified bug found" while it held
findings that had survived the adversarial source challenge, because only a proof run (which a read-only
turn may not do) could promote them. Now those findings are listed, each labelled unproven, with "prove it"
as the way to check them. Nothing else of the zero-confirmed contract changes: no suggested fixes, no
unreviewed survey rows, no strengths, and a candidate the challenge did not support still stays out.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.agent_runtime.stepped_audit import run_stepped_audit

FIXTURE = Path(__file__).with_name("fixtures") / "multi_file_review"
SOURCES = {p.name: p.read_text() for p in FIXTURE.glob("*.py")}
MANIFEST = SimpleNamespace(provider_id="test:pinned", model_name="test-model")
REAL = {
    "title": "Partial line cancellation never releases stock", "file": "orders.py", "line_start": 25, "line_end": 28,
    "cited_line_text": "    order.lines[sku] = current - qty",
    "failure_scenario": "cancel_line with qty=2 on a line of 5 decrements the line but stock.release only runs when "
                        "it reaches zero, so the 2 cancelled units stay reserved forever.",
    "harm_class": "integrity", "suggested_fix": "Release the cancelled quantity on every partial cancel.",
}
NIT = dict(REAL, title="Variable name lacks descriptive clarity", harm_class="crash",
           failure_scenario="The short local name `current` makes cancel_line harder to follow; behaviour is unaffected.")


class _Router:
    def __init__(self, replies):
        self.replies = list(replies)

    def _requested_model_manifest(self, _context):
        return MANIFEST

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        text = self.replies.pop(0) if self.replies else json.dumps({"findings": []})
        return None, SimpleNamespace(output_text=text, usage={"prompt_tokens": 60, "completion_tokens": 20},
                                     provider_id=manifest.provider_id, model_name=manifest.model_name,
                                     model_call_id="call-1", response_id="resp-1"), None


def _review(replies, session):
    agent = SimpleNamespace(
        memory_router=_Router(replies),
        _execute_tool_intent=lambda *a, **k: SimpleNamespace(ok=True, handled=True, response_text="", details={},
                                                             status="executed"),
        hive_activity_tracker=None, public_hive_bridge=None,
    )
    context = {
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {"all_paths": tuple(SOURCES), "inspected_paths": tuple(SOURCES), "sources": SOURCES,
                                     "workspace_root": f"/tmp/{session}", "incomplete_files": ()},
        "requested_model": MANIFEST.model_name,
    }
    decision = run_stepped_audit(agent, task=SimpleNamespace(task_id=f"task-{session}"),
                                 effective_input="Review orders.py for the single highest-risk bug. Do not modify anything.",
                                 source_context=context, session_id=session)
    return str(getattr(decision, "output_text", "") or "")


def test_a_supported_finding_is_listed_and_labelled_unproven():
    text = _review([json.dumps({"findings": [REAL]}), json.dumps({"verdict": "supported", "reason": "zero-only release"})],
                   "n2-supported")
    assert REAL["title"] in text, text
    assert "orders.py:25" in text and "unproven" in text.lower() and "prove it" in text.lower(), text
    assert "No verified bug found" not in text, text
    assert "- Challenged: 1" in text and "- Confirmed: 0" in text, text
    assert "reproduced" not in text.lower() and "Release the cancelled quantity" not in text, text


def test_a_nit_the_challenge_refuted_stays_out():
    text = _review([json.dumps({"findings": [NIT]}),
                    json.dumps({"verdict": "refuted", "reason": "behaviour is unaffected", "counterexample": "n/a"})],
                   "n2-nit")
    assert NIT["title"] not in text.split("_Candidate-by-candidate")[0], text
