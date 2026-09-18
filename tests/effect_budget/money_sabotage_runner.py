"""Sabotage proofs for the monetary law: every mutation must turn its named
tests red while every other money test stays green, and every file is restored
from the committed candidate (hash-checked) before the next mutation.

Usage (from anywhere, on a clean committed tree):
    python -B tests/effect_budget/money_sabotage_runner.py <repo> <python> <evidence_dir> [mutation-id ...]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
PYTHON = sys.argv[2]
OUT = Path(sys.argv[3]).resolve()
ONLY = set(sys.argv[4:])

MONEY_TESTS = [
    "tests/effect_budget/test_money_authority.py",
    "tests/effect_budget/test_money_cross_process.py",
    "tests/effect_budget/test_money_gateway_and_provider.py",
    "tests/effect_budget/test_money_served_api.py",
    "tests/effect_budget/test_money_collection_order.py",
]
ENV = {
    **os.environ,
    "PYTHONPATH": str(REPO),
    "PYTHONDONTWRITEBYTECODE": "1",
    "VOOL_KEY_STORAGE_MODE": "file",
    "VOOL_CREDENTIAL_STORE": "vault",
    "VOOL_ALLOW_LIVE_OLLAMA_TESTS": "0",
    "VOOL_SKIP_PROVIDER_PREWARM": "1",
    "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
    "VOOL_ALPHA_LIVE_SOAK": "0",
    "PYTEST_ADDOPTS": "",
}

AUTH = "tests/effect_budget/test_money_authority.py::"
XPROC = "tests/effect_budget/test_money_cross_process.py::"
DOORS = "tests/effect_budget/test_money_gateway_and_provider.py::"
SERVED = "tests/effect_budget/test_money_served_api.py::"
ORDER = "tests/effect_budget/test_money_collection_order.py::"

MUTATIONS = [
    {
        "id": "S1-grant-consumption-reads-zero",
        "file": "core/effect_budget_money.py",
        "old": '    return {\n        "principal": principal,\n        "fees": fees,',
        "new": '    return {\n        "principal": 0,\n        "fees": 0,',
        "must_fail": [
            AUTH + "test_prepaid_reservation_holds_the_maximum_and_consumes_the_grant_in_one_act",
            XPROC + "test_four_processes_each_needing_three_against_a_total_of_four_one_wins_no_overspend",
        ],
    },
    {
        "id": "S2-check-then-write-gap-commit-before-insert",
        "file": "core/effect_budget_money.py",
        "old": "        liability_id = \"mli:\" + secrets.token_hex(10)\n        identity = request.identity\n",
        "new": "        conn.commit()\n        time.sleep(0.25)\n        liability_id = \"mli:\" + secrets.token_hex(10)\n        identity = request.identity\n",
        "prelude": ("from core import effect_budget as _eb\n", "import time\n\nfrom core import effect_budget as _eb\n"),
        "must_fail": [
            XPROC + "test_four_processes_each_needing_three_against_a_total_of_four_one_wins_no_overspend",
        ],
    },
    {
        "id": "S3-dead-claimant-released-not-unknown",
        "file": "core/effect_budget_money.py",
        "old": '                    _transition(conn, row, LIABILITY_UNKNOWN, sets={"unknown_reason": reason})',
        "new": '                    _transition(conn, row, LIABILITY_RELEASED, sets={"close_reason": reason})',
        "must_fail": [
            XPROC + "test_process_death_at_each_boundary_then_restart_never_reopens_unknown_money",
            XPROC + "test_an_exited_unclaimed_reservation_returns_while_a_claimed_one_stays_held",
        ],
    },
    {
        "id": "S4-record-unknown-releases",
        "file": "core/effect_budget_money.py",
        "old": '        _transition(txn.conn, row, LIABILITY_UNKNOWN, sets={"unknown_reason": clean_reason})',
        "new": '        _transition(txn.conn, row, LIABILITY_RELEASED, sets={"close_reason": clean_reason})',
        "must_fail": [
            AUTH + "test_unknown_holds_the_maximum_and_only_external_proof_releases_it",
            DOORS + "test_the_claim_precedes_the_attempt_and_a_failed_attempt_is_unknown_not_released",
        ],
    },
    {
        "id": "S5-over-cap-silent",
        "file": "core/effect_budget_money.py",
        "old": "            over_cap = over_cap or amount > maximum\n            conn.execute(\n                f\"UPDATE {_LINES_TABLE} SET actual_atomic=?, line_state=?, evidence_id=?, resolved_epoch=? WHERE line_id=?\",",
        "new": "            over_cap = False\n            conn.execute(\n                f\"UPDATE {_LINES_TABLE} SET actual_atomic=?, line_state=?, evidence_id=?, resolved_epoch=? WHERE line_id=?\",",
        "must_fail": [AUTH + "test_settlement_is_exact_where_proven_bounded_where_not_and_never_silent_over_cap"],
    },
    {
        "id": "S6-liquidity-ignores-held-debits",
        "file": "core/effect_budget_money.py",
        "old": "        total += _line_amount(row, liability_id=liability_id)\n    return total\n\n\ndef _verified_credits_since",
        "new": "        total += 0\n    return total\n\n\ndef _verified_credits_since",
        "must_fail": [
            AUTH + "test_a_fee_asset_shortage_refuses_even_when_the_principal_is_funded",
            XPROC + "test_a_novel_race_where_the_fee_asset_and_verified_credit_bind_not_the_budget",
        ],
    },
    {
        "id": "S7-gateway-skips-the-dispatch-claim",
        "file": "core/effect_gateway.py",
        "old": "        if not self._money_liability_id:\n            return None\n        from core import effect_budget_money as ebm\n        from core.effect_budget import EffectBudgetRefusedError\n\n        executor = ",
        "new": "        if True:\n            return None\n        from core import effect_budget_money as ebm\n        from core.effect_budget import EffectBudgetRefusedError\n\n        executor = ",
        "must_fail": [
            DOORS + "test_the_claim_precedes_the_attempt_and_a_failed_attempt_is_unknown_not_released",
            DOORS + "test_proven_unsent_is_retried_through_the_same_lifecycle_and_then_settles",
        ],
    },
    {
        "id": "S8-permit-consume-skips-the-claim",
        "file": "core/provider_invocation_gateway.py",
        "old": "            claim = None\n            if self._money_liability_id:",
        "new": "            claim = None\n            if False:",
        "must_fail": [
            DOORS + "test_the_permit_claims_money_before_the_payload_leaves_and_an_interrupted_stream_stays_unknown",
            DOORS + "test_revocation_stops_consume_before_the_payload_leaves",
        ],
    },
    {
        "id": "S9-pid-liveness-off",
        "file": "core/effect_budget.py",
        "old": "    if os.name != \"posix\":\n        return False",
        "new": "    if True:\n        return False",
        "must_fail": [XPROC + "test_an_exited_unclaimed_reservation_returns_while_a_claimed_one_stays_held"],
    },
    {
        "id": "S10-owned-identity-overridden-silently",
        "file": "core/effect_budget_money.py",
        "old": "        if owned_value and stated_value and owned_value != stated_value:\n            raise EffectBudgetRefusedError(\n                MONEY_IDENTITY_CONFLICT,",
        "new": "        if False:\n            raise EffectBudgetRefusedError(\n                MONEY_IDENTITY_CONFLICT,",
        "must_fail": [
            DOORS + "test_a_stated_identity_that_conflicts_with_the_turn_is_refused_and_its_unit_returns",
            DOORS + "test_the_sealed_model_and_provider_are_the_liabilitys",
        ],
    },
    {
        "id": "S11-revocation-not-checked-at-claim",
        "file": "core/effect_budget_money.py",
        "old": "        if grant.state == GRANT_REVOKED:\n            refusal = (MONEY_AUTHORITY_REVOKED,",
        "new": "        if False:\n            refusal = (MONEY_AUTHORITY_REVOKED,",
        "must_fail": [
            AUTH + "test_revocation_before_claim_releases_the_never_sent_reservation_and_refuses",
            DOORS + "test_revocation_stops_consume_before_the_payload_leaves",
        ],
    },
    {
        "id": "S12-freeze-unread",
        "file": "core/effect_budget_money.py",
        "old": "        from core.wallet import limits\n\n        return bool(limits.is_frozen())",
        "new": "        from core.wallet import limits\n\n        return False",
        "must_fail": [AUTH + "test_the_wallet_panic_freeze_stops_wallet_debits_and_not_prepaid_credit"],
    },
    {
        "id": "S13-served-widening-route-open",
        "file": "core/web/api/money_authority_api.py",
        "old": "    if path in _WIDENING_PATHS:",
        "new": "    if False:",
        "must_fail": [SERVED + "test_money_routes_are_owner_local_guarded_and_never_widen_authority"],
    },
    {
        "id": "S14-duplicate-evidence-applies-twice",
        "file": "core/effect_budget_money.py",
        "old": "        if prior is not None:\n            if str(prior[\"digest\"]) == digest:",
        "new": "        if False:\n            if str(prior[\"digest\"]) == digest:",
        "must_fail": [
            AUTH + "test_duplicate_evidence_is_a_no_op_and_conflicting_evidence_changes_nothing",
            XPROC + "test_duplicate_and_conflicting_receipts_from_several_processes",
        ],
    },
    {
        "id": "S15-reconciler-not-put-on-record",
        "file": "core/effect_budget_money.py",
        "old": '                    reconciler["id"] = _eb._current_instance(conn)\n',
        "new": '                    reconciler["id"] = ""\n',
        "must_fail": [
            AUTH + "test_a_dead_reserver_releases_and_a_dead_claimant_becomes_unknown_in_process_simulation",
            AUTH + "test_each_automatic_release_or_unknown_cites_its_own_proof_and_spares_live_holdings",
            XPROC + "test_an_exited_unclaimed_reservation_returns_while_a_claimed_one_stays_held",
        ],
    },
    {
        "id": "S16-death-proof-not-cited",
        "file": "core/effect_budget_money.py",
        "old": "                return _eb._instance_death_reason(instance, now)\n",
        "new": "                return \"dead\" if _eb._instance_death_reason(instance, now) else \"\"\n",
        "must_fail": [
            AUTH + "test_a_dead_reserver_releases_and_a_dead_claimant_becomes_unknown_in_process_simulation",
            AUTH + "test_each_automatic_release_or_unknown_cites_its_own_proof_and_spares_live_holdings",
            XPROC + "test_an_exited_unclaimed_reservation_returns_while_a_claimed_one_stays_held",
        ],
    },
    {
        "id": "S17-live-instance-judged-gone",
        "file": "core/effect_budget.py",
        "old": "    if _instance_process_gone(row):\n        return INSTANCE_DEATH_PROCESS_GONE\n",
        "new": "    if True:\n        return INSTANCE_DEATH_PROCESS_GONE\n",
        "must_fail": [AUTH + "test_each_automatic_release_or_unknown_cites_its_own_proof_and_spares_live_holdings"],
    },
    {
        "id": "S18-package-isolation-not-exported",
        "file": "tests/effect_budget/conftest.py",
        "old": '    "_isolated_budget_store",\n    "_real_home_residue_guard",\n',
        "new": "",
        "must_fail": [
            ORDER + "test_money_isolation_survives_a_collection_order_that_re_enters_the_package[package-parent-authority]",
            ORDER + "test_money_isolation_survives_a_collection_order_that_re_enters_the_package[package-parent-gateway-served-crossprocess]",
        ],
    },
]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True).stdout.strip()


def blob(path: str) -> str:
    return git("hash-object", path)


def run_tests(label: str) -> dict[str, str]:
    junit = OUT / f"{label}.junit.xml"
    log = OUT / f"{label}.log"
    # the slow real-daemon test stays out of the per-mutation loop; it is covered by its own gate
    deselect = ["--deselect", SERVED + "test_the_daemon_serves_money_state_written_by_independent_processes_and_keeps_it_across_restart"]
    command = [PYTHON, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q", "-m", "not pa_beta_live and not live", "--junitxml", str(junit), *deselect, *MONEY_TESTS]
    with log.open("w") as handle:
        subprocess.run(command, cwd=str(REPO), env=ENV, stdout=handle, stderr=subprocess.STDOUT, timeout=900)
    import xml.etree.ElementTree as ET

    outcomes: dict[str, str] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        file_part = str(case.get("classname") or "").replace(".", "/") + ".py"
        node = f"{file_part}::{case.get('name')}"
        if case.find("failure") is not None or case.find("error") is not None:
            outcomes[node] = "failed"
        elif case.find("skipped") is not None:
            outcomes[node] = "skipped"
        else:
            outcomes[node] = "passed"
    return outcomes


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    head = git("rev-parse", "HEAD")
    if git("status", "--porcelain"):
        print("tree is not clean; commit before sabotage")
        return 2
    report = {"head": head, "mutations": []}
    control = run_tests("control-before")
    report["control_before"] = {"failed": sorted(k for k, v in control.items() if v == "failed"), "passed": sum(1 for v in control.values() if v == "passed")}
    for mutation in MUTATIONS:
        if ONLY and mutation["id"] not in ONLY:
            continue
        path = REPO / mutation["file"]
        original = path.read_text(encoding="utf-8")
        assert blob(mutation["file"]) == git("rev-parse", f"HEAD:{mutation['file']}"), mutation["file"]
        text = original
        count = text.count(mutation["old"])
        entry = {"id": mutation["id"], "file": mutation["file"], "anchor_count": count}
        if count != 1:
            entry["verdict"] = "ANCHOR_NOT_UNIQUE"
            report["mutations"].append(entry)
            continue
        text = text.replace(mutation["old"], mutation["new"])
        if "prelude" in mutation:
            before, after = mutation["prelude"]
            if text.count(before) == 1:
                text = text.replace(before, after)
        path.write_text(text, encoding="utf-8")
        for cache in REPO.rglob("__pycache__"):
            for stale in cache.glob(Path(mutation["file"]).stem + "*.pyc"):
                stale.unlink()
        started = time.monotonic()
        try:
            outcomes = run_tests(mutation["id"])
        finally:
            git("checkout", "--", mutation["file"])
        entry["restored"] = blob(mutation["file"]) == git("rev-parse", f"HEAD:{mutation['file']}")
        entry["seconds"] = round(time.monotonic() - started, 1)
        failed = sorted(k for k, v in outcomes.items() if v == "failed")
        entry["failed"] = failed
        entry["must_fail_missing"] = [node for node in mutation["must_fail"] if node not in failed]
        entry["collateral_failures"] = [node for node in failed if node not in mutation["must_fail"]]
        entry["verdict"] = "BITES" if not entry["must_fail_missing"] else "ABSORBED"
        report["mutations"].append(entry)
        print(json.dumps({k: entry[k] for k in ("id", "verdict", "must_fail_missing", "restored", "seconds")}), flush=True)
    control_after = run_tests("control-after")
    report["control_after"] = {"failed": sorted(k for k, v in control_after.items() if v == "failed"), "passed": sum(1 for v in control_after.values() if v == "passed")}
    report["tree_clean_after"] = not git("status", "--porcelain")
    (OUT / "sabotage-report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"control_before": report["control_before"], "control_after": report["control_after"], "tree_clean_after": report["tree_clean_after"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
