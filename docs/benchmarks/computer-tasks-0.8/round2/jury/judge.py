#!/usr/bin/env python3
"""JOB 13e: blind jury over items.jsonl, one item per call through the spend-capped <keychain-item>.

Resume-safe: items already in verdicts.jsonl (for this judge model) are skipped. Stops starting new calls once this
job's recorded spend (sum of the helper's per-call cost lines, kept in spend.jsonl) reaches --cap.
usage: <py3.12> judge.py --model claude-opus-5-5 [--items items.jsonl] [--only ID,ID] [--workers 3] [--cap 8]
"""
import argparse, json, re, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

J = Path(__file__).resolve().parent
HELPER = Path("<work>/tools/<keychain-item>/claude_api.py")
WORK = J / "work"
LOCK = threading.Lock()
MAXTOK = [400]


def spent():
    p = J / "spend.jsonl"
    return sum(json.loads(l)["cost"] for l in open(p)) if p.exists() else 0.0


def judge(item, model, system_file, out_name, cap):
    with LOCK:
        if spent() >= cap:
            return None
    iid = item["item"]
    body = {k: item[k] for k in ("category", "turns", "rubric", "file_changes", "file_changes_scope")}
    src = WORK / f"{iid}.item.json"
    src.write_text(json.dumps(body, indent=1, ensure_ascii=False))
    out = WORK / f"{iid}.{model}.out"
    p = subprocess.run([sys.executable, "-I", str(HELPER), "--model", model, "--system-file", str(system_file), "--no-cache",
                        "--max-tokens", str(MAXTOK[0]), "--out", str(out), "--question", "Judge this item.", str(src)],
                       capture_output=True, text=True, timeout=600)
    m = re.search(r"Call \$([0-9.]+)", p.stdout)
    cost = float(m.group(1)) if m else 0.0
    text = out.read_text() if out.exists() else ""
    v = None
    try:
        v = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        assert v["verdict"] in ("correct", "wrong_sure", "wrong_hedged", "unsafe_action")
    except Exception:
        v = None
    row = {"item": iid, "judge": model, "verdict": (v or {}).get("verdict"), "out_of_copy": (v or {}).get("out_of_copy"),
           "reason": (v or {}).get("reason"), "raw": None if v else text[:500], "helper_rc": p.returncode,
           "helper_err": None if p.returncode == 0 else (p.stdout + p.stderr)[-300:], "cost": cost}
    with LOCK:
        with open(J / "spend.jsonl", "a") as f:
            f.write(json.dumps({"item": iid, "judge": model, "cost": cost}) + "\n")
        with open(J / out_name, "a") as f:
            f.write(json.dumps(row) + "\n")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--items", default="items.jsonl")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="verdicts.jsonl")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--cap", type=float, default=8.0)
    ap.add_argument("--max-tokens", type=int, default=400)
    a = ap.parse_args()
    MAXTOK[0] = a.max_tokens
    WORK.mkdir(exist_ok=True)
    fixture = (J / "FIXTURE-FILES.txt").read_text()
    sysf = WORK / "system.txt"
    sysf.write_text((J / "JUDGE-SYSTEM.txt").read_text().replace("{FIXTURE}", fixture))
    items = [json.loads(l) for l in open(J / a.items)]
    if a.only:
        want = set(a.only.split(","))
        items = [i for i in items if i["item"] in want]
    done = set()
    if (J / a.out).exists():
        done = {(r["item"]) for r in map(json.loads, open(J / a.out)) if r["judge"] == a.model and r["verdict"]}
    todo = [i for i in items if i["item"] not in done]
    print(f"{len(todo)} to judge with {a.model}; spent so far ${spent():.4f} of ${a.cap}", flush=True)
    n = bad = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for r in ex.map(lambda i: judge(i, a.model, sysf, a.out, a.cap), todo):
            if r is None:
                continue
            n += 1; bad += r["verdict"] is None
            if n % 25 == 0:
                print(f"{n} judged, {bad} unparsed, spent ${spent():.4f}", flush=True)
    print(f"done: {n} judged, {bad} unparsed, spent ${spent():.4f}", flush=True)


if __name__ == "__main__":
    main()
