#!/usr/bin/env python3
"""Operator notes store census: which operator kind and which notes store every wording the suite already uses gets.

A change to how the operator parser or the Notes destination reader decides the notes store moves some wordings from
one kind or store to another and must leave every other wording where it was. The law suites pin the wordings their
authors thought of; this reads EVERY request-like string literal in the test files and the routing corpus, records the
operator parse (kind, target label, destination) and, for a note save, the destination store with the payload read as
data, and compares two runs by wording. The comparison is the blast radius of a store-reading change, measured instead
of argued.

Not collected by pytest (no ``test_`` prefix). Run it against two trees (a base export and the change) over one corpus:

    python tests/operator_notes_store_census.py record --corpus-root <tree> --out base.jsonl
    python tests/operator_notes_store_census.py record --corpus-root <tree> --out change.jsonl
    python tests/operator_notes_store_census.py compare base.jsonl change.jsonl

``record`` imports ``core`` from the interpreter's path, so run it from the tree under test. ``--corpus-root`` names where
the wordings are read from. A wording whose reading raises is recorded with the error, never skipped.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import time
from collections import Counter
from pathlib import Path


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
    if not (6 <= len(text) <= 400) or len(text.split()) < 2 or not any(char.isalpha() for char in text):
        return False
    return not text.startswith(("/", "core.", "tests.", "http://", "https://", "def ", "import ", "from "))


def corpus(root: Path) -> list[tuple[str, str]]:
    """Every request-like string literal (docstrings excluded) in tests/**/*.py and ops/routing_reliability_corpus.py."""
    files = sorted({*root.glob("tests/**/*.py"), root / "ops" / "routing_reliability_corpus.py"})
    seen: dict[str, str] = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        skip = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                if _request_like(node.value):
                    seen.setdefault(node.value, str(path.relative_to(root)))
    return sorted(seen.items())


def reading(wording: str) -> dict[str, object]:
    from core.operator.apple_notes import parse_notes_destination
    from core.operator.notes import parse_note_request
    from core.operator.parser import parse_operator_action_intent

    intent = parse_operator_action_intent(wording)
    if intent is None:
        return {"kind": None}
    row: dict[str, object] = {"kind": intent.kind, "target_label": intent.target_label,
                              "destination_path": intent.destination_path}
    if intent.kind == "save_note":
        body = parse_note_request(wording)["body"]
        row["destination"] = parse_notes_destination(wording, data=(body,))
    return row


def record(args: argparse.Namespace) -> int:
    rows = corpus(Path(args.corpus_root).resolve())
    started = time.monotonic()
    with Path(args.out).open("x", encoding="utf-8") as handle:
        for wording, origin in rows:
            entry: dict[str, object] = {"wording": wording, "origin": origin}
            try:
                entry.update(reading(wording))
            except Exception as exc:  # recorded, never skipped
                entry["error"] = f"{type(exc).__name__}: {exc}"
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps({"wordings": len(rows), "seconds": round(time.monotonic() - started, 1), "out": args.out}))
    return 0


def _load(path: str) -> dict[str, dict]:
    return {entry["wording"]: entry for entry in map(json.loads, Path(path).read_text(encoding="utf-8").splitlines())}


def _store(entry: dict) -> str:
    destination = entry.get("destination") or {}
    return str(destination.get("wants_apple_notes", ""))


_FIELDS = ("kind", "target_label", "destination_path", "destination", "error")


def compare(args: argparse.Namespace) -> int:
    base, change = _load(args.base), _load(args.change)
    shared = sorted(set(base) & set(change))
    moved = [w for w in shared if {k: base[w].get(k) for k in _FIELDS} != {k: change[w].get(k) for k in _FIELDS}]
    transitions = Counter()
    for wording in moved:
        before, after = base[wording], change[wording]
        if before.get("kind") != after.get("kind"):
            transitions[f"kind {before.get('kind')} -> {after.get('kind')}"] += 1
        elif _store(before) != _store(after):
            transitions[f"{before.get('kind')} store {_store(before) or '-'} -> {_store(after) or '-'}"] += 1
        else:
            transitions[f"{before.get('kind')} values changed"] += 1
    report = {
        "base_wordings": len(base), "change_wordings": len(change), "compared": len(shared),
        "only_in_base": len(set(base) - set(change)), "only_in_change": len(set(change) - set(base)),
        "moved": len(moved), "transitions": dict(sorted(transitions.items())),
        "errors_in_base": sum(1 for entry in base.values() if "error" in entry),
        "errors_in_change": sum(1 for entry in change.values() if "error" in entry),
    }
    print(json.dumps(report, indent=2))
    for wording in moved:
        print("-" * 100)
        print("WORDING:", json.dumps(wording, ensure_ascii=False), "  (from", base[wording]["origin"] + ")")
        print("  base:  ", json.dumps({k: base[wording][k] for k in _FIELDS if k in base[wording]}, ensure_ascii=False))
        print("  change:", json.dumps({k: change[wording][k] for k in _FIELDS if k in change[wording]}, ensure_ascii=False))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    rec = commands.add_parser("record")
    rec.add_argument("--corpus-root", required=True)
    rec.add_argument("--out", required=True)
    cmp_ = commands.add_parser("compare")
    cmp_.add_argument("base")
    cmp_.add_argument("change")
    args = parser.parse_args(argv)
    return record(args) if args.command == "record" else compare(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
