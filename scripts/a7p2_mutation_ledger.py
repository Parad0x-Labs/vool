#!/usr/bin/env python3
"""A7 pass-002 mutation ledger runner.

For each load-bearing A7 guard in ``core/finalization.py``: apply a textual
mutation, run the detector tests, and require RED (>=1 failure); restore the
file, re-run, require GREEN. Any surviving-green mutation is reported as an
INCOMPLETE proof — never hidden.

Usage:  python scripts/a7p2_mutation_ledger.py [--only M1,M2]
Output: MUTATION_LEDGER.json next to cwd + per-mutation stdout verdicts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "core" / "finalization.py"
CC = "tests/foundation/test_a7p2_concurrency_crash.py"
SV = "tests/foundation/test_a7p2_served_matrix.py"

# (id, guard, original snippet, mutated snippet, detector -k expression)
MUTATIONS = [
    (
        "M01",
        "byte-equality/admission binding (HASH-LAST, WRITE-ONE identity)",
        'if stored and stored_hash == commit["content_hash"]:\n            return "ACCEPTED_IDENTICAL"',
        'if stored:\n            return "ACCEPTED_IDENTICAL"',
        f"{CC}::test_concurrent_different_bytes_same_admission_exactly_one_truth or {SV}::test_provider_retry_fallback_cannot_overwrite_sealed_truth",
    ),
    (
        "M02",
        "duplicate finalization guard (one truth row per admitted sr)",
        '                INSERT INTO a7_finalizations (\n                    finalization_id, semantic_result_id, turn_id,\n                    content_hash, canonical_content, status, request_id,\n                    payload_ref, availability\n                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, \'AVAILABLE\')\n                """,\n                (\n                    commit["finalization_id"],\n                    commit["semantic_result_id"],',
        '                INSERT INTO a7_finalizations (\n                    finalization_id, semantic_result_id, turn_id,\n                    content_hash, canonical_content, status, request_id,\n                    payload_ref, availability\n                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, \'AVAILABLE\')\n                """,\n                (\n                    commit["finalization_id"],\n                    commit["finalization_id"],',
        f"{CC}::test_concurrent_same_admission_finalizations_exactly_one_row",
    ),
    (
        "M03",
        "closure authority (durable ledger overrides caller dict)",
        '    if _bound_active is not None:\n        return dict(_obligations.closure_verdict(*_bound_active))\n    return dict(caller_closure) if caller_closure else _active_closure_verdict()',
        '    return dict(caller_closure) if caller_closure else _active_closure_verdict()',
        f"{CC}::test_caller_forged_covered_true_over_open_ledger_refused",
    ),
    (
        "M04",
        "fence requirement at finality (FENCE-OR-REFUSE)",
        '    from core.semantic.semantic_admissions import enforce_fence_if_onboarded\n\n    enforce_fence_if_onboarded()\n',
        '',
        f"{CC}::test_stale_generation_finalization_refused_zero_rows",
    ),
    (
        "M05",
        "payload availability guard (WITHHELD reversal forbidden)",
        "    AVAILABILITY_WITHHELD: {AVAILABILITY_ERASED},",
        "    AVAILABILITY_WITHHELD: {AVAILABILITY_ERASED, AVAILABILITY_AVAILABLE},",
        f"{CC}::test_withheld_to_available_reversal_is_refused_deterministically or tests/foundation/test_f1d_a7_payload_finality.py::test_erased_replay_returns_unavailable_by_policy_never_regenerates",
    ),
    (
        "M06",
        "delivery evidence strengthening (canonical vocabulary gate)",
        'if new_status == DELIVERY_DELIVERED and clean_class not in DELIVERY_EVIDENCE_CLASSES:',
        'if False:',
        f"{CC}::test_delivered_requires_canonical_evidence_class",
    ),
    (
        "M07",
        "DELIVERED absorbing terminal (no downgrade)",
        "    DELIVERY_DELIVERED: set(),",
        "    DELIVERY_DELIVERED: {DELIVERY_ATTEMPTED_UNKNOWN, DELIVERY_FAILED_TRANSPORT},",
        f"{CC}::test_delivery_transitions_race_never_downgrade_delivered",
    ),
    (
        "M08",
        "erased/withheld replay honesty (UNAVAILABLE_BY_POLICY)",
        '    availability = str(row.get("availability") or "AVAILABLE")\n    if availability in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):\n        return {',
        '    availability = str(row.get("availability") or "AVAILABLE")\n    if False:\n        return {',
        "tests/foundation/test_f1d_a7_payload_finality.py::test_erased_replay_returns_unavailable_by_policy_never_regenerates",
    ),
    (
        "M09",
        "replay identity (replay reads committed truth, mints nothing)",
        '    return {\n        "semantic_result_id": str(row.get("semantic_result_id") or ""),\n        "finalization_id": str(row.get("finalization_id") or ""),\n        "turn_id": str(row.get("turn_id") or ""),\n        "status": str(row.get("status") or ""),\n        "delivery_status": str(row.get("delivery_status") or ""),\n        "availability": availability,\n        "canonical_content": str(row.get("canonical_content") or ""),',
        '    return {\n        "semantic_result_id": str(row.get("semantic_result_id") or ""),\n        "finalization_id": "fc:" + hashlib.sha256(_utcnow().encode()).hexdigest()[:32],\n        "turn_id": str(row.get("turn_id") or ""),\n        "status": str(row.get("status") or ""),\n        "delivery_status": str(row.get("delivery_status") or ""),\n        "availability": availability,\n        "canonical_content": str(row.get("canonical_content") or ""),',
        f"{CC}::test_replay_mints_nothing_zero_rows_after_read",
    ),
    (
        "M10",
        "caller-forged closure acceptance (covered check)",
        '    if not verdict["covered"]:\n        raise FinalizationRejected(\n            "",\n            f"OBLIGATIONS_OPEN:{int(verdict[\'open_count\'])}:{verdict[\'set_version\']}",\n            "",\n        )',
        '',
        f"{CC}::test_open_obligation_blocks_finality_even_when_raced or {CC}::test_caller_forged_covered_true_over_open_ledger_refused",
    ),
    (
        "M11",
        "zero-accidental-bytes guard (empty content never a valid answer)",
        '    content = str(canonical_content or "")\n    if not content.strip():\n        raise NoAnswerContent(',
        '    content = str(canonical_content or "")\n    if False:\n        raise NoAnswerContent(',
        "tests/foundation/test_f1e_delivery_terminal.py::test_blank_done_only_stream_never_generic_success",
    ),
]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    only = {m.strip() for m in args.only.split(",") if m.strip()}

    original = TARGET.read_text()
    results = []
    incomplete = []
    try:
        for mid, guard, old, new, detectors in MUTATIONS:
            if only and mid not in only:
                continue
            if old not in original:
                results.append({"id": mid, "error": "SNIPPET NOT FOUND"})
                incomplete.append(mid)
                continue
            mutated = original.replace(old, new)
            TARGET.write_text(mutated)
            red = _run(
                ["uv", "run", "--python", "3.11", "--with", "pytest", "--",
                 "pytest", "-q", *detectors.split(" or ")]
            )
            red_failed = red.returncode != 0

            TARGET.write_text(original)
            green = _run(
                ["uv", "run", "--python", "3.11", "--with", "pytest", "--",
                 "pytest", "-q", *detectors.split(" or ")]
            )
            green_ok = green.returncode == 0

            verdict = "KILLED" if (red_failed and green_ok) else "SURVIVED"
            if verdict != "KILLED":
                incomplete.append(mid)
            results.append(
                {
                    "id": mid,
                    "guard": guard,
                    "red": red_failed,
                    "green": green_ok,
                    "verdict": verdict,
                    "red_tail": red.stdout.strip().splitlines()[-1:] ,
                    "green_tail": green.stdout.strip().splitlines()[-1:],
                }
            )
            print(f"{mid} {verdict}  ({guard})")
    finally:
        TARGET.write_text(original)

    out = REPO / "MUTATION_LEDGER.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nledger written: {out}")
    if incomplete:
        print(f"INCOMPLETE — surviving/not-run mutations: {incomplete}")
        return 1
    print("ALL MUTATIONS KILLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
