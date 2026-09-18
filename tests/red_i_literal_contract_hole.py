"""RED-I — is the literal-output suppression a silent-drop hole? A deterministic, in-process
A/B against the REAL closure sweep and the REAL obligation ledger. No model, no HTTP, no mocks.

WHY IN-PROCESS RATHER THAN OVER HTTP
------------------------------------
Over HTTP the answer body is model-authored, so a turn that drops a slot and a turn that answers
it are not distinguishable by construction — the arm can fail for the wrong reason. Here the body
is fixed by the probe: a body that answers ONE of two demanded slots and says nothing about the
other. The only variable between the two arms is whether the request carries a literal-output
clause. Everything else — the mint, the demand decomposition, the evidence ladder, the sweep, the
census — is the runtime's own code, called directly.

THE MATCHED PAIR
----------------
    request A:  "<two slots>"                             -> not under a literal contract
    request B:  "<two slots> Answer with JSON only."      -> under a literal contract
    body (both): answers slot 1, silent on slot 2

If A renders a disclosure row for slot 2 and B does not, then the BLUE-1 pass-3 suppression at
`core/finalization.py:292` re-opens the original silent-drop defect for every request that carries
an output-shape clause.

A THIRD ARM, BECAUSE THE INSTRUMENT MUST NOT PASS A RUNTIME THAT DROPS SILENTLY
------------------------------------------------------------------------------
Arm C uses a body that REFUSES slot 2 in prose ("... is not available") while echoing the slot's
own anchor words. If the evidence ladder grades that `satisfied`, the runtime discharges a slot on
the strength of a sentence declining it — a hole that needs no literal contract at all. Reported
separately, because it changes what arm B means.

USAGE
-----
    cd /Users/example-user/vool/worktrees/rss-repair-20260829
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_i_literal_contract_hole.py
"""

from __future__ import annotations

import json
import uuid
from typing import Any

SLOT_1 = "What is 1000 EUR to RUB"
SLOT_2 = "and what is the water temperature in the Baltic Sea?"
PLAIN = f"{SLOT_1}, {SLOT_2}"
LITERAL = f"{PLAIN} Answer with JSON only."

#: Answers slot 1 with a real-looking sourced line; says NOTHING about slot 2. This is the
#: silent drop the whole audit exists to make impossible.
BODY_DROPS_SLOT_2 = (
    "1000 EUR (euro) x 99.8 = 99800.0 RUB (Russian ruble), using a live rate as of "
    "2026-08-29T00:00:00+00:00."
)

#: Answers slot 1, and DECLINES slot 2 in prose while naming its anchor words.
BODY_REFUSES_SLOT_2_IN_PROSE = (
    BODY_DROPS_SLOT_2
    + "\nThe water temperature in the Baltic Sea is not available."
)


def mint(request: str) -> tuple[str, str]:
    """Mint a demand set exactly the way `apps/vool_agent.py:616` mints one at intake."""
    from core.agent_runtime.answer_coverage import demand_units
    from core.conductor import obligation_ledger

    attempt_id = f"attempt-{uuid.uuid4().hex}"
    units = demand_units(request)
    obset = obligation_ledger.open_obligation_set(
        request_text=request,
        request_id=f"req:probe:{uuid.uuid4().hex[:16]}",
        obligations=[
            {"obligation_id": f"ob:{attempt_id}:answer", "text": request[:240], "kind": "prose"},
            *(
                {
                    "obligation_id": f"ob:{attempt_id}:demand:{u.unit_id}",
                    "text": u.text,
                    "kind": "demand",
                    "unit_id": u.unit_id,
                    "slice_id": u.slice_id,
                }
                for u in units
            ),
        ],
    )
    return obset["set_id"], obset["version"]


def run_arm(label: str, request: str, body: str) -> dict[str, Any]:
    """Drive the REAL sweep over a freshly minted set. Returns what the user would have seen."""
    from core.conductor import obligation_ledger
    from core.finalization import _rss_closure_sweep, _under_literal_output_contract

    set_id, version = mint(request)
    closure = {"set_id": set_id, "set_version": version}
    obligation_ledger.bind_active_set(set_id, version)
    try:
        served, census = _rss_closure_sweep(body, closure)
    finally:
        obligation_ledger.clear_active_set()

    states = {
        str(row.get("unit_id") or row.get("obligation_id")): str(row.get("state") or "")
        for row in obligation_ledger.demand_obligations(set_id, version)
    }
    appended = served[len(body) :] if served.startswith(body) else "<REWRITTEN>"
    return {
        "arm": label,
        "request": request,
        "under_literal_contract": _under_literal_output_contract(request),
        "served_bytes_equal_input": served == body,
        "appended_to_body": appended.strip(),
        "ledger_states": states,
        "census": census,
    }


def main() -> int:
    from core.agent_runtime.answer_coverage import unit_answer_evidence

    print("=" * 78)
    print("ANOMALY 2 — matched pair: same body, same slots, contract clause is the only variable")
    print("=" * 78)

    arms = [
        run_arm("A / no contract   ", PLAIN, BODY_DROPS_SLOT_2),
        run_arm("B / JSON only     ", LITERAL, BODY_DROPS_SLOT_2),
        run_arm("C / prose refusal ", PLAIN, BODY_REFUSES_SLOT_2_IN_PROSE),
    ]
    for arm in arms:
        print(f"\n--- {arm['arm']} ---")
        print(f"  under_literal_contract : {arm['under_literal_contract']}")
        print(f"  ledger states          : {json.dumps(arm['ledger_states'])}")
        print(f"  census                 : {json.dumps(arm['census'])}")
        print(f"  served == body         : {arm['served_bytes_equal_input']}")
        print(f"  appended to body       : {arm['appended_to_body'] or '<NOTHING>'}")

    a, b, c = arms
    print("\n" + "=" * 78)
    print("READING")
    print("=" * 78)
    hole = (not a["served_bytes_equal_input"]) and b["served_bytes_equal_input"]
    print(f"  A disclosed the dropped slot            : {not a['served_bytes_equal_input']}")
    print(f"  B (literal contract) disclosed nothing  : {b['served_bytes_equal_input']}")
    print(f"  => literal-contract silent drop         : {hole}")
    print(f"  B withheld rows count                   : {b['census'].get('demand_render_withheld')}")

    print("\n  arm C — does a prose REFUSAL discharge its own slot?")
    print(f"    ledger states : {json.dumps(c['ledger_states'])}")
    print(f"    census        : {json.dumps(c['census'])}")
    ladder = unit_answer_evidence(PLAIN, BODY_REFUSES_SLOT_2_IN_PROSE)
    print(f"    evidence ladder verdicts, read directly : {json.dumps(ladder)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
