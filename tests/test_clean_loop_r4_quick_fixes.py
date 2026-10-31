"""Clean-loop quick fixes from the round-4 review (2026-10-31): more steps, joined follow-up questions,
bank and card words in a read-only question, and file names from every readable home folder.

None of these tests uses a round-4 task text: each one checks the general rule with its own words.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.clean_loop import route
from core.clean_loop.loop import DEFAULT_MAX_STEPS, run_loop
from core.clean_loop.registry import Registry, ToolContext
from core.clean_loop.tools import install_builtin_tools

ON = {route.FLAG_ENV: "1"}
OWNER = {"surface": "cli"}


def _local_brain():
    return object()


def _decide(ask: str, **kwargs):
    return route.decide(ask, source_context=OWNER, resolve_brain=_local_brain, env=ON, **kwargs)


# --- steps ----------------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for name in ("Desktop", "Downloads", "Documents"):
        (root / name).mkdir(parents=True)
    (root / "Desktop" / "todo.txt").write_text("call the vet\n", encoding="utf-8")
    (root / "Documents" / "lease.txt").write_text("Rent: EUR 900 a month\n", encoding="utf-8")
    (root / "Downloads" / "receipt.txt").write_text("Bike service EUR 45\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(root))
    return root


def test_a_seven_look_ask_still_gets_its_answer(home):
    registry = Registry()
    install_builtin_tools(registry)
    replies = [
        '{"tool": "list_dir", "path": "Desktop"}',
        '{"tool": "list_dir", "path": "Documents"}',
        '{"tool": "list_dir", "path": "Downloads"}',
        '{"tool": "read_file", "path": "Desktop/todo.txt"}',
        '{"tool": "read_file", "path": "Documents/lease.txt"}',
        '{"tool": "read_file", "path": "Downloads/receipt.txt"}',
        '{"tool": "disk_usage"}',
        '{"answer": "Rent is EUR 900 a month."}',
    ]
    calls: list[int] = []

    def brain(messages):
        calls.append(1)
        return replies[len(calls) - 1]

    ctx = ToolContext(home=home, source_context=dict(OWNER))
    result = run_loop("what do i pay in rent", brain=brain, registry=registry, ctx=ctx)
    assert DEFAULT_MAX_STEPS == 10
    assert result.stopped == "answered" and result.answer == "Rent is EUR 900 a month."
    assert len(result.tools_used) == 7


# --- joined follow-ups ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ask",
    [
        "and how much am i paying for the gym a year",
        "so when do i pay the dentist",
        "ok, what did the bike service cost",
        "but when is the rent due",
        "also how much was the vet",
    ],
)
def test_a_question_after_a_joining_word_is_still_a_question(ask):
    assert route.is_question(ask)
    assert not route.is_red(ask)


@pytest.mark.parametrize(
    ("ask", "reason"),
    [
        ("and pay the dentist", "red_turn"),
        ("so send it to my sister", "red_turn"),
        ("and delete the old ones", "red_turn"),
        ("ok what if i delete the old ones", "red_turn"),
        ("and move it to my desktop", "write_turn"),
    ],
)
def test_a_joining_word_never_turns_an_order_into_a_question(ask, reason):
    decision = _decide(ask)
    assert (decision.admit, decision.reason) == (False, reason)


def test_a_joined_follow_up_to_a_clean_loop_turn_comes_in():
    route.note_clean_turn("sess-joined")
    decision = _decide("and how much am i paying for it a year", session_id="sess-joined")
    assert decision.admit, decision.reason


# --- bank and card words --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ask",
    [
        "which bank account does my rent come out of",
        "when is my credit card bill due",
        "what was the last bank account statement i saved",
    ],
)
def test_a_question_about_bank_or_card_papers_comes_in(ask):
    decision = _decide(ask)
    assert decision.admit, decision.reason


@pytest.mark.parametrize(
    "ask",
    [
        "can i pay the plumber from my bank account?",
        "what is my bank account number, send it to my brother",
        "how do i transfer money to my credit card",
        "move my bank account statements to the desktop",
        "give me my credit card number",
        "what is in my wallet",
        "how much bitcoin do i have",
        "which credit card did i delete",
    ],
)
def test_money_orders_and_money_assets_stay_red(ask):
    assert route.is_red(ask)
    decision = _decide(ask)
    assert (decision.admit, decision.reason) == (False, "red_turn")


# --- file names from every readable folder --------------------------------------------------------


def test_file_words_cover_the_other_home_folders_and_media_but_never_app_data(tmp_path):
    from core.clean_loop import tools

    home = (tmp_path / "h").resolve()
    (home / "Projects" / "allotment plans").mkdir(parents=True)
    (home / "Pictures").mkdir()
    (home / "Pictures" / "hedgehog visit.jpg").write_bytes(b"x")
    (home / "Library" / "keychains").mkdir(parents=True)
    (home / "Library" / "keychains" / "passwords backup.db").write_bytes(b"x")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "sensitive keyring").write_text("x")
    (home / "Linked").symlink_to(home / "Library")
    tools._NAME_CACHE.clear()
    words = tools.file_words(home)
    assert {"allotment", "plan", "hedgehog", "visit"} <= words
    assert not {"keychain", "password", "backup", "sensitive", "keyring"} & words


def test_an_ask_naming_a_folder_outside_documents_comes_in(tmp_path):
    from core.clean_loop import tools

    home = (tmp_path / "h2").resolve()
    (home / "Projects" / "allotment plans").mkdir(parents=True)
    tools._NAME_CACHE.clear()
    words = lambda: set(tools.file_words(home))  # noqa: E731
    assert _decide("when does the allotment open", file_words=words).admit
    assert _decide("when does the cinema open", file_words=words).reason == "not_about_own_files"
