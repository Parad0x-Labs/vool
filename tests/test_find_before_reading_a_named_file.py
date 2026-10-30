"""A PDF or picture asked for by a name or guessed path is found before it is read.

Measured 2026-10-30 on qwen3:8b (dev asks nt-16, nt-19): asked "read my boarding pass pdf, what
time is the flight", the local model called pdf.extract_text with a guessed path
(``~/Downloads/boarding_pass.pdf``) for a file that is on disk as ``boarding-pass-VNO-LIS.pdf``;
the read failed on the guess and the turn ended with no answer, although one search in the folders
VOOL may read would have found it. A path that names no file is now searched for there first:
one match is read, several are listed with a question, none is said plainly, and a home folder
VOOL may not read (~/Pictures) is named in one line, never searched or widened.
"""
from __future__ import annotations

import pytest

from core import runtime_paths
from core.runtime_execution_tools import execute_runtime_tool


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool"))
    runtime_paths.configure_runtime_home(tmp_path / "vool")
    user = tmp_path / "user"
    (user / "Downloads").mkdir(parents=True)
    (user / "Documents" / "Work").mkdir(parents=True)
    (user / "Desktop").mkdir()
    (user / "Pictures").mkdir()
    (user / "Downloads" / "boarding-pass-VNO-LIS.pdf").write_bytes(b"%PDF-1.4 stand-in")
    (user / "Documents" / "Work" / "Q3 report draft.txt").write_text("Revenue grew 4% in Q3.\n")
    (user / "Downloads" / "invoice_4471.pdf").write_bytes(b"%PDF-1.4 a")
    (user / "Documents" / "invoice_4471.pdf").write_bytes(b"%PDF-1.4 b")
    (user / "Pictures" / "cat_on_sofa.png").write_bytes(b"\x89PNG\r\n")
    monkeypatch.setenv("HOME", str(user))
    yield user
    runtime_paths.configure_runtime_home(None)


def test_a_guessed_pdf_path_with_one_match_reads_the_file_that_exists(home, monkeypatch) -> None:
    read_paths: list[str] = []

    def _extract(path: str, *, max_pages: int = 0) -> dict:
        read_paths.append(path)
        return {"status": "ok", "path": path, "pages_read": 1, "pages_total": 1, "characters": 12,
                "text": "[page 1]\nDeparts 07:40"}

    monkeypatch.setattr("core.pdf_tools.extract_text", _extract)
    result = execute_runtime_tool("pdf.extract_text", {"path": "~/Downloads/boarding_pass.pdf"})
    assert read_paths == [str(home / "Downloads" / "boarding-pass-VNO-LIS.pdf")]
    assert result.ok and "Departs 07:40" in result.response_text
    assert "boarding-pass-VNO-LIS.pdf" in result.response_text


def test_a_bare_text_file_name_with_one_match_is_read(home) -> None:
    result = execute_runtime_tool("machine.read_file", {"path": "Q3 report draft.txt"})
    assert result.ok, result.response_text
    assert "Revenue grew 4% in Q3." in result.response_text


def test_several_matches_are_listed_and_the_owner_is_asked(home, monkeypatch) -> None:
    monkeypatch.setattr("core.pdf_tools.extract_text", lambda *a, **k: pytest.fail("must not read"))
    result = execute_runtime_tool("pdf.extract_text", {"path": "invoice_4471.pdf"})
    assert not result.ok and result.status == "ambiguous_match"
    assert "Downloads/invoice_4471.pdf" in result.response_text
    assert "Documents/invoice_4471.pdf" in result.response_text
    assert "Which one" in result.response_text


def test_no_match_is_said_plainly_and_nothing_is_read(home, monkeypatch) -> None:
    monkeypatch.setattr("core.pdf_tools.extract_text", lambda *a, **k: pytest.fail("must not read"))
    result = execute_runtime_tool("pdf.extract_text", {"path": "~/Documents/passport_scan.pdf"})
    assert not result.ok and result.status == "not_found"
    assert result.response_text == "I could not find a file called 'passport_scan.pdf' in Desktop, Downloads, Documents."


def test_a_picture_in_a_folder_vool_may_not_read_gets_one_plain_line(home) -> None:
    result = execute_runtime_tool("machine.read_file", {"path": "~/Pictures/cat on the sofa.png"})
    assert not result.ok and result.status == "folder_not_readable"
    assert result.response_text == "VOOL cannot read your Pictures folder yet; it can read Desktop, Downloads, Documents."
    assert "\n" not in result.response_text


def test_an_existing_path_is_left_to_the_reading_tool(home) -> None:
    # The search never widens what a reader may open: an existing file outside the safe folders
    # keeps machine.read_file's own refusal.
    result = execute_runtime_tool("machine.read_file", {"path": str(home / "Pictures" / "cat_on_sofa.png")})
    assert not result.ok and result.status == "not_allowed"
    assert "located_from" not in (result.details or {})


def test_a_missing_path_outside_the_home_keeps_the_readers_refusal(home) -> None:
    result = execute_runtime_tool("machine.read_file", {"path": "/private/tmp/vool-secret.txt"})
    assert not result.ok and result.status == "not_allowed"
    assert "vool-secret.txt" in result.response_text
