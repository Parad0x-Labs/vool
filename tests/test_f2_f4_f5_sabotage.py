"""S1-S14 and P1-P21: every guard is load-bearing at BOTH tiers, or it is a survivor.

A mutation is applied to production source, the file's hash is compared before and after to prove it
actually landed, the test tiers are re-run in a subprocess against the mutated tree, and the file is
restored from a saved copy with its hash checked again. A string replace that silently matched
nothing produces a green run that reads as a surviving guard, and the wrong conclusion gets drawn in
the safe-looking direction -- so "the mutation applied" is asserted, not assumed.

Each mutation must fail:

  1. at least one OWNER-BOUNDARY test  (the type, the reducer, the validator), AND
  2. at least one PRODUCT-BOUNDARY test (provider arguments, dispatch, persistence).

Failing only one tier is a RELEASE BLOCKER, not a pass, and the assertion below says so by name.

Two of the validator's rules -- `EvidenceStatus.ABSTAINED` and a role span that binds to no text --
are unreachable from every production proposer today: `parse_proposal` only ever builds spans over
the canonical text it was handed, and the formal grammars only ever emit PROVEN. Mutating those
lines therefore fails the owner tier alone, which is a statement about reach rather than about the
guard. S1 and S2 are aimed instead at the seams of the same intent that production DOES reach --
the span locator and the frame-level refusal in `parse_proposal` -- so both tiers bite. The two
unreachable rules keep their owner-tier proofs in `test_semantic_turn_proof.py`; they are defence
for the next producer, and they are not counted as proven at the product boundary.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

OWNER_TIER = (
    "tests/test_semantic_turn_proof.py",
    "tests/test_f2_f4_f5_contract.py",
    "tests/test_requirement_ledger.py",
    "tests/test_real_planner_producer.py",
    "tests/test_planner_artifact_turn_lifetime.py",
    "tests/test_wire_and_emergency_envelope.py",
)
PRODUCT_TIER = (
    "tests/test_requirement_authority_product.py",
    "tests/test_obligation_floor_production_dispatch.py",
    "tests/test_obligation_realization_red_set.py",
    "tests/test_conductor_turn_cache_lifetime_product.py",
    "tests/test_conductor_wire_persistence_product.py",
    "tests/test_post_claim_envelope_product.py",
)


@dataclass(frozen=True)
class Mutation:
    """One deliberate break, and where each tier must notice it.

    `edits` is a tuple because some rules are enforced in TWO places, and a mutation that defeats
    one of them proves the other rather than the rule. `fx_quote` is the standing example: a
    conversion needs both ISO membership AND a family that permits closed syntax to affirm it, so
    breaking either alone still buys no provider call. Breaking both is one semantic change --
    "accept an ISO-shaped pair as executable work" -- expressed across the two lines that jointly
    forbid it. Splitting it into two mutations would report two survivors and hide the rule.
    """

    name: str
    path: str
    edits: tuple[tuple[str, str], ...]
    note: str

    @classmethod
    def one(cls, name: str, path: str, before: str, after: str, note: str) -> Mutation:
        return cls(name, path, ((before, after),), note)


MUTATIONS = (
    Mutation.one(
        "S1_unknown_becomes_owned",
        "core/conductor/semantic_proof.py",
        "    index = canonical.text.find(probe, begin, end)\n"
        "    if index < 0:\n"
        "        return None\n"
        "    return canonical.span(index, index + len(probe), label=probe)",
        "    index = canonical.text.find(probe, begin, end)\n"
        "    if index < 0:\n"
        "        return canonical.span(begin, min(end, begin + len(probe)))\n"
        "    return canonical.span(index, index + len(probe), label=probe)",
        "a surface the text does not contain is given a span anyway -- absence promoted to "
        "evidence. Fabricated WITHOUT a label on purpose: the label check is a second layer over "
        "the same rule, and a mutation it absorbs proves the second layer rather than the first.",
    ),
    Mutation.one(
        "S2_remove_frame_abstention",
        "core/conductor/semantic_proof.py",
        "        if broken:\n"
        "            abstentions.append(\n"
        "                Abstention(frame_id, family, AbstentionReason.INVENTED_SURFACE, broken)\n"
        "            )\n"
        "            continue",
        "        if broken:\n"
        "            abstentions.append(\n"
        "                Abstention(frame_id, family, AbstentionReason.INVENTED_SURFACE, broken)\n"
        "            )",
        "a frame whose role could not be located is kept, minus the role it could not prove",
    ),
    Mutation.one(
        "S3_default_polarity_affirmed",
        "core/conductor/semantic_proof.py",
        "    try:\n        return Polarity(str(value).strip().casefold())\n    except ValueError:\n        return None",
        "    try:\n        return Polarity(str(value).strip().casefold())\n    except ValueError:\n        return Polarity.AFFIRMED",
        "a missing polarity defaults to yes, so an omission buys a provider call",
    ),
    Mutation.one(
        "S4_delete_derived_realization",
        "core/conductor/realization.py",
        "        groups: tuple[tuple[RequirementSlot, ...], ...] = (\n"
        "            tuple((member,) for member in members) if members else ((),)\n"
        "        )",
        "        groups: tuple[tuple[RequirementSlot, ...], ...] = tuple(\n"
        "            (member,) for member in members\n"
        "        )",
        "a requirement with no subject slots gets no realization -- the F4 defect itself",
    ),
    Mutation.one(
        "S5_realization_id_becomes_requirement_id",
        "core/conductor/realization.py",
        '            realization_id = f"{requirement.requirement_id}:realization:{position}"',
        "            realization_id = requirement.requirement_id",
        "two realizations of one requirement collapse into one accounting slot",
    ),
    Mutation.one(
        "S6_delete_realization_binding",
        "core/conductor/realization.py",
        "        matched = by_key.get(realization.execution_key, ())\n"
        "        if matched:\n"
        "            bindings[realization.realization_id] = (matched[0],)\n"
        "            continue",
        "        matched = ()\n"
        "        if matched:\n"
        "            bindings[realization.realization_id] = (matched[0],)\n"
        "            continue",
        "an existing node is never bound, so the work is done twice and accounted once",
    ),
    Mutation.one(
        "S7_remove_execution_key_normalization",
        "core/conductor/registry.py",
        "    if spec.execution_key is not None:\n        return f\"{spec.name}:{spec.execution_key(arguments)}\"",
        "    if False:\n        return f\"{spec.name}:{spec.execution_key(arguments)}\"",
        "the operation's own identity is ignored, so one piece of work gets two keys",
    ),
    Mutation.one(
        "S8_dedup_realizations_with_nodes",
        "core/conductor/planner.py",
        "        bindings=tuple(\n"
        "            RealizationBinding(\n"
        "                realization_id=realization.realization_id,",
        "        bindings=tuple(\n"
        "            RealizationBinding(\n"
        "                realization_id=bound.get(realization.realization_id, ('x',))[0],",
        "realizations are collapsed onto their node, so two obligations share one slot",
    ),
    Mutation.one(
        "S9_remove_prerequisite_edges",
        "core/conductor/realization.py",
        "            for prerequisite in realization.prerequisite_realization_ids:",
        "            for prerequisite in ():",
        "blocking no longer travels the realization DAG",
    ),
    Mutation.one(
        "S10_empty_projection_becomes_unavailable",
        "core/conductor/realization.py",
        "    if work.unresolved_roles:\n"
        '        return CapabilityLookupResult.unresolved(",".join(work.unresolved_roles))',
        "    if work.unresolved_roles:\n"
        '        return CapabilityLookupResult.unavailable(",".join(work.unresolved_roles))',
        "a subject nobody could identify is reported as a capability this runtime lacks",
    ),
    Mutation.one(
        "S11_render_failures_from_node_request_text",
        "core/conductor/realization.py",
        '            f"- {outcome.realization.display_subject} — {sentences[outcome.state]}"',
        '            f"- {outcome.realization.source_surface} — {sentences[outcome.state]}"',
        "the failure line names the whole clause instead of the role-owned subject",
    ),
    Mutation.one(
        "S12_remove_explicit_runtime_task_outcome",
        "core/agent_runtime/agent.py",
        '            result["conductor_product_decision"] = decision.to_dict()',
        '            result["conductor_product_decision"] = {}',
        "the decision's exact outcome never reaches persistence, which re-derives one",
    ),
    Mutation.one(
        "S13_restore_non_empty_answer_fulfillment",
        "core/conductor/product_decision.py",
        "    status = _FULFILMENT_OF_DISPOSITION[disposition]",
        "    status = FulfillmentStatus.FULFILLED",
        "every claimed turn persists as fulfilled regardless of what happened",
    ),
    Mutation.one(
        "S14_restore_post_claim_except_none",
        "core/agent_runtime/agent.py",
        "        except BaseException as exc:\n            failed = claim_gate.integrity_failure(exc)",
        "        except BaseException as exc:\n            return None\n            failed = claim_gate.integrity_failure(exc)",
        "a post-claim fault silently hands the turn to the ordinary lane again",
    ),
    # --- P1-P14: the defects RED proved, each aimed at the seam production actually reaches ------
    Mutation.one(
        "P1_share_unkeyed_artifact_between_call_kinds",
        "core/agent_runtime/turn_planner_hook.py",
        "        key = PlannerCallKind(kind).value",
        '        key = "shared"',
        "one cache slot for two contracts -- the exact production defect: one provider call, a "
        "byte-identical reply, and a semantic proposer that never ran",
    ),
    Mutation.one(
        "P2_return_the_decomposition_cache_for_the_semantic_call",
        "core/agent_runtime/turn_planner_hook.py",
        "        kind=PlannerCallKind.SEMANTIC_PROOF,\n"
        "        json_schema=_semantic_proposal_json_schema(),",
        "        kind=PlannerCallKind.CLAUSE_DECOMPOSITION,\n"
        "        json_schema=_semantic_proposal_json_schema(),",
        "the semantic call reads the decomposition's cache slot",
    ),
    Mutation.one(
        "P3_semantic_call_uses_the_planner_json_schema",
        "core/agent_runtime/turn_planner_hook.py",
        "        json_schema=_semantic_proposal_json_schema(),",
        "        json_schema=_planner_json_schema(),",
        "the provider is asked for a clause array while the reply is parsed as a proposal",
    ),
    Mutation.one(
        "P4_semantic_object_routed_through_the_strict_array_parser",
        "core/agent_runtime/turn_planner_hook.py",
        "        parse=_strict_semantic_proposal,",
        "        parse=_strict_json_array,",
        "a semantic proposal is refused by the clause parser and every frame is lost",
    ),
    Mutation.one(
        "P5_a_null_group_discards_a_valid_solitary_role",
        "core/conductor/semantic_proof.py",
        '                coordination_group_id=f"{role_name}:members",',
        '                coordination_group_id="",',
        "bookkeeping stops being derived; a proposer that omits the group loses a proven role",
    ),
    Mutation.one(
        "P6_ordinal_bookkeeping_discards_an_otherwise_valid_proof",
        "core/conductor/semantic_proof.py",
        "        ordinal = counters.get(role_name, 0)\n        counters[role_name] = ordinal + 1",
        "        ordinal = counters.get(role_name, 0) + 1\n        counters[role_name] = ordinal",
        "member ordinals stop being a 0-based run, so a valid coordinated list fails contiguity",
    ),
    Mutation(
        "P7_formal_grammar_accepts_csv_to_xml_as_fx",
        "core/conductor/semantic_proof.py",
        (
            (
                "        if not (\n"
                '            _is_iso_currency(match.group("base")) and _is_iso_currency(match.group("quote"))\n'
                "        ):",
                "        if False:",
            ),
            (
                "        if proposed is Polarity.AFFIRMED and not self.formal_proof_may_affirm:\n"
                "            return Polarity.UNRESOLVED",
                "        if False:\n            return Polarity.UNRESOLVED",
            ),
        ),
        "three capitals with a preposition become an EXECUTABLE currency conversion. Compound "
        "because membership and authority both forbid it, and breaking one proves the other.",
    ),
    Mutation(
        "P8_formal_affirmed_overrides_an_overlapping_semantic_negation",
        "core/conductor/semantic_proof.py",
        (
            (
                "    if bounded.polarity is not Polarity.AFFIRMED:\n        return formal",
                "    if bounded.polarity is not Polarity.AFFIRMED:\n        return bounded",
            ),
            (
                "        if proposed is Polarity.AFFIRMED and not self.formal_proof_may_affirm:\n"
                "            return Polarity.UNRESOLVED",
                "        if False:\n            return Polarity.UNRESOLVED",
            ),
        ),
        "a bounded refusal loses to a closed grammar that cannot see the prose around it, and the "
        "grammar is allowed to affirm -- the two halves of 'never convert EUR to JPY' converting.",
    ),
    Mutation.one(
        "P8b_formal_grammar_may_affirm_an_outward_facing_family",
        "core/conductor/semantic_proof.py",
        "        if proposed is Polarity.AFFIRMED and not self.formal_proof_may_affirm:\n"
        "            return Polarity.UNRESOLVED",
        "        if False:\n            return Polarity.UNRESOLVED",
        "a network-reaching family is affirmed by closed syntax alone",
    ),
    Mutation.one(
        "P9_overlapping_contradictory_frames_both_proven",
        "core/conductor/semantic_proof.py",
        "            if not first.scope.overlaps(second.scope):\n                continue",
        "            if True:\n                continue",
        "two readings of one region both ship",
    ),
    Mutation.one(
        "P10_role_span_swallows_two_coordination_members",
        "core/conductor/semantic_proof.py",
        "            if first.span.overlaps(second.span):",
        "            if False:",
        "one filler may contain another, so a swallowed list passes as a single subject",
    ),
    Mutation.one(
        "P11_calculation_key_distinguishes_equivalent_notation",
        "core/conductor/operations.py",
        "    return _canonical_expression_identity(fragment)",
        '    return " ".join(str(fragment).split()).rstrip(".,;:?!").casefold()',
        "the raw string comes back as the identity, so one sum gets four execution keys",
    ),
    Mutation.one(
        "P12_registry_exception_escapes_the_typed_capability_contract",
        "core/conductor/realization.py",
        "    try:\n        return _lookup_capability(work)\n    except Exception as exc:",
        "    if True:\n        return _lookup_capability(work)\n    try:\n"
        "        return _lookup_capability(work)\n    except Exception as exc:",
        "a software defect unwinds past realization and becomes a silent pre-claim decline",
    ),
    Mutation.one(
        "P13_nothing_captured_to_dict_throws",
        "core/conductor/product_decision.py",
        '            "runtime_task_outcome": (\n'
        "                self.runtime_task_outcome.to_dict()\n"
        "                if self.runtime_task_outcome is not None\n"
        "                else None\n"
        "            ),",
        '            "runtime_task_outcome": self.runtime_task_outcome.to_dict(),',
        "a legitimate unclaimed decision cannot be serialized, so the decline is an exception",
    ),
    Mutation.one(
        "P14_wire_decision_contradicting_itself_persists_as_declared",
        "core/runtime_task_outcome.py",
        "    if status is None or status not in allowed:",
        "    if False:",
        "a serialized PARTIAL/FAILED decision carrying a FULFILLED outcome persists as fulfilled",
    ),
    Mutation.one(
        # The other half of the same rule. P14 breaks the comparison for a disposition this
        # runtime KNOWS; this one breaks the allow-list itself, so a disposition it has never
        # heard of matches no rule and is waved through -- which is how `made_up_disposition`
        # beside FULFILLED persisted as fulfilled.
        "P15_unrecognized_wire_disposition_is_waved_through",
        "core/runtime_task_outcome.py",
        "    if allowed is None:",
        "    if False:",
        "a serialized decision whose disposition this runtime does not recognize persists as declared",
    ),
    Mutation.one(
        # The pre-classification cache is keyed by (turn, call kind). P16-P18 break the TURN half,
        # which is the half that was missing: the artifact said "for one user turn" and its identity
        # only carried the contract, so on a surface that reuses one `source_context` -- which the
        # runtime front door keeps BY REFERENCE -- turn 2 was handed turn 1's answers and made no
        # provider call at all.
        "P16_planner_artifact_identity_drops_the_turn",
        "core/agent_runtime/turn_planner_hook.py",
        "    if isinstance(existing, SharedPlannerArtifact) and existing.turn_id == turn_id:",
        "    if isinstance(existing, SharedPlannerArtifact):",
        "one artifact serves every turn of a session, so turn 2 replays turn 1's classification",
    ),
    Mutation.one(
        "P17_planner_turn_identity_is_never_read",
        "core/agent_runtime/turn_planner_hook.py",
        "    turn_id = _context_turn_id(source_context)",
        "    turn_id = ''",
        "every turn looks like the same turn, so the artifact is never replaced",
    ),
    Mutation.one(
        "P18_planner_turn_identity_comes_from_the_context_object",
        "core/agent_runtime/turn_planner_hook.py",
        '    if not isinstance(source_context, dict):\n        return ""',
        "    if isinstance(source_context, dict):\n        return str(id(source_context))\n"
        '    if not isinstance(source_context, dict):\n        return ""',
        "identity is the dict's address, which is stable across the turns that mutate it in place",
    ),
    Mutation.one(
        "P19_faulting_call_slot_inherits_its_neighbour",
        "core/agent_runtime/turn_planner_hook.py",
        '            except Exception:\n                record.raw = ""\n                record.failed = True',
        '            except Exception:\n                other = self._records.get("clause_decomposition")\n'
        "                record.raw = other.raw if other else \"\"\n                record.failed = True",
        "a faulting semantic call answers with the decomposition reply -- the original P0, exactly",
    ),
    Mutation.one(
        # The last-resort post-claim exit is a dict literal so nothing left in it can fail, and it
        # serializes through `emergency_decision_payload` because the version that called
        # `decision.to_dict()` inline WAS a remaining call that could fail -- directly under a
        # comment saying there was none.
        #
        # Mutated at the FUNCTION, not at the call site in `apps/vool_agent.py`. A call-site swap
        # is only visible where that call site runs, so it fails the product tier alone -- which
        # this suite's own rule correctly calls a survivor rather than a proof. The call site keeps
        # two independent product-tier tests in `test_post_claim_envelope_product.py`.
        "P20_emergency_envelope_stops_guarding_the_serializer",
        "core/conductor/product_decision.py",
        "    try:\n        payload = decision.to_dict()\n        if isinstance(payload, dict):\n"
        "            return payload\n"
        "    except BaseException:  # a broken serializer must not lose the claim\n"
        "        pass",
        "    return decision.to_dict()",
        "a broken serializer takes the last-resort path down with it, and the claim is lost",
    ),
    Mutation.one(
        "P21_emergency_envelope_reclassifies_a_post_claim_fault",
        "core/conductor/product_decision.py",
        '            "fulfillment_status": FulfillmentStatus.FAILED.value,\n'
        '            "failure_stage": FAILURE_STAGE,',
        '            "fulfillment_status": FulfillmentStatus.FULFILLED.value,\n'
        '            "failure_stage": FAILURE_STAGE,',
        "the envelope forms its own verdict instead of carrying the claim gate's",
    ),
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(tests: tuple[str, ...]) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.name)
def test_every_mutation_fails_an_owner_test_and_a_product_test(mutation: Mutation):
    target = REPO / mutation.path
    original_bytes = target.read_bytes()
    original_hash = _hash(target)
    source = original_bytes.decode("utf-8")

    mutated = source
    for index, (before, after) in enumerate(mutation.edits):
        occurrences = mutated.count(before)
        assert occurrences == 1, (
            f"{mutation.name}: edit {index} matches the anchor {occurrences} times in "
            f"{mutation.path}. A mutation that matches nothing produces a green run that reads "
            "as a surviving guard."
        )
        mutated = mutated.replace(before, after, 1)

    try:
        target.write_text(mutated, encoding="utf-8")
        mutated_hash = _hash(target)
        assert mutated_hash != original_hash, f"{mutation.name}: the mutation did not land"

        owner = _run(OWNER_TIER)
        product = _run(PRODUCT_TIER)
    finally:
        target.write_bytes(original_bytes)
        assert _hash(target) == original_hash, (
            f"{mutation.name}: {mutation.path} was NOT restored -- the tree is dirty"
        )

    caught_by_owner = owner.returncode != 0
    caught_by_product = product.returncode != 0
    assert caught_by_owner and caught_by_product, (
        f"SURVIVOR / RELEASE BLOCKER -- {mutation.name} ({mutation.note}). "
        f"owner tier caught it: {caught_by_owner}; product tier caught it: {caught_by_product}.\n"
        f"owner tail:\n{owner.stdout[-2500:]}\n"
        f"product tail:\n{product.stdout[-2500:]}"
    )


def test_the_matrix_covers_every_mandated_sabotage():
    """The list itself is a contract. A mutation quietly dropped is a guard quietly unproven."""
    names = {m.name.split("_", 1)[0] for m in MUTATIONS}
    assert {f"S{n}" for n in range(1, 15)} <= names
    assert {f"P{n}" for n in range(1, 15)} <= names
