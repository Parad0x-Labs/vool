"""RED-G — population-level blast radius of the sweep's ALWAYS-RENDER units (pass 2).

WHAT THIS ANSWERS
-----------------
RED-D proves the false-denial class exists on 23 curated strings. RED-F proves blue's mint
over-mints on 12 of RED-C's 39. Neither says how much ORDINARY traffic is affected, and that
is the number a sign-off decision turns on: after `ed027e52` removed the render gate, what
fraction of real user turns gain a "could not be answered" row for something the runtime
answered?

THE STRUCTURAL PREDICATE, AND WHY IT IS A LOWER BOUND
-----------------------------------------------------
`core/finalization.py::_rss_closure_sweep` renders a row for every demand unit that reaches
finalization unsatisfied. A unit is discharged by one of three rungs:

  1 `slice_answer_record`     -- the answering lane named its own clauses
  2 `served_content_overlap`  -- `units_present_in_answer`: ANY unit token with len >= 3 and
                                 not in `_CONTENT_STOP_TOKENS` appears in the answering body
  3 `ride_along_no_object`    -- `units_without_own_object`: the unit names nothing to look up

A unit whose ELIGIBLE token set is empty can never be discharged by rung 2 no matter what the
runtime answers, and `unit_is_disclosed_in` returns False on the same empty set, so it is
never suppressed as already-disclosed either. If it is also not a ride-along, the ONLY thing
that can save it is rung 1 -- a lane recording per-slice coverage for that exact unit.

So this instrument counts, over a real harvested population:

  ALWAYS_RENDER   units with an empty eligible-token set that are not ride-alongs
                  -> a row unless a lane records slice coverage for them. LOWER BOUND on the
                     false-denial population, because it ignores the much larger class whose
                     tokens are eligible but simply absent from a paraphrased answer
                     (measured live by RED-D: "say ok" -> "Ok." has token "say" eligible and
                     absent, and renders).

  MULTI_UNIT      turns minting more than one unit -> every unit beyond the one the answering
                  lane claims is exposed to the same reading.

POPULATION
----------
The prompt-shaped literals RED-A harvests from the tree's own test modules -- the same
population its reroute census runs on, read out of RED-A's JSON so no harvesting logic is
duplicated or allowed to drift. Not synthetic, not written by me for this measurement.

USAGE
    cd <repair worktree>
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_g_overrender_population.py --tree "$PWD" --red-a <red_a_after.json>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter


def load_population(path: str) -> list[str]:
    with open(path) as fh:
        data = json.load(fh)
    texts: list[str] = []
    seen: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key in ("text", "prompt", "literal", "flow", "value"):
                v = node.get(key)
                if isinstance(v, str) and v.strip() and v not in seen:
                    seen.add(v)
                    texts.append(v)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return texts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", default=os.getcwd())
    ap.add_argument("--red-a", required=True, help="RED-A --out JSON (the harvested population)")
    ap.add_argument("--show", type=int, default=25)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tree = os.path.realpath(args.tree)
    sys.path.insert(0, tree)
    from core.agent_runtime import answer_coverage as ac

    resolved = os.path.realpath(ac.__file__)
    if not resolved.startswith(tree):
        raise SystemExit(f"REFUSING TO RUN: answer_coverage resolved to {resolved}, outside {tree}")

    population = load_population(args.red_a)
    stop = ac._CONTENT_STOP_TOKENS

    print("=" * 94)
    print("RED-G — how much ordinary traffic gains a row it did not earn")
    print("=" * 94)
    print(f"tree       : {tree}")
    print(f"module     : {resolved}")
    print(f"population : {len(population)} distinct prompt-shaped literals (from RED-A)")
    print()

    n_multi = 0
    n_always = 0
    always_examples: list[tuple[str, str]] = []
    unit_hist = Counter()
    total_units = 0
    total_always_units = 0

    for text in population:
        try:
            units = ac.demand_units(text)
            riders = set(ac.units_without_own_object(text))
        except Exception:
            continue
        unit_hist[len(units)] += 1
        total_units += len(units)
        if len(units) > 1:
            n_multi += 1
        hit = False
        for u in units:
            eligible = [t for t in ac._unit_tokens(u.text) if len(t) >= 3 and t not in stop]
            if not eligible and u.unit_id not in riders:
                hit = True
                total_always_units += 1
                if len(always_examples) < args.show:
                    always_examples.append((text, u.text))
        if hit:
            n_always += 1

    n = len(population) or 1
    print("MINTED UNITS PER TURN")
    for k in sorted(unit_hist):
        print(f"  {k:>3} unit(s) : {unit_hist[k]:>5}  ({100.0*unit_hist[k]/n:5.1f}%)")
    print(f"  total units minted over the population : {total_units}")
    print()
    print("BLAST RADIUS")
    print(f"  turns minting MORE THAN ONE unit                  : {n_multi:>5} / {n}"
          f"  ({100.0*n_multi/n:5.1f}%)")
    print(f"  turns carrying >=1 ALWAYS-RENDER unit (lower bound): {n_always:>5} / {n}"
          f"  ({100.0*n_always/n:5.1f}%)")
    print(f"  ALWAYS-RENDER units in total                      : {total_always_units}")
    print()
    print("EXAMPLES — a unit here renders a 'could not be answered' row unless a lane records")
    print("per-slice coverage for it, however well the runtime answers the turn:")
    for whole, unit in always_examples:
        print(f"  turn: {whole[:88]!r}")
        print(f"    -> ALWAYS-RENDER unit: {unit[:88]!r}")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(
                {
                    "tree": tree, "population": len(population),
                    "multi_unit_turns": n_multi, "always_render_turns": n_always,
                    "always_render_units": total_always_units,
                    "unit_histogram": dict(unit_hist),
                    "examples": always_examples,
                }, fh, indent=1,
            )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
