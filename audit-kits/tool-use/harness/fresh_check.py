#!/usr/bin/env python3
"""Freshness check for audit asks (frozen; SHA256SUMS covers it).

    python3 -I fresh_check.py <repo> <cases.jsonl> [<other cases.jsonl> ...]

An ask is NOT fresh when any run of 6 words from it (lowercased, punctuation dropped) appears in the repo's tests/,
research/, ops/, eval or benchmark files, or in another job's cases file given after the first. Prints one line per
stale case and exits 1 when any is stale. Run it before the first ask is sent; a stale case is rewritten, not kept.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

N = 6
WORD = re.compile(r"[a-z0-9']+")
SCAN_DIRS = ("tests", "research", "ops", "test-kit", "data")
SCAN_SUFFIXES = {".py", ".json", ".jsonl", ".md", ".txt", ".yaml", ".yml", ".csv"}


def shingles(text: str) -> set[str]:
    words = WORD.findall(text.lower())
    return {" ".join(words[i:i + N]) for i in range(len(words) - N + 1)}


def corpus(repo: Path) -> set[str]:
    seen: set[str] = set()
    for top in SCAN_DIRS:
        root = repo / top
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() in SCAN_SUFFIXES and path.is_file() and path.stat().st_size < 8_000_000:
                seen |= shingles(path.read_text(encoding="utf-8", errors="ignore"))
    return seen


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    repo, mine, others = Path(sys.argv[1]), Path(sys.argv[2]), [Path(p) for p in sys.argv[3:]]
    known = corpus(repo)
    for other in others:
        for line in other.read_text(encoding="utf-8").splitlines():
            if line.strip():
                for words in json.loads(line).get("turns", []):
                    known |= shingles(words)
    stale = 0
    total = 0
    for line in mine.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        total += 1
        hits = sorted({s for words in case.get("turns", []) for s in shingles(words)} & known)
        if hits:
            stale += 1
            print(f"STALE {case['id']}: {hits[:3]}")
    print(f"fresh {total - stale}/{total}")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
