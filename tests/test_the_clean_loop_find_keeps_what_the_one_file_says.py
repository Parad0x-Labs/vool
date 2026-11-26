"""The clean loop's find keeps what the one file it found says, read by the reader's own rules.

iMac gate38: the per-word finds read the one gym card file, the merge kept only its "- name" line, and the model
gave today's date as the card's expiry; the file says June. Names and contents are fresh.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.clean_loop.registry import Registry, ToolContext
from core.clean_loop.tools import install_builtin_tools

OWNER = {"surface": "cli"}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for folder in ("Desktop", "Downloads", "Documents"):
        (root / folder).mkdir(parents=True)
    (root / "Desktop" / "library-card-renewal.txt").write_text("Card 5531, renew by 3 April\n", encoding="utf-8")
    (root / "Documents" / "bike-shed-key.txt").write_text("Spare key with the neighbour\n", encoding="utf-8")
    (root / "Documents" / "bike-lights-receipt.txt").write_text("Lights 24 EUR\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(root))
    return root


def _find(home: Path, query: str):
    registry = Registry()
    install_builtin_tools(registry)
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    ctx.ask = query
    return registry.tool("find").run({"query": query}, ctx)


def test_the_one_file_found_comes_with_what_it_says(home):
    outcome = _find(home, "library card renewal")
    assert "library-card-renewal.txt" in outcome.text and "renew by 3 April" in outcome.text, outcome.text


def test_several_files_found_are_listed_without_their_words(home):
    outcome = _find(home, "bike")
    assert "bike-shed-key.txt" in outcome.text and "bike-lights-receipt.txt" in outcome.text, outcome.text
    assert "neighbour" not in outcome.text and "24 EUR" not in outcome.text
