"""RED-F — blue's REAL mint, measured against RED-C's frozen 39-entry annotated corpus.

WHY THIS IS A SEPARATE FILE
---------------------------
RED-C's `--mint auto` searches for a real minting function by eleven candidate
module/attribute pairs written in pass 1, before blue's mint existed. Blue shipped
`core.agent_runtime.answer_coverage.demand_units`, which is not among them, so the pass-2
re-run of RED-C reports `real mint fn : NONE FOUND` and falls back to E012's Rule A/Rule B.
My pass-1 attack plan (A1) said I would add the pair and re-run.

Editing RED-C would change its SHA-256 and break the byte-identity of the frozen instrument
whose served baseline is the mandated before/after comparison. So the corpus is IMPORTED
here instead and blue's real mint is measured against the same annotations, with RED-C left
untouched. RED-C keeps measuring the served behaviour; RED-F measures the mint.

CLASSIFICATION (E012's three falsification directions, unchanged from RED-C)
---------------------------------------------------------------------------
    OK                 minted cardinality == annotation, and every must-mint fragment is
                       present in some minted unit
    OVER_MINT          minted > annotation  (spurious rows -- the fix's new failure direction)
    UNDER_MINT         0 < minted < annotation  (slots that can still be dropped silently)
    UNDER_MINT_TO_ZERO minted == 0  (the whole request is invisible to the accounting)
    WRONG_SLOT         right count, wrong clause -- a mint that hits the number by luck

`expected` is RED-C's human annotation and is not restated or adjusted here. Rows where the
new mint disagrees with the annotation are printed in full so the disagreement can be judged
rather than trusted.

USAGE
    cd <repair worktree>
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_f_mint_classification.py --tree "$PWD" --out red_f.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from typing import Any

#: Blue's real mint, found by reading `core/finalization.py::_rss_closure_sweep` and
#: `core/agent_runtime/answer_coverage.py` at HEAD ed027e52.
REAL_MINT = ("core.agent_runtime.answer_coverage", "demand_units")


def classify(minted: int, expected: int, fragments_ok: bool) -> str:
    if minted == 0 and expected > 0:
        return "UNDER_MINT_TO_ZERO"
    if minted == expected:
        return "OK" if fragments_ok else "WRONG_SLOT"
    return "OVER_MINT" if minted > expected else "UNDER_MINT"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", default=os.getcwd())
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tree = os.path.realpath(args.tree)
    sys.path.insert(0, tree)
    sys.path.insert(0, os.path.join(tree, "tests"))

    module = importlib.import_module(REAL_MINT[0])
    resolved = os.path.realpath(module.__file__)
    if not resolved.startswith(tree):
        raise SystemExit(
            f"REFUSING TO RUN: {REAL_MINT[0]} resolved to {resolved}, outside {tree}."
        )
    mint = getattr(module, REAL_MINT[1])

    red_c = importlib.import_module("red_c_adversarial_corpus")
    corpus = red_c.CORPUS

    print("=" * 92)
    print("RED-F — blue's real mint against RED-C's frozen annotated corpus")
    print("=" * 92)
    print(f"tree      : {tree}")
    print(f"mint fn   : {REAL_MINT[0]}.{REAL_MINT[1]}  ->  {resolved}")
    print(f"corpus    : {len(corpus)} entries (imported from tests/red_c_adversarial_corpus.py)")
    print()

    rows: list[dict[str, Any]] = []
    tally: dict[str, int] = {}
    for entry in corpus:
        units = mint(entry.text)
        texts = [u.text for u in units]
        fragments_ok = all(
            any(frag.lower() in t.lower() for t in texts)
            for frag in (entry.must_mint_fragments or ())
        )
        verdict = classify(len(units), entry.expected, fragments_ok)
        tally[verdict] = tally.get(verdict, 0) + 1
        rows.append(
            {
                "key": entry.key,
                "text": entry.text,
                "expected": entry.expected,
                "minted": len(units),
                "unit_texts": texts,
                "must_mint_fragments": list(entry.must_mint_fragments or ()),
                "fragments_ok": fragments_ok,
                "verdict": verdict,
                "origin": entry.origin,
                "aim": entry.aim,
            }
        )

    print(f"{'key':38} {'exp':>3} {'mint':>4}  verdict")
    print("-" * 92)
    for r in rows:
        flag = "" if r["verdict"] == "OK" else "  <--"
        print(f"{r['key']:38} {r['expected']:>3} {r['minted']:>4}  {r['verdict']}{flag}")

    print("\n" + "=" * 92)
    print("CLASSIFICATION (blue's real mint, HEAD ed027e52)")
    for k in sorted(tally):
        print(f"  {k:22} {tally[k]:>3} / {len(corpus)}")
    ok = tally.get("OK", 0)
    print(f"\n  OK rate: {ok}/{len(corpus)} = {100.0 * ok / len(corpus):.1f}%")

    print("\n" + "=" * 92)
    print("EVERY DISAGREEMENT, IN FULL — judge these, do not trust the tally")
    print("=" * 92)
    for r in rows:
        if r["verdict"] == "OK":
            continue
        print(f"\n[{r['verdict']}] {r['key']}   expected={r['expected']} minted={r['minted']}")
        print(f"   origin: {r['origin']}")
        print(f"   aim   : {r['aim']}")
        print(f"   text  : {r['text'][:200]}")
        for t in r["unit_texts"]:
            print(f"     unit: {t[:150]!r}")
        if r["must_mint_fragments"] and not r["fragments_ok"]:
            print(f"   MISSING FRAGMENTS: {r['must_mint_fragments']}")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"tree": tree, "mint": list(REAL_MINT), "tally": tally, "rows": rows},
                      fh, indent=1, default=str)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
