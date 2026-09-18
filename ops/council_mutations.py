"""Council mutation matrix — sabotage discipline for the adjudication laws.

For each mutation: assert the suite is GREEN, apply the mutant, assert the NAMED test
goes RED (a mutant nothing bites means the law is untested), restore byte-identically
(sha256 compared), assert GREEN again. Exit non-zero on any violation.

Run: .venv/bin/python ops/council_mutations.py
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _interpreter() -> str:
    """The python that runs the suite. A worktree has no `.venv` of its own, so the
    interpreter is named by VOOL_MUTATION_PYTHON when it is not the repo's own — a
    matrix that silently ran on a different interpreter than the gate proves nothing."""
    named = os.environ.get("VOOL_MUTATION_PYTHON", "").strip()
    if named:
        return named
    local = ROOT / ".venv/bin/python"
    return str(local) if local.exists() else sys.executable


SUITE = [
    "tests/test_council_orchestrator.py",
    "tests/test_council_dispatch.py",
    "tests/test_council_api.py",
    "tests/test_council_chat_record.py",
    "tests/test_council_chat_card.py",
    "tests/test_council_pin_fence.py",
    "tests/test_council_needs_attention.py",
    "tests/test_council_scorecard.py",
    "tests/test_council_scorecard_truth.py",
]

MUTATIONS = [
    {
        "id": "M-C1 counterexample trump removed",
        "file": "core/council/orchestrator.py",
        "old": "            backed = [r for r in effective if r.counterexample_backed]",
        "new": "            backed = []  # MUTANT: receipts no longer trump votes",
        "bitten_by": "tests/test_council_orchestrator.py::test_receipt_backed_counterexample_trumps_unanimous_agreement",
    },
    {
        "id": "M-C2 advisor verdicts counted in the tally",
        "file": "core/council/orchestrator.py",
        "old": "            voters = [r for r in effective if r.seat_id in voting_ids]",
        "new": "            voters = list(effective)  # MUTANT: advisors vote",
        "bitten_by": "tests/test_council_orchestrator.py::test_advisor_reports_feed_judges_but_never_vote",
    },
    {
        "id": "M-C3 barrier snapshot leaks the round in flight",
        "file": "core/council/orchestrator.py",
        "old": (
            "            previous: list[SeatReport] = (\n"
            "                self.effective_reports(self.rounds[self.round_no - 2])\n"
            "                if self.round_no >= 2 else []\n"
            "            )"
        ),
        "new": (
            "            previous: list[SeatReport] = self.effective_reports(\n"
            "                self.rounds[self.round_no - 1])  # MUTANT: the round in flight leaks\n"
            "            #"
        ),
        "bitten_by": "tests/test_council_orchestrator.py::test_round_one_is_blind_and_later_rounds_are_not",
    },
    {
        "id": "M-C4 unanimity relaxed to majority for small benches",
        "file": "core/council/orchestrator.py",
        "old": "            converged = (disagree == 0) if len(voters) <= 4 else (agree > len(voters) / 2)",
        "new": "            converged = agree > len(voters) / 2  # MUTANT: majority everywhere",
        "bitten_by": "tests/test_council_orchestrator.py::test_tally_law_unanimity_small_majority_large",
    },
    {
        "id": "M-C5 seats dispatched with write permissions",
        "file": "core/council/dispatch.py",
        "old": '            "mode": "plan",',
        "new": '            "mode": "auto",  # MUTANT: seats may write',
        "bitten_by": "tests/test_council_dispatch.py::test_seat_turn_runs_in_plan_mode_with_own_session",
    },
    {
        "id": "M-C6 council self-authorizes unaccepted paid spend",
        "file": "core/council/dispatch.py",
        "old": '    if status == 409 and code == "paid_model_confirm_required" and _acceptance_recorded(model):',
        "new": '    if status == 409 and code == "paid_model_confirm_required":  # MUTANT: no acceptance wall',
        "bitten_by": "tests/test_council_dispatch.py::test_unaccepted_paid_model_fails_typed_and_never_confirms",
    },
    # ---- C3: the run lives in the chat it was convened from -------------------------
    {
        "id": "M-C3a the card never reaches the chat",
        "file": "core/council_chat_card_fragment.py",
        "old": "  if (!rec.chatId || rec.chatId !== displayedChat()) return;",
        "new": "  if (true) return;  // MUTANT: no card is ever inserted into the chat log",
        "bitten_by": "tests/test_council_chat_card.py::test_convene_puts_exactly_one_council_card_into_the_active_chat_log",
    },
    {
        "id": "M-C3b the modal stays the run's home",
        "file": "core/council_chat_card_fragment.py",
        "old": "  var host = a && a.chatLog ? a.chatLog() : null;",
        "new": "  var host = document.getElementById('councilBody');  // MUTANT: mounted in the overlay",
        "bitten_by": "tests/test_council_chat_card.py::test_closing_the_convene_modal_neither_hides_nor_stops_the_run",
    },
    {
        "id": "M-C3c the requested model is sold as the actual one",
        "file": "core/council_chat_card_fragment.py",
        "old": "  var actual = String((report && report.model_actual) || '').trim();",
        "new": "  var actual = String((report && (report.model_actual || report.model_requested)) || '').trim();  // MUTANT",
        "bitten_by": "tests/test_council_chat_card.py::test_every_seat_separates_the_model_asked_for_from_the_model_that_answered",
    },
    {
        "id": "M-C3d the ledger reference is never followed",
        "file": "core/council_chat_card_fragment.py",
        "old": "  var seq = ref && ref.seq;",
        "new": "  var seq = null;  // MUTANT: text_ref is never resolved",
        "bitten_by": "tests/test_council_chat_card.py::test_a_seat_report_body_is_fetched_through_text_ref_and_shown",
    },
    {
        "id": "M-C3e a pruned run blanks instead of keeping its summary",
        "file": "core/council_chat_card_fragment.py",
        "old": "  var showSummary = !!rec.summaryText && !run;",
        "new": "  var showSummary = false;  // MUTANT: missing evidence empties the message",
        "bitten_by": "tests/test_council_chat_card.py::test_pruned_evidence_keeps_the_readable_summary_and_says_details_are_gone",
    },
    {
        "id": "M-C3f a reloaded transcript no longer rebuilds the card",
        "file": "core/council_chat_card_fragment.py",
        "old": "    var marker = parseMarker(row.text);",
        "new": "    var marker = null;  // MUTANT: the marker in history is ignored",
        "bitten_by": "tests/test_council_chat_card.py::test_a_persisted_marker_in_chat_history_is_rebuilt_into_the_folded_card",
    },
    {
        "id": "M-C3g the composer stays usable while a council owns the pin",
        "file": "core/vool_chat_page.py",
        "old": "function councilOwnsComposer() { return councilLockActive; }",
        "new": "function councilOwnsComposer() { return false; }  // MUTANT: the lock never applies",
        "bitten_by": "tests/test_council_chat_card.py::test_a_send_during_a_live_council_never_starts_a_turn",
    },
    {
        "id": "M-C3h the terminal verdict never reaches the transcript",
        "file": "core/council/api.py",
        "old": "                persist_council_summary(orchestrator.run_id)",
        "new": "                pass  # MUTANT: no summary is ever persisted",
        "bitten_by": "tests/test_council_chat_record.py::test_a_convened_run_leaves_its_verdict_in_the_chat_transcript",
    },
    {
        "id": "M-C3i council artifacts leak into recall and extraction",
        "file": "core/memory/entries.py",
        "old": '        if not include_artifacts and str(row.get("artifact_kind") or "").strip():',
        "new": "        if False:  # MUTANT: artifact rows are fed back to the model like turns",
        "bitten_by": "tests/test_council_chat_record.py::test_artifact_rows_are_invisible_to_recall_and_extraction_by_default",
    },
    # ---- C3b: the model pin is fenced by the SERVER --------------------------------
    {
        "id": "M-C3b-a the server guard never refuses",
        "file": "core/web/api/service.py",
        "old": "        if _council_admission.refusal is not None:",
        "new": "        if False:  # MUTANT: the fence is decorative",
        "bitten_by": "tests/test_council_pin_fence.py::test_the_originating_chat_cannot_send_an_ordinary_turn_during_a_council",
    },
    {
        "id": "M-C3b-b a council-shaped name is treated as authority",
        "file": "core/council/pin_lock.py",
        "old": (
            "    with _CONDITION:\n"
            "        return _owner is not None and hmac.compare_digest(presented, _owner.capability)"
        ),
        "new": (
            "    with _CONDITION:  # MUTANT: a council-shaped NAME is treated as authority\n"
            "        return _owner is not None and presented.startswith(\"council\")"
        ),
        "bitten_by": "tests/test_council_pin_fence.py::test_knowing_the_run_id_or_forging_a_seat_session_id_buys_nothing",
    },
    {
        "id": "M-C3b-c the lock surface serves the capability",
        "file": "core/council/pin_lock.py",
        "old": '        "run_id": current["run_id"],\n        "state": current["state"],',
        "new": (
            '        "run_id": current["run_id"],\n'
            '        "capability": dispatch_capability_for_tests(),  # MUTANT: the key is served\n'
            '        "state": current["state"],'
        ),
        "bitten_by": "tests/test_council_pin_fence.py::test_no_council_read_surface_ever_carries_the_capability",
    },
    {
        "id": "M-C3b-d a crashed run keeps the pin",
        "file": "core/council/api.py",
        "old": "            pin_lock.release(orchestrator.run_id)\n            # The verdict belongs",
        "new": "            pass  # MUTANT: the pin is never handed back\n            # The verdict belongs",
        "bitten_by": "tests/test_council_pin_fence.py::test_a_crashed_council_thread_still_hands_the_pin_back",
    },
    {
        "id": "M-C3b-e convene pins without draining admitted turns",
        "file": "core/council/pin_lock.py",
        "old": "            while _live_admissions_locked() > 0:",
        "new": "            while False:  # MUTANT: pin over a turn already in flight",
        "bitten_by": "tests/test_council_pin_fence.py::test_convene_refuses_rather_than_pinning_over_a_turn_that_will_not_drain",
    },
    {
        "id": "M-C3b-f only the chat holding the card is locked",
        "file": "core/council_chat_card_fragment.py",
        "old": "  a.setCouncilLock(payload && payload.locked === true, String((payload || {}).reason || ''));",
        "new": "  a.setCouncilLock(!!(payload && payload.locked === true && runs[String((payload || {}).run_id || '')]), String((payload || {}).reason || ''));  // MUTANT",
        "bitten_by": "tests/test_council_chat_card.py::test_a_council_convened_in_another_tab_locks_this_one_with_no_card_involved",
    },
    # ---- C3c: no clock evicts a live turn, and the pin has one writer boundary --------
    {
        "id": "M-C3c-a a live turn is aged out of the count",
        "file": "core/council/pin_lock.py",
        "old": (
            '    """How many ordinary turns are still running. Counted, never aged out."""\n'
            "    return len(_admissions)"
        ),
        "new": (
            "    now = time.monotonic()  # MUTANT: a long turn is forgiven by the clock\n"
            "    for token in [t for t, started in _admissions.items() if now - started > 2700.0]:\n"
            "        _admissions.pop(token, None)\n"
            "    return len(_admissions)"
        ),
        "bitten_by": "tests/test_council_pin_fence.py::test_a_long_running_turn_is_never_evicted_by_the_clock",
    },
    {
        "id": "M-C3c-b the model endpoint classifies before it refuses",
        "file": "core/web/api/service.py",
        "old": "        _pin_refusal = _pin_lock.pin_write_refusal(_council_capability, owner_local=True)",
        "new": "        _pin_refusal = None  # MUTANT: no early refusal; pricing work runs first",
        "bitten_by": "tests/test_council_pin_fence.py::test_the_refusal_lands_before_any_classification_or_pricing_work",
    },
    {
        "id": "M-C3c-c a model write joins no drain",
        "file": "core/web/api/service.py",
        "old": (
            "        _pin_admission = _pin_lock.admit_model_write(\n"
            "            capability=_council_capability, owner_local=True\n"
            "        )"
        ),
        "new": "        _pin_admission = _pin_lock.Admission()  # MUTANT: the write races the pin",
        "bitten_by": "tests/test_council_pin_fence.py::test_convene_waits_for_a_model_write_already_inside_the_mutation",
    },
    {
        "id": "M-C3c-d the mutation authority itself is unfenced",
        "file": "core/cloud_model_control.py",
        "old": '    if _council_refusal:\n        return False, _council_refusal, ""',
        "new": "    if False:  # MUTANT: direct writers walk past the council fence\n        pass",
        "bitten_by": "tests/test_council_pin_fence.py::test_the_mutation_authority_itself_refuses_a_council_pin_it_was_not_given_the_key_for",
    },
    {
        "id": "M-C3c-e the final restoration carries no capability",
        "file": "core/council/api.py",
        "old": "            restore_pin(base_url, original_pin, dispatch_capability)",
        "new": "            restore_pin(base_url, original_pin)  # MUTANT: the council's last write has no key",
        "bitten_by": "tests/test_council_pin_fence.py::test_a_terminal_run_restores_the_operator_pin_carrying_its_own_capability",
    },
    {
        "id": "M-C3c-f the second writer can reset the pin under a council",
        "file": "core/web/api/service.py",
        "old": '        _cred_refusal = _cred_pin_lock.pin_write_refusal("", owner_local=True)',
        "new": "        _cred_refusal = None  # MUTANT: a key save rewrites the pin mid-council",
        "bitten_by": "tests/test_council_pin_fence.py::test_the_second_writer_saving_a_cloud_key_cannot_reset_the_pin_under_a_council",
    },
    # ---- C4: a dead seat is the operator's decision --------------------------------
    {
        "id": "M-C4-a a blocking seat is adjudicated around",
        "file": "core/council/orchestrator.py",
        "old": "            blocking = self.blocking_failures()",
        "new": "            blocking = []  # MUTANT: a missing vote is voted away after all",
        "bitten_by": "tests/test_council_needs_attention.py::test_an_exhausted_voting_seat_pauses_the_run_instead_of_ending_it",
    },
    {
        "id": "M-C4-b a retry deletes the attempt it replaces",
        "file": "core/council/orchestrator.py",
        "old": (
            "            if report.seat_id == str(seat_id) and not report.superseded:\n"
            "                report.superseded = True\n"
            "                return True"
        ),
        "new": (
            "            if report.seat_id == str(seat_id) and not report.superseded:\n"
            "                self.current_reports().remove(report)  # MUTANT: history erased\n"
            "                return True"
        ),
        "bitten_by": "tests/test_council_needs_attention.py::test_retry_redispatches_the_seat_and_never_overwrites_its_dead_attempt",
    },
    {
        "id": "M-C4-c a disable that destroys quorum is allowed",
        "file": "core/council/orchestrator.py",
        "old": "        refusal = self.quorum_refusal(without=seat.seat_id)",
        "new": '        refusal = ""  # MUTANT: nothing checks what would be left',
        "bitten_by": "tests/test_council_needs_attention.py::test_a_disable_that_would_leave_no_quorum_is_refused",
    },
    {
        "id": "M-C4-d a resume abandons the round it paused in",
        "file": "core/council/orchestrator.py",
        "old": "                rows = self._blocking_rows(blocking)",
        "new": (
            "                self.round_open = False  # MUTANT: resume opens a fresh round\n"
            "                rows = self._blocking_rows(blocking)"
        ),
        "bitten_by": "tests/test_council_needs_attention.py::test_retry_redispatches_the_seat_and_never_overwrites_its_dead_attempt",
    },
    {
        "id": "M-C4-e a replacement model skips the cloud-only wall",
        "file": "core/council/api.py",
        "old": "            violation = _seat_model_violation(model)",
        "new": "            violation = None  # MUTANT: any model may be smuggled in here",
        "bitten_by": "tests/test_council_needs_attention.py::test_a_replacement_model_is_refused_when_it_is_not_a_cloud_seat",
    },
    {
        "id": "M-C4-f a paused run is stranded by the next convene",
        "file": "core/council/api.py",
        "old": "    if paused_ids:",
        "new": "    if False:  # MUTANT: a second convene strands the paused run",
        "bitten_by": "tests/test_council_needs_attention.py::test_a_second_council_cannot_convene_while_one_is_paused",
    },
    # ---- C5: the scorecard is derived, or it is a popularity score ------------------
    {
        "id": "M-C5-a contributions scored by how much prose a seat produced",
        "file": "core/council/scorecard.py",
        "old": '            "claims_adopted": len(adopted_claims),',
        "new": (
            '            "claims_adopted": max(  # MUTANT: prose volume becomes the score\n'
            '                len(adopted_claims),\n'
            '                sum(len(str(r.get("text") or "")) for r in my_reports) // 100),'
        ),
        "bitten_by": "tests/test_council_scorecard.py::test_prose_length_and_confidence_never_enter_the_score",
    },
    {
        "id": "M-C5-b a tie is collapsed to one seat",
        "file": "core/council/scorecard.py",
        "old": '    winners = sorted(r["seat_id"] for r in rows if r["claims_adopted"] == top and top > 0)',
        "new": (
            '    winners = sorted(r["seat_id"] for r in rows\n'
            '                     if r["claims_adopted"] == top and top > 0)[:1]  # MUTANT'
        ),
        "bitten_by": "tests/test_council_scorecard.py::test_a_real_tie_stays_a_tie",
    },
    {
        "id": "M-C5-c a failed seat is ranked as a poor contributor",
        "file": "core/council/scorecard.py",
        "old": '                          if r["claims_refuted"] > 0 and r["claims_adopted"] == 0)',
        "new": (
            '                          if (r["claims_refuted"] > 0 or r["failures"] > 0)\n'
            '                          and r["claims_adopted"] == 0)  # MUTANT'
        ),
        "bitten_by": "tests/test_council_scorecard.py::test_a_seat_that_only_ever_failed_is_never_called_the_least_contributor",
    },
    {
        "id": "M-C5-d attribution follows the replacement, not the model that proposed",
        "file": "core/council/scorecard.py",
        "old": '                 "model": _model_at(events, seat_id, c["round_no"]),',
        "new": '                 "model": seat["model"],  # MUTANT: replacement takes the credit',
        "bitten_by": "tests/test_council_scorecard.py::test_a_replaced_model_keeps_the_attribution_of_what_the_old_model_did",
    },
    {
        "id": "M-C5-e unresolved disagreements are dropped",
        "file": "core/council/scorecard.py",
        "old": '        if str(report.get("verdict") or "") != "DISAGREE":',
        "new": '        if True:  # MUTANT: no disagreement survives to the summary',
        "bitten_by": "tests/test_council_scorecard.py::test_remaining_disagreements_name_their_parties_and_carry_their_text",
    },
    {
        "id": "M-C5-f the scorecard is read from the transient run state",
        "file": "core/council/scorecard.py",
        "old": "    return scorecard_from_events(CouncilRunStore(run_id).read_events_ordered())",
        "new": (
            "    store = CouncilRunStore(run_id)  # MUTANT: a transient view becomes the truth\n"
            "    if store.read_state() is None:\n"
            "        return {\"run\": {}, \"seats\": []}\n"
            "    return scorecard_from_events(store.read_events_ordered())"
        ),
        "bitten_by": "tests/test_council_scorecard.py::test_the_scorecard_is_rebuilt_from_the_ledger_with_no_run_state_at_all",
    },
    {
        "id": "M-C5-g verdicts bind by text again",
        "file": "core/council/scorecard.py",
        "old": '''        adopted_claims = [c for c in mine if c["claim_id"] in adopted_ids]''',
        "new": '''        adopted_claims = [c for c in mine if c["text"] in adopted]  # MUTANT: text binds again''',
        "bitten_by": "tests/test_council_scorecard_truth.py::test_identical_text_from_two_seats_cannot_double_credit",
    },
    {
        "id": "M-C5-h winner attribution credits the seat's current model",
        "file": "core/council/scorecard.py",
        "old": '''            "produced_by": prov["model_actual"] or prov["model_requested"],''',
        "new": '''            "produced_by": next((s["model"] for s in seats if s["seat_id"] == claim["seat_id"]), ""),  # MUTANT: the replacement takes the credit''',
        "bitten_by": "tests/test_council_scorecard_truth.py::test_the_visible_winner_names_the_model_that_produced_the_adopted_claim",
    },
]


#: One pytest invocation may never hang the matrix. Generous (the whole council suite
#: runs in well under a minute), but BOUNDED: a mutant that wedges a test into an
#: infinite wait is a finding, not a session to babysit.
PYTEST_TIMEOUT_SECONDS = 900


def run_pytest(targets: list[str]) -> tuple[bool, bool]:
    """Run pytest bounded. Returns (completed_ok, timed_out) — a timeout is a reported
    outcome, never an exception that skips the byte-exact restore.

    The child starts its own process group (POSIX) so a timeout can kill the WHOLE
    tree it spawned — a mutant whose test leaves a subprocess behind must not outlive
    its killer and keep mutating files under the matrix.
    """
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    group_kwargs: dict = {"start_new_session": True} if os.name == "posix" else {}
    try:
        completed = subprocess.run(
            [_interpreter(), "-m", "pytest", "-q", *targets],
            cwd=ROOT, capture_output=True, text=True, env=env,
            timeout=PYTEST_TIMEOUT_SECONDS, **group_kwargs,
        )
    except subprocess.TimeoutExpired:
        return False, True
    return completed.returncode == 0, False



def main() -> int:
    failures: list[str] = []
    pre_ok, pre_timed_out = run_pytest(SUITE)
    if pre_timed_out:
        print(f"PRE-GATE TIMEOUT after {PYTEST_TIMEOUT_SECONDS}s — aborting, nothing mutated")
        return 2
    if not pre_ok:
        print("PRE-GATE RED: the council suite is not green before mutating — aborting")
        return 2
    print("pre-gate GREEN")
    for mutation in MUTATIONS:
        target = ROOT / mutation["file"]
        original = target.read_bytes()
        original_sha = hashlib.sha256(original).hexdigest()
        source = original.decode("utf-8")
        if mutation["old"] not in source:
            failures.append(f"{mutation['id']}: anchor not found — matrix is stale")
            print(f"STALE  {mutation['id']}")
            continue
        target.write_text(source.replace(mutation["old"], mutation["new"], 1), encoding="utf-8")
        timed_out = False
        try:
            bitten, bite_timeout = run_pytest([mutation["bitten_by"]])
            bitten = (not bitten) and not bite_timeout
            timed_out = timed_out or bite_timeout
            suite_ok, suite_timeout = run_pytest(SUITE)
            survived_suite = suite_ok and not suite_timeout
            timed_out = timed_out or suite_timeout
        finally:
            target.write_bytes(original)
        restored_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        if restored_sha != original_sha:
            failures.append(f"{mutation['id']}: restore NOT byte-identical")
            print(f"RESTORE-FAIL {mutation['id']}")
            continue
        if timed_out:
            failures.append(
                f"{mutation['id']}: a gated run TIMED OUT at {PYTEST_TIMEOUT_SECONDS}s — "
                "recorded, restored, not hung"
            )
            print(f"TIMEOUT {mutation['id']}")
            continue
        if not bitten:
            failures.append(f"{mutation['id']}: named test did not bite")
            print(f"SURVIVED {mutation['id']} — {mutation['bitten_by']} stayed green")
        else:
            print(f"KILLED {mutation['id']} by {mutation['bitten_by']}")
            if survived_suite:
                # The named test bit, yet the whole suite passed with the mutant in
                # place. That can only mean the named test is not in SUITE, so the
                # gate this matrix claims to protect never sees this defect.
                failures.append(
                    f"{mutation['id']}: killed by its named test but the SUITE stayed "
                    "green — the named test is outside the gate"
                )
                print(f"OUTSIDE-GATE {mutation['id']}")
    post_ok, post_timed_out = run_pytest(SUITE)
    if post_timed_out:
        failures.append(f"post-gate TIMED OUT at {PYTEST_TIMEOUT_SECONDS}s after restores")
    elif not post_ok:
        failures.append("post-gate RED after restores")
    else:
        print("post-gate GREEN")
    if failures:
        print("\nMATRIX FAILED:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nMATRIX PASS: every mutation killed by its named test; restores byte-identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
