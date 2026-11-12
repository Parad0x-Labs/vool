"""A size-of-a-file/folder ask is answered with the size of THAT thing only.

Measured in a benchmark run (2026-10-30): a "how big is <file>" ask fell to the model lane and
the reply bundled the machine's drive fill ("GB free of", "% used") with the file's size. Those
drive numbers were never asked for and rode the turn to the provider. These unit checks drive
the deterministic size lane directly (no daemon, no model): a fake home under tmp_path holds
files of exact byte sizes, every size ask must answer with the seeded thing's size and never a
drive number, and a disk ask must stay routed to the disk lane.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.execution.constants import machine_size_query_intent
from core.file_size_answer import size_answer_for_ask

ZIP_BYTES = 12 * 1024 * 1024
PDF_BYTES = 700 * 1024
TXT_BYTES = 2048
VIDEO_BYTES = 5 * 1024 * 1024
FOLDER_BYTES = 1024 * 1024

#: What a drive-fill leak looks like in reply text. A size answer must match NONE of these.
REPLY_DISK_FILL_PATTERNS = (
    re.compile(r"\d+(?:\.\d+)?\s*(?:GB|MB|TB|GiB|MiB|TiB)\s+free", re.IGNORECASE),
    re.compile(r"free\s+of", re.IGNORECASE),
    re.compile(r"%\s*(?:full|used)", re.IGNORECASE),
    re.compile(r"drive\s+space", re.IGNORECASE),
    re.compile(r"disk", re.IGNORECASE),
)


def _assert_no_drive_numbers(text: str) -> None:
    for pattern in REPLY_DISK_FILL_PATTERNS:
        assert not pattern.search(text), pattern.pattern


def _human_candidates(nbytes: int) -> tuple[str, ...]:
    """Every plausible rendering of an exact byte count (1000- and 1024-based, spaced and not).

    The lane's own unit convention is not pinned here on purpose -- what is pinned is that the
    answer carries the size of the seeded thing in human units, in whichever convention the
    renderer uses.
    """
    out: set[str] = set()
    for factor, units in (
        (1000, ("KB", "MB", "GB", "TB")),
        (1024, ("KB", "MB", "GB", "TB")),
        (1024, ("KiB", "MiB", "GiB", "TiB")),
    ):
        value = float(nbytes)
        unit = "B"
        for step in units:
            if value < factor:
                break
            value /= factor
            unit = step
        for rendered in (f"{value:.0f}", f"{value:.1f}", f"{value:.2f}"):
            out.add(f"{rendered} {unit}")
            out.add(f"{rendered}{unit}")
    return tuple(sorted(out))


@pytest.fixture
def seeded_home(tmp_path, monkeypatch):
    """The established fake-machine-home pattern (see tests/test_find_named_file.py): real files
    of exact byte sizes under Desktop/Downloads/Documents, with HOME and Path.home pointed there
    so the safe roots resolve inside the fixture."""
    root = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents"):
        (root / name).mkdir(parents=True)
    (root / "Downloads" / "holiday-pictures.zip").write_bytes(b"\0" * ZIP_BYTES)
    (root / "Downloads" / "quarterly-report.pdf").write_bytes(b"\0" * PDF_BYTES)
    (root / "Desktop" / "meeting-notes.txt").write_bytes(b"\0" * TXT_BYTES)
    (root / "Documents" / "wedding-video.mp4").write_bytes(b"\0" * VIDEO_BYTES)
    taxes = root / "Documents" / "Taxes 2025"
    taxes.mkdir()
    (taxes / "receipts.pdf").write_bytes(b"\0" * (512 * 1024))
    (taxes / "scan.png").write_bytes(b"\0" * (256 * 1024))
    (taxes / "notes.txt").write_bytes(b"\0" * (256 * 1024))
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    return root


# (ask, expected bytes) -- every wording is its own; the family asked for is covered: a plain
# file, a zip, a folder, "how large", "what size", "how many MB", and a name given without its
# extension. None of these is a disk ask (verified against the disk detector on this checkout).
SIZE_ASKS = [
    ("how big is the holiday-pictures.zip in my downloads?", ZIP_BYTES),
    ("how large is holiday-pictures.zip?", ZIP_BYTES),
    ("how many megabytes is holiday-pictures.zip?", ZIP_BYTES),
    ("whats the size of the quarterly-report.pdf file?", PDF_BYTES),
    ("what size is the quarterly-report.pdf in downloads?", PDF_BYTES),
    ("whats the file size of quarterly-report.pdf?", PDF_BYTES),
    ("tell me how big the meeting-notes.txt on my desktop is", TXT_BYTES),
    ("how big is meeting-notes.txt?", TXT_BYTES),
    ("how many KB is meeting-notes.txt?", TXT_BYTES),
    ("give me the size of the wedding-video.mp4 in documents", VIDEO_BYTES),
    ("how large is the wedding video", VIDEO_BYTES),
    ("how big is the Taxes 2025 folder in my documents?", FOLDER_BYTES),
    ("whats the total size of the Taxes 2025 folder?", FOLDER_BYTES),
    ("how many megabytes is the Taxes 2025 folder?", FOLDER_BYTES),
]


@pytest.mark.parametrize(("ask", "nbytes"), SIZE_ASKS)
def test_a_size_ask_answers_with_the_size_of_that_thing_only(seeded_home, ask, nbytes):
    assert machine_size_query_intent(ask), ask
    result = size_answer_for_ask(ask)
    assert isinstance(result, dict), ask
    assert {"handled", "ok", "response_text", "details"} <= set(result), ask
    assert result.get("handled") is True, ask
    assert result.get("ok") is True, ask
    text = str(result.get("response_text") or "")
    assert text, ask
    assert any(candidate in text for candidate in _human_candidates(nbytes)), ask
    _assert_no_drive_numbers(text)
    # What rides a provider turn is built from the same details: no drive rows in there either.
    _assert_no_drive_numbers(str(result.get("details") or {}))


# Every wording here routes to the existing disk lane on this checkout (machine.disk_usage),
# so each must come back None from the size lane and keep its drive numbers.
DISK_ASKS = [
    "am i running low on storage on this machine?",
    "how many gigabytes are free on my drive?",
    "do i have enough room left on my disk for another backup?",
    "whats the storage situation on this mac looking like?",
    "is the ssd nearly full yet?",
    "how much room is left on this computer?",
    "how much space can i still use on the ssd?",
    "am i about to run out of disk space?",
]


@pytest.mark.parametrize("ask", DISK_ASKS)
def test_a_disk_ask_stays_routed_to_the_disk_lane(seeded_home, ask):
    from core.execution.constants import machine_diagnostics_intent

    assert machine_size_query_intent(ask) is None, ask
    assert size_answer_for_ask(ask) is None, ask
    assert machine_diagnostics_intent(ask) == "machine.disk_usage", ask


def test_a_size_ask_for_a_name_that_is_nowhere_fails_closed(seeded_home):
    ask = "how big is the mystery-archive.zip in downloads?"
    assert machine_size_query_intent(ask), ask
    result = size_answer_for_ask(ask)
    assert isinstance(result, dict), ask
    assert result.get("ok") is False, ask
    text = str(result.get("response_text") or "")
    assert text, ask
    # Not found is said in plain words; the machine's drive fill is never the consolation.
    _assert_no_drive_numbers(text)


def test_a_folder_size_ask_reports_the_summed_size(seeded_home):
    taxes = seeded_home / "Documents" / "Taxes 2025"
    expected = sum(path.stat().st_size for path in taxes.rglob("*") if path.is_file())
    assert expected == FOLDER_BYTES
    result = size_answer_for_ask("how big is the Taxes 2025 folder in documents?")
    assert isinstance(result, dict)
    assert result.get("ok") is True
    text = str(result.get("response_text") or "")
    assert text
    assert any(candidate in text for candidate in _human_candidates(expected))
    _assert_no_drive_numbers(text)
