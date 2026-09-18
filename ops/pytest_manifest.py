"""Collect pytest's canonical item and file manifest without parsing console output."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest


class _ManifestPlugin:
    def __init__(self, *, repo_root: Path, output_path: Path) -> None:
        self._repo_root = repo_root.resolve()
        self._output_path = output_path

    def pytest_collection_finish(self, session: Any) -> None:
        targets: set[str] = set()
        items: list[dict[str, str]] = []
        for item in session.items:
            item_path = Path(str(item.path)).resolve()
            relative_path = item_path.relative_to(self._repo_root).as_posix()
            targets.add(relative_path)
            items.append({"nodeid": str(item.nodeid), "path": relative_path})
        payload = {
            "schema": "vool.pytest-manifest.v1",
            "item_count": len(session.items),
            "targets": sorted(targets),
            "items": sorted(items, key=lambda item: item["nodeid"]),
        }
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write the canonical pytest collection manifest as JSON."
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    for variable in ("PYTEST_ADDOPTS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTEST_PLUGINS"):
        os.environ.pop(variable, None)
    plugin = _ManifestPlugin(
        repo_root=Path(args.repo_root),
        output_path=Path(args.output),
    )
    return int(
        pytest.main(
            ["--collect-only", "-q", "-o", "addopts=", *pytest_args], plugins=[plugin]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
