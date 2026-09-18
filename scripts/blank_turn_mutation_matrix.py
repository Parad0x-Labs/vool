#!/usr/bin/env python3
"""Permanent source-mutation matrix for the Blank Turn repair boundary.

Each mutation is applied to a clean ``git archive`` export of the frozen HEAD. Its named guard
must turn red for the intended assertion while an unrelated fast-command control remains green.
The export is discarded afterwards; the working tree is never mutated.

Usage:
    .venv/bin/python scripts/blank_turn_mutation_matrix.py --self-test
    .venv/bin/python scripts/blank_turn_mutation_matrix.py
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Edit:
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class Mutation:
    name: str
    edits: tuple[Edit, ...]
    guard_file: str
    guard_expression: str
    expected_red: str


REQUEST_AUTHORITY = "core/agent_runtime/request_authority.py"
EMPTY_TURN = "core/agent_runtime/empty_turn.py"
AGENT = "apps/vool_agent.py"
MEDIA = "core/media_ingestion.py"
CONTINUITY = "core/runtime_continuity.py"
CHECKPOINTS = "core/agent_runtime/checkpoints.py"
CHANNEL = "core/channel_gateway.py"
FAST = "core/agent_runtime/fast_command_surface.py"
BLANK_TEST = "tests/test_a_turn_with_no_request_is_answered_not_crashed.py"
REPAIR_TEST = "tests/test_blank_turn_post_codex_repair.py"
RESTORATION_TEST = "tests/test_blank_turn_final_restoration_work.py"


def _m(
    name: str,
    path: str,
    old: str,
    new: str,
    guard_expression: str,
    expected_red: str,
    *,
    guard_file: str = BLANK_TEST,
    extra_edits: tuple[Edit, ...] = (),
) -> Mutation:
    return Mutation(
        name=name,
        edits=(Edit(path, old, new), *extra_edits),
        guard_file=guard_file,
        guard_expression=guard_expression,
        expected_red=expected_red,
    )


# The first 35 preserve the pre-review matrix's attack surface. The final five are the permanent
# additions required by the final restoration-work hostile review.
MUTATIONS: tuple[Mutation, ...] = (
    _m(
        "carried_01_front_gate_removed",
        EMPTY_TURN,
        "    return not turn_command_text(text, source_context)\n",
        "    return False\n",
        "test_a_turn_with_no_request_never_reaches_task_envelope_construction and spaces",
        "test_a_turn_with_no_request_never_reaches_task_envelope_construction",
    ),
    _m(
        "carried_02_resume_gate_removed",
        AGENT,
        "        if turn_has_no_request(effective_input):\n",
        "        if False:\n",
        "test_resuming_a_checkpoint_whose_stored_request_is_empty_is_answered",
        "test_resuming_a_checkpoint_whose_stored_request_is_empty_is_answered",
    ),
    _m(
        "carried_03_invisible_predicate_degraded_to_strip",
        REQUEST_AUTHORITY,
        '''    return "".join(\n        char\n        for char in str(text or "")\n        if not char.isspace() and unicodedata.category(char) not in _INVISIBLE_CATEGORIES\n    )\n''',
        '    return str(text or "").strip()\n',
        "test_text_carries_no_request_claims_every_invisible_turn and zero_width_space",
        "test_text_carries_no_request_claims_every_invisible_turn",
    ),
    _m(
        "carried_04_punctuation_swallowed",
        REQUEST_AUTHORITY,
        "    return not visible_request_text(text)\n",
        '    return not visible_request_text(text) or str(text).strip() in {"?!?!", "..."}\n',
        "test_text_carries_no_request_claims_nothing_a_person_typed and ellipsis_only",
        "test_text_carries_no_request_claims_nothing_a_person_typed",
    ),
    _m(
        "carried_05_evidence_text_promoted_to_command",
        REQUEST_AUTHORITY,
        "COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset()",
        'COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset({"text"})',
        "test_evidence_never_becomes_the_turns_command and attachment_extracted_text",
        "test_evidence_never_becomes_the_turns_command",
    ),
    _m(
        "carried_06_multiple_evidence_items_fused",
        REQUEST_AUTHORITY,
        "COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset()",
        'COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset({"text", "transcript"})',
        "test_evidence_items_are_never_fused_into_one_command and voice_request_plus_malicious_pdf",
        "test_evidence_items_are_never_fused_into_one_command",
        extra_edits=(
            Edit(
                REQUEST_AUTHORITY,
                '''    if len(claims) != 1:\n        return ""\n    return safe_text_boundary(claims[0], TURN_COMMAND_MATERIAL_MAX)\n''',
                '''    if not claims:\n        return ""\n    return safe_text_boundary(" ".join(claims), TURN_COMMAND_MATERIAL_MAX)\n''',
            ),
        ),
    ),
    _m(
        "carried_07_hidden_metadata_promoted",
        REQUEST_AUTHORITY,
        "COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset()",
        'COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset({"alt_text"})',
        "test_evidence_never_becomes_the_turns_command and hidden_alt_text",
        "test_evidence_never_becomes_the_turns_command",
    ),
    _m(
        "carried_08_per_item_command_cap_amplifies_turn",
        REQUEST_AUTHORITY,
        "COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset()",
        'COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset({"caption"})',
        "test_many_attachments_cannot_amplify_the_turn",
        "test_many_attachments_cannot_amplify_the_turn",
        extra_edits=(
            Edit(
                REQUEST_AUTHORITY,
                '''    if len(claims) != 1:\n        return ""\n    return safe_text_boundary(claims[0], TURN_COMMAND_MATERIAL_MAX)\n''',
                '''    if not claims:\n        return ""\n    return " ".join(safe_text_boundary(claim, TURN_COMMAND_MATERIAL_MAX) for claim in claims)\n''',
            ),
        ),
    ),
    _m(
        "carried_09_unicode_boundary_naive_slice",
        REQUEST_AUTHORITY,
        '    value = str(text or "")\n',
        '    return str(text or "")[: max(0, limit)]\n',
        "test_safe_text_boundary_never_leaves_a_broken_sequence",
        "test_safe_text_boundary_never_leaves_a_broken_sequence",
    ),
    _m(
        "carried_10_ambiguous_transcript_authoritative",
        REQUEST_AUTHORITY,
        "COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset()",
        'COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset({"transcript"})',
        "test_evidence_items_are_never_fused_into_one_command and voice_request_plus_malicious_pdf",
        "test_evidence_items_are_never_fused_into_one_command",
    ),
    _m(
        "carried_11_turn_item_cap_removed",
        REQUEST_AUTHORITY,
        "TURN_EVIDENCE_ITEMS_MAX = 64",
        "TURN_EVIDENCE_ITEMS_MAX = 50000",
        "test_turn_evidence_budget_is_the_fixed_runtime_contract",
        "test_turn_evidence_budget_is_the_fixed_runtime_contract",
        guard_file=REPAIR_TEST,
    ),
    _m(
        "carried_12_bounded_primitive_materializes_every_item",
        REQUEST_AUTHORITY,
        "        taken = tuple(islice(iter(items), bound))\n",
        "        taken = tuple(iter(items))\n",
        "test_bounded_evidence_items_never_walks_what_it_does_not_take",
        "test_bounded_evidence_items_never_walks_what_it_does_not_take",
    ),
    _m(
        "carried_13_descriptor_reads_past_cap",
        EMPTY_TURN,
        '    evidence = bounded_evidence((source_context or {}).get("external_evidence"))\n',
        '    evidence = bounded_evidence((source_context or {}).get("external_evidence"), limit=50_000)\n',
        "test_describe_turn_attachments_is_bounded_and_unbroken",
        "test_describe_turn_attachments_is_bounded_and_unbroken",
    ),
    _m(
        "carried_14_normalizer_processes_unbounded_source",
        MEDIA,
        "    raw_items = list(evidence.items)\n",
        '    raw_items = list(source_context.get("external_evidence") or [])\n',
        "test_direct_media_ingestion_defends_itself",
        "test_direct_media_ingestion_defends_itself",
    ),
    _m(
        "carried_15_gateway_copies_every_attachment",
        CHANNEL,
        '        "external_evidence": bounded_evidence_items(request.attachments),\n',
        '        "external_evidence": list(request.attachments or []),\n',
        "test_the_channel_gateway_bounds_attachments_at_ingress",
        "test_the_channel_gateway_bounds_attachments_at_ingress",
    ),
    _m(
        "carried_16_runtime_frontdoor_bound_removed",
        AGENT,
        '''        if isinstance(runtime_source_context.get("external_evidence"), (list, tuple)) or (\n            runtime_source_context.get("external_evidence") is not None\n        ):\n''',
        '''        if False and (isinstance(runtime_source_context.get("external_evidence"), (list, tuple)) or (\n            runtime_source_context.get("external_evidence") is not None\n        )):\n''',
        "test_the_runtime_bounds_the_context_it_was_handed",
        "test_the_runtime_bounds_the_context_it_was_handed",
    ),
    _m(
        "carried_17_contextual_urls_ignore_remaining_budget",
        MEDIA,
        '    for match in islice(_URL_RE.finditer(user_input or ""), evidence.remaining):\n',
        '    for match in _URL_RE.finditer(user_input or ""):\n',
        "test_every_evidence_origin_shares_one_turn_budget and full_budget_plus_one_url",
        "test_every_evidence_origin_shares_one_turn_budget",
    ),
    _m(
        "carried_18_remaining_capacity_does_not_decrement",
        REQUEST_AUTHORITY,
        "        return max(0, self.limit - self.taken)\n",
        "        return self.limit\n",
        "test_the_canonical_view_reports_remaining_capacity_for_other_origins",
        "test_the_canonical_view_reports_remaining_capacity_for_other_origins",
    ),
    _m(
        "carried_19_filtered_count_falsely_exact",
        REQUEST_AUTHORITY,
        "        return self.source_exhausted\n",
        "        return True\n",
        "test_an_exact_count_is_claimed_only_when_the_source_actually_ended",
        "test_an_exact_count_is_claimed_only_when_the_source_actually_ended",
    ),
    _m(
        "carried_20_unknown_kind_interpolated",
        EMPTY_TURN,
        '    return _ATTACHMENT_NOUNS.get(str(kind or "").strip().lower(), _NEUTRAL_NOUN)\n',
        '    return _ATTACHMENT_NOUNS.get(str(kind or "").strip().lower(), str(kind or "attachment"))\n',
        "test_a_hostile_attachment_kind_cannot_write_a_sentence_into_the_reply",
        "test_a_hostile_attachment_kind_cannot_write_a_sentence_into_the_reply",
    ),
    _m(
        "carried_21_plausible_ascii_kind_accepted",
        EMPTY_TURN,
        '    "social_post": "post",\n',
        '    "social_post": "post",\n    "system message": "system message",\n',
        "test_a_plausible_but_unknown_kind_is_never_spoken_by_the_runtime and system message",
        "test_a_plausible_but_unknown_kind_is_never_spoken_by_the_runtime",
    ),
    _m(
        "carried_22_ansi_kind_accepted",
        EMPTY_TURN,
        '    "social_post": "post",\n',
        '    "social_post": "post",\n    "\\x1b[31m": "\\x1b[31m",\n',
        "test_a_hostile_attachment_kind_never_reaches_the_reply and ansi_colour",
        "test_a_hostile_attachment_kind_never_reaches_the_reply",
    ),
    _m(
        "carried_23_bidi_kind_accepted",
        EMPTY_TURN,
        '    "social_post": "post",\n',
        '    "social_post": "post",\n    "\\u202e": "\\u202e",\n',
        "test_a_hostile_attachment_kind_never_reaches_the_reply and rtl_override",
        "test_a_hostile_attachment_kind_never_reaches_the_reply",
    ),
    _m(
        "carried_24_attachment_wording_promises_retention",
        EMPTY_TURN,
        '            "done and I\'ll work from that."\n',
        '            "done; tell me what to look for and I\'ll go through it."\n',
        "test_the_attachment_reply_does_not_promise_access_it_does_not_have",
        "test_the_attachment_reply_does_not_promise_access_it_does_not_have",
    ),
    _m(
        "carried_25_checkpoint_adapter_rebound_removed",
        AGENT,
        '        if checkpoint_source_context.get("external_evidence") is not None:\n',
        '        if False and checkpoint_source_context.get("external_evidence") is not None:\n',
        "test_runtime_rebounds_an_oversized_checkpoint_adapter_result_before_downstream_work",
        "test_runtime_rebounds_an_oversized_checkpoint_adapter_result_before_downstream_work",
        guard_file=REPAIR_TEST,
    ),
    _m(
        "carried_26_recognized_view_accepts_malformed_items",
        REQUEST_AUTHORITY,
        "        return tuple(item for item in self.items if isinstance(item, dict))\n",
        "        return tuple(self.items)\n",
        "test_an_exact_count_is_claimed_only_when_the_source_actually_ended",
        "test_an_exact_count_is_claimed_only_when_the_source_actually_ended",
    ),
    _m(
        "carried_27_source_exhaustion_forged",
        REQUEST_AUTHORITY,
        "    return BoundedEvidence(items=taken, limit=bound, source_exhausted=len(taken) < bound)\n",
        "    return BoundedEvidence(items=taken, limit=bound, source_exhausted=True)\n",
        "test_an_exact_count_is_claimed_only_when_the_source_actually_ended",
        "test_an_exact_count_is_claimed_only_when_the_source_actually_ended",
    ),
    _m(
        "carried_28_no_request_mints_fake_task_id",
        FAST,
        '_NON_TASK_FAST_PATH_REASONS = frozenset({"empty_turn_fast_path"})\n',
        "_NON_TASK_FAST_PATH_REASONS = frozenset()\n",
        "test_a_turn_with_no_request_mints_no_task_id_and_completes_no_task and spaces",
        "test_a_turn_with_no_request_mints_no_task_id_and_completes_no_task",
    ),
    _m(
        "carried_29_no_request_emits_fake_completion",
        FAST,
        '''    if is_task:\n        agent._emit_runtime_event(\n            source_context,\n            event_type="task_completed",\n''',
        '''    if True:\n        agent._emit_runtime_event(\n            source_context,\n            event_type="task_completed",\n''',
        "test_a_turn_with_no_request_mints_no_task_id_and_completes_no_task and spaces",
        "test_a_turn_with_no_request_mints_no_task_id_and_completes_no_task",
    ),
    _m(
        "new_30_checkpoint_pre_materialization_bound_removed",
        CONTINUITY,
        '''            LIMIT ?\n            """,\n            (evidence_path, checkpoint_id, bounded_limit),\n''',
        '''            LIMIT 50000\n            """,\n            (evidence_path, checkpoint_id),\n''',
        "test_checkpoint_restoration_materializes_nonzero_bounded_work_at_the_load_boundary",
        "test_checkpoint_restoration_materializes_nonzero_bounded_work_at_the_load_boundary",
        guard_file=REPAIR_TEST,
    ),
    _m(
        "new_31_checkpoint_serialization_bound_removed",
        CONTINUITY,
        '''        stable["external_evidence"] = bounded_evidence_items(\n            stable.get("external_evidence")\n        )\n''',
        '        stable["external_evidence"] = list(stable.get("external_evidence") or [])\n',
        "test_checkpoint_serialization_never_receives_an_oversized_evidence_collection",
        "test_checkpoint_serialization_never_receives_an_oversized_evidence_collection",
        guard_file=REPAIR_TEST,
    ),
    _m(
        "new_32_restored_evidence_replaced_by_fresh",
        CONTINUITY,
        '''        merged_source_context["external_evidence"] = compose_evidence_origins(\n            stored_evidence,\n            incoming_evidence,\n        )\n''',
        '        merged_source_context["external_evidence"] = list(incoming_evidence or [])\n',
        "test_reproduces_fresh_evidence_replacing_instead_of_composing_with_restored_evidence",
        "test_reproduces_fresh_evidence_replacing_instead_of_composing_with_restored_evidence",
        guard_file=REPAIR_TEST,
    ),
    _m(
        "new_33_legacy_request_text_accepted_without_provenance",
        CHECKPOINTS,
        '''    if resumable and not checkpoint_request_has_authority(\n        resumable,\n        expected_session_id=session_id,\n    ):\n''',
        '''    if False and resumable and not checkpoint_request_has_authority(\n        resumable,\n        expected_session_id=session_id,\n    ):\n''',
        "test_reproduces_legacy_request_text_becoming_command_authority_without_provenance",
        "test_reproduces_legacy_request_text_becoming_command_authority_without_provenance",
        guard_file=REPAIR_TEST,
    ),
    _m(
        "new_34_malformed_string_crashes_ingestion",
        MEDIA,
        '''    if not isinstance(item, Mapping):\n        return None\n''',
        '''    if False and not isinstance(item, Mapping):\n        return None\n''',
        "test_reproduces_malformed_external_evidence_crashing_media_ingestion",
        "test_reproduces_malformed_external_evidence_crashing_media_ingestion",
        guard_file=REPAIR_TEST,
        extra_edits=(
            Edit(
                MEDIA,
                '''    try:\n        item_values = dict(item)\n    except Exception:\n        return None\n''',
                "    item_values = item\n",
            ),
        ),
    ),
    _m(
        "new_35_malformed_evidence_refunds_raw_budget",
        MEDIA,
        "    raw_items = list(evidence.items)\n",
        "    raw_items = list(evidence.recognized)\n",
        "test_malformed_items_consume_the_raw_budget_and_cannot_force_unbounded_scanning",
        "test_malformed_items_consume_the_raw_budget_and_cannot_force_unbounded_scanning",
        guard_file=REPAIR_TEST,
    ),
    _m(
        "restoration_36_turn_cache_reuse_removed",
        CONTINUITY,
        "    if restoration_scope is not None and clean_checkpoint_id in restoration_scope.cache:\n",
        "    if False and restoration_scope is not None and clean_checkpoint_id in restoration_scope.cache:\n",
        "test_one_resume_turn_has_one_total_checkpoint_evidence_work_budget and same_both and 100",
        "test_one_resume_turn_has_one_total_checkpoint_evidence_work_budget",
        guard_file=RESTORATION_TEST,
    ),
    _m(
        "restoration_37_source_context_decoded_independently_again",
        CONTINUITY,
        '    source_has_evidence = "external_evidence" in source_context\n',
        '''    _load_bounded_checkpoint_json_object(
        conn,
        checkpoint_id=checkpoint_id,
        json_column="source_context_json",
        evidence_path="$.external_evidence",
        evidence_limit=evidence_limit,
    )
    source_has_evidence = "external_evidence" in source_context
''',
        "test_one_resume_turn_has_one_total_checkpoint_evidence_work_budget and same_both and 100",
        "test_one_resume_turn_has_one_total_checkpoint_evidence_work_budget",
        guard_file=RESTORATION_TEST,
    ),
    _m(
        "restoration_38_state_json_decoded_independently_again",
        CONTINUITY,
        "        evidence_limit=0 if source_has_evidence else remaining,\n",
        "        evidence_limit=evidence_limit,\n",
        "test_one_resume_turn_has_one_total_checkpoint_evidence_work_budget and same_both and 100",
        "test_one_resume_turn_has_one_total_checkpoint_evidence_work_budget",
        guard_file=RESTORATION_TEST,
    ),
    _m(
        "restoration_39_whole_turn_counter_reset_between_consumers",
        CONTINUITY,
        "    restoration_scope = _CHECKPOINT_RESTORATION_SCOPE.get()\n    if restoration_scope is not None and clean_checkpoint_id in restoration_scope.cache:\n",
        "    restoration_scope = _CHECKPOINT_RESTORATION_SCOPE.get()\n    if restoration_scope is not None:\n        restoration_scope.cache.clear()\n        restoration_scope.evidence_work = 0\n    if restoration_scope is not None and clean_checkpoint_id in restoration_scope.cache:\n",
        "test_agent_run_once_owns_the_restoration_scope_for_all_checkpoint_consumers",
        "test_agent_run_once_owns_the_restoration_scope_for_all_checkpoint_consumers",
        guard_file=RESTORATION_TEST,
    ),
    _m(
        "restoration_40_repeated_restore_with_per_call_budget",
        CONTINUITY,
        "    if restoration_scope is not None and clean_checkpoint_id in restoration_scope.cache:\n",
        "    if False and restoration_scope is not None and clean_checkpoint_id in restoration_scope.cache:\n",
        "test_agent_run_once_owns_the_restoration_scope_for_all_checkpoint_consumers",
        "test_agent_run_once_owns_the_restoration_scope_for_all_checkpoint_consumers",
        guard_file=RESTORATION_TEST,
        extra_edits=(
            Edit(
                CONTINUITY,
                '''                evidence_limit = (
                    restoration_scope.remaining_evidence_work
                    if restoration_scope is not None
                    else _checkpoint_evidence_limit()
                )
''',
                "                evidence_limit = _checkpoint_evidence_limit()\n",
            ),
        ),
    ),
)


CONTROL = (
    "tests/test_agent_runtime_fast_command_surface.py::"
    "test_fast_command_surface_help_and_capability_truth_facades_delegate_to_extracted_module"
)


def _apply_edit(root: Path, edit: Edit) -> str:
    path = root / edit.path
    original = path.read_text()
    count = original.count(edit.old)
    if count != 1:
        raise RuntimeError(f"{edit.path}: mutation pattern matched {count} times, expected once")
    path.write_text(original.replace(edit.old, edit.new, 1))
    return original


def _run_pytest(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    env.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args, "--tb=short"],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )


def _archive_head(repo: Path, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        tar.extractall(destination, filter="data")


def _self_test(repo: Path) -> None:
    if len(MUTATIONS) != 40:
        raise RuntimeError(f"expected 40 mutations, found {len(MUTATIONS)}")
    names = [mutation.name for mutation in MUTATIONS]
    if len(names) != len(set(names)):
        raise RuntimeError("mutation names must be unique")
    for mutation in MUTATIONS:
        for edit in mutation.edits:
            count = (repo / edit.path).read_text().count(edit.old)
            if count != 1:
                raise RuntimeError(
                    f"{mutation.name}: {edit.path} pattern matched {count} times, expected once"
                )
    print(f"self-test OK: {len(MUTATIONS)} unique real-source mutations")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    _self_test(repo)
    if args.self_test:
        return 0

    results: list[tuple[str, bool, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="blank_turn_mutations_") as temp_dir:
        root = Path(temp_dir)
        _archive_head(repo, root)
        for mutation in MUTATIONS:
            originals: dict[Path, str] = {}
            try:
                for edit in mutation.edits:
                    path = root / edit.path
                    originals.setdefault(path, path.read_text())
                    _apply_edit(root, edit)
                guard = _run_pytest(
                    root,
                    [mutation.guard_file, "-k", mutation.guard_expression],
                )
                caught = guard.returncode != 0 and mutation.expected_red in guard.stdout
                control = _run_pytest(root, [CONTROL])
                control_green = control.returncode == 0
                detail = guard.stdout[-1200:] if not caught else ""
                if not control_green:
                    detail += "\nCONTROL:\n" + control.stdout[-1200:]
                results.append((mutation.name, caught, control_green, detail))
                state = "CAUGHT" if caught and control_green else "ERROR"
                print(f"{state:7} {mutation.name}", flush=True)
            finally:
                for path, content in originals.items():
                    path.write_text(content)

    survivors = [name for name, caught, control, _ in results if not caught or not control]
    print("\nBLANK TURN MUTATION MATRIX")
    print(f"caught={len(results) - len(survivors)} total={len(results)} survivors={len(survivors)}")
    for name, caught, control, detail in results:
        if caught and control:
            continue
        print(f"\n{name}: caught={caught} control_green={control}\n{detail}")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
