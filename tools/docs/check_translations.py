#!/usr/bin/env python3
"""check_translations.py — keep docs/translations/<lang>/ true to the English docs.

Run from anywhere (Python standard library only):

    python3 tools/docs/check_translations.py                  # check every translation; exit 1 on any problem
    python3 tools/docs/check_translations.py check --allow-stale
                                                              # the same, but an out-of-date page only warns
    python3 tools/docs/check_translations.py status           # one line per language
    python3 tools/docs/check_translations.py stamp de getting-started/install.md
                                                              # after (re)translating: record the English hash
    python3 tools/docs/check_translations.py slug "Erstes Öffnen"   # the #anchor the docs builder gives a heading
    python3 tools/docs/check_translations.py glossary --write # regenerate docs/translations/GLOSSARY.md

A translated page is docs/translations/<lang>/<path>; its source is docs/<path>.
What it must keep from that English page (docs/translations/TRANSLATOR_RULES.md
has the why):

  * front matter: `translation: machine` (or `reviewed` with a `reviewer:`),
    `source_sha256:` of the English file it was made from, and a translated
    `description:` when the English has one. A different English hash means the
    English changed since: the page is STALE until it is translated again (the
    docs site still shows it, under a notice that it is out of date);
  * every code block byte for byte, every inline code span, every URL;
  * every link to the same page and section: a page translated in this language
    is linked in this language, any other page is linked by climbing out of the
    translation folder to the English file (../../guides/x.md), and #sections
    point at the matching heading;
  * the same headings (levels and order), tables (rows and columns), GitBook
    hints (styles and order), list items and numbers;
  * the app's own words: each label the English page names in bold, in quotes
    or as a multi-word phrase is written exactly as the app shows it in that
    language (core/i18n/catalogs), and macOS words exactly as Apple writes them
    (tools/docs/translation_glossary.json);
  * on the Error Book, each code's message and next step exactly as the app
    shows them in that language (tools/docs/translate_error_book.py makes it);
  * in Chinese and Japanese, no line break between two characters of a paragraph
    (a browser shows it as a space in the middle of the sentence);
  * no machine paths.

A translated SUMMARY.md lists only translated pages, in the English order and
sections, starting with README.md; every translated page must be listed in it.
"""

import argparse
import collections
import hashlib
import importlib.util
import itertools
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
DOCS = os.path.join(REPO, "docs")
TRANSLATIONS = os.path.join(DOCS, "translations")
GLOSSARY_JSON = os.path.join(HERE, "translation_glossary.json")
GLOSSARY_MD = os.path.join(TRANSLATIONS, "GLOSSARY.md")
CATALOGS = os.path.join(REPO, "core", "i18n", "catalogs")


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "vool_docs_builder", os.path.join(HERE, "build_docs.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()

_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+-]*)\s*$")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.S)
_LINK = re.compile(r'(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
_URL = re.compile(r"https?://[^\s)>\]\"'`]+")
_HEADING = re.compile(r"^(#{1,6})\s+\S")
_HINT = re.compile(r'^\s*\{%\s*hint\s+style="?(\w+)"?\s*%\}\s*$')
_LIST_ITEM = re.compile(r"^\s*(?:([*+-])|(\d+)[.)])\s+\S")
# A number. Thousands may be grouped the way the language writes them: 120,000, 120.000, or
# 120 000 with a space or a no-break space (French, Polish, Russian, Ukrainian, Lithuanian).
_NUMBER = re.compile(r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)|\d+(?:[.,]\d+)*")
_GROUP_SPACE = re.compile(r"[ \u00a0\u202f]")
_MACHINE_PATH = re.compile(r"(?:/Users/|/home/|/Volumes/|[A-Za-z]:\\Users\\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_QUOTED = re.compile(r'"([^"\n]+)"|\u201c([^\u201d\n]+)\u201d')
_PATH_SEP = re.compile(r"\s*(?:\u2192|>)\s*")
_SHA = re.compile(r"^[0-9a-f]{64}$")
STATUSES = ("machine", "reviewed")
# Chinese and Japanese put no spaces between words, but a browser shows a line break inside a
# paragraph as a space: 接続することも⏎できます reads 接続することも できます.
UNSPACED_LANGS = ("ja", "zh")
# Pages made from the app's own texts (tools/docs/translate_error_book.py): each code's message and
# next step are the app's words in that language, so they count as app labels. The page must show
# them exactly, and a digit inside them is the app's (1つ for "one"), not a fact the page changed.
APP_TEXT_PAGES = ("ERROR_BOOK.md",)


# ---------------------------------------------------------------------------
# Reading pages
# ---------------------------------------------------------------------------


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Page:
    """One markdown page split the way the docs builder reads it."""

    def __init__(self, path):
        self.path = path
        raw = read_text(path)
        self.meta, body = BUILDER.split_front_matter(raw)
        self.fences, prose = [], []
        lines = body.replace("\r\n", "\n").split("\n")
        i = 0
        while i < len(lines):
            m = _FENCE.match(lines[i])
            if m:
                fence, lang, buf = m.group(2)[0], m.group(3), []
                i += 1
                while i < len(lines) and not re.match(r"^\s*%s{3,}\s*$" % re.escape(fence), lines[i]):
                    buf.append(lines[i])
                    i += 1
                self.fences.append((lang, "\n".join(buf)))
                prose.append("")
                i += 1
                continue
            prose.append(lines[i])
            i += 1
        self.prose = prose
        text = "\n".join(prose)
        self.code_spans = [m.group(2).strip() for m in _CODE_SPAN.finditer(text)]
        # Inline code is taken out before links and numbers are read, so a
        # bracket or digit inside code never counts twice.
        self.plain = _CODE_SPAN.sub(" ", text)
        self.links, self.images = [], []
        for m in _LINK.finditer(self.plain):
            (self.images if m.group(1) else self.links).append((m.group(2), m.group(3)))
        self.urls = sorted({u.rstrip(".,;:!?") for u in _URL.findall(text)})
        self.headings = [len(m.group(1)) for m in map(_HEADING.match, prose) if m]
        self.hints = [m.group(1) for m in map(_HINT.match, prose) if m]
        self.tables = self._tables(prose)
        items = [m for m in map(_LIST_ITEM.match, prose) if m]
        self.list_items = (sum(1 for m in items if m.group(1)),
                           sum(1 for m in items if m.group(2)))
        self.number_text = self._number_text(prose)
        self.numbers = self.numbers_outside()
        self.slugs = self._slugs(body)

    @staticmethod
    def _tables(prose):
        tables, current = [], None
        for line in prose:
            if line.strip().startswith("|"):
                row = line.strip().replace("\\|", "")
                cells = row.strip("|").split("|")
                if re.match(r"^\|?[\s:|-]+\|?$", row) and "-" in row:
                    continue  # the separator row
                if current is None:
                    current = []
                    tables.append(current)
                current.append(len(cells))
            else:
                current = None
        return [tuple(t) for t in tables]

    @staticmethod
    def _number_text(prose):
        """The prose that numbers are read from: no list markers, hint tags,
        inline code, link targets, URLs or heading marks."""
        text = []
        for line in prose:
            if _HEADING.match(line) is None and line.strip().startswith(("{%",)):
                continue
            line = _LIST_ITEM.sub(lambda m: " " * len(m.group(0)), line) if _LIST_ITEM.match(line) else line
            text.append(line)
        flat = _CODE_SPAN.sub(" ", "\n".join(text))
        flat = _LINK.sub(lambda m: " %s " % m.group(2), flat)   # keep link text, drop targets
        flat = _URL.sub(" ", flat)
        return re.sub(r"^#{1,6}\s", " ", flat, flags=re.M)

    def numbers_outside(self, labels=()):
        """Every number in the prose, as a sorted list (a decimal comma, and a comma or
        space between groups of thousands, reads as a point: 120 000 is 120,000). Digits inside the given labels are not counted: an app label can
        carry a digit its English does not (何を1人で行えますか？)."""
        flat = self.number_text
        for label in sorted(set(labels), key=len, reverse=True):
            if label:
                flat = flat.replace(label, " ")
        return sorted(_GROUP_SPACE.sub(".", n).replace(",", ".") for n in _NUMBER.findall(flat))

    @staticmethod
    def _slugs(body):
        html_out, _toc = BUILDER.Markdown(lambda href, asset=False: href).convert(body)
        return re.findall(r'<h[1-6] id="([^"]+)"', html_out)

    def label_text(self):
        """Prose where the app's words can appear: no headings, link text or code.
        Line breaks count as spaces, so a label wrapped across two lines
        ("**System\\nSettings**") is still the label."""
        keep = [ln for ln in self.prose if not _HEADING.match(ln)]
        flat = _CODE_SPAN.sub(" ", "\n".join(keep))
        return re.sub(r"\s+", " ", _LINK.sub(" ", flat))


def summary_entries(path):
    """[(section, src)] from a SUMMARY.md, in order (external links skipped)."""
    out = []
    for e in BUILDER.parse_summary(path, ""):
        if not e.external:
            out.append((e.section, e.src.split("#")[0]))
    return out


# ---------------------------------------------------------------------------
# Glossary: the app's words and Apple's words, per language
# ---------------------------------------------------------------------------


def _flatten(d, prefix=""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _flatten(v, prefix + k + ".")
        else:
            yield prefix + k, v


def load_glossary():
    data = json.loads(read_text(GLOSSARY_JSON))
    catalogs = {}
    for code, spec in data["languages"].items():
        tag = spec["catalog"]
        catalogs[code] = dict(_flatten(json.loads(
            read_text(os.path.join(CATALOGS, tag + ".json")))["messages"]))
    catalogs["en"] = dict(_flatten(json.loads(
        read_text(os.path.join(CATALOGS, "en.json")))["messages"]))
    return data, catalogs


def labels_for(lang, data, catalogs):
    """{English label: the exact words a page in `lang` must use}."""
    out = {}
    for item in data["app_labels"]:
        if "key" in item:
            en = catalogs["en"].get(item["key"])
            if en != item["en"]:
                raise SystemExit("glossary: catalog key %s now reads %r in English, not %r"
                                 % (item["key"], en, item["en"]))
            out[item["en"]] = catalogs[lang][item["key"]]
        else:
            out[item["en"]] = item["fixed"]
    for en, per_lang in data["os_labels"].items():
        local = per_lang.get(lang)
        out[en] = en if local is None else local
    return out


def app_texts(lang, catalogs):
    """{English message or next step of a fault code: the app's words for it in `lang`}."""
    return {catalogs["en"][key]: catalogs[lang][key] for key in catalogs["en"]
            if key.startswith("fault.") and key.endswith((".message", ".action"))
            and key in catalogs[lang]}


def english_labels_on(page, known):
    """Labels from `known` that the English page names: any multi-word label in
    the prose, and one-word labels only in bold or quotes ("Open the DMG" is not
    the Open button). Longest label first, so "System Settings" is not also
    "Settings"."""
    text = page.label_text()
    found = collections.Counter()
    spans = []
    for m in _BOLD.finditer(text):
        spans.append(m.group(1))
    for m in _QUOTED.finditer(text):
        spans.append(m.group(1) or m.group(2))
    for span in spans:
        for part in _PATH_SEP.split(span):
            part = part.strip().rstrip(".:,;")
            for label in known:
                if part == label or part == label.rstrip("?") or part == label.upper():
                    found[label] += 1
    multi = sorted((lb for lb in known if " " in lb.strip()), key=len, reverse=True)
    masked = text
    for label in multi:
        pattern = r"(?<![\w])%s(?![\w])" % re.escape(label)
        for _m in re.finditer(pattern, masked):
            found[label] += 0 if found[label] else 1
        masked = re.sub(pattern, " ", masked)
    return sorted(found)


# ---------------------------------------------------------------------------
# Checking one language
# ---------------------------------------------------------------------------


class Report:
    def __init__(self):
        self.problems = collections.defaultdict(list)   # lang -> [text]
        self.pages = collections.defaultdict(dict)      # lang -> {src: status}
        self.stale = collections.defaultdict(list)      # lang -> [src]
        self.stale_texts = set()

    def add(self, lang, text, stale=False):
        self.problems[lang].append(text)
        if stale:
            self.stale_texts.add(text)

    def blocking(self, lang, allow_stale=False):
        """Problems that fail the check (a stale page only warns with allow_stale)."""
        return [p for p in self.problems.get(lang, [])
                if not (allow_stale and p in self.stale_texts)]


def _languages():
    if not os.path.isdir(TRANSLATIONS):
        return []
    return sorted(d for d in os.listdir(TRANSLATIONS)
                  if os.path.isdir(os.path.join(TRANSLATIONS, d)))


def _translated_pages(lang):
    root = os.path.join(TRANSLATIONS, lang)
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".md") and not (dirpath == root and name == "SUMMARY.md"):
                out.append(os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/"))
    return sorted(out)


def _resolve(lang, from_src, href):
    """Where a link in translations/<lang>/<from_src> lands: ("translation"|"english", path, frag)."""
    target, _, frag = href.partition("#")
    if not target:
        return ("self", from_src, frag)
    rel = os.path.normpath(os.path.join(os.path.dirname(from_src), target)).replace(os.sep, "/")
    if rel == ".." or rel.startswith("../"):
        real = os.path.normpath(os.path.join("translations", lang, os.path.dirname(from_src),
                                             target)).replace(os.sep, "/")
        return ("english", real, frag)
    return ("translation", rel, frag)


def _english_target(from_src, href):
    target, _, frag = href.partition("#")
    if not target:
        return (from_src, frag)
    rel = os.path.normpath(os.path.join(os.path.dirname(from_src), target)).replace(os.sep, "/")
    return (rel, frag)


def check_language(lang, report, data, catalogs, english_summary):
    ldir = os.path.join(TRANSLATIONS, lang)
    known = dict(BUILDER.LANGS)
    if lang not in known:
        report.add(lang, "docs/translations/%s is not a docs language (tools/docs/build_docs.py LANGS)" % lang)
        return
    if lang not in data["languages"]:
        report.add(lang, "%s has no entry in tools/docs/translation_glossary.json" % lang)
        return
    en_order = [src for _sec, src in english_summary]
    en_section = {src: sec for sec, src in english_summary}

    # ---- SUMMARY.md ----
    spath = os.path.join(ldir, "SUMMARY.md")
    pages = _translated_pages(lang)
    if not os.path.isfile(spath):
        report.add(lang, "SUMMARY.md is missing (nothing in this language is published)")
        listed = []
    else:
        listed_entries = summary_entries(spath)
        listed = [src for _s, src in listed_entries]
        if not listed or listed[0] != "README.md":
            report.add(lang, "SUMMARY.md must list README.md first (the language's front page)")
        for src in listed:
            if src not in en_section:
                report.add(lang, "SUMMARY.md lists %s, which is not in the English SUMMARY.md" % src)
            if src not in pages:
                report.add(lang, "SUMMARY.md lists %s, but docs/translations/%s/%s does not exist"
                           % (src, lang, src))
        order = [en_order.index(s) for s in listed if s in en_section]
        if order != sorted(order):
            report.add(lang, "SUMMARY.md order differs from the English order")
        pairs = {}
        for sec, src in listed_entries:
            if src in en_section:
                pairs.setdefault(sec, set()).add(en_section[src])
        for sec, en_secs in pairs.items():
            if len(en_secs) > 1:
                report.add(lang, "SUMMARY.md section %r mixes English sections %s"
                           % (sec, sorted(s or "(top)" for s in en_secs)))
        for src in pages:
            if src not in listed:
                report.add(lang, "%s is not listed in SUMMARY.md, so it never publishes" % src)
    if "README.md" not in pages:
        report.add(lang, "README.md is missing: the language picker links every untranslated page here")

    labels = labels_for(lang, data, catalogs)
    translated = set(pages)
    for src in pages:
        page_labels = {**labels, **app_texts(lang, catalogs)} if src in APP_TEXT_PAGES else labels
        report.pages[lang][src] = _check_page(lang, src, translated, page_labels, en_section, report)


def _check_page(lang, src, translated, labels, en_section, report):
    tpath = os.path.join(TRANSLATIONS, lang, src)
    epath = os.path.join(DOCS, src)
    where = "%s/%s" % (lang, src)
    if not os.path.isfile(epath):
        report.add(lang, "%s has no English source docs/%s" % (where, src))
        return "orphan"
    en, tr = Page(epath), Page(tpath)
    bad = []

    status = tr.meta.get("translation", "")
    if status not in STATUSES:
        bad.append("front matter needs translation: machine (or reviewed)")
    if status == "reviewed" and not tr.meta.get("reviewer", "").strip():
        bad.append("translation: reviewed needs a reviewer:")
    recorded = tr.meta.get("source_sha256", "")
    if not _SHA.match(recorded):
        bad.append("front matter needs source_sha256: (run: check_translations.py stamp %s %s)"
                   % (lang, src))
    elif recorded != sha256_file(epath):
        # Compared with an English page it was not made from, every other check
        # would only repeat that the English moved on. The site shows the page
        # with an out-of-date notice until it is translated again.
        for b in bad:
            report.add(lang, "%s: %s" % (where, b))
        report.stale[lang].append(src)
        report.add(lang, "%s: STALE: docs/%s changed since this translation was made; translate "
                         "the change, then stamp it" % (where, src), stale=True)
        return "stale"
    if en.meta.get("description") and not tr.meta.get("description"):
        bad.append("front matter needs a translated description:")

    if tr.fences != en.fences:
        bad.append("code blocks differ from the English (they must be byte-identical)")
    if collections.Counter(tr.code_spans) != collections.Counter(en.code_spans):
        missing = collections.Counter(en.code_spans) - collections.Counter(tr.code_spans)
        extra = collections.Counter(tr.code_spans) - collections.Counter(en.code_spans)
        bad.append("inline code differs: missing %s, extra %s" % (sorted(missing), sorted(extra)))
    if tr.urls != en.urls:
        bad.append("URLs differ: missing %s, extra %s"
                   % (sorted(set(en.urls) - set(tr.urls)), sorted(set(tr.urls) - set(en.urls))))
    if tr.headings != en.headings:
        bad.append("headings differ: English %s, translation %s" % (en.headings, tr.headings))
    if tr.tables != en.tables:
        bad.append("tables differ: English %s, translation %s" % (en.tables, tr.tables))
    if tr.hints != en.hints:
        bad.append("hints differ: English %s, translation %s" % (en.hints, tr.hints))
    if tr.list_items != en.list_items:
        bad.append("list items differ (bulleted, numbered): English %s, translation %s"
                   % (en.list_items, tr.list_items))
    en_numbers = en.numbers_outside(labels)
    tr_numbers = tr.numbers_outside(labels.values())
    if tr_numbers != en_numbers:
        missing = collections.Counter(en_numbers) - collections.Counter(tr_numbers)
        extra = collections.Counter(tr_numbers) - collections.Counter(en_numbers)
        bad.append("numbers differ: missing %s, extra %s" % (sorted(missing.elements()),
                                                             sorted(extra.elements())))
    if [s for _t, s in tr.images] != [s for _t, s in en.images]:
        bad.append("images differ from the English")
    bad += _check_links(lang, src, en, tr, translated, en_section)
    for label in english_labels_on(en, labels):
        local = labels[label]
        if not _names_label(tr.label_text(), local, labels.values()):
            bad.append("app/macOS label %r must read %r in this language" % (label, local))
    if _MACHINE_PATH.search("\n".join(tr.prose)) and not _MACHINE_PATH.search("\n".join(en.prose)):
        bad.append("contains a machine path (/Users/, /home/, /Volumes/ or C:\\Users\\)")
    if lang in UNSPACED_LANGS:
        breaks = spaced_breaks(tr.prose)
        if breaks:
            bad.append("%d line break(s) inside a paragraph show as a space between two "
                       "characters; join the lines at %s" % (len(breaks), ", ".join(breaks[:3])))

    for b in bad:
        report.add(lang, "%s: %s" % (where, b))
    return status if not bad else "fails"


def _wide(ch):
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _shown(line):
    """A line as a reader sees its ends: link targets and the marks of emphasis, code and
    brackets do not show."""
    return _LINK.sub(lambda m: m.group(2), line).strip().strip("*_`[]")


def spaced_breaks(prose):
    """Line breaks inside a paragraph that fall between two wide (CJK) characters, which a
    browser shows as a space, as "before⏎after" snippets. A new block (heading, list item,
    table row, hint) and a hard break (two trailing spaces) start a new line instead."""
    found = []
    for a, b in itertools.pairwise(prose):
        if not a.strip() or not b.strip() or a.endswith("  "):
            continue
        if _HEADING.match(a) or _HEADING.match(b) or _LIST_ITEM.match(b):
            continue
        if a.lstrip().startswith(("|", "{%")) or b.lstrip().startswith(("|", "{%")):
            continue
        last, first = _shown(a)[-1:], _shown(b)[:1]
        if last and first and _wide(last) and _wide(first):
            found.append("%s⏎%s" % (a.rstrip()[-8:], b.lstrip()[:8]))
    return found


def _names_label(text, local, all_locals):
    """True when `text` names `local` on its own, not only inside a longer label
    that contains it (Einstellungen inside Systemeinstellungen, 設定 inside
    システム設定). A plain substring test, because labels are inflected or
    followed by particles in many languages and CJK has no word breaks."""
    def norm(t):
        # One Unicode form for both sides (a nukta letter or a Vietnamese tone
        # mark can be typed precomposed or as base + combining mark).
        return unicodedata.normalize("NFC", t).casefold()
    flat = norm(text)
    want = norm(local)
    for other in sorted({norm(o) for o in all_locals}, key=len, reverse=True):
        if len(other) > len(want) and want in other:
            flat = flat.replace(other, " ")
    return want in flat


def _check_links(lang, src, en, tr, translated, en_section):
    bad = []
    expected = collections.Counter()
    for _text, href in en.links:
        if re.match(r"^(https?:|mailto:|tel:|//)", href):
            continue
        path, frag = _english_target(src, href)
        if path in translated:
            want_path = "translations/%s/%s" % (lang, path)
            if frag:
                en_slugs = Page(os.path.join(DOCS, path)).slugs if path != src else en.slugs
                tr_slugs = (Page(os.path.join(TRANSLATIONS, lang, path)).slugs
                            if path != src else tr.slugs)
                if frag in en_slugs and len(tr_slugs) == len(en_slugs):
                    frag = tr_slugs[en_slugs.index(frag)]
            expected[(want_path, frag)] += 1
        else:
            expected[(path, frag)] += 1
    actual = collections.Counter()
    for _text, href in tr.links:
        if re.match(r"^(https?:|mailto:|tel:|//)", href):
            continue
        kind, path, frag = _resolve(lang, src, href)
        if kind == "english":
            if path.startswith("translations/") or path.startswith("../"):
                bad.append("link %s leaves the docs" % href)
                continue
            if path in translated:
                bad.append("link %s goes to the English page, but this language has %s"
                           % (href, path))
            if path not in en_section:
                bad.append("link %s goes to a page the site does not publish" % href)
            elif frag and frag not in Page(os.path.join(DOCS, path)).slugs:
                bad.append("link %s: no such section on the English page" % href)
            actual[(path, frag)] += 1
        else:
            if kind == "translation" and path not in translated:
                bad.append("link %s: %s is not translated in this language; link the English "
                           "file by climbing out of the translation folder" % (href, path))
                continue
            slugs = tr.slugs if path == src else Page(os.path.join(TRANSLATIONS, lang, path)).slugs
            if frag and frag not in slugs:
                bad.append("link %s: no such section (anchors come from this language's headings;"
                           " see the slug command)" % href)
            actual[("translations/%s/%s" % (lang, path), frag)] += 1
    if expected != actual:
        bad.append("links differ from the English: missing %s, extra %s"
                   % (sorted((expected - actual).elements()), sorted((actual - expected).elements())))
    return bad


def check_all():
    data, catalogs = load_glossary()
    report = Report()
    english_summary = summary_entries(os.path.join(DOCS, "SUMMARY.md"))
    for lang in _languages():
        check_language(lang, report, data, catalogs, english_summary)
    return report


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_check(args):
    allow_stale = getattr(args, "allow_stale", False)
    report = check_all()
    langs = _languages()
    if not langs:
        print("no translations under docs/translations/")
        return 1
    bad = 0
    for lang in langs:
        probs = report.blocking(lang, allow_stale)
        n = len(report.pages.get(lang, {}))
        stale = report.stale.get(lang, [])
        if probs:
            bad += 1
            print("FAIL %s (%d pages)" % (lang, n))
            for p in probs:
                print("  - %s" % p)
        else:
            print("ok   %s (%d pages%s)" % (lang, n, ", %d out of date" % len(stale) if stale else ""))
        if allow_stale:
            for src in stale:
                print("  ~ out of date, published with a notice: %s/%s" % (lang, src))
    if bad:
        print("%d of %d languages have problems" % (bad, len(langs)))
        return 1
    print("all %d languages match their English pages%s"
          % (len(langs), " (out-of-date pages allowed)" if allow_stale else ""))
    return 0


def cmd_status(_args):
    report = check_all()
    english = [src for _s, src in summary_entries(os.path.join(DOCS, "SUMMARY.md"))]
    print("%-4s %-6s %-8s %-9s %-6s %s" % ("lang", "pages", "machine", "reviewed", "stale", "problems"))
    for lang in _languages():
        st = report.pages.get(lang, {})
        print("%-4s %2d/%-3d %-8d %-9d %-6d %d" % (
            lang, len(st), len(english), sum(1 for v in st.values() if v == "machine"),
            sum(1 for v in st.values() if v == "reviewed"),
            sum(1 for v in st.values() if v == "stale"), len(report.problems.get(lang, []))))
    return 0


def cmd_stamp(args):
    code = 0
    for src in args.pages:
        tpath = os.path.join(TRANSLATIONS, args.lang, src)
        epath = os.path.join(DOCS, src)
        if not (os.path.isfile(tpath) and os.path.isfile(epath)):
            print("no such pair: docs/%s and docs/translations/%s/%s" % (src, args.lang, src))
            code = 1
            continue
        text = read_text(tpath)
        m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
        if not m:
            print("%s/%s: no front matter" % (args.lang, src))
            code = 1
            continue
        lines = [ln for ln in m.group(1).split("\n") if not ln.startswith("source_sha256:")]
        lines.append("source_sha256: %s" % sha256_file(epath))
        with open(tpath, "w", encoding="utf-8") as fh:
            fh.write("---\n%s\n---\n%s" % ("\n".join(lines), text[m.end():]))
        print("stamped %s/%s" % (args.lang, src))
    return code


def cmd_slug(args):
    print(BUILDER.slugify(args.text))
    return 0


def render_glossary():
    data, catalogs = load_glossary()
    names = dict(BUILDER.LANGS)
    out = [
        "# Translation glossary",
        "",
        "Generated by `python3 tools/docs/check_translations.py glossary --write` from",
        "`tools/docs/translation_glossary.json` and the app catalogs in `core/i18n/catalogs/`.",
        "Do not edit by hand. Glossary version: **%s**." % data["version"],
        "",
        "One agreed word per thing, per language, so the docs say what the app and the",
        "Mac actually show. The rules are in [TRANSLATOR_RULES.md](TRANSLATOR_RULES.md).",
        "",
        "## Never translated",
        "",
        "| Term | Rule |",
        "| --- | --- |",
    ]
    for term, rule in data["identity"]:
        out.append("| %s | %s |" % (term, rule))
    out += ["", "Also never translated, in any language: " + data["never_translate"], ""]
    out += ["## Where the words come from", ""]
    for line in data["sources"]:
        out.append("* " + line)
    out.append("")
    en_terms = data["terms"]["en"]
    for code, spec in data["languages"].items():
        labels = labels_for(code, data, catalogs)
        out += ["## %s (`%s`)" % (names.get(code, code), code), ""]
        if spec.get("note"):
            out += [spec["note"], ""]
        out += ["| English | %s | Source |" % names.get(code, code), "| --- | --- | --- |"]
        for item in data["app_labels"]:
            src = ("app: `%s`" % item["key"]) if "key" in item else "fixed"
            if item.get("note"):
                src += " (%s)" % item["note"]
            out.append("| %s | %s | %s |" % (item["en"], labels[item["en"]], src))
        for en in data["os_labels"]:
            out.append("| %s | %s | macOS |" % (en, labels[en]))
        for key, en_word in en_terms.items():
            word = data["terms"].get(code, {}).get(key)
            if word:
                out.append("| %s | %s | term |" % (en_word, word))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def cmd_glossary(args):
    text = render_glossary()
    if args.write:
        with open(GLOSSARY_MD, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote %s" % os.path.relpath(GLOSSARY_MD, REPO))
        return 0
    if args.check:
        if not os.path.isfile(GLOSSARY_MD) or read_text(GLOSSARY_MD) != text:
            print("docs/translations/GLOSSARY.md is out of date: run glossary --write")
            return 1
        print("GLOSSARY.md is current")
        return 0
    sys.stdout.write(text)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check docs translations against the English docs.")
    sub = ap.add_subparsers(dest="cmd")
    ck = sub.add_parser("check")
    ck.add_argument("--allow-stale", action="store_true",
                    help="report out-of-date pages without failing (the site shows them with a notice)")
    sub.add_parser("status")
    st = sub.add_parser("stamp")
    st.add_argument("lang")
    st.add_argument("pages", nargs="+")
    sl = sub.add_parser("slug")
    sl.add_argument("text")
    gl = sub.add_parser("glossary")
    gl.add_argument("--write", action="store_true")
    gl.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    return {"status": cmd_status, "stamp": cmd_stamp, "slug": cmd_slug,
            "glossary": cmd_glossary}.get(args.cmd, cmd_check)(args)


if __name__ == "__main__":
    sys.exit(main())
