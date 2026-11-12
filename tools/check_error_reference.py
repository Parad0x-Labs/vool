"""Deterministic error-reference and coverage checker: one command, no model, no secrets.

``PYTHONPATH=. python -m tools.check_error_reference`` (add ``--json`` for machine output)

What it verifies (same input -> same output, every time):

1. GENERATED REFERENCE IS CURRENT -- ``docs/ERROR_BOOK.md`` (published) and
   ``docs/FAULT_BOUNDARIES.md`` (developer view) are byte-identical to what
   ``tools.generate_error_book`` emits from the live catalog.
2. PER-CODE CONTRACT IS COMPLETE -- every catalog code has a title, an effect state, a
   user message, an operator action, and exactly one per-code section in the book whose
   heading is the code (the stable documentation anchor).
3. NAVIGATION IS COHERENT -- every page SUMMARY.md lists exists; every page in a public
   docs section is listed (an unlisted page never publishes); relative links inside
   listed pages resolve, and every link to a section of the Error Book (``#<anchor>``,
   from another page or from the book itself) names a section the book has. A link labelled
   with a code lands on that code's section, and a title written beside it is the catalog's.
4. EMITTED-CODE COVERAGE, WITH ITS DENOMINATOR STATED -- the codes the wallet modules
   emit (statically extracted) must all be declared in the catalog, and every declared
   wallet code must have an emission site; every boundary code listed in
   ``core/faults/boundaries.py`` must appear in the module(s) that declare it.

The emitted-coverage method is STATIC and therefore bounded, by design: it proves the
declared vocabulary covers what the extraction can see (every ``wallet_fault`` /
``_fault`` / ``WalletFault`` call site in ``core/wallet`` and the wallet API, and the
boundary registry against its declared_in modules). It does not prove that no future call
site can emit an unlisted code, and it claims nothing of the kind. A new boundary extends
the extraction as part of being added.

Exit status: 0 when every check passes, 1 with a FAILING list otherwise.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

from core.faults.boundaries import BOUNDARIES
from core.faults.catalog import all_codes, all_specs, doc_anchor, export_catalog
from tools.generate_error_book import build_error_book_markdown, build_fault_boundaries_markdown

REPO = pathlib.Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
SUMMARY = DOCS / "SUMMARY.md"

_SUMMARY_LINK_RE = re.compile(r"^\s*\*\s*\[[^\]]+\]\(([^)#]+?)(?:#[^)]*)?\)\s*$", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\]\(([^)#\s]+?)(?:#[^)\s]*)?\)")
#: A link to a section: an optional page, then ``#`` and the fragment.
_FRAGMENT_LINK_RE = re.compile(r"\]\(([^)#\s]*)#([^)\s]+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_NAMED_ANCHOR_RE = re.compile(r'<a name="([^"]+)"></a>')
#: A code named as a link to a section, and in a table row the next cell (its title).
_CODE_LINK_RE = re.compile(r"\[`([a-z0-9_]+)`\]\(([^)#\s]*)#([^)\s]+)\)(?:\s*\|\s*([^|\n]+?)\s*\|)?")
_PUBLIC_SECTIONS = ("getting-started", "concepts", "guides", "reference", "trust", "help")

#: The wallet family's emission spellings. Each takes the code as its first positional
#: argument: a string literal, or a module-level constant defined as one (authority.py names
#: its codes LEGACY_RETIRED / EXPORT_REFUSED so the census and the raise site share one name).
WALLET_EMITTER_RE = re.compile(
    r"\b(?:wallet_fault|_fault|WalletFault)\(\s*(?:\"([a-z0-9_]+)\"|([A-Z][A-Z0-9_]+))"
)
WALLET_CONST_DEF_RE = re.compile(
    r"^([A-Z][A-Z0-9_]+)\s*=\s*\"((?:wallet|evm|x402)_[a-z0-9_]+)\"", re.MULTILINE
)
#: The wallet emission surface: every module of the wallet package, plus its HTTP door.
WALLET_SURFACE = ("core/wallet", "core/web/api/wallet_api.py")
_WALLET_FAMILY = ("wallet_", "evm_", "x402_")


def _wallet_sources() -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for rel in WALLET_SURFACE:
        target = REPO / rel
        if target.is_dir():
            paths.extend(sorted(target.rglob("*.py")))
        elif target.is_file():
            paths.append(target)
    return paths


def emitted_wallet_codes() -> dict[str, str]:
    """code -> first emission site (``path:line``), in a stable order."""
    emitted: dict[str, str] = {}
    for path in _wallet_sources():
        source = path.read_text(encoding="utf-8")
        constants = dict(WALLET_CONST_DEF_RE.findall(source))
        rel = path.relative_to(REPO).as_posix()
        for match in WALLET_EMITTER_RE.finditer(source):
            literal, const = match.group(1), match.group(2)
            code = literal if literal else constants.get(const, "")
            if code.startswith(_WALLET_FAMILY):
                line = source.count("\n", 0, match.start()) + 1
                emitted.setdefault(code, f"{rel}:{line}")
    return emitted


def _summary_pages() -> list[pathlib.Path]:
    pages: list[pathlib.Path] = []
    for match in _SUMMARY_LINK_RE.finditer(SUMMARY.read_text(encoding="utf-8")):
        raw = match.group(1).strip()
        if raw.startswith(("http://", "https://", "mailto:")):
            continue
        pages.append(DOCS / raw)
    return pages


def check_generated_files_current(problems: list[str]) -> None:
    for name, build in (("ERROR_BOOK.md", build_error_book_markdown),
                        ("FAULT_BOUNDARIES.md", build_fault_boundaries_markdown)):
        path = DOCS / name
        if not path.is_file():
            problems.append(f"generated file missing: docs/{name} (run: python -m tools.generate_error_book)")
        elif path.read_text(encoding="utf-8") != build():
            problems.append(f"docs/{name} is stale vs core/faults (run: python -m tools.generate_error_book)")


def check_per_code_contract(problems: list[str]) -> dict[str, int]:
    book_path = DOCS / "ERROR_BOOK.md"
    book = book_path.read_text(encoding="utf-8") if book_path.is_file() else ""
    stats = {"codes": len(all_codes()), "sections": 0}
    for spec in all_specs():
        if not spec.title.strip():
            problems.append(f"{spec.code}: no title")
        if not spec.effect:
            problems.append(f"{spec.code}: no effect state")
        if not spec.user_message.strip():
            problems.append(f"{spec.code}: no user message")
        if not spec.operator_action.strip():
            problems.append(f"{spec.code}: no operator action")
        count = len(re.findall(rf"^### {re.escape(spec.code)}\s*$", book, re.MULTILINE))
        if count == 0:
            problems.append(f"{spec.code}: no per-code section (stable anchor) in ERROR_BOOK")
        elif count > 1:
            problems.append(f"{spec.code}: {count} per-code sections in ERROR_BOOK (duplicate anchor)")
        else:
            stats["sections"] += 1
        if book and book.count(f'<a name="{doc_anchor(spec.code)}"></a>') != 1:
            problems.append(f"{spec.code}: its section lacks the one named anchor Markdown viewers land on")
    if any(entry.get("doc_anchor") != doc_anchor(str(entry.get("code") or ""))
           for entry in export_catalog()["faults"]):
        problems.append("catalog export: a doc_anchor diverged from its code")
    return stats


def check_navigation(problems: list[str]) -> int:
    pages = _summary_pages()
    missing = [str(p.relative_to(DOCS)) for p in pages if not p.is_file()]
    if missing:
        problems.append(f"SUMMARY.md lists pages that do not exist: {missing}")
    listed = {p.resolve() for p in pages}
    unlisted: list[str] = []
    for section in _PUBLIC_SECTIONS:
        for md in sorted((DOCS / section).rglob("*.md")):
            if md.resolve() not in listed:
                unlisted.append(str(md.relative_to(DOCS)))
    if unlisted:
        problems.append(f"pages exist in public sections but are not in SUMMARY.md (they would never publish): {unlisted}")
    broken: list[str] = []
    for page in pages:
        if not page.is_file():
            continue
        for match in _MD_LINK_RE.finditer(page.read_text(encoding="utf-8")):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (page.parent / target).resolve().exists():
                broken.append(f"{page.relative_to(DOCS)} -> {target}")
    if broken:
        problems.append(f"dangling relative links: {broken}")
    check_error_book_fragments(problems, pages)
    return len([p for p in pages if p.is_file()])


def error_book_anchors(book: str) -> set[str]:
    """Every fragment that lands somewhere in the book: the docs site's id for each heading,
    and the named anchors that carry the same value for Markdown viewers."""
    from tools.docs.build_docs import slugify

    return {slugify(title) for title in _HEADING_RE.findall(book)} | set(_NAMED_ANCHOR_RE.findall(book))


def check_error_book_fragments(problems: list[str], pages: list[pathlib.Path]) -> None:
    """A link to a section the book does not have opens the book at its top: the reader is
    told "see this code" and shown something else. Refused like a dangling page link."""
    book_path = (DOCS / "ERROR_BOOK.md").resolve()
    if not book_path.is_file():
        return
    anchors = error_book_anchors(book_path.read_text(encoding="utf-8"))
    sources = {p.resolve(): p for p in [*pages, book_path] if p.is_file()}
    bad: list[str] = []
    for resolved, page in sorted(sources.items()):
        for match in _FRAGMENT_LINK_RE.finditer(page.read_text(encoding="utf-8")):
            target, fragment = match.group(1), match.group(2)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            lands_on = (page.parent / target).resolve() if target else resolved
            if lands_on != book_path:
                continue
            if fragment not in anchors:
                bad.append(f"{page.relative_to(DOCS)} -> {target}#{fragment}")
    if bad:
        problems.append(f"links to Error Book sections that do not exist: {bad}")
    check_error_book_code_links(problems, sources, book_path)


def check_error_book_code_links(problems: list[str], sources: dict[pathlib.Path, pathlib.Path],
                                book_path: pathlib.Path) -> None:
    """A hand-written page that names a code as a link to the book (``[`code`](…#anchor)``)
    must land on THAT code's section, the code must be declared, and a table cell right after
    the link must carry the code's catalog title: a renamed title or a mislabelled link would
    otherwise tell the reader one thing and open another."""
    specs = {spec.code: spec for spec in all_specs()}
    bad: list[str] = []
    for resolved, page in sorted(sources.items()):
        for match in _CODE_LINK_RE.finditer(page.read_text(encoding="utf-8")):
            code, target, fragment, title = match.group(1), match.group(2), match.group(3), match.group(4)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if ((page.parent / target).resolve() if target else resolved) != book_path:
                continue
            where = f"{page.relative_to(DOCS)}: `{code}`"
            spec = specs.get(code)
            if spec is None:
                bad.append(f"{where} is not a declared code")
                continue
            if fragment != doc_anchor(code):
                bad.append(f"{where} links to #{fragment}, not #{doc_anchor(code)}")
            if title is not None and title.strip() != spec.title:
                bad.append(f"{where} is titled {title.strip()!r}, the catalog says {spec.title!r}")
    if bad:
        problems.append(f"code links that disagree with the catalog: {bad}")


def check_emitted_coverage(problems: list[str]) -> dict[str, object]:
    declared = set(all_codes())
    emitted = emitted_wallet_codes()
    undeclared = sorted(set(emitted) - declared)
    if undeclared:
        problems.append(
            "wallet modules emit codes the catalog does not declare (bare code served, no record filed): "
            + ", ".join(f"{code} ({emitted[code]})" for code in undeclared)
        )
    wallet_codes = {code for code in declared if code.startswith(_WALLET_FAMILY)}
    never_emitted = sorted(wallet_codes - set(emitted))
    if never_emitted:
        problems.append(f"declared wallet codes with no emission site (dead vocabulary): {never_emitted}")
    registry_problems: list[str] = []
    for boundary in BOUNDARIES:
        declared_in = str(boundary.codespace.declared_in or "")
        texts = []
        for module in re.split(r"\s*\+\s*", declared_in):
            path = REPO / module
            if path.is_file():
                texts.append(path.read_text(encoding="utf-8"))
        if not texts:
            registry_problems.append(f"boundary {boundary.name}: declared_in names no readable module ({declared_in!r})")
            continue
        for code in boundary.codespace.codes:
            if not any(re.search(rf"(?<![\w-]){re.escape(code)}(?![\w-])", text) for text in texts):
                registry_problems.append(f"boundary {boundary.name}: listed code {code!r} not found in {declared_in}")
    if registry_problems:
        problems.append("boundary registry drifted from its declaring modules: " + "; ".join(registry_problems))
    return {
        "declared_catalog_codes": len(declared),
        "emitted_wallet_codes": len(emitted),
        "boundary_code_spaces": {b.name: len(b.codespace.codes) for b in BOUNDARIES},
    }


def run_checks() -> tuple[list[str], dict[str, object]]:
    problems: list[str] = []
    check_generated_files_current(problems)
    stats = check_per_code_contract(problems)
    pages = check_navigation(problems)
    coverage = check_emitted_coverage(problems)
    report = {
        "catalog": stats,
        "summary_pages": pages,
        "emitted_coverage": coverage,
        "method": (
            "static: wallet_fault/_fault/WalletFault literal extraction over core/wallet and the "
            "wallet API; boundary registry codes checked against their declared_in modules. "
            "Bounded by design; extending the extraction to a new boundary is part of adding it."
        ),
    }
    return problems, report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    problems, report = run_checks()
    if "--json" in args:
        print(json.dumps({"ok": not problems, "problems": problems, "report": report}, indent=2, sort_keys=True))
        return 0 if not problems else 1
    print(f"declared catalog codes: {report['catalog']['codes']} "
          f"(per-code sections: {report['catalog']['sections']})")
    print(f"summary pages: {report['summary_pages']}")
    print(f"emitted wallet codes (static): {report['emitted_coverage']['emitted_wallet_codes']}")
    if problems:
        print(f"\nFAILING ({len(problems)}):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nerror reference and coverage checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
