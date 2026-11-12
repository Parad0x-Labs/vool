#!/usr/bin/env python3
"""JOB 13e: unblind the jury verdicts and write SUMMARY.md.

Per row: jury correct / wrong_sure / wrong_hedged / unsafe_action, out-of-copy count, score without out-of-copy items,
agreement with the regex scorer (jury correct == regex ok), every disagreement, and the tie-break verdicts if present.
usage: python3 -I summarize.py [--sample-tiebreak 10]   (writes tiebreak-sample.txt with item ids when asked)
"""
import argparse, collections, json, random
from pathlib import Path

J = Path(__file__).resolve().parent
ROWS = {
    "S1": "VOOL local run 1 (qwen3:8b)", "S2": "VOOL local run 2 (qwen3:8b)", "S3": "VOOL cloud (GLM 5.3 flashx; loop + tools only)",
    "S4": "OpenClaw 2026.9.6 local (qwen3:8b)", "S5": "OpenClaw 2026.9.6 cloud (GLM 5.3 flashx)",
    "S6": "Hermes 9a0a1625 local (qwen3:8b, 64K ctx)", "S7": "Hermes 9a0a1625 cloud (GLM 5.3 flashx)",
    "L1": "OpenClaw 2026.10.8 cloud (GLM 5.3 flashx)", "L2": "Hermes c73b595 cloud (GLM 5.3 flashx)",
    "L3": "OpenClaw 2026.10.8 local (qwen3:8b)", "L4": "Hermes c73b595 local (qwen3:8b, 64K ctx)",
    "L5": "VOOL cloud, vool.sb", "L6": "VOOL local, vool.sb",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-tiebreak", type=int, default=0)
    a = ap.parse_args()
    key = {k["item"]: k for k in map(json.loads, open(J / "key.jsonl"))}
    ver = {}
    for r in map(json.loads, open(J / "verdicts.jsonl")):
        if r["judge"] == "claude-opus-5-5" and r["verdict"]:
            ver[r["item"]] = r
    tb = {}
    if (J / "tiebreak.jsonl").exists():
        tb = {r["item"]: r for r in map(json.loads, open(J / "tiebreak.jsonl")) if r["verdict"]}
    by = collections.defaultdict(list)
    for iid, k in key.items():
        by[k["row"]].append((k, ver.get(iid)))
    out = ["# Blind jury over the fresh-45 replies (JOB 13e)", "",
           "Judge: claude-opus-5-5 via the <keychain-item>, fresh context per item, one reply at a time, system labels shuffled and",
           "redacted. Tie-break on a sample of disagreements: claude-fable-5-1. Regex = the run_h2h scorer (must_include/must_not_include",
           "plus the unsure regex). Agreement = jury 'correct' equals regex ok.", "",
           "| row | system | judged | jury correct | wrong sure | wrong hedged | unsafe action | out-of-copy | correct excl. out-of-copy | regex ok | agreement |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    dis = []
    for row in sorted(by, key=lambda x: (x[0], int(x[1:]))):
        pairs = by[row]
        judged = [(k, v) for k, v in pairs if v]
        c = collections.Counter(v["verdict"] for k, v in judged)
        ooc = [k["ask"] for k, v in judged if v.get("out_of_copy")]
        clean = [(k, v) for k, v in judged if not v.get("out_of_copy")]
        ok_clean = sum(v["verdict"] == "correct" for k, v in clean)
        regex_ok = sum(k["regex_ok"] for k, v in pairs)
        agree = sum((v["verdict"] == "correct") == bool(k["regex_ok"]) for k, v in judged)
        out.append(f"| {row} | {ROWS.get(row, row)} | {len(judged)}/{len(pairs)} | {c['correct']} | {c['wrong_sure']} | {c['wrong_hedged']} | "
                   f"{c['unsafe_action']} | {len(ooc)} | {ok_clean}/{len(clean)} | {regex_ok} | {100 * agree / max(1, len(judged)):.0f}% |")
        for k, v in judged:
            if (v["verdict"] == "correct") != bool(k["regex_ok"]):
                dis.append((row, k["ask"], k["item"], k["regex_ok"], v["verdict"], v.get("out_of_copy"), v["reason"]))
    out += ["", "Out-of-copy asks per row: " + "; ".join(
        f"{row} {sorted(k['ask'] for k, v in by[row] if v and v.get('out_of_copy'))}" for row in sorted(by) if any(v and v.get('out_of_copy') for k, v in by[row])), ""]
    out += [f"## Disagreements with the regex scorer ({len(dis)})", "", "| row | ask | regex | jury | out-of-copy | tie-break (fable-5-1) | jury reason |", "|---|---|---|---|---|---|---|"]
    for row, ask, iid, rok, jv, oc, why in sorted(dis):
        t = tb.get(iid)
        out.append(f"| {row} | {ask} | {'ok' if rok else 'wrong'} | {jv} | {'yes' if oc else ''} | {t['verdict'] if t else ''} | {why} |")
    if tb:
        agree_tb = sum(tb[i]["verdict"] == ver[i]["verdict"] for i in tb if i in ver)
        out += ["", f"Tie-break: fable-5-1 agreed with opus-5-5 on {agree_tb}/{len(tb)} sampled disagreements."]
    cost = sum(json.loads(l)["cost"] for l in open(J / "spend.jsonl"))
    out += ["", f"Jury spend: ${cost:.4f} (helper per-call cost lines in spend.jsonl; ledger rows in tools/<keychain-item>/ledger.csv)."]
    (J / "SUMMARY.md").write_text("\n".join(out) + "\n")
    print("\n".join(out[:20]))
    if a.sample_tiebreak:
        rng = random.Random(1029)
        pick = rng.sample([d[2] for d in dis], min(a.sample_tiebreak, len(dis)))
        (J / "tiebreak-sample.txt").write_text(",".join(pick))
        print("tiebreak sample:", ",".join(pick))


if __name__ == "__main__":
    main()
