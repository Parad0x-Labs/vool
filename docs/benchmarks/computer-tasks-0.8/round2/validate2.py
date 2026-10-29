#!/usr/bin/env python3
"""Mechanical check of the independent author's asks against PACK.txt. A must_include item passes if it appears
in the pack (case-insensitive), or in the author's own computation line. any_of passes if one alternative passes.
Exemptions: 'absent' asks' any_of (not-found phrases, by design not in the pack) and, for 'change' asks, names that
appear in the ask's own turns (a new folder/file name). Writes round2-asks.json (kept) and VALIDATION.txt."""
import json, sys
pack = open("PACK.txt").read().lower()
s = open("AUTHOR-REPLY.json").read(); asks = json.loads(s[s.find("{"):s.rfind("}") + 1])["asks"]
kept, log = [], []
def ok_item(v, a):
    v = str(v).lower()
    if v in pack or v in str(a.get("computation", "")).lower():
        return True
    if a["category"] == "change" and v in " ".join(a["turns"]).lower():
        return True
    return False
for a in asks:
    bad = []
    for m in a.get("must_include", []):
        if isinstance(m, dict) and "any_of" in m:
            if a["category"] == "absent":
                continue
            if not any(ok_item(x, a) for x in m["any_of"]):
                bad.append(m)
        elif not ok_item(m, a):
            bad.append(m)
    if not a.get("must_include"):
        bad.append("empty must_include")
    if bad:
        log.append("DROP %s (%s): %s" % (a["id"], a["category"], json.dumps(bad)[:200]))
    else:
        kept.append(a)
json.dump(kept, open("round2-asks.json", "w"), indent=1)
import collections
log.append("kept %d of %d: %s" % (len(kept), len(asks), dict(collections.Counter(a["category"] for a in kept))))
open("VALIDATION.txt", "w").write("\n".join(log) + "\n"); print("\n".join(log))
