"""Differential grader: did TYPING NOISE break a prompt that works when typed cleanly?

Every case carries `base` (which verified-working prompt it came from) and `mut` (what was done to
it). The clean baseline for each base is measured in the same run, so this compares like with like
on the same daemon at the same moment -- no stored expectations to go stale.

A mutation FAILS when the clean form was served and the noisy form was not. "Served" is judged
structurally, never by prose: the same lane claimed it, or the answer still carries the evidence the
clean one carried (a source link, a figure). This matters because the operator's real failures all
looked like this -- `wheather` fell out of the weather lane onto a model, and the answer became an
apology instead of a temperature.

usage: python grade_noise.py run-<name>/set_noise.json
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict

SERVED_MARKERS = ("source:", "](http", "°c", " c (", "usd", "eur")


def served(record: dict) -> bool:
    """Structural: did the turn actually produce the goods, rather than an apology?"""
    answer = " ".join(str(record.get("answer") or "").split())
    if record.get("error") or not answer:
        return False
    low = answer.lower()
    for bad in ("couldn't map", "could not be answered", "no current weather results",
                "unable to fetch", "i couldn't produce", "did not contain the requested"):
        if bad in low:
            return False
    if any(m in low for m in SERVED_MARKERS):
        return True
    # a deterministic string/arithmetic answer has no source line; accept a short exact-ish reply
    return len(answer) <= 120 and bool(re.search(r"[A-Za-z0-9]", answer))


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as handle:
        records = json.load(handle)
    by_base: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in records:
        by_base[r.get("base", "?")][r.get("mut", "?")] = r

    broke: list[tuple[str, str, str, str]] = []
    survived = 0
    no_baseline: list[str] = []

    for base, muts in sorted(by_base.items()):
        clean = muts.get("clean")
        if clean is None:
            no_baseline.append(base)
            continue
        if not served(clean):
            print(f"  [skip] base {base}: the CLEAN form did not serve either -- not a noise finding")
            continue
        for mut, rec in sorted(muts.items()):
            if mut == "clean":
                continue
            if served(rec):
                survived += 1
            else:
                broke.append((base, mut, str(rec.get("route")), " ".join(str(rec.get("answer") or "").split())[:70]))

    print(f"\n=== NOISE BROKE {len(broke)} of {len(broke)+survived} mutations ===")
    for base, mut, route, ans in broke:
        print(f"  {base}/{mut:12} route={route!s:26} {ans!r}")

    print("\n=== by mutation (how fragile is each typing habit) ===")
    counts: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    for muts in by_base.values():
        clean = muts.get("clean")
        if clean is None or not served(clean):
            continue
        for mut, rec in muts.items():
            if mut == "clean":
                continue
            totals[mut] += 1
            if not served(rec):
                counts[mut] += 1
    for mut in sorted(totals, key=lambda m: -counts[m]):
        flag = "  <== FRAGILE" if counts[mut] else ""
        print(f"  {mut:12} broke {counts[mut]}/{totals[mut]}{flag}")

    if no_baseline:
        print(f"\n  no clean baseline for: {no_baseline}")


if __name__ == "__main__":
    main()
