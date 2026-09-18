# VOOL Shared Localization System — Groundwork (pass-001)

2026-08-26 · Localization Goblin · **isolated groundwork** — nothing canonical was edited,
nothing pushed, nothing integrated. Everything lives under `localization/`.

## Goal

One language system for every VOOL surface — macOS / Windows / Linux desktop dashboard and
CLI/chat, public web, installer UX, iOS/Android Companion, notifications, voice output, and
signed-receipt status terminology — driven by a single registry with `pt-BR` as a distinct
first-class priority locale (never merged with `pt-PT`).

Reality check from studying the stacks: VOOL's UI today is Python-rendered (HTML dashboards in
`core/dashboard/`, CLI/chat in `apps/`, web pages in `Beta2_Website/core/`, installer copy in
`installer/*.sh|.bat|.ps1`). There is no native Swift/Kotlin UI in this tree yet — so the
adapters below define the export formats those future native shells will consume, instead of
inventing per-platform string files ad hoc later.

## Layout

```
localization/
  registry/
    en.json           source of truth (60 keys curated from REAL extracted strings)
    pt-BR.json        GENERATED translation, complete, labeled unreviewed
    pt-PT.json        GENERATED partial sample proving BR/PT stay distinct
    ar-TORTURE.json   SYNTHETIC RTL stress fixture (never ship)
    qya-pseudo.json   pseudo-locale, tool-generated
  vool_localization/  core package
    message.py        ICU-lite syntax: {var}, {count, plural, ...}, nested, '#'
    plural.py         CLDR-lite rules (en, pt-BR/pt-PT, ar six-category)
    formatting.py     dates / numbers / currencies, stdlib-only, no setlocale
    catalog.py        load, fallback chain (tag -> lang -> en), visible missing policy
    pseudo.py         pseudo-localization generator (accents + ~40% expansion + [!! !!] markers)
    bidi.py           direction detection, FSI/PDI isolation, RLE legacy wrap, torture fragments
    adapters/         apple (.strings/.stringsdict), android (strings.xml/plurals.xml),
                      windows (.resw + Plurals.json sidecar), gettext (.po),
                      web-json (identity interchange), voice (speak-as lexicon + SSML)
  tools/
    extract_strings.py  mechanical inventory of real repo strings (868 found in pass-001)
    audit_strings.py    coverage + placeholder integrity + provenance + expansion budgets
    gen_pseudo_locale.py regenerate qya-pseudo.json deterministically
    demo.py             renders sample screens in all five locales
  tests/              30 pytest tests (all passing)
```

## Contract highlights

- **Keys** are dot-namespaced by surface: `common.*`, `desktop.dashboard.*`, `desktop.cli.*`,
  `web.public.*`, `mobile.companion.*`, `notifications.*`, `receipts.status.*`, `voice.*`.
  Receipt/status codes map 1:1 to the real verdict constants in `core/honesty_receipt.py`
  (`clean`, `no_action_claimed`, `blocked_false_claim`, `unbacked_claim`, `evidence_contradicted`).
- **Fallback**: exact tag → language → `en`. Crucially `pt-PT` never falls back through `pt-BR`;
  that path would silently erase the BR/PT distinction. Under test.
- **Missing strings**: visible `[missing: key (locale)]` marker + recorded for audits;
  strict mode raises.
- **Pluralization**: CLDR categories; Arabic exercises zero/one/two/few/many/other.
- **Dates/numbers/currencies**: explicit per-locale pattern table (e.g. `R$ 1.234,56` in pt-BR vs
  `1 234,56 €` in pt-PT) because Python's `locale` module is process-global and host-dependent.
- **RTL**: registry stores logical-order Unicode; adapters/renderers call `bidi.isolate()` /
  `bidi_wrap_paragraph()`. The torture fixture mixes Arabic + "VOOL" + `null://` URIs + digits +
  currencies so reordering bugs surface immediately.
- **Pseudo-localization**: `[!! … !!]` markers catch truncation, accented lookalikes catch font
  gaps, vowel inflation (~1.7× here) catches layout overflow; audit enforces a 2× budget on short
  labels (.tab/.button/.label).
- **Layout expansion**: covered by the pseudo budget above plus per-surface length heuristics in
  the audit; native shells should treat pseudo-locale screenshots as a release gate.
- **Voice**: `voice.py` exports speak-as expansions (verdict codes and `R$` spoken properly) and
  SSML pause hints consumed by TTS on any platform.
- **Provenance**: every generated locale carries `_meta.generated=true, human_reviewed=false`;
  the audit fails if a generated locale claims review it does not have, and web/adapter exports
  propagate the flag so consumers cannot mistake machine drafts for reviewed copy.

## Generated-content disclosure

All pt-BR, pt-PT, ar-TORTURE and qya-pseudo content in this pass is **machine-generated, not
human-reviewed**. Brazilian Portuguese reviewers must sign off before any of it ships.

## Run it

```
cd localization
PYTHONPATH=. python3 tools/demo.py                       # see the system work
PYTHONPATH=. python3 -m pytest tests -q                  # 30 tests
python3 tools/extract_strings.py --markdown --out /tmp/inv.json
PYTHONPATH=. python3 tools/audit_strings.py
```

## Next passes (not started)

1. Human review sign-off for pt-BR (priority) via a review workflow on `_meta`.
2. Key-coverage ramp: fold extracted inventory (868 strings) into `en.json` namespace by namespace.
3. Wire adapters' output formats as build artifacts once native shells exist (iOS/macOS `.lproj`,
   Android `res/values-*`, Windows `.resw`, Linux `.po`).
4. ICU-lite → full ICU MessageFormat if select/matchOrdinal needs appear.
