"""Mechanical scorer for Trial #4 — no model judgment.

Substring/regex checkers against the frozen truth in trial4_truth.py.
Every flagged case must be manually audited before final numbers are quoted.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trial4_truth as T
from trial4_truth import TRUTH, H1_TRUTH

RESULTS = Path(__file__).resolve().parent / "council_trial4_results.json"


def score_text(tid: str, text: str) -> dict:
    text_l = (text or "").lower()
    spec = TRUTH[tid]
    missing_must = [m for m in spec.get("must", [])
                    if m.lower() not in text_l]
    hit_forbidden = [p for p in spec.get("must_not", []) if p.lower() in text_l]
    hit_regex = [p for p in spec.get("forbidden_patterns", []) if re.search(p, text or "", re.I)]
    should = [s for s in spec.get("should", []) if s.lower() in text_l]
    return {
        "must_missing": missing_must,
        "forbidden_hit": hit_forbidden,
        "forbidden_regex": hit_regex,
        "should_hits": should,
        "pass": not missing_must and not hit_forbidden and not hit_regex,
    }


def main() -> None:
    d = json.loads(RESULTS.read_text())
    out = {}
    for arm in ("SOLO", "C3", "C5", "C7"):
        arm_out = {}
        for k, rec in sorted(d.items()):
            if not k.startswith(arm + ":"):
                continue
            tid = k.split(":", 1)[1]
            if "error" in rec:
                arm_out[tid] = {"verdict": "RUN_ERROR", "detail": rec["error"][:120]}
                continue
            if arm == "SOLO":
                text = rec.get("answer") or ""
            else:
                # sealed-judge law: the committed bytes ARE the judge's raw
                # final response (or empty when refused)
                text = rec.get("answer") or ""
            if rec.get("status") == "invalid_refused" or (not text):
                arm_out[tid] = {"verdict": "INVALID_REFUSED"}
                continue
            s = score_text(tid, text)
            if tid == "H1_scheduling":
                s["uniqueness_stated"] = bool(
                    re.search(r"not\s+unique", (text or "").lower()))
            arm_out[tid] = {"verdict": "PASS" if s["pass"] else "CHECK",
                            **{k2: v2 for k2, v2 in s.items() if k2 != "pass"},
                            "status": rec.get("status")}
        out[arm] = arm_out
    summary = {}
    for arm, tasks in out.items():
        verdicts = {}
        for tid, r in tasks.items():
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        summary[arm] = verdicts
    print(json.dumps({"summary": summary, "detail": out}, indent=1))
    Path(__file__).resolve().parent.joinpath("council_trial4_scored.json").write_text(
        json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
