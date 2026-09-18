"""Mutation matrix for the semantic Phase-0 guards: does each one actually have a test behind it?

A green suite proves that nothing currently fails. It does not prove that any particular guard is
load-bearing -- a guard with no test behind it stays green when you delete it, and the suite reports
exactly the same number either way. This harness deletes each guard on purpose and checks that a
named test goes red.

Modelled on `scripts/anvil_mutation_matrix.py`, including the exit-code discipline that file records
having got wrong once: pytest's exit code 4 (usage error) and 5 (no tests collected) are NOT kills.
A mutation only counts as caught when an assertion actually failed.

    .venv/bin/python scripts/semantic_phase0_mutation_matrix.py
    .venv/bin/python scripts/semantic_phase0_mutation_matrix.py --self-test

Sources are snapshotted before anything is touched and restored byte-for-byte afterwards; the run
reports whether the restore succeeded, and exits non-zero if it did not.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

sys.path.insert(0, str(REPO_ROOT))
from tests import _source_guard as source_guard

#: pytest exit codes. 1 is "tests failed" -- the only code that means a mutation was caught.
_EXIT_ALL_PASSED = 0
_EXIT_TESTS_FAILED = 1


@dataclass(frozen=True)
class Mutation:
    """One deliberate defect, and the test that must notice it."""

    name: str
    path: str
    old: str
    new: str
    test: str
    #: What the guard is for, in one line -- printed beside a survivor so the gap reads as a gap.
    guards: str


MUTATIONS = (
    # ---- 1. Permission authority ------------------------------------------------------
    # ---- 2. An arbitrary executor is not authorization ---------------------------------
    Mutation(
        name="injected_runner_treated_as_authorized",
        path="core/conductor/registry.py",
        old="            raise NotAuthorizedError(",
        new="            runner = getattr(_context, \"run_tool_intent\", None)\n            if runner is not None:\n                return runner({\"intent\": intent, \"arguments\": dict(getattr(_node, \"arguments\", {}) or {})})\n            raise NotAuthorizedError(",
        test="tests/semantic_phase0/test_semantic_operation_registration.py::test_an_injected_runner_is_not_proof_of_authorization",
        guards="'somebody handed us a function' standing in for 'the policy said yes'",
    ),
    Mutation(
        name="read_only_implies_parallel_safe",
        path="core/conductor/registry.py",
        old="            can_run_in_parallel=False,",
        new="            can_run_in_parallel=bool(getattr(contract, \"read_only\", False)),",
        test="tests/semantic_phase0/test_semantic_operation_registration.py::test_it_is_projected_into_an_operation_spec",
        guards="inferring concurrency safety from a claim about side effects",
    ),
    # ---- 3. REACH truth ---------------------------------------------------------------
    Mutation(
        name="telemetry_can_claim_it_preempted_the_model",
        path="core/routing_decision_log.py",
        old="            semantic_reach.OUTCOME_TELEMETRY,",
        new="            semantic_reach.OUTCOME_CLAIMED if handled else semantic_reach.OUTCOME_DECLINED,",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_a_telemetry_row_can_never_claim_it_preempted_the_model",
        guards="a late telemetry row retroactively claiming it took the turn from the model",
    ),
    Mutation(
        name="receipt_verification_always_passes",
        path="core/semantic/receipt.py",
        old="    problems: list[str] = []\n    reach = receipt.get(\"reach\")",
        new="    problems: list[str] = []\n    return True, ()\n    reach = receipt.get(\"reach\")",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_a_receipt_that_renames_the_preempting_gate_is_caught",
        guards="a receipt that misreports the path passing verification",
    ),
    Mutation(
        name="preempted_by_reports_the_last_claimant",
        path="core/semantic/reach.py",
        old="            for event in self.events:\n                if event.outcome == OUTCOME_CLAIMED:\n                    return event.gate",
        new="            for event in reversed(self.events):\n                if event.outcome == OUTCOME_CLAIMED:\n                    return event.gate",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_preempted_by_names_the_first_claimant_not_the_last",
        guards="the receipt naming the wrong gate as the one that preempted the model",
    ),
    # ---- 4. source_context lifecycle --------------------------------------------------
    Mutation(
        name="source_context_none_loses_the_receipt",
        path="apps/vool_agent.py",
        old="            observed_context = source_context if isinstance(source_context, dict) else {}",
        new="            observed_context = source_context",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_a_turn_with_no_source_context_still_lands_a_receipt",
        guards="a caller that passes no context producing no semantic record at all",
    ),
    Mutation(
        name="receipt_not_written_when_a_turn_raises",
        path="core/semantic/turn_observation.py",
        old="        try:\n            yield observation\n        finally:\n            if recorder is not None:",
        new="        yield observation\n        if True:\n            if recorder is not None:",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_every_driven_turn_lands_a_resolution_receipt_in_the_ledger",
        guards="losing the record of a turn exactly when it failed",
    ),
    Mutation(
        name="nested_turn_starts_a_second_recorder",
        path="core/semantic/turn_observation.py",
        old="    existing = semantic_reach.current()\n    if existing is not None:",
        new="    existing = semantic_reach.current()\n    if False and existing is not None:",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_a_nested_turn_joins_the_outer_record_instead_of_starting_a_second_one",
        guards="one user message producing several partial receipts",
    ),
    # ---- 5. Hidden provider call ------------------------------------------------------
    Mutation(
        name="hidden_dynamic_provider_call_in_receipt_writing",
        path="core/semantic/turn_observation.py",
        old="        session_id, turn_id = _turn_identity(source_context)\n        preempted = recorder.preempted_by",
        new="        session_id, turn_id = _turn_identity(source_context)\n        try:\n            from core.model_registry import ModelRegistry\n\n            ModelRegistry().list_manifests(limit=1)\n        except Exception:\n            pass\n        preempted = recorder.preempted_by",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_the_semantic_layer_never_reaches_the_model_layer",
        guards="instrumentation reaching a provider through a dynamic import an import scan cannot see",
    ),
    # ---- 6. Admission / dependencies --------------------------------------------------
    Mutation(
        name="zero_admission_proposal_accepted",
        path="core/semantic/types.py",
        old="        if len(self.admissions) != len(self.proposals):",
        new="        if self.admissions and len(self.admissions) != len(self.proposals):",
        test="tests/semantic_phase0/test_semantic_sabotage_invariants.py::test_an_outcome_cannot_be_built_that_loses_a_clause",
        guards="a turn that resolved nothing constructing cleanly and reading as success-shaped",
    ),
    Mutation(
        name="dependent_admitted_after_rejected_prerequisite",
        path="core/semantic/admission.py",
        old="        if admitted_clauses is None or dependency not in admitted_clauses:",
        new="        if False and dependency not in (admitted_clauses or set()):",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_a_dependent_is_refused_when_its_prerequisite_was_rejected",
        guards="a clause running on an answer its prerequisite never produced",
    ),
    Mutation(
        name="unsupported_capability_allowed_to_run",
        path="core/semantic/admission.py",
        old="    if not bool(getattr(contract, \"supported\", False)):",
        new="    if False and not bool(getattr(contract, \"supported\", False)):",
        test="tests/semantic_phase0/test_semantic_sabotage_invariants.py::test_an_unsupported_capability_is_refused_even_when_permission_would_allow",
        guards="a registered-but-unservable capability reaching execution",
    ),
    Mutation(
        name="undeclared_side_effect_treated_as_harmless",
        path="core/semantic/admission.py",
        old="    if side_effect_class in _UNDECLARED_SIDE_EFFECTS:",
        new="    if False and side_effect_class in _UNDECLARED_SIDE_EFFECTS:",
        test="tests/semantic_phase0/test_semantic_sabotage_invariants.py::test_an_undeclared_side_effect_class_never_resolves_to_harmless",
        guards="an unspecified side-effect class resolving to safe",
    ),
    Mutation(
        name="malformed_proposal_reaches_the_registry",
        path="core/semantic/admission.py",
        old="    if not isinstance(proposal, IntentProposal):",
        new="    if False and not isinstance(proposal, IntentProposal):",
        test="tests/semantic_phase0/test_semantic_sabotage_invariants.py::test_a_malformed_proposal_is_refused_before_anything_is_looked_up",
        guards="a malformed proposal being looked up and permission-checked",
    ),
    Mutation(
        name="admit_all_drops_a_failing_clause",
        path="core/semantic/admission.py",
        old="        except Exception as exc:\n            # A fault admitting one clause must not silently shorten the list",
        new="        except Exception as exc:  # noqa: F841\n            continue\n            # A fault admitting one clause must not silently shorten the list",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_admit_all_survives_a_clause_that_raises_without_shortening_the_list",
        guards="a clause disappearing when admitting it raises",
    ),
    # ---- 8. Spans ---------------------------------------------------------------------
    Mutation(
        name="dishonest_span_representation",
        path="core/semantic/canonical_text.py",
        old="        text = unicodedata.normalize(form, source) if form else source",
        new="        text = unicodedata.normalize(\"NFC\", source)",
        test="tests/semantic_phase0/test_semantic_spans_bind_to_one_text.py::test_a_representation_cannot_claim_a_normal_form_it_does_not_apply",
        guards="a representation name that misdescribes the normal form actually applied",
    ),
    Mutation(
        name="raw_unicode_error_escapes_the_span_layer",
        path="core/semantic/canonical_text.py",
        old="    except UnicodeEncodeError as exc:",
        new="    except _NeverRaisedError as exc:",
        test="tests/semantic_phase0/test_semantic_spans_bind_to_one_text.py::test_a_lone_surrogate_is_a_typed_refusal_not_a_codec_error",
        guards="a hostile input surfacing as a codec error instead of a typed refusal",
    ),
    Mutation(
        name="overlaps_ignores_text_length",
        path="core/semantic/canonical_text.py",
        old="        return (self.representation, self.text_digest, self.text_length)",
        new="        return (self.representation, self.text_digest, 0)",
        test="tests/semantic_phase0/test_semantic_spans_bind_to_one_text.py::test_overlaps_respects_the_complete_binding_identity",
        guards="spans over differently-sized texts comparing as if they were comparable",
    ),
    Mutation(
        name="span_digest_check_removed",
        path="core/semantic/canonical_text.py",
        old="        if self.text_digest != canonical.digest:",
        new="        if False and self.text_digest != canonical.digest:",
        test="tests/semantic_phase0/test_semantic_spans_bind_to_one_text.py::test_same_length_but_different_text_is_caught_by_the_digest",
        guards="a span resolving against same-length but different text",
    ),
    Mutation(
        name="span_range_check_removed",
        path="core/semantic/canonical_text.py",
        old="        if not self.is_well_formed:",
        new="        if False and not self.is_well_formed:",
        test="tests/semantic_phase0/test_semantic_spans_bind_to_one_text.py::test_an_out_of_range_span_is_refused_rather_than_clamped",
        guards="an out-of-range span being silently clamped by str slicing",
    ),
    # ---- 9. LDAR ----------------------------------------------------------------------
    Mutation(
        name="split_tuple_hides_authority",
        path="core/semantic/lexical_authority.py",
        old="    if isinstance(value, ast.BinOp):",
        new="    if False and isinstance(value, ast.BinOp):",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_splitting_a_table_across_a_concatenation_does_not_hide_it",
        guards="a table split across a concatenation vanishing from the inventory",
    ),
    Mutation(
        name="regex_group_directive_not_stripped",
        path="core/semantic/lexical_authority.py",
        old="        group = _GROUP_DIRECTIVE.sub(\"\", raw_group)",
        new="        group = raw_group",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_the_same_vocabulary_measures_the_same_as_a_tuple_a_frozenset_and_a_regex",
        guards="vocabulary hiding in a non-capturing group's first alternative",
    ),
    Mutation(
        name="unmeasured_files_reported_as_nothing",
        path="core/semantic/lexical_authority.py",
        old="            unreadable.append(relative)",
        new="            pass",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_the_inventory_reports_what_it_could_not_measure",
        guards="silence about unmeasurable authority reading as absence of authority",
    ),
    Mutation(
        name="failing_case_dropped_from_the_corpus",
        path="core/semantic/generalization.py",
        old="            errors.append(message)\n            outcome = CaseOutcome(case=case, actual_route=\"\", covered_by=covered, error=message)",
        new="            errors.append(message)\n            continue",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_a_case_that_raises_fails_its_class_rather_than_being_dropped",
        guards="the score improving whenever the runtime crashes on a phrasing",
    ),
    Mutation(
        name="class_passes_on_one_good_paraphrase",
        path="core/semantic/generalization.py",
        old="        return bool(self.outcomes) and all(outcome.routed_correctly for outcome in self.outcomes)",
        new="        return bool(self.outcomes) and any(outcome.routed_correctly for outcome in self.outcomes)",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_one_failing_paraphrase_fails_its_whole_class",
        guards="a class counting as generalized when only one phrasing works",
    ),
    Mutation(
        name="certain_recognizer_state_widened",
        path="core/semantic/types.py",
        old="        if self.state not in CERTAIN_RECOGNIZER_STATES:",
        new="        if False and self.state not in CERTAIN_RECOGNIZER_STATES:",
        test="tests/semantic_phase0/test_semantic_sabotage_invariants.py::test_a_certain_recognizer_can_only_return_certain_or_abstain",
        guards="a formal recognizer returning something other than Certain or Abstain",
    ),
    # ---- Hostile review #2 -------------------------------------------------------------
    Mutation(
        name="importable_permission_token",
        path="core/semantic/types.py",
        old="        grant = self.grant\n        if not isinstance(grant, _Grant) or grant not in _ISSUED_GRANTS:",
        new="        grant = self.grant\n        if not isinstance(grant, _Grant):",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_an_allowing_permission_record_cannot_be_written_by_hand",
        guards="a grant object built by a caller counting as one the policy issued",
    ),
    Mutation(
        name="public_admission_classmethod_reintroduced",
        path="core/semantic/types.py",
        old="class AdmissionResult:\n    \"\"\"Whether one validated intent may run, and why.",
        new=(
            "class AdmissionResult:\n"
            "    admit = classmethod(lambda cls, **_kwargs: object.__new__(cls))\n\n"
            "    \"\"\"Whether one validated intent may run, and why."
        ),
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_direct_admitted_result_construction_cannot_bypass_policy",
        guards="a public classmethod recreating admitted results without production policy consultation",
    ),
    Mutation(
        name="issued_registry_manipulation_can_construct_admission",
        path="core/semantic/types.py",
        old="        if admitted:\n            raise ValueError(",
        new="        if False:\n            raise ValueError(",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_caller_manipulation_of_the_issued_registry_cannot_admit",
        guards="caller-written registry membership becoming a usable admitted result",
    ),
    Mutation(
        name="caller_assembled_result_treated_as_admission_authority",
        path="core/semantic/types.py",
        old=(
            '        if claim_frame is None or claim_frame.f_locals.get("owner") is not self:\n'
            "            return False\n"
            "        locals_ = occasion.f_locals\n"
            "        return (\n"
            '            locals_.get("result") is self'
        ),
        new=(
            '        if claim_frame is None or claim_frame.f_locals.get("owner") is None:\n'
            "            return False\n"
            "        locals_ = occasion.f_locals\n"
            "        return (\n"
            '            locals_.get("result") is not None'
        ),
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_caller_assembled_admitted_result_is_not_production_admission_authority",
        guards="a copied production occasion transferring authority to a caller-assembled result",
    ),
    Mutation(
        name="caller_frame_treated_as_production_admission_occasion",
        path="core/semantic/types.py",
        old="        if occasion.__class__ is not frame_type or occasion.f_code is not expected_code:",
        new="        if occasion.__class__ is not frame_type or False:",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_admission_authority_exposes_no_mutable_registration_graph",
        guards="a caller-owned frame replacing the exact production admission occasion",
    ),
    Mutation(
        name="permission_argument_keys_coerced_to_strings",
        path="core/semantic/types.py",
        old="                    _typed_argument_bytes(key, active) + _typed_argument_bytes(item, active),",
        new="                    _typed_argument_bytes(str(key), active) + _typed_argument_bytes(item, active),",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_permission_argument_identity_preserves_types_structure_and_map_semantics",
        guards="integer and string map keys collapsing into one permission identity",
    ),
    Mutation(
        name="late_provider_boundary_registration_not_delivered",
        path="core/provider_execution_boundary.py",
        old="        for subscriber in subscribers:",
        new="        for subscriber in ():",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_boundaries_created_after_guard_installation_are_observed_live",
        guards="provider boundaries created after guard installation escaping a stale snapshot",
    ),
    Mutation(
        name="post_class_provider_replacement_loses_boundary_role",
        path="core/provider_execution_boundary.py",
        old="        if _class_has_boundary_role(cls, name):",
        new="        if False and _class_has_boundary_role(cls, name):",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_post_class_provider_method_replacements_remain_live_boundaries",
        guards="the active callable installed after class registration escaping observation",
    ),
    Mutation(
        name="stale_admission_reused_by_later_resolution_outcome",
        path="core/semantic/types.py",
        old="            claimed_outcome is outcome",
        new="            claimed_outcome is not None",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_admitted_result_is_authority_for_one_exact_resolution_outcome_only",
        guards="one real admission occasion authorizing a later retry or reconstructed outcome",
    ),
    Mutation(
        name="instance_provider_shadow_not_observed_on_resolution",
        path="core/provider_execution_boundary.py",
        old="    if not _class_has_boundary_role(type(instance), name):",
        new="    if True:",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_instance_provider_method_replacements_remain_live_boundaries",
        guards="an instance setattr or raw __dict__ provider shadow escaping live observation",
    ),
    Mutation(
        name="hostile_getattribute_bypasses_the_invocation_seam",
        path="core/provider_execution_boundary.py",
        old=(
            "    for subscriber in subscribers:\n"
            "        subscriber(instance, name, active, label)\n"
            "    return active(*args, **kwargs)"
        ),
        new=(
            "    for subscriber in ():\n"
            "        subscriber(instance, name, active, label)\n"
            "    return active(*args, **kwargs)"
        ),
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_provider_invocation_seam_observes_the_callable_resolved_by_hostile_getattribute",
        guards="a plugin callable crossing the canonical seam without a dispatch observation",
    ),
    Mutation(
        name="inherited_callable_call_not_observed",
        path="core/provider_execution_boundary.py",
        old=(
            "    if not callable(value):\n"
            "        return None\n"
            "    for owner in type(value).__mro__:\n"
            '        call_descriptor = vars(owner).get("__call__", _MISSING)'
        ),
        new=(
            "    if not callable(value):\n"
            "        return None\n"
            "    for owner in (type(value),):\n"
            '        call_descriptor = vars(owner).get("__call__", _MISSING)'
        ),
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_supplementary_code_markers_follow_plain_callable_object_mro",
        guards="a callable object whose actual __call__ implementation is inherited escaping observation",
    ),
    Mutation(
        name="canonical_dispatch_reports_the_wrong_callable_identity",
        path="core/provider_execution_boundary.py",
        old="        subscriber(instance, name, active, label)",
        new="        subscriber(instance, name, object(), label)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_nested_descriptor_chain_is_observed_once_at_the_identity_preserving_seam",
        guards="the seam reporting an approximation instead of the exact callable it invokes",
    ),
    Mutation(
        name="canonical_seam_reimplements_special_method_lookup",
        path="core/provider_execution_boundary.py",
        old="    return active(*args, **kwargs)",
        new="    return active.__call__(*args, **kwargs)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_supplementary_code_markers_follow_plain_callable_object_mro",
        guards="the canonical seam replacing Python call dispatch with explicit __call__ lookup",
    ),
    Mutation(
        name="teacher_health_bypasses_canonical_invocation_seam",
        path="core/model_teacher_pipeline.py",
        old='            health = dict(invoke_provider_execution_boundary(adapter, "health_check") or {})',
        new="            health = dict(adapter.health_check() or {})",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_model_teacher_pipeline_observes_the_exact_dynamic_health_and_invoke_callables",
        guards="the teacher lane invoking a dynamically resolved health callable unobserved",
    ),
    Mutation(
        name="teacher_model_bypasses_canonical_invocation_seam",
        path="core/model_teacher_pipeline.py",
        old='            response = invoke_provider_execution_boundary(adapter, "invoke", request)',
        new="            response = adapter.invoke(request)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_model_teacher_pipeline_observes_the_exact_dynamic_health_and_invoke_callables",
        guards="the teacher lane invoking a dynamically resolved model callable unobserved",
    ),
    Mutation(
        name="helper_reasoning_bypasses_canonical_invocation_seam",
        path="core/llm_reasoning.py",
        old=(
            '        resp = invoke_provider_execution_boundary(adapter, "invoke", request)\n'
            "        if resp.error:"
        ),
        new=(
            "        resp = adapter.invoke(request)\n"
            "        if resp.error:"
        ),
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="helper reasoning using a second dynamic provider calling convention",
    ),
    Mutation(
        name="helper_grading_bypasses_canonical_invocation_seam",
        path="core/llm_reasoning.py",
        old=(
            '        resp = invoke_provider_execution_boundary(adapter, "invoke", request)\n'
            "        text = (resp.output_text or \"\").strip()"
        ),
        new=(
            "        resp = adapter.invoke(request)\n"
            "        text = (resp.output_text or \"\").strip()"
        ),
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="helper output grading using a second dynamic provider calling convention",
    ),
    Mutation(
        name="media_analysis_bypasses_canonical_invocation_seam",
        path="core/media_analysis_pipeline.py",
        old='        response = invoke_provider_execution_boundary(adapter, "run_text_task", request)',
        new="        response = adapter.run_text_task(request)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="media analysis invoking a dynamically resolved model callable unobserved",
    ),
    Mutation(
        name="registry_prewarm_bypasses_canonical_invocation_seam",
        path="core/model_registry.py",
        old='                results.append(invoke_provider_execution_boundary(adapter, "prewarm"))',
        new="                results.append(adapter.prewarm())",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="registry prewarm executing provider code outside the canonical seam",
    ),
    Mutation(
        name="base_text_delegation_bypasses_canonical_invocation_seam",
        path="adapters/base_adapter.py",
        old=(
            "    def run_text_task(self, request: ModelRequest) -> ModelResponse:\n"
            '        return invoke_provider_execution_boundary(self, "invoke", request)'
        ),
        new=(
            "    def run_text_task(self, request: ModelRequest) -> ModelResponse:\n"
            "        return self.invoke(request)"
        ),
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="the base text role dynamically delegating outside the canonical seam",
    ),
    Mutation(
        name="base_structured_delegation_bypasses_canonical_invocation_seam",
        path="adapters/base_adapter.py",
        old=(
            "    def run_structured_task(self, request: ModelRequest) -> ModelResponse:\n"
            '        return invoke_provider_execution_boundary(self, "invoke", request)'
        ),
        new=(
            "    def run_structured_task(self, request: ModelRequest) -> ModelResponse:\n"
            "        return self.invoke(request)"
        ),
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="the base structured role dynamically delegating outside the canonical seam",
    ),
    Mutation(
        name="base_stream_delegation_bypasses_canonical_invocation_seam",
        path="adapters/base_adapter.py",
        old='        response = invoke_provider_execution_boundary(self, "run_text_task", request)',
        new="        response = self.run_text_task(request)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="the base streaming role dynamically delegating outside the canonical seam",
    ),
    Mutation(
        name="cloud_catalog_discovery_bypasses_canonical_invocation_seam",
        path="core/cloud_broker.py",
        old='        models = invoke_provider_execution_boundary(provider, "discover_models", transport)',
        new="        models = provider.discover_models(transport)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="cloud catalog discovery executing provider code outside the canonical seam",
    ),
    Mutation(
        name="cloud_account_probe_bypasses_canonical_invocation_seam",
        path="core/cloud_broker.py",
        old='            limits = invoke_provider_execution_boundary(provider, "get_account_limits", transport)',
        new="            limits = provider.get_account_limits(transport)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="cloud account probing executing provider code outside the canonical seam",
    ),
    Mutation(
        name="cloud_model_send_bypasses_canonical_invocation_seam",
        path="core/cloud_broker.py",
        old=(
            "                    response = invoke_provider_execution_boundary(\n"
            "                        provider,\n"
            '                        "send_request",\n'
            "                        transport,\n"
            "                        call_request,\n"
            "                    )"
        ),
        new="                    response = provider.send_request(transport, call_request)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="the cloud broker sending a model request outside the canonical seam",
    ),
    Mutation(
        name="generic_cloud_health_discovery_bypasses_canonical_invocation_seam",
        path="adapters/generic_openai_cloud_provider.py",
        old='        models = invoke_provider_execution_boundary(self, "discover_models", transport)',
        new="        models = self.discover_models(transport)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="generic cloud health probing dynamically dispatching discovery outside the seam",
    ),
    Mutation(
        name="cloudflare_health_discovery_bypasses_canonical_invocation_seam",
        path="adapters/cloudflare_workers_ai_provider.py",
        old='        models = invoke_provider_execution_boundary(self, "discover_models", transport)',
        new="        models = self.discover_models(transport)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="Cloudflare health probing dynamically dispatching discovery outside the seam",
    ),
    Mutation(
        name="openrouter_health_discovery_bypasses_canonical_invocation_seam",
        path="adapters/openrouter_cloud_provider.py",
        old='        models = invoke_provider_execution_boundary(self, "discover_models", transport)',
        new="        models = self.discover_models(transport)",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_production_provider_roles_have_no_direct_dynamic_invocation_path",
        guards="OpenRouter health probing dynamically dispatching discovery outside the seam",
    ),
    Mutation(
        name="differential_reads_live_disk_capacity",
        path="scripts/semantic_phase0_base_differential.py",
        old="shutil.disk_usage = _frozen_disk_usage",
        new="shutil.disk_usage = shutil.disk_usage",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_proof_subprocess_freezes_disk_capacity_for_both_trees",
        guards="host free-space movement manufacturing candidate differences on four frozen turns",
    ),
    Mutation(
        name="grant_not_bound_to_its_operation",
        path="core/semantic/types.py",
        old="        return bool(wanted) and grant.operation == wanted and self.operation == wanted",
        new="        return bool(wanted)",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_a_grant_is_bound_to_one_operation_and_cannot_be_carried_to_another",
        guards="one capability's grant being spent on another",
    ),
    Mutation(
        name="contract_alias_authorizes_another_operation",
        path="core/semantic/admission.py",
        old="    if canonical_intent != operation:",
        new="    if False and canonical_intent != operation:",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_a_contract_alias_cannot_authorize_one_operation_under_another",
        guards="evil.delete_everything admitted under machine.inspect_specs' grant",
    ),
    Mutation(
        name="caller_supplied_decision_is_trusted",
        path="core/semantic/admission.py",
        old="    if not isinstance(decision, PermissionDecision):",
        new="    if False and not isinstance(decision, PermissionDecision):",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_a_policy_that_returns_something_other_than_a_decision_denies",
        guards="a substitute for the permission engine being read for a truthy .allowed",
    ),
    Mutation(
        name="permission_checked_for_a_different_operation",
        path="core/semantic/types.py",
        # Anchored where a caller CAN supply a mismatched pair. The same substitution inside
        # `admission.admit` is provably equivalent -- the record there is minted for
        # `intent.tool_intent` two lines earlier -- so mutating it would assert nothing.
        # The check moved into `authorizes()` when grants gained occasion binding.
        old="        if not self.allows(getattr(intent, \"tool_intent\", \"\")):",
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_an_admission_cannot_carry_a_grant_for_a_different_operation",
        guards="the operation authorized differing from the operation about to execute",
    ),
    Mutation(
        name="false_returned_model_calls_decides_invocation",
        path="core/semantic/reach.py",
        old="        return self.saw(GATE_MODEL_LANE, OUTCOME_CALL_ATTEMPTED)",
        new="        return bool(self.outcome.get(\"model_calls\"))",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_a_provider_reached_with_zero_reported_model_calls_still_counts_as_invoked",
        guards="orchestration telemetry vetoing what the invocation seam actually saw",
    ),
    Mutation(
        name="invocation_seam_not_recorded",
        path="core/memory_first_router.py",
        old="            semantic_reach.note_provider_call_attempt(",
        new="            _ = lambda **_k: None; _(",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_the_production_invocation_seam_is_wired_to_the_recorder",
        guards="the production invocation seam going unobserved",
    ),
    Mutation(
        name="response_constraint_retry_attempt_unrecorded",
        path="core/memory_first_router.py",
        old=(
            "                            retry_response = _call_adapter_task(\n"
            "                                \"run_text_task\", retry_request\n"
            "                            )"
        ),
        new="                            retry_response = adapter.run_text_task(retry_request)",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_every_real_retry_adapter_entry_is_recorded[response_constraint]",
        guards="the response-constraint repair entering the adapter without an attempt receipt",
    ),
    Mutation(
        name="response_control_retry_attempt_unrecorded",
        path="core/memory_first_router.py",
        old="                    retry_response = _call_adapter_task(\"run_text_task\", retry_request)",
        new="                    retry_response = adapter.run_text_task(retry_request)",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_every_real_retry_adapter_entry_is_recorded[response_control]",
        guards="the ordinary-response repair entering the adapter without an attempt receipt",
    ),
    Mutation(
        name="turn_planner_executor_drops_context",
        path="core/agent_runtime/turn_planner.py",
        old=(
            "                    pool.submit(\n"
            "                        contextvars.copy_context().run,\n"
            "                        _run_single,\n"
            "                        task,\n"
            "                        run_one,\n"
            "                        snapshot,\n"
            "                    ): task"
        ),
        new="                    pool.submit(_run_single, task, run_one, snapshot): task",
        test="tests/semantic_phase0/test_semantic_reach_and_receipt_fidelity.py::test_turn_planner_executor_propagates_the_turn_recorder",
        guards="parallel task execution silently losing the active turn receipt context",
    ),
    Mutation(
        name="nfd_search_folds_the_needle_to_nfc",
        path="core/semantic/canonical_text.py",
        old="        probe = self.normalize(str(needle or \"\"))",
        new="        probe = unicodedata.normalize(\"NFC\", str(needle or \"\"))",
        test="tests/semantic_phase0/test_semantic_spans_bind_to_one_text.py::test_a_search_speaks_the_representation_it_is_searching",
        guards="a needle normalized to a different form than the haystack, so it can never match",
    ),
    Mutation(
        name="computed_vocabulary_guessed_instead_of_opaque",
        path="core/semantic/lexical_authority.py",
        # The whole walk, not one branch of it: `dict.fromkeys` and `re.compile` are Attribute
        # nodes, so disabling only the Call arm left them opaque anyway and the mutation survived.
        old="    for child in ast.walk(node):",
        new="    for child in ():",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_computed_vocabulary_is_opaque_rather_than_guessed",
        guards="tuple(...split()) measured as one wrong term instead of admitted unreadable",
    ),
    Mutation(
        name="opaque_authority_still_publishes_a_score",
        path="core/semantic/generalization.py",
        old="        if not self.inventory_complete:\n            return None",
        new="        if False:\n            return None",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_making_authority_opaque_cannot_improve_the_metric",
        guards="hiding vocabulary producing a better number instead of no number",
    ),
    Mutation(
        name="candidate_drift_excluded_instead_of_reported",
        path="scripts/semantic_phase0_base_differential.py",
        # The drift check moved into the per-id loop when the comparison became total.
        old="        if _row_key(row_a) != _row_key(row_b):",
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_candidate_instability_is_a_difference_not_an_exclusion",
        guards="a drifting candidate hiding inside an exclusion it caused itself",
    ),
    Mutation(
        name="connect_ex_left_unsealed",
        path="tests/_network_seal.py",
        # The guard, not the installation. Removing the `connect_ex` line from `_install` alone is
        # absorbed by `reassert`, which re-installs it before the test body -- correct behaviour,
        # and a mutation that proves nothing. Skipping the policy consultation is the real defect.
        old="    _run(KIND_CONNECT_EX, address)",
        new="    pass  # connect_ex deliberately consults no policy",
        test="tests/semantic_phase0/test_semantic_hermeticity.py::test_connect_ex_is_sealed_for_loopback_and_unix_sockets",
        guards="connect_ex returning an errno and walking straight through the seal",
    ),
    # ---- 8. The differential must be TOTAL over the frozen ids ------------------------
    Mutation(
        name="candidate_omission_treated_as_absence",
        path="scripts/semantic_phase0_base_differential.py",
        old="        if row_a is None or row_b is None:",
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_omitting_frozen_turns_is_missing_never_a_smaller_denominator",
        guards="a turn the candidate could not answer leaving no trace at all",
    ),
    Mutation(
        name="frozen_ids_replaced_by_candidate_ids",
        path="scripts/semantic_phase0_base_differential.py",
        old="    frozen = list(expected) if expected is not None else [row[\"text\"] for row in base_a]",
        new="    frozen = [row[\"text\"] for row in cand_a]",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_omitting_frozen_turns_is_missing_never_a_smaller_denominator",
        guards="the candidate deciding the denominator of its own proof",
    ),
    Mutation(
        name="duplicate_candidate_row_silently_accepted",
        path="scripts/semantic_phase0_base_differential.py",
        old="        if text in seen:",
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_a_duplicate_frozen_id_is_rejected_rather_than_resolved",
        guards="two conflicting answers for one turn resolved by whichever arrived last",
    ),
    Mutation(
        name="unexpected_candidate_row_silently_accepted",
        path="scripts/semantic_phase0_base_differential.py",
        old="        if text not in expected:",
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_a_candidate_case_outside_the_frozen_set_is_rejected_not_counted",
        guards="a candidate padding its coverage with rows nobody asked for",
    ),
    # ---- 9. Computed lexical authority must be opaque ----------------------------------
    Mutation(
        name="computed_regex_marked_complete",
        path="core/semantic/lexical_authority.py",
        old="        if isinstance(child, ast.Name):\n            return False",
        new="        if False:\n            return False",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_a_regex_whose_only_unreadable_part_is_a_name_is_opaque",
        guards="an unresolved name inside a pattern read as if the pattern were complete",
    ),
    Mutation(
        name="comprehension_authority_vanishes",
        path="core/semantic/lexical_authority.py",
        old="    if isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):",
        new="    if False:",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_every_computed_shape_is_opaque_and_never_vanishes",
        guards="a comprehension-built table disappearing while the inventory calls itself complete",
    ),
    Mutation(
        name="string_concatenation_reported_as_terms",
        path="core/semantic/lexical_authority.py",
        old="        if _assembles_a_string(value):\n            return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f\"<computed:{symbol}>\",))",
        new="        if False:\n            return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f\"<computed:{symbol}>\",))",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_a_bare_string_concatenation_is_opaque",
        guards="'storm' + 'term' counted as two terms that match nothing",
    ),
    Mutation(
        name="restoring_the_seal_re_mints_its_tokens",
        path="tests/_network_seal.py",
        old="        _STACK[:] = list(entries)",
        new="        _STACK[:] = [(_NEXT_TOKEN + 1, name, policy) for _t, name, policy in entries]",
        test="tests/semantic_phase0/test_semantic_network_seal_composition.py::test_lifting_the_seal_for_a_probe_does_not_orphan_the_policies_that_come_back",
        guards="a lifted-and-restored policy outliving the fixture that pushed it, for the rest of the process",
    ),
    # ---- 10. Post-Codex delta ---------------------------------------------------------
    Mutation(
        name="imported_mint_can_construct_admission",
        path="core/semantic/types.py",
        old="        if admitted:\n            raise ValueError(",
        new="        if False:\n            raise ValueError(",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_an_imported_mint_can_build_an_allowing_record_and_still_cannot_admit",
        guards="an importable internal mint becoming authority through direct result construction",
    ),
    Mutation(
        name="grant_not_bound_to_its_arguments",
        path="core/semantic/types.py",
        old="        if grant.arguments_digest != wanted:",
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_a_grant_is_bound_to_the_arguments_the_policy_was_asked_about",
        guards="a grant for read_file(safe.txt) spent on read_file(/etc/shadow)",
    ),
    Mutation(
        name="grant_replayable_on_a_second_occasion",
        path="core/semantic/types.py",
        old="        if grant.spent:",
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_a_grant_authorizes_one_occasion_and_cannot_be_replayed",
        guards="one permission answer authorizing every later turn that asks the same thing",
    ),
    Mutation(
        name="concurrent_grant_consumption_is_unlocked",
        path="core/semantic/types.py",
        old="        with grant._lock:",
        new="        if True:",
        test="tests/semantic_phase0/test_semantic_typed_failure_states.py::test_concurrent_same_occasion_replay_has_exactly_one_winner",
        guards="two threads checking and spending one permission occasion outside its grant lock",
    ),
    Mutation(
        name="canonical_text_constructor_accepts_a_lie",
        path="core/semantic/canonical_text.py",
        old="        if form and unicodedata.normalize(form, self.text) != self.text:",
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_spans_bind_to_one_text.py::test_the_public_constructor_cannot_be_handed_a_lie",
        guards="NFC text labelled NFD through the public dataclass constructor",
    ),
    Mutation(
        name="canonical_text_digest_unchecked",
        path="core/semantic/canonical_text.py",
        old="        if self.digest != expected:",
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_spans_bind_to_one_text.py::test_the_public_constructor_cannot_be_handed_a_lie",
        guards="an identity belonging to a different string, so every span resolves elsewhere",
    ),
    Mutation(
        name="corpus_digest_check_removed",
        path="scripts/semantic_phase0_base_differential.py",
        old="    if seen != FROZEN_CORPUS_SHA256:",
        new="    if False:",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_loader_refuses_a_trusted_object_that_disagrees_with_the_digest_pin",
        guards="trusted artifact bytes being accepted when they disagree with the controller's pin",
    ),
    Mutation(
        name="controller_does_not_reexec_isolated",
        path="scripts/semantic_phase0_base_differential.py",
        old="if __name__ == \"__main__\" and not _bootstrap_sys.flags.isolated:",
        new="if False and __name__ == \"__main__\" and not _bootstrap_sys.flags.isolated:",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_executable_controller_reexecs_isolated_from_cwd_and_pythonpath",
        guards="candidate-controlled import shadows loading before the proof controller isolates itself",
    ),
    Mutation(
        name="frozen_corpus_git_object_not_pinned",
        path="scripts/semantic_phase0_base_differential.py",
        old='FROZEN_CORPUS_SOURCE_SHA = "c23257da1afd53ed721aa01da529ef74bd6df45a"',
        new='FROZEN_CORPUS_SOURCE_SHA = "HEAD"',
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_the_corpus_is_a_pinned_artifact_the_candidate_does_not_compute",
        guards="the current candidate commit replacing the immutable corpus source commit",
    ),
    Mutation(
        name="subprocess_connect_ex_left_open",
        path="scripts/semantic_phase0_base_differential.py",
        old="socket.socket.connect_ex = _blocked\n",
        new="",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_the_subprocess_seal_actually_refuses_every_surface_and_family",
        guards="connect_ex returning an errno and walking through the subprocess seal",
    ),
    Mutation(
        name="proof_interpreter_can_spawn_a_grandchild",
        path="scripts/semantic_phase0_base_differential.py",
        old="subprocess.Popen = _blocked_process",
        new="subprocess.Popen = subprocess.Popen",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_proof_interpreter_refuses_grandchild_creation",
        guards="a measured proof interpreter outsourcing work to an unsealed grandchild",
    ),
    Mutation(
        name="truncated_journal_treated_as_recoverable",
        path="tests/_source_guard.py",
        old="    if not raw.strip():",
        new="    if False:",
        test="tests/semantic_phase0/test_semantic_source_restoration.py::test_a_truncated_journal_fails_closed_rather_than_being_guessed_at",
        guards="a zero-byte journal beside a mutated file reading as nothing to repair",
    ),
    Mutation(
        name="journal_checksum_not_verified",
        path="tests/_source_guard.py",
        old="    if digest and hashlib.sha256(original).hexdigest() != digest:",
        new="    if False:",
        test="tests/semantic_phase0/test_semantic_source_restoration.py::test_a_journal_that_disagrees_with_its_own_checksum_is_refused",
        guards="restoring a file from bytes that are not its original",
    ),
    Mutation(
        name="stale_journal_original_outranks_git_head",
        path="tests/_source_guard.py",
        old='        if entry["original"] != trusted:',
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_source_restoration.py::test_stale_self_consistent_journal_cannot_clobber_trusted_source",
        guards="self-consistent stale journal bytes overwriting the tracked source at frozen HEAD",
    ),
    Mutation(
        name="conflicting_journals_for_one_path_are_accepted",
        path="tests/_source_guard.py",
        old="        if len(journals) > 1:",
        new="        if False:",
        test="tests/semantic_phase0/test_semantic_source_restoration.py::test_two_journals_for_one_path_fail_before_either_is_applied",
        guards="recovery guessing between two valid-looking originals for one tracked path",
    ),
    Mutation(
        name="dict_values_dropped_from_the_inventory",
        path="core/semantic/lexical_authority.py",
        old="            for node in list(value.keys) + list(value.values)",
        new="            for node in list(value.keys)",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_vocabulary_hidden_in_dict_values_is_still_measured",
        guards="a table hidden by moving it across the colon",
    ),
    Mutation(
        name="host_drift_launders_a_real_difference",
        path="scripts/semantic_phase0_base_differential.py",
        old="            if excused and not row_claims:",
        new="            if excused:",
        test="tests/semantic_phase0/test_semantic_differential_eligibility.py::test_mixed_host_drift_never_launders_a_stable_candidate_regression",
        guards="one volatile field relabelling a mixed row and erasing a stable behaviour regression",
    ),
    Mutation(
        name="bare_string_authority_vanishes",
        path="core/semantic/lexical_authority.py",
        old="    if isinstance(value, ast.Constant) and isinstance(value.value, str):",
        new="    if False and isinstance(value, ast.Constant) and isinstance(value.value, str):",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_module_level_bare_string_authority_is_measured",
        guards="a one-term module binding disappearing from lexical-authority measurement",
    ),
    Mutation(
        name="plugin_loader_called_with_wrong_arity",
        path="core/semantic/lexical_authority.py",
        old="            found = tuple(load_skills(plugin_dir, plugin_id=plugin_id))",
        new="            found = tuple(load_skills())",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_plugin_discovery_uses_the_real_loader_contract_and_measures_triggers",
        guards="plugin authority silently disappearing because discovery does not honor the shipped loader contract",
    ),
    Mutation(
        name="plugin_load_failure_swallowed_as_zero_authority",
        path="core/semantic/lexical_authority.py",
        old=(
            '            failures.append(f"plugin:{plugin_id}:load:{type(exc).__name__}")\n'
            "            continue"
        ),
        new="            continue",
        test="tests/semantic_phase0/test_semantic_ldar_generalization.py::test_plugin_load_and_manifest_failures_make_the_inventory_unmeasured",
        guards="an unavailable plugin source publishing a complete zero-authority inventory",
    ),
    Mutation(
        name="free_gateway_alias_is_not_a_discovered_boundary",
        path="core/provider_invocation_gateway.py",
        old="@provider_execution_boundary\ndef seal_direct_provider_invocation(",
        new="def seal_direct_provider_invocation(",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_the_tripwire_actually_fires_on_a_call_from_the_semantic_package",
        guards="an alias captured before guard installation bypassing attribute monkeypatches",
    ),
    Mutation(
        name="private_ollama_gateway_is_not_a_discovered_boundary",
        path="core/provider_invocation_gateway.py",
        old="@provider_execution_boundary\ndef seal_provider_invocation(",
        new="def seal_provider_invocation(",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_private_ollama_io_reaches_a_watched_free_gateway_from_semantic",
        guards="private adapter I/O reaching the real free gateway outside the zero-call proof",
    ),
    Mutation(
        name="adapter_override_loses_execution_boundary_role",
        path="adapters/base_adapter.py",
        old="        inherit_provider_execution_boundaries(cls)",
        new="        pass",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_direct_adapter_and_cloud_broker_execution_paths_trip_from_semantic",
        guards="a concrete adapter override becoming invisible to structural execution discovery",
    ),
    Mutation(
        name="actual_cloud_broker_execute_is_not_watched",
        path="core/cloud_broker.py",
        old="    @provider_execution_boundary\n    def execute(",
        new="    def execute(",
        test="tests/semantic_phase0/test_semantic_zero_provider_invocations.py::test_direct_adapter_and_cloud_broker_execution_paths_trip_from_semantic",
        guards="the actual cloud-broker execution entrypoint sitting outside the zero-call proof",
    ),
)


def _snapshot(paths: set[str]) -> dict[str, str]:
    return {path: (REPO_ROOT / path).read_text(encoding="utf-8") for path in sorted(paths)}


def _run_test(selector: str) -> int:
    env = dict(os.environ)
    # Belt and braces with `_invalidate_bytecode`: nothing written during a mutation run can become
    # the stale cache for the next one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [PYTHON, "-m", "pytest", selector, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.returncode


def _invalidate_bytecode(path: Path) -> None:
    """Delete any cached bytecode for `path`.

    Not paranoia -- this harness reported a false SURVIVOR without it. CPython invalidates a `.pyc`
    on (mtime, size), and every mutation here inserts the same ten characters ("False and "). Two
    consecutive mutations of `core/semantic/admission.py` therefore produced files of identical size
    within the same mtime second, so the second run imported the FIRST mutation's bytecode: the guard
    under test was still intact, the test passed, and the matrix reported no test detects it. The
    mutation was caught every time it was run alone, which is exactly how a harness bug looks.
    """
    cache = path.parent / "__pycache__"
    if not cache.is_dir():
        return
    for stale in cache.glob(f"{path.stem}.*.pyc"):
        stale.unlink(missing_ok=True)


def _apply(mutation: Mutation) -> bool:
    path = REPO_ROOT / mutation.path
    text = path.read_text(encoding="utf-8")
    if text.count(mutation.old) != 1:
        return False
    source_guard._write_journal(mutation.path, text.encode("utf-8"))
    path.write_text(text.replace(mutation.old, mutation.new, 1), encoding="utf-8")
    _invalidate_bytecode(path)
    return True


def self_test() -> bool:
    """The restore set must cover every file any mutation touches.

    Derived rather than listed, and checked, because a snapshot that misses a file leaves a
    deliberate defect in the tree after the run.
    """
    touched = {mutation.path for mutation in MUTATIONS}
    snapshot = _snapshot(touched)
    missing = touched - set(snapshot)
    if missing:
        print(f"self-test FAILED: not snapshotted: {sorted(missing)}")
        return False
    unanchored = [
        mutation.name
        for mutation in MUTATIONS
        if (REPO_ROOT / mutation.path).read_text(encoding="utf-8").count(mutation.old) != 1
    ]
    if unanchored:
        print(f"self-test FAILED: these mutations do not anchor to exactly one site: {unanchored}")
        return False
    print(f"self-test ok: {len(MUTATIONS)} mutations anchor uniquely across {len(touched)} files")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="check anchors and the restore set only")
    parser.add_argument("--only", default="", help="run one mutation by name")
    args = parser.parse_args()

    if args.self_test:
        return 0 if self_test() else 3

    # A previous run may have been killed mid-mutation, in which case a tracked production file is
    # still holding a deliberate defect right now. Repair it BEFORE reading any source: every
    # snapshot and every anchor check below would otherwise be taken against that defect.
    repaired = source_guard.recover_orphaned()
    if repaired:
        print(f"recovered from an earlier interrupted run: {repaired}")
    tolerated = source_guard.assert_tree_is_clean("before the mutation run", executable_only=True)
    if tolerated:
        print(f"tracked but non-executable edits present (not blocking, stated anyway): {tolerated}")

    selected = [m for m in MUTATIONS if not args.only or m.name == args.only]
    if not selected:
        print(f"no mutation named {args.only!r}")
        return 3

    snapshot = _snapshot({m.path for m in selected})

    def _restore_everything() -> None:
        for path, text in snapshot.items():
            (REPO_ROOT / path).write_text(text, encoding="utf-8")
            _invalidate_bytecode(REPO_ROOT / path)

    # SIGTERM is how a proof worker is reaped on a timeout, and `finally` does not run for it.
    # Restore, then die of the original signal so the run still looks killed.
    source_guard.install_restore_on_signal(_restore_everything)

    survivors: list[Mutation] = []
    errors: list[tuple[Mutation, str]] = []
    caught = 0

    try:
        for mutation in selected:
            if not _apply(mutation):
                errors.append((mutation, "anchor did not match exactly once"))
                continue
            # `_apply` already wrote this file's journal, durably, BEFORE it changed the source.
            # Writing it a second time here re-opened the exact window the journal exists to close:
            # a kill during that write left a truncated journal beside an already-mutated file.
            journal = source_guard._journal_path(mutation.path)
            try:
                code = _run_test(mutation.test)
            finally:
                (REPO_ROOT / mutation.path).write_text(snapshot[mutation.path], encoding="utf-8")
                _invalidate_bytecode(REPO_ROOT / mutation.path)
                if (REPO_ROOT / mutation.path).read_text(encoding="utf-8") == snapshot[mutation.path]:
                    journal.unlink(missing_ok=True)

            if code == _EXIT_TESTS_FAILED:
                caught += 1
                status = "CAUGHT"
            elif code == _EXIT_ALL_PASSED:
                survivors.append(mutation)
                status = "SURVIVED"
            else:
                errors.append((mutation, f"pytest exit {code} -- not a kill"))
                status = f"HARNESS ERROR (exit {code})"
            print(f"  {status:<24} {mutation.name}")
    finally:
        _restore_everything()
        source_guard.recover_orphaned()

    unrestored = [path for path, text in snapshot.items() if (REPO_ROOT / path).read_text(encoding="utf-8") != text]

    print()
    print(f"{len(selected)} mutations, {caught} caught, {len(survivors)} survivor(s), {len(errors)} harness error(s)")
    if survivors:
        print("\nSURVIVORS (no test detects these):")
        for mutation in survivors:
            print(f"  {mutation.name}\n      guards: {mutation.guards}\n      test:   {mutation.test}")
    if errors:
        print("\nHARNESS ERRORS:")
        for mutation, reason in errors:
            print(f"  {mutation.name}: {reason}")
    print(f"\nsources restored: {'NO -- ' + str(unrestored) if unrestored else 'YES'}")

    # Fail closed. A green matrix over a modified tracked tree has proven nothing -- the mutation
    # that was left behind is part of what every one of those tests just ran against.
    executable_dirt = [path for path in source_guard.dirty_tracked_paths() if path.endswith(".py")]
    if executable_dirt:
        print(f"tracked source is DIRTY after the run: {executable_dirt}")
        return 2
    print("tracked tree: no executable source left modified")

    if unrestored:
        return 2
    if errors:
        return 3
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
