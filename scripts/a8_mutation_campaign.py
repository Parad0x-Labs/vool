#!/usr/bin/env python3
"""A8 pass-001 mutation campaign — real RED/GREEN sabotage proof.

For every adjudicated mutation (MUTATION_REQUIREMENTS.md), in an ISOLATED
temporary copy of the candidate tree:

    baseline GREEN -> sabotage the exact guard -> detector RED -> discard copy

The candidate working tree is never mutated. Results are written as JSON and a
Markdown ledger to the A8 builder evidence directory.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = Path(
    "/Users/example-user/vool/council/a8-canonical-privacy/pass-001-20260826"
)
PYTHON = os.environ.get("A8_PYTHON", "/Users/example-user/vool/.venv-c1-py312/bin/python")

DETECTOR_FILE = "tests/foundation/test_a8_privacy_pass001.py"

MUTATIONS = [
    {
        "id": "M-01",
        "guard": "WITHHELD -> AVAILABLE reversal allowed in transition table",
        "file": "core/finalization.py",
        "find": "AVAILABILITY_WITHHELD: {AVAILABILITY_ERASED},",
        "replace": "AVAILABILITY_WITHHELD: {AVAILABILITY_ERASED, AVAILABILITY_AVAILABLE},",
        "detector": ["tests/foundation/test_a7p2_concurrency_crash.py", "-k", "withheld_to_available"],
    },
    {
        "id": "M-02",
        "guard": "ERASED -> AVAILABLE reversal allowed (absorbing state broken)",
        "file": "core/finalization.py",
        "find": "AVAILABILITY_ERASED: set(),",
        "replace": "AVAILABILITY_ERASED: {AVAILABILITY_AVAILABLE},",
        "detector": ["tests/foundation/test_a7p2_concurrency_crash.py", "-k", "withheld_to_available"],
    },
    {
        "id": "M-03a",
        "guard": "replay availability check disabled",
        "file": "core/finalization.py",
        "find": "if availability in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):\n        return {\n            \"semantic_result_id\"",
        "replace": "if False and availability in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):\n        return {\n            \"semantic_result_id\"",
        "detector": ["tests/foundation/test_f1d_a7_payload_finality.py", "-k", "erased_replay"],
    },
    {
        "id": "M-03b",
        "guard": "raw readers serve unavailable bytes (suppression set emptied)",
        "file": "core/finalization.py",
        "find": "_UNSERVEABLE_AVAILABILITY = (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED)",
        "replace": "_UNSERVEABLE_AVAILABILITY = ()",
        "detector": [DETECTOR_FILE, "-k", "raw_readers_suppress"],
    },
    {
        "id": "M-04",
        "guard": "history content gate never fires",
        "file": "core/web/api/service.py",
        "find": "if availability in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):\n                        messages.append(",
        "replace": "if False and availability in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):\n                        messages.append(",
        "detector": [DETECTOR_FILE, "-k", "history_withheld_suppressed"],
    },
    {
        "id": "M-05",
        "guard": "replay principal validation stripped",
        "file": "core/finalization.py",
        "find": "    validate_principal(principal)\n    row = None",
        "replace": "    # sabotage: principal validation stripped\n    row = None",
        "detector": [DETECTOR_FILE, "-k", "principal_scoped_fail_closed"],
    },
    {
        "id": "M-06",
        "guard": "finalized_responses sweep step skipped (derivative survives)",
        "file": "core/finalization.py",
        "find": "(\"finalized_responses\", lambda: _sweep_step_finalized_responses(finalization_id, content_hash)),",
        "replace": "(\"finalized_responses\", lambda: \"completed\"),",
        "detector": [DETECTOR_FILE, "-k", "traversal_reaches_every_governed_derivative"],
    },
    {
        "id": "M-07",
        "guard": "sync_useful_outputs availability fence removed (resurrection)",
        "file": "storage/useful_output_store.py",
        "find": "if _source_unavailable(row.get(\"finalization_id\")):",
        "replace": "if False and _source_unavailable(row.get(\"finalization_id\")):",
        "detector": [DETECTOR_FILE, "-k", "sync_useful_outputs_cannot_resurrect"],
    },
    {
        "id": "M-08a",
        "guard": "mirror snapshot sweep step skipped",
        "file": "core/finalization.py",
        "find": "(\"mirror_snapshots\", lambda: _sweep_step_mirror_snapshots(finalization_id, content_hash)),",
        "replace": "(\"mirror_snapshots\", lambda: \"completed\"),",
        "detector": [DETECTOR_FILE, "-k", "traversal_reaches_every_governed_derivative"],
    },
    {
        "id": "M-08b",
        "guard": "mirror publish availability gate removed",
        "file": "relay/channel_outbound.py",
        "find": "if verdict is not None and verdict != AVAILABILITY_AVAILABLE:",
        "replace": "if False and verdict is not None and verdict != AVAILABILITY_AVAILABLE:",
        "detector": [DETECTOR_FILE, "-k", "mirror_publish_refuses"],
    },
    {
        "id": "M-09",
        "guard": "dialogue_turns lineage stamp dropped at write",
        "file": "storage/dialogue_memory.py",
        "find": "        return str(current_request_id() or \"\")",
        "replace": "        return \"\"",
        "detector": [DETECTOR_FILE, "-k", "lineage_stamped_at_write_time"],
    },
    {
        "id": "M-10",
        "guard": "erasure sweep-complete event recorded without traversal",
        "file": "core/finalization.py",
        "find": "    if not failed:\n        # The complete marker exists ONLY when every store step actually",
        "replace": "    if True:\n        # The complete marker exists ONLY when every store step actually",
        "detector": [DETECTOR_FILE, "-k", "no_sweep_complete_without_actual_traversal"],
    },
    {
        "id": "M-11a",
        "guard": "knowledge tombstone retains the withdrawn shard's content_hash",
        "file": "storage/knowledge_index.py",
        "find": "(shard_id, \"\", int(version), reason, _utcnow()),",
        "replace": "(shard_id, content_hash, int(version), reason, _utcnow()),",
        "detector": [DETECTOR_FILE, "-k", "knowledge_tombstone_retains_no"],
    },
    {
        "id": "M-11b",
        "guard": "erasure digest tombstone not written (legacy gate oracle lost)",
        "file": "core/finalization.py",
        "find": "if pre_erase_hash and not pre_erase_hash.startswith(\"salted-sha256:\"):",
        "replace": "if False and pre_erase_hash and not pre_erase_hash.startswith(\"salted-sha256:\"):",
        "detector": [DETECTOR_FILE, "-k", "digest_tombstone_survives_row_salting"],
    },
    {
        "id": "M-12a",
        "guard": "in-predicate epoch fence removed on _bind_durably INSERT",
        "file": "core/finalization.py",
        "find": "                    WHERE EXISTS (",
        "replace": "                    WHERE 1=1 OR EXISTS (",
        "detector": [DETECTOR_FILE, "-k", "stale_epoch_writer_cannot_mint"],
    },
    {
        "id": "M-12b",
        "guard": "hydration availability gate disabled (cached-availability disclosure)",
        "file": "core/persistent_memory.py",
        "find": "        if str(request_id or \"\").strip():",
        "replace": "        return False\n        if str(request_id or \"\").strip():",
        "detector": [DETECTOR_FILE, "-k", "hydration_excludes_unavailable"],
    },
    {
        "id": "M-12c",
        "guard": "delivery sweep availability predicate removed",
        "file": "core/finalization.py",
        "find": "(DELIVERY_ATTEMPTED_UNKNOWN, ANSWER_PRESENT, AVAILABILITY_AVAILABLE, int(limit)),",
        "replace": "(DELIVERY_ATTEMPTED_UNKNOWN, ANSWER_PRESENT, AVAILABILITY_WITHHELD, int(limit)),",
        "detector": [DETECTOR_FILE, "-k", "delivery_sweep_excludes_unavailable"],
    },
    {
        "id": "M-14",
        "guard": "honesty-receipt free-text availability gate removed",
        "file": "core/web/api/service.py",
        "find": "payload_unavailable = payload_state in (",
        "replace": "payload_unavailable = False and payload_state in (",
        "detector": [DETECTOR_FILE, "-k", "receipts_free_text_gated"],
    },
    {
        "id": "M-15",
        "guard": "pin serve availability gate removed",
        "file": "core/message_pins.py",
        "find": "if verdict in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):",
        "replace": "if False and verdict in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):",
        "detector": [DETECTOR_FILE, "-k", "pins_served_only_when_available"],
    },
    {
        "id": "M-D1",
        "guard": "chat delete no longer rebinds to the availability authority",
        "file": "core/memory/entries.py",
        "find": "    erase_finalizations_for_session(sid)",
        "replace": "    pass  # sabotage: delete rebind removed",
        "detector": [DETECTOR_FILE, "-k", "delete_conversation_session_rebinds"],
    },
    {
        "id": "M-WIRE",
        "guard": "typed replay outcome dropped from the wire response",
        "file": "core/web/api/service.py",
        "find": "\"replay_outcome\": str(\n                                prior.get(\"replay_outcome\") or \"AVAILABLE\"\n                            ),",
        "replace": "\"replay_outcome\": \"AVAILABLE\",",
        "detector": [DETECTOR_FILE, "-k", "identical_replay_carries_typed_outcome"],
    },
]


def make_copy(dest: Path) -> None:
    """Copy the candidate tree (tracked files + builder's new files) — the
    working tree itself is never mutated."""
    files = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for rel in files + untracked:
        if not rel or "__pycache__" in rel:
            continue
        src = REPO / rel
        if not src.is_file():
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)


def run_pytest(copy: Path, detector: list[str]) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", *detector, "-q", "-p", "no:randomly"],
        cwd=copy,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-2000:]


def apply_mutation(copy: Path, mutation: dict) -> bool:
    target = copy / mutation["file"]
    text = target.read_text(encoding="utf-8")
    count = text.count(mutation["find"])
    if count != 1:
        print(f"    APPLY FAILED: find-count={count} for {mutation['id']}")
        return False
    target.write_text(text.replace(mutation["find"], mutation["replace"]), encoding="utf-8")
    return True


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for mutation in MUTATIONS:
        mid = mutation["id"]
        print(f"[{mid}] {mutation['guard']}")
        with tempfile.TemporaryDirectory(prefix=f"a8mut-{mid}-") as tmp:
            copy = Path(tmp) / "tree"
            make_copy(copy)
            rc_green, out_green = run_pytest(copy, mutation["detector"])
            baseline_green = rc_green == 0
            applied = apply_mutation(copy, mutation)
            rc_red, out_red = run_pytest(copy, mutation["detector"])
            detector_red = rc_red != 0
            verdict = (
                "PASS"
                if baseline_green and applied and detector_red
                else "FAIL"
            )
            print(
                f"    baseline={'GREEN' if baseline_green else 'RED'} "
                f"applied={applied} sabotaged={'RED' if detector_red else 'GREEN'} -> {verdict}"
            )
            results.append(
                {
                    "id": mid,
                    "guard": mutation["guard"],
                    "file": mutation["file"],
                    "detector": " ".join(mutation["detector"]),
                    "baseline_green": baseline_green,
                    "mutation_applied": applied,
                    "detector_red": detector_red,
                    "verdict": verdict,
                    "red_tail": out_red[-400:] if detector_red else "",
                    "baseline_tail": out_green[-200:] if not baseline_green else "",
                }
            )
    stamp = datetime.now(timezone.utc).isoformat()
    summary = {
        "ran_at": stamp,
        "python": PYTHON,
        "mutation_count": len(results),
        "passed": sum(1 for r in results if r["verdict"] == "PASS"),
        "failed": sum(1 for r in results if r["verdict"] == "FAIL"),
        "results": results,
    }
    (EVIDENCE_DIR / "MUTATION_LEDGER.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    lines = [
        "# MUTATION LEDGER — A8 canonical privacy builder pass-001",
        "",
        f"Ran at `{stamp}`. Procedure per mutation: isolated tree copy → baseline GREEN →",
        "sabotage the exact guard → detector RED → copy discarded (restored by construction).",
        "M-13 (external-erasure vocabulary) is a vocabulary-absence pin (grep-law test),",
        "per MUTATION_REQUIREMENTS.md — no production guard exists to sabotage.",
        "",
        f"**{summary['passed']}/{summary['mutation_count']} mutations detected (RED under sabotage).**",
        "",
        "| # | Guard sabotaged | File | Baseline | Applied | Detector | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {r['guard']} | `{r['file']}` | "
            f"{'GREEN' if r['baseline_green'] else 'RED'} | "
            f"{'yes' if r['mutation_applied'] else 'no'} | "
            f"{'RED' if r['detector_red'] else 'GREEN'} | {r['verdict']} |"
        )
    (EVIDENCE_DIR / "MUTATION_LEDGER.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{summary['passed']}/{summary['mutation_count']} PASS")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
