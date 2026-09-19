"""RED-I — the user-visible price of the conservative coverage rule.

THE TRADE BEING QUANTIFIED
--------------------------
`unit_answer_evidence` (installed by `2a3986d2`) requires EVERY anchor of a demand unit to
appear in the served body before it books `satisfied`. A partial match is
`DEMAND_INDETERMINATE`: the runtime makes NO claim, renders NO row, and the certificate does
NOT count the unit as covered. That closed RED-1 NEW-4 (one shared token buying coverage for
a different slot's wrong answer).

The price BLUE-1 flagged: `covered:true` is now a strong claim, so a turn a reader would call
fully answered can still come back with units in `indeterminate`. This instrument measures how
often that happens, on REAL SERVED BYTES, and prints the served body next to the per-unit
verdict so a reader can grade the turn rather than trust a tally.

WHAT IS MEASURED, PER UNIT
--------------------------
  anchors          `unit_anchors(unit.text)` -- what a truthful answer would have to mention
  present/missing  which of those the served body actually carries, via `_anchor_present`
  state            `unit_answer_evidence(prompt, served)[unit_id]`
  rendered         whether a `Could not be answered:` row for this unit appears in the bytes

`reader_answered` is NOT computed here. The instrument prints the body; the human grades it.
Nothing in this file compares the model's answer to an expected string, and nothing adjusts a
threshold to make a verdict come out a chosen way.

TWO SOURCES, BOTH REAL, NEITHER SYNTHETIC
-----------------------------------------
  --live       drive the running daemon over `/api/chat` (chat_id prefix enforced below) with
               the prompts in PROMPTS, which are turn shapes drawn from this repo's own
               corpora, not written to produce a verdict.
  --served-json  score served bytes already captured by another instrument
               (`tests/red_baselines/red_c_baseline.json` -> rows[].served.text). Those bytes
               were served by the BASELINE build; scoring them here measures TODAY's rule
               against YESTERDAY's answers and must be labelled that way.

USAGE
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_i_indeterminate_cost.py --tree "$PWD" --live --base-url http://127.0.0.1:11437
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

AUDIT_PREFIX = "audit-final-mint-"

#: Turn shapes taken from this repo's own RED corpora and the operator's logged turns. Chosen
#: for SHAPE (single slot / two slots / four slots / mixed family), not for an expected answer.
PROMPTS: tuple[tuple[str, str], ...] = (
    ("single_weather", "what is the weather in Rome"),
    ("two_weather", "what is the weather in Rome and what is the weather in Paris"),
    ("weather_plus_fx", "what is the weather in Rome and what is 1000 EUR to RUB"),
    ("single_arith", "what is 18^2"),
    ("two_arith", "what is 18^2 and what is 19^2"),
    ("fx_plus_seatemp",
     "1500 eur to usd, and what is the water temperature in the Baltic Sea"),
    ("canonical_4slot",
     "What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in Rome, "
     "and what is the water temperature in the Baltic Sea?"),
    ("two_fx", "convert 100 eur to usd and convert 100 gbp to usd"),
    ("two_capitals",
     "what is the capital of France and what is the capital of Italy"),
    ("identity_plus_arith", "who are you and what is 2+2"),
    ("two_named_things", "name one graph database and name one vector database"),
    ("lowercase_fx_no_number", "convert to rub and what is the weather in Rome"),
)


def _post(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def drive(base_url: str, key: str, prompt: str, timeout: float) -> dict:
    chat_id = f"{AUDIT_PREFIX}{key}-{int(time.time())}"
    assert chat_id.startswith(AUDIT_PREFIX)
    t0 = time.time()
    try:
        raw = _post(
            base_url.rstrip("/") + "/api/chat",
            {"model": "vool", "chat_id": chat_id,
             "messages": [{"role": "user", "content": prompt}], "stream": False},
            timeout,
        )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"key": key, "prompt": prompt, "chat_id": chat_id, "served": "",
                "error": f"{type(exc).__name__}: {exc}", "latency_s": time.time() - t0}
    msg = raw.get("message") if isinstance(raw.get("message"), dict) else {}
    commit = raw.get("vool_response_commit")
    return {
        "key": key, "prompt": prompt, "chat_id": chat_id,
        "served": str((msg or {}).get("content") or ""),
        "commit": commit if isinstance(commit, dict) else {},
        "latency_s": round(time.time() - t0, 2), "error": "",
    }


def score(ac, prompt: str, served: str) -> dict:
    body = ac.answering_body(served)
    served_tokens = frozenset(ac._unit_tokens(body))
    served_tokens = served_tokens | {t.replace(",", "").replace(".", "") for t in served_tokens}
    states = ac.unit_answer_evidence(prompt, served)
    rows = []
    for unit in ac.demand_units(prompt):
        anchors = list(ac.unit_anchors(unit.text))
        present = [a for a in anchors if ac._anchor_present(a, served_tokens)]
        missing = [a for a in anchors if a not in present]
        rendered = any(
            line.lstrip().startswith("*") and unit.text.strip().strip(".?").lower() in line.lower()
            for line in served.splitlines()
        )
        rows.append({
            "unit_id": unit.unit_id, "unit": unit.text, "anchors": anchors,
            "present": present, "missing": missing,
            "state": states.get(unit.unit_id), "rendered_row": rendered,
        })
    return {"units": rows, "n_units": len(rows)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", default=os.getcwd())
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--base-url", default="http://127.0.0.1:11437")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--served-json", default="", help="red_c_baseline.json-shaped capture")
    ap.add_argument("--prompts", default="",
                    help="JSON list of [key, prompt] pairs to drive INSTEAD of PROMPTS")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tree = os.path.realpath(args.tree)
    sys.path.insert(0, tree)
    from core.agent_runtime import answer_coverage as ac

    resolved = os.path.realpath(ac.__file__)
    if not resolved.startswith(tree):
        raise SystemExit(f"REFUSING TO RUN: answer_coverage resolved to {resolved}, outside {tree}")

    prompts = PROMPTS
    if args.prompts:
        prompts = tuple((str(k), str(p)) for k, p in json.load(open(args.prompts)))

    captures: list[dict] = []
    if args.live:
        for key, prompt in prompts:
            captures.append(drive(args.base_url, key, prompt, args.timeout))
            print(f"  drove {key} ({captures[-1].get('latency_s')}s)", flush=True)
    if args.served_json:
        blob = json.load(open(args.served_json))
        for row in blob.get("rows", []):
            served = ((row.get("served") or {}).get("text")) or ""
            captures.append({"key": row.get("key"), "prompt": row.get("text") or "",
                             "chat_id": (row.get("served") or {}).get("chat_id", ""),
                             "served": served, "error": "", "source": args.served_json})

    print("=" * 96)
    print("RED-I — indeterminate cost of the every-anchor coverage rule")
    print("=" * 96)
    print(f"tree   : {tree}")
    print(f"module : {resolved}")
    print(f"turns  : {len(captures)}")
    print()

    tally = {"satisfied": 0, "indeterminate": 0, "unanswered": 0}
    n_turn_with_indet = 0
    n_turn_all_sat = 0
    scored: list[dict] = []
    for cap in captures:
        if cap.get("error") or not cap.get("served"):
            print(f"[{cap['key']}] NO BYTES: {cap.get('error')}")
            continue
        s = score(ac, cap["prompt"], cap["served"])
        cap.update(s)
        scored.append(cap)
        states = [u["state"] for u in s["units"]]
        for st in states:
            if st in tally:
                tally[st] += 1
        if any(st == "indeterminate" for st in states):
            n_turn_with_indet += 1
        if states and all(st == "satisfied" for st in states):
            n_turn_all_sat += 1

        print("-" * 96)
        print(f"[{cap['key']}]  units={s['n_units']}  states={states}")
        print(f"  prompt : {cap['prompt']}")
        print("  served :")
        for line in cap["served"].splitlines():
            print(f"    | {line}")
        for u in s["units"]:
            print(f"    unit {u['unit_id']}: {u['unit']!r}")
            print(f"      anchors={u['anchors']} missing={u['missing']} "
                  f"-> {u['state']}  rendered_row={u['rendered_row']}")

    total_units = sum(v for v in tally.values())
    print()
    print("=" * 96)
    print("TALLY (machine verdicts only — reader grading is done in the report, not here)")
    for k, v in tally.items():
        pct = 100.0 * v / max(total_units, 1)
        print(f"  {k:16} {v:>4} / {total_units}  ({pct:5.1f}%)")
    print(f"  turns with >=1 indeterminate unit : {n_turn_with_indet} / {len(scored)}")
    print(f"  turns where every unit satisfied  : {n_turn_all_sat} / {len(scored)}")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"tree": tree, "tally": tally, "turns": captures}, fh, indent=1)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
