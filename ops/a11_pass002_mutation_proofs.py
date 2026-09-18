#!/usr/bin/env python3
"""A11 PASS002 mutation-proof runner — mechanical sabotage of each load-bearing guard.

For every invariant repaired in pass002 this runner:
  1. mechanically mutates the REAL production guard in place (a targeted string edit),
  2. runs the named fence tests and expects them to FAIL for the right reason,
  3. restores the file byte-identically (sha256 verified),
  4. re-runs the fences and expects GREEN.

Usage:  python ops/a11_pass002_mutation_proofs.py
        (run from the repo root; needs the repo's pytest + the pass002 fences)
Exit 0 = every mutation was caught by its named fence; exit 1 otherwise.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

PYTEST = sys.argv[1] if len(sys.argv) > 1 else sys.executable

REPO = Path(__file__).resolve().parent.parent
P2 = "tests/test_a11_pass002_authority.py"
P1 = "tests/test_a11_minimum_product_truth.py"

# (id, file, old, new, failing tests, why)
MUTATIONS = [
    (
        "M1-central-authority-removed",
        "core/cloud_model_control.py",
        'if classification["cost_state"] != COST_STATE_FREE and not confirm_paid:',
        "if False and classification[\"cost_state\"] != COST_STATE_FREE and not confirm_paid:",
        [f"{P2}::test_paid_pin_refused_on_every_surface_identically",
         f"{P2}::test_case_spacing_and_prefix_variants_cannot_dodge_the_paid_decision"],
        "with the authority gate dead, the chat command persists the id the endpoint refused",
    ),
    (
        "M2-fail-closed-inverted-unknown-becomes-free",
        "core/cloud_model_control.py",
        '''        if row is None:
            return {
                "cost_state": COST_STATE_UNKNOWN,
                "reason_code": MODEL_COST_UNKNOWN,
                "row_verified_free": False,
            }''',
        '''        if row is None:
            return {
                "cost_state": COST_STATE_FREE,
                "reason_code": "",
                "row_verified_free": False,
            }''',
        [f"{P2}::test_adversarial_non_free_suffix_on_a_paid_base_is_not_read_as_free"],
        "a missing row silently becomes FREE — the exact 'missing catalog allows unknown model' hole",
    ),
    (
        "M3-canonicalization-removed",
        "core/cloud_model_control.py",
        "row = next(\n            (item for item in all_models if canonical_model_identity(item.model_id) == probe), None\n        )",
        "row = next(\n            (item for item in all_models if str(item.model_id) == model_id), None\n        )",
        [f"{P2}::test_case_spacing_and_prefix_variants_cannot_dodge_the_paid_decision"],
        "raw-case matching lets ZZZ/ALPHA-BIG dodge the row it really is",
    ),
    (
        "M4-get-owner-gate-removed",
        "core/web/api/service.py",
        '''        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"error": "owner_local_required"}), runtime)
        from core import cloud_escalation_policy as _cep''',
        '''        from core import cloud_escalation_policy as _cep''',
        [f"{P1}::test_get_is_owner_local_symmetric_with_post",
         f"{P2}::test_get_is_gated_like_the_post_that_sets_the_same_state"],
        "GET hands the pinned selection (spend state) to any remote reader again",
    ),
    (
        "M5-stale-pin-clear-removed",
        "core/vool_chat_page.py",
        '''  if (!serverModel) {
    modelValue = 'vool';
    localStorage.setItem('vool_model', 'vool');
    rememberActiveModel();
    try { reflectModel(); } catch (e) {}
    return;
  }''',
        '''  if (!serverModel) {
    return;
  }''',
        [f"{P1}::test_boot_hydration_clears_stale_pin_when_server_pin_empty"],
        "an empty server pin leaves the stale (paid) browser pin alive across reloads",
    ),
    (
        "M6-client-decides-paid-again",
        "core/vool_chat_page.py",
        "body: JSON.stringify({ model: id }) });",
        "body: JSON.stringify({ model: id, confirm_paid: true }) });",
        [f"{P1}::test_switch_posts_confirm_paid_for_the_server_paid_gate"],
        "the client pre-asserts the confirmation, re-becoming the paid/free decider",
    ),
    (
        "M7-receipt-provenance-reverts-to-plan",
        "core/cloud_broker.py",
        "fallback_from=predecessor_key,",
        "fallback_from=fallback_chain[0] if fallback_chain else \"\",",
        [f"{P2}::test_single_candidate_first_try_success_reports_no_fallback",
         f"{P2}::test_multi_hop_fallback_names_the_actual_previous_executed_hop",
         f"{P2}::test_never_executed_candidates_are_not_named_as_predecessors"],
        "receipts lie again: self-referencing/plan-derived fallback provenance",
    ),
    (
        "M8-restore-before-snapshot-removed",
        "core/mode_permission_policy.py",
        "    _restore_persisted_approvals()\n    path = _pending_approvals_path()\n    if path is None:\n        return",
        "    path = _pending_approvals_path()\n    if path is None:\n        return",
        [f"{P2}::test_post_restart_mint_cannot_erase_another_pending_approval"],
        "a post-restart mint overwrites the mirror from un-restored memory and erases a pending approval",
    ),
    (
        "M9-reset-disk-clear-removed",
        "core/mode_permission_policy.py",
        '''    try:
        path = _pending_approvals_path()
        if path is not None and path.exists():
            path.unlink(missing_ok=True)
    except Exception:
        pass  # best-effort; an unremovable mirror leaves no revivable authority to read''',
        "",
        [f"{P2}::test_reset_cannot_be_resurrected_from_the_disk_mirror"],
        "reset clears memory only — disk revives the supposedly cleared approval",
    ),
    (
        "M10-expiry-enforcement-removed",
        "core/mode_permission_policy.py",
        '''        if float(approval.get("expires_at") or 0) <= time.time():
            approval["status"] = "expired"
            _persist_pending_approvals_locked()
            return None''',
        "",
        [f"{P2}::test_expiry_is_enforced_on_resolution_of_a_live_process_entry"],
        "an expired prompt converts into a grant when resolution never re-checks lifetime",
    ),
    (
        "M11-command-flag-bypass-readded",
        "core/agent_runtime/fast_command_surface.py",
        "arg, owner_local=owner_local, confirm_paid=confirm_paid\n    )",
        "arg, owner_local=owner_local, confirm_paid=True\n    )",
        [f"{P2}::test_paid_pin_refused_on_every_surface_identically"],
        "the command surface self-grants the confirmation instead of carrying operator intent",
    ),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_pytest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [PYTEST, "-m", "pytest", "-q", *targets],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-1500:]


def main() -> int:
    failures: list[str] = []
    for mid, rel, old, new, fences, why in MUTATIONS:
        path = REPO / rel
        original = path.read_text()
        before = sha256(path)
        if old not in original:
            failures.append(f"{mid}: MUTATION ANCHOR NOT FOUND in {rel} (the instrument is stale)")
            continue
        path.write_text(original.replace(old, new, 1))
        try:
            code, tail = run_pytest(fences)
        finally:
            path.write_text(original)
        restored = sha256(path) == before
        red_ok = code != 0
        status = "CAUGHT" if (red_ok and restored) else "PROBLEM"
        print(f"[{status}] {mid}: pytest exit={code} restored_byte_identical={restored} — {why}")
        if not red_ok:
            failures.append(f"{mid}: sabotage SURVIVED — the invariant is unprotected: {fences}")
        if not restored:
            failures.append(f"{mid}: file not restored byte-identically — restore manually from git")
    # final: fences green on the pristine tree
    code, tail = run_pytest([P1, P2, "tests/test_cloud_model_control.py"])
    print(f"pristine fence rerun: exit={code}")
    if code != 0:
        failures.append("pristine rerun not green: " + tail[-500:])
    if failures:
        print("\n".join("PROBLEM: " + f for f in failures))
        return 1
    print("ALL MUTATIONS CAUGHT — every repaired invariant is protected by a named fence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
