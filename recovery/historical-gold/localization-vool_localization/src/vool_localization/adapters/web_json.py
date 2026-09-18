"""Web/JSON adapter — the identity format used by dashboards and public web.

Also the canonical interchange format for any future Electron/Tauri shell.
Emits a flat JSON dict plus a header object carrying generation provenance,
so no consumer can mistake generated strings for human-reviewed ones.
"""
from __future__ import annotations

import json
from pathlib import Path

from vool_localization.adapters.base import PlatformAdapter


class WebJsonAdapter(PlatformAdapter):
    name = "web-json"

    def export(self, locale: str, out_dir: Path) -> list[Path]:
        self._ensure_dir(out_dir)
        meta = self.catalog.metas.get(locale)
        payload = {
            "$schema": "vool.localization.web.v1",
            "locale": locale,
            "generated": bool(meta.generated) if meta else False,
            "human_reviewed": bool(meta.human_reviewed) if meta else False,
            "direction": self.catalog.direction(locale),
            "strings": self.flat(locale),
        }
        path = out_dir / f"{locale}.web.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return [path]
