#!/usr/bin/env python3
"""One language system — live demo across locales, surfaces, and directions.

Renders the same three sample screens (dashboard header, notification,
signed-receipt summary) in en, pt-BR, pt-PT, pseudo-locale and RTL Arabic
torture data, showing plural selection, variables, currency/date formatting,
fallback chains, and bidi isolation working off one shared registry.

    PYTHONPATH=. python3 localization/tools/demo.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

LOC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOC))

from vool_localization import bidi
from vool_localization.catalog import Catalog

NOW = dt.datetime(2026, 8, 26, 14, 5)


def render(cat: Catalog, locale: str) -> None:
    meta = cat.metas[locale]
    print(f"\n=== {locale} — {meta.label}")
    print(f"    generated={meta.generated} human_reviewed={meta.human_reviewed} "
          f"direction={cat.direction(locale)}")
    d = cat.direction(locale)
    lines = [
        cat.t("desktop.dashboard.section.proof_of_useful_work", locale),
        cat.t("notifications.tasks_completed", locale, count=3),
        cat.t("receipts.status.blocked_false_claim", locale),
        cat.t("receipts.count_line", locale, count=1),
        cat.t("desktop.cli.agent_ready", locale, name="Alex"),
    ]
    for line in lines:
        text = bidi.bidi_wrap_paragraph(line, d) if d == "rtl" else line
        origin = cat.origin(
            "notifications.tasks_completed" if "# tasks" in line or "مهام" in line else "",
            locale)
        prefix = "RTL| " if d == "rtl" else "    "
        print(f"  {prefix}{text}")
    print(f"  cost: {cat.currency(1234.56, 'BRL', locale) if locale.startswith('pt') else cat.currency(99.0, 'USD', locale)}"
          f" | date: {cat.date(NOW.date(), locale)} {cat.date(NOW, locale, kind='time')}")
    # fallback demonstration: key only en has, requested in pt-PT
    print(f"  fallback: desktop.cli.bye@pt-PT -> {cat.t('desktop.cli.bye', 'pt-PT')!r} "
          f"(origin={cat.origin('desktop.cli.bye', 'pt-PT')})")


def main() -> int:
    cat = Catalog.load(LOC / "registry")
    for locale in ("en", "pt-BR", "pt-PT", "qya-pseudo", "ar-TORTURE"):
        render(cat, locale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
