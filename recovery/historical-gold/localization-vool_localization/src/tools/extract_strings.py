#!/usr/bin/env python3
"""Mechanical inventory of real user-facing strings in the VOOL repo.

Pass 001 scope: scan the known UI surfaces (Python-rendered HTML dashboards,
CLI/chat apps, public website, installer UX) and dump every candidate string
with file, line, surface classification, and a suggested registry key.

This is an *inventory* tool, not a migration tool: it writes nothing back into
canonical code. Output is JSON (+ optional markdown) for the audit step.

Usage:
    python3 localization/tools/extract_strings.py [--repo ROOT] [--out FILE] [--markdown]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_REPO = Path(__file__).resolve().parents[2]

# Surface -> list of glob patterns relative to repo root.
SURFACES: dict[str, list[str]] = {
    "desktop.dashboard": ["core/dashboard/**/*.py", "core/earnings_page.py", "core/null_browser_page.py"],
    "desktop.cli": ["apps/*.py"],
    "web.public": ["Beta2_Website/core/*.py", "Beta2_Website/*.py"],
    "installer": ["installer/*.sh", "installer/*.bat", "installer/*.ps1"],
}

# HTML-ish text between tags, e.g. >What matters now<
RE_HTML_TEXT = re.compile(r">([A-Z][^<>{}\n]{2,120})<")
# print("literal") / print(f"literal ... {var}")
RE_PRINT = re.compile(r"\bprint\(\s*f?\"((?:[^\"\\]|\\.){3,160})\"")
# shell installer echoes
RE_ECHO = re.compile(r"^\s*(?:echo|ECHO)\s+\"?([A-Za-z][^\"\n]{4,120})\"?", re.MULTILINE)

# Strings that look like code, paths, or log noise rather than user copy.
NOISE_RE = re.compile(
    r"^[\s\d\W]*$"                 # punctuation only
    r"|\.(py|json|html|css|js|sh|bat)$"
    r"|^(SELECT|INSERT|http|/|\.|\{)"
    r"|%[sd]|VOOL_[A-Z_]+|^\w+://"
)


def _clean(s: str) -> str:
    s = s.replace("\\n", " ").replace('\\"', '"').strip()
    return re.sub(r"\s+", " ", s)


def _is_noise(s: str) -> bool:
    if len(s) < 3 or len(s) > 140:
        return True
    if NOISE_RE.search(s):
        return True
    words = s.split()
    return len(words) < 1 or not any(c.isalpha() for c in s)


def _suggested_key(surface: str, text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40].strip("_")
    area = surface.split(".", 1)[-1]
    return f"{area}.{slug}"


def extract(repo: Path) -> list[dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for surface, patterns in SURFACES.items():
        for pattern in patterns:
            for path in sorted(repo.glob(pattern)):
                rel = str(path.relative_to(repo))
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for lineno, line in enumerate(lines, start=1):
                    candidates: list[str] = []
                    if surface == "installer":
                        m = RE_ECHO.match(line)
                        if m:
                            candidates.append(m.group(1))
                    else:
                        candidates.extend(RE_HTML_TEXT.findall(line))
                        m = RE_PRINT.search(line)
                        if m:
                            candidates.append(m.group(1))
                    for raw in candidates:
                        text = _clean(raw)
                        if _is_noise(text):
                            continue
                        rec_id = (rel + ":" + text[:80], surface)
                        # multi-line HTML literals repeat; keep first occurrence
                        if rec_id not in found:
                            found[rec_id] = {
                                "key_hint": _suggested_key(surface, text),
                                "source_locale": "en",
                                "surface": surface,
                                "text": text,
                                "file": rel,
                                "line": lineno,
                            }
    return sorted(found.values(), key=lambda r: (r["surface"], r["file"], r["line"]))


def to_markdown(records: list[dict[str, Any]]) -> str:
    out = ["# VOOL string inventory — mechanical extraction (pass-001)", ""]
    by_surface: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_surface.setdefault(r["surface"], []).append(r)
    for surface in sorted(by_surface):
        rows = by_surface[surface]
        out += [f"## {surface} — {len(rows)} strings", "",
                "| suggested key | text | file:line |", "|---|---|---|"]
        for r in rows:
            out.append(f"| `{r['key_hint']}` | {r['text'].replace('|', chr(92)+'|')} | `{r['file']}:{r['line']}` |")
        out.append("")
    total = len(records)
    out.insert(1, f"Total: {total} unique strings across {len(by_surface)} surfaces.")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--out", type=Path, default=None, help="write JSON here")
    ap.add_argument("--markdown", action="store_true", help="also write .md next to --out")
    args = ap.parse_args()

    records = extract(args.repo)
    payload = {"tool": "vool-extract-strings", "pass": "001", "repo": str(args.repo),
               "count": len(records), "records": records}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {len(records)} strings -> {args.out}")
        if args.markdown:
            md_path = args.out.with_suffix(".md")
            md_path.write_text(to_markdown(records), encoding="utf-8")
            print(f"wrote {md_path}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
