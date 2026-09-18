"""Android adapter: res/values-*/strings.xml with <plurals> + <item>.

Also emits ``rtl.xml`` boolean resource when the locale is RTL so layouts can
flip via ``android:supportsRtl`` conventions instead of hardcoded checks.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from vool_localization.adapters.base import PlatformAdapter
from vool_localization.bidi import direction
from vool_localization.message import parse_placeholder, tokenize_braces


def _android_escape(text: str) -> str:
    out = text
    for old, new in [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "\\\""), ("\n", "\\n")]:
        out = out.replace(old, new)
    # protect ICU placeholders from Android formatter parsing unless positional
    return out.replace("'", "\\'")


def _icu_to_android_positional(text: str) -> str:
    """Android's String.format needs %1$s style positions; convert {name}."""
    out: list[str] = []
    i = 0
    order: dict[str, int] = {}
    while i < len(text):
        c = text[i]
        if c == "{":
            inner, i = tokenize_braces(text, i)
            name, arg_type, _rest = parse_placeholder(inner)
            if arg_type == "plural":
                out.append(_render_android_plural(inner))
            else:
                idx = order.setdefault(name, len(order) + 1)
                out.append(f"%{idx}$s")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _render_android_plural(inner: str) -> str:
    # keep ICU plural body verbatim — Android's getQuantityString consumes ICU syntax natively
    return "{" + inner + "}"


class AndroidAdapter(PlatformAdapter):
    name = "android"

    def values_dir_name(self, locale: str) -> str:
        tag = locale.lower().replace("_", "-")
        parts = tag.split("-")
        if len(parts) == 1:
            return f"values-{parts[0]}"
        return f"values-{parts[0]}-r{parts[1].upper()}"

    def export(self, locale: str, out_dir: Path) -> list[Path]:
        d = self._ensure_dir(out_dir) / self.values_dir_name(locale)
        d.mkdir(parents=True, exist_ok=True)
        path = d / "strings.xml"
        root = ET.Element("resources")
        plurals_keys: set[str] = set()
        for key in self.keys(locale):
            template = self.flat(locale)[key]
            converted = _icu_to_android_positional(template)
            if "{count" in converted or ",plural," in converted:
                plurals_keys.add(key)
                continue
            el = ET.SubElement(root, "string", {"name": _resource_name(key)})
            el.text = _android_escape(converted)
        written = []
        ET.indent(root, space="  ")
        path.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
        written.append(path)

        if plurals_keys:
            proot = ET.Element("resources")
            for key in sorted(plurals_keys):
                pel = ET.SubElement(proot, "plurals", {"name": _resource_name(key)})
                item_el = ET.SubElement(pel, "item")
                item_el.text = _android_escape(self.flat(locale)[key])
            ET.indent(proot, space="  ")
            ppath = d / "plurals.xml"
            ppath.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(proot, encoding="unicode") + "\n", encoding="utf-8")
            written.append(ppath)

        if direction(locale) == "rtl":
            rroot = ET.Element("resources")
            b = ET.SubElement(rroot, "bool", {"name": "is_rtl"})
            b.text = "true"
            ET.indent(rroot, space="  ")
            rpath = d / "rtl.xml"
            rpath.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(rroot, encoding="unicode") + "\n", encoding="utf-8")
            written.append(rpath)
        return written


def _resource_name(key: str) -> str:
    # registry keys are dot-separated; Android wants snake_case identifiers
    name = key.replace(".", "_").replace("-", "_")
    if name[0].isdigit():
        name = "s_" + name
    return name
