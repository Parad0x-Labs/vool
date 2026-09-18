"""A follow-up proves the finding it was about, in the project it was made in.

The incident these pin: an audit reported a decompression defect, the operator replied "Prove the
bug you identified before fixing it", and the runtime generated, wrote and executed tests for
empty-input behaviour nobody had mentioned — then scored their exit status as a verdict about the
audited code.

The subject here is DELIBERATELY not that file. Everything under test is a general property of the
agent loop — a finding is addressed and scoped state, a continuation derives a task contract, and
an artifact is checked against that contract before it is written or run — so it is exercised on a
different project, a different domain and a different class of bug. A fix that only worked on the
original fixture would pass nothing in this module.

Lettered to the hostile-test plan:

    A  defect X, "prove it"                     -> the reproduction still targets X
    B  the same, worded "show me that's broken" -> still bound to X
    C  an unrelated project and defect type     -> the same behaviour
    D  "ignore that one, check Y instead"       -> the target CHANGES, because the operator said so
    E  finding in project A, asked in project B  -> project A's finding is not available
    F  the claim fails reproduction              -> withdrawn, never confirmed
    G  structured internal state                 -> prose reaches the UI, structure stays internal
    H  paths, languages, bug types and wording written after the implementation
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.agent_runtime.active_finding import (
    FindingScope,
    active_finding_for,
    classify_follow_up,
    clear_active_findings,
)
from core.agent_runtime.audit_session import audit_follow_up_resumes, clear_audit_capsules
from core.agent_runtime.stepped_audit import run_stepped_audit
from core.tool_call_dialects import looks_like_internal_payload

# --------------------------------------------------------------------------------------
# A different project entirely: a session ledger, whose bugs are boundary and bookkeeping
# defects rather than anything to do with parsing or decompression.
# --------------------------------------------------------------------------------------

_LEDGER_LINES = (
    ["#!/usr/bin/env python3", '"""A rotating session ledger."""', "import time", "", "class Ledger:"]
    + [f"    def audit_{i}(self):" if i % 2 else f"        return {i}" for i in range(6, 24)]
    + [
        "    def issue(self, token, ttl, now):",
        "        expires = now + ttl",
        "        self._rows[token] = {'expires': expires, 'revoked': False}",
        "        return token",
        "    def valid(self, token, now):",
        "        row = self._rows.get(token)",
        "        if row is None:",
        "            return False",
        "        return row['expires'] > now and not row['revoked']",
        "    def revoke(self, token):",
        "        self._rows.pop(token, None)",
        "        return True",
    ]
)
LEDGER = "\n".join(_LEDGER_LINES)
LEDGER_TARGET = "pkg/session/rotating_ledger.py"

_TTL_LINE = "        expires = now + ttl"
_REVOKE_LINE = "        self._rows.pop(token, None)"
TTL_LINE_NO = _LEDGER_LINES.index(_TTL_LINE) + 1
REVOKE_LINE_NO = _LEDGER_LINES.index(_REVOKE_LINE) + 1

MANIFEST = SimpleNamespace(provider_id="local-ollama", model_name="qwen3-coder:30b")

AUDIT_PROMPT = (
    f"Look over {LEDGER_TARGET} and tell me the single highest-risk real bug. "
    "Do not modify anything."
)


def _finding(*, title, line_no, line_text, scenario):
    return json.dumps(
        {
            "title": title,
            "file": LEDGER_TARGET,
            "line_start": line_no,
            "line_end": line_no + 1,
            "cited_line_text": line_text,
            "failure_scenario": scenario,
        }
    )


# Defect X — a boundary bug, with a concrete triggering input (a ttl of 0).
TTL_FINDING = _finding(
    title="A zero ttl issues a token that is already expired",
    line_no=TTL_LINE_NO,
    line_text=_TTL_LINE,
    scenario=(
        "issue(token, 0, now) stores expires equal to now, and valid() requires expires > now, so "
        "the very next check returns an incorrect False and the caller silently loses data for a "
        "session it was just handed."
    ),
)
# A different defect in the same file, so "the search advanced" is distinguishable from
# "the search repeated itself".
REVOKE_FINDING = _finding(
    title="Revoking a token that was never issued reports success",
    line_no=REVOKE_LINE_NO,
    line_text=_REVOKE_LINE,
    scenario=(
        "revoke() pops a key that is absent and returns True, so a caller records a revocation "
        "that never happened and a later audit returns a wrong answer about the session."
    ),
)

# A claim the evidence gate refuses (no incorrect output, no data loss — a housekeeping remark),
# so a turn built from it ends with no candidate at all.
UNCHECKABLE_FINDING = _finding(
    title="The ledger keeps revoked rows in memory",
    line_no=REVOKE_LINE_NO,
    line_text=_REVOKE_LINE,
    scenario="Rows are never pruned, so a long-lived process holds more memory than it needs.",
)

SUPPORTED = json.dumps(
    {
        "verdict": "supported",
        "reason": "The cited assignment is the expiry the validity check compares against.",
        "counterexample": "A ttl of 0 makes expires == now, which the strict comparison rejects.",
    }
)

# A reproduction artifact for defect X: it exercises the input the claim names and asserts the
# CORRECT contract, so it fails while the claimed bug is present.
TTL_TEST_FILE = (
    "class TestZeroTtl(unittest.TestCase):\n"
    "    def test_a_zero_ttl_token_is_valid_immediately_after_issue(self):\n"
    "        ledger = subject.Ledger()\n"
    "        ledger.issue('t', 0, 100)\n"
    "        self.assertTrue(ledger.valid('t', 100))\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)
# The incident's shape, transplanted: a perfectly well-formed test of a DIFFERENT behaviour. It
# would run, and its exit status would say nothing whatever about the claim it was asked to prove.
OFF_TARGET_TEST_FILE = (
    "class TestRevocation(unittest.TestCase):\n"
    "    def test_revoking_an_issued_token_marks_the_row(self):\n"
    "        ledger = subject.Ledger()\n"
    "        ledger.issue('t', 5, 9)\n"
    "        self.assertTrue(ledger.revoke('t'))\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)
REVOKE_TEST_FILE = (
    "class TestUnknownRevocation(unittest.TestCase):\n"
    "    def test_revoking_a_token_that_was_never_issued_reports_failure(self):\n"
    "        self.assertFalse(subject.Ledger().revoke('never-issued'))\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)

FAILING_RUN = "test_it ... FAIL\n\nRan 1 test in 0.002s\n\nFAILED (failures=1)"
GREEN_RUN = "test_it ... ok\n\nRan 1 test in 0.002s\n\nOK"
SYNTHESIS = "A ttl of zero yields an expiry the validity check immediately rejects."

PROVE_IT = "Prove it."
SHOW_ME_BROKEN = "Show me that's actually broken."
RETARGET = "Ignore that one. Check for an empty-input bug instead."


class _ScriptedRouter:
    def __init__(self, replies, *, manifest=MANIFEST):
        self.replies = list(replies)
        self.requests = []
        self.manifest = manifest

    def _requested_model_manifest(self, _context):
        return self.manifest

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        self.requests.append(request)
        step = str(dict(getattr(request, "metadata", None) or {}).get("stepped_audit_step") or "")
        item = None
        if step == "challenge":
            # Tests that exercise the challenge itself queue an explicit verdict; everything else
            # gets a default "supported" without consuming a nomination or proof reply.
            if self.replies:
                head = self.replies[0]
                try:
                    parsed = json.loads(head if isinstance(head, str) else "")
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict) and parsed.get("verdict"):
                    item = self.replies.pop(0)
            if item is None:
                item = SUPPORTED
        elif self.replies:
            item = self.replies.pop(0)
        if item is None:
            return (None, None, "script_exhausted")
        if isinstance(item, str) and item.startswith("ERROR:"):
            return (None, None, item[len("ERROR:") :])
        return (
            None,
            SimpleNamespace(
                output_text=str(item),
                usage={"prompt_tokens": 90, "completion_tokens": 30},
                provider_id=manifest.provider_id,
                model_name=manifest.model_name,
                model_call_id="call-1",
                response_id="resp-1",
            ),
            None,
        )

    def steps(self):
        return [
            str(dict(getattr(request, "metadata", None) or {}).get("stepped_audit_step") or "")
            for request in self.requests
        ]

    def prompts(self, step):
        return [
            str(getattr(request, "prompt", "") or "")
            for request in self.requests
            if str(dict(getattr(request, "metadata", None) or {}).get("stepped_audit_step") or "") == step
        ]


class _ToolRunner:
    def __init__(self, *, runs=((1, FAILING_RUN),)):
        self.runs = list(runs)
        self.calls = []

    def intents(self):
        return [intent for intent, _args in self.calls]

    def written(self):
        return [
            str(args.get("content") or "")
            for intent, args in self.calls
            if intent == "workspace.write_file"
        ]

    def __call__(self, payload, **kwargs):
        intent = str(payload.get("intent") or "")
        self.calls.append((intent, dict(payload.get("arguments") or {})))
        if intent == "sandbox.run_command":
            returncode, output = self.runs.pop(0) if self.runs else (1, FAILING_RUN)
            return SimpleNamespace(
                ok=returncode == 0, handled=True, response_text=output,
                details={"returncode": returncode}, status="executed", mode="tool_executed",
            )
        return SimpleNamespace(
            ok=True, handled=True, response_text="", details={}, status="executed",
            mode="tool_executed",
        )


def _context(*, project_id="proj-ledger", source=LEDGER, target=LEDGER_TARGET):
    return {
        "project_id": project_id,
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {
            "all_paths": (target, "README.md"),
            "inspected_paths": (target,),
            "sources": {target: source},
            "workspace_root": "/tmp/ledger-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }


def _drive(replies, *, prompt=AUDIT_PROMPT, session_id="ledger-1", context=None, tool_runner=None):
    router = _ScriptedRouter(replies)
    tools = tool_runner if tool_runner is not None else _ToolRunner()
    agent = SimpleNamespace(
        memory_router=router,
        _execute_tool_intent=tools,
        hive_activity_tracker=None,
        public_hive_bridge=None,
    )
    decision = run_stepped_audit(
        agent,
        task=SimpleNamespace(task_id="task-ledger"),
        effective_input=prompt,
        source_context=_context() if context is None else context,
        session_id=session_id,
    )
    return decision, router, tools


def _stepped(decision):
    return dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})


def _report(decision):
    return str(getattr(decision, "output_text", "") or "")


@pytest.fixture(autouse=True)
def _clean_state():
    clear_audit_capsules()
    clear_active_findings()
    yield
    clear_audit_capsules()
    clear_active_findings()


def _seed_defect_x(session_id="ledger-1", project_id="proj-ledger"):
    """A read-only audit that leaves defect X as the finding a follow-up refers to."""
    return _drive([TTL_FINDING], session_id=session_id, context=_context(project_id=project_id))


# --------------------------------------------------------------------------------------
# TEST A — "prove it" reproduces the finding that was made, not a new one
# --------------------------------------------------------------------------------------


def test_prove_it_generates_a_reproduction_for_the_finding_that_was_reported() -> None:
    _seed_defect_x()
    decision, router, tools = _drive(
        [TTL_TEST_FILE, SYNTHESIS], prompt=PROVE_IT, session_id="ledger-1"
    )

    assert "nominate" not in router.steps(), "the follow-up picked a new target instead of proving X"
    prove_prompts = router.prompts("prove")
    assert prove_prompts and "zero ttl" in prove_prompts[0].lower(), (
        "the test-writing call was not told which finding it is reproducing"
    )
    assert _stepped(decision)["finding"]["line_start"] == TTL_LINE_NO
    assert "sandbox.run_command" in tools.intents()
    assert _stepped(decision)["terminal_state"] == "proven"


def test_an_off_target_reproduction_is_refused_before_it_is_written_or_run() -> None:
    """The incident itself, on a different project: the artifact is well-formed, it would run, and
    it tests something the finding never claimed. Nothing about it may reach the workspace."""
    _seed_defect_x()
    decision, router, tools = _drive(
        [OFF_TARGET_TEST_FILE, OFF_TARGET_TEST_FILE],
        prompt=PROVE_IT,
        session_id="ledger-1",
    )

    assert router.steps().count("prove") == 2, "the rejected artifact got no correction attempt"
    assert "workspace.write_file" not in tools.intents()
    assert "sandbox.run_command" not in tools.intents()
    assert _stepped(decision)["terminal_state"] != "proven"
    assert _stepped(decision)["proof"]["contract_rejected"] is True
    assert "reproduced by a failing test" not in _report(decision)


def test_the_correction_names_what_the_rejected_artifact_missed() -> None:
    """A bare rejection produces the same mistake again; the second attempt must be a repair."""
    _seed_defect_x()
    _decision, router, _tools = _drive(
        [OFF_TARGET_TEST_FILE, TTL_TEST_FILE, SYNTHESIS], prompt=PROVE_IT, session_id="ledger-1"
    )

    second = router.prompts("prove")[1].lower()
    assert "rejected" in second
    assert "fails on the current code" in second


def test_the_accepted_artifact_is_the_one_that_reaches_the_workspace() -> None:
    _seed_defect_x()
    _decision, _router, tools = _drive(
        [OFF_TARGET_TEST_FILE, TTL_TEST_FILE, SYNTHESIS], prompt=PROVE_IT, session_id="ledger-1"
    )

    written = tools.written()
    assert len(written) == 1
    assert "zero_ttl" in written[0]
    assert "TestRevocation" not in written[0]


# --------------------------------------------------------------------------------------
# TEST B — the same binding under different wording
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wording",
    [
        "Show me that's actually broken.",
        "Prove the bug you identified before fixing it.",
        "reproduce it",
        "Can you verify the bug first, please.",
        "demonstrate that finding for me",
    ],
)
def test_alternate_wordings_stay_bound_to_the_same_finding(wording: str) -> None:
    _seed_defect_x()
    decision, router, _tools = _drive(
        [TTL_TEST_FILE, SYNTHESIS], prompt=wording, session_id="ledger-1"
    )

    assert "nominate" not in router.steps(), f"{wording!r} re-picked a target instead of resuming"
    assert _stepped(decision)["finding"]["line_start"] == TTL_LINE_NO


def test_an_ordinary_sentence_containing_the_verb_never_binds() -> None:
    """The mirror-image failure: binding on a verb would drag unrelated turns into the lane."""
    _seed_defect_x()
    for ordinary in (
        "prove that you can write Python",
        "show me how to rotate a session token",
        "can you demonstrate the difference between TTL and expiry",
        "write a test for the ledger",
    ):
        assert (
            audit_follow_up_resumes(ordinary, session_id="ledger-1", project_id="proj-ledger")
            is None
        ), ordinary


# --------------------------------------------------------------------------------------
# TEST C — an unrelated project and defect class behaves identically
# --------------------------------------------------------------------------------------

_CSV_LINES = (
    ["import csv", "", "class Importer:"]
    + [f"    def step_{i}(self):" if i % 2 else f"        return {i}" for i in range(4, 20)]
    + [
        "    def load(self, rows):",
        "        header = rows[0]",
        "        out = []",
        "        for row in rows:",
        "            out.append(dict(zip(header, row)))",
        "        return out",
    ]
)
CSV_SOURCE = "\n".join(_CSV_LINES)
CSV_TARGET = "etl/importers/customer_csv.py"
_CSV_LINE = "        for row in rows:"
CSV_LINE_NO = _CSV_LINES.index(_CSV_LINE) + 1

CSV_FINDING = json.dumps(
    {
        "title": "The header row is imported as a data record",
        "file": CSV_TARGET,
        "line_start": CSV_LINE_NO,
        "line_end": CSV_LINE_NO + 1,
        "cited_line_text": _CSV_LINE,
        "failure_scenario": (
            "The loop starts at index 0 rather than 1, so load() returns an incorrect first "
            "record whose every field holds its own column name."
        ),
    }
)
CSV_TEST_FILE = (
    "class TestHeaderRow(unittest.TestCase):\n"
    "    def test_the_header_row_is_not_returned_as_a_record(self):\n"
    "        rows = [['id', 'name'], ['1', 'ada']]\n"
    "        self.assertEqual(len(subject.Importer().load(rows)), 1)\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)


def test_the_binding_transfers_to_an_unrelated_project_and_defect_class() -> None:
    context = _context(project_id="proj-etl", source=CSV_SOURCE, target=CSV_TARGET)
    _drive([CSV_FINDING], session_id="etl-1", context=context)
    decision, router, tools = _drive(
        [CSV_TEST_FILE, "The header row is emitted as a record."],
        prompt="Prove the bug you identified before fixing it.",
        session_id="etl-1",
        context=context,
    )

    assert "nominate" not in router.steps()
    assert _stepped(decision)["finding"]["file"] == CSV_TARGET
    assert _stepped(decision)["terminal_state"] == "proven"
    assert "header" in tools.written()[0].lower()


def test_an_off_target_artifact_is_refused_in_the_unrelated_project_too() -> None:
    context = _context(project_id="proj-etl", source=CSV_SOURCE, target=CSV_TARGET)
    _drive([CSV_FINDING], session_id="etl-2", context=context)
    decision, _router, tools = _drive(
        [TTL_TEST_FILE, TTL_TEST_FILE],
        prompt="Prove it.",
        session_id="etl-2",
        context=context,
    )

    assert "workspace.write_file" not in tools.intents()
    assert _stepped(decision)["terminal_state"] != "proven"


# --------------------------------------------------------------------------------------
# TEST D — an explicit change of subject is obeyed
# --------------------------------------------------------------------------------------


def test_an_explicit_retarget_releases_the_binding() -> None:
    _seed_defect_x()

    assert (
        audit_follow_up_resumes(RETARGET, session_id="ledger-1", project_id="proj-ledger") is None
    ), "the operator explicitly dropped the finding and the runtime held on to it"
    assert classify_follow_up(RETARGET).explicit_retarget is True
    assert classify_follow_up(RETARGET).binds_to_active_finding is False


def test_an_explicit_retarget_makes_the_runtime_pick_a_new_candidate() -> None:
    _seed_defect_x()
    _decision, router, _tools = _drive(
        [REVOKE_FINDING], prompt=RETARGET, session_id="ledger-1"
    )

    assert "nominate" in router.steps(), (
        "the runtime proved the discarded finding instead of searching again"
    )


@pytest.mark.parametrize(
    "wording",
    [
        "Ignore that one. Check for an empty-input bug instead.",
        "Forget that finding — look for a different bug.",
        "Never mind that, prove a different issue instead.",
    ],
)
def test_retarget_phrasings_all_release_the_binding(wording: str) -> None:
    _seed_defect_x()
    assert (
        audit_follow_up_resumes(wording, session_id="ledger-1", project_id="proj-ledger") is None
    ), wording


# --------------------------------------------------------------------------------------
# TEST E — a finding made in one project is not reachable from another
# --------------------------------------------------------------------------------------


def test_a_finding_from_project_a_is_not_available_inside_project_b() -> None:
    _seed_defect_x(session_id="shared-chat", project_id="project-a")

    assert (
        audit_follow_up_resumes(PROVE_IT, session_id="shared-chat", project_id="project-a")
        is not None
    ), "the finding is not reachable in the project that made it"
    assert (
        audit_follow_up_resumes(PROVE_IT, session_id="shared-chat", project_id="project-b") is None
    ), "project A's finding became active inside project B"


def test_the_active_finding_store_isolates_by_project() -> None:
    _seed_defect_x(session_id="shared-chat", project_id="project-a")

    assert active_finding_for(FindingScope(project_id="project-a", chat_id="shared-chat")) is not None
    assert active_finding_for(FindingScope(project_id="project-b", chat_id="shared-chat")) is None
    assert active_finding_for(FindingScope(project_id="project-a", chat_id="other-chat")) is None


def test_project_b_re_nominates_rather_than_proving_project_as_finding() -> None:
    _seed_defect_x(session_id="shared-chat", project_id="project-a")
    _decision, router, tools = _drive(
        [REVOKE_FINDING],
        prompt=PROVE_IT,
        session_id="shared-chat",
        context=_context(project_id="project-b"),
    )

    assert "nominate" in router.steps(), "project B resumed project A's audit"
    assert "sandbox.run_command" not in tools.intents() or "nominate" in router.steps()


# --------------------------------------------------------------------------------------
# TEST F — a claim that fails reproduction is withdrawn, never confirmed
# --------------------------------------------------------------------------------------


def test_a_claim_whose_reproduction_passes_is_withdrawn_not_confirmed() -> None:
    """A proof test that exits 0 DISPROVES its claim. The old lane stopped there and described the
    disproved claim as the finding."""
    _seed_defect_x()
    decision, _router, _tools = _drive(
        [TTL_TEST_FILE, "ERROR:no further candidates"],
        prompt=PROVE_IT,
        session_id="ledger-1",
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN)]),
    )
    report = _report(decision).lower()

    assert _stepped(decision)["terminal_state"] in {"refuted", "no_finding", "blocked"}
    assert "highest-risk bug" not in report
    assert "reproduced by a failing test" not in report
    refuted = _stepped(decision)["refuted"]
    assert refuted and "zero ttl" in str(refuted).lower()


def test_a_disproved_claim_is_recorded_as_such_on_the_active_finding() -> None:
    _seed_defect_x()
    _drive(
        [TTL_TEST_FILE, "ERROR:no further candidates"],
        prompt=PROVE_IT,
        session_id="ledger-1",
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN)]),
    )
    row = active_finding_for(FindingScope(project_id="proj-ledger", chat_id="ledger-1"))

    assert row is not None
    assert row.verification_status in {"refuted", "withdrawn"}
    assert row.confidence != "confirmed"
    assert row.is_open is False, "a disproved finding must not stay referable as 'the bug'"


def test_a_replacement_finding_is_presented_as_a_replacement() -> None:
    """A different bug may be reported after a disproof — but the disproof must be visible, not
    quietly overwritten by the new claim."""
    _seed_defect_x()
    decision, _router, _tools = _drive(
        [TTL_TEST_FILE, REVOKE_FINDING, REVOKE_TEST_FILE, SYNTHESIS],
        prompt=PROVE_IT,
        session_id="ledger-1",
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN), (1, FAILING_RUN)]),
    )
    report = _report(decision).lower()

    assert _stepped(decision)["terminal_state"] == "proven"
    assert "revoking a token" in report
    assert "disproved" in report and "zero ttl" in report


def test_a_continuation_with_nothing_to_resume_nominates_nothing_at_all() -> None:
    """Driven live 2026-08-01 on the frozen fixture: the audit turn ended without a candidate and
    the follow-up nominated a fresh one under the operator's pronoun.

    An earlier pass answered this by LABELLING the substitution ("what follows is a replacement").
    That is still a turn spent proving something nobody asked about, and it still writes a file.
    The referent is now resolved before anything runs: no candidate, no call, no artifact."""
    _drive(
        [UNCHECKABLE_FINDING, UNCHECKABLE_FINDING, UNCHECKABLE_FINDING], session_id="ledger-sub"
    )
    decision, router, tools = _drive(
        [REVOKE_FINDING, REVOKE_TEST_FILE, SYNTHESIS], prompt=PROVE_IT, session_id="ledger-sub"
    )
    report = _report(decision).lower()

    assert router.steps() == [], "a referent was manufactured for a pronoun that pointed at nothing"
    assert tools.intents() == []
    assert _stepped(decision)["terminal_state"] == "follow_up_blocked"
    assert "nothing to reproduce" in report
    assert "revoking a token" not in report, "a substituted finding was reported anyway"


def test_a_candidate_whose_proof_ran_never_claims_no_command_ran() -> None:
    """The report printed a proof run and then footed itself with "no proof command ran"."""
    _seed_defect_x()
    decision, _router, _tools = _drive(
        [TTL_TEST_FILE, "ERROR:done"],
        prompt=PROVE_IT,
        session_id="ledger-1",
        tool_runner=_ToolRunner(runs=[(1, "AttributeError: no such attribute\n")]),
    )
    report = _report(decision)
    proof = _stepped(decision)["proof"]

    assert _stepped(decision)["terminal_state"] == "candidate_unproven"
    assert proof["note"] == "errored"
    # 2026-08-06: CANDIDATE_UNPROVEN's chat report no longer inlines the proof command/output at
    # all (the concise NOT-PROVEN contract) -- the command that actually ran is preserved in the
    # structured proof record for Activity instead, never contradicted by a "no command ran"
    # sentence sitting next to it (the exact self-footing bug this test was written for).
    assert proof["test_command"] == "python3 -m unittest -v test_rotating_ledger_bug"
    assert proof["attempted"] is True
    assert "no proof command ran" not in report
    assert "python3 -m unittest" not in report, "chat must not carry the raw command for a candidate"
    assert "errored before it could report a verdict" in report


def test_the_errored_run_digest_carries_the_exception_that_ended_it() -> None:
    from core.agent_runtime.builder.app_builder import summarize_test_output

    digest = summarize_test_output(
        "Command failed in `generated/x`:\n"
        "trace\n"
        "  file 'test_x.py', line 12, in test_it\n"
        "AttributeError: module 'subject' has no attribute 'Ledger'\n"
    )

    assert "AttributeError: module 'subject' has no attribute 'Ledger'" in digest


# --------------------------------------------------------------------------------------
# TEST G — structured state is internal; the answer is prose
# --------------------------------------------------------------------------------------


def test_the_answer_is_prose_while_the_structured_record_stays_internal() -> None:
    decision, _router, _tools = _drive([TTL_FINDING])
    report = _report(decision)
    stepped = _stepped(decision)

    # Internal: a real structured record, addressable and complete.
    assert stepped["finding"]["title"] == "A zero ttl issues a token that is already expired"
    assert stepped["active_finding"]["finding_id"].startswith("af-")
    assert stepped["active_finding"]["project_id"] == "proj-ledger"
    assert stepped["active_finding"]["verification_status"] == "candidate_unproven"

    # External: prose. No JSON envelope, no generated source, no orchestration lines.
    assert not report.lstrip().startswith("{")
    assert '"failure_scenario"' not in report
    assert '"line_start"' not in report
    assert "unittest.TestCase" not in report
    assert "workspace.write_file" not in report
    assert looks_like_internal_payload(report) is False
    # 2026-08-06: the candidate's own title no longer reaches chat for CANDIDATE_UNPROVEN (the
    # concise NOT-PROVEN contract) — it lives in the structured record checked above instead. The
    # chat answer is still real prose, not empty or malformed.
    assert "NOT PROVEN" in report
    assert "A zero ttl issues a token" not in report


def test_a_bare_internal_record_can_never_be_an_answer() -> None:
    """The response layer's backstop: whatever goes wrong upstream, internal state is not prose."""
    leaked = json.dumps(json.loads(TTL_FINDING), indent=2)

    assert looks_like_internal_payload(leaked) is True
    # A JSON object a person actually asked for is untouched.
    assert looks_like_internal_payload('{"name": "ada", "city": "vilnius", "age": 3}') is False
    assert looks_like_internal_payload("The zero-ttl boundary is the highest-risk bug.") is False


def test_the_proof_report_shows_the_command_and_not_the_generated_source() -> None:
    _seed_defect_x()
    decision, _router, _tools = _drive(
        [TTL_TEST_FILE, SYNTHESIS], prompt=PROVE_IT, session_id="ledger-1"
    )
    report = _report(decision)

    assert "python3 -m unittest" in report, "a proof must show the command that ran"
    assert "assertTrue(ledger.valid" not in report, "the generated source was dumped into the answer"
    assert "class TestZeroTtl" not in report


# --------------------------------------------------------------------------------------
# TEST H — paths, languages, bug classes and wording written after the implementation
# --------------------------------------------------------------------------------------


def test_the_gate_is_language_agnostic() -> None:
    """Nothing in the gate knows about Python. These claims and artifacts are Rust and TypeScript."""
    from core.agent_runtime.continuity_gate import check_proof_artifact, contract_from_finding

    rust = contract_from_finding(
        title="A slice of width 4 panics on a 3-byte frame",
        file="src/frame/decoder.rs",
        cited_line_text="let head = &buf[..4];",
        line_start=88,
        line_end=90,
        failure_scenario=(
            "decode() slices four bytes from a frame of length 3, which panics instead of "
            "returning an incorrect short read to the caller."
        ),
    )
    on_target = (
        "#[test]\n"
        "fn a_three_byte_frame_is_rejected_without_panicking() {\n"
        "    let buf = vec![1u8, 2, 3];\n"
        "    assert!(decode(&buf).is_err());\n"
        "}\n"
    )
    off_target = (
        "#[test]\n"
        "fn an_empty_frame_returns_none() {\n"
        "    assert!(decode(&[]).is_none());\n"
        "}\n"
    )
    assert check_proof_artifact(on_target, contract=rust).accepted is True
    assert check_proof_artifact(off_target, contract=rust).accepted is False

    ts = contract_from_finding(
        title="Retry backoff overflows after 30 attempts",
        file="src/net/retry.ts",
        cited_line_text="const delay = 2 ** attempt;",
        line_start=41,
        line_end=42,
        failure_scenario=(
            "At attempt 30 the shift produces a wrong negative delay, so the client returns an "
            "incorrect immediate retry instead of backing off."
        ),
    )
    ts_on_target = (
        "test('attempt 30 still backs off', () => {\n"
        "  expect(backoffFor(30)).toBeGreaterThan(0);\n"
        "});\n"
    )
    ts_off_target = (
        "test('the first attempt is immediate', () => {\n"
        "  expect(backoffFor(1)).toBe(2);\n"
        "});\n"
    )
    assert check_proof_artifact(ts_on_target, contract=ts).accepted is True
    assert check_proof_artifact(ts_off_target, contract=ts).accepted is False


def test_a_backwards_oracle_is_refused_whatever_the_exception_is_called() -> None:
    """The claim is that the code WRONGLY raises. A test asserting that it raises asserts the bug
    as correct behaviour: it passes while the bug is present, and its exit status means the
    opposite of a reproduction."""
    from core.agent_runtime.continuity_gate import BACKWARDS_ORACLE, check_proof_artifact, contract_from_finding

    contract = contract_from_finding(
        title="A missing locale raises KeyError instead of falling back",
        file="app/i18n/catalog.py",
        cited_line_text="return self._catalog[locale]",
        line_start=17,
        line_end=18,
        failure_scenario=(
            "translate('xx-YY') raises KeyError for an unknown locale rather than returning the "
            "default string, and the request crashes."
        ),
    )
    backwards = (
        "class TestLocale(unittest.TestCase):\n"
        "    def test_unknown_locale(self):\n"
        "        with self.assertRaises(KeyError):\n"
        "            subject.Catalog().translate('xx-YY')\n"
    )
    correct = (
        "class TestLocale(unittest.TestCase):\n"
        "    def test_an_unknown_locale_falls_back_to_the_default(self):\n"
        "        self.assertEqual(subject.Catalog().translate('xx-YY'), 'default')\n"
    )
    verdict = check_proof_artifact(backwards, contract=contract)
    assert verdict.accepted is False
    assert verdict.reason_code == BACKWARDS_ORACLE
    assert check_proof_artifact(correct, contract=contract).accepted is True


def test_an_artifact_that_asserts_both_directions_settles_nothing() -> None:
    from core.agent_runtime.continuity_gate import (
        CONTRADICTORY_ORACLES,
        check_proof_artifact,
        contract_from_finding,
    )

    contract = contract_from_finding(
        title="An unparsable duration is accepted as zero",
        file="ops/schedule.py",
        cited_line_text="return int(raw or 0)",
        line_start=9,
        line_end=10,
        failure_scenario=(
            "parse_duration('later') returns an incorrect 0 instead of rejecting the value, so a "
            "job is scheduled to run immediately."
        ),
    )
    both_ways = (
        "class TestDuration(unittest.TestCase):\n"
        "    def test_unparsable_duration_raises(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            subject.parse_duration('later')\n"
        "    def test_unparsable_duration_returns_zero(self):\n"
        "        self.assertEqual(subject.parse_duration('later'), 0)\n"
    )
    verdict = check_proof_artifact(both_ways, contract=contract)
    assert verdict.accepted is False
    assert verdict.reason_code == CONTRADICTORY_ORACLES


def test_a_source_screened_claim_is_not_reported_as_disproved_by_a_test() -> None:
    """Live on the daemon 2026-08-01: a READ-ONLY turn that ran no test at all told the operator
    "that claim was already disproved this session by a proof test that passed". A claim the source
    challenge screened out and a claim an execution disproved are different facts, and the operator
    reads this sentence."""
    rejected = json.dumps(
        {
            "verdict": "refuted",
            "reason": "The cited line does not do what the scenario describes.",
            "counterexample": "The pop is unconditional; there is no branch to skip.",
        }
    )
    decision, _router, tools = _drive(
        [REVOKE_FINDING, rejected, REVOKE_FINDING, REVOKE_FINDING, REVOKE_FINDING],
        session_id="screened-label",
    )
    report = _report(decision).lower()

    assert "sandbox.run_command" not in tools.intents(), "no test ran, so nothing was disproved"
    assert "disproved this session by a proof test" not in report
    assert "adversarial check against the cited source" in report


def test_a_bad_input_and_a_good_input_are_not_a_contradiction() -> None:
    """The contradiction rule must compare oracles on the SAME input. Asserting that a malformed
    record raises and that a valid one round-trips is one correct test of one claim — reading the
    shared constructor as a conflict would reject the best artifact the model can write."""
    from core.agent_runtime.continuity_gate import check_proof_artifact, contract_from_finding

    contract = contract_from_finding(
        title="A malformed record is silently accepted",
        file="codec/decode.py",
        cited_line_text="except: break",
        line_start=10,
        line_end=11,
        failure_scenario=(
            "decode(b'\\x80') returns a short buffer and raises nothing, so data is silently lost."
        ),
    )
    two_sided = (
        "class TestDecode(unittest.TestCase):\n"
        "    def test_a_malformed_record_is_rejected(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            subject.Codec().decode(b'\\x80')\n"
        "    def test_a_valid_record_still_round_trips(self):\n"
        "        self.assertEqual(subject.Codec().decode(b'ok'), b'ok')\n"
    )

    assert check_proof_artifact(two_sided, contract=contract).accepted is True


def test_a_reworded_claim_is_the_same_claim() -> None:
    from core.agent_runtime.continuity_gate import claim_signature, same_claim

    original = claim_signature(
        "A zero ttl issues a token that is already expired",
        "issue(token, 0, now) stores expires equal to now, so the next check rejects it.",
    )
    reworded = claim_signature(
        "Tokens issued with a ttl of zero expire immediately",
        "Issuing with zero ttl stores an expiry equal to now, and the following check rejects the token.",
    )
    unrelated = claim_signature(
        "Revoking a token that was never issued reports success",
        "revoke() pops an absent key and returns True.",
    )

    assert same_claim(reworded, original) is True
    assert same_claim(unrelated, original) is False


@pytest.mark.parametrize(
    "wording",
    [
        "Prove it.",
        "Show me that's actually broken.",
        "Write the smallest regression test, run it, and paste the exact command.",
        "Reproduce the defect you named, then show me the output.",
        "Create a deterministic regression test for that and execute it.",
    ],
)
def test_a_plainly_worded_proof_request_authorizes_the_test(wording: str) -> None:
    """Driven live 2026-08-01: "Write the smallest regression test, run it" authorized nothing,
    because the permission pattern wanted `write test` adjacent and one adjective hid the request.
    The runtime bound the follow-up to the finding and then refused to prove it — understanding
    the operator and denying them in the same turn."""
    from core.agent_runtime.audit_policy import READ_ONLY_AUDIT, derive_execution_policy

    policy = derive_execution_policy(wording, prior=READ_ONLY_AUDIT)

    assert policy.proof_authorized is True, wording
    assert policy.write_scope == "proof_artifacts_only"
    assert policy.model_substitution is False, "a proof request never authorizes another model"


@pytest.mark.parametrize(
    "wording",
    [
        "Look over the ledger and tell me the highest-risk bug. Do not modify anything.",
        "Read-only review of this file, please.",
        "What does this module do?",
    ],
)
def test_an_analysis_request_still_authorizes_nothing(wording: str) -> None:
    from core.agent_runtime.audit_policy import READ_ONLY_AUDIT, derive_execution_policy

    policy = derive_execution_policy(wording, prior=READ_ONLY_AUDIT)

    assert policy.proof_authorized is False, wording
    assert policy.repository_write is False
    assert policy.repository_test_creation is False


@pytest.mark.parametrize(
    ("wording", "action"),
    [
        ("prove it", "reproduce"),
        ("Show me that's actually broken.", "reproduce"),
        ("now patch it", "fix"),
        ("fix that bug", "fix"),
        ("continue", "continue"),
        ("explain it before you touch anything", "explain"),
        ("what is the capital of Lithuania", "none"),
        ("write me a CSV importer", "none"),
    ],
)
def test_follow_up_wordings_classify_the_way_a_reader_would(wording: str, action: str) -> None:
    assert classify_follow_up(wording).action == action
