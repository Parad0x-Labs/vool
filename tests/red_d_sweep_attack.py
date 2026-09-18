"""RED-D — attacking the demand-accounting mechanism itself (AUD-20260829-003, pass 2).

RED-A/B/C were built BEFORE the fix and measure the defect the fix was aimed at.
RED-D is built AFTER reading the fix and aims at the MECHANISM the fix introduced:
the structural segmenter (`demand_units`), the three-rung evidence ladder
(`slice_answer_record` / `served_content_overlap` / `ride_along_no_object`), and the
unconditional render path in `core/finalization.py::_rss_closure_sweep`.

WHAT THIS INSTRUMENT LOOKS FOR, AND WHY IT IS NOT SYMMETRIC WITH RED-B/C
------------------------------------------------------------------------
RED-B/C detect UNDER-disclosure: a requested slot that vanishes with no row and a
certificate claiming coverage. The fix removed the render gate, so the new failure
direction is the opposite one, and it is NOT merely cosmetic:

  * FALSE_DENIAL  — the runtime answers a request correctly and then appends a row
    saying no lane claimed it, and certifies `covered: false`. The user is told the
    answer they are looking at was not produced. An accounting layer that lies in
    this direction destroys the value of the rows it renders everywhere else: once
    a "could not be answered" row appears under correct answers, the row stops
    carrying information.

  * FALSE_SATISFACTION — the evidence ladder's rung 2 (`served_content_overlap`)
    discharges a unit when ANY one of its content tokens (len >= 3, not a stop
    word) appears in the answering body. Tokens are compared without regard to
    which slot produced them, so one slot's answer can discharge a DIFFERENT slot
    that shares a word. That re-creates the original silent-drop defect inside the
    mechanism built to fix it.

THE STRUCTURAL PREDICTION, MADE FROM THE TREE'S OWN CODE
--------------------------------------------------------
`units_present_in_answer` filters each unit's tokens with `len(token) >= 3 and
token not in _CONTENT_STOP_TOKENS`. A unit whose every token fails that filter has
an EMPTY eligible set and can therefore never be discharged by rung 2, no matter
what the runtime answers. `unit_is_disclosed_in` returns False on the same empty
set, so such a unit is also never suppressed as already-disclosed. If it is not a
ride-along it renders a row on every single turn, forever. The instrument computes
that prediction from the tree (`--predict`) and then checks it against served bytes.

DETECTION IS DETERMINISTIC, NEVER PROSE-MATCHED
-----------------------------------------------
No check compares a served answer to a stored expected answer. Every entry carries
`ground_truth`: a list of tokens whose presence in the ANSWERING half of the body is
an environmental fact that the request was served (e.g. "4" for `what is 2+2?`, a
currency code for a conversion). A FALSE_DENIAL is declared only when BOTH hold:
the ground truth is present in the answering half, AND the sweep rendered a `* ` row
quoting that same unit. That is a self-contradiction inside one served body and
needs no opinion about the answer's quality.

LAYER DISCRIMINATOR (given by the lead, verified in the tree)
------------------------------------------------------------
`core/finalization.py:250` writes rows as `* <text> — <reason>`; `core/conductor/
compose.py` writes rows as `- <text> — <reason>`. Rows are attributed to a layer by
that bullet, so a composition defect is never charged to the sweep and vice versa.

SELFTEST
--------
`--selftest` runs the detectors over synthetic bodies and must report BOTH failure
directions: a runtime that silently drops a slot, and a runtime that denies a slot
it answered. If either goes green the instrument is broken, not the runtime.

USAGE
-----
    cd <repair worktree>
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_d_sweep_attack.py --selftest
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_d_sweep_attack.py --predict --tree "$PWD"
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_d_sweep_attack.py --serve --base-url http://127.0.0.1:11437 \
        --out red_d.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any

AUDIT_PREFIX = "audit-AUD-20260829-003-RED2-"
SWEEP_BULLET = "*"
COMPOSE_BULLET = "-"
DISCLOSURE_HEADERS = (
    "could not be answered:",
    "could not be answered",
    "not answered:",
    "unavailable:",
)


# --------------------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------------------
@dataclass
class Entry:
    key: str
    prompt: str
    #: How many distinct answerable requests a reader sees. The only human judgement here.
    expected_units: int
    #: Tokens whose presence in the ANSWERING half proves the request was served.
    #: Empty means "this entry is not asserted to be answerable" (segmenter-only entries).
    ground_truth: list[str] = field(default_factory=list)
    #: Substrings that, if they appear in the answering half, name a slot that was NEVER
    #: requested (verdict property 5). Checked case-insensitively.
    never_requested: list[str] = field(default_factory=list)
    #: Slot probes: (label, tokens that must appear on ONE line of the answering half).
    #: A slot with neither an answer line nor a disclosure row is a SILENT DROP.
    slots: list[tuple[str, list[str]]] = field(default_factory=list)
    attack: str = ""
    note: str = ""


CORPUS: list[Entry] = [
    # ---- CLASS A: units with an EMPTY eligible-token set -> structurally un-dischargeable ----
    Entry(
        "a_arith_plain", "what is 2+2?", 1, ground_truth=["4"],
        attack="A:undischargeable",
        note="tokens what(stop) is(<3) 2(<3) 2(<3) -> eligible set empty",
    ),
    Entry(
        "a_arith_squared", "what is 18^2?", 1, ground_truth=["324"],
        attack="A:undischargeable",
        note="tokens what(stop) is 18 2 -> empty; also RED-C's five_slots_one_run tail",
    ),
    Entry(
        "a_arith_words", "what is two plus two?", 1, ground_truth=["4", "four"],
        attack="A:undischargeable",
        note="two(stop) plus(>=3, NOT stop) -> 'plus' is eligible; control for a_arith_plain",
    ),
    Entry(
        "a_imperative_short", "say ok", 1, ground_truth=["ok"],
        attack="A:undischargeable",
        note="say(eligible, absent from 'Ok.') ok(<3) -> renders",
    ),
    Entry(
        "a_who_are_you", "who are you?", 1, ground_truth=[],
        attack="A:undischargeable",
        note="who(stop) are(stop) you(stop) -> empty eligible set",
    ),
    Entry(
        "a_what_can_you_do", "what can you do?", 1, ground_truth=[],
        attack="A:undischargeable",
        note="all four tokens are stop words -> empty eligible set",
    ),
    Entry(
        "a_paraphrase_capital", "what is the capital of France?", 1,
        ground_truth=["paris"],
        attack="A:paraphrase",
        note="'capital'/'france' ARE eligible; answer normally repeats France -> control",
    ),
    Entry(
        "a_yes_no", "is 97 a prime number?", 1, ground_truth=["yes", "prime"],
        attack="A:paraphrase",
        note="'prime'/'number' eligible but 'number' is in META_OUTPUT; answer may say 'Yes.' only",
    ),
    # ---- CLASS B: rung-2 content overlap discharging a slot it did not earn ----
    Entry(
        "b_united_states_water",
        "1000 EUR to USD, and the water temperature in the United States",
        2,
        ground_truth=["usd"],
        slots=[("fx", ["eur", "usd"]), ("water", ["water"])],
        attack="B:overlap_overgrant",
        note="FX answer prints 'United States dollar'; the water unit's tokens united/states "
             "then overlap the FX answer and rung 2 discharges a slot nobody answered",
    ),
    Entry(
        "b_euro_europe",
        "convert 100 EUR to GBP and tell me the population of Europe",
        2,
        ground_truth=["gbp"],
        slots=[("fx", ["eur", "gbp"]), ("population", ["population"])],
        attack="B:overlap_overgrant",
        note="'euro' vs 'europe' are distinct tokens; control for b_united_states_water",
    ),
    Entry(
        "b_rate_rate",
        "what is the EUR to USD rate, and what is the current interest rate in Japan",
        2,
        ground_truth=["usd"],
        slots=[("fx", ["eur", "usd"]), ("interest", ["interest"])],
        attack="B:overlap_overgrant",
        note="the shared token 'rate' is eligible (len 4, not a stop word); the FX answer "
             "contains 'rate', so the Japan interest-rate unit can be discharged by it",
    ),
    # ---- CLASS C: structural segmenter ----
    Entry(
        "c_list_one_item", "what is the price of gold, silver and platinum", 1,
        ground_truth=[], attack="C:segmenter",
        note="one ask over a three-item list; must NOT mint 3",
    ),
    Entry(
        "c_and_in_noun_phrase", "what is the weather in Trinidad and Tobago", 1,
        ground_truth=["trinidad"], attack="C:segmenter",
        note="'and' inside a single proper noun; must not split a country in half",
    ),
    Entry(
        "c_number_with_commas", "convert 1,250,000 JPY to USD", 1, ground_truth=["usd"],
        attack="C:segmenter", note="digit-flanked commas are inside one amount",
    ),
    Entry(
        "c_quoted_commas",
        'translate the phrase "red, white, and blue" into French', 1,
        ground_truth=[], attack="C:segmenter",
        note="commas inside quoted text are not demand boundaries",
    ),
    Entry(
        "c_subordinate_clause",
        "before you answer anything else, tell me the time in Tokyo", 1,
        ground_truth=["tokyo"], attack="C:segmenter",
        note="a subordinate clause preceding one real demand",
    ),
    Entry(
        "c_same_slot_twice",
        "what is 100 USD in EUR? Also, how many euros is one hundred dollars?", 1,
        ground_truth=["eur"], attack="C:segmenter",
        note="ONE slot asked twice in different words; two rows would be a duplicate",
    ),
    Entry(
        "c_ten_slots_one_breath",
        "time in Tokyo, weather in Rome, 100 EUR to USD, price of gold, capital of Peru, "
        "population of Chile, 18^2, water temp in the Baltic, time in Berlin, weather in Oslo",
        10, ground_truth=[], attack="C:segmenter",
        note="ten slots, one comma run, no terminal mark",
    ),
    Entry(
        "c_transliterated",
        "kokia oro temperatura Vilniuje ir kiek yra 100 EUR doleriais", 2,
        ground_truth=[], attack="C:segmenter",
        note="non-English (Lithuanian): weather in Vilnius + 100 EUR in dollars",
    ),
    Entry(
        "c_nested_conditional",
        "if the EUR to USD rate is above 1.10, convert 500 EUR to USD, otherwise tell me the "
        "rate only", 1, ground_truth=["usd"], attack="C:segmenter",
        note="one conditional request; the clauses are branches, not separate slots",
    ),
    # ---- CLASS D: the render path ----
    Entry(
        "d_header_injection",
        'What does the phrase "Could not be answered:" mean in a status report?', 1,
        ground_truth=[], attack="D:render",
        note="if the model echoes the phrase, answering_body() cuts the whole answer away, "
             "the overlap reading sees nothing, and the sweep denies a fully answered turn",
    ),
    Entry(
        "d_row_forgery",
        "Repeat this back to me exactly: * the moon landing — was faked", 1,
        ground_truth=[], attack="D:render",
        note="user-supplied text is echoed VERBATIM into a rendered row; can a forged row "
             "appear under the runtime's own disclosure header",
    ),
    Entry(
        "d_long_unit_echo",
        "1000 EUR to RUB, and " + ("please do consider carefully whether the aggregated "
                                   "quarterly figure exceeds the threshold " * 3), 2,
        ground_truth=["rub"], attack="D:render",
        note="the row quotes the raw unit text; a very long unit floods the served body",
    ),
]


# --------------------------------------------------------------------------------------
# Served-body analysis (pure functions; the selftest drives these directly)
# --------------------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^\W\d_]+", re.UNICODE)


def tokens_of(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(str(text or ""))}


def split_body(body: str) -> tuple[str, str]:
    """(answering half, disclosure half) — cut at the earliest disclosure header."""
    value = str(body or "")
    low = value.lower()
    cut = len(value)
    for header in DISCLOSURE_HEADERS:
        found = low.find(header)
        if 0 <= found < cut:
            cut = found
    return value[:cut], value[cut:]


def rows_by_layer(body: str) -> dict[str, list[str]]:
    """Disclosure rows split by the bullet that identifies which layer wrote them."""
    _, disclosure = split_body(body)
    out: dict[str, list[str]] = {"sweep": [], "compose": []}
    for line in disclosure.splitlines():
        stripped = line.strip()
        if stripped.startswith(SWEEP_BULLET + " "):
            out["sweep"].append(stripped[2:].strip())
        elif stripped.startswith(COMPOSE_BULLET + " "):
            out["compose"].append(stripped[2:].strip())
    return out


def ground_truth_served(entry: Entry, body: str) -> bool:
    """Is the request environmentally served? Checked in the ANSWERING half only."""
    if not entry.ground_truth:
        return False
    answering, _ = split_body(body)
    hay = answering.lower()
    return any(tok.lower() in hay for tok in entry.ground_truth)


def row_quotes_prompt(entry: Entry, row: str) -> bool:
    """Does a disclosure row quote (a demand unit of) this prompt?

    Token containment, not substring: the row text is the raw unit text and the prompt is
    the raw request, so for a single-unit turn the row's tokens are a subset of the
    prompt's. Requires at least one token so an empty row never matches.
    """
    row_tokens = tokens_of(row.split("—")[0])
    if not row_tokens:
        return False
    return row_tokens <= tokens_of(entry.prompt)


def analyse(entry: Entry, body: str, verdict: dict[str, Any]) -> dict[str, Any]:
    """Deterministic verdicts over one served turn."""
    answering, _ = split_body(body)
    layers = rows_by_layer(body)
    served = ground_truth_served(entry, body)

    findings: list[str] = []

    # --- FALSE_DENIAL: the body answers a slot AND a row says no lane claimed THAT slot. ---
    #
    # INSTRUMENT DEFECT FOUND BY SELFTEST 3 AND FIXED, NOT TUNED AWAY. The first version
    # declared FALSE_DENIAL whenever the turn's ground truth was served anywhere and any row
    # quoted any part of the prompt. On a multi-slot turn that is wrong in the red team's own
    # favour: a COMPLIANT body which answers the FX slot and correctly discloses the water slot
    # tripped it, because "the turn served something" is not "this denied unit was served".
    # Evidence must be per slot:
    #   * single-unit turn  -> the row IS the whole request, and ground_truth proves that same
    #     request was served. Airtight with no per-slot annotation.
    #   * multi-slot turn   -> require a slot that is BOTH answered on a line of the answering
    #     half AND named in a disclosure row. Same contradiction, proven slot-locally.
    denied_rows: list[str] = []
    if entry.expected_units == 1:
        denied_rows = [r for r in layers["sweep"] if row_quotes_prompt(entry, r)]
        if served and denied_rows:
            findings.append("FALSE_DENIAL")
    else:
        for label, toks in entry.slots:
            answered_line = any(
                all(t.lower() in tokens_of(line) for t in toks)
                for line in answering.splitlines()
                if line.strip()
            )
            naming = [
                r for r in layers["sweep"] + layers["compose"]
                if all(t.lower() in tokens_of(r) for t in toks)
            ]
            if answered_line and naming:
                denied_rows += naming
                findings.append("FALSE_DENIAL")

    # A certificate claiming the request was not covered while the ground truth is served.
    if served and verdict.get("covered") is False and entry.expected_units == 1:
        findings.append("FALSE_UNCOVERED_CERT")

    # --- SILENT_DROP: an annotated slot with no answer line and no row anywhere. ---
    dropped: list[str] = []
    for label, toks in entry.slots:
        answered = any(
            all(t.lower() in tokens_of(line) for t in toks)
            for line in answering.splitlines()
            if line.strip()
        )
        named = any(
            all(t.lower() in tokens_of(r) for t in toks)
            for r in layers["sweep"] + layers["compose"]
        )
        if not answered and not named:
            dropped.append(label)
    if dropped:
        findings.append("SILENT_DROP")

    # --- UNREQUESTED_CONTENT (verdict property 5) ---
    stray = [s for s in entry.never_requested if s.lower() in answering.lower()]
    if stray:
        findings.append("UNREQUESTED_CONTENT")

    # --- DUPLICATE_ROWS: two rows in the same layer for the same token set. ---
    dupes: list[str] = []
    for layer, rows in layers.items():
        seen: dict[frozenset[str], int] = {}
        for r in rows:
            key = frozenset(tokens_of(r.split("—")[0]))
            if not key:
                continue
            seen[key] = seen.get(key, 0) + 1
        dupes += [f"{layer}:{n}x" for n in seen.values() if n > 1]
    if dupes:
        findings.append("DUPLICATE_ROWS")

    # --- FORGED_ROW: a row the runtime did not write, echoed from the user's own text. ---
    if entry.attack == "D:render":
        for r in layers["sweep"] + layers["compose"]:
            if "—" not in r and r:
                findings.append("MALFORMED_ROW")
                break

    return {
        "findings": sorted(set(findings)),
        "ground_truth_served": served,
        "sweep_rows": layers["sweep"],
        "compose_rows": layers["compose"],
        "denied_rows": denied_rows,
        "dropped_slots": dropped,
        "stray": stray,
        "duplicates": dupes,
        "verdict": verdict,
    }


# --------------------------------------------------------------------------------------
# Prediction from the measured tree
# --------------------------------------------------------------------------------------
def predict(tree: str) -> list[dict[str, Any]]:
    sys.path.insert(0, tree)
    from core.agent_runtime import answer_coverage as ac

    resolved = os.path.realpath(ac.__file__)
    if not resolved.startswith(os.path.realpath(tree)):
        raise SystemExit(
            f"REFUSING TO RUN: core.agent_runtime resolved to {resolved}, "
            f"outside the requested tree {tree}. Fix PYTHONPATH/cwd."
        )
    print(f"core.agent_runtime.answer_coverage -> {resolved}\n")

    stop = ac._CONTENT_STOP_TOKENS
    out = []
    for entry in CORPUS:
        units = ac.demand_units(entry.prompt)
        riders = set(ac.units_without_own_object(entry.prompt))
        rows = []
        for u in units:
            eligible = [
                t for t in ac._unit_tokens(u.text) if len(t) >= 3 and t not in stop
            ]
            rows.append(
                {
                    "unit_id": u.unit_id,
                    "text": u.text,
                    "eligible_tokens": eligible,
                    "undischargeable": (not eligible) and u.unit_id not in riders,
                    "ride_along": u.unit_id in riders,
                }
            )
        out.append(
            {
                "key": entry.key,
                "prompt": entry.prompt,
                "expected_units": entry.expected_units,
                "minted": len(units),
                "attack": entry.attack,
                "units": rows,
                "n_undischargeable": sum(1 for r in rows if r["undischargeable"]),
            }
        )
    return out


# --------------------------------------------------------------------------------------
# Live serving
# --------------------------------------------------------------------------------------
def send(base_url: str, prompt: str, chat_id: str, timeout: float) -> tuple[str, dict, float]:
    assert chat_id.startswith(AUDIT_PREFIX), f"probe chat_id must carry {AUDIT_PREFIX}"
    payload = json.dumps(
        {"chat_id": chat_id, "messages": [{"role": "user", "content": prompt}], "stream": False}
    ).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"<TRANSPORT ERROR: {exc}>", {}, time.time() - t0
    body = str(((data.get("message") or {}).get("content")) or "")
    verdict = ((data.get("vool_response_commit") or {}).get("closure_verdict")) or {}
    return body, verdict, time.time() - t0


# --------------------------------------------------------------------------------------
# Selftest — the instrument must fail BOTH directions
# --------------------------------------------------------------------------------------
def selftest() -> int:
    bad = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal bad
        status = "OK  " if cond else "BROKEN"
        if not cond:
            bad += 1
        print(f"  [{status}] {name} {detail}")

    print("SELFTEST 1 — a runtime that ANSWERS then DENIES (the new direction)")
    e = Entry("t", "what is 2+2?", 1, ground_truth=["4"])
    body = "2 + 2 = 4.\n\nCould not be answered:\n* what is 2+2? — no answering lane claimed this part of the request"
    r = analyse(e, body, {"covered": False, "demand_minted": 1, "demand_unanswered": 1})
    check("FALSE_DENIAL detected", "FALSE_DENIAL" in r["findings"], str(r["findings"]))
    check("FALSE_UNCOVERED_CERT detected", "FALSE_UNCOVERED_CERT" in r["findings"])

    print("SELFTEST 2 — a runtime that SILENTLY DROPS a slot (the old direction)")
    e = Entry("t", "1000 EUR to USD, and the water temperature in the United States", 2,
              ground_truth=["usd"], slots=[("fx", ["eur", "usd"]), ("water", ["water"])])
    body = "1,000 EUR (euro) x 1.17 = 1,170.00 USD (United States dollar)."
    r = analyse(e, body, {"covered": True, "demand_minted": 2, "demand_satisfied": 2})
    check("SILENT_DROP detected", "SILENT_DROP" in r["findings"], str(r["dropped_slots"]))
    check("water is the dropped slot", r["dropped_slots"] == ["water"])

    print("SELFTEST 3 — a COMPLIANT body must trip nothing")
    e = Entry("t", "1000 EUR to USD, and the water temperature in the United States", 2,
              ground_truth=["usd"], slots=[("fx", ["eur", "usd"]), ("water", ["water"])])
    body = ("1,000 EUR (euro) x 1.17 = 1,170.00 USD (United States dollar).\n\n"
            "Could not be answered:\n"
            "* the water temperature in the United States — no source for this")
    r = analyse(e, body, {"covered": False, "demand_minted": 2, "demand_satisfied": 1})
    check("no findings on a compliant body", r["findings"] == [], str(r["findings"]))

    print("SELFTEST 4 — layer discriminator: sweep '*' vs compose '-'")
    body = ("answer\n\nCould not be answered:\n- gold — compose row\n* baltic — sweep row")
    lay = rows_by_layer(body)
    check("compose row attributed to compose", lay["compose"] == ["gold — compose row"])
    check("sweep row attributed to sweep", lay["sweep"] == ["baltic — sweep row"])

    print("SELFTEST 5 — ground truth is read from the ANSWERING half only")
    e = Entry("t", "what is the capital of France?", 1, ground_truth=["paris"])
    body = "Could not be answered:\n* what is the capital of France? — no lane (Paris)"
    check("a mention inside the disclosure is NOT served",
          not ground_truth_served(e, body))

    print("SELFTEST 6 — duplicate rows in one layer are detected")
    e = Entry("t", "gold?", 1)
    body = ("x\n\nCould not be answered:\n- how much gold — a\n- how much gold — b")
    r = analyse(e, body, {})
    check("DUPLICATE_ROWS detected", "DUPLICATE_ROWS" in r["findings"], str(r["duplicates"]))

    print(f"\nSELFTEST RESULT: {'OK' if bad == 0 else f'{bad} BROKEN'}")
    return 1 if bad else 0


# --------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--predict", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--tree", default=os.getcwd())
    ap.add_argument("--base-url", default="http://127.0.0.1:11437")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    entries = [e for e in CORPUS if not args.only or args.only in e.key]
    record: dict[str, Any] = {"tree": args.tree, "entries": {}}

    if args.predict:
        print("=" * 86)
        print("RED-D PREDICTION — computed from the measured tree's own functions")
        print("=" * 86)
        preds = predict(args.tree)
        record["prediction"] = preds
        print(f"{'key':28} {'exp':>3} {'mint':>4} {'undis':>5}  attack")
        for p in preds:
            if args.only and args.only not in p["key"]:
                continue
            flag = "  <-- " if p["minted"] != p["expected_units"] else "       "
            print(f"{p['key']:28} {p['expected_units']:>3} {p['minted']:>4} "
                  f"{p['n_undischargeable']:>5}{flag}{p['attack']}")
        n_bad_card = sum(
            1 for p in preds if p["minted"] != p["expected_units"]
        )
        n_undis = sum(1 for p in preds if p["n_undischargeable"] > 0)
        print(f"\ncardinality != annotation : {n_bad_card}/{len(preds)}")
        print(f"entries carrying >=1 structurally UNDISCHARGEABLE unit : {n_undis}/{len(preds)}")
        for p in preds:
            for u in p["units"]:
                if u["undischargeable"]:
                    print(f"  UNDISCHARGEABLE  {p['key']:26} {u['text']!r}")

    if args.serve:
        print("\n" + "=" * 86)
        print(f"RED-D SERVED — {args.base_url}   {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
        print("=" * 86)
        tally: dict[str, int] = {}
        for e in entries:
            cid = AUDIT_PREFIX + e.key + "-" + time.strftime("%H%M%S")
            body, verdict, dt = send(args.base_url, e.prompt, cid, args.timeout)
            res = analyse(e, body, verdict)
            res["latency"] = round(dt, 1)
            res["prompt"] = e.prompt
            res["body"] = body
            res["attack"] = e.attack
            record["entries"][e.key] = res
            for f in res["findings"]:
                tally[f] = tally.get(f, 0) + 1
            mark = ",".join(res["findings"]) or "-"
            print(f"\n--- {e.key}  [{e.attack}]  {dt:.1f}s  ==> {mark}")
            print(f"    prompt : {e.prompt[:110]}")
            for line in body.splitlines()[:12]:
                print(f"    | {line[:130]}")
            print(f"    cert   : {json.dumps(verdict)}")
        print("\n" + "=" * 86)
        print("FINDING TALLY")
        for k in sorted(tally):
            print(f"  {k:24} {tally[k]:>3} / {len(entries)}")
        record["tally"] = tally

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(record, fh, indent=1, default=str)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
