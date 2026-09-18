"""Turn-by-turn diff of two regression runs, on the turns BOTH have completed.

WHY THIS IS STRICTER THAN THE FIRST VERSION. The first `served()` carried a short list of failure
phrases and called anything else served. It was wrong three times out of three on 2026-08-18:

  K6.2   "Weather in Celsius: no verified conditions ..."      counted as an IMPROVEMENT
  K11.2  a block of raw Python source instead of an answer      counted as an IMPROVEMENT
  Y1.1   every city "TimeoutError: The read operation timed out" counted as an IMPROVEMENT
         (it had been returning real Vilnius and Berlin data)

and it missed failures too -- K15.2 and K26.3 went from live wttr.in readings to "no current
conditions found" and were reported UNCHANGED. A grader that flatters the new run is worse than no
grader, because it is believed.

Two changes. The failure vocabulary now covers what was actually observed, including transport
errors and empty-result wording. And an UPSTREAM OUTAGE is separated from a code regression: a turn
whose failure names a timeout or a dead provider is not evidence about the commit under test, and
saying so is the difference between "we broke the weather lane" and "wttr.in was down".
"""
import json
import pathlib
import sys

FAILED = (
    "could not be answered", "no current weather results", "unable to fetch",
    "i couldn't produce", "did not contain the requested", "couldn't map",
    "i cannot provide", "no observation returned", "no current conditions found",
    "no verified conditions", "i couldn't extract", "i couldn't determine",
    "couldn't verify", "i can't include", "no answer is being shown",
)
OUTAGE = (
    "timeouterror", "read operation timed out", "read timed out", "connection refused",
    "temporarily unavailable", "max retries exceeded", "httpconnectionpool",
    "couldn't get a live model response", "isn't available on this runtime",
)

def load(run):
    out = {}
    for f in ("set11", "set_typos", "set_travel"):
        p = pathlib.Path(run) / f"{f}.json"
        if p.exists():
            with p.open(encoding="utf-8") as handle:
                rows = json.load(handle)
            for r in rows:
                out[f"{f}:{r.get('id')}"] = r
    return out

def state(r):
    """'served' | 'failed' | 'outage' -- outage is not evidence about the commit."""
    a = " ".join(str(r.get("answer") or "").split())
    low = a.lower()
    if r.get("error") or any(m in low for m in OUTAGE):
        return "outage"
    if not a or any(m in low for m in FAILED):
        return "failed"
    # A code block where a direct answer was asked for is a raw-output-contract failure, not an answer.
    if a.startswith("```") and "import " in a:
        return "failed"
    return "served"

old, new = load(sys.argv[1]), load(sys.argv[2])
shared = [k for k in new if k in old]
buckets = {}
for k in shared:
    buckets.setdefault((state(old[k]), state(new[k])), []).append(k)

print(f"  compared {len(shared)} turns both runs completed  (new run has {len(new)})\n")
regressed = buckets.get(("served", "failed"), [])
recovered = buckets.get(("failed", "served"), [])
to_outage = buckets.get(("served", "outage"), []) + buckets.get(("failed", "outage"), [])
print(f"    served -> failed  (REGRESSION)      : {len(regressed)}")
print(f"    failed -> served  (fixed)           : {len(recovered)}")
print(f"    * -> outage (upstream, not the code): {len(to_outage)}")
print(f"    unchanged                           : "
      f"{sum(len(v) for (a,b),v in buckets.items() if a==b)}")
for label, keys in (("REGRESSION", regressed), ("FIXED", recovered)):
    if not keys:
        continue
    print(f"\n  === {label} ===")
    for k in keys:
        print(f"    {k}")
        print(f"      Q  : {' '.join(str(old[k].get('prompt') or '').split())[:100]}")
        print(f"      was: {' '.join(str(old[k].get('answer') or '').split())[:100]}")
        print(f"      now: {' '.join(str(new[k].get('answer') or '').split())[:100]}")
if to_outage:
    print("\n  === went to upstream outage (excluded from the verdict) ===")
    for k in to_outage:
        print(f"    {k:24} {' '.join(str(new[k].get('answer') or '').split())[:76]}")
