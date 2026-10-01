"""The docs-translation checker must examine THIS repository and never pass vacuously.

Root cause being pinned (2026-10-01): ``tools/i18n_check_translations.py`` declared
``parents[2]`` while living directly in ``tools/``, so its "repository root" pointed one
directory ABOVE the repo. Every scan then found zero canonical sources, and ``main()``
still printed "check complete" / returned 0 without ``--check`` — a zero-document scan
read as a pass.

Pins:
- the tool's resolved REPO is the repository that contains ``core/i18n`` (the
  parents-depth regression control);
- against a real source + translation fixture the checker detects staleness and
  invariant drift (it examines documents, not just directories);
- a zero-document scan is an explicit failure return from ``main()``, never a pass.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL_PATH = REPO / "tools" / "i18n_check_translations.py"


def _load_tool():
    import sys

    spec = importlib.util.spec_from_file_location("i18n_check_translations_under_test", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    # The tool's dataclasses require the module to be importable during exec.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def test_repo_resolution_points_at_this_repository():
    tool = _load_tool()
    assert tool.REPO == REPO
    # The resolved root must be the one that actually carries the product's i18n
    # authority — the exact drift the flattened tools/ location introduced.
    assert (tool.REPO / "core" / "i18n" / "catalog.py").is_file()


def _write_fixture(root: Path, *, stale_hash: bool, drop_fences: bool = False) -> None:
    sources = root / "docs" / "i18n" / "sources"
    sources.mkdir(parents=True)
    source = "# Title\n\nIntro text.\n\n```bash\nvool serve\n```\n"
    (sources / "quickstart.md").write_text(source, encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(source.encode()).hexdigest()
    if stale_hash:
        digest = "0" * 64
    body = f"""<!--
locale: lt
source: quickstart.md
canonical_source_sha256: {digest}
translation_status: MACHINE_DRAFT
-->

# Pavadinimas

Įvadinis tekstas.

```bash
vool serve
```
"""
    if drop_fences:
        body = f"""<!--
locale: lt
source: quickstart.md
canonical_source_sha256: {digest}
translation_status: MACHINE_DRAFT
-->

# Pavadinimas

Įvadinis tekstas.

```bash
KITAS-KOMANDA
```
"""
    (root / "docs" / "i18n" / "lt" / "quickstart.md").parent.mkdir(parents=True)
    (root / "docs" / "i18n" / "lt" / "quickstart.md").write_text(body, encoding="utf-8")


def test_checker_detects_stale_source_hash(tmp_path, monkeypatch):
    tool = _load_tool()
    _write_fixture(tmp_path, stale_hash=True)
    monkeypatch.setattr(tool, "REPO", tmp_path)
    monkeypatch.setattr(tool, "SOURCES_DIR", tmp_path / "docs" / "i18n" / "sources")
    monkeypatch.setattr(tool, "DOCS_I18N_DIR", tmp_path / "docs" / "i18n")
    results = tool.check_all()
    status = results["lt"]["quickstart"]
    assert status.status == "STALE"
    assert any("hash" in p for p in status.problems)


def test_checker_detects_code_token_drift(tmp_path, monkeypatch):
    tool = _load_tool()
    _write_fixture(tmp_path, stale_hash=False, drop_fences=True)
    monkeypatch.setattr(tool, "REPO", tmp_path)
    monkeypatch.setattr(tool, "SOURCES_DIR", tmp_path / "docs" / "i18n" / "sources")
    monkeypatch.setattr(tool, "DOCS_I18N_DIR", tmp_path / "docs" / "i18n")
    results = tool.check_all()
    status = results["lt"]["quickstart"]
    assert status.status == "STALE"
    assert any("code tokens" in p for p in status.problems)


def test_checker_accepts_a_matching_translation(tmp_path, monkeypatch):
    tool = _load_tool()
    _write_fixture(tmp_path, stale_hash=False)
    monkeypatch.setattr(tool, "REPO", tmp_path)
    monkeypatch.setattr(tool, "SOURCES_DIR", tmp_path / "docs" / "i18n" / "sources")
    monkeypatch.setattr(tool, "DOCS_I18N_DIR", tmp_path / "docs" / "i18n")
    results = tool.check_all()
    assert results["lt"]["quickstart"].status == "MACHINE_DRAFT"


def test_zero_document_scan_is_a_failure_not_a_pass(tmp_path, monkeypatch, capsys):
    tool = _load_tool()
    # A tree with no docs/i18n/sources at all: the historical bug scanned exactly
    # this shape one directory too high and still returned success.
    empty_root = tmp_path / "elsewhere"
    empty_root.mkdir()
    monkeypatch.setattr(tool, "REPO", empty_root)
    monkeypatch.setattr(tool, "SOURCES_DIR", empty_root / "docs" / "i18n" / "sources")
    monkeypatch.setattr(tool, "DOCS_I18N_DIR", empty_root / "docs" / "i18n")
    rc = tool.main([])
    out = capsys.readouterr().out
    assert rc == 1
    assert "no canonical sources" in out
    assert "check complete" not in out


def test_real_repository_scan_is_not_vacuous(capsys):
    """On THIS repository the tool must reach the source-scan stage honestly: either
    real documents exist (and are checked) or the zero-document guard fires. The one
    outcome the law forbids is "check complete" over zero documents."""
    tool = _load_tool()
    rc = tool.main([])
    out = capsys.readouterr().out
    if "no canonical sources" in out:
        assert rc == 1
    else:
        assert "check complete" in out and rc == 0
