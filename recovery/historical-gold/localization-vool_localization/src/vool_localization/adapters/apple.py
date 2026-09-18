"""Apple adapter (macOS + iOS): .strings for plain strings, .stringsdict for plurals.

Output per locale:
    <locale>.lproj/Localizable.strings      "key" = "value";  (UTF-8)
    <locale>.lproj/Localizable.stringsdict  plural rules as keyed dicts

Plural templates in the registry use ICU syntax; here they are decomposed
into Apple's variable-width rule format (zero/one/two/few/many/other).
"""
from __future__ import annotations

import json
from pathlib import Path

from vool_localization.adapters.base import PlatformAdapter
from vool_localization.message import parse_plural_branches
from vool_localization.pseudo import PLACEHOLDER


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _is_plural(template: str) -> bool:
    m = PLACEHOLDER.search(template)
    return bool(m and ",plural," in m.group(0))


class AppleAdapter(PlatformAdapter):
    name = "apple"
    supports_plurals = True

    def export(self, locale: str, out_dir: Path) -> list[Path]:
        d = self._ensure_dir(out_dir) / f"{_lproj_name(locale)}.lproj"
        d.mkdir(parents=True, exist_ok=True)
        strings_path = d / "Localizable.strings"
        dict_path = d / "Localizable.stringsdict"

        plain: list[str] = []
        plurals: dict[str, dict] = {}
        for key in self.keys(locale):
            template = self.flat(locale)[key]
            if _is_plural(template):
                plurals[key] = self._stringsdict_entry(key, template, locale)
            else:
                plain.append(f'"{_escape(key)}" = "{_escape(template)}";\n')
        strings_path.write_text("".join(plain), encoding="utf-8")
        if plurals:
            dict_path.write_text(json.dumps(plurals, ensure_ascii=False, indent=2), encoding="utf-8")
        return [p for p in (strings_path, dict_path if plurals else None) if p]

    def _stringsdict_entry(self, key: str, template: str, locale: str) -> dict:
        m = PLACEHOLDER.search(template)
        inner = m.group(0)[1:-1]
        var = inner.split(",", 1)[0].strip()
        spec = inner.split(",", 2)[2]
        categories: dict[str, str] = {}
        for keyword, branch in parse_plural_branches(spec):
            fmt = branch.replace("#", "%#@var@").replace("{", "%").replace("}", "@")
            # Apple uses %#@var@ to reference the count; rebuild simple %var placeholders
            fmt = fmt.replace(f"%{var}@", f"%#@{var}@") if f"%{var}@" in fmt else fmt
            categories[keyword] = fmt
        categories.setdefault("NSStringLocalizedFormatKey", "%#@var@")
        return {
            "NSStringLocalizedFormatKey": categories.pop("NSStringLocalizedFormatKey"),
            var: {
                "NSStringFormatSpecTypeKey": "NSStringPluralRuleType",
                "NSStringFormatValueTypeKey": "d",
                **{k: categories[k] for k in ("zero", "one", "two", "few", "many", "other") if k in categories},
            },
        }


def _lproj_name(locale: str) -> str:
    # pt-BR -> pt-BR.lproj (Apple accepts region-suffixed lproj names)
    return locale.replace("_", "-")
