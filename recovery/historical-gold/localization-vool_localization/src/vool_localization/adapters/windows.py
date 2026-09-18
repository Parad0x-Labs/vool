"""Windows adapter (.resw) for WinUI/UWP resource loading.

.resw is strings-only; plural handling on Windows goes through
WinRT's plural-aware APIs fed by the same ICU templates, exported here as
``_Plural.json`` sidecar per locale.
"""
from __future__ import annotations

import json
from pathlib import Path

from vool_localization.adapters.base import PlatformAdapter
from vool_localization.pseudo import PLACEHOLDER


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class WindowsAdapter(PlatformAdapter):
    name = "windows"

    def export(self, locale: str, out_dir: Path) -> list[Path]:
        d = self._ensure_dir(out_dir) / locale.replace("-", "_")
        d.mkdir(parents=True, exist_ok=True)
        path = d / "Resources.resw"
        lines = ['<?xml version="1.0" encoding="utf-8"?>', "<root>", "  <data>"]
        # real structure: one <data name=...><value>...</value></data> block each
        body: list[str] = []
        plurals: dict[str, str] = {}
        for key in self.keys(locale):
            template = self.flat(locale)[key]
            m = PLACEHOLDER.search(template)
            if m and ",plural," in m.group(0):
                plurals[key] = template
                continue
            body.append(f"  <data name=\"{key}\" xml:space=\"preserve\">")
            body.append(f"    <value>{_escape(template)}</value>")
            body.append("  </data>")
        path.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<root>\n'
            + "\n".join(body)
            + "\n</root>\n",
            encoding="utf-8",
        )
        written = [path]
        if plurals:
            sidecar = d / "Plurals.json"
            sidecar.write_text(json.dumps(plurals, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(sidecar)
        return written
