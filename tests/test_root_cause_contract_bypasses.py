"""ROOT-CAUSE CONTRACT — AMENDMENT: the three production bypasses, RED at HEAD.

The first delivery proved the contract exists and governs the orchestrated
envelope lane. The amendment closes the three bypasses found live and by audit:

* GAP 1 (approval continuation): a repair approved in Manual mode executes
  through the permission-controller continuation — that lane must open/recover
  the same typed diagnosis, record patch + validation evidence into it, and
  publish the derived status on the served result.
* GAP 2 (restart durability): diagnosis identity and evidence must survive a
  daemon/process restart through the existing durable runtime store
  (storage.db) — the in-memory registry alone is a bypass.
* GAP 3 (cumulative regression writer): a repair must be able to reach
  ``root_cause_repaired`` through the REAL executor, with a recurrence
  identity and a DISTINCT cumulative-suite identity both recorded from real
  receipts.

Every test here is RED at 0dde0e3b (the seams do not exist); each names its
cause. Rule 5 stays load-bearing: an approved edit with no validation anywhere
opens no diagnosis, and ordinary turns keep byte-identical commits.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

try:
    from core import root_cause_contract as rcc
except ImportError:  # pragma: no cover
    rcc = None

from core.request_trust import RESERVED_TRUST_KEYS


def mod():
    assert rcc is not None, "core.root_cause_contract missing at this tree"
    return rcc


@pytest.fixture(autouse=True)
def _isolated_diagnosis_state(tmp_path):
    """Isolated durable store + empty registry per test (a fresh process).

    Attribute-guarded so every test fails on ITS OWN missing seam at the
    pre-amendment HEAD rather than on a fixture error.
    """
    import storage.db as sdb
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "rcc_amendment.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    _reset = getattr(rcc, "reset_diagnosis_registry_for_tests", None)
    _clear = getattr(rcc, "clear_turn_diagnosis_binding", None)
    if callable(_reset):
        _reset()
    if callable(_clear):
        _clear()
    yield
    sdb.configure_default_db_path(None)
    if callable(_reset):
        _reset()
    if callable(_clear):
        _clear()
    from core.conductor import obligation_ledger as ol

    ol.clear_active_set()
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()


def _simulate_restart(m) -> None:
    """A new process: the in-memory registry is empty; only the durable store
    remains. The binding clear is the seam the turn spine owns; guarded so the
    RED at pre-amendment HEAD names the durability gap, not a missing attr."""
    m.reset_diagnosis_registry_for_tests()
    clear = getattr(m, "clear_turn_diagnosis_binding", None)
    if callable(clear):
        clear()


def _approval_ctx(workspace: str, *, token: str = "tok-1", with_batch: bool = False) -> dict:
    """The turn context of an approval-resumed continuation turn.

    `with_batch` carries the controller's pending batch — the CONCRETE plan
    that makes the turn repair-shaped (mutation + validation), exactly as the
    tool loop stamps it before dispatch.
    """
    ctx = {
        "workspace": workspace,
        "runtime_session_id": "sess-amend",
        "mode_approval_token": token,
    }
    if with_batch:
        ctx["pending_batch_calls"] = [
            {"intent": "workspace.replace_in_file", "arguments": dict(REPLACE_ARGS)},
            {"intent": "workspace.run_tests", "arguments": {"command": "python3 -m pytest -q test_app.py"}},
        ]
    return ctx


REPLACE_ARGS = {
    "path": "app.py",
    "old_text": "return 41",
    "new_text": "return 42",
    "replace_all": True,
}


def _seed_workspace(tmpdir: str, *, extra_red_test: bool = False) -> str:
    Path(tmpdir, "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
    Path(tmpdir, "test_app.py").write_text(
        "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
        encoding="utf-8",
    )
    if extra_red_test:
        Path(tmpdir, "test_broken.py").write_text(
            "def test_broken():\n    assert False\n", encoding="utf-8"
        )
    return tmpdir


# --------------------------------------------------------------------------
# GAP 1 — the approval continuation opens/recovers the diagnosis and publishes
# --------------------------------------------------------------------------


def test_approved_repair_continuation_records_and_publishes():
    """The continuation executes replace + validation through the permission
    lane; the diagnosis must open, take seam evidence + recurrence, reach
    verified, and publish through the derived status."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        _seed_workspace(tmpdir)
        ctx = _approval_ctx(tmpdir, with_batch=True)
        mutation = m.record_repair_step(
            ctx,
            intent="workspace.replace_in_file",
            arguments=dict(REPLACE_ARGS),
            ok=True,
        )
        assert mutation is not None, "approved mutation opened no diagnosis fact"
        validation = m.record_repair_step(
            ctx,
            intent="workspace.run_tests",
            arguments={"command": "python3 -m pytest -q test_app.py"},
            ok=True,
        )
        assert validation is not None, "validation never bound the repair diagnosis"
        contract = m.current_contract(ctx)
        assert contract is not None, "approval continuation opened no diagnosis"
        assert contract.hypothesis_source == "user_declared"
        assert contract.has_seam_evidence(), "approved patch never became seam evidence"
        assert contract.recurrence_result is not None and contract.recurrence_result.passed
        assert contract.state == m.ROOT_CAUSE_VERIFIED
        assert contract.operator_status() == "Root cause verified"


def test_gate_opens_the_diagnosis_before_the_approved_mutation_executes():
    """The amendment's letter: at the REQUIRE_APPROVAL gate, a repair-shaped
    mutation (its own plan carries a validation) opens the durable diagnosis
    BEFORE anything executes — and an ordinary gated edit opens nothing."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        _seed_workspace(tmpdir)
        ctx = _approval_ctx(tmpdir, with_batch=True)
        ctx.pop("mode_approval_token")  # the gate runs in the ORIGINAL turn
        opened = m.note_gated_repair(
            ctx, intent="workspace.replace_in_file", arguments=dict(REPLACE_ARGS)
        )
        assert opened is not None, "the gate did not open the repair diagnosis"
        assert opened.diagnosis_id.startswith("rc:")
        # Ordinary gated edit, in the NEXT turn (the spine cleared the
        # binding at turn end): no validation anywhere in its plan.
        m.clear_turn_diagnosis_binding()
        plain = _approval_ctx(tmpdir)
        plain.pop("mode_approval_token")
        plain["pending_batch_calls"] = [
            {"intent": "workspace.write_file", "arguments": {"path": "notes.txt"}}
        ]
        assert m.note_gated_repair(
            plain, intent="workspace.write_file", arguments={"path": "notes.txt"}
        ) is None


def test_continuation_stash_survives_a_context_copy_at_seal():
    """The seal reads the turn context it holds — a diagnosis opened deeper in
    the turn (loop-context copies) must still reach the seal via the
    turn-scoped binding, not only via the context key."""
    from core.agent_runtime.agent import _seal_semantic_result

    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        _seed_workspace(tmpdir)
        loop_ctx = _approval_ctx(tmpdir)
        m.record_repair_step(
            loop_ctx,
            intent="workspace.replace_in_file",
            arguments=dict(REPLACE_ARGS),
            ok=True,
        )
        m.record_repair_step(
            loop_ctx,
            intent="workspace.run_tests",
            arguments={"command": "python3 -m pytest -q test_app.py"},
            ok=True,
        )
        # The seal sees the TURN's context — a different dict that never
        # carried the reserved key (the loop worked on its own merge copy).
        turn_ctx: dict = {"surface": "openclaw", "session_id": "sess-amend"}
        sealed = _seal_semantic_result(
            {"response": "Repaired and verified.", "route_reason": "probe"},
            session_id="sess-amend",
            user_input="allow the repair",
            source_context=turn_ctx,
        )
        stashed = sealed.get("_root_cause_contract")
        assert isinstance(stashed, dict), "seal lost the diagnosis opened inside the turn"
        assert stashed["state"] == rcc.ROOT_CAUSE_VERIFIED


def test_turn_binding_does_not_leak_across_turns():
    """Rule 5 across turns: the spine clears the turn-scoped diagnosis binding
    when the turn ends, so a later ordinary turn in the same process opens
    nothing by adjacency."""
    m = mod()
    assert hasattr(m, "clear_turn_diagnosis_binding"), (
        "no turn-scoped diagnosis binding exists to clear at turn end"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        _seed_workspace(tmpdir)
        ctx = _approval_ctx(tmpdir)
        m.record_repair_step(
            ctx, intent="workspace.replace_in_file", arguments=dict(REPLACE_ARGS), ok=True
        )
        m.record_repair_step(
            ctx,
            intent="workspace.run_tests",
            arguments={"command": "python3 -m pytest -q test_app.py"},
            ok=True,
        )
        # The turn ends; the spine clears the binding; the next turn's fresh
        # context must resolve NO diagnosis.
        m.clear_turn_diagnosis_binding()
        assert m.current_contract({"runtime_session_id": "sess-amend"}) is None


def test_ordinary_approved_edit_opens_no_diagnosis():
    """Rule 5 in the approval lane: a mutation with no validation anywhere in
    the turn is an ordinary edit, not a repair — no diagnosis."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = _approval_ctx(tmpdir)
        fact = m.record_repair_step(
            ctx,
            intent="workspace.replace_in_file",
            arguments={"path": "groceries.txt", "old_text": "milk", "new_text": "oat milk"},
            ok=True,
        )
        assert fact is None or m.current_contract(ctx) is None, (
            "an ordinary approved edit opened a repair diagnosis"
        )


# --------------------------------------------------------------------------
# GAP 2 — durability across restart
# --------------------------------------------------------------------------


def _simulate_restart(m) -> None:
    """A new process: the in-memory registry is empty; only the durable store
    and the turn-scoped binding reset remain."""
    m.reset_diagnosis_registry_for_tests()
    m.clear_turn_diagnosis_binding()


def test_diagnosis_identity_and_evidence_survive_restart():
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        _seed_workspace(tmpdir)
        ctx = _approval_ctx(tmpdir, with_batch=True)
        m.record_repair_step(
            ctx, intent="workspace.replace_in_file", arguments=dict(REPLACE_ARGS), ok=True
        )
        before = m.current_contract(ctx)
        assert before is not None
        assert before.has_seam_evidence()
        original_id = before.diagnosis_id
        _simulate_restart(m)
        recovered = m.recover_diagnosis(original_id)
        assert recovered is not None, "diagnosis did not survive the restart"
        assert recovered.diagnosis_id == original_id
        assert recovered.to_dict() == before.to_dict(), (
            "recovered record differs from the record opened before the restart"
        )


def test_pending_mutation_survives_restart_and_binds_later_validation():
    """The cross-turn repair: the approved mutation executes in one process
    (durably stashed — its plan carried no validation this turn), the daemon
    restarts, and the validation in a NEW process opens and completes the SAME
    diagnosis, seam evidence intact."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        _seed_workspace(tmpdir)
        first_ctx = _approval_ctx(tmpdir, token="tok-replace")
        stashed = m.record_repair_step(
            first_ctx, intent="workspace.replace_in_file", arguments=dict(REPLACE_ARGS), ok=True
        )
        assert stashed is None, (
            "a mutation whose own turn plans no validation must not open the diagnosis"
        )
        _simulate_restart(m)
        # New process, new turn: the validation confirms repair shape and
        # binds the stashed mutation into the durable diagnosis.
        second_ctx = _approval_ctx(tmpdir, token="tok-validate")
        bound = m.record_repair_step(
            second_ctx,
            intent="workspace.run_tests",
            arguments={"command": "python3 -m pytest -q test_app.py"},
            ok=True,
        )
        assert bound is not None, "post-restart validation recovered no diagnosis"
        assert bound.has_seam_evidence(), "pre-restart patch evidence lost"
        assert bound.recurrence_result is not None and bound.recurrence_result.passed
        assert bound.state == m.ROOT_CAUSE_VERIFIED
        # Identity is content-derived and restart-stable: the same approved
        # change in a fresh process resumes THIS diagnosis, not a new one.
        _simulate_restart(m)
        third_ctx = _approval_ctx(tmpdir, token="tok-retry", with_batch=True)
        resumed = m.record_repair_step(
            third_ctx,
            intent="workspace.replace_in_file",
            arguments=dict(REPLACE_ARGS),
            ok=True,
        )
        assert resumed is not None
        assert resumed.diagnosis_id == bound.diagnosis_id, (
            "the retried repair minted a second diagnosis for the same problem"
        )


def test_durable_record_refuses_forged_completion():
    """The durable store is read back through the same wire-integrity law: a
    hand-edited durable row claiming repaired without the triple is refused."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        _seed_workspace(tmpdir)
        ctx = _approval_ctx(tmpdir, with_batch=True)
        m.record_repair_step(
            ctx, intent="workspace.replace_in_file", arguments=dict(REPLACE_ARGS), ok=True
        )
        original = m.current_contract(ctx)
        forged = {**original.to_dict(), "state": m.ROOT_CAUSE_REPAIRED}
        with pytest.raises(m.RootCauseContractError):
            m.validate_publication(forged)


# --------------------------------------------------------------------------
# GAP 3 — a production writer for distinct cumulative regression
# --------------------------------------------------------------------------


SUITE_REQUEST = (
    "tests are failing. replace `return 41` with `return 42` in app.py, "
    "then run `python3 -m pytest -q test_app.py`, then run the tests"
)


def _plan_repair(tmpdir: str, text: str) -> tuple[dict, dict]:
    from core.execution.planner import plan_tool_workflow
    from core.mode_permission_policy import set_active_mode

    session = "repair-" + Path(tmpdir).name
    set_active_mode(session, "auto")
    ctx = {"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir,
           "session_id": session, "runtime_session_id": session, "operating_mode": "auto"}
    decision = plan_tool_workflow(
        user_text=text, task_class="debugging", executed_steps=[], source_context=ctx
    )
    assert decision.handled, "fixture drift: the repair request no longer plans"
    assert decision.next_payload["intent"] == "orchestration.execute_envelope"
    return ctx, decision.next_payload["arguments"]


def test_suite_request_schedules_a_distinct_regression_child():
    """The planner must schedule a regression verifier whose validation
    identity is DISTINCT from the recurrence test when the request names a
    broader suite."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _seed_workspace(tmpdir)
        _ctx, arguments = _plan_repair(tmpdir, SUITE_REQUEST)
        subtasks = [
            str(item.get("task_id") or "")
            for item in arguments["task_envelope"]["inputs"]["subtasks"]
        ]
        assert any(tid.startswith("regression-verify-") for tid in subtasks), (
            f"no regression child scheduled: {subtasks}"
        )


def test_full_repair_reaches_repaired_through_the_real_executor():
    """GREEN path: real planner, real envelope executor, real subprocess
    validations — recurrence AND a distinct suite green — repaired reached
    through production, not constructed."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        _seed_workspace(tmpdir)
        ctx, arguments = _plan_repair(tmpdir, SUITE_REQUEST)
        from core.runtime_execution_tools import execute_runtime_tool

        result = execute_runtime_tool(
            "orchestration.execute_envelope", dict(arguments), source_context=ctx
        )
        assert result.ok, result.details.get("envelope_result")
        contract = m.current_contract(ctx)
        assert contract is not None
        assert contract.recurrence_result is not None and contract.recurrence_result.passed
        assert contract.regression_result is not None, (
            "the suite child's green receipt never became regression evidence"
        )
        assert contract.regression_result.passed
        assert (
            contract.regression_result.ref != contract.recurrence_result.ref
        ), "regression identity is the recurrence test recycled"
        assert contract.root_repair.strip(), "the applied patch was never declared as the repair"
        assert contract.state == m.ROOT_CAUSE_REPAIRED, (
            f"real executor could not reach repaired: {contract.state}"
        )
        assert contract.operator_status() == "Root cause repaired"


def test_failed_suite_blocks_repaired_status():
    """A red wider suite keeps the diagnosis at verified — completion refused
    from real receipts, not constructed ones."""
    m = mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        _seed_workspace(tmpdir, extra_red_test=True)
        ctx, arguments = _plan_repair(tmpdir, SUITE_REQUEST)
        from core.runtime_execution_tools import execute_runtime_tool

        result = execute_runtime_tool(
            "orchestration.execute_envelope", dict(arguments), source_context=ctx
        )
        contract = m.current_contract(ctx)
        assert contract is not None
        if contract.regression_result is not None:
            assert contract.regression_result.passed is False
        assert contract.state != m.ROOT_CAUSE_REPAIRED, (
            "a red cumulative suite still completed the repair"
        )
        assert contract.operator_status() == "Root cause verified"
        # the honest record: repair declared, verification held, completion refused
        assert result is not None


# --------------------------------------------------------------------------
# Controls — ordinary turns stay untouched
# --------------------------------------------------------------------------


def test_reserved_keys_still_strip_the_contract_and_pending_facts():
    assert "root_cause_contract" in RESERVED_TRUST_KEYS


def test_ordinary_turn_commit_stays_byte_identical_in_shape():
    """No diagnosis anywhere in the process → no root-cause keys on the commit
    (the first pack's control, restated for the amendment's seams)."""
    from core.finalization import finalize_answer

    commit = finalize_answer(
        turn_id="plain-amend",
        canonical_content="The capital of Uruguay is Montevideo.",
        closure={"covered": True, "open_count": 0, "set_version": "v1"},
    )
    assert "root_cause" not in commit
    assert commit["turn_result"].get("root_cause_state", "") == ""
