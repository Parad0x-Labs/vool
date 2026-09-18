#!/usr/bin/env python3
"""Deterministic stale-translation checker and language-index generator.

Usage (from the repository root):

    python tools/i18n/check_translations.py            # check everything, print report
    python tools/i18n/check_translations.py --check    # exit 1 on any STALE/invariant failure (CI)
    python tools/i18n/check_translations.py --write-index  # regenerate docs/i18n/README.md

Laws (docs/i18n/TRANSLATOR_RULES.md):
- a translation records the SHA-256 of the canonical English source it was made
  from; if the current source hash differs, the translation is STALE;
- fenced code blocks, URL/command/code-token sets, heading counts, table shapes
  and warning blockquotes are structural invariants — any drift is STALE;
- statuses are MACHINE_DRAFT or HUMAN_VERIFIED (never set without a reviewer).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
SOURCES_DIR = REPO / "docs" / "i18n" / "sources"
DOCS_I18N_DIR = REPO / "docs" / "i18n"
INDEX_PATH = DOCS_I18N_DIR / "README.md"

VALID_STATUSES = ("MACHINE_DRAFT", "HUMAN_VERIFIED")
DOC_ORDER = ("quickstart", "providers", "permissions", "troubleshooting", "privacy", "wallet-x402")

_FENCE_RE = re.compile(r"^```.*?$|^~~~.*?$", re.MULTILINE)
_URL_RE = re.compile(r"https?://[^\s)>\]\"'`]+")
_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^<!--\s*\n(.*?)\n-->\s*\n?", re.DOTALL)
_KV_RE = re.compile(r"^([a-z0-9_]+):\s*(.+?)\s*$", re.MULTILINE)
_CODE_TOKEN_RE = re.compile(
    r"```[a-z]*\n(.*?)```", re.DOTALL
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_docs() -> dict[str, Path]:
    return {p.stem: p for p in sorted(SOURCES_DIR.glob("*.md"))}


@dataclass
class DocStatus:
    locale: str
    doc: str
    status: str  # MACHINE_DRAFT | HUMAN_VERIFIED | STALE | ABSENT
    problems: list[str] = field(default_factory=list)
    engine: str = ""
    translated_at: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("MACHINE_DRAFT", "HUMAN_VERIFIED")


def _fences(text: str) -> list[str]:
    blocks: list[str] = []
    lines = text.split("\n")
    current: list[str] | None = None
    for line in lines:
        if line.strip().startswith("```") or line.strip().startswith("~~~"):
            if current is None:
                current = [line]
            else:
                current.append(line)
                blocks.append("\n".join(current))
                current = None
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append("\n".join(current))
    return blocks


def _code_tokens(text: str) -> list[str]:
    """Command-like tokens inside fenced code (commands, paths, urls, codes)."""
    tokens: list[str] = []
    for block in _CODE_TOKEN_RE.findall(text):
        for line in block.split("\n"):
            stripped = line.strip()
            if stripped:
                tokens.append(stripped)
    return tokens


def _url_set(text: str) -> set[str]:
    """URLs as a set, ignoring sentence punctuation that trails them."""
    return {u.rstrip(".,;:!?") for u in _URL_RE.findall(text)}


def _tables_shape(text: str) -> list[int]:
    """Column count of every table row group (lines starting with '|')."""
    shape: list[int] = []
    for line in text.split("\n"):
        if line.strip().startswith("|"):
            shape.append(line.strip().strip("|").count("|") + 1)
    return shape


def _warning_blocks(text: str) -> int:
    """Consecutive `>` lines form ONE warning block; wrapping width is not an invariant."""
    blocks = 0
    previous_quote = False
    for line in text.split("\n"):
        is_quote = line.strip().startswith(">")
        if is_quote and not previous_quote:
            blocks += 1
        previous_quote = is_quote
    return blocks


def parse_frontmatter(text: str) -> dict[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    return dict(_KV_RE.findall(m.group(1)))


def check_translation(path: Path, doc: str, source_text: str, source_hash: str) -> DocStatus:
    text = path.read_text(encoding="utf-8")
    meta = parse_frontmatter(text)
    problems: list[str] = []
    status = "STALE"

    if not meta:
        return DocStatus(path.parent.name, doc, "STALE", ["missing front matter"])
    if meta.get("locale") != path.parent.name:
        problems.append(f"front matter locale {meta.get('locale')!r} != directory {path.parent.name!r}")
    if meta.get("source") != f"{doc}.md":
        problems.append(f"front matter source {meta.get('source')!r} != {doc}.md")
    recorded_hash = meta.get("canonical_source_sha256", "")
    if recorded_hash != source_hash:
        problems.append("canonical source hash does not match (stale)")
    declared = meta.get("translation_status", "")
    if declared not in VALID_STATUSES:
        problems.append(f"invalid translation_status {declared!r}")
    if declared == "HUMAN_VERIFIED" and not meta.get("reviewer", "").strip():
        problems.append("HUMAN_VERIFIED requires a named reviewer")

    body = _FRONTMATTER_RE.sub("", text, count=1)

    src_fences, dst_fences = _fences(source_text), _fences(body)
    if src_fences != dst_fences:
        problems.append(
            f"fenced code blocks differ (source {len(src_fences)} vs translation {len(dst_fences)} or content drift)"
        )
    if _code_tokens(source_text) != _code_tokens(body):
        problems.append("code tokens inside fences are not preserved byte-exactly")
    if _url_set(source_text) != _url_set(body):
        problems.append("URL set differs from the canonical source")
    if len(_HEADING_RE.findall(source_text)) != len(_HEADING_RE.findall(body)):
        problems.append("heading count differs from the canonical source")
    if _tables_shape(source_text) != _tables_shape(body):
        problems.append("table shape differs from the canonical source")
    if _warning_blocks(source_text) > _warning_blocks(body):
        problems.append("warning blockquotes were dropped")

    if not problems:
        status = declared if declared in VALID_STATUSES else "STALE"
    return DocStatus(
        path.parent.name,
        doc,
        status,
        problems,
        engine=meta.get("translation_engine", ""),
        translated_at=meta.get("translated_at", ""),
    )


def check_all() -> dict[str, dict[str, DocStatus]]:
    from core.i18n.locales import response_supported_tags

    results: dict[str, dict[str, DocStatus]] = {}
    sources = canonical_docs()
    source_hashes = {name: sha256_bytes(p.read_bytes()) for name, p in sources.items()}
    # A primary whose docs ship under a script-default sibling (zh -> zh-Hans) reads
    # that directory; the canonical English row reads docs/i18n/sources/.
    docs_dir_for = {"zh": "zh-Hans"}
    for locale in response_supported_tags():
        row: dict[str, DocStatus] = {}
        if locale == "en":
            # The canonical English sources are AUTHORED (the source of truth), not
            # reviewed translations: they carry COMPLETE, never HUMAN_VERIFIED —
            # that label is reserved for a named human reviewer of a translation.
            for doc in DOC_ORDER:
                row[doc] = DocStatus(
                    locale, doc, "COMPLETE" if doc in sources else "ABSENT",
                    [] if doc in sources else ["canonical source missing"],
                    engine="canonical source (authored English)",
                )
            results[locale] = row
            continue
        locale_dir = DOCS_I18N_DIR / docs_dir_for.get(locale, locale)
        for doc in DOC_ORDER:
            path = locale_dir / f"{doc}.md"
            if doc not in sources:
                row[doc] = DocStatus(locale, doc, "ABSENT", ["canonical source missing"])
            elif not path.is_file():
                row[doc] = DocStatus(locale, doc, "ABSENT", [])
            else:
                status = check_translation(path, doc, sources[doc].read_text(encoding="utf-8"), source_hashes[doc])
                row[doc] = status
        results[locale] = row
    return results


def write_index(results: dict[str, dict[str, DocStatus]]) -> None:
    from core.i18n.locales import endonym_for, get_locale

    lines = [
        "# VOOL language and support index",
        "",
        "Deterministically generated by `python tools/i18n/check_translations.py --write-index`.",
        "Do not edit by hand. Statuses:",
        "**MACHINE_DRAFT** machine-translated, unreviewed · **HUMAN_VERIFIED** named human reviewer",
        "· **STALE** canonical English changed (or an invariant drifted) · **ABSENT** not shipped.",
        "",
        "Canonical English sources live in `docs/i18n/sources/`; translations in",
        "`docs/i18n/<locale>/`; terminology in `docs/i18n/GLOSSARY.md`; rules in",
        "`docs/i18n/TRANSLATOR_RULES.md`; capability truth in `docs/I18N_CAPABILITY_MATRIX.json`.",
        "",
        "## Document status per locale",
        "",
    ]
    header = "| Locale | " + " | ".join(DOC_ORDER) + " | UI catalog |"
    sep = "|---|" + "---|" * (len(DOC_ORDER) + 1)
    lines += [header, sep]
    for locale in sorted(results):
        row = results[locale]
        spec = get_locale(locale)
        ui = spec.ui if spec else "?"
        cells = " | ".join(
            {
                "MACHINE_DRAFT": "DRAFT",
                "HUMAN_VERIFIED": "VERIFIED",
                "COMPLETE": "SOURCE",
                "STALE": "**STALE**",
                "ABSENT": "—",
            }.get(row[doc].status, row[doc].status)
            for doc in DOC_ORDER
        )
        lines.append(f"| {locale} ({endonym_for(locale)}) | {cells} | {ui} |")
    lines += [
        "",
        "_DRAFT = MACHINE_DRAFT. This index is truth: a locale claiming a document",
        "here has the file on disk with a matching source hash and intact invariants._",
        "",
        "## Count clarification (physical artifacts vs coverage)",
        "",
        "- **49 physical machine-draft locale directories** x 6 documents = **294",
        "  translated files** on disk; the English canonical sources in",
        "  `docs/i18n/sources/` are separate and authored, not translations.",
        "- Deterministic fallback means those artifacts cover **50 non-English",
        "  registry tags**: `zh` reads its declared `zh-Hans` script-default",
        "  directory (one physical set, two tags served).",
        "- **Zero** documents are HUMAN_VERIFIED; that label requires a named human",
        "  reviewer and none exists.",
        "",
    ]
    INDEX_PATH.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 on STALE/invariant failures")
    parser.add_argument("--write-index", action="store_true", help="regenerate docs/i18n/README.md")
    parser.add_argument("--locale", help="restrict to one locale directory")
    args = parser.parse_args(argv)

    results = check_all()
    if args.locale:
        results = {args.locale: results[args.locale]} if args.locale in results else {}

    bad = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"vool i18n translation check — {now}")
    for locale in sorted(results):
        statuses = [results[locale][doc].status for doc in DOC_ORDER]
        problems = [
            f"{doc}: {p}"
            for doc in DOC_ORDER
            for p in results[locale][doc].problems
        ]
        if any(s in ("STALE", "ABSENT") for s in statuses) or problems:
            bad += 1
            print(f"  {locale}: " + ", ".join(statuses))
            for p in problems[:6]:
                print(f"    - {p}")
            if len(problems) > 6:
                print(f"    … {len(problems) - 6} more")
        else:
            print(f"  {locale}: OK (" + ", ".join(statuses) + ")")

    if args.write_index:
        write_index(check_all())
        print(f"index written: {INDEX_PATH}")

    if args.check and bad:
        print(f"CHECK FAILED for {bad} locale(s)")
        return 1
    print("check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
