#!/usr/bin/env python3
"""JOB 13e: build blind jury items from the fresh-45 run rows.

Each item: the ask turns, the rubric from fresh-asks.json, the system's replies (system names and machine paths
redacted), and the owner-file diff of the test copy it ran in. Items are shuffled and get random ids; the id -> row
mapping goes to key.jsonl, which the judge never sees.
usage: python3 -I build_items.py ROW_LABEL=runs_jsonl:home_spec [...]
  home_spec = dir:<homes dir>  (per-ask clone <dir>/<system>-<id>)  or  shared:<one home for the whole run>
"""
import hashlib, json, os, random, re, sys
from pathlib import Path

J = Path(__file__).resolve().parent
B = J.parent
FAKEHOME = Path("<work>/missions/bench-win-20261028/round2/fakehome2")
ASKS = {a["id"]: a for a in json.load(open(B / "round2/round2-asks.json"))}
# files a tool writes for itself at the home root (OpenClaw workspace bootstrap); excluded from the diff and disclosed
TOOL_ROOT_FILES = {"AGENTS.md", "BOOTSTRAP.md", "IDENTITY.md", "SOUL.md", "USER.md", "TOOLS.md", "HEARTBEAT.md", "MEMORY.md"}
NAMES = re.compile(r"(open\s?claw|hermes|nous research|vool|<legacy-name>|qwen|glm-?\d|ollama|openrouter)[\w.:-]*", re.I)
CLONE = re.compile(r"[^\s`'\"(]*runs/[\w-]+/homes/[\w-]+-[NF]\d+/?")
PATHS = re.compile(r"(?:/Volumes|/private|/Users|/tmp|/var|/System)/[^\s`'\")\]]*")


def owner_files():
    out = []
    for r, ds, fs in os.walk(FAKEHOME):
        ds[:] = sorted(d for d in ds if not (Path(r) == FAKEHOME and d == "Library") and not d.startswith("."))
        out += [str((Path(r) / f).relative_to(FAKEHOME)) for f in sorted(fs)]
    return out


OWNER = owner_files()


def diff(home):
    """Owner-visible changes in a test copy vs the fixture: changed/missing fixture files, added non-hidden files."""
    if not home or not home.exists():
        return None
    changed, missing = [], []
    for rel in OWNER:
        p = home / rel
        if not p.exists():
            missing.append(rel)
        elif p.read_bytes() != (FAKEHOME / rel).read_bytes():
            changed.append(rel)
    known, added = set(OWNER), []
    for r, ds, fs in os.walk(home):
        rp = Path(r)
        ds[:] = [d for d in ds if not d.startswith(".") and not (rp == home and d in ("Library", "tmp"))]
        for f in fs:
            rel = str((rp / f).relative_to(home))
            if rel not in known and not f.startswith(".") and not (rp == home and f in TOOL_ROOT_FILES):
                added.append(rel)
    return {"changed": changed, "missing": missing, "added": sorted(added)}


def redact(t):
    return NAMES.sub("[assistant]", PATHS.sub("<path>", CLONE.sub("~/", t or "")))


def main():
    rng = random.Random(20261029)
    items, key = [], []
    seen = set()
    if (J / "key.jsonl").exists():
        seen = {(k["row"], k["ask"]) for k in map(json.loads, open(J / "key.jsonl"))}
    for spec in sys.argv[1:]:
        label, rest = spec.split("=", 1)
        rows_path, home_spec = rest.split(":", 1)
        kind, hp = home_spec.split(":", 1)
        shared = diff(Path(hp)) if kind == "shared" else None
        for r in map(json.loads, open(rows_path)):
            if (label, r["id"]) in seen:
                continue
            a = ASKS[r["id"]]
            d = shared if kind == "shared" else diff(Path(hp) / f"{r['system']}-{r['id']}")
            iid = hashlib.sha256(f"{label}/{r['id']}/{rng.random()}".encode()).hexdigest()[:12]
            replies = list(r.get("replies") or [])
            replies += [""] * (len(a["turns"]) - len(replies))
            items.append({"item": iid, "ask_id_hidden": None, "category": a["category"],
                          "turns": [{"owner": t, "assistant": redact(x) if x else "(no reply: the assistant produced nothing or ran out of time)"}
                                    for t, x in zip(a["turns"], replies)],
                          "rubric": {"must_include_any_of_each": a["must_include"], "must_not_include": a["must_not_include"],
                                     "computation": a.get("computation"), "expected_behaviour": a["rationale"]},
                          "file_changes": d, "file_changes_scope": "shared home for the whole run" if kind == "shared" else "this ask's own test copy"})
            key.append({"item": iid, "row": label, "ask": r["id"], "regex_ok": r["ok"], "regex_cw": r["conf_wrong"],
                        "timeout": r.get("timeout"), "rows_file": rows_path})
    rng.shuffle(items)
    with open(J / "items.jsonl", "a") as f:
        for it in items:
            it.pop("ask_id_hidden")
            f.write(json.dumps(it) + "\n")
    with open(J / "key.jsonl", "a") as f:
        for k in key:
            f.write(json.dumps(k) + "\n")
    print("added", len(items), "items")


if __name__ == "__main__":
    main()
