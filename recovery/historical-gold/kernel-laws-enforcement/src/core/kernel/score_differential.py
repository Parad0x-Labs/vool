"""Score the differential report into the attribution table.

Buckets, per the differential's own logic:
- HARNESS-CLASS: a failure class present under EVERY judge on the same turn — the only
  shared component is the harness (laws, prompts, adapters, tools).
- CEILING-CLASS: present under smaller local judges, absent under the largest/cloud —
  the local-model ceiling, measured.
- JUDGE-SPECIFIC: present under exactly one judge — individual model quirks.
Latest row per (model, turn) wins, so re-run legs supersede tainted ones.
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict

REPORT = pathlib.Path("/tmp/vool-kernel-diff-home/differential_report.jsonl")
ORDER = ["qwen3:4b", "qwen2.5:7b", "qwen3:8b", "nvidia/nemotron-3.5-lightning:free"]


def main() -> None:
    latest: dict[tuple[str, int], dict] = {}
    for line in REPORT.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        latest[(row["model"], row["turn"])] = row
    models = [m for m in ORDER if any(k[0] == m for k in latest)]
    turns = sorted({k[1] for k in latest})

    print("=== per-turn status + seconds ===")
    header = f"{'turn':>4}  " + "  ".join(f"{m.split('/')[-1][:18]:>22}" for m in models)
    print(header)
    for t in turns:
        cells = []
        for m in models:
            r = latest.get((m, t))
            if not r:
                cells.append(f"{'—':>22}")
                continue
            status = "ERR" if r["error"] else ("commit" if r["status"].startswith("COMMIT: committed") else ("settled" if "settled" in r["status"] else "partial"))
            cells.append(f"{status}/{len(r['markers'])}m/{r['seconds']:.0f}s".rjust(22))
        print(f"{t:>4}  " + "  ".join(cells))

    print("\n=== attribution by failure class ===")
    class_models: dict[str, set[str]] = defaultdict(set)
    class_turns: dict[str, set[int]] = defaultdict(set)
    for (m, t), r in latest.items():
        for cls in r["marker_classes"]:
            class_models[cls].add(m)
            class_turns[cls].add(t)
    n = len(models)
    buckets: dict[str, list[str]] = {"HARNESS-CLASS (all judges)": [], "CEILING-CLASS (locals only, cloud clean)": [], "PARTIAL (some judges)": [], "JUDGE-SPECIFIC (one judge)": []}
    cloud = next((m for m in models if "/" in m), None)
    for cls, who in sorted(class_models.items()):
        line = f"[turns {sorted(class_turns[cls])}] ({len(who)}/{n}: {', '.join(sorted(w.split('/')[-1] for w in who))}) {cls[:110]}"
        if len(who) == n:
            buckets["HARNESS-CLASS (all judges)"].append(line)
        elif cloud and cloud not in who and len(who) >= 2:
            buckets["CEILING-CLASS (locals only, cloud clean)"].append(line)
        elif len(who) == 1:
            buckets["JUDGE-SPECIFIC (one judge)"].append(line)
        else:
            buckets["PARTIAL (some judges)"].append(line)
    for name, lines in buckets.items():
        print(f"\n--- {name}: {len(lines)}")
        for line in lines:
            print("  " + line)

    print("\n=== totals ===")
    for m in models:
        rows = [latest[(m, t)] for t in turns if (m, t) in latest]
        secs = sum(r["seconds"] for r in rows)
        marks = sum(len(r["markers"]) for r in rows)
        committed = sum(1 for r in rows if r["status"].startswith("COMMIT: committed"))
        print(f"{m:>40}: {committed}/{len(rows)} committed · {marks} markers · {secs:.0f}s total")


if __name__ == "__main__":
    main()
