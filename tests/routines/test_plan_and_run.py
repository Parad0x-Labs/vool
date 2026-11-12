"""The exact list a run would make, and a run that does only that list, never replacing a file."""
from __future__ import annotations

import threading

import pytest

from core.routines import runner
from core.routines.model import RoutineError, validate
from core.routines.plan import plan, safe_cell
from tests.routines.conftest import put, tree


def _routine(folder, **changes):
    raw = {"name": "Sort scans", "folder": str(folder), "types": [".pdf"],
           "rename": [{"token": "date"}, {"text": " "}, {"token": "name"}],
           "into": [{"token": "year"}, {"text": "/"}, {"token": "month"}],
           "log": {"file": "receipts.csv", "columns": [[{"token": "date"}], [{"token": "new_name"}]]}}
    raw.update(changes)
    return validate(raw)


def _scans(folder):
    put(folder, "scan_001.pdf", b"%PDF first", day=3)
    put(folder, "scan_002.pdf", b"%PDF second", day=5)
    put(folder, "notes.txt", b"not a scan")
    (folder / "receipts.csv").write_bytes(b"date,file\r\n2026-11-01,old.pdf\r\n")


def refused(code, call, *args):
    with pytest.raises(RoutineError) as caught:
        call(*args)
    assert caught.value.code == code, (caught.value.code, caught.value.message)
    return caught.value


def test_a_plan_lists_every_change_and_writes_nothing(person):
    folder = person / "Downloads"
    _scans(folder)
    before = tree(folder)
    routine = _routine(folder)
    result = plan(routine)
    assert tree(folder) == before and not (folder / "2026").exists()
    assert [(op["from"], op["to"], op["make"], op["row"]) for op in result["operations"]] == [
        ("scan_001.pdf", "2026/11/2026-11-03 scan_001.pdf", ["2026", "2026/11"], ["2026-11-03", "2026-11-03 scan_001.pdf"]),
        ("scan_002.pdf", "2026/11/2026-11-05 scan_002.pdf", ["2026", "2026/11"], ["2026-11-05", "2026-11-05 scan_002.pdf"]),
    ]
    assert result["left_alone"] == {"other_types": 1, "already_done": 0}  # notes.txt; the list itself is not counted
    assert result["log"]["rows"] == 2 and result["log"]["delimiter"] == ","
    assert result["needs_you"] == [] and result["stop"] == ""
    # The same folder gives the same digest; any change to a file in the list gives another.
    assert plan(routine)["digest"] == result["digest"]
    put(folder, "scan_002.pdf", b"%PDF second, edited", day=5)
    assert plan(routine)["digest"] != result["digest"]


def test_files_already_done_are_left_alone(person):
    folder = person / "Downloads"
    put(folder, "2026-11-03 scan_001.pdf", b"done", day=3)
    put(folder, "scan_002.pdf", b"new", day=5)
    in_place = _routine(folder, into=[], log=None)
    result = plan(in_place)
    assert [(op["from"], op["to"]) for op in result["operations"]] == [("scan_002.pdf", "2026-11-05 scan_002.pdf")]
    assert result["left_alone"]["already_done"] == 1


def test_files_that_would_collide_or_cannot_be_named_wait_for_the_person(person):
    folder = person / "Downloads"
    put(folder, "a.pdf", b"a", day=3)
    put(folder, "b.pdf", b"b", day=3)
    put(folder, "c:d.pdf", b"c", day=4)
    put(folder, "2026-11-06.pdf", b"already there", day=9)
    put(folder, "e.pdf", b"e", day=6)
    by_date = _routine(folder, rename=[{"token": "date"}], into=[], log=None)
    result = plan(by_date)
    assert [(op["from"], op["to"]) for op in result["operations"]] == [("a.pdf", "2026-11-03.pdf"),
                                                                       ("c:d.pdf", "2026-11-04.pdf")]
    assert result["needs_you"] == [{"file": "b.pdf", "reason": "same_target"}, {"file": "e.pdf", "reason": "target_exists"}]
    by_name = _routine(folder, rename=[{"token": "name"}, {"text": " done"}], into=[], log=None)
    assert {"file": "c:d.pdf", "reason": "bad_new_name"} in plan(by_name)["needs_you"]
    by_shop = _routine(folder, rename=[{"token": "doc_shop"}], into=[], log=None)
    assert {"file": "a.pdf", "reason": "missing_value:doc_shop"} in plan(by_shop)["needs_you"]


def test_a_rename_to_one_read_value_still_finds_new_files(person):
    folder = person / "Downloads"
    put(folder, "Bakery Nord.txt", b"Bakery Nord\n2026-11-02\nTotal 4.20\n", day=3)
    put(folder, "receipt 2.txt", b"Corner Shop\n2026-11-03\nTotal 9.90\n", day=3)
    by_shop = _routine(folder, types=[".txt"], rename=[{"token": "doc_shop"}], into=[], log=None)
    result = plan(by_shop)
    # One value with no fixed text says nothing about which files are done: each file's own new name does.
    assert [(op["from"], op["to"]) for op in result["operations"]] == [("receipt 2.txt", "Corner Shop.txt")]
    assert result["left_alone"]["already_done"] == 1


def test_a_folder_on_the_way_that_is_a_file_or_a_link_stops_that_file(person, tmp_path):
    folder = person / "Downloads"
    put(folder, "a.pdf", b"a", day=3)
    (folder / "2026").write_bytes(b"a file where a folder should be")
    result = plan(_routine(folder, log=None))
    assert result["operations"] == [] and result["needs_you"] == [{"file": "a.pdf", "reason": "bad_target"}]
    (folder / "2026").unlink()
    (folder / "2026").symlink_to(tmp_path)
    assert plan(_routine(folder, log=None))["needs_you"] == [{"file": "a.pdf", "reason": "bad_target"}]


def test_too_many_files_stop_the_whole_list(person):
    folder = person / "Downloads"
    _scans(folder)
    routine = _routine(folder, max_files=1)
    result = plan(routine)
    assert result["stop"] == "too_many" and result["count"] == 2 and result["operations"] == []
    before = tree(folder)
    refused("plan_stopped", runner.run, routine, result["digest"])
    assert tree(folder) == before


def test_a_list_file_that_is_gone_stops_before_anything(person):
    folder = person / "Downloads"
    _scans(folder)
    (folder / "receipts.csv").unlink()
    error = refused("list_missing", plan, _routine(folder))
    assert error.status == 409


def test_list_cells_are_never_read_as_formulas():
    assert safe_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert safe_cell("+cmd") == "'+cmd"
    assert safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert safe_cell("-2+3") == "'-2+3"
    for plain in ("-12.50", "+3", "-", "12,50", "Coffee - 2 cups"):
        assert safe_cell(plain) == plain
    assert safe_cell("two\nlines\t here") == "two lines here"


def test_a_run_needs_the_list_the_person_saw(person):
    folder = person / "Downloads"
    _scans(folder)
    routine = _routine(folder)
    before = tree(folder)
    refused("plan_changed", runner.run, routine, "0" * 64)
    refused("plan_changed", runner.run, routine, "")
    assert tree(folder) == before
    assert runner.receipts_for(routine["id"]) == []


def test_a_run_moves_the_listed_files_adds_their_rows_and_checks_them(person):
    folder = person / "Downloads"
    _scans(folder)
    routine = _routine(folder)
    seen = plan(routine)
    receipt = runner.run(routine, seen["digest"])
    assert receipt["stopped"] == "" and receipt["planned"] == 2
    assert receipt["checks"] == {"moved_as_planned": 2, "moved": 2}
    assert [entry["to"] for entry in receipt["done"]] == ["2026/11/2026-11-03 scan_001.pdf",
                                                          "2026/11/2026-11-05 scan_002.pdf"]
    after = tree(folder)
    assert after["2026/11/2026-11-03 scan_001.pdf"] == b"%PDF first"
    assert after["2026/11/2026-11-05 scan_002.pdf"] == b"%PDF second"
    assert "scan_001.pdf" not in after and "scan_002.pdf" not in after
    assert after["notes.txt"] == b"not a scan"
    # The list keeps its own line endings.
    assert after["receipts.csv"] == (b"date,file\r\n2026-11-01,old.pdf\r\n2026-11-03,2026-11-03 scan_001.pdf\r\n"
                                     b"2026-11-05,2026-11-05 scan_002.pdf\r\n")
    assert runner.receipts_for(routine["id"])[0]["id"] == receipt["id"]
    refused("nothing_to_do", runner.run, routine, plan(routine)["digest"])


def test_a_list_without_a_last_line_end_gets_one_first(person):
    folder = person / "Downloads"
    put(folder, "a.pdf", b"a", day=3)
    (folder / "receipts.csv").write_bytes(b"date;file\n2026-11-01;old.pdf")
    routine = _routine(folder)
    runner.run(routine, plan(routine)["digest"])
    assert (folder / "receipts.csv").read_bytes() == b"date;file\n2026-11-01;old.pdf\n2026-11-03;2026-11-03 a.pdf\n"


def test_a_file_changed_during_the_run_stops_it_there(person, monkeypatch):
    folder = person / "Downloads"
    _scans(folder)
    routine = _routine(folder)
    seen = plan(routine)
    import core.machine_file_ops as file_ops

    real_move = file_ops.move_path

    def move_then_someone_edits_the_next_file(*args, **kwargs):
        result = real_move(*args, **kwargs)
        # Outside actor: the person saves over the second scan while the run is between files.
        put(folder, "scan_002.pdf", b"%PDF second, saved again", day=5)
        return result

    monkeypatch.setattr(file_ops, "move_path", move_then_someone_edits_the_next_file)
    receipt = runner.run(routine, seen["digest"])
    assert receipt["stopped"] == "file_changed" and receipt["stopped_at"] == "scan_002.pdf"
    assert [entry["from"] for entry in receipt["done"]] == ["scan_001.pdf"]
    assert receipt["checks"] == {"moved_as_planned": 1, "moved": 1}
    after = tree(folder)
    assert after["scan_002.pdf"] == b"%PDF second, saved again"
    assert after["receipts.csv"].count(b"\r\n") == 3  # one row, for the file that moved


def test_a_file_that_appears_at_the_target_is_never_replaced(person, monkeypatch):
    folder = person / "Downloads"
    _scans(folder)
    routine = _routine(folder)
    seen = plan(routine)
    import core.machine_file_ops as file_ops

    real_rename = file_ops.rename_no_replace

    def someone_saves_there_first(src, dst):
        if not dst.exists():
            # Outside actor: another app writes the exact target name between the check and the move.
            dst.write_bytes(b"another app's file")
        return real_rename(src, dst)

    monkeypatch.setattr(file_ops, "rename_no_replace", someone_saves_there_first)
    receipt = runner.run(routine, seen["digest"])
    assert receipt["stopped"] == "dest_exists" and receipt["stopped_at"] == "scan_001.pdf" and receipt["done"] == []
    after = tree(folder)
    assert after["2026/11/2026-11-03 scan_001.pdf"] == b"another app's file"
    assert after["scan_001.pdf"] == b"%PDF first" and after["scan_002.pdf"] == b"%PDF second"
    assert after["receipts.csv"] == b"date,file\r\n2026-11-01,old.pdf\r\n"


def test_a_list_changed_during_the_run_stops_after_that_file(person, monkeypatch):
    folder = person / "Downloads"
    _scans(folder)
    routine = _routine(folder)
    seen = plan(routine)
    import core.machine_file_ops as file_ops

    real_move = file_ops.move_path

    def move_then_the_list_is_edited(*args, **kwargs):
        result = real_move(*args, **kwargs)
        with (folder / "receipts.csv").open("ab") as out:
            out.write(b"typed by hand\r\n")
        return result

    monkeypatch.setattr(file_ops, "move_path", move_then_the_list_is_edited)
    receipt = runner.run(routine, seen["digest"])
    assert receipt["stopped"] == "list_changed" and receipt["stopped_at"] == "scan_001.pdf"
    assert [entry["from"] for entry in receipt["done"]] == ["scan_001.pdf"]
    assert "row" not in receipt["done"][0]
    assert (folder / "receipts.csv").read_bytes() == b"date,file\r\n2026-11-01,old.pdf\r\ntyped by hand\r\n"
    assert (folder / "scan_002.pdf").read_bytes() == b"%PDF second"


def test_one_run_at_a_time(person, monkeypatch):
    folder = person / "Downloads"
    _scans(folder)
    routine = _routine(folder)
    seen = plan(routine)
    import core.machine_file_ops as file_ops

    real_move = file_ops.move_path
    moving, release = threading.Event(), threading.Event()

    def slow_disk(*args, **kwargs):
        moving.set()
        release.wait(30)
        return real_move(*args, **kwargs)

    monkeypatch.setattr(file_ops, "move_path", slow_disk)
    results = []
    first = threading.Thread(target=lambda: results.append(runner.run(routine, seen["digest"])))
    first.start()
    try:
        assert moving.wait(30)
        error = refused("run_busy", runner.run, routine, seen["digest"])
        assert error.status == 409
    finally:
        release.set()
        first.join(60)
    assert results and results[0]["checks"] == {"moved_as_planned": 2, "moved": 2}


def test_only_the_last_twenty_receipts_are_kept(person):
    folder = person / "Downloads"
    routine = _routine(folder, rename=[{"text": "done "}, {"token": "name"}], into=[], log=None)
    ids = []
    for index in range(runner.KEEP_RECEIPTS + 2):
        put(folder, f"scan_{index:03}.pdf", bytes([index]), day=3)
        ids.append(runner.run(routine, plan(routine)["digest"])["id"])
    kept = runner.receipts_for(routine["id"])
    assert len(kept) == runner.KEEP_RECEIPTS
    assert [receipt["id"] for receipt in kept] == list(reversed(ids))[: runner.KEEP_RECEIPTS]
    assert len(list((person.parent / "vool" / "data" / "routines" / "runs" / routine["id"]).glob("run-*.json"))) == \
        runner.KEEP_RECEIPTS
