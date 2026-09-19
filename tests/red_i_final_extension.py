#!/usr/bin/env python3
"""RED-FINAL extension — attacks aimed at the repair mechanism AS IT NOW STANDS (2b2f9e51).

The corpus (RED-C) was written before the mechanism existed, so it attacks the DEFECT.
This file attacks the FIX: its three layers, each on its own terms.

  A. STRUCTURAL SEGMENTATION / MINT — can a slot fail to be minted at all?
     A slot that never mints cannot be disclosed, counted, rendered or certified against.
     This is a silent drop UNDERNEATH the whole accounting layer.
  B. THE EVIDENCE LADDER — can a slot the runtime never answered be marked `satisfied`?
     `unit_answer_evidence` grants SATISFIED on (i) an empty anchor set and (ii) every
     anchor present, where "present" is lexical and slot-blind.
  C. THE LITERAL-OUTPUT CONTRACT — can it swallow a GENUINE unanswered slot?
     `_rss_closure_sweep` rewrites every non-satisfied verdict to `indeterminate` and sets
     `demand_rendered = 0` when `parse_raw_output_contract(request)` is not None.

For every probe this records, side by side:
  * the daemon's OWN certificate (`closure_verdict` off `/api/chat`);
  * the ladder RECOMPUTED IN-PROCESS against the very bytes the daemon served.
Two independent readings of one turn. Where they disagree the disagreement is the finding,
and this file reports both rather than picking one.

READ-ONLY against the daemon. No restart, no kill. Probe ids carry `audit-final-corpus-`.

USAGE
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_i_final_extension.py --base-url http://127.0.0.1:11437 \
        --out <scratch>/red_i_extension.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PREFIX = "audit-final-corpus-ext-"

CANON = (
    "What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in "
    "Rome, and what is the water temperature in the Baltic Sea?"
)

#: (probe_id, layer, prompt, what a correct runtime must do)
PROBES: tuple[tuple[str, str, str, str], ...] = (
    # ---- A: mint-layer under-mint. The ask is never turned into an obligation. -------
    ("A1_dedup_max_min", "MINT",
     "what is the max temp in Rome, and what is the min temp in Rome",
     "TWO obligations. `max`/`min` are 3 chars so both units yield anchors ('temp','rome') "
     "and the mint's anchor-equality dedup collapses them into one."),
    ("A2_dedup_arith", "MINT",
     "what is 12 x 7, and what is 12 + 7",
     "TWO obligations (84 and 19). Both units anchor to ('12','7') and dedup to one."),
    ("A3_prohibition_opener_swallows_ask", "MINT",
     "Do not forget the gold price, and give me 1000 EUR to RUB",
     "TWO obligations. `Do not forget X` is an ordinary way of ASKING for X, but the "
     "prohibition-opener filter drops the whole unit before it is minted."),

    # ---- B: ladder. The slot mints, is never answered, and is marked satisfied. ------
    ("B1_empty_anchor_slot", "LADDER",
     "what is 1000 EUR to RUB, and how hot is the sun",
     "If the sun slot is not answered it must be disclosed. `unit_anchors('and how hot is "
     "the sun') == ()` and the ladder marks a no-anchor unit SATISFIED unconditionally."),
    ("B2_empty_anchor_slot_2", "LADDER",
     "1500 eur to usd, and who won the war",
     "Same hole, different words: every token is <4 chars, so the anchor set is empty."),
    ("B3_anchor_subset_collision", "LADDER",
     "convert 1000 EUR to USD, and what is the USD to EUR rate",
     "The reverse-rate ask anchors to ('usd','eur','rate'), a SUBSET of the forward "
     "conversion's own answer tokens, so answering one discharges both."),

    # ---- C: the literal-output contract, over a turn that genuinely drops slots. -----
    ("C0_canonical_control", "LITERAL-CONTROL", CANON,
     "CONTROL for C1/C2: the same four slots with no formatting tail. Whatever this turn "
     "drops, it should DISCLOSE."),
    ("C1_canonical_just_numbers", "LITERAL",
     CANON + " Just the numbers.",
     "Same four slots. `parse_raw_output_contract` binds on the tail, so every pending "
     "row is withheld and every unanswered verdict is rewritten to `indeterminate` -- "
     "`demand_unanswered` reads 0 on a turn that dropped slots."),
    ("C2_canonical_plain_text_only", "LITERAL",
     CANON + " Plain text only.",
     "Same suppression through a different, equally ordinary formatting preference."),

    # ---- D: spurious rows on a single-domain turn. ----------------------------------
    ("D1_pure_audit", "SPURIOUS",
     "Audit this project. Find the security vulnerabilities. Give me a verdict.",
     "ONE ask. The suite's own sharpest control mints 3 units; any rendered row here is "
     "an obligation the user never created."),
    ("D2_washington_dc", "SPURIOUS",
     "I have 1,000 units of local currency in Washington, D. C. and buy a coffee for 5 "
     "units of local currency. Name the currency.",
     "ONE ask. `D. C.` splits at the comma-free period so `C.` becomes its own unit "
     "(RED-1 NEW-6). No row may name `C.`"),
    ("D3_prohibition_row", "SPURIOUS",
     "Do not send any money. Just tell me the EUR/USD rate.",
     "ONE ask. RED-1 NEW-3: the prohibition must not be minted or reported unmet."),
    ("D4_header_in_prose", "SPURIOUS",
     'What does the phrase "Could not be answered:" mean in a status report?',
     "ONE ask, answered. RED-1 NEW-5: `answering_body` cuts at the header the model's own "
     "correct answer quotes, so the answer is cut to nothing and denied."),

    # ---- E: a single-unit turn that genuinely cannot be served. ---------------------
    ("E1_impossible_single", "SINGLE",
     "what is the water temperature in the Baltic Sea in the year 3000?",
     "Ungroundable. It must be NAMED unavailable with a reason. The sweep's `len(states) "
     "< 2` rule rewrites every single-unit turn's verdict to `indeterminate`, so this "
     "sweep can never render it -- only the lane's own refusal text can."),
    ("E2_no_family_single", "SINGLE",
     "what is the airspeed velocity of an unladen swallow in knots?",
     "Same shape, no registered family."),
)


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:11437")
    ap.add_argument("--tree", default=os.getcwd())
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tree = os.path.realpath(args.tree)
    sys.path.insert(0, tree)
    import core

    resolved = os.path.realpath(core.__file__)
    if not resolved.startswith(tree + os.sep):
        raise SystemExit(f"REFUSING: asked for {tree}, core resolved to {resolved}")

    from core.agent_runtime.answer_coverage import (
        answering_body,
        demand_units,
        unit_anchors,
        unit_answer_evidence,
    )
    from core.raw_output_contract import parse_raw_output_contract

    head = subprocess.run(["git", "-C", tree, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False).stdout.strip()
    dirty = subprocess.run(["git", "-C", tree, "status", "--porcelain"],
                           capture_output=True, text=True, check=False).stdout.strip()

    print("=" * 78)
    print("RED-FINAL EXTENSION — attacking the repair mechanism itself")
    print("=" * 78)
    print(f"tree          : {tree}")
    print(f"HEAD          : {head}")
    print(f"dirty paths   : {len(dirty.splitlines())} ({', '.join(p[3:] for p in dirty.splitlines()) or 'none'})")
    print(f"core resolved : {resolved}")
    print(f"daemon        : {args.base_url}")
    print(f"probe ids     : {PREFIX}*")
    print()

    keys = {k.strip() for k in args.only.split(",") if k.strip()}
    stamp = time.strftime("%H%M%S")
    rows = []
    for probe_id, layer, prompt, expectation in PROBES:
        if keys and probe_id not in keys:
            continue
        cid = f"{PREFIX}{probe_id}-{stamp}"
        t0 = time.time()
        try:
            raw = _post(f"{args.base_url}/api/chat",
                        {"model": "vool", "chat_id": cid,
                         "messages": [{"role": "user", "content": prompt}],
                         "stream": False}, args.timeout)
            err = ""
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raw, err = {}, f"{type(exc).__name__}: {exc}"
        latency = round(time.time() - t0, 2)
        msg = raw.get("message") if isinstance(raw.get("message"), dict) else {}
        content = str((msg or {}).get("content") or "")
        commit = raw.get("vool_response_commit")
        cert = (commit or {}).get("closure_verdict") if isinstance(commit, dict) else {}

        units = demand_units(prompt)
        row = {
            "probe_id": probe_id, "layer": layer, "prompt": prompt,
            "expectation": expectation, "chat_id": cid, "latency_s": latency,
            "transport_error": err, "served": content,
            "certificate": cert if isinstance(cert, dict) else {},
            "route_reason": (raw.get("vool_response_commit") or {}).get("route_reason")
            if isinstance(raw.get("vool_response_commit"), dict) else None,
            "in_process": {
                "n_units": len(units),
                "units": [[u.unit_id, u.text, list(unit_anchors(u.text))] for u in units],
                "raw_output_contract_binds": parse_raw_output_contract(prompt) is not None,
                # THE CROSS-CHECK: the ladder re-run against the bytes the daemon served.
                "ladder_on_served_bytes": unit_answer_evidence(prompt, content),
                "answering_body": answering_body(content),
            },
        }
        rows.append(row)
        cert_s = json.dumps(row["certificate"], sort_keys=True)
        print(f"--- {probe_id} [{layer}]  ({latency}s)")
        print(f"    PROMPT : {prompt}")
        print(f"    UNITS  : {len(units)} {[u.text for u in units]}")
        print(f"    RAWOUT : {row['in_process']['raw_output_contract_binds']}")
        print(f"    SERVED : {content!r}")
        print(f"    CERT   : {cert_s}")
        print(f"    LADDER(recomputed on served bytes): "
              f"{json.dumps(row['in_process']['ladder_on_served_bytes'], sort_keys=True)}")
        if err:
            print(f"    ERROR  : {err}")
        print()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"instrument": "RED-FINAL-EXT", "tree": tree, "head": head,
                       "core_file": resolved, "base_url": args.base_url,
                       "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "rows": rows}, fh, indent=1)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
