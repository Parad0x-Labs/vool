# Translator rules for the VOOL docs

These rules apply to every page under `docs/translations/<lang>/`. The checker,
`python3 tools/docs/check_translations.py`, enforces the mechanical ones; a reviewer who
reads the language enforces the rest. The agreed words for each language are in
[GLOSSARY.md](GLOSSARY.md).

The docs are translated into the languages the app itself speaks: Arabic (`ar`), German
(`de`), Spanish (`es`), French (`fr`), Hebrew (`he`), Hindi (`hi`), Japanese (`ja`),
Korean (`ko`), Lithuanian (`lt`), Polish (`pl`), Brazilian Portuguese (`pt`), Russian (`ru`),
Turkish (`tr`), Ukrainian (`uk`), Vietnamese (`vi`) and Simplified Chinese (`zh`). Arabic and
Hebrew pages are shown right to left; the site sets the direction.

## Workflow

1. Translate the English page `docs/<path>` into `docs/translations/<lang>/<path>`. Start the
   file with a front matter block holding a translated `description:` and
   `translation: machine`.
2. List the page in `docs/translations/<lang>/SUMMARY.md`. A translated SUMMARY starts with
   `README.md` and lists only translated pages, in the English order and sections.
3. Record which English text you translated:
   `python3 tools/docs/check_translations.py stamp <lang> <path>`. This writes
   `source_sha256:`, the hash of the English file. Never type it by hand.
4. Run `python3 tools/docs/check_translations.py` until it prints that every language matches
   its English pages. Then build the site and run the publish gate
   ([tools/docs/README.md](../../tools/docs/README.md)).

If you change `tools/docs/translation_glossary.json` or an app catalog, run
`python3 tools/docs/check_translations.py glossary --write` so GLOSSARY.md stays current.

## Status of a page

* `translation: machine` means a machine translation that no native reader has checked yet.
  The site shows a short note on the page with a link to the English page.
* `translation: reviewed` with `reviewer: <name or handle>` means a named person who reads the
  language checked the page against the English. Never set it without that person.
* **Out of date** is computed, never written: the English page changed after the translation
  was stamped. The site still publishes the page, with a notice in that language that the
  English page has changed and a link to it, and the build log lists the page. `check` fails
  on an out-of-date page; `check --allow-stale` only reports it. To clear it, translate what
  changed in the English page and stamp the page again.

## Mechanical rules (checked)

1. **Code stays as it is.** Code blocks are byte for byte the English ones. Inline code, URLs,
   commands, file and folder names, version numbers, ports and checksums do not change.
2. **Same shape.** The same headings (levels and order), tables (rows and columns), hints
   (styles and order) and list items as the English page. Heading text is translated.
3. **Same numbers.** Every number the English page has, and no others, in Western digits
   (0-9). Write "a third", not a fraction the English does not have. A language that writes
   a decimal comma may use it for measured values such as 82,7 %, never in a version
   number. Digits that are part of an app label do not count.
4. **Links go where the English links go.** A page translated in this language is linked in
   this language. Any other page is linked to the English file by climbing out of the
   translation folder (from `getting-started/` that is `../../../guides/local-models.md`);
   the site marks such a link as English. A `#section` must point at the matching heading:
   for a translated page, use the anchor the site gives the translated heading
   (`python3 tools/docs/check_translations.py slug "<heading>"`).
5. **The app's own words.** Every label the English page names, in bold, in quotes or in a
   menu path, is written exactly as the app shows it in that language. The words come from
   the app's catalog for the language and are listed in GLOSSARY.md. If the app's word reads
   badly, still use it and report it, so the app text is fixed first and the docs follow.
6. **The Mac's own words.** macOS buttons, menus and folders are written exactly as Apple
   writes them in that language (GLOSSARY.md, "macOS" rows).
7. **No machine paths**, usernames or personal names on a page.

## Judgment rules (reviewed)

8. **Say only what the English says.** No new features, limits, dates, links or support
   claims. If something reads wrong in English, fix the English first.
9. **Warnings keep their weight.** "Never", "only", "off by default" and every security
   statement read as firmly as in English.
10. **One thing, one word.** Use the GLOSSARY.md term everywhere in a language. Where the app
    already has a word for something, use the app's word.
11. **Speak the way the app speaks.** Plain and direct, with the form of address the app
    uses in that language: for example `du` in German, the plural imperative in Hebrew,
    `vous` in French.
12. **Names stay names.** VOOL, Parad0x Labs, product, platform and hardware names stay in
    Latin letters, as in the GLOSSARY.md table "Never translated".

## Special cases

* **The macOS warning's Done and Cancel buttons.** Apple's words for them were not verified
  in every language, so a translation says to close the message instead of naming a button.
* **Lithuanian.** macOS has no Lithuanian interface, so Lithuanian pages keep Apple's English
  words, and the install page says so once. Hindi pages keep About This Mac in English, as
  Apple's Hindi pages do.
* **Certify** is the 0.7.7 button, which the app shows in English in every language, so it
  stays in English. When the English pages name Use this model instead, translations use the
  catalog words for `settings.local_models.use`.
* **Right to left.** Write Arabic and Hebrew normally and use `←` in menu paths. If a Latin
  name next to brackets or punctuation shows in the wrong order, put a right-to-left mark
  (U+200F) before the bracket, or a left-to-right mark (U+200E) after the Latin
  punctuation, and look at the page in a browser.

## Reviewing

Read the translated page next to the English one in the built site. Check the judgment
rules, click every link, and compare each app label with the app running in that language.
Then set `translation: reviewed` and `reviewer:`, keep `source_sha256:` as stamped, and run
the checker again.
