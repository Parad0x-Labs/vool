"""TASK_CONTRACT: the typed task law a council run must satisfy before it spends.

A contract is not a prompt. Every field the operator's spec names — exact base and
candidate SHAs, objective, writable and forbidden scope, dependencies, authority
owner, done condition, verification, counterexample, evidence destination, cost
policy, mutation permission — is a typed field validated at construction, so a
task that cannot state them cannot exist. Seats are ROLES with replaceable model
identities; reviewers are mechanically read-only; a verified counterexample beats
any majority; and task completion, integration green, and VOOL obligation closure
are three DISTINCT certificates that never imply one another.
"""

from __future__ import annotations

import pytest

from core.council.cost_ladder import (
    CostPolicy,
    SpendCeilings,
    mint_operator_escalation,
)
from core.council.model_provenance import ModelIdentity
from core.council.task_contract import (
    CompletionRecord,
    CounterexampleClaim,
    IntegrationGreenRefused,
    MutationPermission,
    MutationProof,
    MutationProofRefused,
    PromotionRefused,
    TaskContract,
    TaskContractError,
    TaskDoneRefused,
    TaskSeat,
    close_obligation,
    close_task,
    mark_integration_green,
    mint_mutation_grant,
    verify_mutation_proof,
)

BASE_SHA = "a" * 40
CAND_SHA = "b" * 40
CEILINGS = SpendCeilings(max_calls=4, max_tokens=100_000, max_cost_usd=0.50,
                         wall_clock_seconds=900.0)

GPT = ModelIdentity("openai", "gpt-4o", harness="app-chat")
CLAUDE = ModelIdentity("anthropic", "claude-3-5-sonnet", harness="app-chat")
GEMINI = ModelIdentity("google", "gemini-2.0-flash", harness="openrouter")

SEATS = (
    TaskSeat("seat-w", "builder", GPT),
    TaskSeat("seat-r1", "reviewer", CLAUDE),
    TaskSeat("seat-r2", "falsifier", GEMINI),
)


def make_contract(**overrides):
    fields = {
        "task_id": "T1",
        "repo_root": "/repo/checkouts/main",
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
        "seats": SEATS,
    }
    fields.update(overrides)
    return TaskContract(**fields)


def make_proof(contract=None, **overrides):
    fields = {
        "task_id": (contract or make_contract()).task_id,
        "touched_paths": ("core/council/containment.py",),
        "diff_sha256": "c" * 64,
        "lease_id": "lease-1",
        "fencing_counter": 1,
    }
    fields.update(overrides)
    return MutationProof(**fields)


class TestContractRequiresEverything:
    @pytest.mark.parametrize(
        "field,null_value",
        [
            ("task_id", ""),
            ("repo_root", ""),
            ("base_sha", ""),
            ("objective", "   "),
            ("authority_owner", ""),
            ("done_condition", ""),
            ("verification", ""),
            ("counterexample", ""),
            ("evidence_path", ""),
            ("cost_policy", None),
            ("seats", ()),
        ],
    )
    def test_missing_required_fields_are_construction_errors(self, field, null_value):
        with pytest.raises(TaskContractError):
            make_contract(**{field: null_value})

    def test_base_sha_must_be_an_exact_hex_sha(self):
        with pytest.raises(TaskContractError):
            make_contract(base_sha="short")
        with pytest.raises(TaskContractError):
            make_contract(base_sha="z" * 40)

    def test_candidate_sha_may_be_pending_or_exact_hex(self):
        assert make_contract(candidate_sha="").candidate_sha == ""
        assert make_contract(candidate_sha=CAND_SHA).candidate_sha == CAND_SHA
        with pytest.raises(TaskContractError):
            make_contract(candidate_sha="deadbeef")

    def test_writable_and_forbidden_scope_must_be_disjoint(self):
        with pytest.raises(TaskContractError):
            make_contract(
                writable_scope=("core/council",),
                forbidden_scope=("core/council/orchestrator.py",),
            )

    def test_write_permission_requires_a_declared_writable_scope(self):
        with pytest.raises(TaskContractError):
            make_contract(mutation=MutationPermission.WORKSPACE_WRITE, writable_scope=())

    def test_evidence_path_must_be_relative_and_unescaping(self):
        with pytest.raises(TaskContractError):
            make_contract(evidence_path="/abs/evidence.jsonl")
        with pytest.raises(TaskContractError):
            make_contract(evidence_path="evidence/../../etc/passwd")

    def test_a_read_only_contract_needs_no_writable_scope(self):
        contract = make_contract(
            mutation=MutationPermission.READ_ONLY, writable_scope=()
        )
        assert contract.mutation is MutationPermission.READ_ONLY

    def test_with_candidate_mints_a_new_contract_immutably(self):
        pending = make_contract(candidate_sha="")
        minted = pending.with_candidate(CAND_SHA)
        assert pending.candidate_sha == ""
        assert minted.candidate_sha == CAND_SHA
        with pytest.raises(TaskContractError):
            minted.with_candidate("nope")

    def test_scope_allows_respects_both_directions(self):
        contract = make_contract()
        assert contract.scope_allows("core/council/seats.py")
        assert not contract.scope_allows("core/updater/updater.py")
        assert not contract.scope_allows("core/council-2/x.py")

    def test_dependencies_are_recorded(self):
        contract = make_contract(dependencies=("T0", "T0b"))
        assert contract.dependencies == ("T0", "T0b")


class TestSeats:
    def test_seat_roles_are_typed_and_models_replaceable(self):
        seat = TaskSeat("seat-w", "builder", GPT)
        assert seat.role_id == "builder"
        swapped = TaskSeat("seat-w", "builder", CLAUDE)
        # Same seat (same role), different model behind it.
        assert seat.role_id == swapped.role_id
        assert seat.model != swapped.model

    def test_unknown_roles_are_refused(self):
        with pytest.raises(TaskContractError):
            make_contract(seats=(TaskSeat("s", "emperor", GPT),))

    def test_duplicate_seat_ids_are_refused(self):
        seats = (
            TaskSeat("seat-1", "builder", GPT),
            TaskSeat("seat-1", "reviewer", CLAUDE),
            TaskSeat("seat-2", "falsifier", GEMINI),
        )
        with pytest.raises(TaskContractError):
            make_contract(seats=seats)

    def test_the_same_model_twice_is_a_duplicate_seat(self):
        seats = (
            TaskSeat("seat-w", "builder", GPT),
            TaskSeat("seat-r1", "reviewer", GPT),
            TaskSeat("seat-r2", "falsifier", GEMINI),
        )
        with pytest.raises(TaskContractError):
            make_contract(seats=seats)

    def test_aliases_of_one_family_do_not_count_as_independent_reviewers(self):
        aliased = ModelIdentity("openai", "gpt-4o-2024-05-13", harness="app-chat")
        seats = (
            TaskSeat("seat-w", "builder", CLAUDE),
            TaskSeat("seat-r1", "reviewer", GPT),
            TaskSeat("seat-r2", "falsifier", aliased),  # same family as GPT
        )
        with pytest.raises(TaskContractError):
            make_contract(seats=seats)

    def test_two_distinct_reviewer_families_are_accepted(self):
        contract = make_contract()
        families = {seat.model.family for seat in contract.seats if seat.role_id != "builder"}
        assert len(families) == 2


class TestReviewersAreMechanicallyReadOnly:
    def test_only_the_builder_seat_ever_holds_write_permission(self):
        contract = make_contract(mutation=MutationPermission.WORKSPACE_WRITE)
        envelopes = {e.seat_id: e for e in contract.seat_envelopes()}
        assert envelopes["seat-w"].mutation is MutationPermission.WORKSPACE_WRITE
        for seat_id, envelope in envelopes.items():
            if seat_id != "seat-w":
                assert envelope.mutation is MutationPermission.READ_ONLY

    def test_reviewers_stay_read_only_even_when_the_contract_grants_write(self):
        # The grant is task-level; the ENVELOPE each seat presents at the effect
        # door is computed from its ROLE. There is no field that can name a
        # reviewer as a writer.
        contract = make_contract(mutation=MutationPermission.WORKSPACE_WRITE)
        for envelope in contract.seat_envelopes():
            if envelope.role_id != "builder":
                assert envelope.mutation is MutationPermission.READ_ONLY

    def test_every_envelope_carries_the_model_identity_and_harness(self):
        contract = make_contract()
        envelopes = {e.seat_id: e for e in contract.seat_envelopes()}
        assert envelopes["seat-r2"].model is GEMINI
        assert envelopes["seat-r2"].model.harness == "openrouter"


class TestMutationProof:
    def test_a_valid_proof_verifies(self):
        contract = make_contract()
        proof = make_proof(contract)
        verified = verify_mutation_proof(
            proof, contract, current_fencing=1, lease_scope=("core/council",)
        )
        assert verified.task_id == "T1"
        assert verified.touched_paths == proof.touched_paths

    def test_paths_outside_the_writable_scope_are_refused(self):
        contract = make_contract()
        proof = make_proof(contract, touched_paths=("core/updater/updater.py",))
        with pytest.raises(MutationProofRefused):
            verify_mutation_proof(proof, contract, current_fencing=1,
                                  lease_scope=("core/council",))

    def test_forbidden_paths_are_refused_even_if_also_writable(self):
        contract = make_contract()
        proof = make_proof(contract, touched_paths=("core/council", ".git/config"))
        with pytest.raises(MutationProofRefused):
            verify_mutation_proof(proof, contract, current_fencing=1,
                                  lease_scope=("core/council",))

    def test_a_stale_lease_cannot_prove_a_mutation(self):
        contract = make_contract()
        proof = make_proof(contract, fencing_counter=1)
        with pytest.raises(MutationProofRefused):
            verify_mutation_proof(proof, contract, current_fencing=2,
                                  lease_scope=("core/council",))

    def test_paths_outside_the_lease_scope_are_refused(self):
        contract = make_contract(writable_scope=("core/council", "docs"))
        proof = make_proof(contract, touched_paths=("docs/report.md",))
        with pytest.raises(MutationProofRefused):
            verify_mutation_proof(proof, contract, current_fencing=1,
                                  lease_scope=("core/council",))

    def test_proof_from_another_task_is_refused(self):
        contract = make_contract()
        proof = make_proof(contract, task_id="T9")
        with pytest.raises(MutationProofRefused):
            verify_mutation_proof(proof, contract, current_fencing=1,
                                  lease_scope=("core/council",))

    def test_a_bad_diff_hash_is_refused(self):
        contract = make_contract()
        # At construction — a proof with a junk hash never exists:
        with pytest.raises(TaskContractError):
            make_proof(contract, diff_sha256="nothex")


class TestCounterexample:
    def test_a_verified_counterexample_requires_a_receipt(self):
        with pytest.raises(TaskContractError):
            CounterexampleClaim(claim="x fails", verified=True, receipt="")
        claim = CounterexampleClaim(claim="x fails", verified=True, receipt="sha256:" + "d" * 64)
        assert claim.verified

    def test_an_unverified_counterexample_is_admitted_as_a_claim(self):
        claim = CounterexampleClaim(claim="hunch", verified=False, receipt="")
        assert not claim.verified


class TestCloseTask:
    def AGREE(self, model):
        return (model, "AGREE")

    def test_a_clean_task_closes_with_its_candidate_and_votes(self):
        contract = make_contract()
        done = close_task(
            contract,
            mutation_proof=make_proof(contract),
            reviewer_verdicts=(self.AGREE(CLAUDE), self.AGREE(GEMINI)),
        )
        assert done.task_id == "T1"
        assert done.candidate_sha == CAND_SHA
        assert done.standing_counterexamples == 0

    def test_a_counterexample_beats_a_unanimous_majority(self):
        contract = make_contract()
        with pytest.raises(TaskDoneRefused) as caught:
            close_task(
                contract,
                mutation_proof=make_proof(contract),
                reviewer_verdicts=(self.AGREE(CLAUDE), self.AGREE(GEMINI)),
                counterexamples=(
                    CounterexampleClaim(
                        claim="repro crashes", verified=True, receipt="sha256:" + "d" * 64
                    ),
                ),
            )
        assert "counterexample" in str(caught.value).lower()

    def test_an_unverified_counterexample_does_not_veto(self):
        contract = make_contract()
        done = close_task(
            contract,
            mutation_proof=make_proof(contract),
            reviewer_verdicts=(self.AGREE(CLAUDE), self.AGREE(GEMINI)),
            counterexamples=(CounterexampleClaim(claim="hunch", verified=False),),
        )
        assert done.standing_counterexamples == 0

    def test_aliases_of_one_family_cast_one_vote(self):
        contract = make_contract()
        gpt_alias = ModelIdentity("openai", "gpt-4o-2024-05-13", harness="app-chat")
        # Three AGREE seats — but two of them are one family, so the family tally
        # is 1 AGREE (openai) + 1 DISAGREE (anthropic): no majority.
        with pytest.raises(TaskDoneRefused):
            close_task(
                contract,
                mutation_proof=make_proof(contract),
                reviewer_verdicts=(
                    self.AGREE(GPT),
                    self.AGREE(gpt_alias),
                    (CLAUDE, "DISAGREE"),
                ),
            )

    def test_family_collapsed_majority_closes(self):
        contract = make_contract()
        gpt_alias = ModelIdentity("openai", "gpt-4o-2024-05-13", harness="app-chat")
        done = close_task(
            contract,
            mutation_proof=make_proof(contract),
            reviewer_verdicts=(
                self.AGREE(GPT),
                self.AGREE(gpt_alias),
                self.AGREE(CLAUDE),
                self.AGREE(GEMINI),
            ),
        )
        assert done.votes_by_family["openai/gpt-4o"] == "AGREE"

    def test_a_write_task_without_a_mutation_proof_is_refused(self):
        contract = make_contract()
        with pytest.raises(TaskDoneRefused):
            close_task(contract, mutation_proof=None,
                       reviewer_verdicts=(self.AGREE(CLAUDE), self.AGREE(GEMINI)))

    def test_a_read_only_task_needs_no_mutation_proof(self):
        contract = make_contract(mutation=MutationPermission.READ_ONLY, writable_scope=())
        done = close_task(
            contract, mutation_proof=None,
            reviewer_verdicts=(self.AGREE(CLAUDE), self.AGREE(GEMINI)),
        )
        assert done.mutation_proof_sha is None

    def test_a_pending_candidate_sha_cannot_close(self):
        contract = make_contract(candidate_sha="")
        with pytest.raises(TaskDoneRefused):
            close_task(
                contract,
                mutation_proof=make_proof(contract),
                reviewer_verdicts=(self.AGREE(CLAUDE), self.AGREE(GEMINI)),
            )

    def test_a_stale_proof_cannot_close_a_task(self):
        contract = make_contract()
        with pytest.raises(TaskDoneRefused):
            close_task(
                contract,
                mutation_proof=make_proof(contract, fencing_counter=0),
                lease_fencing=1,  # the ledger honors epoch 1; the proof rides 0
                reviewer_verdicts=(self.AGREE(CLAUDE), self.AGREE(GEMINI)),
            )


class TestThreeDistinctClosures:
    def test_integration_green_is_minted_separately_from_task_done(self):
        contract = make_contract()
        green = mark_integration_green(
            contract,
            verification_command=contract.verification,
            exit_code=0,
            output_sha256="e" * 64,
        )
        assert green.task_id == "T1"
        assert green.verification_command == contract.verification

    def test_a_failing_verification_cannot_be_marked_green(self):
        contract = make_contract()
        with pytest.raises(IntegrationGreenRefused):
            mark_integration_green(
                contract, verification_command=contract.verification,
                exit_code=1, output_sha256="e" * 64,
            )

    def test_obligation_closure_names_its_vool_obligation(self):
        contract = make_contract()
        closure = close_obligation(contract, obligation_id="VOOL-OBL-77")
        assert closure.obligation_id == "VOOL-OBL-77"
        with pytest.raises(TaskContractError):
            close_obligation(contract, obligation_id="")

    def test_task_done_does_not_imply_integration_or_obligation(self):
        contract = make_contract()
        done = close_task(
            contract,
            mutation_proof=make_proof(contract),
            reviewer_verdicts=((CLAUDE, "AGREE"), (GEMINI, "AGREE")),
        )
        record = CompletionRecord(task=done)
        assert record.task is not None
        assert record.integration is None
        assert record.obligation is None
        assert record.fully_closed is False

    def test_fully_closed_requires_all_three_certificates(self):
        contract = make_contract()
        record = CompletionRecord(
            task=close_task(
                contract,
                mutation_proof=make_proof(contract),
                reviewer_verdicts=((CLAUDE, "AGREE"), (GEMINI, "AGREE")),
            ),
            integration=mark_integration_green(
                contract, verification_command=contract.verification,
                exit_code=0, output_sha256="e" * 64,
            ),
            obligation=close_obligation(contract, obligation_id="VOOL-OBL-77"),
        )
        assert record.fully_closed is True

    def test_no_automatic_merge_push_or_promotion_exists(self):
        contract = make_contract()
        record = CompletionRecord(
            task=close_task(
                contract,
                mutation_proof=make_proof(contract),
                reviewer_verdicts=((CLAUDE, "AGREE"), (GEMINI, "AGREE")),
            ),
        )
        with pytest.raises(PromotionRefused):
            record.promote()
        # There is no merge/push helper to call: the refusal above is the whole API.
        assert not hasattr(record, "merge") and not hasattr(record, "push")


class TestMutationGrant:
    def test_a_mutation_grant_is_minted_not_constructed(self):
        with pytest.raises(Exception):
            from core.council.task_contract import MutationGrant

            MutationGrant(task_id="T1", granted_by="operator")
        grant = mint_mutation_grant("T1", granted_by="operator")
        assert grant.task_id == "T1"

    def test_the_grant_names_one_task(self):
        grant = mint_mutation_grant("T9")
        assert grant.task_id == "T9"

    def test_escalation_objects_are_not_mutation_grants(self):
        escalation = mint_operator_escalation(task_id="T1")
        grant = mint_mutation_grant("T1")
        assert escalation != grant
