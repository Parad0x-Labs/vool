"""The docs site builds and its navigation is coherent: one source, no orphan or dangling pages.

The public docs (vool.dev/docs) are built from THIS repository's docs/ (GitBook layout under
.gitbook.yaml). The builder and gate are vendored at tools/docs/ so the suite needs no package
installs. What these checks pin:

* SUMMARY.md <-> docs/ bidirectional coherence: every listed page exists, and every public
  section's pages are listed (a page not listed is not published -- an unlisted NEW page is a
  silently missing page, not a private one);
* the build runs from a clean output directory and the site gate passes (links, search index,
  sitemap, raw-HTML rules);
* the generated references (error book, localization handoff) are exactly what the registries
  produce -- drift fails here, not at deploy time.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
SUMMARY = DOCS / "SUMMARY.md"
GITBOOK = REPO / ".gitbook.yaml"
BUILDER = REPO / "tools" / "docs" / "build_docs.py"
GATE = REPO / "tools" / "docs" / "check_docs_build.py"

_LINK_RE = re.compile(r"^\s*\*\s*\[[^\]]+\]\(([^)#]+?)(?:#[^)]*)?\)\s*$", re.MULTILINE)


def _summary_pages() -> list[Path]:
    pages: list[Path] = []
    for match in _LINK_RE.finditer(SUMMARY.read_text(encoding="utf-8")):
        raw = match.group(1).strip()
        if raw.startswith(("http://", "https://", "mailto:")):
            continue
        pages.append(DOCS / raw)
    return pages


def test_summary_lists_only_pages_that_exist() -> None:
    missing = [str(p.relative_to(DOCS)) for p in _summary_pages() if not p.is_file()]
    assert not missing, f"SUMMARY.md lists pages that do not exist: {missing}"


def test_every_public_section_page_is_listed() -> None:
    """The published sections must not grow unlisted pages (an unlisted page never ships)."""
    listed = {p.resolve() for p in _summary_pages()}
    public_sections = ("getting-started", "concepts", "guides", "reference", "trust", "help")
    unlisted: list[str] = []
    for section in public_sections:
        for md in (DOCS / section).rglob("*.md"):
            if md.resolve() not in listed:
                unlisted.append(str(md.relative_to(DOCS)))
    assert not unlisted, (
        f"pages exist in public sections but are not in SUMMARY.md (they would never publish): {unlisted}"
    )


def test_markdown_links_inside_public_pages_resolve() -> None:
    """Relative links in public pages must point at files that exist (the gate checks the BUILT
    site; this catches them at source-review time with the failing page named)."""
    broken: list[str] = []
    for page in _summary_pages():
        text = page.read_text(encoding="utf-8")
        for match in re.finditer(r"\]\(([^)#\s]+?)(?:#[^)\s]*)?\)", text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (page.parent / target).resolve().exists():
                broken.append(f"{page.relative_to(DOCS)} -> {target}")
    assert not broken, f"dangling relative links: {broken}"


def test_gitbook_contract_is_what_the_site_builder_reads() -> None:
    text = GITBOOK.read_text(encoding="utf-8")
    assert "root: ./docs/" in text
    assert "readme: README.md" in text and "summary: SUMMARY.md" in text


@pytest.mark.order(index=-1)
def test_the_docs_site_builds_and_passes_the_publish_gate(tmp_path: Path) -> None:
    """End-to-end: build from the repo into a scratch dir with the SAME command the website
    and the server-side sync use, then run the publish gate against the output."""
    out = tmp_path / "docs-build"
    build = subprocess.run(
        [sys.executable, str(BUILDER), "--src", str(REPO), "--out", str(out),
         "--base", "/docs", "--site-url", "https://vool.dev"],
        capture_output=True, text=True, timeout=300,
        cwd=str(REPO),
    )
    assert build.returncode == 0, build.stderr[-2000:]
    gate = subprocess.run(
        [sys.executable, str(GATE), str(out), "/docs"],
        capture_output=True, text=True, timeout=300, cwd=str(REPO),
    )
    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert "build is publishable" in gate.stdout
    # The built site carries the search index and the generated error reference.
    assert (out / "search.json").is_file()
    index = json.loads((out / "search.json").read_text(encoding="utf-8"))
    assert index, "empty search index"
    error_pages = list(out.rglob("ERROR_BOOK*.html")) or list((out / "ERROR_BOOK").glob("*.html"))
    assert error_pages, "the generated error reference did not publish"
    # The public concept pages that share their names with personal-file ignore rules
    # must publish and be reachable through the generated search index.
    for concept in ("memory", "tools"):
        assert (out / "concepts" / concept / "index.html").is_file(), f"concepts/{concept} did not publish"
    indexed_urls = {entry.get("u", "") for entry in index}
    for concept in ("memory", "tools"):
        assert f"/docs/concepts/{concept}/" in indexed_urls, f"concepts/{concept} is missing from the search index"


def _git_in(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60)


def _tracked(repo: Path, relative: str) -> bool:
    listing = _git_in(repo, "ls-files", "--", relative)
    return listing.returncode == 0 and bool(listing.stdout.strip())


@pytest.mark.parametrize("ignore_case", [False, True], ids=["ignoreCase-false", "ignoreCase-true"])
def test_ignore_rules_add_public_concept_pages_and_still_ignore_personal_files(
    tmp_path: Path, ignore_case: bool,
) -> None:
    """The bare MEMORY.md/TOOLS.md personal-file rules must not swallow the public concept
    pages that share their names (case-insensitively where core.ignoreCase applies), while
    every synthetic personal file -- root, nested, any case -- stays ignored.

    Drives a disposable git repository holding THIS repository's actual .gitignore, so the
    published ignore contract itself is what is under test, on both ignoreCase settings.
    """
    probe = tmp_path / "ignore-probe"
    (probe / "docs" / "concepts").mkdir(parents=True)
    (probe / "notes").mkdir()
    (probe / ".gitignore").write_text((REPO / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8")
    for concept in ("memory.md", "tools.md"):
        (probe / "docs" / "concepts" / concept).write_text(f"# {concept.removesuffix('.md').title()}\n", encoding="utf-8")
    personal_files = ["MEMORY.md", "TOOLS.md", "notes/MEMORY.md", "notes/TOOLS.md"]
    for relative in personal_files:
        (probe / relative).write_text("operator-private content\n", encoding="utf-8")
    nested_lowercase = ["notes/memory.md", "notes/tools.md"]
    for relative in nested_lowercase:
        (probe / relative).write_text("private notes\n", encoding="utf-8")

    init = _git_in(probe, "init", "-q")
    assert init.returncode == 0, init.stderr
    configured = _git_in(probe, "config", "core.ignoreCase", str(ignore_case).lower())
    assert configured.returncode == 0, configured.stderr

    for relative in ("docs/concepts/memory.md", "docs/concepts/tools.md"):
        added = _git_in(probe, "add", "--", relative)
        assert added.returncode == 0, (
            f"the personal-name ignore rules swallow the public page {relative}: {added.stderr.strip()}"
        )
        assert _tracked(probe, relative), f"public page was not staged: {relative}"

    still_ignored = list(personal_files)
    if ignore_case:
        # Only case-insensitive matching makes the bare rules catch these; with matching
        # off they are distinct paths that are merely untracked, not ignored.
        still_ignored += nested_lowercase
    for relative in still_ignored:
        checked = _git_in(probe, "check-ignore", "--", relative)
        assert checked.returncode == 0, f"a personal file is no longer ignored: {relative}"
        # git's add exit code is not a stable refusal signal (2.50.1 exits 0 while
        # silently refusing ignored paths under ignoreCase): the index is the truth.
        _git_in(probe, "add", "--", relative)
        assert not _tracked(probe, relative), (
            f"an ignored personal file became addable: {relative}"
        )


def test_generated_references_match_their_registries() -> None:
    """ERROR_BOOK and the localization handoff are GENERATED; a stale copy fails here."""
    from tools.generate_error_book import build_error_book_markdown
    from tools.generate_localization_handoff import build_handoff

    assert (DOCS / "ERROR_BOOK.md").read_text(encoding="utf-8") == build_error_book_markdown()
    handoff_on_disk = json.loads((REPO / "config" / "localization-handoff.json").read_text(encoding="utf-8"))
    assert handoff_on_disk == build_handoff()
