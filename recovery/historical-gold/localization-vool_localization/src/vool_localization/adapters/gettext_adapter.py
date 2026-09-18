"""Gettext adapter (.po) — Linux desktop / CLI convention.

ICU plural templates are exported as msgid/msgstr_plural pairs using gettext
plural-form conventions where the target locale has them (pt: 2 forms),
otherwise as plain msgstr with the ICU template preserved verbatim so the
runtime formatter can process it.
"""
from __future__ import annotations

from pathlib import Path

from vool_localization.adapters.base import PlatformAdapter
from vool_localization.pseudo import PLACEHOLDER

PO_PLURAL_FORMS = {
    "en": "nplurals=2; plural=(n != 1);",
    "pt": "nplurals=2; plural=(n > 1);",       # pt-PT convention
    "pt-br": "nplurals=2; plural=(n > 1);",    # pt-BR convention
    "ar": "nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5);",
}


def _po_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class GettextAdapter(PlatformAdapter):
    name = "gettext"

    def export(self, locale: str, out_dir: Path) -> list[Path]:
        lang = locale.lower().replace("_", "-")
        d = self._ensure_dir(out_dir) / lang
        d.mkdir(parents=True, exist_ok=True)
        path = d / "messages.po"
        lines = [
            'msgid ""',
            'msgstr ""',
            f'"Language: {lang}\\n"',
            f'"Plural-Forms: {PO_PLURALS.get(lang, PO_PLURALS["en"])}\\n"',
            "",
        ]
        for key in self.keys(locale):
            template = self.flat(locale)[key]
            m = PLACEHOLDER.search(template)
            if m and ",plural," in m.group(0):
                lines += [f'msgid "{_po_escape(key)}"', 'msgid_plural ""',
                          f'msgstr[0] "{_po_escape(template)}"', '']
            else:
                lines += [f'#. {key}', f'msgid "{_po_escape(key)}"',
                          f'msgstr "{_po_escape(template)}"', '']
        path.write_text("\n".join(lines), encoding="utf-8")
        return [path]


PO_PLURALS = PO_PLURAL_FORMS
