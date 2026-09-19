#!/usr/bin/env python3
"""RED-FINAL — does a lane's consumption RECEIPT outrank the evidence read off the bytes?

`unit_answer_evidence` is the rung the repair added so the FINAL served bytes decide whether
a slot was answered. But `sweep_demand_obligations` consults it only as a FALLBACK:

    if unit_id in consumed:
        state = "satisfied"
    else:
        state = supplied.get(unit_id) or "unanswered"      # <- the evidence ladder

So a receipt written by the answering lane BEFORE the turn's outcome was known wins over the
evidence of the outcome. This file proves the precedence directly against the real ledger.

Measured live at 2b2f9e51 (probe D3, `audit-final-corpus-ext-D3_prohibition_row-*`): the
served bytes were `qwen2.5:7b failed before producing a usable answer... Retry the turn.` and
the certificate came back `covered: true, demand_satisfied: 1, demand_unanswered: 0`. The
ladder re-run on those exact bytes says `unanswered`, and both receipt-writing readings that
run inside the sweep (`units_without_own_object`, `units_present_in_answer`) return empty --
so the satisfaction can only have come from a receipt written earlier in the turn. This file
turns that inference into a mechanism proof.

SAFETY: writes to the ledger under `VOOL_HOME`, so point it at a scratch directory. It
touches no daemon and no live store.

USAGE
    VOOL_HOME=<scratch>/nullahome PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 \
        .venv/bin/python tests/red_k_receipt_precedence.py
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    from storage.db import active_default_db_path

    db = active_default_db_path()
    home = os.environ.get("VOOL_HOME", "")
    print(f"VOOL_HOME : {home or '(unset)'}")
    print(f"ledger db  : {db}")
    if not home:
        print("REFUSING: set VOOL_HOME to a scratch dir; this writes ledger rows.",
              file=sys.stderr)
        return 2

    from storage.db import init_schema
    from storage.migrations import run_migrations

    init_schema(db)          # scratch db, created empty by the redirect above
    run_migrations(db)       # obligation_sets lives in the migration set

    from core.agent_runtime.answer_coverage import demand_units, unit_answer_evidence
    from core.conductor import obligation_ledger as ledger

    request = "Just tell me the EUR/USD rate."
    failed_bytes = (
        "`qwen2.5:7b` failed before producing a usable answer. "
        "No cached or remembered text was substituted. Retry the turn."
    )
    units = demand_units(request)
    print(f"\nrequest    : {request}")
    print(f"served     : {failed_bytes}")
    print(f"units      : {[(u.unit_id, u.text) for u in units]}")

    ladder = unit_answer_evidence(request, failed_bytes)
    print(f"\nEVIDENCE LADDER on the served bytes : {ladder}")

    obset = ledger.open_obligation_set(
        obligations=[{"obligation_id": f"demand:{u.unit_id}", "kind": "demand",
                      "unit_id": u.unit_id, "text": u.text, "state": "open"}
                     for u in units],
        request_text=request,
        request_id="red-final-receipt-precedence",
    )
    sid, ver = obset["set_id"], obset["version"]

    # ARM 1 -- no receipt. The ladder must decide, and it says the slot was not answered.
    rows = ledger.sweep_demand_obligations(sid, ver, states=dict(ladder))
    census_no_receipt = dict(ledger.demand_census(sid, ver))
    print(f"\nARM 1  sweep WITHOUT a receipt : "
          f"{[(r['unit_id'], r['state']) for r in rows]}")
    print(f"       census                  : {census_no_receipt}")

    # ARM 2 -- identical bytes, identical ladder verdict, but a lane wrote a receipt first.
    obset2 = ledger.open_obligation_set(
        obligations=[{"obligation_id": f"demand:{u.unit_id}", "kind": "demand",
                      "unit_id": u.unit_id, "text": u.text, "state": "open"}
                     for u in units],
        request_text=request,
        request_id="red-final-receipt-precedence-2",
    )
    sid2, ver2 = obset2["set_id"], obset2["version"]
    for u in units:
        ledger.record_slice_consumption(sid2, ver2, unit_id=u.unit_id,
                                   family="currency", evidence="slice_coverage")
    rows2 = ledger.sweep_demand_obligations(sid2, ver2, states=dict(ladder))
    census_receipt = dict(ledger.demand_census(sid2, ver2))
    print(f"\nARM 2  sweep WITH a lane receipt : "
          f"{[(r['unit_id'], r['state']) for r in rows2]}")
    print(f"       census                    : {census_receipt}")

    print("\n" + "=" * 74)
    same_bytes = "IDENTICAL bytes, IDENTICAL ladder verdict"
    flipped = census_no_receipt.get("demand_unanswered") and census_receipt.get(
        "demand_satisfied"
    )
    print(f"{same_bytes}; the only difference is the receipt.")
    print(f"  without receipt -> demand_unanswered = "
          f"{census_no_receipt.get('demand_unanswered')}")
    print(f"  with receipt    -> demand_satisfied  = "
          f"{census_receipt.get('demand_satisfied')}, demand_unanswered = "
          f"{census_receipt.get('demand_unanswered')}")
    print(f"\nRECEIPT OUTRANKS THE EVIDENCE LADDER: {bool(flipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
