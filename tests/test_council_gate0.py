"""Gate 0: the deterministic stop every task passes before ANY model call.

Gate 0 is not advice and it is not a prompt: it is a pure decision over typed facts,
evaluated in a FIXED check order, and the orchestrator consults it before it
dispatches a single seat. A refused task must spend ZERO model calls — proven here
by driving the REAL council state machine with a counting seat turn, one prohibited
contract per stop reason, and asserting the counter never leaves zero.
"""

from __future__ import annotations

import pytest

from core.council.cost_ladder import (
    CostPolicy,
    SpendCeilings,
    SpendMeter,
    mint_operator_escalation,
)
from core.council.gate0 import (
    GATE0_CHECK_ORDER,
    Gate0,
    Gate0Check,
    Gate0Decision,
    Gate0Facts,
    Gate0Reason,
)
from core.council.model_provenance import ModelIdentity
from core.council.orchestrator import CouncilOrchestrator, default_bench
from core.council.task_contract import (
    MutationPermission,
    TaskContract,
    TaskSeat,
    mint_mutation_grant,
)
from core.council.task_dag import TaskStatus, WriterLease

BASE_SHA = "a" * 40
CAND_SHA = "b" * 40
REPO = "/repo/checkouts/main"
CEILINGS = SpendCeilings(max_calls=4, max_tokens=100_000, max_cost_usd=0.50,
                         wall_clock_seconds=900.0)

GPT = ModelIdentity("openai", "gpt-4o", harness="app-chat")
CLAUDE = ModelIdentity("anthropic", "claude-3-5-sonnet", harness="app-chat")
GEMINI = ModelIdentity("google", "gemini-2.0-flash", harness="openrouter")
ALL_ROLES = frozenset({
    "builder", "falsifier", "reviewer", "verifier", "adjudicator",
})


def make_contract(**overrides):
    fields = {
        "task_id": "T1",
        "repo_root": REPO,
        "base_sha": BASE_SHA,
        "candidate_sha": CAND_SHA,
        "objective": "Repair the council seat leak",
        "writable_scope": ("core/council",),
        "forbidden_scope": ("core/updater", ".git"),
        "dependencies": (),
        "authority_owner": "operator:sls_0x",
        "done_condition": "the containment tests pass",
        "verification": "pytest tests/test_council_seat_containment.py",
        "counterexample": "a fenced seat still writes via a symlinked parent",
        "evidence_path": "evidence/T1.jsonl",
        "cost_policy": CostPolicy(ceilings=CEILINGS),
        "mutation": MutationPermission.WORKSPACE_WRITE,
        "seats": (
            TaskSeat("seat-w", "builder", GPT),
            TaskSeat("seat-r1", "reviewer", CLAUDE),
            TaskSeat("seat-r2", "falsifier", GEMINI),
        ),
    }
    fields.update(overrides)
    return TaskContract(**fields)


def make_facts(**overrides):
    fields = {
        "repo_root": REPO,
        "head_sha": BASE_SHA,
        "dirty_paths": frozenset(),
        "task_status": TaskStatus.PENDING,
        "dependency_statuses": {},
        "authority_owners": frozenset({"operator:sls_0x"}),
        "provider_availability": {"openai": True, "anthropic": True, "google": True},
        "role_support": {
            GPT.key: ALL_ROLES,
            CLAUDE.key: ALL_ROLES,
            GEMINI.key: ALL_ROLES,
        },
        "live_leases": (),
        "spend_meter": SpendMeter(CEILINGS),
        "evidence_root_writable": True,
        "mutation_grants": (mint_mutation_grant("T1"),),
    }
    fields.update(overrides)
    return Gate0Facts(**fields)


class TestTheHappyPath:
    def test_a_clean_contract_passes_with_every_check_green(self):
        decision = Gate0(make_facts()).evaluate(make_contract())
        assert decision.allowed is True
        assert decision.reason is None
        assert [c.name for c in decision.checks] == list(GATE0_CHECK_ORDER)
        assert all(c.passed for c in decision.checks)

    def test_the_decision_carries_the_seat_envelopes_for_the_model_door(self):
        decision = Gate0(make_facts()).evaluate(make_contract())
        envelopes = {e.seat_id: e for e in decision.seat_envelopes}
        assert envelopes["seat-w"].mutation is MutationPermission.WORKSPACE_WRITE
        assert envelopes["seat-r1"].mutation is MutationPermission.READ_ONLY


class TestEveryStop:
    def test_wrong_repo_stops(self):
        decision = Gate0(make_facts()).evaluate(make_contract(repo_root="/repo/other"))
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.WRONG_REPO

    def test_wrong_base_sha_stops(self):
        decision = Gate0(make_facts(head_sha="c" * 40)).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.WRONG_BASE_SHA

    def test_a_dirty_owned_tree_stops(self):
        facts = make_facts(dirty_paths=frozenset({"core/council/seats.py"}))
        decision = Gate0(facts).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.DIRTY_OWNED_TREE
        assert "core/council/seats.py" in decision.detail

    def test_a_dirty_tree_outside_the_owned_scope_does_not_stop(self):
        facts = make_facts(dirty_paths=frozenset({"docs/README.md"}))
        decision = Gate0(facts).evaluate(make_contract())
        assert decision.allowed is True

    def test_an_already_complete_task_stops(self):
        decision = Gate0(make_facts(task_status=TaskStatus.COMPLETE)).evaluate(
            make_contract()
        )
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.TASK_ALREADY_COMPLETE

    def test_an_incomplete_dependency_stops_and_names_it(self):
        facts = make_facts(
            dependency_statuses={"T0": TaskStatus.PENDING.value}
        )
        decision = Gate0(facts).evaluate(make_contract(dependencies=("T0",)))
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.DEPENDENCY_BLOCKED
        assert "T0" in decision.detail

    def test_a_missing_dependency_stops(self):
        facts = make_facts(dependency_statuses={})
        decision = Gate0(facts).evaluate(make_contract(dependencies=("ghost",)))
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.DEPENDENCY_BLOCKED

    def test_complete_dependencies_do_not_stop(self):
        facts = make_facts(
            dependency_statuses={"T0": TaskStatus.COMPLETE.value}
        )
        decision = Gate0(facts).evaluate(make_contract(dependencies=("T0",)))
        assert decision.allowed is True

    def test_an_unknown_authority_owner_stops(self):
        decision = Gate0(make_facts()).evaluate(
            make_contract(authority_owner="who:unknown")
        )
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.UNKNOWN_AUTHORITY_OWNER

    def test_an_overlapping_writer_stops(self):
        lease = WriterLease(
            lease_id="lease-9", task_id="T9", scope=("core/council/runtime.py",),
            holder="daemon-x", fencing_counter=1, acquired_at=1000.0, expires_at=1600.0,
        )
        decision = Gate0(make_facts(live_leases=(lease,))).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.OVERLAPPING_WRITER
        assert "T9" in decision.detail

    def test_an_overlapping_lease_does_not_stop_a_read_only_task(self):
        lease = WriterLease(
            lease_id="lease-9", task_id="T9", scope=("core/council",),
            holder="daemon-x", fencing_counter=1, acquired_at=1000.0, expires_at=1600.0,
        )
        contract = make_contract(mutation=MutationPermission.READ_ONLY, writable_scope=())
        decision = Gate0(make_facts(live_leases=(lease,))).evaluate(contract)
        assert decision.allowed is True

    def test_a_disjoint_lease_does_not_stop(self):
        lease = WriterLease(
            lease_id="lease-9", task_id="T9", scope=("core/updater",),
            holder="daemon-x", fencing_counter=1, acquired_at=1000.0, expires_at=1600.0,
        )
        decision = Gate0(make_facts(live_leases=(lease,))).evaluate(make_contract())
        assert decision.allowed is True

    def test_an_unavailable_provider_stops(self):
        facts = make_facts(provider_availability={"openai": True, "anthropic": False,
                                                  "google": True})
        decision = Gate0(facts).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.PROVIDER_UNAVAILABLE

    def test_an_unknown_provider_fails_closed(self):
        facts = make_facts(provider_availability={"openai": True, "anthropic": True})
        decision = Gate0(facts).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.PROVIDER_UNAVAILABLE

    def test_an_unsupported_model_role_stops(self):
        # The gemini seat is the falsifier; a support map without that role for
        # gemini must stop the task before any seat is dispatched.
        role_support = {
            GPT.key: ALL_ROLES,
            CLAUDE.key: ALL_ROLES,
            GEMINI.key: ALL_ROLES - {"falsifier"},
        }
        decision = Gate0(make_facts(role_support=role_support)).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.UNSUPPORTED_MODEL_ROLE

    def test_a_model_missing_from_the_support_map_fails_closed(self):
        role_support = {GPT.key: ALL_ROLES, CLAUDE.key: ALL_ROLES}
        decision = Gate0(make_facts(role_support=role_support)).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.UNSUPPORTED_MODEL_ROLE

    def test_an_exhausted_budget_stops(self):
        meter = SpendMeter(SpendCeilings(max_calls=1, max_tokens=10,
                                         max_cost_usd=1.0, wall_clock_seconds=60.0))
        meter.record_usage(tokens=11)  # over the token ceiling
        decision = Gate0(make_facts(spend_meter=meter)).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.BUDGET_EXHAUSTED

    def test_a_wall_clock_exhausted_budget_stops(self):
        now = {"t": 0.0}
        meter = SpendMeter(
            SpendCeilings(max_calls=10, max_tokens=10 ** 6, max_cost_usd=10.0,
                          wall_clock_seconds=30.0),
            clock=lambda: now["t"],
        )
        now["t"] = 31.0
        decision = Gate0(make_facts(spend_meter=meter)).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.BUDGET_EXHAUSTED

    def test_write_permission_without_an_operator_grant_stops(self):
        decision = Gate0(make_facts(mutation_grants=())).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.FORBIDDEN_PERMISSION

    def test_a_grant_for_another_task_does_not_authorize_this_one(self):
        decision = Gate0(
            make_facts(mutation_grants=(mint_mutation_grant("T-other"),))
        ).evaluate(make_contract())
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.FORBIDDEN_PERMISSION

    def test_a_premium_escalation_for_another_task_stops(self):
        policy = CostPolicy(
            ceilings=CEILINGS, escalation=mint_operator_escalation(task_id="T-other")
        )
        decision = Gate0(make_facts()).evaluate(make_contract(cost_policy=policy))
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.FORBIDDEN_PERMISSION

    def test_an_unwritable_evidence_destination_stops(self):
        decision = Gate0(make_facts(evidence_root_writable=False)).evaluate(
            make_contract()
        )
        assert decision.allowed is False
        assert decision.reason is Gate0Reason.MISSING_EVIDENCE_DESTINATION


class TestDeterminism:
    def test_same_facts_same_contract_same_decision(self):
        gate = Gate0(make_facts())
        contract = make_contract()
        first = gate.evaluate(contract)
        second = gate.evaluate(contract)
        assert first == second

    def test_the_first_failing_check_wins_and_the_order_is_fixed(self):
        # Two violations at once: the reason reported is the one that comes FIRST
        # in the fixed check order, so identical inputs can never yield a coin
        # flip between reasons.
        facts = make_facts(head_sha="c" * 40, dirty_paths=frozenset({"core/council/x.py"}))
        decision = Gate0(facts).evaluate(make_contract())
        assert decision.reason is Gate0Reason.WRONG_BASE_SHA
        names = [c.name for c in decision.checks]
        assert names == list(GATE0_CHECK_ORDER)[: len(names)]
        assert names[-1] == "base_sha"

    def test_the_decision_row_is_ledger_shaped(self):
        decision = Gate0(make_facts()).evaluate(make_contract())
        row = decision.as_row()
        assert row["allowed"] is True
        assert row["reason"] is None
        assert len(row["checks"]) == len(GATE0_CHECK_ORDER)


class TestGate0RefusesToSpend:
    """Prohibited tasks spend ZERO model calls — through the real orchestrator."""

    @pytest.fixture()
    def isolated_store(self, tmp_path, monkeypatch):
        def _patched(*parts):
            base = tmp_path / "data"
            base.mkdir(parents=True, exist_ok=True)
            out = base
            for part in parts:
                out = out / part
            return out

        monkeypatch.setattr("core.council.run_store.data_path", _patched)
        return tmp_path

    @staticmethod
    def _counting_seat_turn(calls):
        def turn(seat, prompt, round_no, run_id):
            calls.append((seat.seat_id, round_no))
            return {"text": "DIAGNOSIS: all good\nVERDICT: AGREE", "receipt_count": 0}

        return turn

    def _run(self, isolated_store, contract, facts):
        calls: list = []
        orchestrator = CouncilOrchestrator(
            problem="repair the seat leak",
            seats=default_bench(""),
            seat_turn=self._counting_seat_turn(calls),
            task_contract=contract,
            task_gate=Gate0(facts),
        )
        return orchestrator.run(), calls, orchestrator

    def test_every_stop_reason_spends_zero_model_calls(self, isolated_store):
        meter = SpendMeter(CEILINGS)
        meter.record_usage(tokens=CEILINGS.max_tokens + 1)  # pre-exhausted budget
        cases = [
            (make_contract(repo_root="/repo/other"), make_facts()),
            (make_contract(), make_facts(head_sha="c" * 40)),
            (make_contract(), make_facts(dirty_paths=frozenset({"core/council/x.py"}))),
            (make_contract(), make_facts(task_status=TaskStatus.COMPLETE)),
            (
                make_contract(dependencies=("T0",)),
                make_facts(dependency_statuses={"T0": "pending"}),
            ),
            (make_contract(authority_owner="who:unknown"), make_facts()),
            (
                make_contract(),
                make_facts(
                    live_leases=(
                        WriterLease(
                            lease_id="l9", task_id="T9", scope=("core/council",),
                            holder="daemon-x", fencing_counter=1,
                            acquired_at=1000.0, expires_at=1600.0,
                        ),
                    )
                ),
            ),
            (
                make_contract(),
                make_facts(provider_availability={"openai": True, "anthropic": True,
                                                  "google": False}),
            ),
            (
                make_contract(),
                make_facts(role_support={GPT.key: ALL_ROLES, CLAUDE.key: ALL_ROLES}),
            ),
            (make_contract(), make_facts(spend_meter=meter)),
            (make_contract(), make_facts(mutation_grants=())),
            (make_contract(), make_facts(evidence_root_writable=False)),
        ]
        for contract, facts in cases:
            outcome, calls, orchestrator = self._run(isolated_store, contract, facts)
            assert outcome["result"] == "gate0_refused", outcome
            assert calls == [], f"gate0 refusal spent model calls for {outcome}"
            assert facts.spend_meter.snapshot()["calls_used"] == 0

    def test_the_refusal_is_recorded_in_the_run_ledger(self, isolated_store):
        outcome, calls, orchestrator = self._run(
            isolated_store, make_contract(), make_facts(mutation_grants=())
        )
        events = orchestrator.store.read_events()
        gate_rows = [row for row in events if row["type"] == "gate0_evaluated"]
        assert len(gate_rows) == 1
        assert gate_rows[0]["allowed"] is False
        assert gate_rows[0]["reason"] == Gate0Reason.FORBIDDEN_PERMISSION.value

    def test_an_allowed_contract_proceeds_normally(self, isolated_store):
        outcome, calls, orchestrator = self._run(
            isolated_store, make_contract(), make_facts()
        )
        assert outcome["result"] == "adjudicated", outcome
        assert calls, "an allowed task must dispatch its seats"
        events = orchestrator.store.read_events()
        gate_rows = [row for row in events if row["type"] == "gate0_evaluated"]
        assert gate_rows[0]["allowed"] is True

    def test_a_gate_without_a_contract_is_a_construction_error(self, isolated_store):
        with pytest.raises(Exception):
            CouncilOrchestrator(
                problem="p", seats=default_bench(""), seat_turn=lambda *a: {},
                task_gate=Gate0(make_facts()),
            )

    def test_a_contract_without_a_gate_is_a_construction_error(self, isolated_store):
        with pytest.raises(Exception):
            CouncilOrchestrator(
                problem="p", seats=default_bench(""), seat_turn=lambda *a: {},
                task_contract=make_contract(),
            )
