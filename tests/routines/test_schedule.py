"""A routine's schedule only puts a note on the bell: it never runs anything by itself."""
from __future__ import annotations

from datetime import datetime

from core.routines import schedule, store
from tests.routines.conftest import put, tree


def test_the_latest_due_time_daily_and_weekly():
    daily = {"every": "daily", "time": "09:00", "weekday": 0}
    assert schedule.latest_slot(daily, datetime(2026, 11, 12, 8, 59)) == "2026-11-11T09:00"
    assert schedule.latest_slot(daily, datetime(2026, 11, 12, 9, 0)) == "2026-11-12T09:00"
    assert schedule.latest_slot(daily, datetime(2026, 11, 12, 23, 0)) == "2026-11-12T09:00"
    monday = {"every": "weekly", "time": "07:30", "weekday": 0}
    assert schedule.latest_slot(monday, datetime(2026, 11, 12, 10, 0)) == "2026-11-09T07:30"  # a Thursday
    assert schedule.latest_slot(monday, datetime(2026, 11, 9, 7, 0)) == "2026-11-02T07:30"
    assert schedule.latest_slot(monday, datetime(2026, 11, 9, 7, 30)) == "2026-11-09T07:30"
    assert schedule.latest_slot({"every": "", "time": "09:00", "weekday": 0}, datetime(2026, 11, 12)) == ""


def _bell():
    from core.operator import notification_center

    return [item for item in notification_center.list_items(include_dismissed=True)["items"]
            if (item.get("payload") or {}).get("kind") == "routine"]


def _saved(person, **changes):
    folder = person / "Downloads"
    put(folder, "scan_001.pdf", b"%PDF first", day=3)
    raw = {"name": "Sort scans", "folder": str(folder), "types": [".pdf"], "rename": [{"token": "date"}, {"text": " "},
           {"token": "name"}], "schedule": {"every": "daily", "time": "09:00", "weekday": 0}}
    raw.update(changes)
    return store.save(raw), folder


def test_a_due_routine_adds_one_note_and_changes_no_file(person):
    routine, folder = _saved(person)
    before = tree(folder)
    # First sight: it waits for its next time, even though 09:00 today has passed.
    assert schedule.tick(datetime(2026, 11, 12, 10, 0)) == []
    assert _bell() == []
    # The next 09:00: one note that opens the list; nothing moved.
    assert schedule.tick(datetime(2026, 11, 13, 9, 1)) == [
        {"routine_id": routine["id"], "slot": "2026-11-13T09:00", "changes": 1}]
    assert tree(folder) == before
    notes = _bell()
    assert len(notes) == 1
    assert notes[0]["title"] == "Sort scans is ready to run"
    assert notes[0]["body"] == "1 change is ready. Open My routines to look at it and run it."
    assert notes[0]["payload"] == {"kind": "routine", "section": "needs", "routine_id": routine["id"],
                                   "name": "Sort scans", "changes": 1, "problem": False,
                                   "open": f"/routines?run={routine['id']}"}
    # The same time again, or later the same day: nothing new.
    assert schedule.tick(datetime(2026, 11, 13, 9, 1)) == []
    assert schedule.tick(datetime(2026, 11, 13, 18, 0)) == []
    assert len(_bell()) == 1 and tree(folder) == before


def test_a_time_missed_while_vool_was_closed_is_caught_up_once(person):
    routine, _folder = _saved(person)
    schedule.tick(datetime(2026, 11, 12, 10, 0))
    # VOOL was closed for three days: one note for the latest time, not three.
    assert [row["slot"] for row in schedule.tick(datetime(2026, 11, 16, 12, 0))] == ["2026-11-16T09:00"]
    assert len(_bell()) == 1


def test_nothing_to_do_means_no_note(person):
    routine, folder = _saved(person)
    (folder / "scan_001.pdf").unlink()
    schedule.tick(datetime(2026, 11, 12, 10, 0))
    assert schedule.tick(datetime(2026, 11, 13, 9, 1)) == [
        {"routine_id": routine["id"], "slot": "2026-11-13T09:00", "changes": 0}]
    assert _bell() == []


def test_a_routine_that_cannot_get_ready_says_so(person):
    routine, folder = _saved(person, log={"file": "receipts.csv", "columns": [[{"token": "new_name"}]]})
    schedule.tick(datetime(2026, 11, 12, 10, 0))
    # The list file is gone: the plan cannot be made, and the note says why.
    assert schedule.tick(datetime(2026, 11, 13, 9, 1)) == [
        {"routine_id": routine["id"], "slot": "2026-11-13T09:00", "problem": "list_missing"}]
    notes = _bell()
    assert len(notes) == 1 and notes[0]["title"] == "Sort scans could not get ready"
    assert notes[0]["body"] == "receipts.csv is not in the folder any more, so nothing will be done."
    assert notes[0]["payload"]["problem"] is True and notes[0]["payload"]["changes"] == 0


def test_a_changed_schedule_waits_for_its_next_time_and_removed_routines_are_forgotten(person):
    routine, _folder = _saved(person)
    schedule.tick(datetime(2026, 11, 12, 10, 0))
    stored = {key: value for key, value in routine.items() if key != "version"}  # as the page sends it back
    store.save(dict(stored, schedule={"every": "daily", "time": "08:00", "weekday": 0}))
    assert schedule.tick(datetime(2026, 11, 13, 9, 1)) == []  # the new 08:00 is not caught up
    assert _bell() == []
    assert len(schedule.tick(datetime(2026, 11, 14, 8, 0))) == 1
    state_file = person.parent / "vool" / "data" / "routines" / "schedule.json"
    assert routine["id"] in state_file.read_text(encoding="utf-8")
    store.remove(routine["id"])
    assert schedule.tick(datetime(2026, 11, 15, 8, 0)) == []
    assert routine["id"] not in state_file.read_text(encoding="utf-8")


def test_a_routine_without_a_schedule_is_never_prepared(person):
    _routine, folder = _saved(person, schedule={"every": "", "time": "09:00", "weekday": 0})
    for day in (12, 13, 14):
        assert schedule.tick(datetime(2026, 11, day, 9, 30)) == []
    assert _bell() == []
