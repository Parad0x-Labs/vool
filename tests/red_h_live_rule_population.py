"""RED-H — RED-G's population question, asked of the rule the sweep ACTUALLY consults.

WHY THIS FILE EXISTS (the adjudicated defect in RED-D and RED-G)
---------------------------------------------------------------
RED-D (`tests/red_d_sweep_attack.py:448`) and RED-G (`tests/red_g_overrender_population.py:129`)
both compute a unit's dischargeability with a HAND-COPY of the filter inside
`answer_coverage.units_present_in_answer`:

    eligible = [t for t in ac._unit_tokens(u.text) if len(t) >= 3 and t not in stop]

At `ed027e52` that was the sweep's rung 2, so the copy was faithful. Commit `2a3986d2`
deleted that rung from `core/finalization.py` — the diff removes
`units_present_in_answer` / `evidence="served_content_overlap"` and installs
`unit_answer_evidence` — and `units_present_in_answer` now has ZERO production callers.
So both instruments still measure a rule the runtime no longer runs. BLUE-1's objection is
upheld; this file is the re-measurement.

WHAT MEASURING "THE LIVE RULE" MEANS, AND WHY IT IS NOT JUST SWAPPING THE PREDICATE
----------------------------------------------------------------------------------
The obvious repair — swap `len(t) >= 3 and t not in stop` for `ac.unit_anchors` — would
report ZERO undischargeable units and read as an all-clear. It is not one. The live rule
opens a hazard in the OPPOSITE direction, in `unit_answer_evidence`:

    anchors = unit_anchors(unit.text)
    if not anchors:
        verdicts[unit.unit_id] = DEMAND_SATISFIED   # <-- whatever the answer says

A unit with no anchors is booked SATISFIED against bytes nobody looked at. An instrument
that counted only the old direction would give a clean bill of health to a runtime that
silently discharges slots. So RED-H measures BOTH directions, and neither number alone is
the verdict:

  UNDISCHARGEABLE     Given an ORACLE answer that restates every unit of the turn verbatim,
                      the unit is still not SATISFIED. Nothing the runtime could say would
                      discharge it -> the successor of RED-G's ALWAYS_RENDER class.

  SILENT_DISCHARGE    Given an EMPTY answer -- the runtime served nothing at all -- the unit
                      is booked SATISFIED anyway. The accounting reports coverage it has no
                      evidence for. Split into `no_anchors` (the branch above) and
                      `ride_along` (the sweep's `ride_along_no_object` rung, which records
                      consumption before any evidence is read).

  UNDISCLOSABLE_TURN  `core/finalization.py` collapses every non-satisfied verdict to
                      `indeterminate` when `len(states) < 2`, so a turn minting fewer than
                      two units can NEVER render a row however badly it was served. Counted
                      because it bounds what any disclosure number can mean.

Every classification below is produced by CALLING the tree -- `demand_units`,
`unit_anchors`, `unit_answer_evidence`, `units_without_own_object` -- and the two answers
fed to it are constructed from the turn itself, never from an expected verdict.

POPULATION
----------
Identical to RED-G's by construction: `load_population` is IMPORTED from
`tests/red_g_overrender_population.py` rather than re-written, so the two instruments cannot
drift apart on which strings they read out of RED-A's JSON.

USAGE
    cd <repair worktree>
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_h_live_rule_population.py --tree "$PWD" --red-a <red_a.json> --out red_h.json

READ-ONLY. Imports and calls the measured tree; writes only --out. Never touches the daemon.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", default=os.getcwd())
    ap.add_argument("--red-a", required=True, help="RED-A --out JSON (the harvested population)")
    ap.add_argument("--show", type=int, default=12)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tree = os.path.realpath(args.tree)
    sys.path.insert(0, tree)
    sys.path.insert(0, os.path.join(tree, "tests"))

    from core.agent_runtime import answer_coverage as ac

    resolved = os.path.realpath(ac.__file__)
    if not resolved.startswith(tree):
        raise SystemExit(f"REFUSING TO RUN: answer_coverage resolved to {resolved}, outside {tree}")

    # Population read through RED-G's own loader so the two cannot diverge.
    import red_g_overrender_population as red_g

    population = red_g.load_population(args.red_a)

    print("=" * 94)
    print("RED-H — the population question, asked of the rule the sweep actually consults")
    print("=" * 94)
    print(f"tree       : {tree}")
    print(f"module     : {resolved}")
    print(f"population : {len(population)} distinct prompt-shaped literals (from RED-A)")
    print(f"live rule  : unit_answer_evidence / unit_anchors  "
          f"(units_present_in_answer has no production caller)")
    print()

    unit_hist: Counter[int] = Counter()
    total_units = 0
    n_multi = 0
    n_undisclosable_turn = 0

    n_undischargeable_units = 0
    n_undischargeable_turns = 0
    n_silent_units = 0
    n_silent_turns = 0
    n_silent_no_anchor = 0
    n_silent_rider = 0
    # The subset that can actually cost a user a dropped slot without a word said: a silently
    # discharged unit on a turn that HAS two or more units, i.e. where rows can still render.
    n_silent_units_on_renderable_turn = 0

    undischargeable_examples: list[tuple[str, str, list[str], str]] = []
    silent_examples: list[tuple[str, str, str]] = []

    for text in population:
        try:
            units = ac.demand_units(text)
            riders = set(ac.units_without_own_object(text))
            oracle = "\n".join(u.text for u in units)
            states_oracle = ac.unit_answer_evidence(text, oracle)
            states_empty = ac.unit_answer_evidence(text, "")
        except Exception:
            continue

        unit_hist[len(units)] += 1
        total_units += len(units)
        renderable = len(units) > 1
        if renderable:
            n_multi += 1
        else:
            n_undisclosable_turn += 1

        turn_undis = False
        turn_silent = False
        for u in units:
            anchors = list(ac.unit_anchors(u.text))
            rider = u.unit_id in riders

            if states_oracle.get(u.unit_id) != ac.DEMAND_SATISFIED and not rider:
                n_undischargeable_units += 1
                turn_undis = True
                if len(undischargeable_examples) < args.show:
                    undischargeable_examples.append(
                        (text, u.text, anchors, str(states_oracle.get(u.unit_id)))
                    )

            if states_empty.get(u.unit_id) == ac.DEMAND_SATISFIED or rider:
                n_silent_units += 1
                turn_silent = True
                if not anchors:
                    n_silent_no_anchor += 1
                if rider:
                    n_silent_rider += 1
                if renderable:
                    n_silent_units_on_renderable_turn += 1
                if len(silent_examples) < args.show:
                    kind = "ride_along" if rider else "no_anchors"
                    silent_examples.append((text, u.text, kind))

        if turn_undis:
            n_undischargeable_turns += 1
        if turn_silent:
            n_silent_turns += 1

    n = len(population) or 1
    print("MINTED UNITS PER TURN")
    for k in sorted(unit_hist):
        print(f"  {k:>3} unit(s) : {unit_hist[k]:>5}  ({100.0*unit_hist[k]/n:5.1f}%)")
    print(f"  total units minted over the population : {total_units}")
    print()
    print("DIRECTION 1 — OVER-RENDER (RED-G's original question, live rule)")
    print(f"  units UNDISCHARGEABLE by any answer                : {n_undischargeable_units:>5}"
          f" / {total_units}")
    print(f"  turns carrying >=1 such unit                       : {n_undischargeable_turns:>5}"
          f" / {n}  ({100.0*n_undischargeable_turns/n:5.1f}%)")
    print()
    print("DIRECTION 2 — SILENT DISCHARGE (the hazard the live rule opened)")
    print(f"  units booked SATISFIED against an EMPTY answer     : {n_silent_units:>5}"
          f" / {total_units}  ({100.0*n_silent_units/max(total_units,1):5.1f}%)")
    print(f"    of which no_anchors branch                       : {n_silent_no_anchor:>5}")
    print(f"    of which ride_along_no_object rung               : {n_silent_rider:>5}")
    print(f"  turns carrying >=1 such unit                       : {n_silent_turns:>5}"
          f" / {n}  ({100.0*n_silent_turns/n:5.1f}%)")
    print(f"  such units on turns that COULD render a row (>=2)  :"
          f" {n_silent_units_on_renderable_turn:>5}")
    print()
    print("DIRECTION 3 — DISCLOSURE SWITCHED OFF BY TURN SHAPE")
    print(f"  turns with <2 units: no row can EVER render        : {n_undisclosable_turn:>5}"
          f" / {n}  ({100.0*n_undisclosable_turn/n:5.1f}%)")
    print(f"  turns with >=2 units: disclosure is live           : {n_multi:>5}"
          f" / {n}  ({100.0*n_multi/n:5.1f}%)")
    print()

    print("EXAMPLES — UNDISCHARGEABLE (an oracle answer restating the request still fails)")
    if not undischargeable_examples:
        print("  (none)")
    for whole, unit, anchors, state in undischargeable_examples:
        print(f"  turn: {whole[:84]!r}")
        print(f"    -> unit {unit[:70]!r} anchors={anchors} oracle_state={state}")
    print()
    print("EXAMPLES — SILENT DISCHARGE (booked satisfied although nothing was served)")
    if not silent_examples:
        print("  (none)")
    for whole, unit, kind in silent_examples:
        print(f"  turn: {whole[:84]!r}")
        print(f"    -> unit {unit[:70]!r}  [{kind}]")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(
                {
                    "tree": tree,
                    "population": len(population),
                    "total_units": total_units,
                    "unit_histogram": dict(unit_hist),
                    "undischargeable_units": n_undischargeable_units,
                    "undischargeable_turns": n_undischargeable_turns,
                    "silent_discharge_units": n_silent_units,
                    "silent_discharge_no_anchor": n_silent_no_anchor,
                    "silent_discharge_ride_along": n_silent_rider,
                    "silent_discharge_turns": n_silent_turns,
                    "silent_units_on_renderable_turn": n_silent_units_on_renderable_turn,
                    "undisclosable_turns_lt2_units": n_undisclosable_turn,
                    "renderable_turns_ge2_units": n_multi,
                    "undischargeable_examples": undischargeable_examples,
                    "silent_examples": silent_examples,
                },
                fh, indent=1,
            )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
