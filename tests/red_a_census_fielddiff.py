#!/usr/bin/env python3
"""RED-A companion: full per-column diff of two red_a census JSON captures.

WHY THIS EXISTS
---------------
`red_a_reroute_census.py --baseline` reports only three quantities: contract lost,
contract gained, and whole-turn claimant moved.  A regression in the admission/routing
surface can also show up as a changed admitting arm, a changed slice decomposition, a
changed unclaimed set, or a flipped reroute column -- none of which that diff prints.
This tool diffs EVERY column the census records, over the intersection of the two
captures, and separately over the baseline's closed-contract population.

It also reports population drift with attribution, because the census harvests string
literals out of `tests/*.py` and therefore harvests the source text of any red
instrument that happens to name an admission-seam symbol.  That inflates P_closed
without any runtime change; the intersection-restricted counts printed here are the
apples-to-apples numbers.

NOTE ON NAMING: this module deliberately contains no admission-seam symbol as a source
literal (column names are read from the capture at run time, never hard-coded), so that
adding it to tests/ does not itself enlarge the census population.

USAGE
-----
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
        tests/red_a_census_fielddiff.py BEFORE.json AFTER.json

READ-ONLY.  Reads two JSON captures; writes nothing.
"""
from __future__ import annotations

import collections
import json
import sys
from typing import Any

# Columns are discovered from the capture rather than written out here, so this file
# stays free of seam substrings.  "text" is the join key, not a measured column.
JOIN_KEY = "text"
CLOSED_KEY = "contract" + "_holds"


def _norm(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _columns(rows: list[dict]) -> list[str]:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key != JOIN_KEY and key not in keys:
                keys.append(key)
    return keys


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    before = json.load(open(sys.argv[1], encoding="utf-8"))
    after = json.load(open(sys.argv[2], encoding="utf-8"))
    b_rows = {r[JOIN_KEY]: r for r in before["rows"]}
    a_rows = {r[JOIN_KEY]: r for r in after["rows"]}
    shared = [t for t in b_rows if t in a_rows]
    only_after = [t for t in a_rows if t not in b_rows]
    only_before = [t for t in b_rows if t not in a_rows]
    columns = _columns(before["rows"])

    print("=" * 78)
    print("RED-A CENSUS FIELD DIFF")
    print("=" * 78)
    print(f"before : {before.get('head','?')[:8]}  {before.get('tree')}  rows={len(b_rows)}")
    print(f"after  : {after.get('head','?')[:8]}  {after.get('tree')}  rows={len(a_rows)}")
    print(f"shared literals={len(shared)}  new={len(only_after)}  removed={len(only_before)}")
    print()

    for label, population in (
        (f"ALL {len(shared)} SHARED LITERALS", shared),
        (
            "BASELINE CLOSED-CONTRACT POPULATION",
            [t for t in shared if b_rows[t].get(CLOSED_KEY)],
        ),
    ):
        print(f"=== PER-COLUMN DIFF, {label} (n={len(population)}) ===")
        moved_any: set[str] = set()
        for column in columns:
            moved = [
                t
                for t in population
                if _norm(b_rows[t].get(column)) != _norm(a_rows[t].get(column))
            ]
            moved_any |= set(moved)
            print(f"  {column:34s} changed: {len(moved)}")
            for t in moved[:5]:
                print(f"      ~ {t!r}")
                print(f"          before={b_rows[t].get(column)}")
                print(f"          after ={a_rows[t].get(column)}")
        print(f"  {'ANY COLUMN':34s} changed: {len(moved_any)}")
        print()

    print("=== HEADLINE COUNTS RESTRICTED TO THE SHARED POPULATION ===")
    for column in columns:
        if not column.startswith("reroute"):
            continue
        nb = sum(1 for t in shared if b_rows[t].get(CLOSED_KEY) and b_rows[t].get(column))
        na = sum(1 for t in shared if a_rows[t].get(CLOSED_KEY) and a_rows[t].get(column))
        print(f"  {column:38s} before={nb:4d} after={na:4d} delta={na - nb:+d}")
    pb = sum(1 for t in shared if b_rows[t].get(CLOSED_KEY))
    pa = sum(1 for t in shared if a_rows[t].get(CLOSED_KEY))
    print(f"  {'closed population':38s} before={pb:4d} after={pa:4d} delta={pa - pb:+d}")
    print()

    if only_after:
        print("=== POPULATION DRIFT ATTRIBUTION (new literals by origin module) ===")
        origins = after.get("origins", {})
        counter: collections.Counter = collections.Counter()
        closed_counter: collections.Counter = collections.Counter()
        for t in only_after:
            for module in origins.get(t, []):
                counter[module] += 1
                if a_rows[t].get(CLOSED_KEY):
                    closed_counter[module] += 1
        for module, n in counter.most_common():
            print(f"  {n:5d}  {module}   (closed: {closed_counter.get(module, 0)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
