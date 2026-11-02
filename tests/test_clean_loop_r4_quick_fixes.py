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


# --- money words and chained orders (independent money review 2026-10-31) -----------------------


@pytest.mark.parametrize(
    "ask",
    [
        "which bank account does my rent come out of",
        "what is my credit card number and cvv",
        "is it ok to close the bank account",
        "can i pay the plumber from my bank account?",
        "and when is my credit card bill due",
        "what is in my wallet",
        "how much bitcoin do i have",
    ],
)
def test_money_words_stay_red_even_in_a_question(ask):
    assert route.is_red(ask)
    decision = _decide(ask)
    assert (decision.admit, decision.reason) == (False, "red_turn")


@pytest.mark.parametrize(
    ("ask", "reason"),
    [
        ("so when is the rent due, then wire it", "red_turn"),
        ("when is the gym fee due? pay it", "red_turn"),
        ("what is the invoice total and then pay it", "red_turn"),
        ("ok what if you move the invoices to my desktop", "write_turn"),
        ("what is in the lease. now move it to my desktop", "write_turn"),
        ("what is my balance and pay it", "red_turn"),
        ("when is the rent due so wire it to the landlord", "red_turn"),
        ("how much do i owe anna? reimburse her", "red_turn"),
        ("is it ok to transfer my savings", "red_turn"),
        ("are you able to settle the plumber's bill", "red_turn"),
        ("what about paying the plumber", "red_turn"),
        ("is it ok to move my invoices to the desktop", "write_turn"),
        ("what's my sort code", "red_turn"),
        ("what is my card pin", "red_turn"),
    ],
)
def test_an_order_after_a_question_is_still_an_order(ask, reason):
    decision = _decide(ask)
    assert (decision.admit, decision.reason) == (False, reason)


def test_several_questions_in_one_turn_are_still_a_question():
    assert route.is_question("when is the rent due? and how much is it?")
    assert not route.is_red("when do i pay the dentist? how much is it?")


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


# --- folder total ---------------------------------------------------------------------------------


def test_largest_ends_with_the_folders_measured_total(home):
    registry = Registry()
    install_builtin_tools(registry)
    (home / "Downloads" / "albums").mkdir()
    (home / "Downloads" / "albums" / "live set.mp3").write_bytes(b"x" * 3_000_000)
    (home / "Downloads" / "notes.txt").write_bytes(b"x" * 1_000_000)
    (home / "Downloads" / "loop").symlink_to(home / "Documents")
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = registry.tool("largest").run({"path": "Downloads"}, ctx)
    assert outcome.ok, outcome.text
    total = 3_000_000 + 1_000_000 + len("Bike service EUR 45\n")
    assert f"Total of Downloads: {total / 1024 / 1024:.1f} MB in 3 files (measured, all subfolders)" in outcome.text
    assert "Total of Downloads" in ctx.receipts[-1]["response_preview"]


# --- searching inside documents -------------------------------------------------------------------


def _clear_text_cache():
    from core.clean_loop import tools

    tools._TEXT_CACHE.clear()


def test_find_looks_inside_older_documents_too(home):
    import os

    _clear_text_cache()
    registry = Registry()
    install_builtin_tools(registry)
    old = home / "Documents" / "journey.txt"
    old.write_text("Ferry to Mull, departs 09:40, berth 12\n", encoding="utf-8")
    os.utime(old, (1_600_000_000, 1_600_000_000))
    for n in range(40):
        (home / "Documents" / f"meeting {n}.txt").write_text(f"agenda item {n}\n", encoding="utf-8")
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = registry.tool("find").run({"query": "ferry"}, ctx)
    assert "journey.txt" in outcome.text and "departs 09:40" in outcome.text, outcome.text


def test_a_single_weak_name_hit_also_brings_the_documents_that_mention_it(home):
    _clear_text_cache()
    registry = Registry()
    install_builtin_tools(registry)
    (home / "Desktop" / "ferry photo ideas.txt").write_text("sunset shots\n", encoding="utf-8")
    (home / "Documents" / "trip plan.txt").write_text("Ferry booked: Oban to Mull, 09:40\n", encoding="utf-8")
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = registry.tool("find").run({"query": "ferry"}, ctx)
    assert "ferry photo ideas.txt" in outcome.text
    assert "also mention it inside" in outcome.text and "trip plan.txt" in outcome.text, outcome.text


def test_a_second_find_reads_only_the_documents_that_can_match(home):
    _clear_text_cache()
    registry = Registry()
    install_builtin_tools(registry)
    (home / "Documents" / "trip plan.txt").write_text("Ferry booked: Oban to Mull\n", encoding="utf-8")
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    registry.tool("find").run({"query": "ferry"}, ctx)
    second = ToolContext(home=home, source_context=dict(OWNER))
    outcome = registry.tool("find").run({"query": "oban"}, second)
    assert "trip plan.txt" in outcome.text
    reads = [r for r in second.receipts if r["tool_name"] == "machine.read_file"]
    assert len(reads) == 1, [r.get("arguments") for r in reads]


@pytest.mark.parametrize(
    "ask",
    [
        "how much was the deposit on the flat",
        "how much does the gym charge",
        "when is the refund due",
        "which block is my flat in",
        "where is the lock screen screenshot",
        "how much was the bank transfer to anna",
        "when do i have to pay the plumber by",
    ],
)
def test_money_nouns_in_a_question_are_not_orders(ask):
    assert not route.is_red(ask)


@pytest.mark.parametrize("ask", ["is it ok to refund anna", "when is it due? refund it", "can you charge my card"])
def test_the_same_words_as_orders_stay_red(ask):
    assert route.is_red(ask)
