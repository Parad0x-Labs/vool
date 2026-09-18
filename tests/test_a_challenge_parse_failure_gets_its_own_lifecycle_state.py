"""SCALPEL fixture repair, failure class D/I: a challenge that never returned usable JSON must not
be indistinguishable from a challenge that returned a real, considered verdict.

Before this fix, `_challenge_finding`'s three failure shapes -- a provider/transport error, an
empty reply, and a reply that was not valid JSON -- all collapsed into the identical
`FindingChallenge(verdict="uncertain")`, rendered as the same generic `CANDIDATE_CHALLENGED`
lifecycle state as a challenge that genuinely ran and came back unsure. The only way to tell them
apart was to read the free-text `reason` string. `FindingChallenge.error_kind` makes the
distinction structured; these drive `_challenge_finding` directly (the real function, not a mirror
of it) with each of the four real shapes and assert the resulting field, then confirm the SAME
distinction survives into `screened_rows`'s `lifecycle_state` through a real `run_stepped_audit`
pass (bounded to fit the read-only ledger's 5-call ceiling: one nomination, one challenge).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.agent_runtime.stepped_audit import SteppedAuditBudget, SteppedFinding, _challenge_finding

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
FINDING = SteppedFinding(
    title="Zero-length record spins the decode loop",
    file=TARGET,
    line_start=9,
    line_end=9,
    cited_line_text="                break",
    failure_scenario="A record whose length byte is zero never advances the cursor, so the while "
    "loop never terminates and decompress hangs.",
)


class _OneShotRouter:
    """Returns exactly one scripted reply, of any of the four real shapes `_call_step` can see."""

    def __init__(self, reply):
        self.reply = reply

    def _requested_model_manifest(self, _context):
        return MANIFEST

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        if isinstance(self.reply, str) and self.reply.startswith("ERROR:"):
            return (None, None, self.reply[len("ERROR:") :])
        return (
            None,
            SimpleNamespace(
                output_text=self.reply,
                usage={"prompt_tokens": 60, "completion_tokens": 20},
                provider_id=manifest.provider_id,
                model_name=manifest.model_name,
                model_call_id="call-1",
                response_id="resp-1",
            ),
            None,
        )


def _challenge(reply):
    agent = SimpleNamespace(memory_router=_OneShotRouter(reply))
    return _challenge_finding(
        agent,
        manifest=MANIFEST,
        task=SimpleNamespace(task_id="t"),
        source_context={},
        evidence=SimpleNamespace(workspace_root="/tmp/ws", sources={TARGET: CODEC}),
        finding=FINDING,
        budget=SteppedAuditBudget(),
        usages=[],
    )


def test_a_provider_transport_error_is_tagged_provider_failure() -> None:
    challenge = _challenge("ERROR:transport_timeout")
    assert challenge.verdict == "uncertain"
    assert challenge.attempted is True
    assert challenge.error_kind == "provider_failure"


def test_a_non_json_reply_is_tagged_parse_failed() -> None:
    challenge = _challenge("Sure, I think this looks like a real bug because...")
    assert challenge.verdict == "uncertain"
    assert challenge.attempted is True
    assert challenge.error_kind == "parse_failed"


def test_an_empty_reply_is_tagged_parse_failed() -> None:
    challenge = _challenge("")
    assert challenge.verdict == "uncertain"
    assert challenge.error_kind == "parse_failed"


def test_a_genuine_structured_uncertain_verdict_carries_no_error_kind() -> None:
    challenge = _challenge(json.dumps({"verdict": "uncertain", "reason": "Not enough to decide."}))
    assert challenge.verdict == "uncertain"
    assert challenge.attempted is True
    assert challenge.error_kind == "", "a real, considered verdict must not be tagged as a failure"


def test_a_genuine_refuted_verdict_carries_no_error_kind() -> None:
    challenge = _challenge(
        json.dumps(
            {
                "verdict": "refuted",
                "reason": "The cited line does not do what the claim says.",
                "counterexample": "A zero-length record advances pid=0, out.extend(blob[:0]) is a no-op.",
            }
        )
    )
    assert challenge.verdict == "refuted"
    assert challenge.error_kind == ""


def test_the_distinction_survives_into_the_screened_row_lifecycle_state() -> None:
    """The same distinction, through the real `run_stepped_audit` path end to end — not just the
    unit-level `_challenge_finding` call above."""
    from core.agent_runtime.stepped_audit import run_stepped_audit

    finding_json = json.dumps(
        {
            "title": FINDING.title,
            "file": FINDING.file,
            "line_start": FINDING.line_start,
            "line_end": FINDING.line_end,
            "cited_line_text": FINDING.cited_line_text,
            "failure_scenario": FINDING.failure_scenario,
        }
    )

    class _TwoCallRouter:
        def __init__(self):
            self.replies = [finding_json, "not json at all, just prose"]

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

    agent = SimpleNamespace(
        memory_router=_TwoCallRouter(),
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
            "workspace_root": "/tmp/parse-fail-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }
    decision = run_stepped_audit(
        agent,
        task=SimpleNamespace(task_id="task-parse-fail"),
        effective_input="Audit " + TARGET + " for the single highest-risk bug. Do not modify anything.",
        source_context=context,
        session_id="parse-fail-e2e",
    )
    stepped = dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})
    rows = list(stepped.get("screened_out") or [])

    assert any(row.get("lifecycle_state") == "challenge_parse_failed" for row in rows), rows
    assert not any(row.get("lifecycle_state") == "rejected" for row in rows), (
        "a parse failure must never be mislabelled as an adversarial rejection"
    )
