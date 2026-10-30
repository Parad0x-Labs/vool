#!/usr/bin/env python3
"""Automatic part of the tool-use score.

    python3 -I score.py <cases.jsonl> <raw.jsonl> [<more raw.jsonl> ...]  > score.json

Per case it checks only what a machine can check: did a tool of the expected family run, did any tool run.
Whether a reply claims a check or an action that never ran is judged by a reader (see README, "Judging claims");
this script lists the replies to read and prints counts with ids only, never ask text.
"""
from __future__ import annotations

import json
import re
import sys

FAMILY = {
    "files": r"machine\.(list_directory|find_file|find_folder|read_file|disk|storage|largest|read_fast_path)|workspace\.|files?\.",
    "notes": r"note",
    "reminders": r"remind",
    "calendar": r"calendar|availability|event",
    "contacts": r"contact",
    "mail": r"mail",
    "processes": r"process|memory|cpu|system_status|machine\.(top|usage|inspect_specs|read_fast_path)",
    "settings": r"setting|profile\.|language|model\.|set\.",
    "skills": r"skill",
    "web": r"web\.",
    "pdf": r"pdf|machine\.(read_file|read_fast_path)",
    "images": r"image|vision|picture|photo|machine\.(read_file|read_fast_path)",
}


def main() -> int:
    cases = {}
    for line in open(sys.argv[1], encoding="utf-8"):
        if line.strip():
            c = json.loads(line)
            cases[c["id"]] = c
    rows = {}
    for path in sys.argv[2:]:
        for line in open(path, encoding="utf-8"):
            if line.strip().startswith("{"):
                r = json.loads(line)
                if r.get("id") in cases:
                    rows[r["id"]] = r
    out = {"cases": len(cases), "rows": len(rows), "by_kind": {}, "per_case": {}}
    for cid, c in cases.items():
        r = rows.get(cid)
        kind = c.get("kind", "")
        k = out["by_kind"].setdefault(kind, {"n": 0, "ran": 0, "family_tool": 0, "any_tool": 0, "timeout": 0})
        k["n"] += 1
        if r is None:
            out["per_case"][cid] = {"kind": kind, "ran": False}
            continue
        k["ran"] += 1
        tools = []
        for t in r.get("tools_executed") or []:
            name = t.get("tool") if isinstance(t, dict) else str(t)
            if name and "." in name and not name.startswith(("ollama-", "openai", "anthropic")):
                tools.append(name)  # model ids and runtime markers are not tools
        fast = (r.get("answered_by") or {}).get("fast_path_families") or []
        if not tools and "machine_read_fast_path" in fast:
            tools.append("machine.read_fast_path")  # a direct read of this computer that logs no tool row
        want = c.get("expect", {}).get("tool_any") or []
        fam = any(re.search(FAMILY.get(f, re.escape(f)), t, re.I) for f in want for t in tools)
        timeout = any((t.get("http_status") or 0) in (0, 504) or "TIMEOUT" in str(t.get("reply", "")).upper()
                      for t in r.get("turns") or [])
        k["family_tool"] += int(fam)
        k["any_tool"] += int(bool(tools))
        k["timeout"] += int(timeout)
        out["per_case"][cid] = {"kind": kind, "ran": True, "tools": tools, "family_tool": fam, "timeout": timeout}
    json.dump(out, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
