#!/usr/bin/env python3
"""translate_error_book.py — the Error Book in each docs language, from the app's own words.

docs/ERROR_BOOK.md is generated from the fault catalog by tools/generate_error_book.py. For each
code it shows what the app shows: the message and the next step. The app's catalogs already
hold those two texts in every app language (`fault.<code>.message`, `fault.<code>.action` in
core/i18n/catalogs/, the catalog the glossary names for each docs language), so a translated
book does not translate them again: it takes the app's words, exactly as the app shows them in
that language. The book's own words (the titles, headings, the effect and retry phrases, the
introduction) are in tools/docs/error_book_words.json, one entry per language.

    python3 tools/docs/translate_error_book.py            # write every language
    python3 tools/docs/translate_error_book.py de ja      # only these
    python3 tools/docs/translate_error_book.py --check    # exit 1 if any is not current

Writes docs/translations/<lang>/ERROR_BOOK.md, stamped `translation: machine` with the hash of
the English book, so the docs site and tools/docs/check_translations.py treat it like any other
translated page. Run it again whenever docs/ERROR_BOOK.md or an app catalog changes.

The layout follows tools/generate_error_book.py line for line: built with the English words
from the JSON, it must reproduce docs/ERROR_BOOK.md byte for byte (`--check` checks that too),
so a change to the English layout cannot leave the translations quietly behind.
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tools import generate_error_book as book  # (needs the repository on the path)

WORDS_JSON = os.path.join(HERE, "error_book_words.json")
GLOSSARY_JSON = os.path.join(HERE, "translation_glossary.json")
DOCS = os.path.join(REPO, "docs")
ENGLISH_BOOK = os.path.join(DOCS, "ERROR_BOOK.md")
CATALOGS = os.path.join(REPO, "core", "i18n", "catalogs")

# The comment the English book carries, kept verbatim: the docs checker compares inline code.
GENERATED_NOTE = book._GENERATED_NOTE
TRANSLATED_NOTE = ("<!-- GENERATED FILE: the app's own texts for each code, and the words in "
                   "tools/docs/error_book_words.json. Regenerate with "
                   "tools/docs/translate_error_book.py, never by hand. -->")


def load_words():
    with open(WORDS_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def load_messages(lang):
    """The app catalog of a docs language. The glossary names it (Chinese is zh-Hans)."""
    with open(GLOSSARY_JSON, encoding="utf-8") as fh:
        tag = json.load(fh)["languages"][lang]["catalog"]
    with open(os.path.join(CATALOGS, "%s.json" % tag), encoding="utf-8") as fh:
        return json.load(fh)["messages"]


def _effect(words, fault):
    return words["effect"].get(str(fault.get("effect") or ""), "")


def _retry(words, fault):
    return words["retry"].get(str(fault.get("retry") or ""), "")


def build(words, messages, translated):
    """The book in one language. `messages` is that language's app catalog; `translated` adds
    the second generated-file note (the English book has only the first)."""
    catalog = book.export_catalog()
    faults = list(catalog["faults"])
    digest = book.catalog_digest()
    ordered, by_category = book._by_category(faults)
    stamp = words["stamp"]

    def text(fault, field, key):
        if not translated:
            return fault[field]
        value = messages.get("fault.%s.%s" % (fault["code"], key))
        if not value:
            raise SystemExit("app catalog has no fault.%s.%s" % (fault["code"], key))
        return value

    def title(fault):
        return words["titles"][fault["code"]] if translated else fault["title"]

    lines = ["# " + words["title"], "", GENERATED_NOTE, ""]
    if translated:
        lines += [TRANSLATED_NOTE, ""]
    lines += [*words["intro"], ""]
    lines += [
        "- **%s** `%s`" % (stamp["schema"], book.FAULT_SCHEMA),
        "- **%s** `%s`" % (stamp["version"], book.CATALOG_VERSION),
        "- **%s** `%s`" % (stamp["digest"], digest),
        "- **%s** %d" % (stamp["count"], len(faults)),
        "",
    ]
    lines += [*words["how_to_read"], ""]
    head = words["table_head"]
    for category in ordered:
        rows = sorted(by_category[category], key=lambda f: str(f.get("code")))
        lines.append("## " + words["category_heading"][category])
        lines.append("")
        lines.append("| %s | %s | %s | %s |" % tuple(head))
        lines.append("|---|---|---|---|")
        for f in rows:
            lines.append("| [`{code}`](#{anchor}) | {title} | {sev} | {effect} |".format(
                code=book._cell(f.get("code")), anchor=book._cell(f.get("doc_anchor")),
                title=book._cell(title(f)), sev=book._cell(words["severity"][f["severity"]]),
                effect=book._cell(_effect(words, f))))
        lines.append("")
    lines += ["## " + words["reference_heading"], "", *words["reference_intro"], ""]
    field = words["fields"]
    for f in sorted(faults, key=lambda f: str(f.get("code"))):
        security = (" · **%s** %s" % (field["security_relevant"], field["yes"])
                    if f.get("security_relevant") else "")
        lines += [
            '<a name="%s"></a>' % f["doc_anchor"],
            "",
            "### %s" % f["code"],
            "",
            "**%s**" % title(f),
            "",
            "- **%s** %s" % (field["happened"], text(f, "user_message", "message")),
            "- **%s** %s" % (field["already"], _effect(words, f)),
            "- **%s** %s" % (field["next"], text(f, "operator_action", "action")),
            "- **%s** %s" % (field["retry"], _retry(words, f)),
            "- **%s** %s · **%s** %s%s" % (field["severity"], words["severity"][f["severity"]],
                                           field["category"], words["category"][f["category"]],
                                           security),
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def translated_page(lang, words):
    with open(ENGLISH_BOOK, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    body = build(words["languages"][lang], load_messages(lang), translated=True)
    return "---\ntranslation: machine\nsource_sha256: %s\n---\n\n%s" % (sha, body)


def check_english(words):
    """The English words through this layout must give the English book exactly."""
    with open(ENGLISH_BOOK, encoding="utf-8") as fh:
        current = fh.read()
    return build(words["en"], {}, translated=False) == current == book.build_error_book_markdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 if a translated book is not what this makes")
    parser.add_argument("--out", metavar="DIR",
                        help="write <DIR>/<lang>/ERROR_BOOK.md instead of into docs/translations")
    parser.add_argument("langs", nargs="*")
    args = parser.parse_args(argv)
    words = load_words()
    if not check_english(words):
        print("the English words no longer reproduce docs/ERROR_BOOK.md: the English layout or "
              "book changed; update this tool and tools/docs/error_book_words.json "
              "(and run python -m tools.generate_error_book if the book is stale)")
        return 1
    langs = args.langs or sorted(words["languages"])
    bad = 0
    for lang in langs:
        if lang not in words["languages"]:
            print("no words for %s in tools/docs/error_book_words.json" % lang)
            bad += 1
            continue
        missing = sorted(set(f["code"] for f in book.export_catalog()["faults"])
                         - set(words["languages"][lang]["titles"]))
        if missing:
            print("%s: no title for %s" % (lang, ", ".join(missing)))
            bad += 1
            continue
        page = translated_page(lang, words)
        path = os.path.join(args.out or os.path.join(DOCS, "translations"), lang, "ERROR_BOOK.md")
        if args.check:
            current = None
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    current = fh.read()
            if current != page:
                print("%s: %s is not current; run tools/docs/translate_error_book.py %s"
                      % (lang, os.path.relpath(path, REPO), lang))
                bad += 1
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page)
        print("wrote %s" % (path if args.out else os.path.relpath(path, REPO)))
    if args.check and not bad:
        print("%d translated Error Books are current" % len(langs))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
