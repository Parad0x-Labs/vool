#!/usr/bin/env python3
"""RED-FINAL correction — re-score a captured RED-C run with a labeller that is not blind.

RED-C's `_served_accounting` is wrong in BOTH directions, and this file fixes both rather
than the one that flatters whoever is reading.

  (1) AGAINST the runtime -- it matches disclosure rows on each slot's ANSWER tokens
      (`("324",)`, `("neo4j", ...)`) while the sweep's rows quote the QUESTION. A slot
      correctly named in a `* ` row is scored SILENTLY_DROPPED. RED-1 pass 2 corrected this
      by hand; it is corrected mechanically here.

  (2) FOR the runtime, and this is the direction that matters -- a slot counts ANSWERED as
      soon as ONE body line carries a distinctive token plus a digit. The line
      `1. I cannot provide the current water temperature in the Baltic Sea without
      real-time data.` carries `Baltic` and the digit `1`, so an explicit REFUSAL of the
      slot scores ANSWERED. An instrument that scores a refusal as an answer will certify a
      runtime that silently drops a slot, which makes it useless for the one question it
      exists to settle. That is not a tuning preference; it is a broken instrument.

So this adds a third verdict, REFUSED_IN_PROSE: the slot's evidence line is a refusal
written in the model's own words rather than under a disclosure header. It is NOT counted
as answered and NOT counted as silently dropped -- the user was told. It IS counted against
any certificate claiming `covered: true`, because a turn cannot both refuse a slot and
certify that the request was covered.

Reads a JSON capture only. Sends nothing, touches no daemon.

USAGE
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
        tests/red_j_corpus_correction.py --capture <scratch>/red_c_final.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys

#: Written by a MODEL in its own prose, not under a disclosure header. `answering_body()`
#: strips the runtime's three headers and nothing else, so these survive into the body and
#: every anchor of the refused slot is present in them by construction.
_PROSE_REFUSAL_RE = re.compile(
    r"\b(i cannot|i can't|i can not|cannot provide|can't provide|unable to|"
    r"not able to|don'?t have access|do not have access|no real[- ]time|"
    r"without real[- ]time|is not available|isn'?t available|not currently available|"
    r"fails because|failed because|i won'?t provide|won'?t be able)\b",
    re.IGNORECASE,
)

_HEADER_RE = re.compile(
    r"(could not be answered|unable to answer|unavailable|not answered|"
    r"couldn'?t be answered|no answer for)\s*:?", re.I
)
_ROW_RE = re.compile(r"^\s*[-*•]\s+(.*)$")


def _split(text: str) -> tuple[str, list[str]]:
    """(body, disclosure rows) -- the same split RED-C uses, reproduced so this is standalone."""
    m = _HEADER_RE.search(text)
    body = text[: m.start()] if m else text
    tail = text[m.end():] if m else ""
    rows: list[str] = []
    for line in tail.splitlines():
        rm = _ROW_RE.match(line)
        if rm and rm.group(1).strip():
            rows.append(rm.group(1).strip())
        elif rows and line.strip():
            rows[-1] += " " + line.strip()
    if not rows and tail.strip():
        rows = [tail.strip()]
    return body, rows


def _question_tokens(unit_text: str) -> set[str]:
    return {t for t in re.findall(r"[\w'^]+", unit_text.lower()) if len(t) >= 4}


def rescore(row: dict) -> dict[str, str]:
    """Per-slot verdict with both of RED-C's blind spots removed."""
    served = row["served"]
    body, rows = _split(served["text"])
    # The unit texts the runtime itself minted, so a row quoting the QUESTION is matched.
    unit_texts = [u[1] for u in row.get("_units", [])]
    out: dict[str, str] = {}
    for label, tokens, needs_number in row["_slots"]:
        evidence_line = ""
        for line in body.splitlines():
            if not any(tok.lower() in line.lower() for tok in tokens):
                continue
            if (needs_number and re.search(r"\d", line)) or (
                not needs_number and len(line.split()) >= 3
            ):
                evidence_line = line
                break
        # CORRECTION (1): a disclosure row counts if it shares the slot's ANSWER tokens OR
        # quotes a minted unit that carries them -- the shape the sweep actually writes.
        named = False
        for disclosure in rows:
            if any(tok.lower() in disclosure.lower() for tok in tokens):
                named = True
                break
            for unit_text in unit_texts:
                if not any(tok.lower() in unit_text.lower() for tok in tokens):
                    continue
                shared = _question_tokens(unit_text) & _question_tokens(disclosure)
                if len(shared) >= 2:
                    named = True
                    break
            if named:
                break
        # CORRECTION (2): a refusal in the model's own prose is not an answer.
        refused_in_prose = bool(evidence_line and _PROSE_REFUSAL_RE.search(evidence_line))
        if refused_in_prose:
            out[label] = "REFUSED_IN_PROSE"
        elif evidence_line and named:
            out[label] = "CONTRADICTED"
        elif evidence_line:
            out[label] = "ANSWERED"
        elif named:
            out[label] = "NAMED_FAILED"
        else:
            out[label] = "SILENTLY_DROPPED"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", required=True)
    args = ap.parse_args()

    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from red_c_adversarial_corpus import CORPUS

    by_key = {e.key: e for e in CORPUS}
    capture = json.load(open(args.capture, encoding="utf-8"))
    print(f"capture head : {capture['head']}")
    print(f"capture tree : {capture['tree']}")
    print(f"captured at  : {capture['captured_at']}")
    print()

    from core.agent_runtime.answer_coverage import demand_units

    tot_raw = tot_new = 0
    lying_raw: list[str] = []
    lying_new: list[str] = []
    refused_rows: list[tuple[str, str, bool]] = []
    changed: list[tuple[str, str, str, str]] = []

    for row in capture["rows"]:
        if not row.get("served"):
            continue
        entry = by_key[row["key"]]
        row["_slots"] = [list(s) for s in entry.slots]
        row["_units"] = [[u.unit_id, u.text] for u in demand_units(row["text"])]
        old = row["served"]["slot_states"]
        new = rescore(row)
        covered = bool((row["served"]["closure_verdict"] or {}).get("covered"))
        d_old = sum(1 for v in old.values() if v == "SILENTLY_DROPPED")
        d_new = sum(1 for v in new.values() if v == "SILENTLY_DROPPED")
        tot_raw += d_old
        tot_new += d_new
        if d_old and covered:
            lying_raw.append(row["key"])
        # A certificate claiming coverage while a slot was dropped OR refused in prose.
        bad = d_new + sum(1 for v in new.values() if v == "REFUSED_IN_PROSE")
        if bad and covered:
            lying_new.append(row["key"])
        for label, verdict in new.items():
            if verdict == "REFUSED_IN_PROSE":
                refused_rows.append((row["key"], label, covered))
            if old.get(label) != verdict:
                changed.append((row["key"], label, old.get(label, "?"), verdict))

    print("PER-SLOT VERDICTS THAT MOVED (RED-C label -> corrected)")
    print("-" * 74)
    for key, label, before, after in changed:
        print(f"  {key:34} {label:10} {before:18} -> {after}")
    print()
    print("SLOTS REFUSED IN THE MODEL'S OWN PROSE (not a disclosure row)")
    print("-" * 74)
    for key, label, covered in refused_rows:
        flag = "  <-- CERTIFIED covered:true" if covered else ""
        print(f"  {key:34} {label:10} covered={covered}{flag}")
    print()
    print("EVERY SLOT SCORED SILENTLY_DROPPED, WITH THAT TURN'S DISCLOSURE ROWS")
    print("-" * 74)
    print("  Correction (1) is NOT mechanised: linking a slot to the row that names it needs")
    print("  a prompt-side token list the corpus does not carry, and any looser rule lets one")
    print("  row cover a neighbouring slot -- the exact 'borrowing a row' hazard that blinds a")
    print("  silent-drop detector. The rows are printed so the link is adjudicated by reading.")
    for row in capture["rows"]:
        if not row.get("served"):
            continue
        entry = by_key[row["key"]]
        row["_slots"] = [list(s) for s in entry.slots]
        row["_units"] = [[u.unit_id, u.text] for u in demand_units(row["text"])]
        new_states = rescore(row)
        drops = [k for k, v in new_states.items() if v == "SILENTLY_DROPPED"]
        if not drops:
            continue
        _, rows_out = _split(row["served"]["text"])
        print(f"  {row['key']}  slots={drops}")
        print(f"      disclosure rows: {rows_out or 'NONE'}")
    print()
    print(f"SILENTLY DROPPED  RED-C label : {tot_raw}")
    print(f"SILENTLY DROPPED  corrected   : {tot_new}")
    print(f"covered:true OVER A DROP   RED-C : {len(lying_raw)}  {lying_raw}")
    print(f"covered:true OVER A DROP-OR-REFUSAL corrected : "
          f"{len(lying_new)}  {lying_new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
