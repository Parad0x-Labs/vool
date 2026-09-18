"""ARGUS repair D4, 2026-08-06: strengths split out ONCE, and every candidate count -- the score
line's own "Unreviewed: N" AND the "search ended" narrative sentence describing the identical
candidates -- must derive from the same post-split collection.

Reproduces ARGUS's exact mismatch shape: a nomination batch containing a genuine unreviewed defect
alongside `harm_class == "strength"` rows (a real, valid nomination shape -- a batch may report
something done well, not only defects). Before this repair, `stepped_audit.py`'s own
`unreviewed_count` (fed into `compose_search_ended_reason`, the "N candidates remained unreviewed"
sentence) counted the RAW survey batch, strengths included -- while `_score_lines`'s "Unreviewed: N"
counted `verdict.additional_findings` AFTER `AuditVerdict.__post_init__` had already split the same
strengths out into `verdict.strengths`. One report, two different counts of the same candidates:
"Unreviewed: 1" in the score line next to "3 candidates remained unreviewed" in the narrative
sentence, for the identical 1-defect-plus-2-strengths batch.

Drives the real `run_stepped_audit` through three full candidate rounds (`_MAX_CANDIDATES == 3`),
each nominated and refuted by a real challenge call, so the search ends NATURALLY (the candidate
loop exhausts its own range) with zero failed nomination attempts -- the one shape that reaches
`compose_search_ended_reason` instead of the generic failure-sentence path.
"""
from __future__ import annotations

import json
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


def _candidate(title, line, scenario):
    return {
        "title": title,
        "file": TARGET,
        "line_start": line,
        "line_end": line,
        "cited_line_text": "            try:",
        "failure_scenario": scenario,
    }


def _strength(title, line, scenario):
    return {
        "title": title,
        "file": TARGET,
        "line_start": line,
        "line_end": line,
        "cited_line_text": "            try:",
        "failure_scenario": scenario,
        "harm_class": "strength",
    }


REFUTED = json.dumps(
    {
        "verdict": "refuted",
        "reason": "The cited claim does not match the actual control flow at that line.",
        "counterexample": "The try block at line 7 only wraps a single indexing read; it never "
        "performs byte-order conversion or length validation, so the claimed causal chain does "
        "not exist in the code as written.",
    }
)

# Round 1: a primary candidate (refuted), a genuine unreviewed sibling defect, and a strength.
ROUND_1_BATCH = json.dumps(
    {
        "findings": [
            _candidate(
                "The try block never validates blob is non-empty first", 7,
                "Calling this method with a zero-length blob returns incorrect output because the "
                "surrounding while loop's own emptiness check happens one line too late.",
            ),
            _strength(
                "The bare except is narrowly scoped", 7,
                "The exception handler wraps only the single indexing read, so it cannot "
                "accidentally mask an unrelated failure occurring elsewhere in the loop body.",
            ),
            _candidate(
                "Malformed record silently truncates the output", 9,
                "A malformed record makes the bare except break the decode loop, so decompress "
                "silently returns a truncated prefix instead of raising.",
            ),
        ]
    }
)
# Round 2: a primary candidate (refuted) and a second strength.
ROUND_2_BATCH = json.dumps(
    {
        "findings": [
            _candidate(
                "The indexing operation ignores endianness of the length prefix", 7,
                "Reading blob[0] as a plain integer returns incorrect output on big-endian input "
                "streams because no byte-order conversion is ever applied here.",
            ),
            _strength(
                "The decoder never mutates the caller's original argument object", 11,
                "Extending a fresh local bytearray rather than writing back into the caller's own "
                "buffer keeps repeated decode calls from interfering with one another's state.",
            ),
        ]
    }
)
# Round 3: a primary candidate (refuted), nothing else -- exhausts _MAX_CANDIDATES == 3.
ROUND_3_BATCH = json.dumps(
    {
        "findings": [
            _candidate(
                "The loop retries indexing after a caught exception instead of aborting", 7,
                "Continuing the outer while loop after the inner except fires returns incorrect "
                "output because the partially-consumed buffer state from the failed attempt is "
                "reused on the next iteration.",
            )
        ]
    }
)


class _ScriptedRouter:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def _requested_model_manifest(self, _context):
        return MANIFEST

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        self.requests.append(request)
        if not self.replies:
            return (None, None, "script_exhausted")
        text = self.replies.pop(0)
        return (
            None,
            SimpleNamespace(
                output_text=text, usage={"prompt_tokens": 40, "completion_tokens": 15},
                provider_id=manifest.provider_id, model_name=manifest.model_name,
                model_call_id="call-1", response_id="resp-1",
            ),
            None,
        )

    def steps(self):
        return [
            str(dict(getattr(r, "metadata", None) or {}).get("stepped_audit_step") or "")
            for r in self.requests
        ]


def _drive():
    router = _ScriptedRouter(
        [ROUND_1_BATCH, REFUTED, ROUND_2_BATCH, REFUTED, ROUND_3_BATCH, REFUTED]
    )
    agent = SimpleNamespace(
        memory_router=router,
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
            "workspace_root": "/tmp/strengths-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }
    decision = run_stepped_audit(
        agent, task=SimpleNamespace(task_id="task-strengths"),
        effective_input="Audit " + TARGET + " for the single highest-risk bug. Do not modify anything.",
        source_context=context, session_id="strengths-accounting-1",
    )
    return decision, router


def test_the_score_line_and_the_search_ended_sentence_report_the_same_unreviewed_count() -> None:
    """Three candidate rounds nominate, but `read_only_ledger()` caps the CHALLENGE step at 2 (see
    `DEFAULT_STEP_CEILINGS` in `audit_call_budget.py`) -- the third candidate's challenge is refused
    by the budget before any model call, so it becomes a `never_attempted` `screened_rows` item
    instead of a third real challenge. The final accounting this turn produces is therefore:
    2 challenged-and-refuted (rounds 1 and 2) + 1 never-attempted (round 3, budget) + 1 genuine
    unreviewed survey sibling (round 1's batch) + 2 strengths (rounds 1 and 2's batches) = 6 raw
    candidate-shaped rows nominated in total, of which exactly 2 must be reported as "unreviewed"
    (the never-attempted one plus the genuine survey sibling) -- the 2 strengths must never inflate
    either count."""
    decision, router = _drive()
    assert router.steps().count("nominate") == 3, router.steps()
    # The third challenge is refused by the ledger's own step ceiling before any model call --
    # see the docstring above.
    assert router.steps().count("challenge") == 2, router.steps()

    stepped = dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})
    report = str(getattr(decision, "output_text", "") or "")

    assert stepped.get("terminal_state") == "no_finding", stepped.get("terminal_state")

    blocked_reason = str(stepped.get("blocked_reason") or "")
    assert "candidate" in blocked_reason and "unreviewed" in blocked_reason, blocked_reason

    accounting = stepped.get("candidate_accounting") or {}
    # The raw survey batch this turn actually saw: 1 genuine defect + 2 strengths, across the two
    # rounds that produced siblings -- the strengths this test exists to exclude from every count.
    assert accounting.get("survey_strengths_excluded") == 2, accounting
    # ARGUS repair D4's exact invariant: strengths excluded from the CANDIDATE count everywhere --
    # only the one genuine unreviewed survey sibling counts here, never the 2 strengths beside it.
    assert accounting.get("unreviewed_survey_only") == 1, accounting
    assert accounting.get("unreviewed_never_attempted") == 1, accounting

    # The narrative sentence must name the SAME total the score line's own accounting used (1
    # genuine survey sibling + 1 budget-refused challenge = 2) -- not a strength-inflated count.
    assert "2 candidates remained unreviewed" in blocked_reason, blocked_reason
    assert "4 candidate" not in blocked_reason, (
        "this is exactly the pre-D4 number: len(survey_extra)=3 (unfiltered, 2 strengths + 1 "
        f"defect) + never_attempted_count=1 = 4, which disagreed with the score line's 2: {blocked_reason!r}"
    )

    # The rendered report's own score line must name the identical number.
    assert "Unreviewed: 2" in report, report
    assert "Unreviewed: 4" not in report, report


def test_sabotage_a_no_op_strength_split_reinflates_the_unreviewed_count(monkeypatch) -> None:
    """Reverts the REAL production `split_strengths` (imported from `audit_verdict` and called
    directly on `survey_extra` in `stepped_audit.py`) to a no-op that never classifies anything as
    a strength, and proves the Activity accounting's `unreviewed_survey_only` count reinflates from
    1 back to 3 (the 2 strengths counted as candidates again) -- the exact pre-D4 shape."""
    import core.agent_runtime.audit_verdict as audit_verdict_module

    def no_op_split_strengths(findings):
        return list(findings), []

    monkeypatch.setattr(audit_verdict_module, "split_strengths", no_op_split_strengths)

    decision, _router = _drive()
    stepped = dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})
    accounting = stepped.get("candidate_accounting") or {}

    assert accounting.get("survey_strengths_excluded") == 0, (
        f"sabotage setup failed to neutralize split_strengths: {accounting}"
    )
    assert accounting.get("unreviewed_survey_only") == 3, (
        "the real regression must fail once split_strengths stops classifying strengths -- the "
        f"survey-only count must reinflate to include them: {accounting}"
    )
