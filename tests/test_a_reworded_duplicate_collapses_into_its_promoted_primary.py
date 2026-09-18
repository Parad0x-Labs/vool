"""SCALPEL fixture repair, failure class D: a candidate cannot occupy two terminal buckets.

The exact contradiction observed: one underlying candidate appeared simultaneously as the
headline "likely" finding AND as a "not reviewed" / "challenged" row elsewhere in the same report.
Root cause, found by reading the actual exclusion code (`stepped_audit.py`'s
`survey_rows_unscreened`): the dedup that keeps a candidate out of the unreviewed table only ever
excluded rows matching an already-SCREENED-OUT candidate — never the row matching the candidate
that WON and became the promoted primary. A batch sibling (or a later nomination round) proposing
the same underlying claim under different wording was never checked against the primary's own
signature, so it survived into the "additional findings" table right next to the very candidate it
describes.

This drives the real `run_stepped_audit` — the same one the fixture's live drive used — with a
nomination batch whose two entries describe the identical mechanism in different words, the first
one promoted by a "supported" challenge. It asserts the reworded duplicate never reappears in the
unreviewed/additional-findings accounting once its own claim already won.
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
_CITED_LINE = "                break"
MANIFEST = SimpleNamespace(provider_id="test:pinned", model_name="test-model")

# Two findings describing the SAME underlying mechanism (the bare `except: break` silently
# truncates a malformed record) in genuinely different wording — exactly the shape a reworded
# resurfacing takes: different title, different phrasing of the scenario, same claim.
_PRIMARY_WORDING = {
    "title": "Malformed record silently truncates the output",
    "file": TARGET,
    "line_start": 9,
    "line_end": 9,
    "cited_line_text": _CITED_LINE,
    "failure_scenario": "A malformed trailing byte makes the bare except break the decode loop, so "
    "decompress returns a truncated prefix instead of raising.",
}
_REWORDED_SIBLING = {
    "title": "Malformed record truncates the decompress output",
    "file": TARGET,
    "line_start": 9,
    "line_end": 9,
    "cited_line_text": _CITED_LINE,
    "failure_scenario": "A malformed record hits the except-break in the decode loop, so decompress "
    "silently returns a truncated output instead of raising an exception.",
}
NOMINATION_BATCH = json.dumps({"findings": [_PRIMARY_WORDING, _REWORDED_SIBLING]})
SUPPORTED_CHALLENGE = json.dumps(
    {
        "verdict": "supported",
        "reason": "The cited except-break does swallow a malformed record and return a short prefix.",
        "counterexample": "",
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
                output_text=text,
                usage={"prompt_tokens": 80, "completion_tokens": 30},
                provider_id=manifest.provider_id,
                model_name=manifest.model_name,
                model_call_id="call-1",
                response_id="resp-1",
            ),
            None,
        )


class _ToolRunner:
    def __call__(self, payload, **kwargs):
        return SimpleNamespace(ok=True, handled=True, response_text="", details={}, status="executed")


def _drive(replies, *, prompt):
    router = _ScriptedRouter(replies)
    agent = SimpleNamespace(
        memory_router=router,
        _execute_tool_intent=_ToolRunner(),
        hive_activity_tracker=None,
        public_hive_bridge=None,
    )
    context = {
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {
            "all_paths": (TARGET,),
            "inspected_paths": (TARGET,),
            "sources": {TARGET: CODEC},
            "workspace_root": "/tmp/dedup-fixture-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }
    decision = run_stepped_audit(
        agent,
        task=SimpleNamespace(task_id="task-dedup"),
        effective_input=prompt,
        source_context=context,
        session_id="dedup-1",
    )
    return decision, router


def _stepped(decision):
    return dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})


def test_a_reworded_duplicate_of_the_winning_candidate_is_not_also_listed_as_unreviewed() -> None:
    # Read-only turn: the primary gets challenged (not proof-authorized), the challenge says
    # "supported" so it is promoted, and the batch's second entry — the same claim, reworded — must
    # not also survive into the unreviewed/additional-findings accounting.
    decision, _router = _drive(
        [NOMINATION_BATCH, SUPPORTED_CHALLENGE],
        prompt="Audit " + TARGET + " for the single highest-risk bug. Do not modify anything.",
    )
    stepped = _stepped(decision)

    assert stepped.get("terminal_state") == "candidate_unproven", stepped.get("terminal_state")
    primary = dict(stepped.get("finding") or {})
    assert "malformed record" in primary.get("title", "").lower() or "truncat" in primary.get(
        "title", ""
    ).lower()

    additional = list(stepped.get("additional_findings") or [])
    reworded_titles = {"malformed record truncates the decompress output"}
    leaked = [row for row in additional if str(row.get("title", "")).strip().lower() in reworded_titles]
    assert not leaked, (
        "the reworded duplicate of the promoted primary was also rendered as a separate "
        f"unreviewed candidate: {leaked}"
    )

    # Reconciliation must still hold with the duplicate collapsed — this is the "appears exactly
    # once in terminal accounting" assertion: the candidate is accounted for by the primary alone.
    accounting = dict(stepped.get("candidate_accounting") or {})
    assert accounting.get("unreviewed_survey_only", 0) == 0, accounting


# --------------------------------------------------------------------------------------
# `duplicate_candidate_ids` — the per-candidate hard invariant, direct unit tests + sabotage
# --------------------------------------------------------------------------------------


def test_duplicate_candidate_ids_is_clean_when_every_id_appears_once() -> None:
    from core.agent_runtime.audit_verdict import duplicate_candidate_ids

    screened = [{"id": "cand-aaa", "title": "x"}]
    survey = [{"id": "cand-bbb", "title": "y"}]
    primary = [{"id": "cand-ccc"}]

    assert duplicate_candidate_ids(screened, survey, primary) == ()


def test_duplicate_candidate_ids_flags_the_same_id_across_two_groups() -> None:
    from core.agent_runtime.audit_verdict import duplicate_candidate_ids

    screened = [{"id": "cand-aaa", "title": "x"}]
    survey = [{"id": "cand-aaa", "title": "x, reworded"}]  # SAME id, two buckets

    violations = duplicate_candidate_ids(screened, survey)

    assert violations == ("cand-aaa",)


def test_duplicate_candidate_ids_ignores_rows_with_no_id() -> None:
    """Older/synthetic rows without the field are never flagged -- this only tightens the
    guarantee for rows that carry the new identity, never produces a false positive for rows
    that predate it."""
    from core.agent_runtime.audit_verdict import duplicate_candidate_ids

    screened = [{"title": "no id here"}]
    survey = [{"title": "also no id"}]

    assert duplicate_candidate_ids(screened, survey) == ()


def test_sabotage_the_winning_candidates_own_row_is_not_checked_against_survey() -> None:
    """Direct sabotage of the SAME shape the fixture hit: simulate what the row groups would look
    like if the primary's own id were never added to the exclusion set (the pre-fix behavior) --
    confirm `duplicate_candidate_ids` correctly flags it, so the invariant itself is proven to
    catch this class of bug even independent of the specific `stepped_audit.py` fix above."""
    from core.agent_runtime.audit_verdict import duplicate_candidate_ids

    primary_id = "cand-primary-1234567"
    screened_rows = []  # the primary was promoted, never screened out
    # SABOTAGE: the survey list still contains a row sharing the primary's own id (as it would if
    # the exclusion in `stepped_audit.py` were reverted) instead of having been filtered out.
    survey_rows_unscreened_sabotaged = [{"id": primary_id, "title": "reworded duplicate"}]

    violations = duplicate_candidate_ids(
        screened_rows, survey_rows_unscreened_sabotaged, [{"id": primary_id}]
    )

    assert violations == (primary_id,), "the invariant failed to catch a candidate in two buckets"
