"""VOOL shared localization groundwork (pass-001).

One language system for every VOOL surface — desktop dashboard, CLI/chat,
public web, installer, Companion/mobile, notifications, voice, and
receipt/status terminology. Isolated groundwork: nothing here edits or
requires canonical foundation code.

Layers:
    message      — ICU-lite message syntax ({var}, {n, plural, ...})
    plural       — CLDR-lite cardinal plural rules (en, pt, ar)
    formatting   — dates, numbers, currencies per locale (stdlib only)
    catalog      — the shared registry: load, fallback chain, missing policy
    pseudo       — pseudo-localization generator (accents + expansion)
    bidi         — RTL helpers (isolates, direction detection, torture data)
    adapters     — platform export formats (.strings/.stringsdict, .resw,
                   strings.xml, .po, web JSON, voice SSML lexicon)

Generated translations carry ``_meta.generated = true`` and
``_meta.human_reviewed = false`` until reviewed by humans.
"""
from vool_localization.catalog import Catalog

__all__ = ["Catalog"]
