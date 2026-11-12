"""A read-only reader handed a path that is not there looks the file up before it says so.

Before: the model guessed paths (`workspace/<name>.pdf` for a file that sat in ~/Downloads under a
longer name) and `pdf.extract_text`, `workspace.read_file` and `machine.read_file` answered "no
file at ..." about a file the person has. Now the guessed name goes through the `machine.find_file`
walk first (same safe roots, skip list, other-project rule, remote-turn fence): one match is read
and named, several are listed (up to five) with a question, none says where it looked. A link out
of the roots is never read. A path that exists reads exactly as before.

The PDF backend is not under test here (Linux PDF reading is separate work): the PDF cases check
which file the reader was pointed at, not the text it got back.

These names are this file's own, written for it; none is a measuring ask.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.tool_intent_executor import execute_tool_intent

OWNER = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
         "runtime_session_id": "s-guess", "turn_id": "t-guess", "action_policy": "allowed"}
REMOTE = {**OWNER, "_owner_local": False}
GUEST_LIST = "Wedding-Guest-List-Final.txt"
GUEST_BODY = "Aunt Vida sits at table 3"

# Guessed paths for ~/Documents/Wedding-Guest-List-Final.txt, as a model writes them.
TEXT_GUESSES = (
    ("workspace.read_file", "wedding_guest_list.txt"),
    ("workspace.read_file", "workspace/wedding-guest-list.txt"),
    ("workspace.read_file", "docs/Wedding Guest List.txt"),
    ("workspace.read_file", "guest_list.txt"),
    ("workspace.read_file", "notes/guest-list-final.txt"),
    ("workspace.read_file", "WEDDING_GUEST_LIST_FINAL.txt"),
    ("workspace.read_file", "./wedding_guest.txt"),
    ("machine.read_file", "~/Documents/wedding_guest_list.txt"),
    ("machine.read_file", "~/Desktop/Wedding-Guest-List-Final.txt"),
    ("machine.read_file", "~/Downloads/guest list final.txt"),
    ("machine.read_file", "/tmp/wedding_guest_list.txt"),
    ("machine.read_file", "Documents/wedding-guest-list.txt"),
    ("machine.read_file", "wedding guest list.txt"),
    ("machine.read_file", "~/Documents/Wedding/guest_list.txt"),
)
# Guessed paths for ~/Downloads/Car-Insurance-Policy-2026.pdf.
PDF_GUESSES = (
    ("pdf.extract_text", "workspace/car_insurance_policy.pdf"),
    ("pdf.extract_text", "car insurance policy.pdf"),
    ("pdf.extract_text", "~/Documents/car-insurance-policy.pdf"),
    ("pdf.extract_text", "/tmp/insurance_policy.pdf"),
    ("pdf.extract_text", "Downloads/Car_Insurance.pdf"),
    ("pdf.extract_text", "car_insurance_policy"),
    ("pdf.ocr", "workspace/car-insurance-policy-2026.pdf"),
    ("pdf.ocr", "~/Desktop/insurance policy.pdf"),
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents"):
        (home / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    monkeypatch.chdir(workspace)
    return home


def _run(intent: str, path: str, context: dict = OWNER):
    workspace = Path.cwd()
    return execute_tool_intent(
        {"intent": intent, "arguments": {"path": path}},
        task_id="t-guess",
        session_id="s-guess",
        source_context={**context, "workspace": str(workspace), "workspace_root": str(workspace)},
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )


def _pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")


@pytest.mark.parametrize(("intent", "guess"), TEXT_GUESSES)
def test_one_match_is_read_and_named(intent: str, guess: str, home: Path) -> None:
    (home / "Documents" / GUEST_LIST).write_text(GUEST_BODY, encoding="utf-8")
    execution = _run(intent, guess)
    assert execution.ok, execution.response_text
    assert GUEST_BODY in execution.response_text
    assert f"I found `~/Documents/{GUEST_LIST}` and read that" in execution.response_text
    assert str(home) not in execution.response_text, "the file is named home-relative, never by full path"


@pytest.mark.parametrize(("intent", "guess"), PDF_GUESSES)
def test_a_guessed_pdf_path_points_the_reader_at_the_real_file(intent: str, guess: str, home: Path) -> None:
    _pdf(home / "Downloads" / "Car-Insurance-Policy-2026.pdf")
    execution = _run(intent, guess)
    assert "I found `~/Downloads/Car-Insurance-Policy-2026.pdf`" in execution.response_text, execution.response_text
    assert (execution.details or {}).get("found_path") == "~/Downloads/Car-Insurance-Policy-2026.pdf"
    assert str(home) not in execution.response_text
    # Whether this box's PDF backend can open it is not this fix's; the reply claims only what happened.
    assert ("and read that" in execution.response_text) == bool(execution.ok)


def test_several_matches_are_listed_and_the_person_is_asked(home: Path) -> None:
    for name in ("invoice-march.pdf", "Invoice_April.pdf", "invoice may 2026.pdf"):
        _pdf(home / "Downloads" / name)
    execution = _run("pdf.extract_text", "workspace/invoice.pdf")
    assert not execution.ok and execution.status == "ambiguous_path"
    text = execution.response_text
    assert "found 3 files like it. Which one should I read?" in text
    for name in ("invoice-march.pdf", "Invoice_April.pdf", "invoice may 2026.pdf"):
        assert f"- ~/Downloads/{name}" in text


def test_more_than_five_matches_show_five_and_say_how_many_more(home: Path) -> None:
    for month in ("jan", "feb", "mar", "apr", "may", "jun", "jul"):
        (home / "Documents" / f"payslip-{month}.txt").write_text(month, encoding="utf-8")
    execution = _run("machine.read_file", "~/Documents/payslip.txt")
    assert execution.status == "ambiguous_path"
    assert execution.response_text.count("\n- ~/Documents/payslip-") == 5
    assert "…and 2 more" in execution.response_text


def test_no_match_says_where_it_looked(home: Path) -> None:
    execution = _run("pdf.extract_text", "workspace/tax_return_2025.pdf")
    assert not execution.ok
    assert "looked for a file like `tax_return_2025.pdf` in ~/Desktop, ~/Downloads, ~/Documents" in execution.response_text
    assert str(home) not in execution.response_text


def test_no_match_keeps_the_readers_own_answer(home: Path) -> None:
    execution = _run("workspace.read_file", "src/missing_module.py")
    assert not execution.ok and execution.status == "not_found"
    assert "looked for a file like `missing_module.py`" in execution.response_text


def test_a_symlink_out_of_the_roots_is_never_read(home: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "bank-statement.txt").write_text("account 0000 balance", encoding="utf-8")
    (home / "Downloads" / "bank-statement.txt").symlink_to(outside / "bank-statement.txt")
    execution = _run("machine.read_file", "~/Documents/bank_statement.txt")
    assert "account 0000" not in execution.response_text
    assert not execution.ok


def test_a_file_of_another_type_is_not_read_as_the_guess(home: Path) -> None:
    (home / "Documents" / "budget-2026.xlsx").write_bytes(b"PK")
    execution = _run("pdf.extract_text", "budget.pdf")
    assert "budget-2026.xlsx" not in execution.response_text
    assert "found none" in execution.response_text


def test_hidden_folders_are_not_searched(home: Path) -> None:
    (home / "Documents" / ".private").mkdir()
    (home / "Documents" / ".private" / "diary-entry.txt").write_text("secret", encoding="utf-8")
    execution = _run("machine.read_file", "~/Documents/diary_entry.txt")
    assert "secret" not in execution.response_text and not execution.ok


def test_a_path_that_exists_reads_exactly_as_before(home: Path) -> None:
    (Path.cwd() / "README.md").write_text("project readme", encoding="utf-8")
    (home / "Documents" / "README-old.md").write_text("other", encoding="utf-8")
    execution = _run("workspace.read_file", "README.md")
    assert execution.ok and "project readme" in execution.response_text
    assert "There is no" not in execution.response_text and "other" not in execution.response_text
    (home / "Desktop" / "todo.txt").write_text("buy bread", encoding="utf-8")
    execution = _run("machine.read_file", "~/Desktop/todo.txt")
    assert execution.ok and "buy bread" in execution.response_text and "There is no" not in execution.response_text


@pytest.mark.parametrize("intent", ("pdf.extract_text", "workspace.read_file", "machine.read_file"))
def test_a_remote_turn_gets_no_lookup_and_no_file(intent: str, home: Path) -> None:
    from core.request_trust import REMOTE_TURN_REFUSAL_TEXT

    (home / "Documents" / GUEST_LIST).write_text(GUEST_BODY, encoding="utf-8")
    execution = _run(intent, "wedding_guest_list.txt", REMOTE)
    assert not execution.ok
    assert execution.response_text == REMOTE_TURN_REFUSAL_TEXT
    assert GUEST_LIST not in str(execution.details)


def test_the_lookup_itself_is_fenced_for_a_remote_turn(home: Path) -> None:
    # The reader door refuses a remote turn first; the lookup carries its own fence as well, so a
    # caller reaching it some other way still gets no walk of the owner's folders.
    from core.runtime_execution_tools import _guessed_path_lookup

    (home / "Documents" / GUEST_LIST).write_text(GUEST_BODY, encoding="utf-8")
    result = _guessed_path_lookup("machine.read_file", {"path": "guest_list.txt"},
                                  workspace_root=Path.cwd(), source_context=dict(REMOTE))
    assert result is not None and not result.ok and GUEST_LIST not in result.response_text
