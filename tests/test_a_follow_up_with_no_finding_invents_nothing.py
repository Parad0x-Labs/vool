"""A referential follow-up with no finding behind it does nothing at all.

The incident: an audit finished and said `No checkable finding` — three candidates had failed
source verification, so the authoritative state was NO_FINDING with no active finding. The operator
replied "Prove the bug you identified before fixing it", and the runtime nominated a fresh
candidate, generated a regression test for it, wrote it, ran it, and scored the result. Nothing the
operator asked about existed, and the reply never said so.

The subject here is deliberately a different project, a different language surface and a different
class of defect from the incident fixture, because the property under test is general: a pronoun
resolves against structured state, and when that state says there is no referent, the turn stops
BEFORE any model or tool call. Nothing here reads a filename, a provider name or a bug class.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.agent_runtime.active_finding import (
    FindingScope,
    active_finding_for,
    clear_active_findings,
)
from core.agent_runtime.audit_session import clear_audit_capsules
from core.agent_runtime.stepped_audit import run_stepped_audit

# --------------------------------------------------------------------------------------
# A fresh subject: a bounded retry scheduler. Its defects are arithmetic and boundary
# defects, unrelated to parsing, decompression or session bookkeeping.
# --------------------------------------------------------------------------------------

_BACKOFF_LINES = [
    "#!/usr/bin/env python3",
    '"""Bounded retry with exponential backoff."""',
    "import time",
    "",
    "MAX_ATTEMPTS = 5",
    "BASE_DELAY = 0.25",
    "",
    "",
    "class RetrySchedule:",
    "    def __init__(self, max_attempts=MAX_ATTEMPTS, base_delay=BASE_DELAY):",
    "        self.max_attempts = max_attempts",
    "        self.base_delay = base_delay",
    "        self._attempt = 0",
    "",
    "    def next_delay(self):",
    "        delay = self.base_delay * (2 ** self._attempt)",
    "        self._attempt += 1",
    "        return delay",
    "",
    "    def exhausted(self):",
    "        return self._attempt > self.max_attempts",
    "",
    "    def reset(self):",
    "        self._attempt = 0",
]
BACKOFF = "\n".join(_BACKOFF_LINES)
BACKOFF_TARGET = "svc/net/backoff_retry.py"

_EXHAUSTED_LINE = "        return self._attempt > self.max_attempts"
_DELAY_LINE = "        delay = self.base_delay * (2 ** self._attempt)"
EXHAUSTED_LINE_NO = _BACKOFF_LINES.index(_EXHAUSTED_LINE) + 1
DELAY_LINE_NO = _BACKOFF_LINES.index(_DELAY_LINE) + 1

MANIFEST = SimpleNamespace(provider_id="local-ollama", model_name="qwen3-coder:30b")

AUDIT_PROMPT = f"Audit {BACKOFF_TARGET} and tell me the highest-risk real bug. Do not modify anything."
PROVE_THE_BUG = "Prove the bug you identified before fixing it."


def _nomination(*, title, line_no, line_text, scenario):
    return json.dumps(
        {
            "title": title,
            "file": BACKOFF_TARGET,
            "line_start": line_no,
            "line_end": line_no,
            "cited_line_text": line_text,
            "failure_scenario": scenario,
        }
    )


# Three checkable-looking candidates. Each one is nominated cleanly and then knocked down by the
# adversarial source check, which is exactly the shape of the incident turn.
CANDIDATE_ONE = _nomination(
    title="An off-by-one bound allows one retry past the limit",
    line_no=EXHAUSTED_LINE_NO,
    line_text=_EXHAUSTED_LINE,
    scenario=(
        "With max_attempts of 5 the sixth call still returns a delay, so the caller emits an "
        "incorrect extra request after the budget is spent."
    ),
)
CANDIDATE_TWO = _nomination(
    title="The delay overflows into an unbounded sleep",
    line_no=DELAY_LINE_NO,
    line_text=_DELAY_LINE,
    scenario=(
        "After thirty attempts the doubling returns a delay of hours, so the caller hangs and the "
        "queued work is silently dropped."
    ),
)
CANDIDATE_THREE = _nomination(
    title="reset leaves the recorded delay stale",
    line_no=DELAY_LINE_NO,
    line_text=_DELAY_LINE,
    scenario=(
        "reset() restores the counter but the previously computed delay is reused, so the next "
        "wait is incorrect and a record is lost."
    ),
)

_REFUTED = json.dumps(
    {
        "verdict": "refuted",
        "reason": "the cited source does not perform the operation the scenario depends on",
        "counterexample": "the counter is compared, not indexed",
    }
)

# What the model WOULD have written on turn two, if it were ever asked. Its presence in the script
# is the point: a correct run never consumes it.
UNRELATED_TEST_FILE = (
    "class TestMalformedLines(unittest.TestCase):\n"
    "    def test_a_malformed_line_is_rejected(self):\n"
    "        self.assertFalse(subject.RetrySchedule().exhausted())\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)


class _ScriptedRouter:
    """Replies in order, tagged by step, and records every request it was given."""

    def __init__(self, replies, *, manifest=MANIFEST):
        self.replies = list(replies)
        self.requests = []
        self.manifest = manifest

    def _requested_model_manifest(self, _context):
        return self.manifest

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        self.requests.append(request)
        if not self.replies:
            return (None, None, "script_exhausted")
        item = self.replies.pop(0)
        return (
            None,
            SimpleNamespace(
                output_text=str(item),
                usage={"prompt_tokens": 3100, "completion_tokens": 190},
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


class _ToolRunner:
    def __init__(self):
        self.calls = []

    def intents(self):
        return [intent for intent, _args in self.calls]

    def __call__(self, payload, **kwargs):
        intent = str(payload.get("intent") or "")
        self.calls.append((intent, dict(payload.get("arguments") or {})))
        if intent == "sandbox.run_command":
            return SimpleNamespace(
                ok=False,
                handled=True,
                response_text="Ran 1 test\n\nFAILED (failures=1)",
                details={"returncode": 1},
                status="executed",
                mode="tool_executed",
            )
        return SimpleNamespace(
            ok=True, handled=True, response_text="", details={}, status="executed",
            mode="tool_executed",
        )


def _context(*, project_id="proj-backoff", chat_id="chat-backoff"):
    return {
        "project_id": project_id,
        "chat_id": chat_id,
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {
            "all_paths": (BACKOFF_TARGET, "README.md"),
            "inspected_paths": (BACKOFF_TARGET,),
            "sources": {BACKOFF_TARGET: BACKOFF},
            "workspace_root": "/tmp/backoff-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }


def _drive(replies, *, prompt, session_id="backoff-1", context=None, tool_runner=None):
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
        task=SimpleNamespace(task_id="task-backoff"),
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


def _audit_that_finds_nothing(session_id="backoff-1", context=None):
    """Turn one: three candidates nominated, all three refuted by source checking."""
    return _drive(
        [
            CANDIDATE_ONE, _REFUTED,
            CANDIDATE_TWO, _REFUTED,
            CANDIDATE_THREE, _REFUTED,
        ],
        prompt=AUDIT_PROMPT,
        session_id=session_id,
        context=context,
    )


# --------------------------------------------------------------------------------------
# The authoritative state after an audit that survived nothing
# --------------------------------------------------------------------------------------


def test_an_audit_with_no_surviving_candidate_records_no_finding() -> None:
    decision, _router, _tools = _audit_that_finds_nothing()

    assert _stepped(decision)["terminal_state"] == "no_finding"
    row = active_finding_for(FindingScope(project_id="proj-backoff", chat_id="chat-backoff"))
    assert row is not None, "the audit's conclusion must be recorded, not inferred from prose"
    assert row.finding_status == "no_finding"
    assert row.finding_id == "", "NO_FINDING must leave no active finding id"


# --------------------------------------------------------------------------------------
# The incident itself
# --------------------------------------------------------------------------------------


def test_prove_the_bug_after_no_finding_calls_no_model_and_writes_nothing() -> None:
    _audit_that_finds_nothing()

    _decision, router, tools = _drive(
        [CANDIDATE_ONE, UNRELATED_TEST_FILE, "a synthesis nobody should see"],
        prompt=PROVE_THE_BUG,
        session_id="backoff-1",
    )

    assert router.requests == [], f"a referent that does not exist was manufactured: {router.steps()}"
    assert tools.intents() == [], f"an unrelated artifact reached the workspace: {tools.intents()}"
    assert "workspace.write_file" not in tools.intents()


def test_prove_the_bug_after_no_finding_says_there_is_nothing_to_reproduce() -> None:
    _audit_that_finds_nothing()

    decision, _router, _tools = _drive(
        [CANDIDATE_ONE, UNRELATED_TEST_FILE],
        prompt=PROVE_THE_BUG,
        session_id="backoff-1",
    )
    report = _report(decision)

    lowered = report.lower()
    assert "nothing to reproduce" in lowered
    assert "didn't identify a verified bug" in lowered or "did not identify a verified bug" in lowered
    # It must not describe a bug, a candidate or a test run of its own.
    assert "off-by-one" not in lowered
    assert "unproven candidate" not in lowered
    assert "proof run" not in lowered


@pytest.mark.parametrize(
    "wording",
    [
        "prove it",
        "Prove the bug.",
        "reproduce it",
        "test that",
        "verify that",
        "show me the failure",
        "show me this actually breaks",
        "fix it",
        "patch it",
    ],
)
def test_every_referential_wording_is_blocked_the_same_way(wording: str) -> None:
    """HOSTILE 7. The binding is a property of the sentence's SHAPE, not of a phrase list — a fix
    that only recognised "prove the bug" would leave eight open doors."""
    _audit_that_finds_nothing()

    decision, router, tools = _drive(
        [CANDIDATE_ONE, UNRELATED_TEST_FILE], prompt=wording, session_id="backoff-1"
    )

    assert router.requests == [], f"`{wording}` manufactured a referent"
    assert tools.intents() == []
    assert "nothing to" in _report(decision).lower()


def test_a_request_to_keep_looking_is_not_blocked() -> None:
    """"Continue" asks for more search, and more search is exactly what a turn with no finding can
    honestly offer. Blocking it would make the gate refuse the one follow-up that still makes
    sense."""
    from core.agent_runtime.active_finding import FindingScope as _Scope
    from core.agent_runtime.follow_up_gate import gate_follow_up

    _audit_that_finds_nothing()
    scope = _Scope(project_id="proj-backoff", chat_id="chat-backoff")

    assert gate_follow_up("continue", scope).blocked is False
    assert gate_follow_up("keep going", scope).blocked is False
    assert gate_follow_up("prove it", scope).blocked is True


def test_an_ordinary_sentence_in_a_scope_with_no_conclusion_is_not_claimed() -> None:
    """The mirror-image overreach: "fix it" in a chat that never ran an audit is about whatever the
    operator was just doing, and the gate has no opinion on it."""
    from core.agent_runtime.active_finding import FindingScope as _Scope
    from core.agent_runtime.follow_up_gate import gate_follow_up

    decision = gate_follow_up("fix it", _Scope(project_id="proj-unrelated", chat_id="chat-new"))

    assert decision.blocked is False
    assert decision.message == ""
    assert decision.reason_code == "no_recorded_conclusion"


# --------------------------------------------------------------------------------------
# HOSTILE 2/3 — a finding that DOES exist keeps the follow-up bound to itself
# --------------------------------------------------------------------------------------

_SUPPORTED = json.dumps(
    {
        "verdict": "supported",
        "reason": "The cited comparison is the bound the scenario describes.",
        "counterexample": "the sixth call still returns a delay.",
    }
)

# Exercises the input the claim names (max_attempts of 5) and asserts the CORRECT contract, so it
# fails while the claimed defect is present.
BUDGET_TEST_FILE = (
    "class TestRetryBudget(unittest.TestCase):\n"
    "    def test_the_schedule_reports_exhausted_after_its_last_attempt(self):\n"
    "        schedule = subject.RetrySchedule(max_attempts=5)\n"
    "        for _ in range(5):\n"
    "            schedule.next_delay()\n"
    "        self.assertTrue(schedule.exhausted())\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)
GREEN_RUN = "test_it ... ok\n\nRan 1 test in 0.001s\n\nOK"


class _GreenToolRunner(_ToolRunner):
    """A proof run that PASSES — which disproves the claim it was written to prove."""

    def __call__(self, payload, **kwargs):
        intent = str(payload.get("intent") or "")
        self.calls.append((intent, dict(payload.get("arguments") or {})))
        if intent == "sandbox.run_command":
            return SimpleNamespace(
                ok=True, handled=True, response_text=GREEN_RUN,
                details={"returncode": 0}, status="executed", mode="tool_executed",
            )
        return SimpleNamespace(
            ok=True, handled=True, response_text="", details={}, status="executed",
            mode="tool_executed",
        )


def _audit_with_a_standing_candidate(session_id="backoff-2", context=None):
    return _drive(
        [CANDIDATE_ONE, _SUPPORTED],
        prompt=AUDIT_PROMPT,
        session_id=session_id,
        context=context if context is not None else _context(chat_id=session_id),
    )


def test_a_standing_candidate_is_referable_and_carries_an_id() -> None:
    _audit_with_a_standing_candidate()
    row = active_finding_for(FindingScope(project_id="proj-backoff", chat_id="backoff-2"))

    assert row is not None
    assert row.finding_status == "candidate"
    assert row.finding_id, "a referable finding must be addressable"
    assert row.is_open is True


def test_fix_it_stays_bound_to_the_finding_that_was_reported() -> None:
    """HOSTILE 3. The repair request must be about the same claim, not a fresh nomination."""
    first, _router, _tools = _audit_with_a_standing_candidate()
    bound_id = _stepped(first)["active_finding"]["finding_id"]

    decision, router, _tools = _drive(
        [CANDIDATE_TWO],  # a DIFFERENT candidate, queued so a re-nomination would be visible
        prompt="Fix it.",
        session_id="backoff-2",
        context=_context(chat_id="backoff-2"),
    )

    assert "nominate" not in router.steps(), "the repair request re-opened the search"
    assert _stepped(decision)["active_finding_id"] == bound_id
    # 2026-08-06: the candidate's own title no longer reaches chat for CANDIDATE_UNPROVEN (the
    # concise NOT-PROVEN contract) -- checked against the structured record instead; the negative
    # check (the OTHER candidate's title must never appear) still holds directly against chat.
    assert "off-by-one" in str(_stepped(decision).get("finding", {}).get("title", "")).lower()
    assert "overflows into an unbounded sleep" not in _report(decision).lower()


# --------------------------------------------------------------------------------------
# HOSTILE 5 — a claim its own reproduction disproved does not come back
# --------------------------------------------------------------------------------------


def test_a_disproved_claim_is_not_resurrected_by_asking_again() -> None:
    _audit_with_a_standing_candidate(session_id="backoff-3", context=_context(chat_id="backoff-3"))
    _drive(
        [BUDGET_TEST_FILE, "ERROR:no further candidates"],
        prompt="Prove it.",
        session_id="backoff-3",
        context=_context(chat_id="backoff-3"),
        tool_runner=_GreenToolRunner(),
    )
    row = active_finding_for(FindingScope(project_id="proj-backoff", chat_id="backoff-3"))
    assert row is not None and row.finding_status == "rejected"

    decision, router, tools = _drive(
        [CANDIDATE_ONE, BUDGET_TEST_FILE],
        prompt="Prove it again.",
        session_id="backoff-3",
        context=_context(chat_id="backoff-3"),
        tool_runner=_GreenToolRunner(),
    )
    report = _report(decision).lower()

    assert router.requests == [], "a disproved claim was proved again"
    assert tools.intents() == []
    assert "no longer standing" in report or "did not survive" in report
    assert "nothing left to reproduce" in report


# --------------------------------------------------------------------------------------
# HOSTILE 6 — a conclusion in one project is not reachable from another
# --------------------------------------------------------------------------------------


def test_project_b_cannot_read_project_as_conclusion() -> None:
    _audit_with_a_standing_candidate(
        session_id="shared-chat", context=_context(project_id="proj-a", chat_id="shared-chat")
    )

    assert active_finding_for(FindingScope(project_id="proj-a", chat_id="shared-chat")) is not None
    assert active_finding_for(FindingScope(project_id="proj-b", chat_id="shared-chat")) is None

    # Same chat id, different project: project A's finding must not answer project B's follow-up.
    decision, router, _tools = _drive(
        [CANDIDATE_TWO, _SUPPORTED],
        prompt="Prove it.",
        session_id="shared-chat",
        context=_context(project_id="proj-b", chat_id="shared-chat"),
    )

    assert "nominate" in router.steps(), "project B inherited a finding it never made"
    assert "off-by-one" not in _report(decision).lower()


# --------------------------------------------------------------------------------------
# HOSTILE 8 — a different language and file type behaves identically
# --------------------------------------------------------------------------------------

_MIGRATION_LINES = [
    "-- 0042_add_billing_account.sql",
    "BEGIN;",
    "",
    "ALTER TABLE invoice",
    "    ADD COLUMN billing_account_id BIGINT;",
    "",
    "UPDATE invoice",
    "   SET billing_account_id = (",
    "         SELECT id FROM billing_account",
    "          WHERE billing_account.customer_id = invoice.customer_id",
    "        LIMIT 1",
    "       );",
    "",
    "ALTER TABLE invoice",
    "    ALTER COLUMN billing_account_id SET NOT NULL;",
    "",
    "COMMIT;",
]
MIGRATION = "\n".join(_MIGRATION_LINES)
MIGRATION_TARGET = "db/migrations/0042_add_billing_account.sql"

MIGRATION_CANDIDATE = json.dumps(
    {
        "title": "A customer with no billing account leaves the column null",
        "file": MIGRATION_TARGET,
        "line_start": 15,
        "line_end": 15,
        "cited_line_text": _MIGRATION_LINES[14],
        "failure_scenario": (
            "An invoice whose customer has no billing_account row keeps a NULL, so the NOT NULL "
            "constraint fails and the migration leaves the table with incorrect data."
        ),
    }
)
MIGRATION_REFUTED = json.dumps(
    {
        "verdict": "refuted",
        "reason": "the transaction rolls back, so no incorrect state is committed",
        "counterexample": "the ALTER fails inside BEGIN/COMMIT",
    }
)


def _sql_context():
    return {
        "project_id": "proj-billing",
        "chat_id": "chat-billing",
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {
            "all_paths": (MIGRATION_TARGET,),
            "inspected_paths": (MIGRATION_TARGET,),
            "sources": {MIGRATION_TARGET: MIGRATION},
            "workspace_root": "/tmp/billing-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }


def test_the_state_machine_is_the_same_for_a_sql_migration() -> None:
    decision, _router, _tools = _drive(
        [MIGRATION_CANDIDATE, MIGRATION_REFUTED, MIGRATION_CANDIDATE, MIGRATION_REFUTED],
        prompt=f"Audit {MIGRATION_TARGET} for the highest-risk bug. Do not modify anything.",
        session_id="billing-1",
        context=_sql_context(),
    )
    assert _stepped(decision)["terminal_state"] == "no_finding"

    follow_up, router, tools = _drive(
        [MIGRATION_CANDIDATE, "irrelevant test file"],
        prompt="Prove the bug you identified before fixing it.",
        session_id="billing-1",
        context=_sql_context(),
    )

    assert router.requests == []
    assert tools.intents() == []
    assert "nothing to reproduce" in _report(follow_up).lower()


def test_a_new_audit_request_is_not_blocked_by_an_old_conclusion() -> None:
    """The mirror-image failure. A sentence that names its own file brought its own subject, so an
    earlier turn that ended with nothing must not refuse it — even though it says "prove the bug"."""
    _audit_that_finds_nothing()

    decision, router, _tools = _drive(
        [CANDIDATE_TWO, _REFUTED],
        prompt=f"Now audit {BACKOFF_TARGET} again and prove the highest-risk bug with a failing test.",
        session_id="backoff-1",
    )

    assert "nominate" in router.steps(), "a fresh audit request was refused as a stale pronoun"
    assert "nothing to reproduce" not in _report(decision).lower()


# --------------------------------------------------------------------------------------
# Two stores answer this question, and each one is pinned on its own
# --------------------------------------------------------------------------------------
#
# The finding row and the audit capsule expire independently, so the block is derived from
# either. A test that only checks the OUTCOME passes while either is intact — and then neither
# is really tested. These two remove one store apiece.


def test_the_finding_row_alone_blocks_when_the_capsule_is_gone() -> None:
    _audit_that_finds_nothing()
    clear_audit_capsules()  # the audit lane forgot; the finding state did not

    decision, router, tools = _drive(
        [CANDIDATE_ONE, UNRELATED_TEST_FILE], prompt=PROVE_THE_BUG, session_id="backoff-1"
    )

    assert router.requests == []
    assert tools.intents() == []
    assert "nothing to reproduce" in _report(decision).lower()


def test_the_capsule_alone_blocks_when_the_finding_row_is_gone() -> None:
    _audit_that_finds_nothing()
    clear_active_findings()  # the finding state expired; the capsule still knows how it ended

    decision, router, tools = _drive(
        [CANDIDATE_ONE, UNRELATED_TEST_FILE], prompt=PROVE_THE_BUG, session_id="backoff-1"
    )

    assert router.requests == [], "the capsule's own conclusion did not stop the substitution"
    assert tools.intents() == []
    assert "nothing to reproduce" in _report(decision).lower()
