"""Watching one folder while the person does a task once, and the routine proposed from what it saw."""
from __future__ import annotations

import os
import time

import pytest

from core.routines import watch
from core.routines.infer import decompose, file_values, propose
from core.routines.model import RoutineError
from tests.routines.conftest import put


def test_a_snapshot_sees_only_visible_files_a_few_folders_deep(person, tmp_path):
    folder = person / "Downloads"
    put(folder, "a.pdf", b"a")
    put(folder, ".hidden.pdf", b"h")
    put(folder, ".cache/inside.pdf", b"c")
    put(folder, "one/two/three/deep.pdf", b"d")
    put(folder, "one/two/three/four/too-deep.pdf", b"x")
    os.symlink(tmp_path, folder / "outside-link")
    (tmp_path / "secret.pdf").write_bytes(b"s")
    os.symlink(tmp_path / "secret.pdf", folder / "file-link.pdf")
    files = watch.snapshot(folder)["files"]
    assert sorted(files) == ["a.pdf", "one/two/three/deep.pdf"]
    assert files["a.pdf"]["sha256"]


def test_changes_are_read_as_moves_rows_edits_removals_and_new_files(person):
    folder = person / "Downloads"
    put(folder, "scan_001.pdf", b"first scan")
    put(folder, "scan_002.pdf", b"second scan")
    put(folder, "notes.txt", b"v1")
    put(folder, "old.txt", b"old")
    (folder / "list.csv").write_text("date,file\n2026-11-01,a.pdf\n", encoding="utf-8")
    before = watch.snapshot(folder)
    (folder / "2026").mkdir()
    os.rename(folder / "scan_001.pdf", folder / "2026" / "2026-11-03 scan_001.pdf")
    os.rename(folder / "scan_002.pdf", folder / "renamed.pdf")
    with (folder / "list.csv").open("a", encoding="utf-8") as out:
        out.write("2026-11-03,2026-11-03 scan_001.pdf\n")
    (folder / "notes.txt").write_bytes(b"v2 longer")
    (folder / "old.txt").unlink()
    put(folder, "new.txt", b"new")
    (folder / "fresh.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    notes = watch.diff(before, watch.snapshot(folder, before))
    kinds = [(note["kind"], note.get("from") or note.get("path"), note.get("to")) for note in notes]
    assert kinds == [
        ("moved", "scan_001.pdf", "2026/2026-11-03 scan_001.pdf"),
        ("moved", "scan_002.pdf", "renamed.pdf"),
        ("rows", "fresh.csv", None),
        ("rows", "list.csv", None),
        ("added", "new.txt", None),
        ("changed", "notes.txt", None),
        ("removed", "old.txt", None),
    ]
    rows = {note["path"]: note for note in notes if note["kind"] == "rows"}
    assert rows["list.csv"]["rows"] == [["2026-11-03", "2026-11-03 scan_001.pdf"]]
    assert rows["list.csv"]["header"] == ["date", "file"]
    assert rows["fresh.csv"]["rows"] == [["1", "2"]] and rows["fresh.csv"]["created"]


def test_a_rewritten_list_is_a_change_not_rows(person):
    folder = person / "Downloads"
    (folder / "list.csv").write_text("date,file\n2026-11-01,a.pdf\n", encoding="utf-8")
    before = watch.snapshot(folder)
    (folder / "list.csv").write_text("date,file\n2026-11-02,b.pdf\n2026-11-03,c.pdf\n", encoding="utf-8")
    assert watch.diff(before, watch.snapshot(folder)) == [{"kind": "changed", "path": "list.csv"}]


def test_one_watch_at_a_time_and_stop_keeps_what_it_saw(person):
    folder = person / "Downloads"
    put(folder, "scan_001.pdf", b"first scan")
    started = watch.start(str(folder))
    assert started["running"] and started["folder"] == str(folder.resolve())
    assert 0 < started["seconds_left"] <= watch.WATCH_SECONDS
    with pytest.raises(RoutineError) as caught:
        watch.start(str(person / "Documents"))
    assert caught.value.code == "watch_running" and caught.value.status == 409
    os.rename(folder / "scan_001.pdf", folder / "Scan one.pdf")
    assert watch.refresh()["changes"] == 1
    assert watch.current() is None  # nothing to learn from while it still runs
    stopped = watch.stop()
    assert not stopped["running"] and stopped["end_reason"] == "stopped"
    assert stopped["notes"][0]["kind"] == "moved"
    kept = watch.current()
    assert kept["notes"] == stopped["notes"]
    # A second stop changes nothing; forget deletes everything the watch kept.
    assert watch.stop()["notes"] == stopped["notes"]
    state_file = person.parent / "vool" / "data" / "routines" / "watch.json"
    assert state_file.is_file()
    watch.forget()
    assert not state_file.exists() and watch.current() is None and watch.refresh() is None
    with pytest.raises(RoutineError) as caught:
        watch.stop()
    assert caught.value.code == "no_watch"


class _Clock:
    """The wall clock, moved forward: VOOL was closed or asleep past the watch's 30 minutes."""

    def __init__(self, ahead):
        self.ahead = ahead

    def time(self):
        return time.time() + self.ahead

    def __getattr__(self, name):
        return getattr(time, name)


def test_a_watch_whose_time_ran_out_while_vool_was_closed_keeps_nothing(person, monkeypatch):
    folder = person / "Downloads"
    put(folder, "scan_001.pdf", b"first scan")
    watch.start(str(folder))
    os.rename(folder / "scan_001.pdf", folder / "later.pdf")
    state_file = person.parent / "vool" / "data" / "routines" / "watch.json"
    monkeypatch.setattr(watch, "time", _Clock(watch.WATCH_SECONDS + 60))
    expired = watch.refresh()
    assert expired == {"id": expired["id"], "folder": str(folder.resolve()), "running": False, "seconds_left": 0,
                       "end_reason": "expired", "notes": []}
    assert not state_file.exists() and watch.current() is None


def test_stop_after_the_time_ran_out_keeps_nothing_either(person, monkeypatch):
    folder = person / "Downloads"
    put(folder, "scan_001.pdf", b"first scan")
    watch.start(str(folder))
    os.rename(folder / "scan_001.pdf", folder / "later.pdf")
    monkeypatch.setattr(watch, "time", _Clock(watch.WATCH_SECONDS + 60))
    stopped = watch.stop()
    assert stopped["end_reason"] == "expired" and stopped["notes"] == []
    assert watch.current() is None


def test_a_folder_too_big_to_watch_is_refused_and_nothing_is_kept(person, monkeypatch):
    folder = person / "Downloads"
    for index in range(5):
        put(folder, f"f{index}.pdf", bytes([index]))
    monkeypatch.setattr(watch, "MAX_ENTRIES", 3)
    with pytest.raises(RoutineError) as caught:
        watch.start(str(folder))
    assert caught.value.code == "folder_too_big"
    assert watch.refresh() is None


def test_digits_count_only_as_their_own_number():
    values = {"date": "2026-11-03", "year": "2026", "month": "11", "day": "03", "name": "scan_0113"}
    assert decompose("2026-11-03 scan_0113", values, ("date", "year", "month", "day", "name")) == [
        {"token": "date"}, {"text": " "}, {"token": "name"}]
    assert decompose("Bills 2011/11", values, ("year", "month")) == [{"text": "Bills 2011/"}, {"token": "month"}]
    assert decompose("x", {"name": "x"}, ("name",)) == [{"text": "x"}]  # one character is never a value


def _watched(person, task):
    folder = person / "Downloads"
    put(folder, "scan_001.pdf", b"first scan", day=3)
    put(folder, "scan_002.pdf", b"second scan", day=5)
    (folder / "receipts.csv").write_text("date,file\n", encoding="utf-8")
    watch.start(str(folder))
    task(folder)
    watch.stop()
    return watch.current()


def test_the_proposal_explains_names_folders_and_rows_with_values_it_can_read_again(person):
    def task(folder):
        (folder / "2026" / "11").mkdir(parents=True)
        os.rename(folder / "scan_001.pdf", folder / "2026" / "11" / "2026-11-03 scan_001.pdf")
        os.rename(folder / "scan_002.pdf", folder / "2026" / "11" / "2026-11-05 scan_002.pdf")
        with (folder / "receipts.csv").open("a", encoding="utf-8") as out:
            out.write("2026-11-03,2026-11-03 scan_001.pdf\n2026-11-05,2026-11-05 scan_002.pdf\n")

    proposal = propose(_watched(person, task))
    routine = proposal["routine"]
    assert routine["types"] == [".pdf"]
    assert routine["rename"] == [{"token": "date"}, {"text": " "}, {"token": "name"}]
    assert routine["into"] == [{"token": "year"}, {"text": "/"}, {"token": "month"}]
    assert routine["log"] == {"file": "receipts.csv", "columns": [[{"token": "date"}], [{"token": "new_name"}]]}
    assert proposal["unclear"] == [] and proposal["fixed_text"] == []
    assert routine["name"] == "" and routine["folder"] == str((person / "Downloads").resolve())
    assert {example["from"] for example in proposal["examples"]} == {"scan_001.pdf", "scan_002.pdf"}


def test_examples_that_disagree_are_said_out_loud_not_guessed(person):
    def task(folder):
        os.rename(folder / "scan_001.pdf", folder / "Invoice scan_001.pdf")
        os.rename(folder / "scan_002.pdf", folder / "Receipt scan_002.pdf")

    proposal = propose(_watched(person, task))
    assert proposal["unclear"] == ["rename_differs"]
    # The first example is offered as the starting point, with its typed words pointed out.
    assert proposal["routine"]["rename"] == [{"text": "Invoice "}, {"token": "name"}]
    assert proposal["fixed_text"] == ["Invoice"]


def test_nothing_learned_when_no_file_was_renamed_or_moved(person):
    def task(folder):
        put(folder, "new.pdf", b"new")
        (folder / "scan_001.pdf").unlink()

    state = _watched(person, task)
    with pytest.raises(RoutineError) as caught:
        propose(state)
    assert caught.value.code == "nothing_learned"


def test_file_values_come_from_the_name_and_date():
    stamp = time.mktime((2026, 11, 3, 10, 0, 0, 0, 0, -1))
    assert file_values("scan_001.pdf", int(stamp * 1e9)) == {
        "date": "2026-11-03", "year": "2026", "month": "11", "day": "03", "name": "scan_001"}
