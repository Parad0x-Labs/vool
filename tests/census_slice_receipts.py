#!/usr/bin/env python3
"""The discharge-channel census — lane-written receipts ÷ demand obligations.

PLAN-discharge-channel.md §5: the acceptance metric for the served-slot-loss fix.
Before the producing edge was attached (parent commit, measured 2026-08-30 on a
fresh daemon run) every consumption receipt was `served_answer_evidence` — written
by the finalization sweep from its own reading of the served bytes — and the count
of `slice_answer_record` receipts, the kind a SERVING lane writes, was zero.

Read-only: opens the obligation ledger's SQLite file directly and never writes.

USAGE
    VOOL_HOME=/tmp/<daemon-home> PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 \
        .venv/bin/python tests/census_slice_receipts.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path


def _db_path() -> Path:
    explicit = os.environ.get("CENSUS_DB", "")
    if explicit:
        return Path(explicit)
    home = os.environ.get("VOOL_HOME", "")
    if not home:
        raise SystemExit("Set VOOL_HOME (or CENSUS_DB) — refusing to guess a home.")
    return Path(home) / "data" / "vool_web0_v2.db"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    db_path = _db_path()
    if not db_path.exists():
        raise SystemExit(f"no ledger db at {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT set_id, version, snapshot_json FROM obligation_sets ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()

    sets = 0
    demand_minted = 0
    demand_with_subtask_provenance = 0  # obligations whose snapshot carries a slice_id
    receipts: Counter[str] = Counter()
    dispatch_states: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for row in rows:
        snapshot = json.loads(row["snapshot_json"])
        obligations = list(snapshot.get("obligations") or [])
        demand = [item for item in obligations if item.get("kind") == "demand"]
        if not demand:
            continue
        sets += 1
        demand_minted += len(demand)
        demand_with_subtask_provenance += sum(
            1 for item in demand if item.get("slice_id") or item.get("subtask_id")
        )
        for item in snapshot.get("consumption") or []:
            evidence = str(item.get("evidence") or "")
            receipts[evidence] += 1
            if evidence == "slice_answer_record" and evidence not in examples:
                examples[evidence] = (
                    f"{row['set_id']}/{row['version']} unit={item.get('unit_id')} "
                    f"family={item.get('family')}"
                )
        for item in snapshot.get("dispatches") or []:
            dispatch_states[str(item.get("state") or "?")] += 1

    lane_written = receipts.pop("slice_answer_record", 0)
    total_receipts = lane_written + sum(receipts.values())
    result = {
        "db": str(db_path),
        "obligation_sets_with_demand": sets,
        "demand_obligations_minted": demand_minted,
        "demand_with_subtask_provenance": demand_with_subtask_provenance,
        "receipts_total": total_receipts,
        "receipts_lane_written_slice_answer_record": lane_written,
        "receipts_by_other_evidence": dict(receipts),
        "dispatch_rows_by_state": dict(dispatch_states),
        "metric_lane_written_per_minted_demand": (
            round(lane_written / demand_minted, 4) if demand_minted else None
        ),
        "example_lane_receipt": examples.get("slice_answer_record", ""),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"db                       : {result['db']}")
        print(f"obligation sets (demand) : {sets}")
        print(f"demand obligations       : {demand_minted}")
        print(f"receipts total           : {total_receipts}")
        print(f"  lane-written           : {lane_written}  (slice_answer_record)")
        for evidence, count in sorted(receipts.items()):
            print(f"  {evidence:<24}: {count}")
        if dispatch_states:
            print(f"dispatch rows by state   : {dict(dispatch_states)}")
        print(
            "METRIC lane-written ÷ minted demand : "
            f"{result['metric_lane_written_per_minted_demand']}"
        )
        if examples:
            print(f"example lane receipt     : {examples['slice_answer_record']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
