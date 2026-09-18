"""The boundary between looking at code and passing verdict on it, locked from both sides.

Every case here is a phrasing that was measured in production on 2026-08-07, not an invented one.
Three fresh project-bound chats asked for ordinary read-only navigation and were answered by a
repository audit that opened 189 of 268 files and reported a target nobody had named; a completed
audit's own follow-ups ("Challenge your own audit", "pick the strongest remaining finding") were
classified as brand-new requests and fell through to a demo-video tool pointed at a guessed GitHub
URL.

The mutation block at the bottom is the part that matters most. Each mutation puts one repaired
mechanism back the way it was and asserts the paired regression goes red — a test that stays green
against its own sabotage is testing nothing, and several of these guards sit behind other guards
that could otherwise absorb the failure.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from core.agent_runtime import active_finding, investigation_intent, workspace_audit
from core.agent_runtime.active_finding import (
    CHALLENGE,
    FindingScope,
    classify_follow_up,
    clear_active_findings,
    record_no_finding,
)
from core.agent_runtime.audit_claim_verifier import AuditEvidence
from core.agent_runtime.audit_session import (
    AuditSessionCapsule,
    clear_audit_capsules,
    looks_like_audit_continuation,
    save_audit_capsule,
)
from core.agent_runtime.follow_up_gate import follow_up_blocks_execution
from core.agent_runtime.investigation_session import (
    clear_investigation_capsules,
    investigation_follow_up_resumes,
    record_investigation,
    resolved_paths_from_observations,
)
from core.agent_runtime.stepped_audit import _finding_from_unreviewed, _reported_target_path
from core.agent_runtime.workspace_audit import (
    audit_target_in,
    looks_like_code_audit_request,
    maybe_handle_workspace_audit_request,
)

# --- the exact production phrasings -------------------------------------------------------------
PROMPT_A = (
    "Inspect this project read-only. Find the implementation of /api/runtime/version. Tell me: "
    "exact file; exact function; how commit SHA is determined; how dirty=true/false is determined. "
    "Use only this project. Do not use web search. Do not modify anything."
)
PROMPT_B = (
    "Inspect this project read-only. Find the provider circuit-breaker implementation. Tell me: "
    "threshold; cooldown; which failures degrade health; which failures do not. Give exact file "
    "and function names. Do not use web search."
)
PROMPT_C = (
    "Inspect this project read-only. Trace one workspace.write_file request from: tool schema -> "
    "dispatcher -> write permission check -> actual write implementation -> Activity recording -> "
    "rollback ledger. Give exact files and functions in execution order. Do not modify anything."
)
CHALLENGE_TURN = (
    "Challenge your own audit. Take every confirmed finding you just reported and try to falsify "
    "it. Check whether: - you misunderstood the surrounding code; - another guard already prevents "
    "the failure; - the supposed reproduction depends on an unrealistic state; - the issue is "
    "merely theoretical; - the evidence actually proves the claimed severity. Downgrade or remove "
    "anything that does not survive the challenge. Do not modify anything."
)
PROVE_STRONGEST = (
    "Now pick the strongest remaining finding. Prove it as far as the read-only audit policy "
    "allows. I want evidence, not a longer explanation. If the proof fails, errors, is "
    "inconclusive, or contradicts the finding, do not call the issue confirmed."
)
AUDIT_TARGET = "api/apache/liquefy_apache_repetition_v1.py"


@pytest.fixture(autouse=True)
def _clean_state():
    clear_audit_capsules()
    clear_active_findings()
    clear_investigation_capsules()
    yield
    clear_audit_capsules()
    clear_active_findings()
    clear_investigation_capsules()


def _agent():
    """Enough agent for the audit front door: it only needs to build a fast-path result."""
    return SimpleNamespace(
        _fast_path_result=lambda **kw: {"response": kw.get("response", ""), "reason": kw.get("reason", "")}
    )


def _ctx(**extra):
    base = {
        "surface": "channel",
        "workspace": "/tmp/openclaw-skills",
        "workspace_root": "/tmp/openclaw-skills",
        "project_id": "proj_18450d6438fb",
        "_trusted_project_id": "proj_18450d6438fb",
        "chat_id": "chat-1",
        "workspace_binding": "project",
    }
    base.update(extra)
    return base


# ================================================================================================
# TEST A/B/C/D — read-only is not audit
# ================================================================================================
def test_a_inspect_this_project_is_ordinary_investigation() -> None:
    assert looks_like_code_audit_request("Inspect this project.") is False


def test_b_the_safety_qualifier_does_not_change_the_routing_class() -> None:
    """The measured minimal pair. Adding "read-only" used to flip the turn into a stepped audit."""
    plain = looks_like_code_audit_request("Inspect this project.")
    constrained = looks_like_code_audit_request("Inspect this project read-only.")
    assert constrained == plain
    assert constrained is False


def test_c_do_not_modify_is_a_scope_constraint_not_an_audit_signal() -> None:
    assert looks_like_code_audit_request("Inspect this project. Do not modify anything.") is False


def test_d_an_explicit_audit_request_still_opens_the_lane_under_the_same_qualifier() -> None:
    assert looks_like_code_audit_request("Audit this project for bugs. Read-only.") is True


@pytest.mark.parametrize("prompt", [PROMPT_A, PROMPT_B, PROMPT_C])
def test_the_three_production_navigation_prompts_stay_ordinary(prompt: str) -> None:
    assert looks_like_code_audit_request(prompt) is False


@pytest.mark.parametrize(
    "phrasing",
    [
        "find the implementation of X",
        "trace the call path for X",
        "Where is provider health recorded?",
        "Explain how this works from the source.",
        "Inspect these three files.",
    ],
)
def test_navigation_never_opens_the_audit_lane(phrasing: str) -> None:
    assert looks_like_code_audit_request(phrasing) is False


@pytest.mark.parametrize(
    "phrasing",
    [
        "Audit this repository for correctness bugs.",
        "Audit this project for bugs.",
        "audit this code",
        f"Audit this file: {AUDIT_TARGET} I want a serious read-only code audit. Look for real bugs.",
    ],
)
def test_a_requested_verdict_still_opens_the_audit_lane(phrasing: str) -> None:
    assert looks_like_code_audit_request(phrasing) is True


# ================================================================================================
# TEST E — tool names are not file targets
# ================================================================================================
def test_e_a_dotted_tool_name_is_not_an_audit_target() -> None:
    assert audit_target_in(
        "Trace one workspace.write_file request from tool schema through the rollback ledger."
    ) == ""
    assert audit_target_in(PROMPT_C) == ""


def test_e_a_real_path_is_still_extracted() -> None:
    assert audit_target_in(f"Audit this file: {AUDIT_TARGET} please") == AUDIT_TARGET


@pytest.mark.parametrize(
    "token", ["workspace.write_file", "workspace.read_file", "provider.foo_bar", "blob.startswith"]
)
def test_e_structured_names_never_become_pseudo_paths(token: str) -> None:
    assert audit_target_in(f"audit the {token} path for bugs") == ""


# ================================================================================================
# TEST F — generic investigation continuation
# ================================================================================================
def test_f_a_long_form_continuation_is_recognised() -> None:
    """Shape alone settles these two: one carries a back-reference, one an investigation noun.

    "Keep going on the provider health path" deliberately does NOT bind here — it carries neither,
    and is a continuation only against a recorded subject that mentions provider health. That case
    is `test_r1_an_object_naming_the_recorded_subject_binds` below.
    """
    assert classify_follow_up("Continue from the three files we already identified.").is_continuation
    assert classify_follow_up("Continue that investigation.").is_continuation
    assert classify_follow_up("Continue the audit.").is_continuation
    assert classify_follow_up("Resume the previous audit.").is_continuation


@pytest.mark.parametrize(
    "unrelated",
    [
        "I'll continue to use Python for this.",
        "Continue to the next chapter of the story.",
        "prove that you can write Python",
        "demonstrate that the API is faster than the old one",
    ],
)
def test_f_unrelated_turns_are_not_continuations(unrelated: str) -> None:
    assert classify_follow_up(unrelated).is_continuation is False


def test_f_a_continuation_binds_to_the_recorded_investigation_subject() -> None:
    three = ("core/provider_routing.py", "core/model_selection_policy.py", "adapters/registry.py")
    record_investigation(
        session_id="s1",
        project_id="proj",
        chat_id="chat",
        workspace_root="/tmp/ws",
        subject="identify exactly three provider-routing files",
        resolved_paths=three,
    )
    resumed = investigation_follow_up_resumes(
        "Continue from the three files we already identified.",
        session_id="s1",
        project_id="proj",
        chat_id="chat",
    )
    assert resumed is not None
    assert resumed.resolved_paths == three
    assert resumed.subject == "identify exactly three provider-routing files"


def test_f_a_continuation_in_another_project_misses_rather_than_inherits() -> None:
    record_investigation(
        session_id="s1", project_id="proj-a", chat_id="chat",
        resolved_paths=("core/provider_routing.py",),
    )
    assert investigation_follow_up_resumes(
        "Continue from the three files we already identified.",
        session_id="s1", project_id="proj-b", chat_id="chat",
    ) is None


def test_f_a_turn_that_resolved_nothing_records_no_subject() -> None:
    assert record_investigation(session_id="s1", project_id="p", chat_id="c") is None
    assert investigation_follow_up_resumes("continue", session_id="s1", project_id="p", chat_id="c") is None


def test_f_resolved_paths_come_from_tool_observations_not_prose() -> None:
    observations = [
        {"intent": "workspace.read_file", "path": "core/provider_routing.py"},
        {"intent": "workspace.search_text", "paths": ["core/model_selection_policy.py", "adapters/registry.py"]},
        {"intent": "machine.disk_usage"},
    ]
    assert resolved_paths_from_observations(observations) == (
        "core/provider_routing.py",
        "core/model_selection_policy.py",
        "adapters/registry.py",
    )


# ================================================================================================
# TEST G — no synthetic single-file target
# ================================================================================================
def _unscoped_evidence() -> AuditEvidence:
    """A repository-wide sweep, ordered exactly as the real one is: alphabetically."""
    return AuditEvidence(
        inspected_paths=("api/__init__.py", "api/apache/liquefy_apache_v1.py", "api/aws/cloudtrail.py"),
        all_paths=("api/__init__.py", "api/apache/liquefy_apache_v1.py", "api/aws/cloudtrail.py"),
        sources={
            "api/__init__.py": "x = 1\n",
            "api/apache/liquefy_apache_v1.py": "y = 2\n",
            "api/aws/cloudtrail.py": "z = 3\n",
        },
        workspace_root="/tmp/openclaw-skills",
        scoped_target="",
    )


def test_g_an_unscoped_pass_reports_no_target_instead_of_the_first_file() -> None:
    evidence = _unscoped_evidence()
    assert _reported_target_path(evidence, "api/__init__.py") == ""


def test_g_a_scoped_pass_still_reports_its_target() -> None:
    evidence = AuditEvidence(
        inspected_paths=(AUDIT_TARGET,),
        sources={AUDIT_TARGET: "x = 1\n"},
        scoped_target=AUDIT_TARGET,
    )
    assert _reported_target_path(evidence, AUDIT_TARGET) == AUDIT_TARGET


def test_g_the_report_states_repository_scope_when_there_is_no_target() -> None:
    from core.agent_runtime.audit_verdict import BLOCKED, AuditVerdict, render_audit_report

    report = render_audit_report(
        AuditVerdict(state=BLOCKED, target_path="", blocked_reason="nothing to report")
    )
    assert "api/__init__.py" not in report
    assert "whole repository" in report.lower()


# ================================================================================================
# TEST H — an audit continuation resumes instead of re-opening
# ================================================================================================
def _completed_audit_capsule() -> AuditSessionCapsule:
    return save_audit_capsule(
        AuditSessionCapsule(
            session_id="s-audit",
            project_id="proj_18450d6438fb",
            chat_id="chat-1",
            target_path=AUDIT_TARGET,
            workspace_root="/tmp/openclaw-skills",
            terminal_state="no_finding",
            phase="nominated",
            unreviewed=[
                {
                    "title": "decompress() drops the final block on a truncated stream",
                    "file": AUDIT_TARGET,
                    "line_start": 120,
                    "line_end": 134,
                    "cited_line_text": "return bytes(out)",
                    "failure_scenario": "a truncated archive silently returns short output",
                    "harm_class": "integrity",
                }
            ],
            evidence_blob={
                "inspected_paths": (AUDIT_TARGET,),
                "all_paths": (AUDIT_TARGET,),
                "sources": {AUDIT_TARGET: "def decompress():\n    return bytes(out)\n"},
                "workspace_root": "/tmp/openclaw-skills",
                "scoped_target": AUDIT_TARGET,
            },
        )
    )


def test_h_challenge_is_continuation_vocabulary() -> None:
    assert classify_follow_up(CHALLENGE_TURN).action == CHALLENGE
    assert classify_follow_up("Challenge your own audit.").action == CHALLENGE
    assert classify_follow_up("Why did you reject candidate 2?").action == CHALLENGE
    assert looks_like_audit_continuation(CHALLENGE_TURN) is True


def test_h_a_challenge_resumes_the_capsule_rather_than_opening_a_fresh_audit() -> None:
    """The production failure: the opener claimed this turn on the word "audit", lost the target,
    and re-ran nomination three times against `api/__init__.py`."""
    _completed_audit_capsule()
    ctx = _ctx()
    result = maybe_handle_workspace_audit_request(
        _agent(), CHALLENGE_TURN, session_id="s-audit", source_surface="channel", source_context=ctx
    )
    assert result is None
    assert ctx.get("workspace_audit_continuation") is True
    # The resumed evidence is the ORIGINAL target's bytes, not a fresh repository sweep.
    assert tuple(ctx["workspace_audit_evidence"]["inspected_paths"]) == (AUDIT_TARGET,)
    assert ctx["workspace_audit_evidence"]["scoped_target"] == AUDIT_TARGET


def test_h_the_unreviewed_candidate_is_rebuilt_instead_of_renominated() -> None:
    capsule = _completed_audit_capsule()
    finding = _finding_from_unreviewed(capsule.unreviewed[0])
    assert finding is not None
    assert finding.file == AUDIT_TARGET
    assert finding.line_start == 120
    assert "truncated" in finding.failure_scenario


def test_h_a_challenge_with_no_capsule_says_so_instead_of_auditing_something() -> None:
    result = maybe_handle_workspace_audit_request(
        _agent(), CHALLENGE_TURN, session_id="s-fresh", source_surface="channel",
        source_context=_ctx(chat_id="chat-fresh"),
    )
    assert result is not None
    assert result["reason"] == "audit_continuation_without_capsule"
    assert "previous audit" in result["response"]


def test_h_naming_a_new_file_is_a_new_audit_not_a_resume() -> None:
    _completed_audit_capsule()
    ctx = _ctx()
    assert audit_target_in("Audit svc/queue.py for bugs") == "svc/queue.py"
    # A sentence that names its own target must not be diverted into the capsule.
    maybe_handle_workspace_audit_request(
        _agent(), "prove the bug in svc/queue.py", session_id="s-audit",
        source_surface="channel", source_context=ctx,
    )
    assert ctx.get("workspace_audit_continuation") is not True


# ================================================================================================
# Zero-confirmed guard
# ================================================================================================
def test_zero_confirmed_findings_stops_before_any_tool_routing() -> None:
    scope = FindingScope(project_id="proj_18450d6438fb", chat_id="chat-1")
    record_no_finding(scope, target_files=(AUDIT_TARGET,), lane="workspace_audit")
    refusal = follow_up_blocks_execution(PROVE_STRONGEST, scope)
    assert refusal
    assert "nothing to reproduce" in refusal


def test_zero_confirmed_guard_names_no_unrelated_tool() -> None:
    scope = FindingScope(project_id="p", chat_id="c")
    record_no_finding(scope, target_files=(AUDIT_TARGET,), lane="workspace_audit")
    refusal = follow_up_blocks_execution(PROVE_STRONGEST, scope)
    lowered = refusal.lower()
    for stray in ("github", "demo", "video", "product page", "http"):
        assert stray not in lowered


def test_a_recorded_conclusion_is_required_before_the_guard_speaks() -> None:
    """With nothing recorded the gate stands aside — an ordinary "prove it" is not its business."""
    assert follow_up_blocks_execution(PROVE_STRONGEST, FindingScope(project_id="x", chat_id="y")) == ""


# ================================================================================================
# MUTATION TESTS — each puts one repair back the way it was and proves the paired test goes red
# ================================================================================================
def test_mutation_reintroducing_read_only_as_an_audit_signal_breaks_the_minimal_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutated = re.compile(
        workspace_audit._AUDIT_VERDICT_RE.pattern.replace(
            r"vulnerabilit(?:y|ies))", r"vulnerabilit(?:y|ies)|read[\s-]+only)"
        ),
        re.IGNORECASE,
    )
    monkeypatch.setattr(workspace_audit, "_AUDIT_VERDICT_RE", mutated)
    monkeypatch.setattr(
        workspace_audit.investigation_intent if hasattr(workspace_audit, "investigation_intent")
        else investigation_intent,
        "investigation_overrides_audit",
        lambda _text: False,
    )
    assert looks_like_code_audit_request("Inspect this project.") is False
    assert looks_like_code_audit_request("Inspect this project read-only.") is True, (
        "sabotage did not reproduce the bug: the minimal pair must diverge again"
    )


def test_mutation_removing_the_investigation_override_reopens_prompt_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(investigation_intent, "investigation_overrides_audit", lambda _text: False)
    assert looks_like_code_audit_request(PROMPT_B) is True, (
        "sabotage did not reproduce the bug: prompt B must be mis-claimed again"
    )


def test_mutation_reanchoring_continue_breaks_the_long_form_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchored = re.compile(
        r"^\s*(?:please\s+)?(?:continue|carry\s+on|keep\s+going|go\s+on|proceed)\s*[.!?]?\s*$",
        re.IGNORECASE,
    )
    monkeypatch.setattr(
        active_finding, "_continuation_binds_on_shape", lambda body: bool(anchored.search(body))
    )
    assert classify_follow_up("continue").is_continuation is True
    assert classify_follow_up("Continue from the three files we already identified.").is_continuation is False, (
        "sabotage did not reproduce the bug: the whole-string anchor must lose the long form"
    )


def test_mutation_restoring_the_adjacent_only_finding_noun_hides_the_zero_confirmed_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_noun = r"(?:the|that|this|your)\s+(?:bug|finding|issue|defect|problem|report|one)"
    monkeypatch.setattr(
        active_finding,
        "_SHORT_REPRODUCE_RE",
        re.compile(
            rf"^\s*(?:please\s+|now\s+|ok(?:ay)?[,\s]+|go\s+)*{active_finding._REPRODUCE_VERB}\s+"
            rf"(?:{old_noun}|{active_finding._TRAILING_PRONOUN})",
            re.IGNORECASE,
        ),
    )
    monkeypatch.setattr(
        active_finding,
        "_REPRODUCE_RE",
        re.compile(
            rf"\b{active_finding._REPRODUCE_VERB}\b.{{0,60}}?"
            rf"(?:{old_noun}|{active_finding._ATTRIBUTION}|\b{active_finding._TRAILING_PRONOUN})",
            re.IGNORECASE | re.DOTALL,
        ),
    )
    monkeypatch.setattr(
        active_finding,
        "_SELECT_FINDING_RE",
        re.compile(rf"\b(?:pick|select|choose|take|start\s+with)\b[^.!?]{{0,40}}?{old_noun}", re.IGNORECASE),
    )
    scope = FindingScope(project_id="m", chat_id="m")
    record_no_finding(scope, target_files=(AUDIT_TARGET,), lane="workspace_audit")
    assert follow_up_blocks_execution("prove it.", scope), "the control must stay green"
    assert follow_up_blocks_execution(PROVE_STRONGEST, scope) == "", (
        "sabotage did not reproduce the bug: the guard must go silent on the production sentence"
    )


def test_mutation_restoring_the_synthetic_target_reintroduces_the_phantom_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.agent_runtime.stepped_audit as stepped

    monkeypatch.setattr(
        stepped, "_reported_target_path", lambda evidence, target_path: str(target_path or "")
    )
    evidence = _unscoped_evidence()
    working = stepped._target_path_for(evidence, "audit this repository for bugs")
    assert stepped._reported_target_path(evidence, working) == "api/__init__.py", (
        "sabotage did not reproduce the bug: the alphabetically-first file must return as the target"
    )


def test_mutation_removing_the_real_file_contract_reintroduces_the_tool_name_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_audit, "is_real_file_target", lambda candidate: bool(candidate))
    assert audit_target_in(
        "Trace one workspace.write_file request from tool schema through the rollback ledger."
    ), "sabotage did not reproduce the bug: a dotted tool name must be extracted as a target again"


def test_mutation_disabling_investigation_state_loses_the_three_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.agent_runtime.investigation_session as session

    monkeypatch.setattr(session, "load_investigation_capsule", lambda *a, **k: None)
    record_investigation(
        session_id="s1", project_id="proj", chat_id="chat",
        resolved_paths=("core/provider_routing.py",),
    )
    assert investigation_follow_up_resumes(
        "Continue from the three files we already identified.",
        session_id="s1", project_id="proj", chat_id="chat",
    ) is None, "sabotage did not reproduce the bug: the continuation must lose its subject"


def test_a_resumed_audit_does_not_forget_that_it_had_a_subject() -> None:
    """Found on the live acceptance drive, not in a unit test.

    The continuation replayed the correct three files and then headed its report
    `Status: No Issues Found — the workspace`: the capsule's own evidence blob omitted
    `scoped_target`, so the reported target resolved to "" and a scoped audit of one file described
    itself as a clean repository sweep. That is the phantom target mirrored, and worse — the first
    over-claimed a subject, this one over-claims coverage.
    """
    _completed_audit_capsule()
    ctx = _ctx()
    maybe_handle_workspace_audit_request(
        _agent(), CHALLENGE_TURN, session_id="s-audit", source_surface="channel", source_context=ctx
    )
    blob = ctx["workspace_audit_evidence"]
    assert blob.get("scoped_target") == AUDIT_TARGET
    from core.agent_runtime.stepped_audit import _evidence_from_context

    evidence = _evidence_from_context(ctx)
    assert _reported_target_path(evidence, AUDIT_TARGET) == AUDIT_TARGET


# ================================================================================================
# ARGUS R1 — a continue-verb is not a continuation; its OBJECT decides
# ================================================================================================
# Every negative below is run with a REAL audit capsule and a REAL investigation capsule in scope,
# because that is the only state in which the defect showed: with nothing recorded these sentences
# were already harmless, and with something recorded they re-armed the audit lane.
R1_POSITIVES = [
    "Continue from the three files we already identified.",
    "Continue that investigation.",
    "Continue the audit.",
    "Resume the previous audit.",
    "Keep going on the provider health path.",
    "Challenge your own audit.",
    "Why did you reject candidate 2?",
]
R1_NEGATIVES = [
    "Continue the deployment to staging.",
    "Resume the download.",
    "Pick up the groceries.",
    "Carry on with lunch.",
    "Keep going with the workout plan.",
    "Continue installing dependencies.",
    "Resume playback.",
    "Continue the migration tomorrow.",
]


def _recorded_work() -> None:
    """A completed audit AND a completed investigation, both in scope."""
    save_audit_capsule(
        AuditSessionCapsule(
            session_id="r1",
            project_id="proj_18450d6438fb",
            chat_id="chat-r1",
            target_path=AUDIT_TARGET,
            original_request=f"Audit {AUDIT_TARGET} for bugs",
            evidence_blob={
                "sources": {AUDIT_TARGET: "x = 1\n"},
                "inspected_paths": [AUDIT_TARGET],
                "scoped_target": AUDIT_TARGET,
            },
        )
    )
    record_investigation(
        session_id="r1",
        project_id="proj_18450d6438fb",
        chat_id="chat-r1",
        subject="identify three files central to provider routing and where provider health is recorded",
        resolved_paths=("core/provider_routing.py", "core/provider_health.py", "adapters/registry.py"),
    )


def _binds(text: str) -> bool:
    from core.agent_runtime.audit_session import audit_follow_up_resumes

    audit = audit_follow_up_resumes(
        text, session_id="r1", project_id="proj_18450d6438fb", chat_id="chat-r1"
    )
    investigation = investigation_follow_up_resumes(
        text, session_id="r1", project_id="proj_18450d6438fb", chat_id="chat-r1"
    )
    return audit is not None or investigation is not None


@pytest.mark.parametrize("phrasing", R1_POSITIVES)
def test_r1_a_real_continuation_still_binds(phrasing: str) -> None:
    _recorded_work()
    assert _binds(phrasing) is True


@pytest.mark.parametrize("phrasing", R1_NEGATIVES)
def test_r1_an_unrelated_object_does_not_bind_even_with_a_capsule(phrasing: str) -> None:
    _recorded_work()
    assert _binds(phrasing) is False


def test_r1_an_object_naming_the_recorded_subject_binds() -> None:
    """The third route, and the only one that needs state.

    "Keep going on the provider health path" carries no pronoun and no investigation noun, so it
    cannot bind on shape — and it must still bind, because the recorded investigation was about
    provider health. Its twin differs only in what the recorded work was about.
    """
    _recorded_work()
    assert classify_follow_up("Keep going on the provider health path.").is_continuation is False
    assert _binds("Keep going on the provider health path.") is True
    assert _binds("Keep going with the workout plan.") is False


def test_r1_the_same_sentence_does_not_bind_when_nothing_recorded_matches() -> None:
    record_investigation(
        session_id="r1", project_id="proj_18450d6438fb", chat_id="chat-r1",
        subject="list the docker compose services",
        resolved_paths=("docker-compose.yml",),
    )
    assert investigation_follow_up_resumes(
        "Keep going on the provider health path.",
        session_id="r1", project_id="proj_18450d6438fb", chat_id="chat-r1",
    ) is None


def test_r1_a_generic_object_word_is_not_evidence_of_a_subject() -> None:
    """"path" appears in almost any investigation; it must not carry a binding on its own."""
    from core.agent_runtime.active_finding import continuation_refers_to_recorded_work

    assert continuation_refers_to_recorded_work(
        "Keep going on the path", subject="trace the request path", paths=()
    ) is False
    assert continuation_refers_to_recorded_work(
        "Keep going on the provider path", subject="trace the provider request path", paths=()
    ) is True


# ================================================================================================
# ARGUS — the short no-capsule challenge reaches lane-specific recovery
# ================================================================================================
def test_the_short_challenge_with_no_capsule_says_no_audit_is_available() -> None:
    result = maybe_handle_workspace_audit_request(
        _agent(), "Challenge your own audit.", session_id="s-none",
        source_surface="channel", source_context=_ctx(chat_id="chat-none"),
    )
    assert result is not None
    assert result["reason"] == "audit_continuation_without_capsule"
    assert "No previous audit is available to challenge" in result["response"]


def test_the_no_capsule_recovery_runs_no_tools_and_names_no_unrelated_surface() -> None:
    ctx = _ctx(chat_id="chat-none2")
    result = maybe_handle_workspace_audit_request(
        _agent(), "Challenge your own audit.", session_id="s-none2",
        source_surface="channel", source_context=ctx,
    )
    assert ctx.get("workspace_audit_evidence_collected") is not True
    assert ctx.get("workspace_audit_continuation") is not True
    lowered = result["response"].lower()
    for stray in ("github", "demo", "video", "product page", "http"):
        assert stray not in lowered


def test_a_bare_continue_in_a_chat_that_never_audited_is_not_an_audit_recovery() -> None:
    """The recovery must not claim every continue-verb — only ones that continue an AUDIT."""
    from core.agent_runtime.audit_session import audit_specific_continuation

    assert audit_specific_continuation("Challenge your own audit.") is True
    assert audit_specific_continuation("Resume the previous audit.") is True
    assert audit_specific_continuation("continue") is False
    assert audit_specific_continuation("Continue that investigation.") is False
    assert maybe_handle_workspace_audit_request(
        _agent(), "continue", session_id="s-none3",
        source_surface="channel", source_context=_ctx(chat_id="chat-none3"),
    ) is None


# ================================================================================================
# ARGUS R1 mutations
# ================================================================================================
def test_mutation_restoring_broad_continuation_matching_binds_an_unrelated_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-R1 shape: verb + any connective + anything at all."""
    broad = re.compile(
        r"^\s*(?:please\s+|now\s+|ok(?:ay)?[,\s]+)*"
        r"(?:continue|carry\s+on|keep\s+going|go\s+on|proceed|resume|pick\s+up)"
        r"(?:\s*[.!?]|\s*$|\s+(?:from|with|on|where|that|this|those|these|the|our|your|it)\b)",
        re.IGNORECASE,
    )
    monkeypatch.setattr(
        active_finding, "_continuation_binds_on_shape", lambda body: bool(broad.search(body))
    )
    _recorded_work()
    assert _binds("Continue that investigation.") is True, "the control must stay green"
    assert _binds("Continue the deployment to staging.") is True, (
        "sabotage did not reproduce the bug: an unrelated object must re-arm the lane again"
    )


def test_mutation_disabling_recorded_subject_binding_loses_a_real_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.agent_runtime.active_finding as af

    monkeypatch.setattr(af, "continuation_refers_to_recorded_work", lambda *a, **k: False)
    _recorded_work()
    assert _binds("Continue that investigation.") is True, "the control must stay green"
    assert _binds("Keep going on the provider health path.") is False, (
        "sabotage did not reproduce the bug: the state-bound continuation must be lost"
    )


def test_mutation_restoring_the_opener_conjunct_hides_the_short_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-ARGUS gate also required `looks_like_code_audit_request`, which the short form fails."""
    import core.agent_runtime.audit_session as audit_session

    monkeypatch.setattr(
        audit_session,
        "audit_specific_continuation",
        lambda text: bool(
            looks_like_audit_continuation(text) and looks_like_code_audit_request(text)
        ),
    )
    assert maybe_handle_workspace_audit_request(
        _agent(), "Challenge your own audit.", session_id="s-mut",
        source_surface="channel", source_context=_ctx(chat_id="chat-mut"),
    ) is None, "sabotage did not reproduce the bug: the short challenge must miss the recovery"
