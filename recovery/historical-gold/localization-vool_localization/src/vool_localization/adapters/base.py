"""Platform adapter contract.

Every adapter converts a Catalog into that platform's native resource format.
Adapters are pure exporters: they read the shared registry and write files;
they never mutate it.
"""
from __future__ import annotations

from pathlib import Path

from vool_localization.catalog import Catalog


class PlatformAdapter:
    name = "abstract"
    file_extension = ".txt"
    supports_plurals = True

    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def export(self, locale: str, out_dir: Path) -> list[Path]:
        raise NotImplementedError

    # helpers -------------------------------------------------------------
    def keys(self, locale: str) -> list[str]:
        return sorted(self.catalog.strings.get(locale, {}))

    def flat(self, locale: str) -> dict[str, str]:
        return self.catalog.strings.get(locale, {})

    def _ensure_dir(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir
