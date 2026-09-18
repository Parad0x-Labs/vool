#!/usr/bin/env python3
"""Execution-grain census: how `demand_ownership.execution_unit_spans` groups every wording the suite already uses.

A change to the execution grain moves a boundary for some wordings and must leave every other wording where it
was. The law suites pin the wordings their authors thought of; this reads EVERY request-like string literal in
the test files that exercise the demand grain or the served turn, records the execution units and the lanes
reading each unit, and compares two runs by wording. The comparison is the blast radius of a grain change,
measured instead of argued.

Not collected by pytest (no ``test_`` prefix). Run it against two trees (a base export and the change) with
the same corpus, then compare:

    python tests/execution_grain_census.py record --corpus-root <tree> --out base.jsonl
    python tests/execution_grain_census.py record --corpus-root <tree> --out fix.jsonl
    python tests/execution_grain_census.py compare base.jsonl fix.jsonl

``record`` imports ``core`` from the interpreter's path, so run it from the tree under test. ``--corpus-root``
names where the wordings are read from, so both runs can read one corpus. A wording whose reading raises is
recorded with the error, never skipped.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import time
from pathlib import Path

#: A test file joins the corpus when its source names one of these: the grain, the interpretation, the lanes'
#: whole-turn laws, or a served turn entry point.
_CORPUS_MARKERS = (
    "demand_ownership",
    "answer_coverage",
    "execution_units",
    "execution_unit_spans",
    "demand_units",
    "demand_coverage",
    "interpret_request",
    "lane_may_claim_whole_turn",
    "run_once",
    "handle_turn",
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _request_like(value: str) -> bool:
    text = value.strip()
    if not (6 <= len(text) <= 400):
        return False
    if len(text.split()) < 2 or not any(char.isalpha() for char in text):
        return False
    # identifiers, paths, format strings and code are not wordings
    return not (text.startswith(("/", "core.", "tests.", "http://", "https://", "def ", "import ", "from ")))


def corpus(root: Path) -> list[tuple[str, str]]:
    files = sorted({*root.glob("tests/*.py"), *root.glob("tests/pa_beta_gate/*.py")})
    seen: dict[str, str] = {}
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(marker in source for marker in _CORPUS_MARKERS):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        skip = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                if _request_like(node.value):
                    seen.setdefault(node.value, str(path.relative_to(root)))
    return sorted((wording, origin) for wording, origin in seen.items())


def record(args: argparse.Namespace) -> int:
    from core.agent_runtime.demand_ownership import demand_coverage, execution_unit_spans

    rows = corpus(Path(args.corpus_root).resolve())
    if args.limit:
        rows = rows[: args.limit]
    started = time.monotonic()
    out = Path(args.out)
    with out.open("x", encoding="utf-8") as handle:
        for wording, origin in rows:
            entry: dict[str, object] = {"wording": wording, "origin": origin}
            try:
                spans = execution_unit_spans(wording)
                entry["units"] = [[span.text, list(span.member_unit_ids)] for span in spans]
                coverage = demand_coverage(wording)
                entry["lanes"] = [sorted(lanes) for lanes in coverage.per_unit_lanes]
            except Exception as exc:  # recorded, never skipped
                entry["error"] = f"{type(exc).__name__}: {exc}"
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps({"wordings": len(rows), "seconds": round(time.monotonic() - started, 1), "out": str(out)}))
    return 0


def _load(path: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            rows[entry["wording"]] = entry
    return rows


def _category(base: dict, change: dict) -> tuple[str, list[str]]:
    """How a wording moved, and the flags that make a move worth reading first."""
    if "error" in base or "error" in change:
        return "error-changed", []
    base_units = [text for text, _members in base["units"]]
    change_units = [text for text, _members in change["units"]]
    if base_units == change_units:
        return "lanes-only", []
    if len(change_units) > len(base_units):
        return "split", []
    if len(change_units) == len(base_units):
        return "regrouped", []
    flags = []
    if base_units and change_units and change_units[0] != base_units[0]:
        flags.append("first-unit-grew")
    base_lanes = [tuple(lanes) for lanes in base["lanes"]]
    if len(change_units) == 1 and change["lanes"][0] and len(set(base_lanes)) > 1:
        # one execution unit now, read by a lane, where the base read several units apart
        flags.append("one-lane-now-reads-the-whole-turn")
    for text, lanes in zip(change_units, change["lanes"], strict=True):
        members = [set(base_l) for base_text, base_l in zip(base_units, base["lanes"], strict=True) if base_text in text]
        if set(lanes) - set().union(*members):
            # the merge made a claim no member made: the fused reading R1e exists to refuse
            flags.append("a-lane-reads-a-merged-unit-no-member-was-read-by")
            break
    return "merged", flags


def compare(args: argparse.Namespace) -> int:
    base, change = _load(args.base), _load(args.change)
    shared = sorted(set(base) & set(change))
    moved = [wording for wording in shared if {k: base[wording].get(k) for k in ("units", "lanes", "error")}
             != {k: change[wording].get(k) for k in ("units", "lanes", "error")}]
    categories: dict[str, list[str]] = {}
    flagged: dict[str, list[str]] = {}
    for wording in moved:
        category, flags = _category(base[wording], change[wording])
        categories.setdefault(category, []).append(wording)
        for flag in flags:
            flagged.setdefault(flag, []).append(wording)
    report = {
        "base_wordings": len(base),
        "change_wordings": len(change),
        "compared": len(shared),
        "only_in_base": len(set(base) - set(change)),
        "only_in_change": len(set(change) - set(base)),
        "moved": len(moved),
        "moved_by_category": {name: len(rows) for name, rows in sorted(categories.items())},
        "merged_flags": {name: len(rows) for name, rows in sorted(flagged.items())},
        "errors_in_base": sum(1 for entry in base.values() if "error" in entry),
        "errors_in_change": sum(1 for entry in change.values() if "error" in entry),
    }
    print(json.dumps(report, indent=2))
    for category, rows in sorted(categories.items()):
        print("=" * 100)
        print("CATEGORY:", category, len(rows))
        for wording in rows:
            _flags = _category(base[wording], change[wording])[1]
            print("-" * 100)
            print("WORDING:", json.dumps(wording, ensure_ascii=False), "  (from", base[wording]["origin"] + ")", _flags or "")
            print("  base:  ", json.dumps({k: base[wording].get(k) for k in ("units", "lanes", "error") if k in base[wording]}, ensure_ascii=False))
            print("  change:", json.dumps({k: change[wording].get(k) for k in ("units", "lanes", "error") if k in change[wording]}, ensure_ascii=False))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    rec = commands.add_parser("record")
    rec.add_argument("--corpus-root", required=True)
    rec.add_argument("--out", required=True)
    rec.add_argument("--limit", type=int, default=0)
    cmp_ = commands.add_parser("compare")
    cmp_.add_argument("base")
    cmp_.add_argument("change")
    args = parser.parse_args(argv)
    return record(args) if args.command == "record" else compare(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
