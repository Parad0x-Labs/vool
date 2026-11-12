"""Mechanical scorer for Trial #3. No model judgment: substring checkers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trial3_tasks
from trial3_tasks import TASKS

OUT = Path(__file__).with_name("council_trial3_results.json")
state = json.loads(OUT.read_text())

UNCERTAIN = ("cannot determine", "cannot be determined", "insufficient", "not enough",
             "cannot say", "undetermined", "cannot establish", "cannot conclude",
             "no way to know", "impossible to determine")


def check_text(text: str, task: dict) -> tuple[bool, bool]:
    """returns (passes_required, hits_known_wrong)"""
    low = text.lower()
    ok = all(m.lower() in low for m in task.get("must", []))
    wrong = any(m.lower() in low for m in task.get("must_not", []))
    return ok, wrong


def score_arm(text: str | None, status: str | None, task: dict) -> str:
    if not text and not status:
        return "infra_failed"
    if task.get("unknown"):
        low = (text or "").lower()
        preserved = any(m in low for m in UNCERTAIN)
        if status == "partial" or preserved:
            return "unknown_preserved"
        # committed without uncertainty marker -> claimed an unknowable answer
        return "false_confident"
    if status == "committed":
        ok, wrong = check_text(text or "", task)
        if ok and not wrong:
            return "correct"
        return "false_confident" if wrong else "wrong"
    if status == "partial":
        ok, wrong = check_text(text or "", task)
        if ok and not wrong:
            return "honest_partial"
        return "unnecessary_partial"
    return "wrong"


rows = []
for task in TASKS:
    tid = task["id"]
    s = state["solo"].get(tid, {})
    c = state["council"].get(tid, {})
    s_verdict = score_arm(s.get("text"), "committed" if s.get("text") else None, task)
    c_verdict = score_arm(c.get("answer"), c.get("status"), task)
    rows.append({
        "id": tid, "family": task["family"], "trap": task.get("trap", False),
        "solo": {"verdict": s_verdict, "tokens": s.get("tokens"),
                 "cost": s.get("cost"), "latency_ms": s.get("latency_ms"),
                 "error": s.get("error")},
        "council": {"verdict": c_verdict, "status": c.get("status"),
                    "paid_calls": c.get("paid_calls"),
                    "n_contested": c.get("n_contested"), "kinds": c.get("kinds"),
                    "unresolved": c.get("unresolved"),
                    "tokens": c.get("tokens"), "cost": c.get("cost"),
                    "wall_s": c.get("wall_s"), "seat_failures": c.get("seat_failures"),
                    "error": c.get("error")},
    })

# aggregate
def agg(arm):
    counts = {}
    for r in rows:
        v = r[arm]["verdict"]
        counts[v] = counts.get(v, 0) + 1
    return counts

print(json.dumps({"solo": agg("solo"), "council": agg("council")}, indent=2))

solo_cost = sum(r["solo"]["cost"] or 0 for r in rows if r["solo"]["cost"])
solo_lat = [r["solo"]["latency_ms"] for r in rows if r["solo"]["latency_ms"]]
council_cost = sum((r["council"].get("cost") or 0) for r in rows)
council_lat = [r["council"].get("wall_s") for r in rows if r["council"].get("wall_s")]
print(f"\nsolo total cost=${solo_cost:.6f} avg_latency={sum(solo_lat)/len(solo_lat):.0f}ms")
print(f"council total cost=${council_cost:.6f} avg_wall={sum(council_lat)/len(council_lat):.1f}s")

wins = sum(1 for r in rows if r["council"]["verdict"].startswith(("correct", "unknown_preserved", "honest"))
           and r["solo"]["verdict"] in ("wrong", "false_confident", "unnecessary_partial"))
losses = sum(1 for r in rows if r["solo"]["verdict"].startswith(("correct", "unknown_preserved"))
             and r["council"]["verdict"] in ("wrong", "false_confident", "unnecessary_partial"))
print(f"\ncouncil-wins-vs-solo={wins} council-losses-vs-solo={losses}")

print("\nper-task:")
for r in rows:
    print(f"{r['id']:>3} {r['family']:<8} trap={str(r['trap']):<5} "
          f"solo={r['solo']['verdict']:<18} council={r['council']['verdict']:<18}"
          f" disputes={r['council'].get('n_contested')} unresolved={r['council'].get('unresolved')}")

Path("council_trial3_scored.json").write_text(json.dumps(
    {"rows": rows, "aggregate": {"solo": agg("solo"), "council": agg("council")},
     "solo_cost": solo_cost, "council_cost": council_cost}, indent=2))
print("\nwritten: council_trial3_scored.json")
