"""The media engine ships inside every app bundle, under the MIT licence its owner chose.

`vendor/nebula-media` is Media Studio's worker: core.media_worker_client finds it beside the code,
at app/vendor/nebula-media in a bundle. Until 2026-11-12 its licence was "not yet recorded", so
none of the three bundle scripts staged it and a packaged app had no worker. Its owner chose MIT,
so the licence now sits in the folder and in docs/THIRD_PARTY_LICENSES.md, and the macOS, Linux
and Windows bundle scripts stage the folder. ffmpeg and ffprobe are still not bundled: the worker
runs them from the computer, as before.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "vendor" / "nebula-media"
BUNDLE = REPO / "installer" / "bundle"


def test_the_engine_carries_its_mit_licence():
    text = (ENGINE / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n")
    assert "Permission is hereby granted, free of charge" in text
    assert re.search(r"^Copyright \(c\) \d{4} \S", text, re.MULTILINE)


def test_the_licence_is_on_record():
    record = (REPO / "docs" / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
    entry = record.split("### vendor/nebula-media", 1)[1].split("\n## ", 1)[0]
    assert "MIT (`vendor/nebula-media/LICENSE`)" in entry
    assert "not yet recorded" not in record
    assert "Licence     : MIT" in (ENGINE / "PROVENANCE.md").read_text(encoding="utf-8")


def test_every_bundle_script_stages_the_engine():
    mac = (BUNDLE / "build_macos_app.sh").read_text(encoding="utf-8")
    packages = re.search(r"^SRC_PACKAGES=\(([^)]*)\)", mac, re.MULTILINE)
    assert packages and "vendor/nebula-media" in packages.group(1).split()
    linux = (BUNDLE / "build_linux_bundle.sh").read_text(encoding="utf-8")
    archive = re.search(r'^git archive "\$\{SHA\}" ([^\n]*)', linux, re.MULTILINE)
    assert archive and "vendor/nebula-media" in archive.group(1).split()
    windows = (BUNDLE / "build_bundle.ps1").read_text(encoding="utf-8")
    assert '"vendor\\nebula-media"' in windows
    # A nested entry needs its parent folder made first, on both copy paths that are not git.
    assert 'mkdir -p "$(dirname "${res}/app/${d}")"' in mac
    assert "New-Item -ItemType Directory -Force (Split-Path -Parent $dest)" in windows


def test_a_bundle_finds_the_engine_beside_its_code(monkeypatch):
    from core import media_worker_client

    monkeypatch.delenv("NEBULA_MEDIA_HOME", raising=False)
    # Every bundle stages core/ and vendor/nebula-media side by side under app/.
    assert media_worker_client.VENDORED_NEBULA_HOME == ENGINE
    assert media_worker_client.default_nebula_home() == str(ENGINE)
    assert (ENGINE / "nebula" / "worker.py").is_file()


def test_the_engine_needs_nothing_the_bundles_do_not_ship():
    """Standard library only, so no bundle installs anything for it."""
    for path in sorted((ENGINE / "nebula").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue  # the engine's own modules
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                assert top == "nebula" or top in sys.stdlib_module_names, f"{path.name} imports {name}"
