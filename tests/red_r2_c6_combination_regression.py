#!/usr/bin/env python3
"""RED-2 — corrected C6: a slot answerable ALONE must not degrade in COMBINATION.

WHY THIS FILE EXISTS
--------------------
`red_b_served_gauntlet.py` C6 ("one slot failing does not stop the others") computes its
regression set as::

    regressed = [sid for sid, ok in solo_ok.items()
                 if ok and states.get(sid) == "SILENTLY_DROPPED"]

Only `SILENTLY_DROPPED` counts.  A slot that the runtime answers when asked ALONE, but
reports as `NAMED_FAILED` inside the multi-slot turn, is therefore scored PASS.

That is the exact regression C6 exists to detect.  Measured at 2b2f9e51 on `/api/chat`,
operator-evening prompt: singleton `water` = ANSWERED (59.9s, a real reading), combined
`water` = NAMED_FAILED ("no answering lane claimed this part of the request").  Stock C6
reported `lost in the 4-slot turn=[]` and PASS.

This instrument recomputes C6 over a saved red-B JSON under the corrected rule:

    a slot is REGRESSED-IN-COMBINATION when solo state == ANSWERED
    and combined state != ANSWERED  (SILENTLY_DROPPED *or* NAMED_FAILED)

NAMED_FAILED is strictly better USER-FACING behaviour than a silent drop — it is disclosed
— but it is not an answer, and C6 asks about capability loss caused by combination, not
about disclosure quality.  Disclosure is already scored by C5/C7.  Keeping the two apart
is the whole point: otherwise a runtime can convert every combination regression into a
polite failure row and score PASS on both criteria.

This file does NOT modify red_b_served_gauntlet.py.  It reads its JSON output.

USAGE
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
        tests/red_r2_c6_combination_regression.py --selftest
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
        tests/red_r2_c6_combination_regression.py --in <red_b_out.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

PASS, FAIL, INCONC = "PASS", "FAIL", "INCONCLUSIVE"


def corrected_c6(solo: dict[str, str], combined: dict[str, str]) -> tuple[str, list[str], str]:
    """Return (status, regressed_slot_ids, evidence).

    A slot counts as regressed when it is ANSWERED alone and NOT ANSWERED in combination.
    """
    if not solo:
        return INCONC, [], "singleton controls not run"
    answerable = sorted(sid for sid, st in solo.items() if st == "ANSWERED")
    if not answerable:
        return INCONC, [], "no slot was answerable alone; nothing to regress"
    regressed = sorted(
        sid for sid in answerable if combined.get(sid) != "ANSWERED"
    )
    detail = ", ".join(
        f"{sid}: solo=ANSWERED -> combined={combined.get(sid)}" for sid in regressed
    )
    ev = f"answerable alone={answerable}; regressed in combination={regressed}"
    if detail:
        ev += f" ({detail})"
    return (FAIL if regressed else PASS), regressed, ev


def _run(path: str) -> int:
    with open(path) as fh:
        blob: dict[str, Any] = json.load(fh)
    bad = 0
    for run in blob.get("runs", []):
        solo = run.get("singleton_states") or {}
        combined = run.get("slot_states") or {}
        status, regressed, ev = corrected_c6(solo, combined)
        stock = next(
            (c["status"] for c in run.get("checks", []) if c["cid"] == "C6"), "?"
        )
        flag = "  <-- DISAGREES WITH STOCK C6" if status != stock else ""
        print(f"{run.get('entrance')} :: {run.get('tag')}")
        print(f"   stock C6     : {stock}")
        print(f"   corrected C6 : {status}{flag}")
        print(f"   evidence     : {ev}")
        if status == FAIL:
            bad += 1
    print(f"\nruns with a combination regression: {bad}")
    return 0


def selftest() -> int:
    """The instrument must catch the NAMED_FAILED regression stock C6 misses, and must
    not manufacture a regression where none exists."""
    failures = 0

    cases: list[tuple[str, dict[str, str], dict[str, str], str, list[str]]] = [
        (
            "solo ANSWERED -> combined NAMED_FAILED must FAIL (the stock-C6 blind spot)",
            {"fx": "ANSWERED", "water": "ANSWERED", "gold": "SILENTLY_DROPPED"},
            {"fx": "ANSWERED", "water": "NAMED_FAILED", "gold": "NAMED_FAILED"},
            FAIL,
            ["water"],
        ),
        (
            "solo ANSWERED -> combined SILENTLY_DROPPED must FAIL (stock C6 agrees)",
            {"fx": "ANSWERED", "water": "ANSWERED"},
            {"fx": "ANSWERED", "water": "SILENTLY_DROPPED"},
            FAIL,
            ["water"],
        ),
        (
            "everything answerable alone is answered in combination -> PASS",
            {"fx": "ANSWERED", "water": "ANSWERED"},
            {"fx": "ANSWERED", "water": "ANSWERED"},
            PASS,
            [],
        ),
        (
            "a slot that fails ALONE is a model/tool limit, not a combination regression",
            {"fx": "ANSWERED", "gold": "SILENTLY_DROPPED"},
            {"fx": "ANSWERED", "gold": "NAMED_FAILED"},
            PASS,
            [],
        ),
        (
            "no singletons -> INCONCLUSIVE, never a silent PASS",
            {},
            {"fx": "NAMED_FAILED"},
            INCONC,
            [],
        ),
    ]

    for name, solo, combined, want_status, want_regressed in cases:
        got_status, got_regressed, ev = corrected_c6(solo, combined)
        ok = got_status == want_status and got_regressed == want_regressed
        print(f"[{'ok ' if ok else 'BAD'}] {name}")
        print(f"        -> {got_status} {got_regressed} :: {ev}")
        if not ok:
            failures += 1
            print(f"        EXPECTED {want_status} {want_regressed}")

    print(f"\nSELFTEST RESULT: {'OK' if not failures else f'{failures} BROKEN'}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--in", dest="path", default="")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.path:
        ap.error("--in <red_b output json> or --selftest")
    return _run(args.path)


if __name__ == "__main__":
    sys.exit(main())
