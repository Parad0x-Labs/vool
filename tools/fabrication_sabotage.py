#!/usr/bin/env python3
"""Sabotage runner for the fabrication/signoff classes C1-C12.

A guard nobody can break is a guard nobody has tested. Each entry below names a
production line, the mutation that removes the protection it provides, and the
tests that MUST go red when it does. A mutation that survives is a finding about
the tests -- it is reported as SURVIVED and never quietly swapped for an easier one.

Discipline (blueprint sec.9, inherited from the C24 runner):

* the mutation NEVER touches the candidate worktree. Every run extracts the frozen
  SHA with `git archive` into a scratch tree under TMPDIR and mutates the copy;
* a bite counts only when a NAMED test fails -- not "some test somewhere";
* the control leg runs first: the named tests must be GREEN before the mutation, or
  the sabotage proves nothing and is reported CONTROL-FAILED;
* the extract's sha256 is recorded before and after so a restore is provable;
* the extract is removed at the end, and the removal is reported.

Usage:
    python tools/fabrication_sabotage.py --list
    python tools/fabrication_sabotage.py --only c2-currency-guard
    python tools/fabrication_sabotage.py --out validation-logs/<lane>/sabotage.json

Exit code is 1 if any mutation SURVIVED or any control failed; 0 only when every
selected mutation bit a named test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = os.environ.get(
    "VOOL_SABOTAGE_PY",
    "~/vool/worktrees/rss-repair-20260829/.venv/bin/python",
)


@dataclass(frozen=True)
class Sabotage:
    """One mutation and the tests it must break."""

    key: str
    clazz: str
    path: str
    #: (exact text to find, replacement). The find text must occur exactly once, or
    #: the mutation is reported AMBIGUOUS rather than applied to the wrong place --
    #: a mutation landing somewhere unintended proves nothing about the seam named.
    old: str
    new: str
    must_red: tuple[str, ...]
    note: str = ""
    extra_green: tuple[str, ...] = field(default_factory=tuple)


SABOTAGES: tuple[Sabotage, ...] = (
    Sabotage(
        key="c2-currency-guard",
        clazz="C2",
        path="core/conductor/operations.py",
        old="        if not price_currency or not quote or quote == price_currency:",
        new="        if True:  # SABOTAGE: accept any amount regardless of its currency",
        must_red=(
            "tests/test_purchasable_amount_solo_shape.py::test_fallback_refuses_a_converted_amount_the_price_is_not_quoted_in",
            "tests/test_purchasable_amount_solo_shape.py::test_fallback_takes_the_fx_legs_base_amount_when_the_conversion_left_the_price_currency",
        ),
        note="restores the pre-fix behaviour: a RUB amount divides a USD price",
    ),
    Sabotage(
        key="c2-single-pass",
        clazz="C2",
        path="core/conductor/operations.py",
        # Collapse the two passes back into one: bind the price lazily inside the same
        # loop that judges amounts. That is the shape the defect actually had, and it
        # restores the order-dependence -- the fx leg iterating first leaves
        # price_currency empty when its RUB amount is judged.
        old="    for dep_id, result in dependencies.items():\n        if not isinstance(result, Mapping):\n            continue\n        if price_value is None and result.get(\"price\") is not None:",
        new="    for dep_id, result in dependencies.items():\n        if not isinstance(result, Mapping):\n            continue\n        if amount_value is None and result.get(\"price\") is None:\n            amount_value = _amount_in_price_currency(result, price_currency)\n        if price_value is None and result.get(\"price\") is not None:",
        must_red=(
            "tests/test_purchasable_amount_solo_shape.py::test_fallback_refuses_a_converted_amount_the_price_is_not_quoted_in",
            "tests/test_purchasable_amount_solo_shape.py::test_fallback_takes_the_fx_legs_base_amount_when_the_conversion_left_the_price_currency",
        ),
        note="reverts the two-pass ordering: the amount is judged before the currency is known",
    ),
    Sabotage(
        key="c2-chain-first-stands",
        clazz="C2",
        path="core/conductor/operations.py",
        old="    if matching:\n        converted_value = matching[-1]",
        new="    if converted_candidates:\n        converted_value = converted_candidates[0][1]",
        must_red=(
            "tests/test_fx_chain_served.py::test_chain_amount_fallback_divides_grounded_operands",
            "tests/test_fx_chain_served.py::test_chain_amount_fallback_refuses_when_no_leg_matches_the_price_currency",
        ),
        note="the old 'first candidate stands' rule",
    ),
    Sabotage(
        key="c2-chain-last-stands",
        clazz="C2",
        path="core/conductor/operations.py",
        old="    if matching:\n        converted_value = matching[-1]",
        new="    if converted_candidates:\n        converted_value = converted_candidates[-1][1]",
        must_red=(
            "tests/test_fx_chain_served.py::test_chain_amount_fallback_picks_the_matching_leg_even_when_it_is_not_last",
            "tests/test_fx_chain_served.py::test_chain_amount_fallback_refuses_when_no_leg_matches_the_price_currency",
        ),
        note="'last candidate stands' -- the arm the committed chain test could not see",
    ),
    Sabotage(
        key="c7-no-anchor-satisfied",
        clazz="C7",
        path="core/agent_runtime/answer_coverage.py",
        old="            verdicts[unit.unit_id] = DEMAND_INDETERMINATE\n            continue",
        new="            verdicts[unit.unit_id] = DEMAND_SATISFIED  # SABOTAGE\n            continue",
        must_red=(
            "tests/test_rss_answer_evidence.py::test_a_unit_that_names_nothing_is_never_satisfied_over_an_empty_answer",
            "tests/test_rss_answer_evidence.py::test_a_unit_that_names_nothing_is_not_an_accusation",
        ),
        note="restores no-anchor auto-satisfaction",
    ),
    Sabotage(
        key="c7-uppercase-codes",
        clazz="C7",
        path="core/agent_runtime/answer_coverage.py",
        old='_CODE_RE = re.compile(r"^[A-Za-z]{2,5}$")',
        new='_CODE_RE = re.compile(r"^[A-Z]{2,5}$")',
        must_red=(
            "tests/test_rss_answer_evidence.py::test_a_lowercase_currency_code_anchors_like_an_uppercase_one",
            "tests/test_rss_answer_evidence.py::test_a_second_ask_is_minted_even_when_its_numbers_match_the_first",
        ),
        note="uppercase-only codes: lowercase ISO asks lose their anchors",
    ),
    Sabotage(
        key="c7-restatement-discharges",
        clazz="C7",
        path="core/agent_runtime/answer_coverage.py",
        old="            verdicts[unit.unit_id] = (\n                DEMAND_SATISFIED\n                if _echo_carries_an_object(unit, anchors, body)\n                else DEMAND_INDETERMINATE\n            )",
        new="            verdicts[unit.unit_id] = DEMAND_SATISFIED  # SABOTAGE: any echo discharges",
        must_red=(
            "tests/test_rss_answer_evidence.py::test_a_prose_refusal_never_discharges_its_own_slot",
        ),
        note="a prose refusal discharges its own slot again",
    ),
    Sabotage(
        key="c7-withheld-zero",
        clazz="C7",
        path="core/finalization.py",
        old="            census[\"demand_render_withheld\"] = len(\n                [\n                    row\n                    for row in rows\n                    if row.get(\"state\") != DEMAND_SATISFIED\n                    and not unit_is_disclosed_in(str(row.get(\"text\") or \"\"), content)\n                ]\n            )",
        new="            census[\"demand_render_withheld\"] = len(pending)  # SABOTAGE: always 0 here",
        must_red=(
            "tests/test_rss_answer_evidence.py::test_a_literal_turn_counts_the_rows_it_withheld",
        ),
        note="literal arm certifies 0 withheld while withholding every row",
    ),
    Sabotage(
        key="c9-drop-the-stamp",
        clazz="C9",
        path="core/agent_runtime/agent.py",
        old="        record_deterministic_render(\n            source_context, rendered, route=\"attempt_followup_explain_failure\"\n        )",
        new="        pass  # SABOTAGE: the render seam records nothing",
        must_red=(
            "tests/test_refused_slot_register_contract.py::test_the_deterministic_ledger_explanation_survives_the_final_guard",
        ),
        note="the explain seam stops recording its render; the guard convicts it again",
    ),
    Sabotage(
        key="c9-shape-keyed-exemption",
        clazz="C9",
        path="core/agent_runtime/response.py",
        old='    return str(recorded.get("render") or "") == str(text or "") != ""',
        new='    return True  # SABOTAGE: any text on a flagged turn is exempt',
        must_red=(
            "tests/test_refused_slot_register_contract.py::test_one_mutated_byte_loses_the_exemption",
            "tests/test_refused_slot_register_contract.py::test_a_model_fabrication_with_the_same_shapes_is_still_convicted",
        ),
        note="drops the byte-exact requirement: the exemption becomes the hole",
    ),
    Sabotage(
        key="c12-shared-gate",
        clazz="C12",
        path="core/agent_runtime/response.py",
        old="    if not live_claim_kinds and not _is_recorded_deterministic_render(\n        final_text, source_context\n    ):\n        from core.model_output_guard import live_value_windows",
        new="    if not live_claim_kinds and raw_contract is None and not turn_ran_observations(\n        source_context\n    ) and not _is_recorded_deterministic_render(final_text, source_context):\n        from core.model_output_guard import live_value_windows",
        must_red=(
            "tests/test_refused_slot_register_contract.py::test_a_raw_output_contract_does_not_buy_a_value_for_a_refused_slot",
            "tests/test_refused_slot_register_contract.py::test_an_unrelated_observation_does_not_disarm_every_refused_slot",
        ),
        note="puts the register back behind the shared whole-turn gate",
    ),
    Sabotage(
        key="c12-per-slot-arming",
        clazz="C12",
        path="core/refused_slot_register.py",
        old="    return tuple(slot for slot in slots if not window_names_slot(witnessed, slot))",
        new="    return ()  # SABOTAGE: any observation disarms every slot",
        must_red=(
            "tests/test_refused_slot_register_contract.py::test_an_unrelated_observation_does_not_disarm_every_refused_slot",
        ),
        note="whole-turn arming: one observation disarms every refused slot",
    ),
    Sabotage(
        key="c12-notice-reshaped",
        clazz="C12",
        path="core/agent_runtime/response.py",
        old="                    if isinstance(source_context, dict):\n                        source_context[\"runtime_notice_not_an_answer\"] = True\n                    raw_contract = None",
        new="                    pass  # SABOTAGE: the notice is re-shaped by the output contract",
        must_red=(
            "tests/test_refused_slot_register_contract.py::test_a_raw_output_contract_does_not_buy_a_value_for_a_refused_slot",
        ),
        note="re-applies the output shape to the notice, restoring the literal fabrication",
    ),
    Sabotage(
        key="c12-carveout-allows-fuzzy",
        clazz="C12",
        path="core/refused_slot_register.py",
        old='        if anchor_match_kind(a, window_tokens, transliterated) in ("exact", "prefix", "stem")',
        new='        if anchor_match_kind(a, window_tokens, transliterated) != ""  # SABOTAGE: fuzzy too',
        must_red=(
            "tests/test_refused_slot_register_contract.py::test_near_miss_a_rome_answer_does_not_bind_to_the_baltic_slot",
        ),
        note="drops the exact/prefix restriction: the fuzzy weather~water near-miss convicts",
    ),
    Sabotage(
        key="c12-carveout-drops-uniqueness",
        clazz="C12",
        path="core/refused_slot_register.py",
        old="    if sum(1 for other in register if anchor in other.anchors) > 1:\n        return ()",
        new="    pass  # SABOTAGE: an anchor shared by two refused slots binds anyway",
        must_red=(
            "tests/test_refused_slot_register_contract.py::test_a_shared_anchor_alone_never_binds",
        ),
        note="drops uniqueness: a shared anchor binds to whichever slot sorted first",
    ),
    Sabotage(
        key="c12-script-fold-identity",
        clazz="C12",
        path="core/refused_slot_register.py",
        old="    if lowered.isascii():\n        return lowered\n    return \"\".join(_SCRIPT_FOLD.get(ch, ch) for ch in lowered)",
        new="    return lowered  # SABOTAGE: script folding becomes identity",
        must_red=(
            "tests/test_refused_slot_register_contract.py::test_a_cyrillic_reply_binds_to_the_slot_it_answers",
        ),
        note="script fold becomes identity: the cross-script fabrication is invisible again",
    ),
    Sabotage(
        key="c10-retry-renders-nothing",
        clazz="C10",
        path="core/attempt_retry.py",
        old="    unanswered_slot_texts = tuple(\n        dict.fromkeys(\n            [\n                text\n                for text in (str(row.get(\"_slot_text\") or \"\") for row in rerun_rows)\n                if text and text not in answered_slot_texts\n            ]\n            + list(unresolved_slot_texts)\n        )\n    )",
        new="    unanswered_slot_texts = tuple(unresolved_slot_texts)  # SABOTAGE: planned-but-empty vanishes",
        must_red=(
            "tests/test_refused_slot_register_contract.py::test_a_real_retry_of_a_fast_path_turn_reports_the_recorded_slot",
        ),
        note="a retry that produces no outcomes renders nothing again",
    ),
    Sabotage(
        key="c11-turn-grain-only",
        clazz="C11",
        path="core/response_provenance.py",
        old='            "tool": capability or "unattributed",\n            "source": lane_id or "unattributed",',
        new='            "tool": "",\n            "source": "",',
        must_red=(
            "tests/test_c11_per_slot_receipts.py::test_one_receipt_per_minted_slot_including_the_one_that_failed",
        ),
        note="empties the per-slot attribution: the rows exist but say nothing",
    ),
    Sabotage(
        key="c11-drop-the-builder",
        clazz="C11",
        path="core/response_provenance.py",
        old="    ledger = list(ledger)",
        new="    ledger = []  # SABOTAGE: the ledger is read and then discarded",
        must_red=(
            "tests/test_c11_per_slot_receipts.py::test_one_receipt_per_minted_slot_including_the_one_that_failed",
            "tests/test_c11_per_slot_receipts.py::test_the_gauntlet_counts_these_receipts_as_per_slot_attribution",
        ),
        note="the ledger reader stops reading: C11 returns to zero per-slot attributions",
    ),
    Sabotage(
        key="c5-receipts-outrank-gated-bytes",
        clazz="C5",
        path="core/conductor/obligation_ledger.py",
        old="        if unit_id in lane_attested and receipts_outrank_bytes:",
        new="        if unit_id in lane_attested:  # SABOTAGE: receipts outrank gated bytes again",
        must_red=(
            "tests/test_withheld_content_unsatisfies_its_slot.py::test_a_withheld_statement_unsatisfies_the_slot_it_was_about",
        ),
        note="a receipt filed before the gate ran certifies a slot whose bytes were withheld",
    ),
    Sabotage(
        key="c5-drop-the-withheld-signal",
        clazz="C5",
        path="core/finalization.py",
        old="            *bound, states=states, receipts_outrank_bytes=not withheld_claims",
        new="            *bound, states=states",
        must_red=(
            "tests/test_withheld_content_unsatisfies_its_slot.py::test_a_withheld_statement_unsatisfies_the_slot_it_was_about",
        ),
        note="the gate's own withholding record never reaches the certificate",
    ),
    Sabotage(
        key="c7-withheld-slot-not-disclosed",
        clazz="C7",
        path="core/finalization.py",
        old="        if withheld_claims:",
        new="        if False:  # SABOTAGE: the reader is never told which slot was withheld",
        must_red=(
            "tests/test_withheld_content_unsatisfies_its_slot.py::test_the_reader_is_told_which_slot_went_missing",
        ),
        note="the certificate is corrected but the answer still drops the slot silently",
    ),
    Sabotage(
        key="c2-registry-never-claims",
        clazz="C2",
        path="core/agent_runtime/demand_ownership.py",
        # Make the registry probe answer "nobody claims this" for every fragment. The
        # guard then finds no minted sibling worth refusing and falls through to its
        # execution-grain body, which FUSES the sibling -- so the turn is claimed and
        # the weather ask is swallowed, which is the pre-fix behaviour exactly.
        # This doubles as the fail-open proof: an empty or raising catalog degrades the
        # guard to what it did before, it does not refuse everything.
        old="def _probe_claims(probe: object, unit_text: str) -> bool:\n    try:\n        return bool(probe(unit_text))  # type: ignore[operator]",
        new="def _probe_claims(probe: object, unit_text: str) -> bool:\n    if True:  # SABOTAGE: the capability registry claims nothing\n        return False\n    try:\n        return bool(probe(unit_text))  # type: ignore[operator]",
        must_red=(
            "tests/test_purchasable_amount_solo_shape.py::test_a_minted_unit_a_registered_lane_claims_is_not_scoped_into_the_purchase",
        ),
        extra_green=(
            "tests/test_purchasable_amount_solo_shape.py::test_a_continuation_no_lane_claims_still_rides_the_purchase",
            "tests/test_purchasable_amount_solo_shape.py::test_a_second_asset_ask_inside_one_purchase_turn_is_still_claimed",
        ),
        note="fail-open proof: with no lane claiming anything the guard degrades to its pre-fix behaviour",
    ),
    Sabotage(
        key="c2-subcheck-reads-execution-grain",
        clazz="C2",
        path="core/conductor/planner.py",
        # The mistake the next person is most likely to make while "tidying" the
        # function to use one grain. The execution grain FUSES the sibling ask, so the
        # sub-check iterates a single unit that belongs to the purchase, finds nothing
        # to refuse, and the guard claims the turn again. Names WHY the mint grain is
        # load-bearing here rather than merely asserting it in a comment.
        old="    for minted in demand_units(text):",
        new="    for minted in execution_unit_spans(text):  # SABOTAGE: the fused grain, not the mint",
        must_red=(
            "tests/test_purchasable_amount_solo_shape.py::test_a_minted_unit_a_registered_lane_claims_is_not_scoped_into_the_purchase",
        ),
        extra_green=(
            "tests/test_purchasable_amount_solo_shape.py::test_a_continuation_no_lane_claims_still_rides_the_purchase",
            "tests/test_purchasable_amount_solo_shape.py::test_a_second_asset_ask_inside_one_purchase_turn_is_still_claimed",
        ),
        note="reading the fused grain restores the swallow; the mint grain is the whole point",
    ),
    Sabotage(
        key="c2-fiat-leg-not-a-payment",
        clazz="C2",
        path="core/conductor/planner.py",
        # Restore the pre-fix gate: a payment leg is valid only if the PRICE-ASSET alias
        # table resolves it, so a fiat currency resolves to nothing and the whole shape
        # declines -- the lane then answers an AMOUNT question with a gold PRICE.
        old="            and resolve_currency_literal(payment_text) is not None",
        new="            and False  # SABOTAGE: a currency is not a payment leg",
        must_red=(
            "tests/test_purchasable_amount_solo_shape.py::test_a_fiat_payment_leg_is_planned_like_any_other",
        ),
        extra_green=(
            "tests/test_purchasable_amount_solo_shape.py::test_an_unknown_payment_token_still_declines",
            "tests/test_purchasable_amount_solo_shape.py::test_the_canonical_four_slot_prompt_is_left_to_the_model_planner",
        ),
        note="the fiat payment leg declines again, and an amount question is answered with a price",
    ),
    Sabotage(
        key="c2-currency-quoted-as-an-asset",
        clazz="C2",
        path="core/conductor/planner.py",
        # The SECOND defect the fiat leg could have introduced, and the reason the payment
        # is kept out of the quote list: ask the market for the price of "eur".
        old="        if payment_text not in alias_folds and not payment_is_currency:\n            aliases = [*aliases, payment_text]",
        new="        if payment_text not in alias_folds:  # SABOTAGE: quote the currency as an asset\n            aliases = [*aliases, payment_text]",
        must_red=(
            "tests/test_purchasable_amount_solo_shape.py::test_a_fiat_payment_leg_is_planned_like_any_other",
        ),
        extra_green=(
            "tests/test_purchasable_amount_solo_shape.py::test_an_unknown_payment_token_still_declines",
        ),
        note="a currency enters the quote list, so the plan asks the market for the price of a currency",
    ),
)

def _sha256_tree(root: Path, rel: str) -> str:
    return hashlib.sha256((root / rel).read_bytes()).hexdigest()


def _extract(sha: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", sha], cwd=REPO, capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)


def _pytest(tree: Path, node_ids: tuple[str, ...], home: Path) -> tuple[int, str]:
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(tree),
        "VOOL_HOME": str(home),
        "TMPDIR": str(home / "tmp"),
        "HOME": str(home / "home"),
    }
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    (home / "home").mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [PY, "-m", "pytest", *node_ids, "-q", "-p", "no:cacheprovider", "-p", "no:randomly"],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_one(sab: Sabotage, sha: str, scratch: Path) -> dict:
    tree = scratch / f"extract-{sab.key}"
    home = scratch / f"home-{sab.key}"
    home.mkdir(parents=True, exist_ok=True)
    _extract(sha, tree)
    target = tree / sab.path
    before = target.read_text()
    before_sha = hashlib.sha256(before.encode()).hexdigest()

    occurrences = before.count(sab.old)
    if occurrences != 1:
        shutil.rmtree(tree, ignore_errors=True)
        return {
            "key": sab.key, "class": sab.clazz, "verdict": "AMBIGUOUS",
            "detail": f"anchor text occurs {occurrences} times in {sab.path}; refusing to mutate",
        }

    control_rc, control_out = _pytest(tree, sab.must_red + sab.extra_green, home)
    if control_rc != 0:
        shutil.rmtree(tree, ignore_errors=True)
        return {
            "key": sab.key, "class": sab.clazz, "verdict": "CONTROL-FAILED",
            "detail": "the named tests were not green before the mutation",
            "control_tail": control_out.strip().splitlines()[-12:],
        }

    target.write_text(before.replace(sab.old, sab.new))
    mutated_sha = hashlib.sha256(target.read_text().encode()).hexdigest()
    rc, out = _pytest(tree, sab.must_red, home)

    failed = [
        node for node in sab.must_red
        if node.rsplit("::", 1)[-1] in out and "FAILED" in out
    ]
    # The controls must SURVIVE the mutation. A mutation that also reds them is too
    # broad to prove anything about the seam named: it shows the file matters, not that
    # THIS guard does. Reported as COLLATERAL, never counted as a bite.
    collateral: list[str] = []
    if sab.extra_green:
        green_rc, green_out = _pytest(tree, sab.extra_green, home)
        if green_rc != 0:
            collateral = [
                node for node in sab.extra_green
                if node.rsplit("::", 1)[-1] in green_out and "FAILED" in green_out
            ] or list(sab.extra_green)

    target.write_text(before)
    restored_sha = hashlib.sha256(target.read_text().encode()).hexdigest()

    verdict = "BIT" if rc != 0 else "SURVIVED"
    if verdict == "BIT" and collateral:
        verdict = "COLLATERAL"
    result = {
        "key": sab.key,
        "class": sab.clazz,
        "path": sab.path,
        "note": sab.note,
        "verdict": verdict,
        "must_red": list(sab.must_red),
        "named_failures": failed,
        "control_green": True,
        "controls_that_must_stay_green": list(sab.extra_green),
        "controls_broken_by_the_mutation": collateral,
        "sha256_before": before_sha,
        "sha256_mutated": mutated_sha,
        "sha256_restored": restored_sha,
        "restored_byte_identical": before_sha == restored_sha,
        "tail": out.strip().splitlines()[-8:],
    }
    shutil.rmtree(tree, ignore_errors=True)
    result["extract_removed"] = not tree.exists()
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[], help="sabotage key(s) to run")
    ap.add_argument("--clazz", action="append", default=[], help="class filter, e.g. C2")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.list:
        for sab in SABOTAGES:
            print(f"{sab.key:32s} {sab.clazz:4s} {sab.path}  -- {sab.note}")
        return 0

    selected = [
        s for s in SABOTAGES
        if (not args.only or s.key in args.only) and (not args.clazz or s.clazz in args.clazz)
    ]
    if not selected:
        print("no sabotage selected", file=sys.stderr)
        return 2

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()

    scratch = Path(tempfile.mkdtemp(prefix="fab-sabotage-"))
    results = []
    try:
        for sab in selected:
            started = time.time()
            res = run_one(sab, sha, scratch)
            res["seconds"] = round(time.time() - started, 1)
            results.append(res)
            print(f"[{res['verdict']:14s}] {sab.key}  ({res['seconds']}s)")
            for node in res.get("named_failures", []):
                print(f"                 red: {node}")
            for node in res.get("controls_broken_by_the_mutation", []):
                print(f"                 COLLATERAL, control also red: {node}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    blob = {
        "sha": sha,
        "tree_dirty": bool(porcelain),
        "porcelain": porcelain.splitlines(),
        "interpreter": PY,
        "scratch_removed": not scratch.exists(),
        "results": results,
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(blob, indent=2) + "\n")
        print(f"wrote {args.out}")

    bad = [r for r in results if r["verdict"] != "BIT"]
    print(f"\n{len(results) - len(bad)}/{len(results)} mutations bit a named test")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
