"""SCALPEL fixture repair, failure class F, 2026-08-06, then ARGUS repair D3, 2026-08-06: cheap
deterministic checks before spending challenge/proof budget on a candidate a plain read of the code
already answers -- narrowed to the one direction that check can actually settle.

ARGUS's D3 finding: the original fixture here filtered a claim of "raises an unexpected exception"
purely because the cited code contained a designed `raise` -- exactly the over-eager filtering
`structural_exception_mismatch` no longer performs, since whether a raise is itself correct is a
question the AST cannot answer. The candidate that must now be filtered is the DIFFERENT, still-
narrow shape: a SILENT-failure claim pointed at a line that demonstrably, unconditionally raises
instead.

Drives the real `run_stepped_audit` with that genuinely-contradicted candidate and a second, real
candidate right behind it. Asserts the first costs NO challenge call (filtered deterministically)
while the second gets a real one -- proving the filter actually intercepts a candidate inside the
real loop, not just in isolation.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.agent_runtime.stepped_audit import run_stepped_audit

TARGET = "codec.py"
CODEC = (
    "class Codec:\n"
    "    def strict_decompress(self, blob):\n"
    "        if not blob:\n"
    "            raise ValueError('empty blob')\n"
    "        return blob\n"
    "\n"
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

# A candidate whose SILENT-failure claim is directly contradicted by an explicit, unconditional
# `raise` at the cited line 4 -- this must be filtered before any challenge call. (Not "raises
# unexpectedly" -- ARGUS repair D3 removed exactly that direction from what this filter settles.)
CONTRADICTED_FINDING = json.dumps(
    {
        "title": "strict_decompress silently accepts empty input",
        "file": TARGET,
        "line_start": 4,
        "line_end": 4,
        "cited_line_text": "            raise ValueError('empty blob')",
        "failure_scenario": "strict_decompress silently accepts an empty blob and returns incorrect "
        "output instead of raising, which callers do not handle and is a bug.",
    }
)
# A genuine, plausible candidate (the swallowing except at line 14) that must NOT be filtered.
GENUINE_FINDING = json.dumps(
    {
        "title": "Malformed record silently truncates the output",
        "file": TARGET,
        "line_start": 14,
        "line_end": 14,
        "cited_line_text": "                break",
        "failure_scenario": "A malformed record makes the bare except break the decode loop, so "
        "decompress silently returns a truncated prefix instead of raising.",
    }
)
CHALLENGE_SUPPORTED = json.dumps(
    {
        "verdict": "supported",
        "reason": "The cited except-break does swallow a malformed record and truncate output.",
        "counterexample": "",
    }
)
# ARGUS repair D3 required-survive case: the OLD pre-repair fixture's exact wording, cited at the
# SAME designed `raise` -- this must now reach a real adversarial challenge call instead of being
# filtered for free. A model, not an AST walk, is what settles whether an explicit raise is itself
# a defect.
UNWANTED_RAISE_FINDING = json.dumps(
    {
        "title": "strict_decompress raises unexpectedly on empty input",
        "file": TARGET,
        "line_start": 4,
        "line_end": 4,
        "cited_line_text": "            raise ValueError('empty blob')",
        "failure_scenario": "strict_decompress raises an unexpected ValueError on empty input, "
        "which is undocumented and callers do not handle it, producing incorrect behavior.",
    }
)
CHALLENGE_REFUTED = json.dumps(
    {
        "verdict": "refuted",
        "reason": "The raise is documented behavior for this method's precondition; callers are "
        "expected to validate non-empty input before calling strict_decompress.",
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


def _drive(replies=None, *, session_id="prefilter-1"):
    router = _ScriptedRouter(
        list(replies) if replies is not None else [CONTRADICTED_FINDING, GENUINE_FINDING, CHALLENGE_SUPPORTED]
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
            "workspace_root": "/tmp/prefilter-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }
    decision = run_stepped_audit(
        agent, task=SimpleNamespace(task_id="task-prefilter"),
        effective_input="Audit " + TARGET + " for the single highest-risk bug. Do not modify anything.",
        source_context=context, session_id=session_id,
    )
    return decision, router


def test_a_contradicted_candidate_is_filtered_without_spending_a_challenge_call() -> None:
    decision, router = _drive()
    stepped = dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})

    # Only ONE challenge call total: the contradicted candidate never reached one.
    assert router.steps().count("challenge") == 1, router.steps()
    assert router.steps() == ["nominate", "nominate", "challenge"], router.steps()

    filtered = stepped.get("filtered") or []
    assert filtered, "the structurally-contradicted candidate was not filtered at all"
    assert filtered[0]["lifecycle_state"] == "filtered"
    assert "does not silently continue" in filtered[0]["reason"]
    assert filtered[0]["id"], "the filtered row must carry the same stable id as any other row"

    accounting = stepped.get("candidate_accounting") or {}
    assert accounting.get("filtered") == 1, accounting

    # The genuine candidate right behind it was promoted normally.
    assert stepped.get("terminal_state") == "candidate_unproven"
    assert "malformed record" in str(stepped.get("finding", {}).get("title", "")).lower()


def test_a_claim_of_unwanted_raise_reaches_a_real_challenge_call_instead_of_being_filtered() -> None:
    """ARGUS repair D3, required-survive case, driven end to end: a claim that a designed `raise`
    is itself an unwanted defect must spend a real adversarial challenge call -- it is NEVER
    filtered deterministically just because the cited code contains an explicit raise. Before this
    repair, this exact candidate (this file's original fixture, verbatim) was filtered for free and
    never reached a model at all."""
    decision, router = _drive(
        [UNWANTED_RAISE_FINDING, CHALLENGE_REFUTED], session_id="prefilter-survives"
    )
    stepped = dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})

    assert router.steps().count("challenge") == 1, (
        f"the unwanted-raise claim must reach a real challenge call: {router.steps()}"
    )

    filtered = stepped.get("filtered") or []
    assert not filtered, f"an unwanted-raise claim must never be filtered deterministically: {filtered}"

    # Genuinely refuted BY THE MODEL, not by a deterministic pre-filter -- the terminal state names
    # a real adversarial outcome, and the search continued (no finding left to nominate here, so it
    # ends honestly rather than reporting a fabricated bug).
    assert stepped.get("terminal_state") in {"no_finding", "candidate_unproven"}, stepped.get(
        "terminal_state"
    )
