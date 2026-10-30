"""Everyday file asks from the 2026-10-27 audit (fake home of an ordinary person, PC and phone).

"check my Invoices folder" walked / to depth 6; "the photos from my lisbon trip" and "a pic of the cat on
the sofa" could not see ~/Pictures; "cant find my boarding pass" waited for a model; "did i save any photos
to downloads recently? like last few days" was not a listing; "clear out my temp files" got the memory
report; a 6 MB zip read "0.0 GB".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.execution.constants import machine_described_file_intent, machine_kind_listing_intent
from core.request_trust import OWNER_LOCAL_KEY
from core.tool_demand_signals import owner_file_demand

OWNER = {OWNER_LOCAL_KEY: True}


@pytest.fixture
def home(tmp_path, monkeypatch):
    drive = tmp_path / "drive"
    root = drive / "Users" / "sam"
    for name in ("Desktop", "Downloads", "Documents", "Pictures/Holiday Lisbon 2026", "Library/Mail/Invoices"):
        (root / name).mkdir(parents=True)
    (root / "Downloads" / "invoice_4471.pdf").write_bytes(b"%PDF-1.4 invoice")
    (root / "Downloads" / "boarding-pass-VNO-LIS.pdf").write_bytes(b"%PDF-1.4 pass")
    (root / "Pictures" / "cat_on_sofa.png").write_bytes(b"\x89PNG cat")
    (root / "Pictures" / "cat_in_garden.png").write_bytes(b"\x89PNG garden")
    for index in (1, 2):
        (root / "Pictures" / "Holiday Lisbon 2026" / f"IMG_{index}.jpg").write_bytes(b"\xff\xd8 lisbon")
    (drive / "opt" / "Invoices").mkdir(parents=True)  # outside the home, on the same drive
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    import core.machine_diagnostics as md

    monkeypatch.setattr(md, "disk_usage", lambda *a, **k: [{"mount": str(drive), "total_gb": 1.0, "free_gb": 0.5}])
    return root


def test_a_folder_search_stays_in_the_home_on_the_home_drive_and_names_matching_files(home):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool("machine.find_folder", {"name": "invoices"})
    assert result.ok and result.details["matches"] == []  # not /opt/Invoices, not ~/Library/Mail/Invoices
    assert "in your home folder" in result.response_text
    assert result.details["file_matches"] == ["~/Downloads/invoice_4471.pdf"]
    assert str(home.parent.parent / "opt") not in result.response_text


def test_pictures_are_readable_but_not_a_write_root(home):
    from core.runtime_execution_tools import _resolve_safe_machine_directory, execute_runtime_tool

    assert _resolve_safe_machine_directory("~/Pictures") == (home / "Pictures").resolve()
    with pytest.raises(ValueError):
        _resolve_safe_machine_directory("~/Pictures", write=True)
    found = execute_runtime_tool("machine.find_file", {"name": "cat_on_sofa"})
    assert found.ok and any("cat_on_sofa.png" in str(path) for path in found.details["matches"])


@pytest.mark.parametrize(("text", "kind", "words"), [
    ("cant find my boarding pass for the flight, can u look for it?", "", ("boarding", "pass")),
    ("im sure i saved a pic of the cat on the sofa at some point, find it?", "image", ("cat", "sofa")),
    ("show me the photos from my lisbon trip", "image", ("lisbon",)),
])
def test_a_described_file_is_a_lookup(text, kind, words):
    assert owner_file_demand(text)
    request = machine_described_file_intent(text)
    assert request is not None and request.kind == kind and request.words == words


@pytest.mark.parametrize("text", [
    "where is my order", "find my keys", "give me a picture of a cat on the moon",
    "send the photos from my lisbon trip to bob", "how do I find my boarding pass in the airline app",
])
def test_not_a_described_file_lookup(text):
    assert machine_described_file_intent(text) is None


def test_described_files_are_found_by_every_word(home, monkeypatch):
    import core.authorized_tool_execution as ate
    from core.agent_runtime.fast_paths_machine import find_described_files
    from core.runtime_execution_tools import execute_runtime_tool

    monkeypatch.setattr(ate, "execute_authorized_runtime_tool",
                        lambda intent, args, source_context=None, **_k: execute_runtime_tool(intent, args, source_context=source_context))

    def answer(text):
        found = find_described_files(machine_described_file_intent(text), source_context=OWNER)
        return found.response_text if found is not None else None

    cat = answer("im sure i saved a pic of the cat on the sofa at some point, find it?")
    assert "~/Pictures/cat_on_sofa.png" in cat and "garden" not in cat
    assert "~/Downloads/boarding-pass-VNO-LIS.pdf" in answer("cant find my boarding pass for the flight, can u look for it?")
    lisbon = answer("show me the photos from my lisbon trip")
    assert "~/Pictures/Holiday Lisbon 2026/IMG_1.jpg" in lisbon and lisbon.count("IMG_1.jpg") == 1
    assert answer("cant find my passport scan, can you look for it?") is None  # nothing matched: the model looks


def test_photos_saved_to_downloads_in_the_last_few_days_is_a_listing():
    listing = machine_kind_listing_intent("did i save any photos to downloads recently? like last few days")
    assert listing is not None and (listing.kind, listing.place, listing.max_age_days) == ("image", "Downloads", 3.0)
    assert machine_kind_listing_intent("did i save photos to downloads and send them to bob") is None


def test_a_photo_listing_with_no_folder_also_looks_in_pictures():
    from core.agent_runtime.fast_paths_machine import _default_places

    assert _default_places("image") == ["Desktop", "Downloads", "Documents", "Pictures"]
    assert _default_places("pdf") == ["Desktop", "Downloads", "Documents"]


def test_clear_out_my_temp_files_is_the_cleanup_not_the_memory_report():
    from core.agent_runtime.fast_paths_governor import maybe_handle_resource_hog_question
    from core.operator.parser import parse_operator_action_intent

    text = "my laptop is slow, can you clear out my temp files?"
    assert parse_operator_action_intent(text).kind == "cleanup_temp_files"
    assert maybe_handle_resource_hog_question(text, owner_local=True) is None
    assert maybe_handle_resource_hog_question("why is my mac so slow", owner_local=False) is not None


def test_small_sizes_read_in_mb_or_kb():
    from core.runtime_execution_tools import _largest_size_text

    assert _largest_size_text({"size_gb": 0.01, "size_bytes": 6_000_004}) == "5.7 MB"
    assert _largest_size_text({"size_gb": 0.0, "size_bytes": 2070}) == "2 KB"
    assert _largest_size_text({"size_gb": 9.2, "size_bytes": 9_878_424_780}) == "9.2 GB"
