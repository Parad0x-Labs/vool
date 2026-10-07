"""A real bug worded in plain language reaches the source challenge; the challenge, not a word list, decides
what is shown.

VOOL-only pass u3, 2026-10-07, on a paid cloud model: the model nominated the keyed orders.py bug three times
with the right lines and mechanism ("the 2 cancelled units stay reserved forever"), and the harm-vocabulary
gate refused all three before any check ran, so the user got no finding. Measured on Pack 1's 42 cases
through the real challenge on that model: letting a declared integrity/crash primary through to the
challenge showed 1/28 non-bugs and 12/14 real bugs (the vocabulary: 0/28, 0/14).
The challenge stays fail-closed: only a clean `supported` verdict is ever shown.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_runtime.audit_claim_verifier import AuditEvidence
from core.agent_runtime.stepped_audit import _finding_defect, run_stepped_audit

FIXTURE = Path(__file__).with_name("fixtures") / "multi_file_review"
SOURCES = {p.name: p.read_text() for p in FIXTURE.glob("*.py")}
MANIFEST = SimpleNamespace(provider_id="test:pinned", model_name="test-model")

# Verbatim from the pass-u3 ledger (the model's first nomination on t1_shop).
PLAIN_REAL_BUG = {
    "title": "Partial line cancellation never releases stock", "file": "orders.py", "line_start": 25, "line_end": 28,
    "cited_line_text": "    order.lines[sku] = current - qty",
    "failure_scenario": "cancel_line is called with qty=2 on a placed line of qty=5 reserved via place(); the order's "
                        "line is decremented but stock.release is only reached when the remaining count hits zero. The 2 "
                        "cancelled units stay reserved forever, permanently shrinking the available stock pool for that SKU.",
    "harm_class": "integrity",
}
# A nit dressed as a crash, in the shape of the Q4 incident.
NIT_AS_CRASH = dict(PLAIN_REAL_BUG, title="Variable name lacks descriptive clarity", harm_class="crash",
                    failure_scenario="The short local name `current` makes cancel_line harder for later readers to "
                                     "follow; behaviour today is unaffected.")


def _evidence() -> AuditEvidence:
    return AuditEvidence(inspected_paths=tuple(SOURCES), all_paths=tuple(SOURCES), sources=SOURCES,
                         workspace_root=str(FIXTURE), scoped_target="orders.py")


def test_a_plain_language_primary_goes_to_the_challenge_with_its_declared_class():
    candidate = dict(PLAIN_REAL_BUG)
    assert _finding_defect(candidate, _evidence(), "orders.py") == ""
    assert candidate["harm_class"] == "integrity" and candidate.get("claim_needs_challenge") is True


def test_a_survey_row_without_supporting_words_is_still_downgraded():
    row = dict(NIT_AS_CRASH)
    _finding_defect(row, _evidence(), "orders.py", allow_observations=True)
    assert row["harm_class"] not in {"integrity", "crash"} and not row.get("claim_needs_challenge")


class _ScriptedRouter:
    """Nomination first, then the challenge's verdict, then empty replies for anything else."""

    def __init__(self, nomination: dict, verdict: dict) -> None:
        self.replies = [json.dumps({"findings": [nomination]}), json.dumps(verdict)]
        self.requests: list[str] = []

    def _requested_model_manifest(self, _context):
        return MANIFEST

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        self.requests.append(str(getattr(request, "system_prompt", "") or "")[:60])
        text = self.replies.pop(0) if self.replies else json.dumps({"findings": []})
        return None, SimpleNamespace(output_text=text, usage={"prompt_tokens": 60, "completion_tokens": 20},
                                     provider_id=manifest.provider_id, model_name=manifest.model_name,
                                     model_call_id="call-1", response_id="resp-1"), None


def _audit(nomination: dict, verdict: dict, session: str):
    router = _ScriptedRouter(nomination, verdict)
    agent = SimpleNamespace(
        memory_router=router,
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
                                 effective_input="Audit orders.py for the single highest-risk bug. Do not modify anything.",
                                 source_context=context, session_id=session)
    details = dict(getattr(decision, "details", None) or {})
    return details, str(getattr(decision, "output_text", "") or ""), dict(details.get("stepped_audit") or {})


def test_a_plain_language_real_bug_the_challenge_supports_becomes_the_audits_finding():
    # Read-only audit: a supported finding is held as the candidate a proof would be written against
    # (`candidate_unproven`), instead of the turn ending "three nominated candidates failed".
    details, text, stepped = _audit(PLAIN_REAL_BUG, {"verdict": "supported", "reason": "release only runs at zero"},
                                    "plain-real-bug")
    assert "three nominated candidates failed" not in text, text
    assert (stepped.get("finding") or {}).get("title") == PLAIN_REAL_BUG["title"], stepped.get("blocked_reason")
    assert stepped.get("terminal_state") == "candidate_unproven", stepped.get("terminal_state")


@pytest.mark.parametrize("verdict", [{"verdict": "refuted", "reason": "no behaviour changes", "counterexample": "n/a"},
                                     {"verdict": "uncertain", "reason": "cannot tell"}])
def test_a_nit_declared_as_a_crash_is_not_shown_unless_the_challenge_supports_it(verdict):
    details, text, stepped = _audit(NIT_AS_CRASH, verdict, f"nit-{verdict['verdict']}")
    assert not stepped.get("finding"), stepped.get("finding")
    rows = list(stepped.get("screened_out") or [])
    assert any(row.get("title") == NIT_AS_CRASH["title"] for row in rows), (rows, text)
