"""The translated docs stay true to the English docs, and the site shows them correctly.

docs/translations/<lang>/ holds the docs in the app's languages. What these checks pin:

* every translation in the repository passes tools/docs/check_translations.py (code byte for
  byte, URLs, headings, tables, hints, lists, numbers, links and the app's own labels); a page
  whose English source changed since it was translated is out of date, which warns here and
  is published with a notice, so editing an English page never blocks on 16 retranslations;
* the checker catches each kind of drift it promises to catch;
* the site build marks machine-translated and out-of-date pages, links untranslated pages to
  the English ones, shows Arabic and Hebrew right to left, and the publish gate catches a link
  to a section that does not exist;
* docs/translations/GLOSSARY.md is exactly what the glossary data produces.
"""

from __future__ import annotations

import html
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
TOOLS = REPO / "tools" / "docs"
BUILDER = TOOLS / "build_docs.py"
GATE = TOOLS / "check_docs_build.py"
CHECKER = TOOLS / "check_translations.py"

# The pages every docs language must keep (the first pages a new user reads).
CORE_PAGES = (
    "README.md",
    "getting-started/install.md",
    "getting-started/first-run.md",
    "getting-started/connect-a-model.md",
    "trust/data-handling.md",
    "help/troubleshooting.md",
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checker(docs: Path | None = None):
    """A fresh checker module, optionally pointed at a copy of docs/."""
    ct = _load(CHECKER, "docs_translation_checker_under_test")
    if docs is not None:
        ct.DOCS = str(docs)
        ct.TRANSLATIONS = str(docs / "translations")
    return ct


def _copy_docs(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(DOCS, root / "docs")
    shutil.copy2(REPO / ".gitbook.yaml", root / ".gitbook.yaml")
    return root


# ---------------------------------------------------------------------------
# The repository's translations
# ---------------------------------------------------------------------------


def test_every_translation_matches_its_english_page() -> None:
    ct = _checker()
    languages = ct._languages()
    assert languages, "no translations under docs/translations/"
    report = ct.check_all()
    problems = {lang: report.blocking(lang, allow_stale=True) for lang in languages}
    problems = {lang: probs for lang, probs in problems.items() if probs}
    assert not problems, json.dumps(problems, ensure_ascii=False, indent=1)


def test_every_app_language_keeps_the_core_pages() -> None:
    """The docs come in the languages the app speaks (the glossary's languages), and each
    keeps the pages a new user reads first."""
    ct = _checker()
    data, _catalogs = ct.load_glossary()
    app_languages = set(data["languages"])
    assert set(ct._languages()) == app_languages, (
        "docs/translations/ must hold exactly the glossary's languages")
    missing = [f"{lang}/{page}" for lang in sorted(app_languages) for page in CORE_PAGES
               if not (DOCS / "translations" / lang / page).is_file()]
    assert not missing, f"translated core pages missing: {missing}"


def test_glossary_page_is_what_the_glossary_data_produces() -> None:
    ct = _checker()
    current = (DOCS / "translations" / "GLOSSARY.md").read_text(encoding="utf-8")
    assert current == ct.render_glossary(), (
        "docs/translations/GLOSSARY.md is out of date: "
        "python3 tools/docs/check_translations.py glossary --write")


def test_glossary_app_labels_come_from_the_app_catalogs() -> None:
    """A label keyed to the app catalog must exist there in English with the same words, so
    the docs and the app cannot drift apart silently."""
    ct = _checker()
    data, catalogs = ct.load_glossary()
    english = catalogs["en"]
    for item in data["app_labels"]:
        if "key" in item:
            assert english.get(item["key"]) == item["en"], item


# ---------------------------------------------------------------------------
# The checker catches drift
# ---------------------------------------------------------------------------


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (path, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


DRIFT_CASES = {
    "code block": ("help/troubleshooting.md", "localhost:11434", "localhost:11435",
                   "code blocks differ"),
    "url": ("README.md", "https://discord.gg/V9NkjP3Fzz", "https://discord.gg/other",
            "URLs differ"),
    "app label": ("getting-started/first-run.md", "Lokale Modelle**", "Lokalen Modelle**",
                  "label 'Local models' must read 'Lokale Modelle'"),
    "number": ("getting-started/connect-a-model.md", "# Ein Modell verbinden\n",
               "# Ein Modell verbinden\n\nVersion 42.\n", "numbers differ"),
    "heading": ("trust/data-handling.md", "## Die zwei Zustände\n", "Die zwei Zustände\n",
                "headings differ"),
    "link target": ("getting-started/install.md", "(../../../guides/local-models.md)",
                    "(../../../guides/openrouter.md)", "links differ from the English"),
    "link to english twin": ("getting-started/install.md", "(first-run.md)",
                             "(../../../getting-started/first-run.md)",
                             "goes to the English page, but this language has"),
    "machine path": ("getting-started/connect-a-model.md", "# Ein Modell verbinden\n",
                     "# Ein Modell verbinden\n\nSiehe /Users/someone/vool.\n", "machine path"),
    "status": ("help/troubleshooting.md", "translation: machine\n", "",
               "needs translation: machine"),
}


@pytest.mark.parametrize("case", sorted(DRIFT_CASES))
def test_checker_catches_drift(tmp_path: Path, case: str) -> None:
    page, old, new, expected = DRIFT_CASES[case]
    root = _copy_docs(tmp_path)
    _edit(root / "docs" / "translations" / "de" / page, old, new)
    ct = _checker(root / "docs")
    report = ct.check_all()
    problems = report.blocking("de", allow_stale=True)
    assert any(expected in p for p in problems), problems
    assert all(p.startswith("de/" + page) for p in problems), problems


def test_checker_rejects_a_summary_that_lists_an_untranslated_page(tmp_path: Path) -> None:
    root = _copy_docs(tmp_path)
    summary = root / "docs" / "translations" / "de" / "SUMMARY.md"
    summary.write_text(summary.read_text(encoding="utf-8") + "* [Wallet](guides/wallet.md)\n",
                       encoding="utf-8")
    report = _checker(root / "docs").check_all()
    assert any("guides/wallet.md does not exist" in p for p in report.blocking("de")), \
        report.blocking("de")


def test_an_out_of_date_page_fails_the_check_and_only_warns_with_allow_stale(
        tmp_path: Path) -> None:
    root = _copy_docs(tmp_path)
    english = root / "docs" / "help" / "troubleshooting.md"
    english.write_text(english.read_text(encoding="utf-8") + "\nOne more English line.\n",
                       encoding="utf-8")
    ct = _checker(root / "docs")
    report = ct.check_all()
    for lang in ct._languages():
        assert report.stale[lang] == ["help/troubleshooting.md"], lang
        assert any("STALE" in p for p in report.blocking(lang)), lang
        assert report.blocking(lang, allow_stale=True) == [], lang
    assert ct.main(["check"]) == 1
    assert ct.main(["check", "--allow-stale"]) == 0
    # Stamping after retranslating clears it.
    assert ct.main(["stamp", "de", "help/troubleshooting.md"]) == 0
    assert "help/troubleshooting.md" not in ct.check_all().stale["de"]


# ---------------------------------------------------------------------------
# The built site
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The site built from a copy of the docs whose English README changed after it was
    translated, so every translated README is out of date."""
    tmp_path = tmp_path_factory.mktemp("docs-translations")
    root = _copy_docs(tmp_path)
    readme = root / "docs" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nOne more English line.\n",
                      encoding="utf-8")
    out = tmp_path / "site"
    build = subprocess.run(
        [sys.executable, str(BUILDER), "--src", str(root), "--out", str(out), "--base", "/docs"],
        capture_output=True, text=True, timeout=300, cwd=str(tmp_path),
    )
    assert build.returncode == 0, build.stdout[-2000:] + build.stderr[-2000:]
    gate = subprocess.run([sys.executable, str(GATE), str(out), "/docs"],
                          capture_output=True, text=True, timeout=300, cwd=str(tmp_path))
    return {"out": out, "log": build.stdout + build.stderr, "gate": gate, "tmp": tmp_path}


def _page(built: dict, rel: str) -> str:
    return (built["out"] / rel / "index.html").read_text(encoding="utf-8")


def test_site_with_out_of_date_pages_is_still_publishable(built: dict) -> None:
    gate = built["gate"]
    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert "build is publishable" in gate.stdout


def test_out_of_date_and_machine_pages_carry_a_notice(built: dict) -> None:
    ui = _load(BUILDER, "docs_builder_under_test").UI
    front = _page(built, "de")
    assert 'class="mt-note mt-stale"' in front
    assert html.escape(ui["de"]["stale"]) in front
    install = _page(built, "de/getting-started/install")
    assert 'class="mt-note"' in install and "mt-stale" not in install
    assert html.escape(ui["de"]["machine"]) in install
    assert "mt-note" not in _page(built, "getting-started/install")
    info = json.loads((built["out"] / "build.json").read_text(encoding="utf-8"))
    assert info["out_of_date"].get("de") == ["README.md"], info["out_of_date"]
    assert "translation older than the English page" in built["log"]


def test_untranslated_pages_are_linked_in_english_and_translated_ones_in_language(
        built: dict) -> None:
    install = _page(built, "de/getting-started/install")
    assert ('<a href="/docs/guides/local-models/"' in install
            and 'class="to-en" hreflang="en"' in install)
    assert 'href="/docs/de/getting-started/first-run/"' in install
    assert 'href="/docs/getting-started/first-run/"' not in install.split("<main")[1]


@pytest.mark.parametrize("lang", ["ar", "he"])
def test_right_to_left_languages_are_shown_right_to_left(built: dict, lang: str) -> None:
    assert f'<html lang="{lang}" dir="rtl">' in _page(built, lang)
    assert f'<html lang="{lang}" dir="rtl">' in _page(built, f"{lang}/getting-started/install")


def test_styles_keep_right_to_left_and_joined_scripts_readable(built: dict) -> None:
    css = (built["out"] / "assets" / "docs.css").read_text(encoding="utf-8")
    # A right-to-left page scrolls to the left: a skip link parked at left:-9999px made it
    # 9999px wide and phones zoomed the whole page out. It is parked past the right edge.
    assert '[dir="rtl"] .skip{left:auto;right:-9999px}' in css
    # Arabic and Devanagari small labels are not letter-spaced apart.
    for lang in ("ar", "hi"):
        assert f":lang({lang}) .nav-group" in css


def test_left_to_right_languages_are_not_flipped(built: dict) -> None:
    assert '<html lang="de">' in _page(built, "de")
    assert 'dir="rtl"' not in _page(built, "de")


def test_gate_rejects_a_link_to_a_missing_section(built: dict) -> None:
    site = built["tmp"] / "site-broken"
    shutil.copytree(built["out"], site)
    page = site / "de" / "getting-started" / "install" / "index.html"
    text = page.read_text(encoding="utf-8")
    link = 'href="/docs/de/getting-started/first-run/"'
    assert link in text
    page.write_text(text.replace(link, 'href="/docs/de/getting-started/first-run/#no-such-section"', 1),
                    encoding="utf-8")
    gate = subprocess.run([sys.executable, str(GATE), str(site), "/docs"],
                          capture_output=True, text=True, timeout=300, cwd=str(built["tmp"]))
    assert gate.returncode != 0
    assert "link to a missing section" in gate.stdout + gate.stderr
