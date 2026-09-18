#!/usr/bin/env python3
"""C24's canonical runner: all fifteen historical semantic families, at one SHA.

The C24 row enumerates fifteen failure families that motivated this build and asks
for each to be replayed with real execution plus at least one mutation that bites.
What existed instead was `tests/red_b_served_gauntlet.py`, which covers four of the
fifteen, is not collected by pytest at all (`python_files = ["test_*.py"]`), and
needs a daemon on 127.0.0.1:11435 that it does not launch. Its
`tests/red_baselines/*.json` are dead: `grep red_baselines` over every `*.py`
returns nothing, so no harness ever loaded them, and they store literal served text
from a different checkout — a prose snapshot, which is not semantic proof.

This replaces the hand method with a runner. For every family it declares:

* the **active tests** that exercise the family's semantics here — real node IDs,
  run by this tool, not cited from a document;
* whether the discharge is **served** (a booted daemon or the real dispatcher) or
  in-process, stated per family rather than claimed for the set;
* the **production seam** the family's truth actually depends on; and
* a **mutation** applied to that seam, after which the same tests must go RED.

## What a mutation here proves, and what it does not

The mutator rebinds the seam everywhere it is already bound — the defining module
and every module in `sys.modules` holding a reference — after collection, so a test
that did `from x import y` is mutated too. That closes the usual escape where a
mutation is a silent no-op.

Two modes, and the ledger records which was used, because they prove different
things:

``absent``   the seam raises. A family that stays GREEN is not exercising that seam
             at all, so the test's claim to cover the family is unsupported. It
             does NOT prove the assertions discriminate a *subtly wrong* answer.
``neutered`` the seam returns a plausible but wrong value. This one does prove
             discrimination, and it is used where the seam's contract is simple
             enough to corrupt precisely.

A family whose mutation does not bite is reported as **NO-OP**, which is a finding
about the tests, not a pass.

Run:
    python -m tools.c24_family_gauntlet --list
    python -m tools.c24_family_gauntlet --run
    python -m tools.c24_family_gauntlet --run --mutate --json out.json --md out.md
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
PYTEST_BASE = ["-q", "-p", "no:randomly", "--no-header"]


@dataclasses.dataclass(frozen=True)
class Mutation:
    """One mutation of a family's production seams, plus what its biting would prove.

    Usually one seam. Sometimes more: a property carried REDUNDANTLY cannot be falsified
    by starving one carrier, and pretending otherwise produces a no-op that reads as
    "the family is fine". Family 8 is the measured case -- turn-1 content reaches turn 3
    by three independent routes, and starving any one, or even two of the three, leaves
    the served assertion green.

    `extra` names the additional (target, build) pairs. Every target is applied before the
    run and every one is restored in the same `finally`, so a kill mid-run cannot leave a
    mutated module behind in the shared checkout.
    """

    target: str  #: "dotted.module:attribute"
    mode: str  #: "absent" | "neutered"
    why: str
    #: Python source for `def build(orig): return <replacement callable>`.
    #: `absent` mutations leave this empty and get the standard raiser.
    build: str = ""
    #: Additional seams starved by the SAME mutation: ((target, build), ...).
    extra: tuple[tuple[str, str], ...] = ()

    def seams(self) -> tuple[tuple[str, str], ...]:
        return ((self.target, self.build), *self.extra)


#: How far out the family's discharge actually reaches. `served` is reserved for the
#: strictest of these, because that is the only one that exercises the product the way
#: an operator does; the middle rung is real code and real routing, but no socket.
TRANSPORT_BOOTED_DAEMON = "booted-daemon"        #: a daemon process, real HTTP over a port
TRANSPORT_IN_PROCESS_DOOR = "in-process-door"    #: the real dispatcher, called directly
TRANSPORT_IN_PROCESS = "in-process"              #: neither


@dataclasses.dataclass(frozen=True)
class Family:
    number: int
    name: str
    nodeids: tuple[str, ...]
    served: bool  #: True ONLY for TRANSPORT_BOOTED_DAEMON
    served_note: str
    production_seam: str
    mutation: Mutation
    notes: str
    #: For a served family, the node that actually drives the daemon. A mutation that
    #: reds only this family's in-process siblings has NOT touched the served path, and
    #: must not be scored as covering it.
    served_nodeid: str = ""


FAMILIES: tuple[Family, ...] = (
    Family(
        number=1,
        name="referential follow-ups and retry ancestry",
        nodeids=(
            "tests/test_a9_turn_routing_served.py::TestServedFailureLifecycle::test_fail_then_why_then_retry_over_the_wire",
            "tests/test_ordinal_anaphora.py::test_ordinal_followup_resolves_latest_bulleted_assistant_item",
            "tests/test_attempt_retry.py::ExecuteAttemptRetryTests::test_retry_creates_new_attempt_linked_to_parent_and_root",
        ),
        served=True,
        served_note="three real HTTP turns on one booted daemon: fail, 'why did that fail?', 'retry that exact request'",
        served_nodeid=(
            "tests/test_a9_turn_routing_served.py::TestServedFailureLifecycle::test_fail_then_why_then_retry_over_the_wire"
        ),
        production_seam="core.turn_routing.mint_retry_plan",
        mutation=Mutation(
            target="core.turn_routing:mint_retry_plan",
            mode="absent",
            why="if the retry plan is never minted, no ancestry can be carried and the lineage assertions must fail",
        ),
        notes="the retry carries retry_of_plan_id == the first plan_id, generation 2, a new turn_id, and the original request id",
    ),
    Family(
        number=2,
        name="temporary-rule arm/consume/decrement/expire/cancel/restart",
        nodeids=(
            "tests/test_a9_turn_routing_served.py::TestServedTemporaryRules::test_provider_only_rule_binds_expires_exactly_and_survives_restart",
            "tests/test_a9_turn_routing_authority.py::TestTemporaryRules::test_arm_then_decrement_per_turn_not_per_call",
            "tests/test_a9_turn_routing_authority.py::TestTemporaryRules::test_rules_survive_restart_by_reloading_from_disk",
            "tests/test_a9_turn_routing_authority.py::TestTemporaryRules::test_cancel_kills_the_rule_immediately",
        ),
        served=True,
        served_note="arms a 2-turn rule, restarts the daemon, then proves turns 2-3 route remote and turn 4 falls back",
        served_nodeid=(
            "tests/test_a9_turn_routing_served.py::TestServedTemporaryRules::test_provider_only_rule_binds_expires_exactly_and_survives_restart"
        ),
        production_seam="core.turn_routing.consume_rules_for_mint",
        mutation=Mutation(
            target="core.turn_routing:consume_rules_for_mint",
            mode="neutered",
            why="a decrement that never decrements: the rule stays armed forever, so expiry-after-exactly-N must fail",
            build=(
                "def build(orig):\n"
                "    import core.turn_routing as tr\n"
                "    def _mutant(*, session_id, conversation_ref='', project_ref='', now_unix_ms=None):\n"
                "        now = now_unix_ms if now_unix_ms is not None else tr._now_ms()\n"
                "        return tr._live_rules_for(session_id=session_id,\n"
                "                                  conversation_ref=conversation_ref,\n"
                "                                  project_ref=project_ref, now_unix_ms=now)\n"
                "    return _mutant\n"
            ),
        ),
        notes="consume_rules_for_mint is documented in-source as the ONLY decrement",
    ),
    Family(
        number=3,
        name="exact identifiers/URLs/literal bytes",
        nodeids=(
            "tests/test_strict_literal_output_p0.py::test_code_and_url_payloads_are_served_verbatim",
            "tests/test_strict_literal_output_p0.py::test_interior_whitespace_is_never_collapsed",
            "tests/test_corrected_delimited_literal_contract.py::test_complete_clean_delimited_literals_bind_exact_canonical_bytes",
        ),
        served=False,
        served_note="in-process at the API response boundary; no socket",
        production_seam="core.raw_output_contract.parse_raw_output_contract",
        mutation=Mutation(
            target="core.raw_output_contract:parse_raw_output_contract",
            mode="neutered",
            why="collapse interior whitespace in the bound literal: a corruption a prose check would miss and a byte check must not",
            build=(
                "def build(orig):\n"
                "    import dataclasses, re\n"
                "    def _mutant(*a, **k):\n"
                "        out = orig(*a, **k)\n"
                "        text = getattr(out, 'exact_text', None)\n"
                "        if isinstance(text, str) and text:\n"
                "            try:\n"
                "                return dataclasses.replace(out, exact_text=re.sub(r'\\s{2,}', ' ', text))\n"
                "            except Exception:\n"
                "                return out\n"
                "        return out\n"
                "    return _mutant\n"
            ),
        ),
        # The first mutation here targeted apply_exact_response_control and was a
        # measured NO-OP: the interior-whitespace assertion runs against the BINDER,
        # and the two payloads that do reach the boundary contain no double spaces to
        # collapse. Recorded rather than quietly swapped -- the no-op was the finding
        # that located the real seam.
        notes="a URL with query, ampersand and fragment must survive the final boundary byte-for-byte",
    ),
    Family(
        number=4,
        name="quantities and units",
        nodeids=(
            "tests/test_kernel_turn_contract.py::test_computed_receipt_cannot_relabel_a_world_units",
            "tests/test_kernel_turn_contract.py::test_stated_quantity_forms_ground_percent_and_time",
            "tests/test_kernel_evidence_types.py::test_a_decimal_does_not_false_match_an_integer_with_the_same_digits",
        ),
        served=False,
        served_note="in-process at the kernel claim validator",
        production_seam="core.kernel.evidence_types.validate_claims",
        mutation=Mutation(
            target="core.kernel.evidence_types:validate_claims",
            mode="absent",
            why="with the validator gone, '460 km' may be relabelled '460 kWh' and the unit-swap assertion must fail",
        ),
        notes="a receipt's number may not be relabelled into a different world quantity",
    ),
    Family(
        number=5,
        name="mixed demands",
        nodeids=(
            "tests/test_mixed_demand_execution_p0.py::test_B_the_three_demand_turn_executes_every_supported_demand",
            "tests/test_mixed_demand_terminal_closure_p0.py::test_C_eight_units_all_served_in_any_order",
            "tests/test_a_message_asking_several_things_is_planned_into_all_of_them.py::test_the_three_part_message_becomes_three_requests",
        ),
        served=False,
        served_note="in-process; no HTTP-served mixed-demand test exists in this tree (recorded as the family's gap)",
        production_seam="core.agent_runtime.demand_ownership.execution_units",
        mutation=Mutation(
            target="core.agent_runtime.demand_ownership:execution_units",
            mode="neutered",
            why="drop every unit after the first: the classic partial answer, which a one-part check would pass",
            build=(
                "def build(orig):\n"
                "    def _mutant(text):\n"
                "        units = orig(text)\n"
                "        return units[:1]\n"
                "    return _mutant\n"
            ),
        ),
        notes="one turn, three demands, all three discharged in one composed answer",
    ),
    Family(
        number=6,
        name="strict output",
        nodeids=(
            "tests/test_corrected_delimited_literal_contract.py::test_real_run_once_set5_16_finishes_with_zero_model_tool_or_web_calls",
            "tests/test_an_exact_output_contract_survives_every_decorator.py::test_the_last_mile_repair_removes_caveat_explanation_and_bullet",
            "tests/test_raw_output_contract.py::test_markdown_and_json_wrappers_are_unwrapped_when_explicitly_forbidden",
        ),
        served=False,
        served_note="drives a real VoolAgent.run_once; in-process",
        production_seam="core.raw_output_contract.parse_raw_output_contract",
        mutation=Mutation(
            target="core.raw_output_contract:parse_raw_output_contract",
            mode="neutered",
            why="return None: the contract is never recognised, so the wrapper text survives and strictness is lost",
            build=(
                "def build(orig):\n"
                "    def _mutant(*a, **k):\n"
                "        return None\n"
                "    return _mutant\n"
            ),
        ),
        notes="a stipulated literal is served with zero model, tool or web calls and no wrapper prose",
    ),
    Family(
        number=7,
        name="terminal refusal/failure persistence",
        nodeids=(
            "tests/test_final_answer_author_eligibility_p0.py::test_a_failed_escalation_cannot_turn_a_refusal_into_an_answer",
            "tests/test_final_answer_author_eligibility_p0.py::test_refinalizing_a_refusal_returns_the_same_bytes",
            "tests/foundation/test_f1e_delivery_terminal.py::test_blank_done_only_stream_never_generic_success",
        ),
        served=False,
        served_note="in-process; the served sibling for this family is family 1's failure lifecycle",
        production_seam="core.final_answer_authorship.gate_authored_content",
        mutation=Mutation(
            target="core.final_answer_authorship:gate_authored_content",
            mode="absent",
            why="with the gate gone a refusal has nothing making it a fixed point, so re-finalizing must diverge",
        ),
        notes="a refusal is idempotent: re-finalizing returns the same bytes and never becomes an answer",
    ),
    Family(
        number=8,
        name="local -> cloud -> local continuity",
        nodeids=(
            "tests/test_a9_turn_routing_served.py::TestServedLocalityHops::test_local_cloud_local_preserves_the_chats_context",
            "tests/test_model_orchestration_integration.py::test_paid_approval_validates_result_and_returns_local",
            "tests/test_model_handoff_capsule.py::test_return_to_local_is_deterministic_and_drops_paid_provider_state",
        ),
        served=True,
        served_note="three POSTs to /api/chat on one booted daemon in one chat_id, crossing local -> remote -> local",
        served_nodeid=(
            "tests/test_a9_turn_routing_served.py::TestServedLocalityHops::test_local_cloud_local_preserves_the_chats_context"
        ),
        production_seam="storage.dialogue_memory.recent_dialogue_turns + core.turn_context.compile_turn_context",
        mutation=Mutation(
            target="storage.dialogue_memory:recent_dialogue_turns",
            mode="neutered",
            why="starve BOTH independent carriers of prior-turn content: the chat then reads one turn deep and turn 1's codeword cannot reach turn 3",
            build=(
                "def build(orig):\n"
                "    def _mutant(*a, **k):\n"
                "        rows = orig(*a, **k)\n"
                "        try:\n"
                "            rows = list(rows)\n"
                "        except Exception:\n"
                "            return rows\n"
                "        # ORDER BY created_at DESC, so rows[0] is the turn being served.\n"
                "        return rows[:1]\n"
                "    return _mutant\n"
            ),
            extra=(
                (
                    "core.turn_context:compile_turn_context",
                    "def build(orig):\n"
                    "    def _mutant(*a, **k):\n"
                    "        compilation = orig(*a, **k)\n"
                    "        try:\n"
                    "            compilation.items = []\n"
                    "        except Exception:\n"
                    "            return compilation\n"
                    "        return compilation\n"
                    "    return _mutant\n",
                ),
            ),
        ),
        # MEASURED: no SINGLE-seam mutation can falsify this family, because the
        # continuity property is carried REDUNDANTLY.
        # Dumping the exact 5,366-character prompt the assertion reads shows turn 1's
        # codeword arriving by three independent routes:
        #   1. the authoritative-visible-history block (history_messages);
        #   2. the "Relevant Context: - Recent dialogue turn: ..." lines
        #      (_dialogue_items -> recent_dialogue_turns);
        #   3. a retrieved [user]/[assistant] memory item carrying the whole turn.
        # Verified by running the served node with each seam starved: transcript alone
        # truncated -> still green; recent_dialogue_turns alone truncated -> still green;
        # BOTH starved together -> still green. And the seam IS reached (a counting probe
        # recorded 15 calls), so this is not the old plugin-never-reached-the-daemon
        # problem -- that one is fixed, and family 8 is where it was proven fixed.
        # Route 3 was then traced to the C09 turn-context admission lane:
        # core/web/api/runtime.py mints "[user]...[/user][assistant]..." pages into
        # turn_context_admissions, and core.turn_context:compile_turn_context is the only
        # reader that puts them back into a prompt. Starving routes 2 and 3 together
        # turns the served assertion RED; adding route 1 is redundant, measured, so the
        # minimal set is two seams and that is what runs. Both are restored in one
        # finally, and the restoration digest covers every mutated file.
        notes="the local provider's turn-3 prompt still contains the codeword introduced in turn 1",
    ),
    Family(
        number=9,
        name="duplicate request IDs",
        nodeids=(
            "tests/test_a6_effect_reconciliation.py::R10ConcurrentReserve::test_concurrent_identical_effects_cannot_double_dispatch",
            "tests/test_retry_idempotency_repair.py::ExecuteAttemptRetryIdempotencyTests::test_two_concurrent_retry_calls_same_trigger_turn_fetch_exactly_once",
            "tests/test_event_hash_chain.py::EventHashChainTests::test_append_hashed_event_is_idempotent_for_same_event_id",
        ),
        served=False,
        served_note="in-process, but genuinely concurrent: 16 threads race one logical effect id",
        production_seam="core.runtime_continuity.reserve_logical_effect",
        mutation=Mutation(
            target="core.runtime_continuity:reserve_logical_effect",
            mode="absent",
            why="without the reservation seam nothing decides a single winner, so the double-dispatch assertion must fail",
        ),
        notes="exactly one of sixteen racing threads wins; the losers see the existing active row",
    ),
    Family(
        number=10,
        name="stale writers",
        nodeids=(
            "tests/test_code_assistant_served_negative_journeys.py::test_served_concurrent_edits_to_the_same_file",
            "tests/test_toolsmith_core_tool_hardening.py::WriteFileStaleBaseTests::test_write_file_rejects_a_stale_expected_hash_without_writing",
            "tests/test_toolsmith_stale_rollback.py::ExternalEditConflictTests::test_external_edit_after_mutation_causes_stale_revert_conflict",
        ),
        served=True,
        served_note="two sessions POST concurrently to /api/chat on a real daemon, both writing the same file from one base",
        served_nodeid=(
            "tests/test_code_assistant_served_negative_journeys.py::test_served_concurrent_edits_to_the_same_file"
        ),
        production_seam="core.runtime_execution_tools._write_file",
        mutation=Mutation(
            target="core.runtime_execution_tools:_write_file",
            mode="neutered",
            why="drop the expected_hash precondition: both writers are admitted, so the second silently overwrites the first",
            build=(
                "def build(orig):\n"
                "    def _mutant(arguments, **k):\n"
                "        args = dict(arguments or {})\n"
                "        args.pop('expected_hash', None)\n"
                "        return orig(args, **k)\n"
                "    return _mutant\n"
            ),
        ),
        notes="exactly one writer journals ok; the other journals stale_base, and the file holds one whole content",
    ),
    Family(
        number=11,
        name="cancellation",
        nodeids=(
            "tests/effect_budget/test_cancel_returns_the_unit.py::test_cancel_before_any_attempt_returns_the_unit",
            "tests/test_cancellation_reaches_the_tool_loop.py::test_a_tool_is_not_dispatched_after_its_turn_was_cancelled",
            "tests/effect_budget/test_cancel_returns_the_unit.py::test_a_cancel_and_reopen_pair_charges_exactly_one_unit",
        ),
        served=False,
        served_note="in-process at the effect gateway and the tool loop",
        production_seam="core.effect_budget.release_effect_reservations",
        mutation=Mutation(
            target="core.effect_budget:release_effect_reservations",
            mode="neutered",
            why="release nothing: the cancelled unit stays spoken for, so the unit is silently lost",
            build=(
                "def build(orig):\n"
                "    def _mutant(*a, **k):\n"
                "        return 0\n"
                "    return _mutant\n"
            ),
        ),
        notes="cancel before any attempt returns the unit; cancel after a real spend refunds nothing",
    ),
    Family(
        number=12,
        name="offline/key loss",
        nodeids=(
            "tests/test_agent_runtime_memory_runtime.py::test_set5_timeout_chain_is_typed_model_specific_and_secret_safe",
            "tests/test_search_api_byok.py::test_an_offline_machine_reports_unreachable_not_a_bad_key",
            "tests/test_search_api_byok.py::test_a_key_deleted_mid_turn_fails_with_a_named_reason",
        ),
        served=False,
        served_note="in-process; the network is denied at the transport, not mocked away at the caller",
        production_seam="core.agent_runtime.memory_runtime.chat_surface_honest_degraded_response",
        mutation=Mutation(
            target="core.agent_runtime.memory_runtime:chat_surface_honest_degraded_response",
            mode="absent",
            why="without the typed degraded reply there is nothing to name the condition, so the honest-refusal assertion must fail",
        ),
        notes="key loss is never laundered into 'the web had nothing', and no secret appears in the refusal",
    ),
    Family(
        number=13,
        name="attachment privacy/erasure",
        nodeids=(
            "tests/test_chat_export_api.py::test_attachment_references_without_bytes",
            "tests/test_chat_export_api.py::test_withheld_payload_is_never_exported",
            "tests/test_chat_attachments_authority.py::test_release_deletes_bytes_keeps_a_receipt_and_sweep_evicts_abandoned_uploads",
        ),
        # NOT served. It calls core.web.api.service.dispatch_get directly -- the real
        # door, the real routing, no daemon and no socket. Counting it as served would
        # inflate the served count from four to six.
        served=False,
        served_note="in-process door: the real export dispatcher, called directly; no daemon, no socket",
        production_seam="core.web.api.chat_export_api._attachment_lines",
        mutation=Mutation(
            target="core.web.api.chat_export_api:_attachment_lines",
            mode="neutered",
            why="append the attachment's own text to its reference line: exactly the leak the export exists to prevent",
            build=(
                "def build(orig):\n"
                "    def _mutant(*a, **k):\n"
                "        lines = list(orig(*a, **k) or [])\n"
                "        return lines + ['attached words']\n"
                "    return _mutant\n"
            ),
        ),
        notes="the export carries the reference and the outcome, never the bytes; a WITHHELD payload never appears",
    ),
    Family(
        number=14,
        name="unknown external-effect reconciliation",
        nodeids=(
            "tests/test_a6_effect_reconciliation.py::R3R4AmbiguousBecomesUnknownAndBlocksRetry::test_mutate_then_raise_records_durable_unknown_and_blocks_fresh_retry",
            "tests/test_a6_resolution_surface.py::test_user_resolution_releases_the_block",
            "tests/effect_budget/test_door_provider_call.py::test_unknown_outcome_keeps_the_unit_and_stays_unknown",
        ),
        # NOT served, for the same reason as family 13: dispatch_post is the real door
        # called in-process, not a booted daemon over a port.
        served=False,
        served_note="in-process door: dispatch_post to /api/runtime/unresolved-effects/resolve; a 'model' source is refused 400. No daemon",
        production_seam="core.runtime_continuity.find_active_unresolved_effect",
        mutation=Mutation(
            target="core.runtime_continuity:find_active_unresolved_effect",
            mode="neutered",
            why="report nothing unresolved: the block lifts itself and the retry double-mutates",
            build=(
                "def build(orig):\n"
                "    def _mutant(*a, **k):\n"
                "        return None\n"
                "    return _mutant\n"
            ),
        ),
        notes="mutated-then-raised is neither success nor failure; it stays unknown and blocks a fresh retry",
    ),
    Family(
        number=15,
        name="final-byte invariance",
        nodeids=(
            "tests/test_response_commit_contract.py::test_speculative_deltas_are_held_and_committed_bytes_are_replayed",
            "tests/test_verifier_exact_output_seal.py::test_frozen_seal_survives_final_ui_validation_and_persistence_commit",
            "tests/test_a7_w2w3_finalization_spine.py::test_hash_covers_exact_canonical_bytes",
        ),
        served=False,
        served_note="in-process at the stream emitter and the finalization spine",
        production_seam="core.finalization.finalize_answer",
        mutation=Mutation(
            target="core.exact_output_seal:validated_exact_output_seal",
            mode="neutered",
            why="drop the seal: a caveat may then be prepended after the bytes were hashed",
            build=(
                "def build(orig):\n"
                "    def _mutant(*a, **k):\n"
                "        return None\n"
                "    return _mutant\n"
            ),
        ),
        notes="the bytes served are the bytes hashed; nothing decorates the answer after finalization",
    ),
)


#: Appended to the target module's SOURCE, inside the mutation worktree. It must be a
#: source edit and not a monkeypatch: the served families boot the daemon as a separate
#: process (tests/_authorship_served_rig.py Popen's `python -m apps.vool_api_server`
#: with an env that OVERWRITES PYTHONPATH), so an in-process rebinding installed by a
#: pytest plugin can never reach the code the daemon actually runs. Measured: under the
#: plugin, all four served families' mutations reddened only their in-process siblings
#: and left the served node green -- zero mutation coverage of the served path, reported
#: as BITES. Editing the module the daemon imports is what closes that.
_SOURCE_MUTATION = """


# --- C24 MUTATION (temporary; restored by tools/c24_family_gauntlet) -------------
def _c24_apply():
{build}
    return build


{attr} = _c24_apply()({attr})
del _c24_apply
# --- end C24 MUTATION -----------------------------------------------------------
"""

_ABSENT_BUILD = """def build(orig):
    def _mutant(*a, **k):
        raise RuntimeError("c24 mutation: {target} was removed")
    return _mutant
"""


def _mutation_source(target: str, build: str) -> str:
    attr = target.partition(":")[2]
    body = build or _ABSENT_BUILD.format(target=target)
    return _SOURCE_MUTATION.format(build=textwrap.indent(body.rstrip("\n"), "    "), attr=attr)


def _module_path(root: str, target: str) -> pathlib.Path:
    """The file whose source carries the seam, inside the given checkout."""
    module_name = target.partition(":")[0]
    return pathlib.Path(root) / (module_name.replace(".", "/") + ".py")


#: Everything a test could reach for that would otherwise be shared between runs. An
#: isolated run gets its own, so a red under the combined runner can be told apart from
#: a red caused by state another node in the same selection left behind.
ISOLATED_ENV_KEYS = (
    "HOME",
    "VOOL_HOME",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
)


def _run_pytest(
    nodeids: list[str],
    timeout: int,
    cwd: str | None = None,
    isolated_home: str | None = None,
) -> dict:
    """Every run here is isolated. `isolated_home` only names WHICH synthetic home.

    This used to fall back to the ambient environment when no home was passed, so the
    baseline, base-attribution and mutated runs -- i.e. all the runs the ledger is about --
    executed against the operator's real HOME and the artifact recorded isolated_home:false.
    A base-attribution run is the worst case: it executes historical code that predates the
    browser credential-isolation fix, which is how automated runs reached the operator's
    installed Chrome and raised Keychain dialogs.
    """
    from tools.isolated_gate import _write_shims, isolation_env, write_chrome_wrapper

    root = cwd or str(REPO)
    owned_home = None
    if not isolated_home:
        owned_home = tempfile.mkdtemp(prefix="c24-run-")
        isolated_home = owned_home
    shim = pathlib.Path(isolated_home) / ".shims"
    audit = pathlib.Path(isolated_home) / "keychain_audit.log"
    _write_shims(shim, audit)
    wrapper, _wrapped = write_chrome_wrapper(shim)
    env = isolation_env(
        root=root, home=isolated_home, shim=str(shim), audit_log=str(audit)
    )
    if wrapper:
        env["VOOL_BROWSER_BINARY"] = wrapper
    args = [sys.executable, "-m", "pytest", *PYTEST_BASE, *nodeids]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            args, cwd=root, env=env, capture_output=True, text=True, timeout=timeout
        )
        out, code = proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        out, code = "TIMEOUT", -9
    tail = [line for line in out.splitlines() if line.strip()][-1:] or [""]
    sites = [line for line in out.splitlines() if line.startswith("C24_MUTATION_SITES=")]
    audit_lines: list[str] = []
    with contextlib.suppress(OSError):
        audit_lines = [ln for ln in audit.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if owned_home:
        shutil.rmtree(owned_home, ignore_errors=True)
    return {
        "exit_code": code,
        "seconds": round(time.monotonic() - started, 1),
        "summary": tail[0][:200],
        "isolated_home": True,
        "synthetic_home_removed": (not pathlib.Path(owned_home).exists()) if owned_home else None,
        "keychain_clean": not audit_lines,
        "keychain_audit_entries": audit_lines,
        "chrome_wrapper_injected": bool(wrapper),
        "cwd": root,
        "mutation_sites": int(sites[-1].split("=")[1]) if sites else None,
        "failed_ids": sorted(
            line.split(" ")[1]
            for line in out.splitlines()
            if line.startswith("FAILED ") and len(line.split(" ")) > 1
        )[:12],
    }


def _digest_of(pairs: list[tuple[pathlib.Path, bytes]]) -> str:
    """One digest over every mutated file, so restoration is proven for all of them."""
    joined = hashlib.sha256()
    for path, data in pairs:
        joined.update(str(path).encode())
        joined.update(hashlib.sha256(data).digest())
    return joined.hexdigest()


def _tree_state(root: str) -> str:
    """A fingerprint of the checkout: HEAD plus anything uncommitted.

    Compared before and after each mutated run so "restored byte-identically" is a
    measurement rather than an assurance -- an in-process monkeypatch should leave
    nothing behind, and this is what proves it did not.
    """
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=60
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=120
        ).stdout.strip()
    except Exception as exc:
        return f"unreadable: {type(exc).__name__}"
    return f"{head}|{hashlib.sha256(dirty.encode()).hexdigest()[:16]}|{len(dirty.splitlines())}"


def _named_bite(fam: Family, result: dict) -> tuple[bool, str]:
    """A mutation counts as biting only when it fails one of the family's OWN tests.

    A non-zero exit is not enough. A mutation that breaks collection, crashes the
    interpreter, or reds some unrelated neighbour has not shown that any assertion in
    this family discriminates the corrupted behaviour -- it has only shown that the
    process died. So the failing ids must name tests from this family's selection.
    """
    if result["summary"] == "TIMEOUT" or result["exit_code"] == -9:
        return False, "the mutated run timed out; a hang is not a discriminating assertion"
    if result["exit_code"] == 0:
        return False, "the mutated run stayed green"
    wanted = {nid.split("::")[-1] for nid in fam.nodeids}
    named = [fid for fid in result["failed_ids"] if fid.split("::")[-1].split("[")[0] in wanted]
    if not named:
        return False, (
            "the mutated run failed, but not on any test in this family: "
            f"{result['failed_ids'][:4]}"
        )
    # A served family is a claim about the SERVED path. Reddening only its in-process
    # siblings leaves that path unmutated and unmeasured, which is exactly what the
    # in-process plugin did before the mutation became a source edit: all four served
    # families scored BITES while their daemon node stayed green.
    if fam.served and fam.served_nodeid:
        served_name = fam.served_nodeid.split("::")[-1]
        if not any(fid.split("::")[-1].split("[")[0] == served_name for fid in named):
            return False, (
                "only the in-process siblings failed; the served node "
                f"{served_name} stayed green, so the served path is not covered: {named[:3]}"
            )
    return True, "; ".join(named[:3])


def _isolation_probe(fam: Family, timeout: int) -> dict:
    """Law: a red under the combined runner is not yet a fact about the product.

    Two questions, in order. Does the same selection pass in a FRESH PROCESS with an
    isolated home -- i.e. was the red caused by state left in a shared home? And if not,
    does every node pass ALONE in its own isolated process -- i.e. is the red an
    interaction between nodes inside the selection rather than a defect any one of them
    reports? Either way the answer is a defect in how the family is run, and the family
    is NOT green.
    """
    out: dict = {}
    with tempfile.TemporaryDirectory(prefix="c24-iso-") as home:
        combined = _run_pytest(list(fam.nodeids), timeout, isolated_home=home)
    out["combined_isolated"] = combined
    if combined["exit_code"] == 0:
        out["isolation"] = "ISOLATION_DEFECT_SHARED_HOME"
        return out
    per_node: dict = {}
    for nodeid in fam.nodeids:
        with tempfile.TemporaryDirectory(prefix="c24-iso-") as home:
            per_node[nodeid] = _run_pytest([nodeid], timeout, isolated_home=home)
    out["per_node_isolated"] = per_node
    if all(r["exit_code"] == 0 for r in per_node.values()):
        out["isolation"] = "ISOLATION_DEFECT_ORDER"
    else:
        out["isolation"] = "REAL_FAILURE"
        out["failing_alone"] = sorted(
            nodeid for nodeid, r in per_node.items() if r["exit_code"] != 0
        )
    return out


def _family_verdict(row: dict, mutate: bool) -> str:
    """PASS is the narrow case. Everything else is named, never rounded up."""
    if row["baseline_verdict"] != "GREEN":
        return "PARTIAL" if str(row.get("isolation", "")).startswith("ISOLATION_DEFECT") else "BLOCKED"
    if mutate and row.get("mutation", {}).get("verdict") != "BITES":
        return "PARTIAL"
    return "PASS"


def run(*, mutate: bool, timeout: int, only: int | None, base: str = "", mutate_in: str = "") -> dict:
    families = [f for f in FAMILIES if only is None or f.number == only]
    results = []
    for fam in families:
        baseline = _run_pytest(list(fam.nodeids), timeout)
        row = {
            "number": fam.number,
            "family": fam.name,
            "nodeids": list(fam.nodeids),
            "served": fam.served,
            "served_note": fam.served_note,
            "production_seam": fam.production_seam,
            "notes": fam.notes,
            "baseline": baseline,
            "baseline_verdict": "GREEN" if baseline["exit_code"] == 0 else "RED",
        }
        # A family that reds here is not yet a finding about this branch. Two of these
        # selections red at the frozen base as well -- pre-existing cross-test
        # interference inside the selection, where each node passes alone. So a RED is
        # re-run at the base worktree and the row says which it is, rather than leaving
        # a reader to assume the change under test caused it.
        if row["baseline_verdict"] == "RED":
            # INHERITED is an attribution, never a pass. Before asking WHOSE red it is,
            # ask whether it is a red about the product at all: a selection that fails
            # only when run as a set, while every node passes alone in a fresh isolated
            # process, is a defect in how this family is run.
            row.update(_isolation_probe(fam, timeout))
            if base:
                at_base = _run_pytest(list(fam.nodeids), timeout, cwd=base)
                row["at_base"] = at_base
                row["attribution"] = (
                    "INHERITED" if at_base["exit_code"] != 0 else "REGRESSION"
                )
                if sorted(at_base["failed_ids"]) == sorted(baseline["failed_ids"]):
                    row["attribution_note"] = "identical failing ids at base and here"
                elif at_base["exit_code"] != 0:
                    row["attribution_note"] = "red at base too, but not on the same ids"
        else:
            row["attribution"] = "CLEAN"
            row["isolation"] = "NOT_PROBED"
        if mutate:
            # The frozen candidate is never mutated. The mutated run happens in a
            # separate checkout at the same SHA, so a mutation that DOES touch the
            # filesystem (the stale-writer one removes a write precondition) cannot
            # reach the tree the rest of this evidence is about.
            mut_root = mutate_in or str(REPO)
            if mut_root == str(REPO):
                raise SystemExit(
                    "refusing to mutate the frozen candidate: pass --mutate-in <worktree at the same sha>"
                )
            before = _tree_state(mut_root)
            seams = fam.mutation.seams()
            originals: list[tuple[pathlib.Path, bytes]] = []
            for target, _build in seams:
                mod_path = _module_path(mut_root, target)
                originals.append((mod_path, mod_path.read_bytes()))
            digest_before = _digest_of(originals)
            try:
                for (mod_path, original), (target, build) in zip(originals, seams, strict=True):
                    mod_path.write_bytes(original + _mutation_source(target, build).encode())
                mutated = _run_pytest(list(fam.nodeids), timeout, cwd=mut_root)
            finally:
                # Every seam restored in the finally, so a kill, a timeout or a raising
                # build cannot leave ANY mutated module behind in the shared checkout.
                for mod_path, original in originals:
                    mod_path.write_bytes(original)
            digest_after = _digest_of([(p_, p_.read_bytes()) for p_, _ in originals])
            after = _tree_state(mut_root)
            bit, why = _named_bite(fam, mutated)
            if mutated["summary"] == "TIMEOUT" or mutated["exit_code"] == -9:
                verdict = "TIMEOUT"
            elif bit:
                verdict = "BITES"
            elif mutated["exit_code"] != 0:
                verdict = "UNPROVEN"
            else:
                verdict = "NO-OP"
            row["mutation"] = {
                **dataclasses.asdict(fam.mutation),
                "result": mutated,
                "verdict": verdict,
                "named_assertion": why,
                "ran_in": mut_root,
                "isolated_from_candidate": mut_root != str(REPO),
                "mutated_files": [str(p_.relative_to(mut_root)) for p_, _ in originals],
                "seam_count": len(seams),
                "sha256_before": digest_before,
                "sha256_after": digest_after,
                "tree_before": before,
                "tree_after": after,
                "restored_byte_identical": digest_before == digest_after and before == after,
            }
        row["family_verdict"] = _family_verdict(row, mutate)
        results.append(row)
        print(
            f"[{fam.number:>2}] {fam.name[:44]:<44} base={row['baseline_verdict']:<5}"
            + f" attr={row.get('attribution', '-'):<10}"
            + (f" mutation={row['mutation']['verdict']:<8}" if mutate else "")
            + f" => {row['family_verdict']}",
            flush=True,
        )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(REPO), capture_output=True, text=True
    ).stdout.strip()
    return {
        "sha": head,
        "tree_clean": not dirty,
        "family_count": len(results),
        "served_count": sum(1 for r in results if r["served"]),
        "green": sum(1 for r in results if r["baseline_verdict"] == "GREEN"),
        "verdicts": {
            v: sorted(r["number"] for r in results if r.get("family_verdict") == v)
            for v in ("PASS", "PARTIAL", "BLOCKED")
        },
        "served_families": sorted(r["number"] for r in results if r["served"]),
        "isolation_defects": sorted(
            r["number"] for r in results if str(r.get("isolation", "")).startswith("ISOLATION_DEFECT")
        ),
        "inherited_red": sorted(
            r["number"] for r in results if r.get("attribution") == "INHERITED"
        ),
        "regressions": sorted(
            r["number"] for r in results if r.get("attribution") == "REGRESSION"
        ),
        "mutations_biting": sum(
            1 for r in results if r.get("mutation", {}).get("verdict") == "BITES"
        )
        if mutate
        else None,
        "mutations_not_proving": sorted(
            r["number"]
            for r in results
            if r.get("mutation", {}).get("verdict") in {"NO-OP", "TIMEOUT"}
        )
        if mutate
        else None,
        "families": results,
    }


def _markdown(report: dict) -> str:
    lines = [
        "# C24 — fifteen semantic families at one SHA",
        "",
        f"SHA `{report['sha']}` · tree clean: {report['tree_clean']}",
        "",
        f"**PASS {len(report['verdicts']['PASS'])} · PARTIAL {len(report['verdicts']['PARTIAL'])} · "
        f"BLOCKED {len(report['verdicts']['BLOCKED'])}** of {report['family_count']}. "
        f"Genuinely served: {report['served_count']} (families {report['served_families']}). "
        f"Mutations biting a named assertion: {report['mutations_biting']}. "
        f"Isolation defects: {report['isolation_defects'] or 'none'}.",
        "",
        "PASS means: the family ran green AND its mutation failed one of the family's own"
        " named tests. Anything else is PARTIAL or BLOCKED; an inherited red is an"
        " attribution, not a pass.",
        "",
        "| # | family | verdict | served | run | attribution | isolation | mutation | mutated seam |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["families"]:
        mut = row.get("mutation") or {}
        lines.append(
            f"| {row['number']} | {row['family']} | **{row['family_verdict']}** | "
            f"{'yes' if row['served'] else 'no'} | {row['baseline_verdict']} | "
            f"{row.get('attribution', '-')} | {row.get('isolation', '-')} | "
            f"{mut.get('verdict', '-')} ({mut.get('mode', '-')}) | "
            f"`{mut.get('target', row['production_seam'])}` |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="print the declared ledger and exit")
    ap.add_argument("--run", action="store_true", help="execute each family's tests")
    ap.add_argument("--mutate", action="store_true", help="also apply each family's mutation")
    ap.add_argument("--only", type=int, default=None, help="run one family by number")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument(
        "--base",
        default="",
        help="worktree at the frozen base; any RED family is re-run there and attributed",
    )
    ap.add_argument(
        "--mutate-in",
        dest="mutate_in",
        default="",
        help="separate checkout at the SAME sha where mutated runs happen; the frozen candidate is never mutated",
    )
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    args = ap.parse_args(argv)

    if args.list or not args.run:
        print(json.dumps([dataclasses.asdict(f) for f in FAMILIES], indent=2))
        return 0

    report = run(
        mutate=args.mutate,
        timeout=args.timeout,
        only=args.only,
        base=args.base,
        mutate_in=args.mutate_in,
    )
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.md:
        pathlib.Path(args.md).write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "families"}, indent=2))
    return 0 if len(report["verdicts"]["PASS"]) == report["family_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
