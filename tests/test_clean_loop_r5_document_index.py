"""The clean loop's in-memory document index (round-5 fix C1/C3): what it may read, what it keeps,
and how `find` and an absence answer use it.

Real files in a temporary home, read through VOOL's real readers and permission authority; only
the model is scripted where a loop runs.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.clean_loop import route, tools
from core.clean_loop.loop import CONTENT_SEARCH_NUDGE, run_loop
from core.clean_loop.registry import Registry, ToolContext
from core.clean_loop.tools import NOT_FOUND_TEXT, UNSEARCHED_TEXT, install_builtin_tools

OWNER = {"surface": "cli"}
ON = {route.FLAG_ENV: "1"}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for name in ("Desktop", "Documents", "Downloads"):
        (root / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("VOOL_HOME", str(root / "Documents" / "vool-data"))
    tools._TEXT_CACHE.clear()
    tools._TEXT_SCANS.clear()
    yield root
    tools._TEXT_CACHE.clear()
    tools._TEXT_SCANS.clear()


def _ctx(home: Path, **context) -> ToolContext:
    return ToolContext(home=home, source_context={**OWNER, **context})


def _registry() -> Registry:
    registry = Registry()
    install_builtin_tools(registry)
    return registry


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- what the index may read ------------------------------------------------------------------


def test_the_index_never_reads_links_hidden_entries_app_data_or_vools_own_data(home, tmp_path):
    outside = _write(tmp_path / "elsewhere" / "outside.txt", "Brennick keeps the boat at Quarrow.\n")
    _write(home / "Documents" / "own.txt", "The ferry we take is the Saltorne.\n")
    (home / "Documents" / "linked.txt").symlink_to(outside)
    (home / "Documents" / "linked_folder").symlink_to(outside.parent, target_is_directory=True)
    _write(home / "Documents" / ".hidden_notes.txt", "Pellwick is in the hidden file.\n")
    _write(home / "Documents" / ".hidden" / "inside.txt", "Dravenhol is in a hidden folder.\n")
    _write(home / "Documents" / "Library" / "cache.txt", "Ostermoor is in app data.\n")
    _write(home / "Documents" / "AppData" / "cache.txt", "Ulvardine is in app data.\n")
    _write(home / "Documents" / "vool-data" / "memory.txt", "Tamsevar is in VOOL's own data.\n")
    ctx = _ctx(home)

    def asks(name: str) -> bool:
        return tools.document_matches(f"Where is {name} now?", ctx)

    assert asks("Saltorne")  # the owner's own document is read
    for name in ("Brennick", "Quarrow", "Pellwick", "Dravenhol", "Ostermoor", "Ulvardine", "Tamsevar"):
        assert not asks(name), name
    indexed = {Path(key[0]).name for key in tools._TEXT_CACHE}
    assert indexed == {"own.txt"}


def test_building_the_index_writes_no_owner_text_to_disk(home, tmp_path):
    _write(home / "Documents" / "letter.txt", "Dear Corvessa, the key is under the third pot.\n")
    before = {p for p in tmp_path.rglob("*")}
    assert tools.document_matches("When is Corvessa back?", _ctx(home))
    after = {p for p in tmp_path.rglob("*")}
    written = [p for p in after - before if p.is_file()]
    holding = [p for p in after if p.is_file() and not p.is_symlink() and b"Corvessa" in p.read_bytes()]
    # Whatever the runtime writes for itself, nothing new carries the owner's text.
    assert all(b"Corvessa" not in p.read_bytes() for p in written)
    assert holding == [home / "Documents" / "letter.txt"]


def test_the_index_reads_nothing_when_the_owner_denies_file_reads(home):
    _write(home / "Documents" / "note.txt", "Velmarra collects the parcel on Friday.\n")
    ctx = _ctx(home, project_permissions={"read_files": False})
    assert not tools.document_matches("When does Velmarra collect it?", ctx)
    assert not tools._TEXT_CACHE
    # Not marked unopenable: the mode may change, and then it is read.
    assert not tools._TEXT_SCANS[str(home)]["unreadable"]
    assert tools.document_matches("When does Velmarra collect it?", _ctx(home))


def test_a_changed_document_is_read_again(home):
    note = _write(home / "Documents" / "plan.txt", "On the trip Hollisan brings the tent.\n")
    ctx = _ctx(home)
    assert tools.document_matches("What does Hollisan bring?", ctx)
    note.write_text("On the trip Merrowen brings the tent instead.\n", encoding="utf-8")
    os.utime(note, ns=(note.stat().st_atime_ns, note.stat().st_mtime_ns + 5_000_000_000))
    assert tools.document_matches("What does Merrowen bring?", ctx)
    assert not tools.document_matches("What does Hollisan bring?", ctx)


def test_a_turn_that_is_not_the_owners_own_never_reaches_the_index(home):
    _write(home / "Documents" / "note.txt", "Velmarra collects the parcel on Friday.\n")
    calls = []

    def matcher(ask):
        calls.append(ask)
        return True

    for context in ({"surface": "channel", "_owner_local": False}, {**OWNER, "planned_subturn": True}):
        decision = route.decide(
            "When does Velmarra collect it?", source_context=context, resolve_brain=lambda: object(),
            env=ON, document_match=matcher,
        )
        assert not decision.admit
    assert calls == []
    red = route.decide(
        "Send Velmarra the parcel note", source_context=OWNER, resolve_brain=lambda: object(),
        env=ON, document_match=matcher,
    )
    assert red.reason == "red_turn" and calls == []


def test_a_full_index_stops_growing_and_says_what_it_left_out(home, monkeypatch):
    for number in range(6):
        _write(home / "Documents" / f"note_{number}.txt", f"Item {number}: Fernmoor visit list.\n" * 20)
    monkeypatch.setattr(tools, "_TEXT_INDEX_BYTES", 1)
    ctx = _ctx(home)
    outcome = _registry().tool("find").run({"query": "glimmerstone"}, ctx)
    assert outcome.status == "content_search_incomplete"
    assert not tools._TEXT_CACHE
    left = tools._TEXT_SCANS[str(home)]["left_out"]
    assert len(left) == 6
    # A second search does not re-read and evict in a loop: the left-out set stays as it is.
    _registry().tool("find").run({"query": "glimmerstone"}, ctx)
    assert tools._TEXT_SCANS[str(home)]["left_out"] == left


# --- what find sees -----------------------------------------------------------------------------


def test_find_searches_inside_every_document_not_only_the_newest(home):
    old = _write(home / "Documents" / "archive" / "old_note.txt", "The Thornwick deposit was 410.\n")
    os.utime(old, (1_000_000_000, 1_000_000_000))
    for number in range(120):
        _write(home / "Documents" / "batch" / f"note_{number:03}.txt", f"Ordinary note {number}.\n")
    outcome = _registry().tool("find").run({"query": "thornwick deposit"}, _ctx(home))
    assert "old_note.txt" in outcome.text, outcome.text
    assert outcome.status == "content_search_complete"


def test_find_searches_inside_documents_even_when_names_match(home):
    for number in range(4):
        _write(home / "Downloads" / f"harbour_{number}.txt", "Nothing here.\n")
    _write(home / "Documents" / "SCN_0091.txt", "Mooring fee for the harbour: 212.40, due 3 May.\n")
    outcome = _registry().tool("find").run({"query": "harbour mooring"}, _ctx(home))
    assert "SCN_0091.txt" in outcome.text, outcome.text


def test_a_fact_past_the_first_3000_characters_is_found_and_shown(home):
    rows = [f"row {number}: nothing to see here, just filler text for the page" for number in range(400)]
    rows.append("row 400: the Wexmoor locker code card is in the blue tin")
    _write(home / "Documents" / "long_notes.txt", "\n".join(rows) + "\n")
    ctx = _ctx(home)
    outcome = _registry().tool("find").run({"query": "wexmoor"}, ctx)
    assert "long_notes.txt" in outcome.text and "blue tin" in outcome.text, outcome.text
    # The read shown to the model (and receipted) is at the matching line, not the first 3000 chars.
    reads = [r for r in ctx.receipts if r["tool_name"] == "machine.read_file"]
    assert reads and "blue tin" in reads[-1]["response_preview"]


# --- absence answers (C3) -----------------------------------------------------------------------


def test_a_meaningless_file_name_is_found_by_its_content_in_a_loop(home):
    _write(home / "Downloads" / "SCN_5820.txt", "Kestrel Lane surgery: Anwen's check-up is on 12 June at 09:20.\n")
    replies = iter([
        '{"tool": "find", "query": "anwen check-up"}',
        '{"answer": "Anwen\'s check-up is on 12 June at 09:20 (SCN_5820.txt in Downloads)."}',
    ])
    result = run_loop("when is anwen's check-up?", brain=lambda _m: next(replies), registry=_registry(), ctx=_ctx(home))
    assert "12 June" in result.answer and "SCN_5820.txt" in result.answer


def test_an_absence_answer_without_a_content_search_takes_one_more_step(home):
    _write(home / "Documents" / "unrelated.txt", "Garden plan.\n")
    seen = []
    replies = iter([
        '{"answer": "That isn\'t there, you don\'t have one."}',
        '{"tool": "find", "query": "dovecote permit"}',
        '{"answer": "It isn\'t there. Do you remember roughly when you got it?"}',
    ])

    def brain(messages):
        seen.append(list(messages))
        return next(replies)

    result = run_loop("where is the dovecote permit?", brain=brain, registry=_registry(), ctx=_ctx(home))
    assert any(CONTENT_SEARCH_NUDGE in m["content"] for m in seen[1])
    assert result.answer == NOT_FOUND_TEXT + " Do you remember roughly when you got it?"
    assert "isn't there" not in result.answer


def test_an_absence_answer_that_never_searched_is_not_stated(home):
    replies = iter([
        '{"answer": "You don\'t have one."}',
        '{"answer": "You don\'t have one."}',
    ])
    result = run_loop("where is the dovecote permit?", brain=lambda _m: next(replies), registry=_registry(), ctx=_ctx(home))
    assert result.answer == UNSEARCHED_TEXT


def test_an_unopenable_document_does_not_block_the_search_and_is_reported(home):
    _write(home / "Documents" / "notes.txt", "Garden plan.\n")
    (home / "Documents" / "scan.pdf").write_bytes(b"not really a pdf")
    outcome = _registry().tool("find").run({"query": "dovecote"}, _ctx(home))
    assert outcome.status == "content_search_complete"
    assert outcome.text.startswith("I didn't find it in your files.")
    assert "1 document could not be opened on this computer" in outcome.text


def test_one_changing_file_does_not_make_the_index_retry_everything(home, monkeypatch):
    for number in range(4):
        _write(home / "Documents" / f"note_{number}.txt", f"Item {number}: Fernmoor visit list.\n" * 20)
    monkeypatch.setattr(tools, "_TEXT_INDEX_BYTES", 1)
    ctx = _ctx(home)
    tools.document_matches("When is the Fernmoor visit?", ctx)
    reads = []
    real = tools._index_read
    monkeypatch.setattr(tools, "_index_read", lambda *args: reads.append(args[1].name) or real(*args))
    log = _write(home / "Documents" / "activity.log", "started\n")
    tools.document_matches("When is the Fernmoor visit?", ctx)
    log.write_text("started\nstill running\n", encoding="utf-8")
    tools.document_matches("When is the Fernmoor visit?", ctx)
    assert reads == ["activity.log", "activity.log"]
