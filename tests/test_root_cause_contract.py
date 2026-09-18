"""ROOT-CAUSE DOCTRINE AS A RUNTIME CONTRACT — the typed-law battery.

The operator doctrine ("root causes, not symptom patches; never fabricate
certainty; containment stays labelled containment") lived as prose in agent
instructions, which a model can restate while doing the opposite. This battery
pins the doctrine as a typed contract riding the real production seams:

* ``core.root_cause_contract`` — the ONE authority: states, transitions, the
  completion gate, the diagnosis registry (identity/evidence survival), and the
  wire-integrity validator for published records.
* the finalizer (``core.finalization.finalize_answer``) — where the contract
  becomes commit truth: stamped operator status, refused forged completions.
* the sealing spine (``_seal_semantic_result``) and the transport shim
  (``_response_commit``) — the payload-stash path that carries the contract to
  finalization after ContextVar scope exit, exactly like ``_closure_verdict``.
* the orchestrated repair lane (``core.execution.planner`` +
  ``_execute_task_envelope_intent``) — the production writer: every planned
  repair envelope opens a contract; envelope receipts record seam evidence and
  the recurrence result; nothing is invented.

Rules under test (operator goal, 2026-09-01):
1. a symptom patch can never be reported as a completed root repair;
2. completion requires seam evidence + recurrence test + cumulative regression;
3. an explicitly requested workaround completes as CONTAINMENT, labelled so;
4. unknown causes stay unresolved — no fabricated certainty;
5. ordinary turns carry no contract and no status;
6. retries/resumes preserve diagnosis identity and evidence;
7. the served/API truth derives from the same typed object as the commit;
8. a compact, visible operator status is published.

Every test fails at base b3f5117f (the authority module does not exist there);
each names its cause in the assert message so the RED is attributable.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

try:  # RED at base: the authority module is the deliverable under test.
    from core import root_cause_contract as rcc
except ImportError:  # pragma: no cover - the base-state red
    rcc = None

from core.request_trust import RESERVED_TRUST_KEYS, strip_reserved_trust_keys


def mod():
    """The authority module, or an attributable RED at base."""
    assert rcc is not None, (
        "core.root_cause_contract is missing: the root-cause doctrine is not "
        "yet a runtime contract at this tree"
    )
    return rcc


@pytest.fixture(autouse=True)
def _isolated_diagnosis_registry(tmp_path):
    """Each test starts with an empty diagnosis registry AND an isolated
    durable store — a persisted diagnosis resumes across tests and runs BY
    DESIGN, so the default DB must never be in play here."""
    import storage.db as sdb
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "rcc1.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    if rcc is not None:
        rcc.reset_diagnosis_registry_for_tests()
    yield
    sdb.configure_default_db_path(None)
    if rcc is not None:
        rcc.reset_diagnosis_registry_for_tests()
    from core.conductor import obligation_ledger as ol

    ol.clear_active_set()


@pytest.fixture()
def fresh_store(tmp_path):
    """Storage isolation for the seams that persist (finalization/admission)."""
    import storage.db as sdb
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "rcc.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)
    from core.conductor import obligation_ledger as ol

    ol.clear_active_set()
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()


# --------------------------------------------------------------------------
# Helpers — one problem, one workspace-shaped repair story, varied per family
# --------------------------------------------------------------------------


def _open_contract(ctx, *, problem_key="payroll-rounding", statement=None, seam=""):
    return mod().open_root_cause_scope(
        ctx,
        problem_key=problem_key,
        problem_statement=statement or "payroll payouts round down a cent for EU rows",
        owning_seam=seam or "services/payroll/rounding.py:41",
    )


def _verified_contract(ctx, m=None):
    """The full honest verification path: hypothesis, seam evidence, recurrence."""
    m = m or mod()
    _open_contract(ctx)
    m.set_hypothesis(
        ctx,
        "integer division in apply_fx_rounding truncates before the cent cast",
        source="user_declared",
    )
    m.record_seam_evidence(
        ctx, ref="services/payroll/rounding.py:41", detail="truncating cast observed at the owning seam"
    )
    m.record_recurrence_result(
        ctx, ref="pytest tests/test_payroll_rounding.py::test_cent_rounding", passed=True
    )
    m.advance_state(ctx, m.ROOT_CAUSE_VERIFIED)
    return m.current_contract(ctx)


# --------------------------------------------------------------------------
# Rule 1 + Rule 2 — the completion gate
# --------------------------------------------------------------------------


def test_symptom_only_patch_cannot_reach_root_cause_repaired():
    """Rule 1: containment recorded, nothing verified — repaired must refuse."""
    m = mod()
    ctx: dict = {}
    _open_contract(ctx)
    m.record_containment(ctx, "clamp the payout at the cent boundary in the caller")
    with pytest.raises(m.RootCauseStateError) as err:
        m.advance_state(ctx, m.ROOT_CAUSE_REPAIRED)
    assert "validation" in str(err.value).lower() or "recurrence" in str(err.value).lower()


def test_repair_completion_requires_seam_evidence_recurrence_and_regression():
    """Rule 2: each third of the validation triple is individually load-bearing."""
    m = mod()
    ctx: dict = {}
    contract = _verified_contract(ctx, m)
    assert contract.state == m.ROOT_CAUSE_VERIFIED
    # No cumulative regression yet: completion must refuse.
    with pytest.raises(m.RootCauseStateError):
        m.advance_state(ctx, m.ROOT_CAUSE_REPAIRED)
    m.declare_root_repair(ctx, "replace the truncating cast with decimal ROUND_HALF_UP at the seam")
    m.record_regression_result(
        ctx,
        ref="pytest tests/ -q (payroll + billing + ledger suites)",
        passed=True,
        scope=214,
    )
    m.advance_state(ctx, m.ROOT_CAUSE_REPAIRED)
    assert m.current_contract(ctx).state == m.ROOT_CAUSE_REPAIRED


def test_verification_without_seam_evidence_is_refused():
    """Rule 2: a hypothesis confirmed only in prose never verifies."""
    m = mod()
    ctx: dict = {}
    _open_contract(ctx)
    m.set_hypothesis(ctx, "the cache key omits the tenant id", source="derived")
    m.record_recurrence_result(ctx, ref="pytest tests/test_cache.py::test_tenant_isolation", passed=True)
    with pytest.raises(m.RootCauseStateError):
        m.advance_state(ctx, m.ROOT_CAUSE_VERIFIED)


def test_failed_recurrence_test_blocks_verification():
    """Rule 2: the reproduction still failing means nothing was verified."""
    m = mod()
    ctx: dict = {}
    _open_contract(ctx)
    m.set_hypothesis(ctx, "the retry loop double-decrements the budget", source="derived")
    m.record_seam_evidence(ctx, ref="core/budget.py:88", detail="double decrement at the seam")
    m.record_recurrence_result(ctx, ref="pytest tests/test_budget.py::test_no_double_decrement", passed=False)
    with pytest.raises(m.RootCauseStateError):
        m.advance_state(ctx, m.ROOT_CAUSE_VERIFIED)
    assert m.current_contract(ctx).state != m.ROOT_CAUSE_VERIFIED


def test_failed_cumulative_regression_blocks_repair_but_not_verification():
    """Rule 2: a red wider suite keeps the turn at verified — completion refused."""
    m = mod()
    ctx: dict = {}
    _verified_contract(ctx, m)
    m.declare_root_repair(ctx, "guard the decrement behind the retry token")
    m.record_regression_result(ctx, ref="pytest tests/ -q", passed=False, scope=183)
    with pytest.raises(m.RootCauseStateError):
        m.advance_state(ctx, m.ROOT_CAUSE_REPAIRED)
    assert m.current_contract(ctx).state == m.ROOT_CAUSE_VERIFIED


def test_regression_must_be_broader_than_the_recurrence_test():
    """Rule 2, anti-vacuous: the single reproduction rerun is not a regression suite."""
    m = mod()
    ctx: dict = {}
    _verified_contract(ctx, m)
    m.declare_root_repair(ctx, "replace the truncating cast with decimal ROUND_HALF_UP at the seam")
    # Same single-test identity recycled as the "regression suite": refused.
    m.record_regression_result(
        ctx,
        ref="pytest tests/test_payroll_rounding.py::test_cent_rounding",
        passed=True,
        scope=1,
    )
    with pytest.raises(m.RootCauseStateError):
        m.advance_state(ctx, m.ROOT_CAUSE_REPAIRED)


# --------------------------------------------------------------------------
# Rule 4 — unknown causes stay unresolved
# --------------------------------------------------------------------------


def test_unknown_cause_stays_unresolved_and_cannot_fabricate_verification():
    """Rule 4: no hypothesis, no evidence — verified must refuse, unresolved holds."""
    m = mod()
    ctx: dict = {}
    _open_contract(ctx)
    with pytest.raises(m.RootCauseStateError):
        m.advance_state(ctx, m.ROOT_CAUSE_VERIFIED)
    m.advance_state(ctx, m.UNRESOLVED)
    contract = m.current_contract(ctx)
    assert contract.state == m.UNRESOLVED
    assert contract.operator_status() == "Unresolved"


def test_unresolved_cannot_be_published_as_a_repair():
    """Rule 4 at the wire: an unresolved record never carries repair language."""
    m = mod()
    ctx: dict = {}
    _open_contract(ctx)
    m.advance_state(ctx, m.UNRESOLVED)
    contract = m.current_contract(ctx)
    assert "repair" not in contract.operator_status().lower()
    assert "resolved" not in contract.operator_status().lower().replace("unresolved", "")


# --------------------------------------------------------------------------
# Rule 3 — explicit workaround completes as containment, labelled so
# --------------------------------------------------------------------------


def test_explicit_workaround_completes_as_containment():
    """Rule 3: user-requested workaround completes the turn without repair labels."""
    m = mod()
    ctx: dict = {}
    _open_contract(ctx)
    m.mark_workaround_requested(ctx, evidence_ref="user message: 'just make it work for today'")
    m.record_containment(ctx, "force the EU rows onto the legacy rounding path for this payout run")
    m.advance_state(ctx, m.CONTAINMENT_ONLY)
    contract = m.current_contract(ctx)
    assert contract.state == m.CONTAINMENT_ONLY
    assert contract.workaround_requested is True
    assert contract.operator_status() == "Containment only"


def test_containment_status_never_claims_repair():
    """Rule 1 publication half: the derived status for containment names containment."""
    m = mod()
    ctx: dict = {}
    _open_contract(ctx)
    m.record_containment(ctx, "pin the flaky dependency version in requirements")
    m.advance_state(ctx, m.CONTAINMENT_ONLY)
    status = m.current_contract(ctx).operator_status()
    assert status == "Containment only"
    assert "repair" not in status.lower()


# --------------------------------------------------------------------------
# Rule 6 — retries/resumes preserve identity and evidence
# --------------------------------------------------------------------------


def test_retry_resume_preserves_diagnosis_identity_and_evidence():
    """Rule 6: a fresh turn resuming the same problem keeps id + evidence."""
    m = mod()
    first: dict = {}
    _open_contract(first, problem_key="invoice-double-send")
    m.record_evidence(first, kind="capture", ref="logs/2026-09-01/send.jsonl", detail="two sends 40ms apart")
    original = m.current_contract(first)
    second: dict = {}
    resumed = _open_contract(
        second,
        problem_key="invoice-double-send",
        statement="invoices sent twice since the queue migration",
    )
    assert resumed.diagnosis_id == original.diagnosis_id
    assert any(
        item.ref == "logs/2026-09-01/send.jsonl" for item in resumed.evidence
    ), "resumed diagnosis lost prior evidence"


def test_distinct_problems_get_distinct_diagnosis_ids():
    """Identity is per-problem: a different diagnosis never reuses an id."""
    a: dict = {}
    b: dict = {}
    first = _open_contract(a, problem_key="invoice-double-send")
    second = _open_contract(b, problem_key="payroll-rounding")
    assert first.diagnosis_id != second.diagnosis_id


def test_open_is_idempotent_within_a_turn():
    """Reopening inside one context returns the same diagnosis, not a second one."""
    ctx: dict = {}
    first = _open_contract(ctx, problem_key="payroll-rounding")
    again = _open_contract(ctx, problem_key="payroll-rounding")
    assert again.diagnosis_id == first.diagnosis_id


# --------------------------------------------------------------------------
# Rule 5 — ordinary turns carry no contract
# --------------------------------------------------------------------------


def test_inbound_body_cannot_forge_a_contract():
    """The reserved context key is stripped at the HTTP door like the trust keys."""
    forged = {
        "root_cause_contract": {"state": "root_cause_repaired"},
        "surface": "openclaw",
    }
    assert "root_cause_contract" in RESERVED_TRUST_KEYS
    assert "root_cause_contract" not in strip_reserved_trust_keys(forged)


def test_ordinary_turn_finalizes_without_a_root_cause_section(fresh_store):
    """Rule 5: no contract, no status — the commit is unchanged for plain turns."""
    from core.finalization import finalize_answer

    commit = finalize_answer(
        turn_id="plain-turn",
        canonical_content="The capital of Uruguay is Montevideo.",
        closure={"covered": True, "open_count": 0, "set_version": "v1"},
    )
    assert "root_cause" not in commit
    assert commit["turn_result"].get("root_cause_state", "") == ""
    assert commit["turn_result"].get("root_cause_status", "") == ""


# --------------------------------------------------------------------------
# Rules 7 + 8 — publication: commit truth and served status agree
# --------------------------------------------------------------------------


def test_finalize_stamps_contract_status_into_commit(fresh_store):
    """Rules 7/8: the commit carries the contract and its derived operator status."""
    from core.finalization import finalize_answer

    m = mod()
    ctx: dict = {}
    contract = _verified_contract(ctx, m)
    commit = finalize_answer(
        turn_id="repair-turn",
        canonical_content="Verified at the rounding seam; recurrence test green.",
        closure={"covered": True, "open_count": 0, "set_version": "v1"},
        root_cause=contract.to_dict(),
    )
    assert commit["root_cause"]["state"] == m.ROOT_CAUSE_VERIFIED
    assert commit["root_cause"]["operator_status"] == "Root cause verified"
    assert commit["root_cause"]["diagnosis_id"] == contract.diagnosis_id
    assert commit["turn_result"]["root_cause_state"] == m.ROOT_CAUSE_VERIFIED
    assert commit["turn_result"]["root_cause_status"] == "Root cause verified"


def test_finalize_reads_contract_from_source_context(fresh_store):
    """The turn context is a first-class transport for the contract at the seam."""
    from core.finalization import finalize_answer

    m = mod()
    ctx: dict = {}
    contract = _verified_contract(ctx, m)
    commit = finalize_answer(
        turn_id="ctx-turn",
        canonical_content="Containment applied while verification is pending.",
        source_context=ctx,
        closure={"covered": True, "open_count": 0, "set_version": "v1"},
    )
    assert commit["root_cause"]["diagnosis_id"] == contract.diagnosis_id


def test_forged_repaired_publication_is_refused(fresh_store):
    """Rule 1 at the wire: a fully CONSISTENT-looking 'repaired' record whose
    evidence does not carry the validation triple fails closed.

    The forgery updates state AND operator_status together, so the cheap
    status-vs-state consistency backstop cannot catch it — only the
    requirements re-check inside `validate_publication` can (sabotage S2
    proved the naive forgery left that guard untested).
    """
    from core.finalization import FinalizationRejected, finalize_answer

    m = mod()
    ctx: dict = {}
    contract = _verified_contract(ctx, m)
    forged = {
        **contract.to_dict(),
        "state": m.ROOT_CAUSE_REPAIRED,
        "operator_status": "Root cause repaired",
    }
    with pytest.raises(FinalizationRejected) as err:
        finalize_answer(
            turn_id="forged-turn",
            canonical_content="All fixed.",
            closure={"covered": True, "open_count": 0, "set_version": "v1"},
            root_cause=forged,
        )
    assert "ROOT_CAUSE" in str(err.value)


def test_wire_integrity_refuses_unknown_states(fresh_store):
    """An unknown state string on the wire is refused, never waved through."""
    from core.finalization import FinalizationRejected, finalize_answer

    m = mod()
    ctx: dict = {}
    contract = _verified_contract(ctx, m)
    with pytest.raises(FinalizationRejected):
        finalize_answer(
            turn_id="skew-turn",
            canonical_content="Status unknown.",
            closure={"covered": True, "open_count": 0, "set_version": "v1"},
            root_cause={**contract.to_dict(), "state": "definitely_fixed"},
        )


def test_served_payload_exposes_the_contract_status(fresh_store):
    """Rule 8: the API surface carries the compact status beside the answer."""
    from core.finalization import finalize_answer
    from core.web.api.runtime import ollama_chat_response, openai_chat_response

    m = mod()
    ctx: dict = {}
    contract = _verified_contract(ctx, m)
    commit = finalize_answer(
        turn_id="served-turn",
        canonical_content="Verified at the rounding seam; recurrence green.",
        closure={"covered": True, "open_count": 0, "set_version": "v1"},
        root_cause=contract.to_dict(),
    )
    result = {
        "response": "Verified at the rounding seam; recurrence green.",
        "vool_response_commit": commit,
        "usage_summary": {},
    }
    ollama_payload = ollama_chat_response(result, "qwen3", None)
    assert ollama_payload["root_cause_status"] == "Root cause verified"
    openai_payload = openai_chat_response(dict(result), "qwen3")
    assert openai_payload["root_cause_status"] == "Root cause verified"


# --------------------------------------------------------------------------
# Production seam 1 — the orchestrated repair lane opens the contract
# --------------------------------------------------------------------------


REPAIR_REQUEST = (
    "tests are failing. replace `return 41` with `return 42` in app.py, "
    "then run `python3 -m pytest -q test_app.py`"
)


def _planned_repair_context(tmpdir: str) -> tuple[dict, dict]:
    from core.execution.planner import plan_tool_workflow

    Path(tmpdir, "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
    Path(tmpdir, "test_app.py").write_text(
        "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
        encoding="utf-8",
    )
    ctx = {"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir}
    decision = plan_tool_workflow(
        user_text=REPAIR_REQUEST,
        task_class="debugging",
        executed_steps=[],
        source_context=ctx,
    )
    assert decision.handled, "fixture drift: the repair request no longer plans an envelope"
    assert decision.next_payload["intent"] == "orchestration.execute_envelope"
    return ctx, decision.next_payload["arguments"]


def test_planned_repair_envelope_opens_a_contract():
    """The planner seam: a planned repair envelope mints the typed diagnosis.

    The planner records the user's own request as the DECLARED hypothesis at
    open time (attributed `user_declared` — the runtime never invents a causal
    claim), so the opened state is honestly ``root_cause_hypothesis``.
    """
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx, _arguments = _planned_repair_context(tmpdir)
        contract = m.current_contract(ctx)
        assert contract is not None, "planned repair envelope opened no root-cause contract"
        assert contract.state == m.ROOT_CAUSE_HYPOTHESIS
        assert contract.hypothesis_source == "user_declared"
        assert contract.problem_statement.strip()
        assert contract.owning_seam.strip()
        assert contract.diagnosis_id.startswith("rc:")


def test_replanning_resumes_the_same_diagnosis():
    """A replanned repair (retry) reuses the diagnosis identity, not a new one."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx, _arguments = _planned_repair_context(tmpdir)
        first = m.current_contract(ctx)
        _again = _planned_repair_context(tmpdir)
        assert m.current_contract(ctx).diagnosis_id == first.diagnosis_id


def test_ordinary_planner_turn_opens_no_contract():
    """Rule 5 at the planner seam: a lookup plans no envelope and no contract."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        from core.execution.planner import plan_tool_workflow

        ctx = {"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir}
        plan_tool_workflow(
            user_text="what is the capital of Uruguay?",
            task_class="chat_conversation",
            executed_steps=[],
            source_context=ctx,
        )
        assert m.current_contract(ctx) is None


# --------------------------------------------------------------------------
# Production seam 2 — envelope execution records typed evidence
# --------------------------------------------------------------------------


def test_envelope_execution_records_seam_evidence_and_recurrence():
    """The executor seam: preflight capture + patch receipts + final green land typed."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx, arguments = _planned_repair_context(tmpdir)
        from core.runtime_execution_tools import execute_runtime_tool

        result = execute_runtime_tool(
            "orchestration.execute_envelope",
            dict(arguments),
            source_context=ctx,
        )
        assert result is not None and result.ok, result.details.get("envelope_result")
        contract = m.current_contract(ctx)
        assert contract is not None
        assert any(item.kind == "seam" for item in contract.evidence), (
            "patch receipts never became seam evidence"
        )
        assert contract.recurrence_result is not None and contract.recurrence_result.passed, (
            "preflight-fail -> final-green never became a passed recurrence test"
        )
        assert contract.state == m.ROOT_CAUSE_VERIFIED, (
            f"orchestrated repair should reach verified, got {contract.state}"
        )
        # Rule 2 honesty: no cumulative regression ran, so completion is NOT claimed.
        assert contract.state != m.ROOT_CAUSE_REPAIRED
        assert contract.operator_status() == "Root cause verified"


def test_followup_regression_run_completes_the_repair():
    """The resume story: the wider suite green on the SAME diagnosis completes it."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx, arguments = _planned_repair_context(tmpdir)
        from core.runtime_execution_tools import execute_runtime_tool

        executed = execute_runtime_tool(
            "orchestration.execute_envelope",
            dict(arguments),
            source_context=ctx,
        )
        assert executed.ok
        original = m.current_contract(ctx)
        # A later turn, fresh context, same problem: run the whole suite.
        followup: dict = {"workspace": tmpdir}
        m.record_regression_result(
            followup,
            ref="python3 -m pytest -q tests/",
            passed=True,
            scope=112,
            problem_key=original.diagnosis_id,
        )
        m.declare_root_repair(
            followup,
            "app.answer returned 41; the fix pins it to 42 at the seam",
            problem_key=original.diagnosis_id,
        )
        m.advance_state(followup, m.ROOT_CAUSE_REPAIRED, problem_key=original.diagnosis_id)
        completed = m.current_contract(followup)
        assert completed.diagnosis_id == original.diagnosis_id
        assert completed.state == m.ROOT_CAUSE_REPAIRED
        assert completed.operator_status() == "Root cause repaired"


# --------------------------------------------------------------------------
# Production seam 3 — the seal stash and the transport shim
# --------------------------------------------------------------------------


def test_seal_semantic_result_stashes_the_contract(fresh_store):
    """The payload stash: sealing carries the contract past ContextVar scope exit."""
    from core.agent_runtime.agent import _seal_semantic_result

    m = mod()
    ctx: dict = {"surface": "test", "session_id": "seal-session"}
    contract = _verified_contract(ctx, m)
    sealed = _seal_semantic_result(
        {"response": "Verified at the rounding seam; recurrence green.", "route_reason": "probe"},
        session_id="seal-session",
        user_input="find the payroll rounding bug",
        source_context=ctx,
    )
    stashed = sealed.get("_root_cause_contract")
    assert isinstance(stashed, dict), "sealing dropped the turn's root-cause contract"
    assert stashed["diagnosis_id"] == contract.diagnosis_id
    assert stashed["state"] == m.ROOT_CAUSE_VERIFIED


def test_response_commit_consumes_the_stash(fresh_store):
    """The shim: a buffered turn finalizes from the stash, contract intact."""
    from core.web.api.runtime import _response_commit

    m = mod()
    ctx: dict = {}
    contract = _verified_contract(ctx, m)
    result_payload = {
        "response": "Verified at the rounding seam; recurrence green.",
        "route_reason": "probe",
        "source_context": {},
        "_root_cause_contract": contract.to_dict(),
    }
    commit = _response_commit(result_payload, source_context=None)
    assert commit["root_cause"]["diagnosis_id"] == contract.diagnosis_id
    assert commit["root_cause"]["operator_status"] == "Root cause verified"


# --------------------------------------------------------------------------
# The contract preserves the whole diagnosis record
# --------------------------------------------------------------------------


def test_contract_dict_roundtrip_preserves_the_record():
    """to_dict -> validate_publication reconstructs the typed truth losslessly."""
    m = mod()
    ctx: dict = {}
    _verified_contract(ctx, m)
    m.set_remaining_uncertainty(ctx, "behaviour under negative rounding not exercised")
    m.record_containment(ctx, "clamp payouts at the cent boundary in the caller")
    latest = m.current_contract(ctx)
    rebuilt = m.validate_publication(latest.to_dict())
    assert rebuilt.diagnosis_id == latest.diagnosis_id
    assert rebuilt.state == latest.state
    assert rebuilt.remaining_uncertainty == latest.remaining_uncertainty
    assert list(rebuilt.containment_actions) == list(latest.containment_actions)
